"""判定质量回归与假接受率（FAR）度量（v0.22.0 / R14）。

与 ``calibrate.py``（解析字段基线）不同，本模块度量的是**实质性条款判定**质量：
对每条标注样本（招标要求 + 投标响应 + 标准判定 + ★标记 + 关键证据位置）运行
:func:`bidcraft.verdict.judge`，比较「预测四态」与「标准四态」，产出：

- 混淆矩阵（TP / FP / TN / FN，按「是否满足」二值化：satisfied = 正类）；
- **False Acceptance Rate（FAR）**：不满足却被判满足的比例——这是投标场景最
  危险的误放行，单独统计并设严格阈值；
- 每条样本的预测/标准/是否命中，供 CI 与人工审阅。

刻意不做：不修改样本标准答案来「提升通过率」；标准样本变化需人工审核（见
``tests/fixtures/verdict_regression.json`` 的顶部说明）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .verdict import (
    PARTIAL,
    REVIEW,
    SATISFIED,
    UNSATISFIED,
    judge,
    parse_deadline,
)

#: 四态判定值（用于四态准确率，review 与 unsatisfied 分开计）。
FOUR_STATES = (SATISFIED, UNSATISFIED, PARTIAL, REVIEW)

#: 默认判定回归集（随包分发）。
FIXTURE_PATH = Path(__file__).parent.parent / "tests" / "fixtures" / "verdict_regression.json"

#: ★ 条款的已知不满足样本若被自动判满足，FAR 必须为零（严格验收）。
STRICT_STAR = "★"


@dataclass(frozen=True)
class VerdictCase:
    """一条判定回归样本。"""

    id: str
    requirement: str
    body: str
    expected: str          # satisfied / unsatisfied / partial / review
    marker: str = ""       # ★ / ▲ / 空
    note: str = ""         # 人工审核说明
    evidence_hint: str = ""  # 关键证据位置（条款号/页码/原文片段）
    # R20：样本自带招标截止日期（支持逐样本 time-window 场景），None 表示样本不涉及时间窗。
    deadline: str = ""
    # R20：样本分类（time_window / project_type / compound / unit ...）与预期证据特征。
    category: str = ""
    expected_evidence: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> VerdictCase:
        return cls(
            id=str(raw["id"]),
            requirement=str(raw["requirement"]),
            body=str(raw.get("body", "")),
            expected=str(raw["expected"]),
            marker=str(raw.get("marker", "") or ""),
            note=str(raw.get("note", "") or ""),
            evidence_hint=str(raw.get("evidence_hint", "") or ""),
            deadline=str(raw.get("deadline", "") or ""),
            category=str(raw.get("category", "") or ""),
            expected_evidence=raw.get("expected_evidence"),
        )

    @property
    def parsed_deadline(self):
        """把样本里的截止日期字符串解析为 ``date``；无法解析返回 None（转人工，不伪造）。"""

        return parse_deadline(self.deadline) if self.deadline else None


@dataclass(frozen=True)
class CaseResult:
    id: str
    expected: str
    predicted: str
    hit: bool
    marker: str
    reason: str
    false_acceptance: bool
    star_false_acceptance: bool
    # R20：分类、逐样本日期、证据关联诊断。
    category: str = ""
    deadline: str = ""
    type_conflict: bool = False
    identity_conflict: bool = False
    evidence_missing: bool = False
    evidence_association_error: bool = False


def load_cases(path: Path | str | None = None) -> list[VerdictCase]:
    """从 JSON 加载判定回归样本。"""

    target = Path(path) if path else FIXTURE_PATH
    data = json.loads(target.read_text(encoding="utf-8"))
    return [VerdictCase.from_dict(item) for item in data.get("cases", [])]


def _evidence_diagnostics(verdict) -> dict[str, Any]:
    """从五层证据里抽取证据关联诊断（供 R20 指标统计）。

    不伪造来源：文本输入通常没有真实页码/文件 ID，``source_reference_status``
    会是 ``missing``；这里只做如实统计，不当成判定错误。
    """

    diag = {
        "type_conflict": False,
        "identity_conflict": False,
        "evidence_missing": False,
    }
    try:
        window = verdict.evidence["layer4"]["deadline_window"]
    except (KeyError, TypeError):
        window = {}
    if isinstance(window, dict):
        diag["type_conflict"] = bool(window.get("type_conflict"))
        diag["identity_conflict"] = bool(window.get("identity_conflict"))
        records = window.get("records") or []
        diag["evidence_missing"] = any(
            isinstance(rec, dict) and rec.get("source_reference_status") == "missing"
            for rec in records
        )
    return diag


def run_case(case: VerdictCase, deadline=None) -> CaseResult:
    """对单条样本运行 judge()，返回预测/标准/是否命中。

    ``deadline`` 显式参数优先；否则用样本自带的 ``case.parsed_deadline``——
    这样时间窗样本由统一执行器驱动，不依赖人工临时传参（R20）。
    """

    requirement = _RequirementStub(case.requirement, case.marker)
    nodes = [{"heading": "投标响应", "body": case.body, "position": 1}]
    effective_deadline = deadline if deadline is not None else case.parsed_deadline
    verdict = judge(requirement, nodes, deadline=effective_deadline)
    predicted = verdict.state
    hit = predicted == case.expected
    false_acceptance = case.expected != SATISFIED and predicted == SATISFIED
    star_fa = false_acceptance and STRICT_STAR in case.marker
    diag = _evidence_diagnostics(verdict)

    # 证据关联错误：样本声明了 expected_evidence 特征但实际不符。
    evidence_association_error = False
    if case.expected_evidence:
        expected = case.expected_evidence
        if "type_conflict" in expected and bool(expected["type_conflict"]) != diag["type_conflict"]:
            evidence_association_error = True
        if (
            "identity_conflict" in expected
            and bool(expected["identity_conflict"]) != diag["identity_conflict"]
        ):
            evidence_association_error = True
        if any(k in expected for k in ("min_valid", "boundary_start_ge", "boundary_end_ge")):
            window = {}
            try:
                window = verdict.evidence["layer4"]["deadline_window"]
            except (KeyError, TypeError):
                pass
            if "min_valid" in expected and int(window.get("valid", 0)) < int(expected["min_valid"]):
                evidence_association_error = True
            if (
                "boundary_start_ge" in expected
                and int(window.get("boundary_start", 0)) < int(expected["boundary_start_ge"])
            ):
                evidence_association_error = True
            if (
                "boundary_end_ge" in expected
                and int(window.get("boundary_end", 0)) < int(expected["boundary_end_ge"])
            ):
                evidence_association_error = True

    return CaseResult(
        id=case.id,
        expected=case.expected,
        predicted=predicted,
        hit=hit,
        marker=case.marker,
        reason=verdict.reason,
        false_acceptance=false_acceptance,
        star_false_acceptance=star_fa,
        category=case.category,
        deadline=case.deadline,
        type_conflict=diag["type_conflict"],
        identity_conflict=diag["identity_conflict"],
        evidence_missing=diag["evidence_missing"],
        evidence_association_error=evidence_association_error,
    )


class _RequirementStub:
    """judge() 只需要 text / marker / source_quote， avoids DB dependency。"""

    def __init__(self, text: str, marker: str) -> None:
        self.text = text
        self.marker = marker
        self.source_quote = text
        self.page = 0
        self.clause_no = ""


def run_regression(cases: list[VerdictCase], deadline=None) -> dict[str, Any]:
    """跑完整回归集，产出逐条明细 + 混淆矩阵 + 多维质量指标。"""

    results = [run_case(case, deadline=deadline) for case in cases]
    total = len(results)

    # 二值化：satisfied = 正类（预测放行）；其余 = 负类。
    tp = sum(1 for r in results if r.expected == SATISFIED and r.predicted == SATISFIED)
    fn = sum(1 for r in results if r.expected == SATISFIED and r.predicted != SATISFIED)
    fp = sum(1 for r in results if r.expected != SATISFIED and r.predicted == SATISFIED)
    tn = sum(1 for r in results if r.expected != SATISFIED and r.predicted != SATISFIED)

    # R20：明确各指标分母口径，避免把不同统计量混为一谈。
    negatives = fp + tn  # 实际负类（标准判定非满足）：FAR 的分母
    positives = tp + fn  # 实际正类（标准判定满足）：FRR 的分母
    far = fp / negatives if negatives else 0.0  # 误放行率 FP/(FP+TN)
    frr = fn / positives if positives else 0.0  # 错误拒绝率 FN/(FN+TP)

    # 四态准确率：review 与 unsatisfied 分开计（不混为一类）。
    four_state_hits = sum(1 for r in results if r.predicted in FOUR_STATES and r.hit)
    four_state_accuracy = four_state_hits / total if total else 0.0
    review_rate = sum(1 for r in results if r.predicted == REVIEW) / total if total else 0.0

    # 证据关联诊断：项目类型/身份冲突、证据来源缺失、预期证据不符。
    type_errors = sum(1 for r in results if r.type_conflict)
    identity_errors = sum(1 for r in results if r.identity_conflict)
    association_errors = sum(1 for r in results if r.evidence_association_error)
    evidence_missing = sum(1 for r in results if r.evidence_missing)

    star_fa = [r.id for r in results if r.star_false_acceptance]

    return {
        "cases": [asdict(r) for r in results],
        "confusion_matrix": {"TP": tp, "FP": fp, "TN": tn, "FN": fn},
        "false_acceptance_rate": round(far, 4),  # FP/(FP+TN)，分母=实际负类
        "false_rejection_rate": round(frr, 4),   # FN/(FN+TP)，分母=实际正类
        "four_state_accuracy": round(four_state_accuracy, 4),
        "review_rate": round(review_rate, 4),
        "evidence_association_errors": association_errors,
        "project_type_association_errors": type_errors,
        "identity_association_errors": identity_errors,
        "evidence_missing_count": evidence_missing,
        "star_false_acceptances": star_fa,
        "total": total,
        "hits": sum(1 for r in results if r.hit),
    }


def assert_regression(cases: list[VerdictCase], deadline=None) -> dict[str, Any]:
    """验证回归集：所有样本命中，且 ★ 条款 FAR 为零。

    严重误放行（★ 不满足却判满足）直接抛 AssertionError，供 CI 拦截。
    """

    report = run_regression(cases, deadline=deadline)
    misses = [r["id"] for r in report["cases"] if not r["hit"]]
    assert not misses, f"判定回归未命中样本: {misses}"
    assert not report["star_false_acceptances"], (
        f"★ 条款严重误放行: {report['star_false_acceptances']}"
    )
    return report
