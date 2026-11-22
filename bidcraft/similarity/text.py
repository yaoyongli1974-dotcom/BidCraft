"""段落级文本查重。

设计要点：

* 复用 :mod:`bidcraft.dedup` 的 ``split_paragraphs`` / ``normalize`` /
  ``similarity``（字符二元组 Jaccard），不另写算法。
* 只读 ``OutlineNode`` + ``Section.body``，任何查重动作都不写回章节。
* 固定条款（法律法规、国家标准、招标文件原文、合同固定条款、企业统一声明）
  命中后仍保留为重复候选，但在 ``evidence`` 中标记为「合理重复候选」，
  交由人工判断，不自动删除或改写。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from ..dedup import normalize, similarity, split_paragraphs
from ..models import OutlineNode, Section

# 与 dedup 的 80 字门槛相比略宽松：标书章节里 40 字以上的段落已足够承载
# 一个可比较的完整表述，同时避免把短标题/口号当作重复。
DEFAULT_MIN_CHARS = 40
DEFAULT_THRESHOLD = 0.85
DEFAULT_MAX_HITS = 500
DEFAULT_MAX_PARAGRAPHS = 4000
EXCERPT_CHARS = 120

_WS_RE = re.compile(r"\s+")

# 固定条款识别（顺序即优先级）。命中即「合理重复候选」，不删除。
_COMMON_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "enterprise_declaration",
        "企业统一声明",
        (
            "本公司",
            "我公司",
            "我单位",
            "郑重承诺",
            "特此声明",
            "声明如下",
            "承诺如下",
            "法定代表人",
            "授权代表",
            "全权代表",
        ),
    ),
    (
        "contract_clause",
        "合同固定条款",
        (
            "违约责任",
            "争议解决",
            "不可抗力",
            "保密条款",
            "合同生效",
            "合同解除",
            "付款方式",
            "付款条件",
            "质保金",
            "履约保证金",
            "合同价款",
            "甲方",
            "乙方",
        ),
    ),
    (
        "tender_original",
        "招标文件原文",
        (
            "招标文件",
            "投标人须知",
            "投标须知",
            "评标办法",
            "评标标准",
            "招标公告",
            "采购文件",
            "采购人",
            "投标人应当",
            "投标人须",
            "本项目招标",
        ),
    ),
    (
        "standard_code",
        "法律法规/国家标准",
        (
            "国家标准",
            "行业标准",
            "技术规范",
            "验收规范",
            "设计规范",
            "施工规范",
        ),
    ),
    (
        "law_regulation",
        "法律法规/国家标准",
        (
            "中华人民共和国",
            "政府采购法",
            "招标投标法",
            "民法典",
            "安全生产法",
            "依据《",
            "根据《",
        ),
    ),
)

_CODE_RE = re.compile(
    r"\b(?:GB|GBT|GB/T|JGJ|CJJ|CECS|ISO|IEC|YD|DL)\s*[/T]?\s*\d{2,}", re.IGNORECASE
)
_LAW_TITLE_RE = re.compile(r"《[^》]{2,40}》")


def classify_common_text(text: str) -> dict[str, Any]:
    """判断段落是否属于法规/国标/招标原文/合同固定条款/企业统一声明。

    返回 ``{"common": bool, "category": str, "reason": str}``；``category``
    为空字符串表示未命中。命中只代表「合理重复候选」，不改变查重结果。
    """

    value = (text or "").strip()
    if not value:
        return {"common": False, "category": "", "reason": ""}
    for key, label, markers in _COMMON_RULES:
        for marker in markers:
            if marker in value:
                return {
                    "common": True,
                    "category": key,
                    "reason": f"命中{label}表述「{marker}」",
                }
    code = _CODE_RE.search(value)
    if code:
        return {
            "common": True,
            "category": "standard_code",
            "reason": f"命中标准编号「{code.group(0)}」",
        }
    law = _LAW_TITLE_RE.search(value)
    if law:
        return {
            "common": True,
            "category": "law_regulation",
            "reason": f"引用法规/标准文件「{law.group(0)}」",
        }
    return {"common": False, "category": "", "reason": ""}


def _field(entry: Any, key: str, default: Any = "") -> Any:
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)


def _text_of(entry: Any) -> str:
    return str(_field(entry, "text", "") or _field(entry, "body", "") or "")


def _excerpt(text: str) -> str:
    value = _WS_RE.sub(" ", text or "").strip()
    if len(value) <= EXCERPT_CHARS:
        return value
    return value[:EXCERPT_CHARS] + "…"


def _source_label(entry: Any) -> str:
    project = str(_field(entry, "project_name", "") or "")
    heading = str(_field(entry, "heading", "") or "")
    if project and heading:
        return f"{project} / {heading}"[:500]
    return (project or heading or "未命名章节")[:500]


def _loc_label(entry: Any) -> str:
    heading = str(_field(entry, "heading", "") or "未命名章节")
    index = int(_field(entry, "index", 0) or 0) + 1
    return f"{heading} 第{index}段"[:120]


def iter_document_paragraphs(
    db,
    project_id: str,
    *,
    node_ids: list[str] | None = None,
    min_chars: int = DEFAULT_MIN_CHARS,
    project_name: str = "",
) -> list[dict[str, Any]]:
    """读取项目大纲与正文，按空行切段并过滤过短段落（只读）。

    返回条目字段：``project_id`` / ``project_name`` / ``node_id`` /
    ``heading`` / ``index`` / ``text`` / ``source`` / ``loc``。
    """

    nodes = db.scalars(
        select(OutlineNode)
        .where(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.position, OutlineNode.created_at)
    ).all()
    if node_ids:
        wanted = set(node_ids)
        nodes = [node for node in nodes if node.id in wanted]

    sections = db.scalars(
        select(Section).where(Section.node_id.in_([node.id for node in nodes]))
    ).all() if nodes else []
    body_by_node: dict[str, str] = {}
    for section in sections:
        # 同一节点理论上只有一条正文；出现多条时保留最后写入的那条。
        body_by_node[section.node_id] = section.body or ""

    out: list[dict[str, Any]] = []
    for node in nodes:
        body = body_by_node.get(node.id, "")
        for index, paragraph in enumerate(split_paragraphs(body)):
            if len(_WS_RE.sub("", paragraph)) < min_chars:
                continue
            entry = {
                "project_id": project_id,
                "project_name": project_name,
                "node_id": node.id,
                "heading": node.heading or "",
                "index": index,
                "text": paragraph,
            }
            entry["source"] = _source_label(entry)
            entry["loc"] = _loc_label(entry)
            out.append(entry)
    return out


def _pair_hit(
    left: dict[str, Any],
    right: dict[str, Any],
    score: float,
) -> dict[str, Any]:
    common_a = classify_common_text(_text_of(left))
    common_b = classify_common_text(_text_of(right))
    common = bool(common_a["common"] or common_b["common"])
    category = common_a["category"] or common_b["category"]
    reason = common_a["reason"] or common_b["reason"]
    return {
        "kind": "text",
        "source_a": _source_label(left),
        "source_b": _source_label(right),
        "loc_a": _loc_label(left),
        "loc_b": _loc_label(right),
        "excerpt_a": _excerpt(_text_of(left)),
        "excerpt_b": _excerpt(_text_of(right)),
        "similarity": round(min(1.0, max(0.0, score)), 4),
        "evidence": {
            "common": common,
            "common_category": category,
            "common_reason": reason,
            "reasonable_duplicate": common,
            "same_section": _field(left, "node_id") == _field(right, "node_id"),
            "project_a": _field(left, "project_id", "") or "",
            "project_b": _field(right, "project_id", "") or "",
            "algorithm": "dedup.similarity",
        },
    }


def compare_sections(
    paragraphs_a: list[dict[str, Any]],
    paragraphs_b: list[dict[str, Any]] | None = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_hits: int = DEFAULT_MAX_HITS,
    cross_only: bool = False,
) -> dict[str, Any]:
    """两两比较段落（纯函数，便于单测）。

    ``paragraphs_b`` 为空时在同一组内做 ``i < j`` 比较（项目内查重）；
    否则做跨项目比较。``cross_only=True`` 时跳过同一章节内部的段落对。
    长度比剪枝与 ``dedup.compare`` 相同：Jaccard 不可能超过阈值时直接跳过。
    """

    left_items = [item for item in paragraphs_a if len(_WS_RE.sub("", _text_of(item))) >= min_chars]
    right_items = (
        [item for item in paragraphs_b if len(_WS_RE.sub("", _text_of(item))) >= min_chars]
        if paragraphs_b is not None
        else left_items
    )
    same_set = paragraphs_b is None

    norm_left = [normalize(_text_of(item)) for item in left_items]
    norm_right = [normalize(_text_of(item)) for item in right_items]

    hits: list[dict[str, Any]] = []
    for i, left in enumerate(left_items):
        if not norm_left[i]:
            continue
        start = i + 1 if same_set else 0
        for j in range(start, len(right_items)):
            right = right_items[j]
            if same_set and i == j:
                continue
            if cross_only and _field(left, "node_id") == _field(right, "node_id"):
                continue
            if not norm_right[j]:
                continue
            shorter = min(len(norm_left[i]), len(norm_right[j]))
            longer = max(len(norm_left[i]), len(norm_right[j]))
            if longer and shorter / longer < threshold:
                continue
            score = similarity(_text_of(left), _text_of(right))
            if score + 1e-12 < threshold:
                continue
            hits.append(_pair_hit(left, right, score))

    hits.sort(key=lambda hit: (-hit["similarity"], hit["loc_a"], hit["loc_b"]))
    common_count = sum(1 for hit in hits if hit["evidence"]["common"])
    stats = {
        "mode": "text",
        "threshold": threshold,
        "paragraphs": len(left_items) + (0 if same_set else len(right_items)),
        "candidates": len(hits),
        "hits": min(len(hits), max_hits),
        "common_candidates": common_count,
        "truncated": len(hits) > max_hits,
        "cross_project": not same_set,
    }
    return {"hits": hits[:max_hits], "stats": stats}


def compare_project_documents(
    db,
    project_id: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    source_project_id: str | None = None,
    node_ids: list[str] | None = None,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_hits: int = DEFAULT_MAX_HITS,
    max_paragraphs: int = DEFAULT_MAX_PARAGRAPHS,
) -> dict[str, Any]:
    """读取项目正文并做段落查重；``source_project_id`` 提供时做跨项目比对。

    返回 ``{"hits": [...], "stats": {...}, "skipped": [...]}``。空文档不抛错，
    只返回 0 命中并在 ``stats`` 中说明。
    """

    from ..models import Project

    project = db.get(Project, project_id)
    project_name = project.name if project is not None else ""
    left = iter_document_paragraphs(
        db,
        project_id,
        node_ids=node_ids,
        min_chars=min_chars,
        project_name=project_name,
    )
    skipped: list[dict[str, Any]] = []
    right: list[dict[str, Any]] | None = None
    if source_project_id and source_project_id != project_id:
        source = db.get(Project, source_project_id)
        right = iter_document_paragraphs(
            db,
            source_project_id,
            min_chars=min_chars,
            project_name=source.name if source is not None else "",
        )
        if not right:
            skipped.append({"mode": "text", "path": source_project_id, "reason": "来源项目没有可比对正文"})
    if not left:
        skipped.append({"mode": "text", "path": project_id, "reason": "项目没有可比对正文"})

    truncated = len(left) > max_paragraphs or bool(right and len(right) > max_paragraphs)
    if len(left) > max_paragraphs:
        left = left[:max_paragraphs]
    if right and len(right) > max_paragraphs:
        right = right[:max_paragraphs]

    result = compare_sections(
        left,
        right,
        threshold=threshold,
        min_chars=min_chars,
        max_hits=max_hits,
    )
    result["stats"]["documents"] = len({item["node_id"] for item in left})
    result["stats"]["source_project_id"] = source_project_id or ""
    result["stats"]["truncated"] = bool(result["stats"].get("truncated") or truncated)
    result["skipped"] = skipped
    return result
