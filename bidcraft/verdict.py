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
    extract_constraints,
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

# 人工复核态的导出门禁级别，按条款重要性分级（用户确认的策略）：
#   ★ 未确认不得正式交付 → blocking（可强制导出，但留完整审计）；
#   ▲ 原则上需人工确认，但不拦交付 → critical（严重提示，非阻断）；
#   普通条款仅提示 → info。
def review_level(marker: str) -> str:
    if "★" in (marker or ""):
        return "blocking"
    if "▲" in (marker or ""):
        return "critical"
    return "info"

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


# 复合条款的连接/转折词：出现它们说明子句之间可能相互限定，机械切分有歧义。
_ATOM_CONNECTORS = re.compile(r"(?:并|且|同时|以及|其中|但|但须|还须|另外|须同时|均应)")
# 原子归属用的主体词，辅助判断一个子句属于哪类约束。
_ATOM_SUBJECT_HINTS = {
    "资质": ("资质", "资格", "证书", "建造师", "等级", "认证"),
    "业绩": ("业绩", "案例", "项目", "合同", "中标"),
    "响应": ("响应", "到场", "到达", "回复", "服务"),
    "质保": ("质保", "保修", "质量保证", "免费"),
    "带宽": ("带宽", "速率", "吞吐", "传输", "容量", "存储"),
    "时间窗": ("近", "最近", "过去", "年", "月"),
}


def _atom_subjects(text: str) -> list[str]:
    return [name for name, hints in _ATOM_SUBJECT_HINTS.items() if any(h in text for h in hints)]


def split_atoms(text: str) -> list[dict]:
    """L2：把复合条款拆成原子要求。

    不再是「见到标点就切」的机械拆分（T03）：
    - 保留每个原子对应的**原始子句**与稳定编号（``atom_id`` / ``index``）；
    - 为每个原子标注它归属的约束主体（``subjects``）与该子句命中的约束标签
      （``constraints``），保留「原条款 ↔ 原子要求」的映射；
    - 含转折/并列连接词或跨子句共享主语时打 ``ambiguous`` 标记：这类拆分
      可能有歧义，上层可据此转人工复核，而不是武断地按单原子判定。
    """

    atoms: list[dict] = []
    raw_clauses = [raw for raw in _CLAUSE_SPLIT.split(text or "") if raw.strip()]
    for index, raw in enumerate(raw_clauses, start=1):
        atom = _LEAD_RE.sub("", raw).strip()
        if len(atom) < 4:
            continue
        constraints = [item.label for item in extract_constraints(atom)]
        atoms.append(
            {
                "index": index,
                "atom_id": f"A{index:02d}",
                "text": atom,
                "raw": raw.strip(),
                "subjects": _atom_subjects(atom),
                "constraints": constraints,
                # 连接词说明子句可能相互限定；无可执行约束的片段也视为拆分存疑。
                "ambiguous": bool(_ATOM_CONNECTORS.search(atom)) or not constraints,
            }
        )
    return atoms


def atom_split_is_ambiguous(atoms: list[dict]) -> bool:
    """拆分是否存在歧义（供上层转人工复核）。

    复合条款里只要有原子带连接词、或某原子未能抽出任何可执行约束，就认为这次
    拆分不够可靠——交给人工确认，而不是凭机械切分自动下结论。
    """

    return len(atoms) > 1 and any(atom.get("ambiguous") for atom in atoms)


def extract_time_windows(text: str) -> list[dict]:
    """L3/L4：提取时间窗（证据维度，不参与硬约束比较）。"""

    windows: list[dict] = []
    for match in _TIME_WINDOW_RE.finditer(text or ""):
        value = _cn_number(match.group(1))
        if value is None:
            continue
        windows.append({"years": value, "unit": match.group(2), "raw": match.group(0)})
    return windows


# 业绩/完成日期的关联词（T05）：时间窗必须和业绩对象、完成日期放一起评估，
# 不能因为正文某处出现一个更短的时间窗就认定「全部业绩不足」。
_DATE_RE = re.compile(r"(?:20\d{2}|19\d{2})\s*[年\-/\.]\s*\d{1,2}\s*月?")
_ACHIEVEMENT_RE = re.compile(r"业绩|案例|同类项目|类似项目|相近项目|合同|中标")


def _time_window_signals(req_windows: list[dict], body: str) -> list[dict]:
    """时间窗 × 业绩对象 × 完成日期 的关联审阅信号（T05）。

    规则（保守，只产 review 信号，绝不升级成 violated）：
    - 只有「较短时间窗」与业绩对象出现在**同一子句**时才记信号——避免正文里
      一个无关的短窗（如「近一年完成培训」）误伤另一处的业绩响应；
    - 同一子句里带有明确完成日期（如「2023年5月」）时，日期是更可靠的锚点，
      时间窗偏短仅作提示，不强判业绩不足；
    - 要求含时间窗，但正文里带业绩对象的子句**没有任何日期或时间窗** → 记一条
      「缺少完成日期」的信号，转人工复核（无法计算业绩是否满足时间范围）。
    """

    signals: list[dict] = []
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(body or "") if c.strip()]
    achievement_clauses = [c for c in clauses if _ACHIEVEMENT_RE.search(c)]

    for req in req_windows:
        months_req = req["years"] * (12 if req["unit"] == "年" else 1)
        window_hits: list[dict] = []
        dated = False
        for clause in achievement_clauses:
            if _DATE_RE.search(clause):
                dated = True
            for got in extract_time_windows(clause):
                window_hits.append({"required": req["raw"], "found": got["raw"], "clause": clause})

        if not window_hits and not dated and achievement_clauses:
            signals.append(
                {
                    "required": req["raw"],
                    "found": "",
                    "note": "业绩未注明完成日期或时间范围，无法核对，转人工复核",
                }
            )
            continue

        for hit in window_hits:
            got = extract_time_windows(hit["clause"])[0]
            months_got = got["years"] * (12 if got["unit"] == "年" else 1)
            if months_got < months_req:
                signals.append(
                    {
                        "required": req["raw"],
                        "found": got["raw"],
                        "note": (
                            "业绩时间窗短于要求但有明确完成日期，仅提示核对"
                            if dated
                            else "业绩时间范围短于招标要求，建议人工确认"
                        ),
                    }
                )
                break  # 同一要求只记一条，避免长文里多处业绩重复报
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

    # 数值单位缺失或跨量纲 → 不能安全比较，转人工复核（绝不按「不满足」拦死，
    # 也绝不按「满足」放行）。级别按条款重要性分级：★ blocking / ▲ critical / 普通 info。
    unclear = [item for item in verdicts if item.state == "unclear"]
    if unclear:
        evidence["layer5"] = {"state": REVIEW, "reason": "单位不明确或跨量纲"}
        detail = "；".join(item.detail for item in unclear[:3])
        return RequirementVerdict(
            REVIEW,
            review_level(marker),
            f"数值单位缺失或跨量纲，无法自动判定：{detail}，请人工复核",
            None,
            evidence,
        )

    # 没有可量化约束，或约束全部满足但语义锚点没到阈值：
    # 有部分语义信号（含时间窗偏短）→ 人工复核；完全没有 → 不满足。
    if best_ratio > 0 or anchor > 0 or time_signals:
        evidence["layer5"] = {"state": REVIEW}
        reasons = ["语义相近但证据不足"]
        if time_signals:
            reasons.append("时间范围短于要求")
        return RequirementVerdict(
            REVIEW,
            review_level(marker),
            "；".join(reasons) + "，建议人工复核",
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


def verdict_from_status(response_status: str) -> bool:
    """人工已确认（responded/na）的条款跳过自动判定。"""

    return (response_status or "pending") != "pending"
