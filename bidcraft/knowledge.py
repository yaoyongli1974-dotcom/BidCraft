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
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from sqlalchemy import and_, delete, func, or_, select

from . import clock
from .config import settings
from .model_config import api_base, resolve_embedding_endpoint
from .models import KbChunk, KbDocument, KnowledgeBase, utcnow
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

    @property
    def page_no(self) -> int | None:
        """Alias used by callers that speak in ``KbChunk.page_no`` terms."""

        return self.page


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


def delete_document(db, kb: KnowledgeBase, document_id: str) -> KbDocument:
    row = db.get(KbDocument, str(document_id or ""))
    if row is None or row.kb_id != kb.id:
        raise LookupError("文档不存在")
    db.execute(delete(KbChunk).where(KbChunk.document_id == row.id))
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
    statement = (
        select(KbChunk)
        .where(
            KbChunk.kb_id == kb.id,
            or_(KbChunk.embed_key.is_(None), KbChunk.embed_key != key),
        )
        .order_by(KbChunk.source, KbChunk.ordinal, KbChunk.id)
    )
    pending = int(
        db.scalar(
            select(func.count())
            .select_from(KbChunk)
            .where(
                KbChunk.kb_id == kb.id,
                or_(KbChunk.embed_key.is_(None), KbChunk.embed_key != key),
            )
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
    # 结构化资产的审核门禁：只有历史行（review_status IS NULL）与 published
    # 可检索；draft/review/approved/rejected/archived 一律显式拦截。
    review_gate = or_(
        KbDocument.review_status.is_(None),  # 旧文档未经资产抽取，保持既有行为
        KbDocument.review_status == "published",
    )
    return or_(
        KbDocument.id.is_(None),  # 迁移前的无溯源块仍可检索
        and_(
            KbDocument.status == "ready",
            or_(KbDocument.valid_to.is_(None), KbDocument.valid_to >= start),
            review_gate,
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

    scored: list[tuple[float, KbChunk]] = []
    for row in candidates.values():
        bigram = _score(set(query_tokens), set((row.tokens or "").split()), query, row.text)
        if query_vector is None:
            # Pure bigram fallback keeps the historical ``score > 0`` semantics.
            if bigram > 0:
                scored.append((bigram, row))
            continue
        score = bigram
        if key and row.embed_key == key and row.embedding:
            similarity = _cosine(query_vector, row.embedding)
            if similarity is not None:
                score = (1 - weight) * bigram + weight * max(0.0, similarity)
        if score >= min_score:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        Passage(
            kb=kb.name,
            kb_id=kb.id,
            source=row.source,
            text=row.text,
            score=round(score, 3),
            document_id=row.document_id,
            page=row.page_no,
            heading=row.heading or "",
        )
        for score, row in scored[:limit]
    ]


def _api_headers(kb: KnowledgeBase) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = decrypt_secret(kb.api_key_enc)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _passages_from_items(kb: KnowledgeBase, items: list, limit: int) -> list[Passage]:
    passages: list[Passage] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        chunk = item.get("segment") or item.get("chunk") or item
        text = ""
        for key in ("content", "text", "answer", "snippet", "content_with_weight"):
            value = chunk.get(key) if isinstance(chunk, dict) else None
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
        if not text:
            continue
        source = ""
        for key in ("document_keyword", "document_name", "title", "source", "doc_name"):
            value = chunk.get(key) if isinstance(chunk, dict) else None
            if isinstance(value, str) and value.strip():
                source = value.strip()
                break
        if not source and isinstance(chunk.get("document"), dict):
            source = str(chunk["document"].get("name") or "")
        score = chunk.get("score") or item.get("score") or 0
        try:
            score_value = float(score)
        except (TypeError, ValueError):
            score_value = 0.0
        passages.append(
            Passage(
                kb=kb.name,
                kb_id=kb.id,
                source=source or kb.name,
                text=text[:1500],
                score=score_value,
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


def search(
    db,
    query: str,
    *,
    limit: int = 4,
    kb_ids: list[str] | None = None,
    include_personal: bool = False,
    extra_kb_ids: list[str] | None = None,
) -> list[Passage]:
    """Retrieve passages from enabled knowledge bases.

    Personal knowledge bases (``options.personal``) are opt-in: they are only
    searched when ``include_personal=True`` so one user's uploads never leak
    into another user's generation context.

    ``kb_ids`` 保持**排他**语义（调用方显式限定检索范围）。``extra_kb_ids`` 是
    **追加**语义：在全局已启用库之外再纳入这些库，且被追加的个人库可豁免
    ``include_personal`` 过滤——用于「全局库 + 当前用户个人库」联合检索。
    """

    if not (query or "").strip():
        return []
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
        try:
            if kb.kind == "local":
                passages.extend(search_local(db, kb, query, limit))
            else:
                passages.extend(search_api(db, kb, query, limit))
        except (KnowledgeError, OSError):
            continue
    passages.sort(key=lambda item: item.score, reverse=True)
    return passages[:limit]


def trace_label(passage: Passage) -> str:
    """文档溯源标签：文档 ID / 页码 / 标题，缺失字段自动省略。"""

    bits: list[str] = []
    if passage.document_id:
        bits.append(f"文档 {passage.document_id}")
    if passage.page:
        bits.append(f"页码 {passage.page}")
    if passage.heading:
        bits.append(f"标题 {passage.heading}")
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
