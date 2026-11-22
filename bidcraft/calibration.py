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

from .verdict import SATISFIED, judge

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
        )


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


def load_cases(path: Path | str | None = None) -> list[VerdictCase]:
    """从 JSON 加载判定回归样本。"""

    target = Path(path) if path else FIXTURE_PATH
    data = json.loads(target.read_text(encoding="utf-8"))
    return [VerdictCase.from_dict(item) for item in data.get("cases", [])]


def run_case(case: VerdictCase, deadline=None) -> CaseResult:
    """对单条样本运行 judge()，返回预测/标准/是否命中。"""

    requirement = _RequirementStub(case.requirement, case.marker)
    nodes = [{"heading": "投标响应", "body": case.body, "position": 1}]
    verdict = judge(requirement, nodes, deadline=deadline)
    predicted = verdict.state
    hit = predicted == case.expected
    false_acceptance = case.expected != SATISFIED and predicted == SATISFIED
    star_fa = false_acceptance and STRICT_STAR in case.marker
    return CaseResult(
        id=case.id,
        expected=case.expected,
        predicted=predicted,
        hit=hit,
        marker=case.marker,
        reason=verdict.reason,
        false_acceptance=false_acceptance,
        star_false_acceptance=star_fa,
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
    """跑完整回归集，产出逐条明细 + 混淆矩阵 + FAR。"""

    results = [run_case(case, deadline=deadline) for case in cases]

    # 二值化：satisfied = 正类（预测放行）；其余 = 负类。
    tp = sum(1 for r in results if r.expected == SATISFIED and r.predicted == SATISFIED)
    fn = sum(1 for r in results if r.expected == SATISFIED and r.predicted != SATISFIED)
    fp = sum(1 for r in results if r.expected != SATISFIED and r.predicted == SATISFIED)
    tn = sum(1 for r in results if r.expected != SATISFIED and r.predicted != SATISFIED)

    negatives = fp + tn  # 实际负类数（标准判定为非满足）
    far = fp / negatives if negatives else 0.0
    star_fa = [r.id for r in results if r.star_false_acceptance]

    return {
        "cases": [asdict(r) for r in results],
        "confusion_matrix": {"TP": tp, "FP": fp, "TN": tn, "FN": fn},
        "false_acceptance_rate": round(far, 4),
        "star_false_acceptances": star_fa,
        "total": len(results),
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
