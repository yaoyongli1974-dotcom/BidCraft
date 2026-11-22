"""大纲结构查重：标题相似、章节顺序、整体大纲结构。

复用 :mod:`bidcraft.knowledge_audit` 的 ``normalize_heading`` / ``similarity``。
标题相似度的「包含即 1.0」语义适合短标题，不适合长段落——因此结构维度
单独使用它，文本维度继续使用 ``dedup.similarity``。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..knowledge_audit import similarity as heading_similarity
from ..models import OutlineNode

DEFAULT_THRESHOLD = 0.7
DEFAULT_MAX_HITS = 500


def _get(node: Any, key: str, default: Any = None) -> Any:
    if isinstance(node, dict):
        return node.get(key, default)
    return getattr(node, key, default)


def extract_document_structure(nodes: list[Any]) -> list[dict[str, Any]]:
    """把大纲节点整理成按阅读顺序排列的结构列表（只读）。

    每项包含 ``node_id`` / ``heading`` / ``parent_id`` / ``position`` /
    ``level`` / ``path`` / ``order``。按 ``parent_id`` 建树，再按 ``position``
    深度优先展开；父节点缺失或存在环时按根节点处理，保证不丢节点。
    """

    records: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(_get(node, "id", "") or "")
        parent_id = str(_get(node, "parent_id", "") or "")
        try:
            position = int(_get(node, "position", 0) or 0)
        except (TypeError, ValueError):
            position = 0
        records.append(
            {
                "node_id": node_id,
                "heading": str(_get(node, "heading", "") or ""),
                "parent_id": parent_id,
                "position": position,
                "level": 0,
                "path": "",
                "order": 0,
            }
        )

    by_id = {record["node_id"]: record for record in records if record["node_id"]}
    children: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for record in records:
        parent = by_id.get(record["parent_id"]) if record["parent_id"] else None
        if parent is None or parent is record:
            roots.append(record)
        else:
            children.setdefault(record["parent_id"], []).append(record)

    def _sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
        return (item["position"], item["heading"], item["node_id"])

    for bucket in children.values():
        bucket.sort(key=_sort_key)
    roots.sort(key=_sort_key)

    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(record: dict[str, Any], level: int, prefix: str) -> None:
        key = record["node_id"] or f"__anon_{len(ordered)}"
        if key in seen:
            return
        seen.add(key)
        heading = record["heading"]
        path = f"{prefix} / {heading}" if prefix else heading
        record["level"] = level
        record["path"] = path
        record["order"] = len(ordered)
        ordered.append(record)
        for child in children.get(record["node_id"], []):
            visit(child, level + 1, path)

    for root in roots:
        visit(root, 0, "")
    # 环等异常情况下仍把未访问节点补在末尾。
    for record in sorted(records, key=_sort_key):
        visit(record, 0, "")
    return ordered


def _same_structure(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    if left is right:
        return True
    left_ids = {item["node_id"] for item in left}
    right_ids = {item["node_id"] for item in right}
    return bool(left_ids) and left_ids == right_ids


def compare_heading_structure(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    duplicates: bool = False,
) -> dict[str, Any]:
    """标题相似配对。

    跨项目时做唯一贪心匹配（每个标题只配一次）。同一项目内部传入
    ``duplicates=True`` 时输出所有 ``i < j`` 的相似标题对，用于发现重复章节名；
    默认 ``duplicates=False`` 时同组比较按位置自匹配（整体结构视为一致）。
    """

    if _same_structure(left, right):
        if not duplicates:
            pairs = [
                {"a": item, "b": item, "similarity": 1.0}
                for item in left
                if item["heading"]
            ]
            return {
                "pairs": pairs,
                "matched": len(pairs),
                "coverage": 1.0 if left else 0.0,
                "same_set": True,
            }
        pairs: list[dict[str, Any]] = []
        for i in range(len(left)):
            for j in range(i + 1, len(left)):
                if left[i]["node_id"] == left[j]["node_id"]:
                    continue
                score = heading_similarity(left[i]["heading"], left[j]["heading"])
                if score + 1e-12 < threshold:
                    continue
                pairs.append({"a": left[i], "b": left[j], "similarity": round(score, 4)})
        pairs.sort(key=lambda item: (-item["similarity"], item["a"]["order"], item["b"]["order"]))
        return {
            "pairs": pairs,
            "matched": len(pairs),
            "coverage": round(len(pairs) / max(1, len(left)), 3),
            "same_set": True,
        }

    candidates: list[tuple[float, int, int]] = []
    for i, item_a in enumerate(left):
        for j, item_b in enumerate(right):
            score = heading_similarity(item_a["heading"], item_b["heading"])
            if score + 1e-12 >= threshold:
                candidates.append((score, i, j))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    used_left: set[int] = set()
    used_right: set[int] = set()
    pairs = []
    for score, i, j in candidates:
        if i in used_left or j in used_right:
            continue
        used_left.add(i)
        used_right.add(j)
        pairs.append({"a": left[i], "b": right[j], "similarity": round(score, 4)})
    pairs.sort(key=lambda item: item["a"]["order"])
    return {
        "pairs": pairs,
        "matched": len(pairs),
        "coverage": round(len(pairs) / max(1, max(len(left), len(right))), 3),
        "same_set": False,
    }


def _lis_length(values: list[int]) -> int:
    """最长上升子序列长度（O(n log n)）。"""

    tails: list[int] = []
    for value in values:
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if tails[mid] < value:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(tails):
            tails.append(value)
        else:
            tails[lo] = value
    return len(tails)


def compare_section_order(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """章节顺序相似度：标题匹配后看匹配项在两侧的先后关系是否一致。

    ``order_score`` = 最长上升子序列长度 / 匹配数，1.0 表示顺序完全一致。
    """

    heading = compare_heading_structure(left, right, threshold=threshold)
    pairs = heading["pairs"]
    if heading["same_set"] or len(pairs) < 2:
        return {
            "matches": pairs,
            "matched": len(pairs),
            "order_score": 1.0 if pairs else 0.0,
            "inversions": 0,
            "total_pairs": 0,
            "same_set": heading["same_set"],
        }
    sequence = [item["b"]["order"] for item in sorted(pairs, key=lambda p: p["a"]["order"])]
    lis = _lis_length(sequence)
    total = len(sequence) * (len(sequence) - 1) // 2
    inversions = 0
    for i in range(len(sequence)):
        for j in range(i + 1, len(sequence)):
            if sequence[i] > sequence[j]:
                inversions += 1
    return {
        "matches": pairs,
        "matched": len(pairs),
        "order_score": round(lis / len(sequence), 4),
        "inversions": inversions,
        "total_pairs": total,
        "same_set": False,
    }


def compare_outline_similarity(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """综合标题覆盖与章节顺序，给出整体大纲相似度（0~1）。"""

    heading = compare_heading_structure(left, right, threshold=threshold)
    order = compare_section_order(left, right, threshold=threshold)
    coverage = heading["coverage"]
    if heading["matched"] >= 2:
        order_score = order["order_score"]
    elif heading["matched"] == 1:
        order_score = 1.0
    else:
        order_score = 0.0
    score = round(0.7 * coverage + 0.3 * order_score, 4)
    return {
        "heading": heading,
        "order": order,
        "matched": heading["matched"],
        "coverage": coverage,
        "order_score": order_score,
        "score": score,
        "same_set": heading["same_set"],
    }


def _hit(
    left: dict[str, Any],
    right: dict[str, Any],
    score: float,
    *,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "structure",
        "source_a": left["path"][:500],
        "source_b": right["path"][:500],
        "loc_a": f"大纲第{left['order'] + 1}项 {left['path']}"[:120],
        "loc_b": f"大纲第{right['order'] + 1}项 {right['path']}"[:120],
        "excerpt_a": (left["heading"] or "")[:120],
        "excerpt_b": (right["heading"] or "")[:120],
        "similarity": round(min(1.0, max(0.0, score)), 4),
        "evidence": evidence,
    }


def compare_project_structure(
    db,
    project_id: str,
    *,
    source_project_id: str | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """读取项目大纲并做结构查重（只读）。

    指定 ``source_project_id`` 时比较两个项目的大纲；否则在同一项目内部
    检测重复/高度相似的章节标题。
    """

    from ..models import Project

    project = db.get(Project, project_id)
    nodes = db.scalars(
        select(OutlineNode)
        .where(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.position, OutlineNode.created_at)
    ).all()
    left = extract_document_structure(list(nodes))
    skipped: list[dict[str, Any]] = []
    if not left:
        skipped.append({"mode": "structure", "path": project_id, "reason": "项目没有大纲节点"})

    project_name = project.name if project is not None else project_id
    if source_project_id and source_project_id != project_id:
        source = db.get(Project, source_project_id)
        source_nodes = db.scalars(
            select(OutlineNode)
            .where(OutlineNode.project_id == source_project_id)
            .order_by(OutlineNode.position, OutlineNode.created_at)
        ).all()
        right = extract_document_structure(list(source_nodes))
        if not right:
            skipped.append(
                {"mode": "structure", "path": source_project_id, "reason": "来源项目没有大纲节点"}
            )
        outline = compare_outline_similarity(left, right, threshold=threshold)
        source_name = source.name if source is not None else source_project_id
        hits = []
        for pair in outline["heading"]["pairs"]:
            hits.append(
                _hit(
                    pair["a"],
                    pair["b"],
                    pair["similarity"],
                    evidence={
                        "cross_project": True,
                        "project_a": project_name,
                        "project_b": source_name,
                        "outline_score": outline["score"],
                        "order_score": outline["order_score"],
                        "algorithm": "knowledge_audit.similarity",
                    },
                )
            )
        stats = {
            "mode": "structure",
            "threshold": threshold,
            "nodes": len(left) + len(right),
            "matched": outline["matched"],
            "coverage": outline["coverage"],
            "order_score": outline["order_score"],
            "outline_score": outline["score"],
            "hits": len(hits),
            "cross_project": True,
        }
        return {"hits": hits[:DEFAULT_MAX_HITS], "stats": stats, "skipped": skipped}

    heading = compare_heading_structure(
        left, left, threshold=threshold, duplicates=True
    )
    hits = []
    for pair in heading["pairs"]:
        hits.append(
            _hit(
                pair["a"],
                pair["b"],
                pair["similarity"],
                evidence={
                    "cross_project": False,
                    "duplicate_heading": True,
                    "project_a": project_name,
                    "project_b": project_name,
                    "algorithm": "knowledge_audit.similarity",
                },
            )
        )
    stats = {
        "mode": "structure",
        "threshold": threshold,
        "nodes": len(left),
        "matched": heading["matched"],
        "coverage": heading["coverage"],
        "duplicate_headings": len(hits),
        "hits": len(hits),
        "cross_project": False,
    }
    return {"hits": hits[:DEFAULT_MAX_HITS], "stats": stats, "skipped": skipped}
