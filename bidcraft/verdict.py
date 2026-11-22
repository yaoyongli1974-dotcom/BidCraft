"""实质性条款五层判定引擎（v0.19.0）。

分层（每一层的输出都可单独举证，不依赖 LLM）：

1. **条款提取**：原文 / source_quote / 页码 / 条款编号 / ★▲ 标记 —— 由
   ``parsing.extract`` 与 ``Requirement`` 落库负责，本层读取其字段。
2. **原子约束拆分**：一条复合条款按子句拆成独立可验证的原子要求。
3. **结构化约束提取**：对象、数值、单位、等级、时间窗、数量、比较关系、
   否定条件 —— 复用 ``constraint_check`` 的判定，并把时间窗作为**证据维度**
   提取（见下方「时间窗口径」）。
4. **多维响应验证**：语义（3-gram + 锚点，**可插拔 SemanticMatcher**）、
   数值 / 等级 / 否定 / 主体关联（``constraint_check``）、时间范围（证据）。
5. **判定与证据留痕**：四态 —— 满足 / 不满足 / 部分满足 / 人工复核，
   连同每层证据写入 ``RequirementVerdict.evidence``。

时间窗口径（守住既有正确判定）
------------------------------
``tests/test_constraint_check.py::test_near_years_window_is_anchor_not_comparison``
要求「近 N 年」不升级成 <= N 的硬比较——「近五年 6 项」覆盖「近三年不少于 3 项」
是既有正确口径。因此时间窗只作为**证据维度**：正文时间窗短于要求时记一条
review 信号（转人工复核），绝不判为 violated，也不改变 ``constraint_blocked``。

AI 语义接口（预留，不接 LLM）
-----------------------------
:class:`SemanticMatcher` 是语义覆盖率的协议。默认 :class:`RuleSemanticMatcher`
用确定性的 3-gram + 锚点实现。将来可注入基于模型的 matcher 替代语义层，
但数值 / 等级 / 时间 / 否定的判定始终由 ``constraint_check`` 的确定性规则负责，
不受 matcher 影响——「语义相似不代表实质性要求满足」由结构保证，不靠提示词。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from .constraint_check import (
    _CLAUSE_SPLIT,
    _NUM,
    ConstraintVerdict,
    _cn_number,
    evaluate_constraints,
)
from .substantive import (
    REQ_TYPE_GENERAL,
    anchor_coverage,
    classify_requirement,
    coverage_ratio,
    find_covering_section,
    requirement_core,
)

# --------------------------------------------------------------- 四态常量
SATISFIED = "satisfied"
UNSATISFIED = "unsatisfied"
PARTIAL = "partial"
REVIEW = "review"

VERDICT_STATES = (SATISFIED, UNSATISFIED, PARTIAL, REVIEW)
VERDICT_LABELS = {
    SATISFIED: "满足",
    UNSATISFIED: "不满足",
    PARTIAL: "部分满足",
    REVIEW: "人工复核",
}

# 时间窗证据：「近 / 最近 / 过去 N 年（月）」。
_TIME_WINDOW_RE = re.compile(rf"(?:近|最近|过去)\s*({_NUM})\s*(年|月)")
# 原子拆分时去掉句首的 ★/▲、编号与噪声标点。
_LEAD_RE = re.compile(r"^[\s★▲\d、.．)）(（\-*•●◆■:：]+")


# --------------------------------------------------------------- L4 语义插槽
class SemanticMatcher(Protocol):
    """语义覆盖率协议——只负责「这段话有没有正面回答这条要求」。

    数值 / 等级 / 时间 / 否定不由本接口决定；实现必须返回 0–1 的覆盖率。
    """

    def coverage(self, text: str, body: str, req_type: str = REQ_TYPE_GENERAL) -> float: ...


class RuleSemanticMatcher:
    """默认规则实现：字符 3-gram + 结构化锚点，确定性、可复现。"""

    def coverage(self, text: str, body: str, req_type: str = REQ_TYPE_GENERAL) -> float:
        return coverage_ratio(text, body, req_type, check_constraints=False)


# --------------------------------------------------------------- 三/四层证据
@dataclass(frozen=True)
class Structure:
    """L3 一条结构化约束的可举证视图。"""

    subject: str      # 对象/主体词族
    quantity: str     # 数量阈值（数值约束）
    unit: str         # 单位
    grade: str        # 等级（资质约束）
    comparator: str   # >= / <= / grade>=
    negated: bool     # 正文是否在否定句里出现该约束
    state: str        # satisfied / violated / absent
    detail: str

    def as_dict(self) -> dict:
        return {
            "subject": self.subject,
            "quantity": self.quantity,
            "unit": self.unit,
            "grade": self.grade,
            "comparator": self.comparator,
            "negated": self.negated,
            "state": self.state,
            "detail": self.detail[:120],
        }


def split_atoms(text: str) -> list[dict]:
    """L2：把复合条款拆成原子要求（按子句切，保留序号与原文）。"""

    atoms: list[dict] = []
    for index, raw in enumerate(_CLAUSE_SPLIT.split(text or ""), start=1):
        atom = _LEAD_RE.sub("", raw).strip()
        if len(atom) < 4:
            continue
        atoms.append({"index": index, "text": atom})
    return atoms


def extract_time_windows(text: str) -> list[dict]:
    """L3/L4：提取时间窗（证据维度，不参与硬约束比较）。"""

    windows: list[dict] = []
    for match in _TIME_WINDOW_RE.finditer(text or ""):
        value = _cn_number(match.group(1))
        if value is None:
            continue
        windows.append({"years": value, "unit": match.group(2), "raw": match.group(0)})
    return windows


def _time_window_signals(req_windows: list[dict], body: str) -> list[dict]:
    """正文时间窗短于要求时记一条 review 信号；长于/相等不算问题。"""

    signals: list[dict] = []
    body_windows = extract_time_windows(body)
    for req in req_windows:
        months_req = req["years"] * (12 if req["unit"] == "年" else 1)
        for got in body_windows:
            months_got = got["years"] * (12 if got["unit"] == "年" else 1)
            if months_got < months_req:
                signals.append(
                    {
                        "required": req["raw"],
                        "found": got["raw"],
                        "note": "正文时间范围短于招标要求，建议人工确认",
                    }
                )
                break
    return signals


def _negated_in_body(verdicts: list[ConstraintVerdict]) -> bool:
    """正文是否以否定句应答某条约束（举证用，不影响 state）。"""

    return any(
        item.state != "absent" and any(token in item.detail for token in ("不", "未", "无", "非"))
        for item in verdicts
    )


# --------------------------------------------------------------- L5 判定结果
@dataclass(frozen=True)
class RequirementVerdict:
    state: str
    level: str          # "" 表示不产 issue；否则 blocking/critical/info
    reason: str
    matched_section: dict | None
    evidence: dict

    @property
    def label(self) -> str:
        return VERDICT_LABELS.get(self.state, self.state)

    def evidence_json(self) -> str:
        return json.dumps(self.evidence, ensure_ascii=False)


def judge(
    requirement,
    nodes: list[dict],
    matcher: SemanticMatcher | None = None,
) -> RequirementVerdict:
    """五层判定入口：条款对象 + 大纲节点 → 四态结论 + 全层证据。

    纯函数（除匹配器外无外部依赖），可直接单测，也是 verification affordance。
    ``requirement`` 需提供 text / marker / source_quote，可选 page / clause_no。
    """

    text = (getattr(requirement, "text", "") or "").strip()
    marker = getattr(requirement, "marker", "") or ""
    source_quote = getattr(requirement, "source_quote", "") or ""
    req_type = classify_requirement(text)
    matcher = matcher or RuleSemanticMatcher()
    core = requirement_core(text)
    threshold = 0.45 if len(core) >= 8 else 1.0
    combined = "\n".join((node.get("body") or "") for node in nodes)

    # L2 原子拆分
    atoms = split_atoms(text)
    # L3 结构化约束（数值/等级/否定/主体由 constraint_check 规则产生）
    verdicts: list[ConstraintVerdict] = evaluate_constraints(text, combined)
    structures = [
        Structure(
            subject=item.constraint.family,
            quantity=str(item.constraint.threshold) if item.constraint.kind == "numeric" else "",
            unit=item.constraint.unit,
            grade=item.constraint.grade,
            comparator=item.constraint.comparator,
            negated=any(token in item.detail for token in ("不", "未", "无", "非")),
            state=item.state,
            detail=item.detail,
        ).as_dict()
        for item in verdicts
    ]
    req_windows = extract_time_windows(text)
    time_signals = _time_window_signals(req_windows, combined)

    # L4 语义覆盖（可插拔 matcher）+ 章节锚点
    best_ratio = 0.0
    for node in nodes:
        body = node.get("body") or ""
        if body:
            best_ratio = max(best_ratio, matcher.coverage(text, body, req_type))
    anchor = max(
        (anchor_coverage(text, node.get("body") or "", req_type) for node in nodes),
        default=0.0,
    )
    section = find_covering_section(text, nodes, req_type)

    block_level = "blocking" if marker else "critical"
    evidence = {
        "layer1": {
            "text": text[:200],
            "source_quote": source_quote[:120],
            "marker": marker,
            "page": getattr(requirement, "page", 0) or 0,
            "clause_no": getattr(requirement, "clause_no", "") or "",
        },
        "layer2": {"atoms": atoms},
        "layer3": {
            "structures": structures,
            "time_windows": req_windows,
            "negated_in_body": _negated_in_body(verdicts),
        },
        "layer4": {
            "semantic_ratio": round(best_ratio, 3),
            "anchor_ratio": round(anchor, 3),
            "threshold": threshold,
            "constraints": [item.as_dict() for item in verdicts],
            "time_signals": time_signals,
            "matcher": type(matcher).__name__,
        },
    }

    # L5 四态判定。约束否决优先于语义相似——「1000Mbps vs 500Mbps」即便
    # 语义完全一致也判不满足；这正是用户强调的核心原则。
    #
    # 分支顺序对应用户给的判定策略表：
    #   任一硬性约束不满足        → 不满足（阻塞）
    #   部分约束缺失（其余已满足）→ 部分满足（阻塞）
    #   全部约束满足且证据充分    → 满足（放行）
    #   语义不明确 / 证据不足     → 人工复核（info，不阻塞）
    violated = [item for item in verdicts if item.state == "violated"]
    absent = [item for item in verdicts if item.state == "absent"]
    satisfied = [item for item in verdicts if item.state == "satisfied"]
    if violated:
        detail = "、".join(item.constraint.label for item in violated[:3])
        evidence["layer5"] = {"state": UNSATISFIED}
        return RequirementVerdict(
            UNSATISFIED,
            block_level,
            f"数值/等级/否定语义不符合要求：{detail}",
            None,
            evidence,
        )
    if absent:
        # 已证实部分约束、另有约束缺失 → 部分满足（阻塞，提示具体缺哪条）；
        # 一条都没证实 → 按既有「未响应即拦截」口径判不满足。
        missing = "、".join(item.constraint.label for item in absent[:3])
        if satisfied:
            evidence["layer5"] = {"state": PARTIAL}
            return RequirementVerdict(
                PARTIAL,
                block_level,
                f"部分约束未在正文体现：{missing}",
                None,
                evidence,
            )
        evidence["layer5"] = {"state": UNSATISFIED}
        return RequirementVerdict(
            UNSATISFIED,
            block_level,
            "未在正文找到明确响应",
            None,
            evidence,
        )
    if section is not None:
        evidence["layer5"] = {"state": SATISFIED}
        return RequirementVerdict(SATISFIED, "", "硬性约束均满足，证据充分", section, evidence)
    # 没有可量化约束，或约束全部满足但语义锚点没到阈值：
    # 有部分语义信号（含时间窗偏短）→ 人工复核（info）；完全没有 → 不满足。
    if best_ratio > 0 or anchor > 0 or time_signals:
        evidence["layer5"] = {"state": REVIEW}
        reasons = ["语义相近但证据不足"]
        if time_signals:
            reasons.append("时间范围短于要求")
        return RequirementVerdict(REVIEW, "info", "；".join(reasons) + "，建议人工复核", None, evidence)
    evidence["layer5"] = {"state": UNSATISFIED}
    return RequirementVerdict(
        UNSATISFIED,
        block_level,
        "未在正文找到明确响应",
        None,
        evidence,
    )


def verdict_from_status(response_status: str) -> bool:
    """人工已确认（responded/na）的条款跳过自动判定。"""

    return (response_status or "pending") != "pending"
