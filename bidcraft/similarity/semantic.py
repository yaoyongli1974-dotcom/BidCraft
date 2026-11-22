"""语义查重（向量余弦），模型不可用时回退到文本查重。

复用 :func:`bidcraft.knowledge.embed_texts` 与 ``knowledge._cosine``。
向量接口的批次、超时、离线开关都由现有配置决定；本模块只做：
截断过长段落、限制单次规模、在失败时降级并显式记录 ``degraded``。
"""

from __future__ import annotations

from typing import Any

from ..knowledge import KnowledgeError, _cosine, embed_texts
from ..model_config import resolve_embedding_endpoint
from . import text as text_engine

DEFAULT_THRESHOLD = 0.82
MAX_ITEMS = 600
MAX_CHARS = 2000
DEFAULT_MAX_HITS = 500


def _paragraph_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("text") or "")
    return str(item or "")


def embed_paragraphs(
    db,
    paragraphs: list[Any],
    *,
    endpoint=None,
    max_items: int = MAX_ITEMS,
    max_chars: int = MAX_CHARS,
) -> dict[str, Any]:
    """为段落生成向量（截断 + 限量）。

    返回 ``{"vectors": [...], "degraded": bool, "reason": str,
    "model": str, "dim": int, "embedded": int, "total": int}``。
    模型不可用时不抛错，``vectors`` 为空，由调用方回退文本查重。
    """

    items = list(paragraphs)[: max(1, max_items)]
    result: dict[str, Any] = {
        "vectors": [],
        "degraded": False,
        "reason": "",
        "model": "",
        "model_provider": "",
        "dim": 0,
        "embedded": 0,
        "total": len(items),
    }
    if not items:
        return result
    resolved = endpoint if endpoint is not None else resolve_embedding_endpoint(db)
    if resolved is None:
        result.update(
            degraded=True,
            reason="未配置可用的向量模型",
        )
        return result
    result["model"] = str(getattr(resolved, "model_name", "") or "")
    result["model_provider"] = str(getattr(resolved, "provider", "") or "")
    texts = [_paragraph_text(item)[:max_chars] for item in items]
    try:
        vectors = embed_texts(resolved, texts)
    except KnowledgeError as exc:
        result.update(degraded=True, reason=str(exc))
        return result
    except Exception as exc:  # noqa: BLE001 - 向量服务异常一律降级，不阻断查重
        result.update(degraded=True, reason=f"向量接口异常：{exc}")
        return result
    if len(vectors) != len(texts):
        result.update(degraded=True, reason="向量接口返回数量与请求不一致")
        return result
    result["vectors"] = vectors
    result["embedded"] = len(vectors)
    result["dim"] = len(vectors[0]) if vectors else 0
    return result


def compare_semantic_vectors(
    vectors_a: list[list[float]],
    vectors_b: list[list[float]] | None = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    same_set: bool | None = None,
) -> list[dict[str, Any]]:
    """两两余弦相似；返回 ``[{"i", "j", "similarity"}]``（降序）。

    ``vectors_b`` 为空时在同一组内做 ``i < j`` 比较。
    """

    left = list(vectors_a or [])
    right = list(vectors_b) if vectors_b is not None else left
    in_same_set = (vectors_b is None) if same_set is None else same_set
    hits: list[dict[str, Any]] = []
    for i, vector_a in enumerate(left):
        start = i + 1 if in_same_set else 0
        for j in range(start, len(right)):
            if in_same_set and i == j:
                continue
            score = _cosine(vector_a, right[j])
            if score is None or score + 1e-12 < threshold:
                continue
            hits.append({"i": i, "j": j, "similarity": round(float(score), 4)})
    hits.sort(key=lambda item: (-item["similarity"], item["i"], item["j"]))
    return hits


def _semantic_hit(
    left: dict[str, Any],
    right: dict[str, Any],
    score: float,
    *,
    model: str,
    vector_similarity: float | None = None,
    method: str = "embedding",
    model_provider: str = "",
) -> dict[str, Any]:
    hit = text_engine._pair_hit(left, right, score)
    hit["kind"] = "semantic"
    evidence = dict(hit["evidence"])
    evidence.update(
        {
            "method": method,
            "model": model,
            "model_provider": model_provider,
            "algorithm": "knowledge._cosine",
        }
    )
    if vector_similarity is not None:
        evidence["vector_similarity"] = vector_similarity
    hit["evidence"] = evidence
    return hit


def find_semantic_duplicates(
    db,
    paragraphs_a: list[dict[str, Any]],
    paragraphs_b: list[dict[str, Any]] | None = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    vectors_a: list[list[float]] | None = None,
    vectors_b: list[list[float]] | None = None,
    max_hits: int = DEFAULT_MAX_HITS,
) -> dict[str, Any]:
    """查找语义重复；模型不可用时自动回退到 ``text.compare_sections``。

    ``vectors_a`` / ``vectors_b`` 可显式注入，便于测试不依赖真实模型。
    """

    same_set = paragraphs_b is None
    right = paragraphs_a if same_set else list(paragraphs_b or [])
    model = ""
    model_provider = ""
    degraded = False
    reason = ""

    if vectors_a is None:
        embedded_a = embed_paragraphs(db, paragraphs_a)
        vectors_a = embedded_a["vectors"]
        model = embedded_a["model"]
        model_provider = embedded_a.get("model_provider", "")
        degraded = embedded_a["degraded"]
        reason = embedded_a["reason"]
    if not same_set and vectors_b is None:
        embedded_b = embed_paragraphs(db, paragraphs_b or [])
        vectors_b = embedded_b["vectors"]
        model = model or embedded_b["model"]
        model_provider = model_provider or embedded_b.get("model_provider", "")
        degraded = degraded or embedded_b["degraded"]
        reason = reason or embedded_b["reason"]

    if degraded or not vectors_a or (not same_set and not vectors_b):
        fallback = text_engine.compare_sections(
            paragraphs_a,
            None if same_set else list(paragraphs_b or []),
            threshold=threshold,
            max_hits=max_hits,
        )
        for hit in fallback["hits"]:
            hit["kind"] = "semantic"
            evidence = dict(hit["evidence"])
            evidence.update(
                {
                    "method": "text-fallback",
                    "model": model,
                    "model_provider": model_provider,
                    "degraded": True,
                    "reason": reason or "没有可用向量",
                }
            )
            hit["evidence"] = evidence
        fallback["stats"].update(
            {
                "mode": "semantic",
                "degraded": True,
                "reason": reason or "没有可用向量",
                "model": model,
                "model_provider": model_provider,
            }
        )
        return {
            "hits": fallback["hits"],
            "stats": fallback["stats"],
            "skipped": (
                [{"mode": "semantic", "reason": reason or "没有可用向量"}]
                if reason
                else []
            ),
        }

    pairs = compare_semantic_vectors(
        vectors_a,
        vectors_b if not same_set else None,
        threshold=threshold,
        same_set=same_set,
    )
    hits: list[dict[str, Any]] = []
    for pair in pairs[: max(1, max_hits)]:
        i, j = pair["i"], pair["j"]
        if i >= len(paragraphs_a) or j >= len(right):
            continue
        hits.append(
            _semantic_hit(
                paragraphs_a[i],
                right[j],
                pair["similarity"],
                model=model,
                vector_similarity=pair["similarity"],
                model_provider=model_provider,
            )
        )
    hits = rerank_similarity_hits(hits)
    stats = {
        "mode": "semantic",
        "threshold": threshold,
        "paragraphs": len(paragraphs_a) + (0 if same_set else len(right)),
        "candidates": len(pairs),
        "hits": len(hits),
        "degraded": False,
        "model": model,
        "model_provider": model_provider,
        "dim": len(vectors_a[0]) if vectors_a else 0,
        "cross_project": not same_set,
    }
    return {"hits": hits, "stats": stats, "skipped": []}


def rerank_similarity_hits(
    hits: list[dict[str, Any]],
    *,
    semantic_weight: float = 0.65,
) -> list[dict[str, Any]]:
    """按语义分与文本分加权重排（原地返回新列表）。

    若 ``evidence.vector_similarity`` 存在，最终分 =
    ``semantic_weight * 向量分 + (1 - semantic_weight) * 文本分``；
    否则沿用原相似度。结果写回 ``evidence.rerank_score`` 并降序排列。
    """

    weight = max(0.0, min(1.0, float(semantic_weight)))
    ranked: list[dict[str, Any]] = []
    for original in hits:
        hit = dict(original)
        evidence = dict(hit.get("evidence") or {})
        base = float(hit.get("similarity") or 0.0)
        vector = evidence.get("vector_similarity")
        if isinstance(vector, (int, float)):
            score = weight * float(vector) + (1 - weight) * base
        else:
            score = base
        evidence["rerank_score"] = round(score, 4)
        hit["evidence"] = evidence
        hit["rerank_score"] = round(score, 4)
        ranked.append(hit)
    ranked.sort(key=lambda item: (-float(item["rerank_score"]), str(item.get("loc_a") or "")))
    return ranked
