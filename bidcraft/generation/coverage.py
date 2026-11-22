"""评分点覆盖矩阵（只读）。

把「评分办法清单」（:class:`~bidcraft.models.ScoringItem`）与当前大纲章节
（:func:`~bidcraft.generation.outline.load_outline` 的返回结构）对照，回答一个
投标场景下很实际的问题：**每个评分点到底有没有对应章节、那一章有没有正文**。

刻意保持只读：本模块不写数据库、不改大纲、不碰任务队列/并发/fencing。
它只做计算，供 API 与未来的规划页展示，判定口径保守——匹配不到或正文为空
一律算「未覆盖」，不做语义脑补。

匹配策略（从强到弱，命中即停）：
1. ``section_hint`` 与章节标题完全相同（去大小写/空白）；
2. ``category`` 出现在章节标题或 intent 中；
3. 评分项名称与章节标题的字符二元组相似度（复用 ``outline.similarity``），
   取最高分且达到阈值者。

一个评分点可对应多个章节（全部列出）；一个章节也可响应多个评分点。
"""

from __future__ import annotations

from typing import Any

from .outline import similarity

#: 标题相似度命中阈值（与 _scoring_outline 的 0.25 口径一致，便于双向对称）。
MATCH_THRESHOLD = 0.25
#: 章节正文低于该字数视为「覆盖薄弱」（有响应但明显不足）。
WEAK_WORDS = 120

STATUS_COVERED = "covered"
STATUS_WEAK = "weak"
STATUS_UNCOVERED = "uncovered"
STATUS_LABELS = {
    STATUS_COVERED: "已覆盖",
    STATUS_WEAK: "覆盖薄弱",
    STATUS_UNCOVERED: "未覆盖",
}


def _norm(text: Any) -> str:
    return "".join(str(text or "").split()).lower()


def _match_reasons(item: dict[str, Any], node: dict[str, Any]) -> list[str]:
    """返回评分点与章节的匹配依据（可能多条，便于人工核验）。"""

    reasons: list[str] = []
    heading = _norm(node.get("heading"))
    intent = _norm(node.get("intent"))
    hint = _norm(item.get("section_hint"))
    category = _norm(item.get("category"))
    if not heading:
        return reasons
    if hint and hint == heading:
        reasons.append("section_hint=标题")
    if category and (category in heading or category in intent):
        reasons.append("category 命中")
    score = similarity(str(item.get("name") or ""), str(node.get("heading") or ""))
    if score >= MATCH_THRESHOLD:
        reasons.append(f"标题相似 {score:.2f}")
    return reasons


def _status_for(nodes: list[dict[str, Any]]) -> str:
    """根据匹配章节的正文情况给出覆盖状态。"""

    if not nodes:
        return STATUS_UNCOVERED
    best = 0
    for node in nodes:
        words = int(node.get("words") or 0)
        best = max(best, words)
    if best <= 0:
        return STATUS_UNCOVERED
    if best < WEAK_WORDS:
        return STATUS_WEAK
    return STATUS_COVERED


def calculate_score_coverage(
    scores: list[dict[str, Any]], nodes: list[dict[str, Any]]
) -> dict[str, Any]:
    """计算评分覆盖矩阵。

    ``scores`` 为 :func:`bidcraft.scoring.score_payload` 的输出列表；``nodes``
    为 :func:`bidcraft.generation.outline.load_outline` 的输出列表。
    """

    rows: list[dict[str, Any]] = []
    covered_score = 0.0
    weak_score = 0.0
    total_score = 0.0
    for item in scores:
        try:
            score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        total_score += max(score, 0.0)
        matched: list[dict[str, Any]] = []
        for node in nodes:
            reasons = _match_reasons(item, node)
            if not reasons:
                continue
            matched.append(
                {
                    "node_id": node.get("id"),
                    "heading": node.get("heading") or "",
                    "words": int(node.get("words") or 0),
                    "has_body": bool(node.get("has_body")),
                    "review_status": node.get("review_status") or "draft",
                    "reasons": reasons,
                }
            )
        status = _status_for(matched)
        if status == STATUS_COVERED:
            covered_score += max(score, 0.0)
        elif status == STATUS_WEAK:
            weak_score += max(score, 0.0)
        rows.append(
            {
                "id": item.get("id"),
                "name": item.get("name") or "",
                "score": score,
                "category": item.get("category") or "",
                "section_hint": item.get("section_hint") or "",
                "status": status,
                "status_label": STATUS_LABELS[status],
                "nodes": matched,
            }
        )

    covered_ratio = round(covered_score / total_score, 4) if total_score > 0 else 0.0
    uncovered = [r for r in rows if r["status"] == STATUS_UNCOVERED]
    return {
        "total_items": len(rows),
        "total_score": round(total_score, 4),
        "covered_score": round(covered_score, 4),
        "weak_score": round(weak_score, 4),
        "covered_ratio": covered_ratio,
        "uncovered_count": len(uncovered),
        "rows": rows,
    }


def validate_scoring_coverage(report: dict[str, Any]) -> list[str]:
    """给出覆盖自检提示（只读、不抛异常）。"""

    issues: list[str] = []
    for row in report.get("rows", []):
        if row.get("status") == STATUS_UNCOVERED:
            hint = row.get("section_hint") or row.get("name") or row.get("id")
            issues.append(f"评分项「{hint}」未找到带正文的对应章节")
    if report.get("total_score", 0) <= 0:
        issues.append("尚无带分值的评分项，无法计算覆盖")
    return issues
