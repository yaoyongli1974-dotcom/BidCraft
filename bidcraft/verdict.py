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
from datetime import date
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
# 原子拆分时去掉句首噪声，但**不能吃掉数值**（「2小时内响应」「3项业绩」
# 的首位数字是约束值，不是列表编号）。只剥离符号/项目符号与「N. / N、/ (N)」
# 形态的显式编号。
_LEAD_SYMBOLS = re.compile(r"^[\s★▲•●◆■*\-]+")
_LEAD_ENUM = re.compile(r"^\(?\d{1,3}\s*[、.．)）]\s*|^[①②③④⑤⑥⑦⑧⑨⑩]+\s*")


def _strip_lead(raw: str) -> str:
    out = raw
    for _ in range(4):
        before = out
        out = _LEAD_SYMBOLS.sub("", out)
        out = _LEAD_ENUM.sub("", out)
        if out == before:
            break
    return out.strip()


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


# 复合条款的连接/转折词。长词优先，避免「并」先于「并提供」匹配。
# 连接词本身是拆分边界候选；是否真拆要看后续片段是否带独立约束/主体。
_ATOM_CONNECTORS = re.compile(r"(?:并|且|同时|以及|其中|但|但须|还须|另外|须同时|均应)")

# 真正用于「无标点也能拆」的连接词拆分表（长词在前）。
_ATOM_CONNECTOR_SPLIT = re.compile(
    r"(?:并提供|并满足|并且|须同时|同时|以及|还须|均应|分别|其中|并|且|但须|但|另外)"
)
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


def _clause_spans(text: str) -> list[tuple[int, int, str]]:
    """按标点切片，返回 (start, end, 片段) 且保留原文偏移。"""

    spans: list[tuple[int, int, str]] = []
    pos = 0
    for match in _CLAUSE_SPLIT.finditer(text):
        if match.start() > pos:
            spans.append((pos, match.start(), text[pos:match.start()]))
        pos = match.end()
    if pos < len(text):
        spans.append((pos, len(text), text[pos:]))
    return spans


def _connector_spans(segment: str) -> list[tuple[int, int]]:
    """在一个标点子句内找「可独立成原子」的连接词边界。

    只有连接词之后的片段带有自己的约束或主体时才认定为真拆分——避免把
    「一级资质并保持有效」这类共同修饰同一主体的补充说明机械切开。
    """

    matches = list(_ATOM_CONNECTOR_SPLIT.finditer(segment))
    kept: list[tuple[int, int]] = []
    for i, match in enumerate(matches):
        tail_end = matches[i + 1].start() if i + 1 < len(matches) else len(segment)
        tail = segment[match.end():tail_end]
        if extract_constraints(tail) or _atom_subjects(tail):
            kept.append((match.start(), match.end()))
    return kept


def split_atoms(text: str, requirement_id: str = "") -> list[dict]:
    """L2：把复合条款拆成原子要求（R01）。

    - 同时按**标点**与**连接词**切分——「具有一级资质并具有三项业绩同时提供
      两小时响应」这种无标点复合条款也能拆开；
    - 不机械切分：连接词后的片段没有独立约束/主体时（共同修饰同一主体、
      补充说明）不拆；
    - 每个原子保留原文偏移 ``source_start`` / ``source_end`` 与稳定编号，
      可完整映射回原条款；``atom_id`` 按原文出现顺序生成，重复运行结果稳定；
    - 含转折/并列连接词或无可执行约束的片段标 ``ambiguous``，供上层转人工复核。
    """

    text = text or ""
    atoms: list[dict] = []
    for base_start, base_end, clause in _clause_spans(text):
        # 原子句含连接词 → 即便被干净切开也标记「拆分存在潜在限定」，
        # 供上层据此转人工复核（不武断按单原子自动下结论）。
        clause_has_connector = bool(_ATOM_CONNECTORS.search(clause))
        connectors = _connector_spans(clause)
        if connectors:
            cuts: list[tuple[int, int]] = []
            pos = 0
            for c_start, c_end in connectors:
                if c_start > pos:
                    cuts.append((pos, c_start))
                pos = c_end
            if pos < len(clause):
                cuts.append((pos, len(clause)))
        else:
            cuts = [(0, len(clause))]

        for rel_start, rel_end in cuts:
            raw = clause[rel_start:rel_end]
            atom = _strip_lead(raw)
            if len(atom) < 4:
                continue
            # 偏移按原文绝对位置记录；atoms 切片去掉首尾空白后重新定位。
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip()) if raw.strip() else 0
            abs_start = base_start + rel_start + leading
            abs_end = base_end - (len(clause) - rel_end) - trailing
            constraints = [item.label for item in extract_constraints(atom)]
            index = len(atoms) + 1
            atoms.append(
                {
                    "index": index,
                    "atom_id": f"A{index:02d}",
                    "requirement_id": requirement_id,
                    "text": atom,
                    "raw": raw.strip(),
                    "subjects": _atom_subjects(atom),
                    "constraints": constraints,
                    "source_start": abs_start,
                    "source_end": abs_end,
                    # 原片段自带连接词（可能相互限定）→ 拆分存疑。
                    # 注意：没有可抽取的约束不代表拆分有歧义（「三项业绩」
                    # 这类表述抽取不到数值，但语义清楚），交由 _judge_atom 处理。
                    "ambiguous": clause_has_connector,
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


# --------------------------------------------------------------- R03：招标截止日期时间窗
#: 事实台账 / Project.meta 中可能承载「投标截止时间」的键（按优先级）。
DEADLINE_KEYS = (
    "tender_deadline",
    "bid_deadline",
    "bid_submission_deadline",
    "投标截止时间",
    "投标截止日期",
    "递交投标文件截止时间",
    "截止日期",
    "deadline",
)
#: 业绩日期类型：必须区分签订/开工/竣工/验收，不能把签订日当作完成日。
_DATE_KIND_TOKENS = (
    ("completion", ("竣工", "完工", "完成", "交付使用", "投入使用")),
    ("acceptance", ("验收",)),
    ("contract", ("签订", "签约", "合同日期", "订立")),
    ("start", ("开工", "动工", "开工日期")),
)
# 同时覆盖「2025年」「2025年11月」「2023年11月22日」「2025-03-01」四种写法；
# 年必须有四位，月/日可缺省（只给年份也算明确日期，默认取 1 月 1 日并留痕）。
_FULL_DATE_RE = re.compile(
    r"(?P<year>20\d{2}|19\d{2})\s*年\s*(?:(?P<month>\d{1,2})\s*月)?\s*(?:(?P<day>\d{1,2})\s*日)?"
    r"|(?P<y2>20\d{2}|19\d{2})\s*[-/.]\s*(?P<m2>\d{1,2})\s*[-/.]\s*(?P<d2>\d{1,2})"
)
# 业绩对象：保留原有的同类/类似项目等精确词，同时纳入普通「项目/工程」——
# 因为真正的日期约束在 _performance_dates 里（必须有四位年份），不会误伤「近一年培训」。
_ACHIEVEMENT_DATED_RE = re.compile(
    r"业绩|案例|同类项目|类似项目|相近项目|合同|中标|项目|工程"
)


def _date_parts(match):
    """从 _FULL_DATE_RE 的匹配里取 (year, month, day)，缺省补 1。"""

    if match.group("y2"):
        return int(match.group("y2")), int(match.group("m2")), int(match.group("d2"))
    return (
        int(match.group("year")),
        int(match.group("month") or 1),
        int(match.group("day") or 1),
    )


def parse_deadline(value) -> date | None:
    """把事实台账/meta 里的截止日期值解析为 ``date``；无法解析返回 None。

    只接受明确的年（月日），绝不回退到服务器当前时间——任务书 R03 明令禁止
    用当前时间替代招标截止日期。
    """

    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    match = _FULL_DATE_RE.search(text)
    if not match:
        return None
    try:
        year, month, day = _date_parts(match)
        return date(year, month, day)
    except ValueError:
        return None


def resolve_deadline(facts, meta: dict | None = None) -> date | None:
    """R03：从事实台账 + Project.meta 解析招标截止日期，找不到返回 None。

    ``facts`` 是有 ``key``/``value`` 属性的对象序列（Fact 行）或 dict 序列。
    按 DEADLINE_KEYS 优先级取第一个能解析出日期的值；台账优先于 meta。
    """

    def as_kv(item):
        if isinstance(item, dict):
            return item.get("key"), item.get("value")
        return getattr(item, "key", None), getattr(item, "value", None)

    buckets: dict[str, str] = {}
    for item in facts or []:
        key, value = as_kv(item)
        if key and value:
            buckets.setdefault(str(key).strip().lower(), str(value))
    meta = meta or {}
    for key, value in meta.items():
        if key and value and str(key).strip().lower() not in buckets:
            buckets[str(key).strip().lower()] = str(value)
    for wanted in DEADLINE_KEYS:
        for stored_key, stored_value in buckets.items():
            if stored_key == wanted.lower():
                parsed = parse_deadline(stored_value)
                if parsed is not None:
                    return parsed
    return None


def _date_kind(clause: str, date_match) -> str:
    """推断某日期是签订 / 开工 / 竣工 / 验收，取日期附近的动词。"""

    before = clause[: date_match.start()]
    after = clause[date_match.end() :]
    context = before[-24:] + after[:24]
    for kind, tokens in _DATE_KIND_TOKENS:
        if any(token in context for token in tokens):
            return kind
    return "unknown"


def _performance_dates(body: str) -> list[dict]:
    """抽取正文里带明确日期的业绩记录，逐条保留日期类型与原文出处。"""

    records: list[dict] = []
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(body or "") if c.strip()]
    for clause in clauses:
        if not _ACHIEVEMENT_DATED_RE.search(clause):
            continue
        for match in _FULL_DATE_RE.finditer(clause):
            year, month, day = _date_parts(match)
            # 年份精度（「2023年」无月）单独标记：按日历年比较，不臆造具体月日。
            precision = "year" if not (match.group("month") or match.group("m2")) else "day"
            records.append(
                {
                    "year": year,
                    "month": month,
                    "day": day,
                    "precision": precision,
                    "kind": _date_kind(clause, match),
                    "source_quote": clause[:200],
                }
            )
    return records


def _cutoff_date(deadline: date, years: int, unit: str) -> date:
    """按招标截止日期回退时间窗：年→同年同月同日，月→按月回退。"""

    if unit == "年":
        try:
            return deadline.replace(year=deadline.year - years)
        except ValueError:  # 2 月 29 日
            return deadline.replace(year=deadline.year - years, day=28)
    # 月：按 30 天近似（仅证据/提示口径，不改变硬约束）。
    from datetime import timedelta

    return deadline - timedelta(days=years * 30)


def _deadline_window_signals(
    req_windows: list[dict], body: str, deadline: date | None
) -> dict:
    """R03：按招标截止日期逐条校验业绩日期。

    返回 ``{applicable, has_deadline, records, expired, valid, missing_deadline,
    boundary_conflict}``。本函数只产出结构化证据；是否升级为终态由 judge 决定。
    """

    out: dict = {
        "applicable": False,
        "has_deadline": deadline is not None,
        "deadline": deadline.isoformat() if deadline else "",
        "records": [],
        "expired": 0,
        "valid": 0,
        "missing_deadline": False,
        "boundary_conflict": False,
    }
    if not req_windows:
        return out
    records = _performance_dates(body)
    if not records:
        # 正文没有可解析的明确日期 → 沿用既有「相对时间窗只作证据」口径，
        # 不在这里强行转 review（守住 test_time_window_is_evidence_not_violation）。
        return out
    out["applicable"] = True
    out["records"] = records
    if deadline is None:
        # 有明确业绩日期但拿不到招标截止日期 → 无法可靠计算，转人工复核。
        out["missing_deadline"] = True
        return out
    for req in req_windows:
        cutoff = _cutoff_date(deadline, req["years"], req["unit"])
        for rec in records:
            # 签订/开工日期不能等同完成日期——口径冲突，转人工。
            if rec["kind"] in {"contract", "start"}:
                out["boundary_conflict"] = True
                continue
            if rec.get("precision") == "year":
                # 只给年份：按日历年近似（cutoff.year..deadline.year）。这是
                # 证据口径，不臆造 1 月 1 日；边界年份转人工更稳妥但仍计数。
                if cutoff.year <= rec["year"] <= deadline.year:
                    out["valid"] += 1
                    rec["in_window"] = True
                else:
                    out["expired"] += 1
                    rec["in_window"] = False
                continue
            try:
                rec_date = date(rec["year"], rec["month"], rec["day"])
            except ValueError:
                out["boundary_conflict"] = True
                continue
            if rec_date < cutoff:
                out["expired"] += 1
                rec["in_window"] = False
            elif rec_date <= deadline:
                out["valid"] += 1
                rec["in_window"] = True
            else:
                # 完成日期晚于截止日期：数据冲突，转人工。
                out["boundary_conflict"] = True
    return out


def _judge_atom(
    atom: dict,
    nodes: list[dict],
    combined: str,
    matcher: SemanticMatcher,
    req_type: str,
) -> str:
    """对单个原子独立执行约束判定，返回四态之一（R01）。"""

    text = atom.get("text", "")
    verdicts = evaluate_constraints(text, combined)
    if any(item.state == "violated" for item in verdicts):
        return UNSATISFIED
    if any(item.state == "unclear" for item in verdicts):
        return REVIEW
    absent = [item for item in verdicts if item.state == "absent"]
    satisfied = [item for item in verdicts if item.state == "satisfied"]
    if absent:
        # 与整条口径一致：有约束已证实、其余缺失 → 部分满足；全缺 → missing。
        return PARTIAL if satisfied else "missing"
    if find_covering_section(text, nodes, req_type) is not None:
        return SATISFIED
    best = max(
        (matcher.coverage(text, n.get("body") or "", req_type) for n in nodes if n.get("body")),
        default=0.0,
    )
    anchor = max((anchor_coverage(text, n.get("body") or "", req_type) for n in nodes), default=0.0)
    if best > 0 or anchor > 0:
        return REVIEW
    return "missing"


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
    deadline: date | None = None,
) -> RequirementVerdict:
    """五层判定入口：条款对象 + 大纲节点 → 四态结论 + 全层证据。

    纯函数（除匹配器外无外部依赖），可直接单测，也是 verification affordance。
    ``requirement`` 需提供 text / marker / source_quote，可选 page / clause_no。
    ``deadline`` 是招标截止日期（R03）；不传时时间窗沿用旧的「只作证据」口径，
    传入后对带明确完成日期的业绩逐条按截止日期校验。
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
    # R03：按招标截止日期逐条校验业绩日期（只有带明确日期的业绩才介入）。
    deadline_window = _deadline_window_signals(req_windows, combined, deadline)

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
        "layer2": {
            "atoms": atoms,
            "ambiguous": atom_split_is_ambiguous(atoms),
        },
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
            "deadline_window": deadline_window,
            "matcher": type(matcher).__name__,
        },
    }

    # R01：复合条款的原子独立判定闭环。先把每个原子对正文独立判一遍，
    # 再按汇总原则合成整条结论；拆分本身存疑时即便各原子看似命中也不自动判满足。
    atom_states: list[str] = []
    if len(atoms) > 1:
        atom_states = [_judge_atom(atom, nodes, combined, matcher, req_type) for atom in atoms]
        evidence["layer2"]["atom_states"] = [
            {"atom_id": atom["atom_id"], "text": atom["text"], "state": state}
            for atom, state in zip(atoms, atom_states)
        ]
        split_ambiguous = atom_split_is_ambiguous(atoms)
        # 汇总原则（任务书 R01-7）：
        #   任一原子明确不满足 → 整条不满足；
        #   部分满足、部分缺失 → 部分满足；
        #   全部满足且拆分可靠 → 满足；拆分存疑 → 人工复核（不自动判满足）。
        if UNSATISFIED in atom_states:
            agg_state, agg_detail = UNSATISFIED, "复合要求中存在原子不满足"
        elif PARTIAL in atom_states:
            agg_state, agg_detail = PARTIAL, "复合要求中部分约束缺少响应"
        elif "missing" in atom_states:
            if SATISFIED in atom_states:
                agg_state, agg_detail = PARTIAL, "复合要求中部分约束缺少响应"
            else:
                agg_state, agg_detail = UNSATISFIED, "复合要求的所有原子均未响应"
        elif REVIEW in atom_states or split_ambiguous:
            agg_state, agg_detail = REVIEW, "复合要求拆分存在歧义或原子需人工确认"
        elif all(state == SATISFIED for state in atom_states):
            agg_state, agg_detail = SATISFIED, "复合要求的全部原子均满足"
        else:  # pragma: no cover - 防御
            agg_state, agg_detail = REVIEW, "复合要求无法可靠汇总"

        lvl = "" if agg_state == SATISFIED else (
            review_level(marker) if agg_state == REVIEW else block_level
        )
        evidence["layer5"] = {"state": agg_state, "aggregated_from_atoms": True}
        return RequirementVerdict(agg_state, lvl, agg_detail, None, evidence)

    # L5 四态判定。约束否决优先于语义相似——「1000Mbps vs 500Mbps」即便
    # 语义完全一致也判不满足；这正是用户强调的核心原则。
    #
    # 分支顺序对应用户给的判定策略表：
    #   任一硬性约束不满足        → 不满足（阻塞）
    #   部分约束缺失（其余已满足）→ 部分满足（阻塞）
    #   全部约束满足且证据充分    → 满足（放行）
    #   语义不明确 / 证据不足     → 人工复核（info，不阻塞）
    # R03：业绩时间窗按招标截止日期进入最终判定（不只是证据）。
    # 仅当正文出现「带明确日期的业绩」时才介入；相对时间窗（近五年6项）仍走旧口径。
    dw = deadline_window
    deadline_driven_satisfied = False
    if dw["applicable"]:
        # 业绩数量约束（≥ N 项），用于把「有效业绩数」和数量要求对齐。
        need: int | None = None
        count_idx: int | None = None
        for idx, item in enumerate(verdicts):
            c = item.constraint
            if c.kind == "numeric" and c.comparator == ">=" and c.family == "achievement":
                need, count_idx = int(c.threshold), idx
                break
        if dw["missing_deadline"] or dw["boundary_conflict"]:
            # 缺截止日期 / 日期口径冲突（签订当完成、完成日晚于截止）→ 人工复核。
            reason = (
                "业绩含明确日期但缺少招标截止日期，无法计算时间窗，转人工复核"
                if dw["missing_deadline"]
                else "业绩日期口径冲突（签订/开工日不等于完成日，或完成日晚于截止日期），转人工复核"
            )
            evidence["layer5"] = {"state": REVIEW, "deadline_driven": True}
            return RequirementVerdict(REVIEW, review_level(marker), reason, None, evidence)
        if need is None:
            # 有时间窗业绩但抽不到数量要求 → 保守转人工。
            evidence["layer5"] = {"state": REVIEW, "deadline_driven": True}
            return RequirementVerdict(
                REVIEW,
                review_level(marker),
                "业绩时间窗无法与数量要求关联，转人工复核",
                None,
                evidence,
            )
        # 用逐条日期核算的「有效业绩数」覆盖被年份数字污染成 unclear 的数量约束。
        if dw["valid"] >= need:
            verdicts = list(verdicts)
            verdicts[count_idx] = ConstraintVerdict(
                verdicts[count_idx].constraint,
                "satisfied",
                f"近时间窗内有效业绩 {dw['valid']} 项，满足不少于 {need} 项",
            )
            deadline_driven_satisfied = True
        else:
            detail = (
                f"有效业绩 {dw['valid']} 项不足 {need} 项（时间窗外 {dw['expired']} 项）"
                if dw["valid"]
                else f"业绩均在时间窗外，有效 0 项（过期 {dw['expired']} 项）"
            )
            evidence["layer5"] = {"state": PARTIAL if dw["valid"] else UNSATISFIED, "deadline_driven": True}
            lvl = block_level
            return RequirementVerdict(PARTIAL if dw["valid"] else UNSATISFIED, lvl, detail, None, evidence)

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
    if section is not None or deadline_driven_satisfied:
        evidence["layer5"] = {"state": SATISFIED}
        reason = (
            "业绩时间窗按招标截止日期核算，有效业绩满足数量要求"
            if deadline_driven_satisfied
            else "硬性约束均满足，证据充分"
        )
        return RequirementVerdict(SATISFIED, "", reason, section, evidence)

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
