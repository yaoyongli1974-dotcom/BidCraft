"""六维质量评分：权重配置化，硬性风险一票否决。

刻意不把质量分称为中标概率或实际评标分数——这是内部质量参考分。
★ 未确认、未处置 blocking、实质性响应证据未确认等硬性风险不受综合分影响；
章节未确认 / 过期引用等导出门禁拦截至少把结论压到 warn，综合分再高也不 PASS。
已确认 ChapterPlan 的规划覆盖在 technical/delivery 维度内部参与计算，六维权重不变；
零分母（未测量）覆盖率按 0 处理，不凭空给满分。
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


def _ratio(value: Any) -> float:
    """覆盖率可能为 None（零分母未测量）；按无证据处理，不给高分。"""
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def calculate_dimension_scores(
    coverage: dict[str, Any],
    risks: dict[str, Any],
    *,
    weights: dict[str, float] | None = None,
    commercial_info: dict[str, Any] | None = None,
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
    plan = coverage.get("plan", {})
    by_level = risks.get("by_level", {})

    evidence_ratio = _ratio(evidence.get("ratio"))
    chapter_ratio = _ratio(chapter.get("ratio"))
    plan_ratio = _ratio(plan.get("ratio"))
    # 六维权重不变：规划覆盖只在维度内部替换/分摊已有信号。没有可测的规划
    # （零分母）时沿用旧口径，避免历史项目因缺数据被误判为低质量。
    if plan.get("measured"):
        technical = evidence_ratio * 45.0 + chapter_ratio * 35.0 + plan_ratio * 20.0
        delivery = chapter_ratio * 60.0 + plan_ratio * 40.0
    else:
        technical = evidence_ratio * 60.0 + chapter_ratio * 40.0
        delivery = chapter_ratio * 100.0

    raw = {
        # 资格符合性：用核对结果里 blocking/critical 反向衡量
        "qualification": max(0.0, 100.0 - (by_level.get("blocking", 0) + by_level.get("critical", 0)) * 20.0),
        # 实质性响应：四态中 satisfied 占比
        "substantive": _ratio(requirement.get("ratio")) * 100.0,
        # 评分点覆盖
        "scoring": _ratio(scoring.get("covered_ratio")) * 100.0,
        # 技术内容质量：来源追溯 + 章节确认 + 已确认规划
        "technical": technical,
        # 商务完整性：有商务标文档时按真实必填项完成率，否则退回 major 数代理
        "commercial": (
            float(commercial_info.get("ratio", 0.0)) * 100.0
            if commercial_info
            else max(0.0, 100.0 - by_level.get("major", 0) * 10.0)
        ),
        # 文档交付质量：章节确认 + 已确认规划
        "delivery": delivery,
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
    elif risk_summary.get("export_blocked"):
        # 章节未确认 / 正文过期引用会拦住真实导出，综合分再高也只能给 warn。
        verdict = "warn"
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
    commercial_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """一步完成六维 + 综合判定。"""

    dims = calculate_dimension_scores(
        coverage, risks, weights=weights, commercial_info=commercial_info
    )
    summary = calculate_overall_quality(dims, risks.get("risk_summary", {}))
    return {"dimensions": dims, **summary}
