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

import calendar
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
    clause_at,
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


# R09：转折/对比连接词才天然带来「两个要求相互限定」的歧义；
# 并列连接词（并/且/同时/以及）若切出的片段各自带独立约束，属于干净拆分。
_CONTRAST_CONNECTOR = re.compile(r"但须|但|然而|除外|另有")


def split_atoms(text: str, requirement_id: str = "") -> list[dict]:
    """L2：把复合条款拆成原子要求（R01 + R09 歧义分离）。

    - 同时按**标点**与**连接词**切分——无标点复合条款也能拆开；
    - 不机械切分：连接词后的片段没有独立约束/主体时（共同修饰同一主体、
      补充说明）不拆，只标 ``shared_subject``；
    - **R09**：连接词出现 ≠ 拆分歧义。只有转折/对比连接词，或「切出的片段
      既无约束又无主体」才标歧义；干净拆分不再强制转人工复核；
    - 每个原子保留原文偏移与稳定编号，可完整映射回原条款。
    """

    text = text or ""
    atoms: list[dict] = []
    for base_start, base_end, clause in _clause_spans(text):
        contrast = bool(_CONTRAST_CONNECTOR.search(clause))
        connectors = _connector_spans(clause)  # 仅保留「切得干净」的连接词边界
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
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip()) if raw.strip() else 0
            abs_start = base_start + rel_start + leading
            abs_end = base_end - (len(clause) - rel_end) - trailing
            constraints = [item.label for item in extract_constraints(atom)]
            subjects = _atom_subjects(atom)
            independent_constraint = bool(constraints) or bool(subjects)
            split_successful = bool(connectors)
            # R09：歧义只来自转折连接词，或「被切开却既无约束又无主体」的片段。
            split_ambiguous = contrast or (split_successful and not independent_constraint
                                           and bool(_ATOM_CONNECTORS.search(raw)))
            shared_subject = (not split_successful) and bool(_ATOM_CONNECTORS.search(clause)) \
                and not independent_constraint
            index = len(atoms) + 1
            atoms.append(
                {
                    "index": index,
                    "atom_id": f"A{index:02d}",
                    "requirement_id": requirement_id,
                    "text": atom,
                    "raw": raw.strip(),
                    "subjects": subjects,
                    "constraints": constraints,
                    "source_start": abs_start,
                    "source_end": abs_end,
                    # 兼容字段：歧义 = 真正的拆分歧义（不再是「有连接词就歧义」）。
                    "ambiguous": split_ambiguous,
                    "split_successful": split_successful,
                    "split_ambiguous": split_ambiguous,
                    "independent_constraint": independent_constraint,
                    "shared_subject": shared_subject,
                }
            )
    return atoms


def atom_split_is_ambiguous(atoms: list[dict]) -> bool:
    """R09：只有**真正无法可靠拆分/关联**的原子才算歧义。

    并列连接词若能干净切出各自带约束的原子，不再强制转人工复核（v0.21.0 的
    「有连接词即歧义」是误判来源，本版分离）。转折/对比、或切出的片段既无
    约束又无主体才转人工。
    """

    return len(atoms) > 1 and any(
        atom.get("split_ambiguous") or atom.get("ambiguous") for atom in atoms
    )


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
      提示（日期/证据不足，建议人工复核）。
    """

    signals: list[dict] = []
    if not req_windows:
        return signals
    clauses = [item.strip() for item in _CLAUSE_SPLIT.split(body or "") if item.strip()]
    for clause in clauses:
        if not _ACHIEVEMENT_RE.search(clause):
            continue
        has_date = bool(_DATE_RE.search(clause))
        has_window = bool(_TIME_WINDOW_RE.search(clause))
        if has_date:
            continue
        if has_window:
            signals.append({"note": "业绩时间窗短于要求或证据不足，建议人工复核",
                            "found": clause[:80]})
        elif not any(k in clause for k in ("提供了", "具有", "完成")):
            signals.append({"note": "业绩缺少完成日期，建议人工复核", "found": clause[:80]})
        else:
            signals.append({"note": "业绩缺少明确完成日期，建议人工复核", "found": clause[:80]})
    return signals


# --------------------------------------------------- R10 业绩证据 / R12 日期精度
# 日期类型词（区分签订/开工/竣工/验收），顺序即优先级。
_DATE_KIND_TOKENS = (
    ("completion", ("完成", "竣工", "完工", "交付")),
    ("acceptance", ("验收",)),
    ("contract", ("签订", "签约", "合同日期", "订立")),
    ("start", ("开工", "动工")),
)
# 同时覆盖「2025年」「2025年11月」「2023年11月22日」「2025-03-01」四种写法；
# 年必须有四位，月/日可缺省。R12：缺省必须保留精度，绝不臆造成 1 月 1 日。
_FULL_DATE_RE = re.compile(
    r"(?P<year>20\d{2}|19\d{2})\s*年\s*(?:(?P<month>\d{1,2})\s*月)?\s*(?:(?P<day>\d{1,2})\s*日)?"
    r"|(?P<y2>20\d{2}|19\d{2})\s*[-/.]\s*(?P<m2>\d{1,2})\s*[-/.]\s*(?P<d2>\d{1,2})"
)
_ACHIEVEMENT_DATED_RE = re.compile(
    r"业绩|案例|同类项目|类似项目|相近项目|合同|中标|项目|工程"
)
# R10：项目名 = 「XXX项目 / XXX工程」之前的修饰词，剥掉动词前缀。
_PROJECT_NAME_RE = re.compile(r"([\u4e00-\u9fffA-Za-z0-9]{1,12})\s*(?:项目|工程)\s*([A-Za-z0-9]{1,6})?")
_PROJECT_VERB_PREFIX = re.compile(r"^(完成|签订|中标|验收|竣工|承建|提供|实施|建设|的|了)+")
# R10：项目类型规则（招标明确类型时按此匹配；无法匹配转人工复核）。
_PROJECT_TYPE_HINTS = {
    "弱电智能化": ("弱电", "智能化", "楼宇智能"),
    "综合布线": ("综合布线", "布线"),
    "安防监控": ("安防", "监控", "摄像"),
    "软件开发": ("软件", "系统开发", "平台开发"),
    "装修工程": ("装修", "装饰"),
    "网络通信": ("网络", "通信", "交换机", "带宽"),
}

# 招标截止日期台账键（R03）：事实台账/meta 里这些 key 的值会被解析为截止日期。
DEADLINE_KEYS = (
    "tender_deadline", "bid_deadline", "bid_submission_deadline",
    "投标截止时间", "截止日期", "投标截止", "递交截止时间", "开标时间",
)


@dataclass
class PerformanceEvidence:
    """R10/R19：单条业绩证据（与 FactItem / 文档证据对齐的可追溯结构）。

    R19：项目身份不能只看名称——增加归一化名称、合同编号、客户名；日期必须
    归属到具体项目；来源页码/文件 ID 拿不到时必须**显式标记缺失**，不伪造。
    """

    performance_id: str
    project_name: str
    project_type: str
    contract_date: str
    start_date: str
    completion_date: str
    acceptance_date: str
    date_precision: str      # year / month / day
    date_kind: str           # completion / acceptance / contract / start / unknown
    source_quote: str
    source_page: int
    source_section: str
    evidence_file_id: str
    # R19 新增：项目身份与来源可信度（均带默认值，兼容既有位置/关键字构造）。
    normalized_project_name: str = ""
    contract_number: str = ""
    customer_name: str = ""
    identity_status: str = "ambiguous"       # certain / ambiguous
    source_reference_status: str = "missing"  # available / missing（不得伪造页码/文件 ID）

    def as_dict(self) -> dict:
        return {
            "performance_id": self.performance_id,
            "project_name": self.project_name,
            "project_type": self.project_type,
            "contract_date": self.contract_date,
            "start_date": self.start_date,
            "completion_date": self.completion_date,
            "acceptance_date": self.acceptance_date,
            "date_precision": self.date_precision,
            "date_kind": self.date_kind,
            "year": self._year(),
            "month": self._month(),
            "day": self._day(),
            "source_quote": self.source_quote[:200],
            "source_page": self.source_page,
            "source_section": self.source_section,
            "evidence_file_id": self.evidence_file_id,
            # R19：身份与来源元数据，供上层转人工复核 / 区分真实溯源与文本占位。
            "normalized_project_name": self.normalized_project_name,
            "contract_number": self.contract_number,
            "customer_name": self.customer_name,
            "identity_status": self.identity_status,
            "source_reference_status": self.source_reference_status,
        }

    def _effective(self) -> str:
        # 完成/验收日期才是「完成时间」；签订/开工不能当完成日。
        return self.completion_date or self.acceptance_date or ""

    def _parts(self) -> tuple[int | None, int | None, int | None]:
        # completion_date 存的是归一化 ISO（如 '2025' / '2025-03' / '2025-03-01'），
        # 直接拆分，不能依赖要求「年」字的 _FULL_DATE_RE。
        text = self._effective()
        if not text:
            return None, None, None
        parts = text.split("-")
        try:
            y = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else None
            d = int(parts[2]) if len(parts) > 2 else None
            return y, m, d
        except (ValueError, IndexError):
            return None, None, None

    def _year(self):
        return self._parts()[0]

    def _month(self):
        return self._parts()[1]

    def _day(self):
        return self._parts()[2]


def _date_parts(match):
    """R12：从 _FULL_DATE_RE 匹配取 (year, month, day)，缺省补 1。

    注意：这里的补 1 仅用于**构造 date 对象比较**；业绩证据的 ``date_precision``
    会单独记录原始精度（year/month/day），边界判定据此转人工复核，不会把
    「2023年」臆造成「2023-01-01」后直接计入或否决。
    """

    if match.group("y2"):
        return int(match.group("y2")), int(match.group("m2")), int(match.group("d2"))
    return (
        int(match.group("year")),
        int(match.group("month") or 1),
        int(match.group("day") or 1),
    )


def _date_precision(match) -> str:
    """R12：原始日期精度——year / month / day（不得臆造更精确的日期）。"""

    if match.group("y2") or match.group("day"):
        return "day"
    if match.group("month"):
        return "month"
    return "year"


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
    """R03：从事实台账 + Project.meta 解析招标截止日期，找不到返回 None。"""

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


def _required_project_type(requirement_text: str) -> str:
    """R10：招标条款里是否指定了类似项目类型；没指定返回空串（不做类型过滤）。"""

    for ptype, hints in _PROJECT_TYPE_HINTS.items():
        if any(hint in (requirement_text or "") for hint in hints):
            return ptype
    return ""


# R19：合同编号 / 客户名 / 项目名归一化，用于项目身份识别与去重。
_CONTRACT_NO_RE = re.compile(r"(?:合同编号|合同号|编号)\s*[:：]?\s*([A-Za-z0-9\-_]{4,40})")
_CUSTOMER_RE = re.compile(r"(?:客户|甲方|业主|建设单位|采购单位)\s*[:：]?\s*([\u4e00-\u9fffA-Za-z0-9]{2,20})")
_NAME_NOISE = re.compile(r"[\s（）()「」『』·、,，。:：\-—]+")


def _clause_at(text: str, pos: int) -> str:
    """返回 pos 所在的子句（复用 constraint_check 的口径）。"""

    return clause_at(text, pos)


def _identity_context(text: str, pos: int, name_spans: list[tuple[int, int, str]]) -> str:
    """R19：取「离日期最近的项目名」附近 ±35 字上下文，覆盖「合同编号HT-001」小句。

    只围绕**单个**项目名取窗口，避免把相邻另一项目的合同编号也圈进来造成
    误判为身份冲突。中文里日期常紧挨项目名（「2025年完成A项目」），以最近项目名为锚。
    """

    # 以句子边界（。！？）为限，避免把下一句另一项目的合同编号圈进来造成假冲突。
    sent = re.compile(r"[。！？!?]")
    lo = 0
    hi = len(text)
    for m in sent.finditer(text):
        if m.end() <= pos:
            lo = m.end()
        elif m.start() > pos:
            hi = m.start()
            break
    return text[lo:hi]


def _normalized_name(name: str) -> str:
    """R19：归一化项目名（去噪声/标点，小写），用于身份比对，不单独作合并依据。"""

    return _NAME_NOISE.sub("", name or "").lower()


def _project_identity(clause: str, raw_name: str) -> tuple[str, str, str, str]:
    """R19：从子句提取 (归一化名, 合同编号, 客户名, 身份状态)。

    身份判定优先级：合同编号 > 客户+名称 > 名称。仅凭相似名称不合并；
    有多个互相矛盾的合同编号/客户名时标 ambiguous 交上层转人工。
    """

    normalized = _normalized_name(raw_name)
    contract_nos = set(_CONTRACT_NO_RE.findall(clause))
    customers = set(_CUSTOMER_RE.findall(clause))
    contract_no = next(iter(contract_nos)) if len(contract_nos) == 1 else ""
    customer = next(iter(customers)) if len(customers) == 1 else ""
    if len(contract_nos) > 1 or len(customers) > 1:
        return normalized, contract_no, customer, "ambiguous"
    if contract_no:
        return normalized, contract_no, customer, "certain"
    if customer and normalized:
        return normalized, "", customer, "certain"
    # 仅名称：有具体名称（含「A」这类标识）或唯一占位都视为可区分身份，
    # 保证既有以 A/B/C 命名、以及无「项目」字样正文的用例口径不变。
    # 只有「多个合同编号/客户名冲突」才是身份歧义（见上方 len>1 分支）。
    return normalized, "", "", "certain"


def _extract_performance_evidence(body: str, requirement_text: str = "") -> list[PerformanceEvidence]:
    """R10：抽取正文里带明确日期的**独立业绩记录**，逐条保留项目归属与出处。

    - 同一项目的合同/开工/竣工/验收日期合并进一条记录，不重复计数；
    - 签订/开工日期不写入 completion_date（不能当完成时间）；
    - 招标指定类似项目类型时，类型不符的记录不计入；类型无法可靠匹配时，
      记录 ``project_type='?'`` 交由上层转人工复核。
    """

    text = body or ""
    # R19：在整段正文上按位置处理，避免标点切分把「项目」和「合同编号」拆散。
    # 项目名捕获后剥掉日期/动词前缀（「12月完成C项目」→「C」）。
    name_spans: list[tuple[int, int, str]] = []
    for nm in _PROJECT_NAME_RE.finditer(text):
        raw = (nm.group(1) or "") + (nm.group(2) or "")
        raw = re.sub(r"^(?:20\d{2}|19\d{2})\s*年?", "", raw)
        raw = re.sub(r"^\d{1,2}\s*月", "", raw)
        # 「A项目2025年」捕获组可能带尾随年份，剥掉后再取动词前缀。
        raw = re.sub(r"(?:20\d{2}|19\d{2})\s*年?\s*$", "", raw)
        name = _PROJECT_VERB_PREFIX.sub("", raw).strip()
        if name:
            name_spans.append((nm.start(), nm.end(), name))

    seen: dict[str, PerformanceEvidence] = {}
    order = 0
    for match in _FULL_DATE_RE.finditer(text):
        # 日期所在子句（用于类型/日期类型判定）；上下文窗口用于身份（合同号/客户）。
        clause = _clause_at(text, match.start())
        if not _ACHIEVEMENT_DATED_RE.search(clause):
            continue
        year, month, day = _date_parts(match)
        precision = _date_precision(match)
        kind = _date_kind(clause, match)
        iso = f"{year:04d}"
        if precision in ("month", "day"):
            iso += f"-{month:02d}"
        if precision == "day":
            iso += f"-{day:02d}"

        # 就近项目名：按绝对距离取（中文日期常在项目名前「2025年完成A项目」）。
        chosen = ""
        if name_spans:
            chosen = min(name_spans, key=lambda sp: min(
                abs(sp[0] - match.start()), abs(sp[1] - match.end())
            ))[2]
        if not chosen:
            order += 1
            chosen = f"未命名项目{order}"

        # 身份上下文：项目名附近 ±40 字，能覆盖「，合同编号HT-001」这一小句。
        ctx = _identity_context(text, match.start(), name_spans)
        normalized, contract_no, customer, id_status = _project_identity(ctx, chosen)
        if contract_no:
            key = f"contract:{contract_no}"
        elif customer and normalized:
            key = f"cust:{customer}:{normalized}"
        else:
            key = f"name:{normalized or chosen}"

        ptype = ""
        for candidate, hints in _PROJECT_TYPE_HINTS.items():
            if any(hint in clause for hint in hints):
                ptype = candidate
                break

        record = seen.get(key)
        if record is None:
            record = PerformanceEvidence(
                performance_id=f"PE-{len(seen) + 1:03d}",
                project_name=chosen, project_type=ptype or "?",
                contract_date="", start_date="", completion_date="", acceptance_date="",
                date_precision=precision, date_kind=kind,
                source_quote=clause, source_page=0, source_section="", evidence_file_id="",
                normalized_project_name=normalized, contract_number=contract_no,
                customer_name=customer, identity_status=id_status,
                source_reference_status="missing",  # 文本输入无真实页码/文件 ID，不伪造。
            )
            seen[key] = record
        else:
            if contract_no and not record.contract_number:
                record.contract_number = contract_no
                record.identity_status = "certain"
            if customer and not record.customer_name:
                record.customer_name = customer
            if id_status == "ambiguous":
                record.identity_status = "ambiguous"
            if record.project_type == "?" and ptype:
                record.project_type = ptype
        if kind == "contract":
            record.contract_date = record.contract_date or iso
        elif kind == "start":
            record.start_date = record.start_date or iso
        elif kind == "acceptance":
            record.acceptance_date = record.acceptance_date or iso
        elif kind == "completion":
            record.completion_date = record.completion_date or iso
        if record.date_precision == "year" and precision != "year":
            record.date_precision = precision
    return list(seen.values())


def _cutoff_date(deadline: date, years: int, unit: str) -> date:
    """R12：按招标截止日期回退时间窗——**日历规则**，月份用真实日历月回退。"""

    if unit == "年":
        total_months = years * 12
    else:
        total_months = years
    month_index = deadline.year * 12 + (deadline.month - 1) - total_months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    day = min(deadline.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _deadline_window_signals(
    req_windows: list[dict], body: str, deadline: date | None, requirement_text: str = ""
) -> dict:
    """R03/R10/R12：按招标截止日期逐条校验业绩日期与项目类型。

    返回 ``{applicable, has_deadline, deadline, records, expired, valid,
    missing_deadline, boundary_conflict, type_conflict, ...}``；只产出结构化证据，
    是否升级为终态由 _decide 决定。
    """

    out: dict = {
        "applicable": False, "has_deadline": deadline is not None,
        "deadline": deadline.isoformat() if deadline else "",
        "cutoff": _cutoff_date(deadline, req_windows[0]["years"], req_windows[0]["unit"]).isoformat()
        if (deadline and req_windows) else "",
        "records": [], "expired": 0, "valid": 0,
        "missing_deadline": False, "boundary_conflict": False,
        # R17：分别保留起始边界 / 截止边界证据，避免只看截止年而漏判起始年。
        "boundary_start": 0, "boundary_end": 0,
        "type_conflict": False, "identity_conflict": False, "project_type": "",
    }
    if not req_windows:
        return out
    records = _extract_performance_evidence(body, requirement_text)
    if not records:
        return out
    out["applicable"] = True
    out["records"] = [item.as_dict() for item in records]
    if deadline is None:
        out["missing_deadline"] = True
        return out

    required_type = _required_project_type(requirement_text)
    out["project_type"] = required_type
    need_cnt = max((window["years"] for window in req_windows), default=3)
    # 时间窗「近N年」由需求里的数量约束（另在 _decide 取 need）；这里只取年份窗口。
    years = need_cnt if False else req_windows[0]["years"]
    unit = req_windows[0]["unit"]
    cutoff = _cutoff_date(deadline, years, unit)

    for rec in records:
        # R19：项目身份不可靠（简称/全称无法确认、多个客户/合同号冲突）→ 转人工，
        # 不因为名称相似就把多条当同一项目累加，也不直接计入有效数量。
        if rec.identity_status == "ambiguous":
            out["identity_conflict"] = True
            continue
        # R10：招标指定类型时按类型过滤；无法匹配（'?'）→ 转人工复核。
        if required_type:
            if rec.project_type == "?":
                out["type_conflict"] = True
                continue
            if rec.project_type != required_type:
                continue
        effective = rec._effective()
        if not effective:
            # 只有签订/开工日期，没有完成/验收日期 → 口径冲突转人工。
            out["boundary_conflict"] = True
            continue
        y, m, d = rec._parts()
        if y is None:
            out["boundary_conflict"] = True
            continue
        precision = rec.date_precision
        try:
            rec_date = date(y, m or 1, d or 1)
        except ValueError:
            out["boundary_conflict"] = True
            continue
        # R12：年份/月份精度处于窗口边界年份/月份时无法精确判断 → 转人工复核，
        # 不直接计入有效数量（「2023年」不臆造成 2023-01-01）。
        if precision == "year":
            # R17：年份精度无法证明具体月日，起始年 / 截止年均存在边界歧义 → 转人工，
            # 绝不臆造成 1 月 1 日后计入（「2023年」无法证明在 2023-11-22 之后）。
            # 只有严格位于窗口内部年份（cutoff.year < y < deadline.year）才可计入。
            if y < cutoff.year:
                out["expired"] += 1
            elif y == cutoff.year:
                out["boundary_conflict"] = True
                out["boundary_start"] += 1
            elif y == deadline.year or y > deadline.year:
                out["boundary_conflict"] = True
                out["boundary_end"] += 1
            else:
                out["valid"] += 1
            continue
        if precision == "month":
            # R17：月份精度起始/截止月同样有歧义；严格内部月才计入，
            # 明确早于起始月的记过期，其余边界记人工。
            if (y, m) < (cutoff.year, cutoff.month):
                out["expired"] += 1
            elif (y, m) == (cutoff.year, cutoff.month):
                out["boundary_conflict"] = True
                out["boundary_start"] += 1
            elif (y, m) >= (deadline.year, deadline.month):
                out["boundary_conflict"] = True
                out["boundary_end"] += 1
            else:
                out["valid"] += 1
            continue
        # day 精度：按日历截止日精确比较，边界含等号（起始日 / 截止日都计入有效）。
        if rec_date < cutoff:
            out["expired"] += 1
        elif rec_date <= deadline:
            out["valid"] += 1
        else:
            out["boundary_conflict"] = True
            out["boundary_end"] += 1
    return out


def _negated_in_body(verdicts: list) -> bool:
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


def _decide(
    text: str,
    verdicts: list,
    deadline_window: dict,
    time_signals: list,
    best_ratio: float,
    anchor: float,
    section: dict | None,
    threshold: float,
    marker: str,
    block_level: str,
    evidence: dict,
) -> RequirementVerdict:
    """统一 L5 判定（R08）：任意一段文本（整条 / 单个原子）→ 四态结论。

    R08 关键：把「业绩按招标截止日期校验」下沉到这里，整条与每个原子走同一套
    完整检查，复合条款不再因提前 return 而绕过截止日期校验。
    """

    dw = deadline_window
    deadline_driven_satisfied = False
    if dw["applicable"]:
        need: int | None = None
        count_idx: int | None = None
        for idx, item in enumerate(verdicts):
            c = item.constraint
            if c.kind == "numeric" and c.comparator == ">=" and c.family == "achievement":
                need, count_idx = int(c.threshold), idx
                break
        if (dw["missing_deadline"] or dw["boundary_conflict"]
                or dw.get("type_conflict") or dw.get("identity_conflict")):
            if dw["missing_deadline"]:
                reason = "业绩含明确日期但缺少招标截止日期，无法计算时间窗，转人工复核"
            elif dw.get("identity_conflict"):
                reason = "业绩项目身份无法确定（名称/客户/合同编号存在歧义），转人工复核"
            elif dw.get("type_conflict"):
                reason = "类似项目类型无法与招标要求可靠匹配，转人工复核"
            else:
                reason = "业绩日期精度不足或口径冲突（签订/开工≠完成、完成日晚于截止），转人工复核"
            evidence["layer5"] = {"state": REVIEW, "deadline_driven": True}
            return RequirementVerdict(REVIEW, review_level(marker), reason, None, evidence)
        if need is None:
            evidence["layer5"] = {"state": REVIEW, "deadline_driven": True}
            return RequirementVerdict(
                REVIEW, review_level(marker),
                "业绩时间窗无法与数量要求关联，转人工复核", None, evidence,
            )
        if dw["valid"] >= need:
            verdicts = list(verdicts)
            verdicts[count_idx] = ConstraintVerdict(
                verdicts[count_idx].constraint, "satisfied",
                f"近时间窗内有效业绩 {dw['valid']} 项，满足不少于 {need} 项",
            )
            deadline_driven_satisfied = True
        else:
            detail = (
                f"有效业绩 {dw['valid']} 项不足 {need} 项（时间窗外 {dw['expired']} 项）"
                if dw["valid"]
                else f"业绩均在时间窗外，有效 0 项（过期 {dw['expired']} 项）"
            )
            state = PARTIAL if dw["valid"] else UNSATISFIED
            evidence["layer5"] = {"state": state, "deadline_driven": True}
            return RequirementVerdict(state, block_level, detail, None, evidence)

    violated = [item for item in verdicts if item.state == "violated"]
    absent = [item for item in verdicts if item.state == "absent"]
    satisfied = [item for item in verdicts if item.state == "satisfied"]
    if violated:
        detail = "、".join(item.constraint.label for item in violated[:3])
        evidence["layer5"] = {"state": UNSATISFIED}
        return RequirementVerdict(
            UNSATISFIED, block_level,
            f"数值/等级/否定语义不符合要求：{detail}", None, evidence,
        )
    if absent:
        missing = "、".join(item.constraint.label for item in absent[:3])
        if satisfied:
            evidence["layer5"] = {"state": PARTIAL}
            return RequirementVerdict(
                PARTIAL, block_level, f"部分约束未在正文体现：{missing}", None, evidence,
            )
        evidence["layer5"] = {"state": UNSATISFIED, "no_response": True}
        return RequirementVerdict(
            UNSATISFIED, block_level, "未在正文找到明确响应", None, evidence,
        )
    if section is not None or deadline_driven_satisfied:
        evidence["layer5"] = {"state": SATISFIED}
        reason = (
            "业绩时间窗按招标截止日期核算，有效业绩满足数量要求"
            if deadline_driven_satisfied
            else "硬性约束均满足，证据充分"
        )
        return RequirementVerdict(SATISFIED, "", reason, section, evidence)

    unclear = [item for item in verdicts if item.state == "unclear"]
    if unclear:
        evidence["layer5"] = {"state": REVIEW, "reason": "单位不明确或跨量纲"}
        detail = "；".join(item.detail for item in unclear[:3])
        return RequirementVerdict(
            REVIEW, review_level(marker),
            f"数值单位缺失或跨量纲，无法自动判定：{detail}，请人工复核", None, evidence,
        )

    if best_ratio > 0 or anchor > 0 or time_signals:
        evidence["layer5"] = {"state": REVIEW}
        reasons = ["语义相近但证据不足"]
        if time_signals:
            reasons.append("时间范围短于要求")
        return RequirementVerdict(
            REVIEW, review_level(marker), "；".join(reasons) + "，建议人工复核", None, evidence,
        )
    evidence["layer5"] = {"state": UNSATISFIED, "no_response": True}
    return RequirementVerdict(
        UNSATISFIED, block_level, "未在正文找到明确响应", None, evidence,
    )


def _judge_atom(
    atom: dict,
    nodes: list[dict],
    combined: str,
    matcher: SemanticMatcher,
    req_type: str,
    marker: str,
    block_level: str,
    deadline,
    requirement_text: str = "",
) -> RequirementVerdict:
    """对单个原子独立执行**完整**判定（含业绩截止日期校验，R08）。

    与整条文本共用 :func:`_decide`，确保复合条款的每个原子都跑过硬约束 + 日期
    校验，不存在「拆成原子后绕过截止日期」的旁路。

    R16：``requirement_text`` 是**原始完整招标条款**。项目类型约束（「弱电智能化
    类似项目」等）属于整条要求，拆分后不能丢失——必须把它传给业绩日期校验，
    否则业绩原子会丢掉类型限定、把无关项目计为有效业绩。
    """

    text = atom.get("text", "")
    atom_verdicts = evaluate_constraints(text, combined)
    atom_windows = extract_time_windows(text)
    # R16：用原始条款判断项目类型/时间窗，而不是只靠拆分后的原子文本。
    atom_deadline = _deadline_window_signals(
        atom_windows, combined, deadline, requirement_text or text
    )
    atom_time_signals = _time_window_signals(atom_windows, combined)
    core = requirement_core(text)
    threshold = 0.45 if len(core) >= 8 else 1.0
    best = 0.0
    for node in nodes:
        body = node.get("body") or ""
        if body:
            best = max(best, matcher.coverage(text, body, req_type))
    anchor = max((anchor_coverage(text, n.get("body") or "", req_type) for n in nodes), default=0.0)
    section = find_covering_section(text, nodes, req_type)
    atom_evidence = {
        "deadline_window": atom_deadline,
        "time_signals": atom_time_signals,
        "constraints": [item.as_dict() for item in atom_verdicts],
    }
    verdict = _decide(
        text, atom_verdicts, atom_deadline, atom_time_signals, best, anchor,
        section, threshold, marker, block_level, atom_evidence,
    )
    # 「原子完全没被响应」以 missing 区分于「被明确违反」：前者在其它原子命中时
    # 聚合为 PARTIAL（部分满足），后者聚合为 UNSATISFIED；日期否决保留 UNSATISFIED。
    if verdict.state == UNSATISFIED and atom_evidence.get("layer5", {}).get("no_response"):
        return RequirementVerdict("missing", "", verdict.reason, None, verdict.evidence)
    return verdict


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

    R08：复合条款与单条文本共用 :func:`_decide`；每个原子独立跑完整判定（含
    截止日期），再按汇总原则合成整条结论，杜绝「拆原子绕过时间窗」。
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
    # R16：整条文本也用原始条款判断项目类型，保证单条与复合业绩口径一致。
    deadline_window = _deadline_window_signals(req_windows, combined, deadline, text)

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

    # R01/R08：复合条款先对每个原子跑**完整**判定（含截止日期），再汇总；
    # 拆分本身存疑时即便各原子看似命中也不自动判满足。
    if len(atoms) > 1:
        atom_verdicts = [
            _judge_atom(
                atom, nodes, combined, matcher, req_type, marker, block_level,
                deadline, requirement_text=text,
            )
            for atom in atoms
        ]
        atom_states = [verdict.state for verdict in atom_verdicts]
        evidence["layer2"]["atom_states"] = [
            {"atom_id": atom["atom_id"], "text": atom["text"], "state": state}
            for atom, state in zip(atoms, atom_states)
        ]
        # R08-8：汇总时保留原子级 deadline_window 证据，不丢失。
        evidence["layer5_detail"] = {
            "atom_deadline_windows": {
                atom["atom_id"]: verdict.evidence.get("deadline_window", {})
                for atom, verdict in zip(atoms, atom_verdicts)
            }
        }
        split_ambiguous = atom_split_is_ambiguous(atoms)
        # 汇总原则：任一不满足 → 不满足；部分缺失 → 部分满足；拆分存疑 → 复核；
        # 全部满足 → 满足。约束否决与日期否决优先于语义相似。
        if UNSATISFIED in atom_states:
            agg_state, agg_detail = UNSATISFIED, "复合要求中存在原子不满足"
        elif split_ambiguous:
            # R09：真正存在拆分歧义（转折/无法关联）→ 人工复核，优先于「部分满足」，
            # 因为此时「哪些原子成立」本身不可靠，不能给出 PARTIAL 这种具体结论。
            agg_state, agg_detail = REVIEW, "复合要求拆分存在歧义或关联不明确"
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

    # 单条文本：走与原子完全相同的统一判定。
    return _decide(
        text, verdicts, deadline_window, time_signals, best_ratio, anchor,
        section, threshold, marker, block_level, evidence,
    )


def verdict_from_status(response_status: str) -> bool:
    """人工已确认（responded/na）的条款跳过自动判定。"""

    return (response_status or "pending") != "pending"
