"""Knowledge base indexing and retrieval.

Two kinds are supported:

* ``local`` — a directory of PDF/DOCX/TXT/Markdown files indexed into SQLite
  with a dependency-free character-bigram scorer.
* ``api`` — a third-party retrieval endpoint (generic JSON, Dify or RAGFlow).

Retrieved passages are advisory context only; ledger facts always win.
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import delete, func, or_, select

from .config import settings
from .model_config import api_base, resolve_embedding_endpoint
from .models import KbChunk, KnowledgeBase, utcnow
from .security import decrypt_secret

logger = logging.getLogger("bidcraft.knowledge")

SUPPORTED = {".pdf", ".docx", ".txt", ".md", ".markdown", ".xlsx", ".xlsm"}
MAX_FILES = 500
MAX_FILE_BYTES = 25 * 1024 * 1024
CHUNK_CHARS = 900
OVERLAP_CHARS = 120
TIMEOUT = 20.0
MAX_API_RESPONSE_BYTES = 2 * 1024 * 1024

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
    root = Path(kb.location).expanduser()
    if not root.exists() or not root.is_dir():
        raise KnowledgeError(f"目录不存在：{root}")
    from .parsing.loader import load_document

    db.execute(delete(KbChunk).where(KbChunk.kb_id == kb.id))
    total = 0
    for path in _iter_files(root):
        try:
            document = load_document(path)
        except Exception:
            logger.debug("跳过无法解析的知识库文件 %s", path, exc_info=True)
            continue
        chunks = _chunks(document.text)
        for ordinal, text in enumerate(chunks):
            db.add(
                KbChunk(
                    kb_id=kb.id,
                    source=str(path.relative_to(root)),
                    ordinal=ordinal,
                    text=text,
                    tokens=" ".join(tokens_for(text)),
                )
            )
            total += 1
    kb.chunk_count = total
    kb.indexed_at = utcnow()
    db.flush()
    return total


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


def search_local(db, kb: KnowledgeBase, query: str, limit: int) -> list[Passage]:
    rows = list(db.scalars(select(KbChunk).where(KbChunk.kb_id == kb.id)))
    query_tokens = set(tokens_for(query))
    key, query_vector = _query_vector(db, query)
    weight = settings.kb_vector_weight
    min_score = settings.kb_vector_min_score
    scored: list[tuple[float, KbChunk]] = []
    for row in rows:
        bigram = _score(query_tokens, set(row.tokens.split()), query, row.text)
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
        Passage(kb=kb.name, source=row.source, text=row.text, score=round(score, 3))
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
            Passage(kb=kb.name, source=source or kb.name, text=text[:1500], score=score_value)
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


def context_block(passages: list[Passage]) -> str:
    if not passages:
        return ""
    rows = []
    for index, passage in enumerate(passages, start=1):
        source = f"（{passage.source}）" if passage.source else ""
        rows.append(f"[{index}] {source}\n{passage.text}")
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
