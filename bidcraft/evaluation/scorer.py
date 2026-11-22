"""六维质量评分：权重配置化，硬性风险一票否决。

刻意不把质量分称为中标概率或实际评标分数——这是内部质量参考分。
★ 未确认、未处置 blocking 等硬性风险不受综合分影响。
"""

from __future__ import annotations

from typing import Any

#: 六维权重（可配置：调用方传入覆盖）。总和 100。
DIMENSION_WEIGHTS: dict[str, float] = {
    "qualification": 20.0,   # 资格符合性
    "substantive": 25.0,     # 实质性响应
    "scoring": 20.0,         # 评分点覆盖
    "technical": 15.0,       # 技术内容质量
    "commercial": 10.0,      # 商务完整性
    "delivery": 10.0,        # 文档交付质量
}

DIMENSION_LABELS: dict[str, str] = {
    "qualification": "资格符合性",
    "substantive": "实质性响应",
    "scoring": "评分点覆盖",
    "technical": "技术内容质量",
    "commercial": "商务完整性",
    "delivery": "文档交付质量",
}


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def calculate_dimension_scores(
    coverage: dict[str, Any],
    risks: dict[str, Any],
    *,
    weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """计算六维得分（0-100）。缺失维度按 0 处理，不因证据不足给高分。"""

    w = dict(DIMENSION_WEIGHTS)
    if weights:
        for key, value in weights.items():
            if key in w:
                w[key] = float(value)

    requirement = coverage.get("requirement", {})
    scoring = coverage.get("scoring", {})
    evidence = coverage.get("evidence", {})
    chapter = coverage.get("chapter", {})
    by_level = risks.get("by_level", {})

    raw = {
        # 资格符合性：用核对结果里 blocking/critical 反向衡量
        "qualification": max(0.0, 100.0 - (by_level.get("blocking", 0) + by_level.get("critical", 0)) * 20.0),
        # 实质性响应：四态中 satisfied 占比
        "substantive": requirement.get("ratio", 0.0) * 100.0,
        # 评分点覆盖
        "scoring": scoring.get("covered_ratio", 0.0) * 100.0,
        # 技术内容质量：来源追溯无 stale + 章节确认
        "technical": (evidence.get("ratio", 0.0) * 60.0 + chapter.get("ratio", 0.0) * 40.0),
        # 商务完整性：暂无独立维度，用事实台账核对失败数反向衡量
        "commercial": max(0.0, 100.0 - by_level.get("major", 0) * 10.0),
        # 文档交付质量：章节确认率
        "delivery": chapter.get("ratio", 0.0) * 100.0,
    }

    dims: list[dict[str, Any]] = []
    for key, weight in w.items():
        score = round(max(0.0, min(100.0, raw.get(key, 0.0))), 2)
        dims.append(
            {
                "key": key,
                "label": DIMENSION_LABELS.get(key, key),
                "score": score,
                "weight": weight,
                "weighted": round(score * weight / 100.0, 2),
            }
        )
    return dims


def calculate_overall_quality(
    dimensions: list[dict[str, Any]],
    risk_summary: dict[str, Any],
) -> dict[str, Any]:
    """综合分 + 等级 + 结论。硬性风险强制 verdict=blocked（一票否决）。"""

    total_weight = sum(d["weight"] for d in dimensions) or 100.0
    overall = round(sum(d["weighted"] for d in dimensions) / total_weight * 100.0, 2)
    grade = _grade(overall)

    if risk_summary.get("has_hard_risk"):
        verdict = "blocked"
    elif overall >= 75 and not risk_summary.get("by_level", {}).get("major"):
        verdict = "pass"
    else:
        verdict = "warn"

    return {
        "overall_score": overall,
        "grade": grade,
        "verdict": verdict,
        "hard_risk_veto": bool(risk_summary.get("has_hard_risk")),
    }


def evaluate_project_quality(
    coverage: dict[str, Any],
    risks: dict[str, Any],
    *,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """一步完成六维 + 综合判定。"""

    dims = calculate_dimension_scores(coverage, risks, weights=weights)
    summary = calculate_overall_quality(dims, risks.get("risk_summary", {}))
    return {"dimensions": dims, **summary}
