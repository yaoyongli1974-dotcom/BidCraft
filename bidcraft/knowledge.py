"""Knowledge base indexing and retrieval.

Two kinds are supported:

* ``local`` — a directory of PDF/DOCX/TXT/Markdown files indexed into SQLite
  with a dependency-free character-bigram scorer.
* ``api`` — a third-party retrieval endpoint (generic JSON, Dify or RAGFlow).

Retrieved passages are advisory context only; ledger facts always win.
"""

from __future__ import annotations

import hashlib
import logging
import math
import mimetypes
import re
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from sqlalchemy import and_, delete, func, or_, select

from . import clock
from .config import settings
from .model_config import api_base, resolve_embedding_endpoint
from .models import (
    KbAssetDuplicateGroup,
    KbAssetDuplicateMember,
    KbChunk,
    KbDocument,
    KnowledgeBase,
    utcnow,
)
from .security import decrypt_secret

logger = logging.getLogger("bidcraft.knowledge")

SUPPORTED = {".pdf", ".docx", ".txt", ".md", ".markdown", ".xlsx", ".xlsm"}
MAX_FILES = 500
MAX_FILE_BYTES = 25 * 1024 * 1024
CHUNK_CHARS = 900
OVERLAP_CHARS = 120
TIMEOUT = 20.0
MAX_API_RESPONSE_BYTES = 2 * 1024 * 1024
# 检索候选上限：SQL 先按 token/LIKE 与文档生命周期收窄，再在 Python 里精确打分。
SEARCH_CANDIDATE_CAP = 2000
MAX_QUERY_TOKENS = 32
# RRF 常数与“库内归一化分”的权重：跨库 score 不可直接比较，只比较名次。
RRF_K = 60
RRF_NORMALIZED_WEIGHT = 0.25

_CJK = re.compile(r"[\u3400-\u9fff]+")
_WORD = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.+-]*")


class KnowledgeError(RuntimeError):
    pass


@dataclass
class Passage:
    kb: str
    source: str
    text: str
    score: float
    # 文档级溯源（旧调用方不传，默认值保证向后兼容）。
    document_id: str | None = None
    page: int | None = None
    heading: str = ""
    # 溯源到具体知识库，避免同名 source 在多个库中互相错配元数据。
    kb_id: str | None = None
    # 块级溯源：真实 KbChunk.id / ordinal；上游无法提供时保持 None。
    chunk_id: str | None = None
    chunk_index: int | None = None
    # 打分来源与分量：不同 provider / 算法的原始 score 不可互相比较。
    score_source: str = ""
    score_components: dict[str, float] = field(default_factory=dict)
    # rerank_hybrid 写入的融合分；不覆盖调用方持有的原始 score。
    rerank_score: float | None = None
    # 就绪/新鲜度信号，仅用于同分时的稳定 tie-break。
    freshness: float = 0.5
    readiness: float = 0.5

    @property
    def page_no(self) -> int | None:
        """Alias used by callers that speak in ``KbChunk.page_no`` terms."""

        return self.page


class SearchResult(list):
    """普通 ``list[Passage]`` 的子类；``with_diagnostics=True`` 时附带诊断。"""

    def __init__(self, passages=(), *, diagnostics: dict | None = None) -> None:
        super().__init__(passages)
        self.diagnostics: dict = diagnostics if diagnostics is not None else {}

    @property
    def passages(self) -> list[Passage]:
        return list(self)


def _embed_key(endpoint) -> str:
    return f"{endpoint.config_id}:{endpoint.model_name}"


def _check_response_size(response: httpx.Response, *, label: str = "知识库接口") -> None:
    """Reject oversized upstream payloads before parsing them."""

    declared = (response.headers.get("content-length") or "").strip()
    if declared.isdigit() and int(declared) > MAX_API_RESPONSE_BYTES:
        raise KnowledgeError(f"{label}响应过大（超过 2MB）")
    if len(response.content or b"") > MAX_API_RESPONSE_BYTES:
        raise KnowledgeError(f"{label}响应过大（超过 2MB）")


def _vector_fields(kb: KnowledgeBase, db) -> dict:
    total = int(kb.chunk_count or 0)
    embedded = 0
    dim = 0
    model = ""
    if db is not None:
        try:
            endpoint = resolve_embedding_endpoint(db)
        except Exception:
            logger.debug("解析向量模型失败", exc_info=True)
            endpoint = None
        if endpoint is not None:
            model = endpoint.model_name
            key = _embed_key(endpoint)
            embedded = int(
                db.scalar(
                    select(func.count())
                    .select_from(KbChunk)
                    .where(KbChunk.kb_id == kb.id, KbChunk.embed_key == key)
                )
                or 0
            )
            if embedded:
                dim = int(
                    db.scalar(
                        select(KbChunk.embed_dim)
                        .where(
                            KbChunk.kb_id == kb.id,
                            KbChunk.embed_key == key,
                            KbChunk.embed_dim > 0,
                        )
                        .order_by(KbChunk.ordinal)
                        .limit(1)
                    )
                    or 0
                )
    return {
        "vector_enabled": settings.kb_vector_enabled,
        "vector_model": model,
        "vector_embedded": embedded,
        "vector_total": total,
        "vector_dim": dim,
        "vector_ready": total > 0 and embedded == total,
        "vector_stale": total > 0 and embedded < total,
    }


def kb_payload(kb: KnowledgeBase, db=None) -> dict:
    return {
        "id": kb.id,
        "name": kb.name,
        "kind": kb.kind,
        "provider": kb.provider,
        "location": kb.location,
        "dataset_id": kb.dataset_id,
        "options": kb.options or {},
        "enabled": kb.enabled,
        "is_default": kb.is_default,
        "has_api_key": bool(kb.api_key_enc),
        "chunk_count": kb.chunk_count,
        "indexed_at": _iso(kb.indexed_at),
        "last_test_at": _iso(kb.last_test_at),
        "last_test_ok": kb.last_test_ok,
        "last_test_message": kb.last_test_message,
        "created_at": _iso(kb.created_at),
        "updated_at": _iso(kb.updated_at),
        **_vector_fields(kb, db),
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def tokens_for(text: str) -> list[str]:
    lowered = (text or "").lower()
    tokens: list[str] = [word.lower() for word in _WORD.findall(lowered)]
    for run in _CJK.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _chunks(text: str) -> list[str]:
    normalized = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if not normalized:
        return []
    pieces: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + CHUNK_CHARS)
        if end < len(normalized):
            window = normalized[start:end]
            cut = max(window.rfind("\n\n"), window.rfind("。"), window.rfind("；"))
            if cut >= CHUNK_CHARS * 0.5:
                end = start + cut + 1
        piece = normalized[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(normalized):
            break
        start = max(start + 1, end - OVERLAP_CHARS)
    return pieces


def _iter_files(root: Path):
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= MAX_FILES:
            break
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in SUPPORTED:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        count += 1
        yield path


def index_local(db, kb: KnowledgeBase) -> int:
    """全量重建本地库索引（按文件增量 upsert，保留未变文件的向量缓存）。

    ``ParsedDocument.pages`` 是 PDF 页码的唯一可靠来源：``loader`` 的
    ``text`` 用空行连接页面，无法再区分页边界。这里用 ``\\f`` 重新拼接页面，
    ``ingest_document`` 的 ``_document_pages`` 据此写 KbDocument + 页码溯源。
    """

    root = Path(kb.location).expanduser()
    if not root.exists() or not root.is_dir():
        raise KnowledgeError(f"目录不存在：{root}")
    from .parsing.loader import load_document

    seen: set[str] = set()
    for path in _iter_files(root):
        source = str(path.relative_to(root))
        try:
            document = load_document(path)
        except Exception:
            logger.debug("跳过无法解析的知识库文件 %s", path, exc_info=True)
            continue
        seen.add(source)
        pages = list(document.pages or []) or [document.text]
        existing = db.scalar(
            select(KbDocument).where(
                KbDocument.kb_id == kb.id, KbDocument.source == source
            )
        )
        ingest_document(
            db,
            kb,
            source,
            "\f".join(pages),
            kind=existing.kind if existing is not None else "other",
            tags=list(existing.tags or []) if existing is not None else None,
            valid_to=existing.valid_to if existing is not None else None,
        )

    # 目录里已删除的文件：连同 KbDocument 与其块一起清理。
    for doc in list(list_documents(db, kb)):
        if doc.source not in seen:
            delete_document(db, kb, doc.id)
    # 旧版全量索引遗留的无 document_id 块不再有生命周期元数据，统一清掉。
    db.execute(
        delete(KbChunk).where(
            KbChunk.kb_id == kb.id, KbChunk.document_id.is_(None)
        )
    )
    total = _refresh_chunk_count(db, kb)
    kb.indexed_at = utcnow()
    return total


# --------------------------------------------------------------- 文档级索引（v0.25）


def _as_datetime(value) -> datetime | None:
    """Parse ``valid_to`` from a date/datetime/ISO string (empty → ``None``)."""

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError as exc:
        raise KnowledgeError("有效期格式无效，应为 YYYY-MM-DD") from exc
    return datetime(parsed.year, parsed.month, parsed.day)


def _heading_for(piece: str) -> str:
    """Best-effort heading: first short line, stripped of Markdown markers."""

    for line in (piece or "").splitlines():
        value = line.strip().lstrip("#").strip()
        if not value:
            continue
        if len(value) <= 120 and not value.endswith(("。", "；", ";", "，", ",")):
            return value
        break
    return ""


def _page_units(text: str) -> list[str]:
    """Split on form feed (PDF page break); a plain text document is one page."""

    normalized = str(text or "")
    if not normalized.strip():
        return []
    return normalized.split("\f")


def _document_pages(text: str) -> list[tuple[int, list[str]]]:
    pages: list[tuple[int, list[str]]] = []
    for index, unit in enumerate(_page_units(text), start=1):
        chunks = _chunks(unit)
        if chunks:
            pages.append((index, chunks))
    return pages


def document_payload(row: KbDocument) -> dict:
    return {
        "id": row.id,
        "kb_id": row.kb_id,
        "source": row.source or "",
        "sha256": row.sha256 or "",
        "size": int(row.size or 0),
        "mime": row.mime or "",
        "pages": int(row.pages or 0),
        "status": row.status or "ready",
        "version": int(row.version or 1),
        "kind": row.kind or "other",
        "tags": list(row.tags or []),
        "valid_to": _iso(row.valid_to),
        "error": row.error or "",
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _refresh_chunk_count(db, kb: KnowledgeBase) -> int:
    total = int(
        db.scalar(
            select(func.count()).select_from(KbChunk).where(KbChunk.kb_id == kb.id)
        )
        or 0
    )
    kb.chunk_count = total
    kb.indexed_at = utcnow()
    return total


def ingest_document(
    db,
    kb: KnowledgeBase,
    source: str,
    content: str,
    *,
    kind: str = "other",
    tags: list[str] | None = None,
    valid_to=None,
    mime: str = "",
) -> KbDocument:
    """Upsert one document and incrementally (re)index only that document.

    增量语义：按 ``kb_id + source`` 定位文档。内容 sha256 未变化时只更新元数据，
    不删除/重建任何 ``KbChunk``（向量缓存因此得以保留）；内容变化时仅删除该
    ``document_id`` 的旧块并重建，其他文档的块不受影响。
    """

    name = str(source or "").strip()
    if not name:
        raise KnowledgeError("文档来源不能为空")
    if len(name) > 500:
        raise KnowledgeError("文档来源不能超过 500 字")
    text = str(content or "")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    size = len(text.encode("utf-8"))
    cleaned_tags = []
    for tag in tags or []:
        value = str(tag or "").strip()
        if value and value not in cleaned_tags:
            cleaned_tags.append(value[:40])
    due = _as_datetime(valid_to)
    guessed = mime or mimetypes.guess_type(name)[0] or ""

    row = db.scalar(
        select(KbDocument)
        .where(KbDocument.kb_id == kb.id, KbDocument.source == name)
        .order_by(KbDocument.created_at.desc())
        .limit(1)
    )
    if row is not None and (row.sha256 or "") == digest and row.status == "ready":
        # 内容未变：元数据可以改，但绝不重建文本块（保留既有向量缓存）。
        row.kind = str(kind or row.kind or "other")[:30]
        row.tags = cleaned_tags if tags is not None else list(row.tags or [])
        row.valid_to = due
        row.mime = guessed or row.mime
        row.error = ""
        db.flush()
        return row

    if row is None:
        row = KbDocument(
            kb_id=kb.id,
            source=name,
            sha256=digest,
            size=size,
            mime=guessed,
            status="ready",
            version=1,
            kind=str(kind or "other")[:30],
            tags=cleaned_tags,
            valid_to=due,
        )
        db.add(row)
        db.flush()
    else:
        # 内容摘要变化意味着旧审核结论针对的是另一份文本：撤销发布痕迹，
        # 新块回到草稿，重新走审核后才能进入检索/向量化/企业证据链路。
        # 历史文档（review_status IS NULL）保持 legacy 语义，不被写入 draft。
        if (row.sha256 or "") != digest and row.review_status is not None:
            row.review_status = "draft"  # knowledge_assets.DRAFT
            row.reviewed_by = None
            row.reviewed_at = None
            row.published_at = None
            row.rejected_reason = None
        row.version = int(row.version or 1) + 1
        row.sha256 = digest
        row.size = size
        row.mime = guessed
        row.status = "ready"
        row.kind = str(kind or row.kind or "other")[:30]
        row.tags = cleaned_tags if tags is not None else list(row.tags or [])
        row.valid_to = due
        row.error = ""
        db.flush()

    # 只清理该文档的旧块；同时清掉旧版全量索引遗留的、同 source 且无
    # document_id 的块，避免同源重复命中。
    db.execute(
        delete(KbChunk).where(
            KbChunk.kb_id == kb.id,
            or_(
                KbChunk.document_id == row.id,
                (KbChunk.document_id.is_(None)) & (KbChunk.source == name),
            ),
        )
    )
    pages = _document_pages(text)
    row.pages = len(pages)
    ordinal = 0
    for page_no, chunks in pages:
        for text_piece in chunks:
            piece_hash = hashlib.sha256(text_piece.encode("utf-8")).hexdigest()
            db.add(
                KbChunk(
                    kb_id=kb.id,
                    source=name,
                    ordinal=ordinal,
                    text=text_piece,
                    tokens=" ".join(tokens_for(text_piece)),
                    document_id=row.id,
                    page_no=page_no,
                    heading=_heading_for(text_piece),
                    chunk_index=ordinal,
                    hash=piece_hash,
                )
            )
            ordinal += 1
    db.flush()
    _refresh_chunk_count(db, kb)
    db.flush()
    return row


def cleanup_duplicate_references(
    db, kb_id: str, *, document_id: str | None = None
) -> None:
    """删除查重孤儿行：先清成员，再清本库中已空的候选组。

    ``document_id`` 给定时只清理该文档的成员（用于单文档删除）；否则清理
    整个知识库的成员（用于 KB 删除）。不改变任何已裁决组的非空成员。
    """

    if document_id is not None:
        db.execute(
            delete(KbAssetDuplicateMember).where(
                KbAssetDuplicateMember.document_id == document_id
            )
        )
    else:
        owned = select(KbAssetDuplicateGroup.id).where(
            KbAssetDuplicateGroup.kb_id == kb_id
        )
        db.execute(
            delete(KbAssetDuplicateMember).where(KbAssetDuplicateMember.group_id.in_(owned))
        )
    populated = select(KbAssetDuplicateMember.group_id)
    db.execute(
        delete(KbAssetDuplicateGroup).where(
            KbAssetDuplicateGroup.kb_id == kb_id,
            ~KbAssetDuplicateGroup.id.in_(populated),
        )
    )
    db.flush()


def delete_document(db, kb: KnowledgeBase, document_id: str) -> KbDocument:
    row = db.get(KbDocument, str(document_id or ""))
    if row is None or row.kb_id != kb.id:
        raise LookupError("文档不存在")
    db.execute(delete(KbChunk).where(KbChunk.document_id == row.id))
    cleanup_duplicate_references(db, kb.id, document_id=row.id)
    db.delete(row)
    db.flush()
    _refresh_chunk_count(db, kb)
    db.flush()
    return row


def list_documents(
    db, kb: KnowledgeBase, *, kind: str = "", status: str = ""
) -> list[KbDocument]:
    stmt = select(KbDocument).where(KbDocument.kb_id == kb.id)
    if kind:
        stmt = stmt.where(KbDocument.kind == kind)
    if status:
        stmt = stmt.where(KbDocument.status == status)
    return list(
        db.scalars(
            stmt.order_by(KbDocument.created_at.asc(), KbDocument.source.asc())
        )
    )


def _score(query_tokens: set[str], chunk_tokens: set[str], query: str, text: str) -> float:
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & chunk_tokens)
    if overlap == 0:
        return 0.0
    base = overlap / math.sqrt(len(query_tokens) * max(1, len(chunk_tokens)))
    if query and query in text:
        base += 0.2
    return max(0.0, min(1.0, base))


def _cosine(left, right) -> float | None:
    if not isinstance(left, list) or not isinstance(right, list):
        return None
    if not left or len(left) != len(right):
        return None
    try:
        dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(float(a) * float(a) for a in left))
        right_norm = math.sqrt(sum(float(b) * float(b) for b in right))
    except (TypeError, ValueError):
        return None
    if left_norm == 0 or right_norm == 0:
        return None
    return dot / (left_norm * right_norm)


def _as_freshness(valid_to) -> float:
    """归一化有效期剩余天数：无有效期取中性 0.5，过期取 0。"""

    if valid_to is None:
        return 0.5
    if isinstance(valid_to, datetime):
        due = valid_to.date()
    elif isinstance(valid_to, date):
        due = valid_to
    else:
        text = str(valid_to).strip()[:10]
        try:
            due = date.fromisoformat(text)
        except ValueError:
            return 0.5
    remaining = (due - clock.today()).days
    if remaining <= 0:
        return 0.0
    return min(1.0, remaining / 365.0)


def _as_readiness(row) -> float:
    """就绪度：ready 且 NULL/published 的资产优先，无文档取中性 0.5。"""

    if row is None:
        return 0.5
    status = str(getattr(row, "status", "") or "")
    review = getattr(row, "review_status", None)
    if status == "ready" and review in (None, "published"):
        return 1.0
    return 0.0


def _document_signals(db, rows: list[KbChunk]) -> dict[str, tuple[float, float]]:
    """批量取块所属文档的 (freshness, readiness)，不改动查询本身。"""

    wanted = {row.document_id for row in rows if row.document_id}
    if not wanted or db is None:
        return {}
    try:
        docs = list(db.scalars(select(KbDocument).where(KbDocument.id.in_(wanted))))
    except Exception:  # noqa: BLE001 - 轻量假 DB 没有真实文档表时保持中性信号
        logger.debug("读取知识文档生命周期信号失败", exc_info=True)
        return {}
    return {
        doc.id: (_as_freshness(getattr(doc, "valid_to", None)), _as_readiness(doc))
        for doc in docs
    }


def _score_lists(passages: list[Passage]) -> dict[tuple[str, str], list[int]]:
    """按 (知识库, 打分来源/分量) 拆成可比名次表；跨库跨算法不混排。"""

    lists: dict[tuple[str, str], list[int]] = {}
    for index, passage in enumerate(passages):
        kb_key = passage.kb_id or f"name:{passage.kb}"
        components = {
            name: float(value)
            for name, value in (passage.score_components or {}).items()
            if isinstance(value, (int, float))
        }
        if not components:
            components = {passage.score_source or "score": float(passage.score)}
        for name in components:
            lists.setdefault((kb_key, name), []).append(index)
        # 本地主分是 bigram/cosine 的加权和，单列一表保证至少一次 RRF 命中。
        if len(components) > 1:
            lists.setdefault((kb_key, f"{passage.score_source or 'local'}:score"), []).append(index)
    return lists


def rerank_hybrid(
    passages,
    *,
    limit: int | None = None,
) -> list[Passage]:
    """跨知识库融合排序（纯函数，不查库、不改输入对象）。

    每库和每种打分分量各自按 score 排名，用 RRF ``1/(K+rank)`` 融合，再加一个
    库内归一化分（同一知识库内才可比较）保留分数强度。同分时按有效期新鲜度、
    就绪度、原顺序稳定打破平局。原始 ``score`` 保持不变，融合分写入 ``rerank_score``。
    """

    items = list(passages)
    if not items:
        return []
    fused = [0.0] * len(items)
    for indices in _score_lists(items).values():
        ordered = sorted(
            indices,
            key=lambda idx: (
                -float(items[idx].score),
                -items[idx].freshness,
                -items[idx].readiness,
                idx,
            ),
        )
        for rank, idx in enumerate(ordered, start=1):
            fused[idx] += 1.0 / (RRF_K + rank)

    # 库内归一化：只在同一 kb_id 内做 min-max，避免跨库量纲污染。
    by_kb: dict[str, list[int]] = {}
    for index, passage in enumerate(items):
        by_kb.setdefault(passage.kb_id or f"name:{passage.kb}", []).append(index)
    for indices in by_kb.values():
        scores = [float(items[idx].score) for idx in indices]
        low, high = min(scores), max(scores)
        spread = high - low
        for idx in indices:
            normalized = (float(items[idx].score) - low) / spread if spread > 0 else 1.0
            fused[idx] += RRF_NORMALIZED_WEIGHT * normalized

    order = sorted(
        range(len(items)),
        key=lambda idx: (
            -fused[idx],
            -items[idx].freshness,
            -items[idx].readiness,
            idx,
        ),
    )
    ranked = [
        replace(items[idx], rerank_score=round(fused[idx], 6))
        for idx in order
    ]
    if limit is None:
        return ranked
    return ranked[: max(0, int(limit))]


def embed_texts(endpoint, texts: list[str]) -> list[list[float]]:
    """Embed ``texts`` in configured batches; raise ``KnowledgeError`` on failure."""
    items = [text or "" for text in texts]
    if not items:
        return []
    base = api_base(endpoint.model_base_url)
    if not base:
        raise KnowledgeError("未配置向量模型地址")
    if not endpoint.model_name:
        raise KnowledgeError("未配置向量模型名称")
    headers = {"Content-Type": "application/json"}
    if endpoint.model_api_key:
        headers["Authorization"] = f"Bearer {endpoint.model_api_key}"
    batch_size = max(1, int(settings.kb_vector_batch or 1))
    timeout = endpoint.model_timeout or TIMEOUT
    vectors: list[list[float]] = []
    dim = 0
    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        try:
            response = httpx.post(
                f"{base}/embeddings",
                headers=headers,
                json={"model": endpoint.model_name, "input": batch},
                timeout=timeout,
            )
            response.raise_for_status()
            _check_response_size(response, label="向量接口")
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "向量接口 HTTP %s: %s",
                exc.response.status_code,
                (exc.response.text or "")[:500],
            )
            raise KnowledgeError(f"向量接口 HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise KnowledgeError(f"向量接口失败：{exc}") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(batch):
            raise KnowledgeError("向量接口返回数量与请求不一致")
        for item in data:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or not vector:
                raise KnowledgeError("向量接口返回空向量")
            try:
                values = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise KnowledgeError("向量接口返回非法向量") from exc
            if dim and len(values) != dim:
                raise KnowledgeError("向量接口返回维度不一致")
            dim = len(values)
            vectors.append(values)
    return vectors


def releasable_review_filter(column=None):
    """资产审核门禁：历史（NULL/空串）与 published 放行，其余状态一律拦截。

    检索、企业匹配与向量化共用同一谓词，避免同一份块在一条链路可发布、
    在另一条链路仍被当成有效证据。
    """

    target = KbDocument.review_status if column is None else column
    return or_(target.is_(None), target == "", target == "published")


def build_vectors(db, kb: KnowledgeBase) -> dict:
    """Embed chunks whose vectors are missing or stale; never raises on model failure."""
    total = int(kb.chunk_count or 0)
    result = {"embedded": 0, "total": total, "model": "", "dim": 0, "error": ""}
    if kb.kind != "local":
        return {**result, "error": "仅本地知识库支持向量索引"}
    endpoint = resolve_embedding_endpoint(db)
    if endpoint is None:
        return {**result, "error": "未配置可用的向量模型"}
    key = _embed_key(endpoint)
    result["model"] = endpoint.model_name
    stale = or_(KbChunk.embed_key.is_(None), KbChunk.embed_key != key)
    statement = (
        select(KbChunk)
        .join(KbDocument, KbChunk.document_id == KbDocument.id, isouter=True)
        .where(KbChunk.kb_id == kb.id, stale, releasable_review_filter())
        .order_by(KbChunk.source, KbChunk.ordinal, KbChunk.id)
    )
    pending = int(
        db.scalar(
            select(func.count())
            .select_from(KbChunk)
            .join(KbDocument, KbChunk.document_id == KbDocument.id, isouter=True)
            .where(KbChunk.kb_id == kb.id, stale, releasable_review_filter())
        )
        or 0
    )
    cap = settings.kb_vector_max_chunks
    rows = list(db.scalars(statement.limit(cap) if cap > 0 else statement))
    errors: list[str] = []
    if cap > 0 and pending > cap:
        errors.append(f"待向量化文本块 {pending} 个，超过上限，仅处理前 {cap} 个")
    batch_size = max(1, int(settings.kb_vector_batch or 1))
    embedded = 0
    dim = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        try:
            vectors = embed_texts(endpoint, [row.text or "" for row in batch])
        except KnowledgeError as exc:
            errors.append(str(exc))
            break
        if len(vectors) != len(batch):
            errors.append("向量接口返回数量与请求不一致")
            break
        for row, vector in zip(batch, vectors, strict=False):
            row.embedding = vector
            row.embed_key = key
            row.embed_dim = len(vector)
            dim = len(vector)
            embedded += 1
        db.commit()
    if dim == 0:
        dim = int(
            db.scalar(
                select(KbChunk.embed_dim)
                .where(
                    KbChunk.kb_id == kb.id,
                    KbChunk.embed_key == key,
                    KbChunk.embed_dim > 0,
                )
                .order_by(KbChunk.ordinal)
                .limit(1)
            )
            or 0
        )
    result["embedded"] = embedded
    result["dim"] = dim
    result["error"] = "；".join(errors)
    return result


def _query_vector(db, query: str) -> tuple[str, list[float] | None]:
    if not settings.kb_vector_enabled:
        return "", None
    try:
        endpoint = resolve_embedding_endpoint(db)
    except Exception:
        logger.debug("解析向量模型失败，回退关键词检索", exc_info=True)
        return "", None
    if endpoint is None:
        return "", None
    try:
        vectors = embed_texts(endpoint, [query])
    except (KnowledgeError, OSError) as exc:
        logger.debug("查询向量化失败，回退关键词检索：%s", exc)
        return "", None
    if not vectors:
        return "", None
    return _embed_key(endpoint), vectors[0]


def _ready_chunk_filter():
    """文档生命周期门禁：未就绪/已过期/未发布的资产不参与检索（旧块兼容放行）。"""

    today = clock.today()
    start = datetime(today.year, today.month, today.day)
    # 结构化资产的审核门禁与向量化/企业匹配共用，历史 NULL/空串与 published
    # 可检索；draft/review/approved/rejected/archived 一律显式拦截。
    return or_(
        KbDocument.id.is_(None),  # 迁移前的无溯源块仍可检索
        and_(
            KbDocument.status == "ready",
            or_(KbDocument.valid_to.is_(None), KbDocument.valid_to >= start),
            releasable_review_filter(),
        ),
    )


def search_local(db, kb: KnowledgeBase, query: str, limit: int) -> list[Passage]:
    """SQL 预筛候选块后再精确打分，避免每次查询全表重建 token 集合。"""

    query_tokens = sorted(set(tokens_for(query)))
    key, query_vector = _query_vector(db, query)
    if not query_tokens and query_vector is None:
        return []
    weight = settings.kb_vector_weight
    min_score = settings.kb_vector_min_score
    cap = min(SEARCH_CANDIDATE_CAP, max(200, int(limit) * 50))
    candidates: dict[str, KbChunk] = {}

    if query_tokens:
        token_terms = [KbChunk.tokens.contains(token) for token in query_tokens[:MAX_QUERY_TOKENS]]
        stmt = (
            select(KbChunk)
            .join(KbDocument, KbChunk.document_id == KbDocument.id, isouter=True)
            .where(KbChunk.kb_id == kb.id, _ready_chunk_filter(), or_(*token_terms))
            .limit(cap)
        )
        for row in db.scalars(stmt):
            candidates[row.id] = row

    if key and query_vector:
        vector_cap = int(settings.kb_vector_max_chunks or 0)
        vector_cap = vector_cap if vector_cap > 0 else SEARCH_CANDIDATE_CAP
        stmt = (
            select(KbChunk)
            .join(KbDocument, KbChunk.document_id == KbDocument.id, isouter=True)
            .where(
                KbChunk.kb_id == kb.id,
                KbChunk.embed_key == key,
                KbChunk.embedding.is_not(None),
                _ready_chunk_filter(),
            )
            .limit(max(1, min(vector_cap, SEARCH_CANDIDATE_CAP)))
        )
        for row in db.scalars(stmt):
            candidates[row.id] = row

    scored: list[tuple[float, dict[str, float], KbChunk]] = []
    for row in candidates.values():
        bigram = _score(set(query_tokens), set((row.tokens or "").split()), query, row.text)
        components = {"bigram": round(bigram, 3)}
        if query_vector is None:
            # Pure bigram fallback keeps the historical ``score > 0`` semantics.
            if bigram > 0:
                scored.append((bigram, components, row))
            continue
        score = bigram
        if key and row.embed_key == key and row.embedding:
            similarity = _cosine(query_vector, row.embedding)
            if similarity is not None:
                components["cosine"] = round(max(0.0, similarity), 3)
                score = (1 - weight) * bigram + weight * max(0.0, similarity)
        if score >= min_score:
            scored.append((score, components, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    selected = scored[:limit]
    signals = _document_signals(db, [row for _, _, row in selected])
    passages: list[Passage] = []
    for score, components, row in selected:
        freshness, readiness = signals.get(row.document_id, (0.5, 0.5))
        passages.append(
            Passage(
                kb=kb.name,
                kb_id=kb.id,
                source=row.source,
                text=row.text,
                score=round(score, 3),
                document_id=row.document_id,
                page=row.page_no,
                heading=row.heading or "",
                chunk_id=row.id,
                chunk_index=row.chunk_index,
                score_source="local",
                score_components=components,
                freshness=freshness,
                readiness=readiness,
            )
        )
    return passages


def _api_headers(kb: KnowledgeBase) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = decrypt_secret(kb.api_key_enc)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _real_field(source: dict | None, keys: tuple[str, ...]) -> str:
    """只取上游真实字段；取不到就留空，绝不拿知识库名伪造出处。"""

    if not isinstance(source, dict):
        return ""
    for key in keys:
        value = source.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                return text[:300]
    return ""


def _real_page(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit() and int(value) > 0:
        return int(value)
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict):
                page = _real_page(
                    entry.get("page") or entry.get("page_no") or entry.get("page_number")
                )
                if page:
                    return page
    return None


def _real_index(value) -> int | None:
    """Provider 的 position/index 允许 0；非数字留空。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _passages_from_items(kb: KnowledgeBase, items: list, limit: int) -> list[Passage]:
    """Dify / RAGFlow / 通用接口结果 → Passage（只透传真实 provider 溯源字段）。"""

    passages: list[Passage] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        chunk = item.get("segment") or item.get("chunk") or item
        if not isinstance(chunk, dict):
            chunk = {}
        text = ""
        for key in ("content", "text", "answer", "snippet", "content_with_weight"):
            value = chunk.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
        if not text:
            continue
        nested = chunk.get("document") if isinstance(chunk.get("document"), dict) else {}
        source = _real_field(
            chunk, ("document_name", "document_keyword", "doc_name", "file_name", "source", "name", "title")
        ) or _real_field(nested, ("name", "source", "title"))
        document_id = _real_field(chunk, ("document_id", "doc_id")) or _real_field(
            nested, ("id", "document_id")
        )
        chunk_id = _real_field(chunk, ("id", "chunk_id", "segment_id")) or _real_field(
            item, ("id", "chunk_id", "segment_id")
        )
        page = _real_page(
            chunk.get("page") or chunk.get("page_no") or chunk.get("page_number")
            or chunk.get("positions")
        )
        heading = _real_field(chunk, ("heading", "section", "chunk_title", "title"))
        chunk_index = _real_index(
            chunk.get("chunk_index", chunk.get("position", chunk.get("index")))
        )
        score = chunk.get("score", chunk.get("similarity", chunk.get("relevance", item.get("score", 0))))
        try:
            score_value = float(score)
        except (TypeError, ValueError):
            score_value = 0.0
        passages.append(
            Passage(
                kb=kb.name,
                kb_id=kb.id,
                source=source,
                text=text[:1500],
                score=score_value,
                document_id=document_id or None,
                page=page,
                heading=heading,
                chunk_id=chunk_id or None,
                chunk_index=chunk_index,
                score_source=str(kb.provider or "api"),
                score_components={"api": round(score_value, 3)},
            )
        )
    return passages


def search_api(db, kb: KnowledgeBase, query: str, limit: int) -> list[Passage]:
    base = (kb.location or "").rstrip("/")
    if not base:
        raise KnowledgeError("未配置 API 地址")
    headers = _api_headers(kb)
    try:
        if kb.provider == "dify":
            if not kb.dataset_id:
                raise KnowledgeError("Dify 需要数据集 ID")
            response = httpx.post(
                f"{base}/datasets/{kb.dataset_id}/retrieve",
                headers=headers,
                json={
                    "query": query,
                    "retrieval_model": {
                        "search_method": "hybrid_search",
                        "reranking_enable": False,
                        "top_k": limit,
                        "score_threshold_enabled": False,
                    },
                },
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            _check_response_size(response)
            return _passages_from_items(kb, response.json().get("records") or [], limit)

        if kb.provider == "ragflow":
            if not kb.dataset_id:
                raise KnowledgeError("RAGFlow 需要数据集 ID")
            response = httpx.post(
                f"{base}/retrieval",
                headers=headers,
                json={
                    "question": query,
                    "dataset_ids": [kb.dataset_id],
                    "page_size": limit,
                },
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            _check_response_size(response)
            payload = response.json()
            data = payload.get("data") or {}
            chunks = data.get("chunks") or []
            return _passages_from_items(kb, chunks, limit)

        response = httpx.post(
            base,
            headers=headers,
            json={"query": query, "top_k": limit},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        _check_response_size(response)
        payload = response.json()
        items = payload.get("results") or payload.get("data") or payload.get("documents") or []
        if isinstance(items, dict):
            items = items.get("results") or items.get("items") or []
        return _passages_from_items(kb, list(items), limit)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "知识库接口 HTTP %s: %s",
            exc.response.status_code,
            (exc.response.text or "")[:500],
        )
        raise KnowledgeError(f"知识库接口 HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise KnowledgeError(f"知识库接口失败：{exc}") from exc


def _kb_scope(kb: KnowledgeBase, *, project_id, package_id) -> tuple[bool, str]:
    """按 ``kb.options`` 的项目/标段声明过滤，声明与调用方缺一即失败关闭。"""

    options = kb.options if isinstance(kb.options, dict) else {}
    for key, requested in (("project_id", project_id), ("package_id", package_id)):
        declared = options.get(key)
        if declared in (None, ""):
            continue
        if isinstance(declared, bool) or not isinstance(declared, (str, int)):
            return False, f"知识库 {key} 声明无效"
        declared_text = str(declared).strip()
        if not declared_text:
            continue
        requested_text = str(requested).strip() if requested is not None else ""
        if not requested_text:
            return False, f"知识库限定 {key}，调用方未提供"
        if requested_text != declared_text:
            return False, f"知识库限定 {key} 不匹配"
    return True, ""


def search(
    db,
    query: str,
    *,
    limit: int = 4,
    kb_ids: list[str] | None = None,
    include_personal: bool = False,
    extra_kb_ids: list[str] | None = None,
    project_id: str | None = None,
    package_id: str | None = None,
    with_diagnostics: bool = False,
) -> list[Passage]:
    """Retrieve passages from enabled knowledge bases.

    Personal knowledge bases (``options.personal``) are opt-in: they are only
    searched when ``include_personal=True`` so one user's uploads never leak
    into another user's generation context.

    ``kb_ids`` 保持**排他**语义（调用方显式限定检索范围）。``extra_kb_ids`` 是
    **追加**语义：在全局已启用库之外再纳入这些库，且被追加的个人库可豁免
    ``include_personal`` 过滤——用于「全局库 + 当前用户个人库」联合检索。

    ``project_id`` / ``package_id`` 是可选检索范围：``kb.options`` 声明了同名
    值的库只在该范围匹配时参与检索（声明缺失/类型非法/不匹配一律跳过，失败关闭）。
    ``with_diagnostics=True`` 返回 :class:`SearchResult`，其 ``diagnostics`` 含
    ``searched`` / ``skipped`` / ``failed``，默认调用方仍得到普通 list。
    """

    diagnostics: dict = {
        "scope": {"project_id": project_id, "package_id": package_id},
        "searched": [],
        "skipped": [],
        "failed": [],
    }
    if not (query or "").strip():
        return SearchResult([], diagnostics=diagnostics) if with_diagnostics else []
    statement = select(KnowledgeBase).where(KnowledgeBase.enabled.is_(True))
    if kb_ids:
        statement = statement.where(KnowledgeBase.id.in_(kb_ids))
    bases = list(db.scalars(statement.order_by(KnowledgeBase.is_default.desc())))
    extra_ids = {str(item) for item in (extra_kb_ids or []) if item}
    if extra_ids:
        known = {kb.id for kb in bases}
        extras = list(
            db.scalars(
                select(KnowledgeBase)
                .where(KnowledgeBase.enabled.is_(True), KnowledgeBase.id.in_(extra_ids))
                .order_by(KnowledgeBase.is_default.desc())
            )
        )
        for kb in extras:
            if kb.id not in known:
                bases.append(kb)
                known.add(kb.id)
    if not include_personal:
        bases = [
            kb
            for kb in bases
            if kb.id in extra_ids or not (kb.options or {}).get("personal")
        ]
    passages: list[Passage] = []
    for kb in bases:
        allowed, reason = _kb_scope(kb, project_id=project_id, package_id=package_id)
        if not allowed:
            diagnostics["skipped"].append(
                {"kb_id": kb.id, "kb": kb.name, "reason": reason}
            )
            continue
        try:
            if kb.kind == "local":
                found = search_local(db, kb, query, limit)
            else:
                found = search_api(db, kb, query, limit)
        except (KnowledgeError, OSError) as exc:
            # 单库失败不再静默丢失：诊断里留痕，同时记录 warning。
            logger.warning("知识库检索失败 kb=%s: %s", kb.id, exc)
            diagnostics["failed"].append(
                {
                    "kb_id": kb.id,
                    "kb": kb.name,
                    "provider": kb.provider,
                    "error": f"{exc.__class__.__name__}: {exc}"[:300],
                }
            )
            continue
        diagnostics["searched"].append(
            {"kb_id": kb.id, "kb": kb.name, "provider": kb.provider, "count": len(found)}
        )
        passages.extend(found)
    ranked = rerank_hybrid(passages, limit=limit)
    if with_diagnostics:
        return SearchResult(ranked, diagnostics=diagnostics)
    return ranked


def trace_label(passage: Passage) -> str:
    """文档溯源标签：文档 ID / 页码 / 标题 / 块 ID，缺失字段自动省略。"""

    bits: list[str] = []
    if passage.document_id:
        bits.append(f"文档 {passage.document_id}")
    if passage.page:
        bits.append(f"页码 {passage.page}")
    if passage.heading:
        bits.append(f"标题 {passage.heading}")
    if passage.chunk_id:
        chunk = f"块 {passage.chunk_id}"
        if passage.chunk_index is not None:
            chunk += f"#{passage.chunk_index}"
        bits.append(chunk)
    return " · ".join(bits)


def context_block(passages: list[Passage]) -> str:
    if not passages:
        return ""
    rows = []
    for index, passage in enumerate(passages, start=1):
        source = f"（{passage.source}）" if passage.source else ""
        trace = trace_label(passage)
        suffix = f"[{trace}]" if trace else ""
        rows.append(f"[{index}] {source}{suffix}\n{passage.text}")
    return (
        "【知识库参考】\n"
        "以下资料只用于补充行业背景、产品能力与实施方法；资质、业绩、价格、人员与参数"
        "仍必须以事实台账为准，不得据此编造。\n\n" + "\n\n".join(rows)
    )


def test_kb(db, kb: KnowledgeBase) -> tuple[bool, str, int]:
    started = time.monotonic()
    try:
        if kb.kind == "local":
            root = Path(kb.location).expanduser()
            if not root.exists() or not root.is_dir():
                return False, f"目录不存在：{root}", 0
            files = list(_iter_files(root))
            return True, f"目录可读，发现 {len(files)} 个可索引文件", _elapsed(started)
        passages = search_api(db, kb, "投标文件", 1)
        return True, f"接口可用，返回 {len(passages)} 条结果", _elapsed(started)
    except KnowledgeError as exc:
        return False, str(exc), _elapsed(started)
    except OSError as exc:
        return False, str(exc), _elapsed(started)


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
