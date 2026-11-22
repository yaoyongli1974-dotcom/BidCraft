"""实质性条款的**约束条件比较**（v0.18.0）。

背景
----
v0.16.0 的锚点判定只回答「条款和正文是否都出现过相同模式」，不回答
「正文给出的值是否满足条款要求」。于是这些场景会被误判为已响应：

- 要求「不小于 1000 Mbps」，正文写「500 Mbps」——模式都在，数值更小；
- 要求「一级资质」，正文写「二级资质」——等级更低；
- 要求「具备一级建造师资格」，正文写「不具备一级建造师资格」——语义相反；
- 复合条款拆成三条约束，正文只满足其中两条——仍被当成整条覆盖。

本模块把可量化的要求升级为**约束**：识别数值方向（>= / <=）、资质等级序、
否定语义，并把一条复合条款拆成多条独立约束，逐条比较。只做规则比较，
不引入 LLM——判定要能复核、能举证、对同一份正文结果稳定。

刻意不做的两件事
----------------
1. **「近 N 年」时间窗不当约束比较**。它是描述性锚点：「近三年业绩」的正文写
   「近五年 6 项」按既有口径仍算覆盖（见 tests/test_substantive_types.py）。
   把它升级成 <=3 年会推翻既有正确判定，收益却只是理论上更严。
2. **不做公司名/产品名等开放域的语义等价**。只处理能被客观比较的量化约束；
   其余部分继续交给 substantive.py 的 3-gram / 锚点基线。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 阿拉伯数字与常见中文数字（「近三年」「六项」在招标文本里很常见）。
_NUM = r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百]{1,3})"
_VALUE_RE = re.compile(rf"({_NUM})\s*(Gbps|Mbps|MB|GB|项|个|份|例|年|月|小时|天|分钟|mm|cm|米|kV|kW|%)?")

# 句子切分：逗号也要切，否则「具备 X，但不具备 Y」会连成一句误判。
_CLAUSE_SPLIT = re.compile(r"[，,。；;、\n！!？?：:]")

# 否定词；比较级的「不低于 / 不小于 / 不超过」不是否定，先剔除。
_NEGATIONS = ("不具备", "不满足", "不符合", "不提供", "不承担", "不保证",
              "不能", "不会", "无法", "未能", "没有", "并非", "拒绝",
              "不", "未", "无", "非")
_NOT_NEGATION = re.compile(r"不(低于|小于|大于|超过|少于|限于|晚于|早于)")

# 资质等级序（同一量纲内从小到大排列，index 越小等级越高）。
_GRADE_SCALES = (
    ("特级", "一级", "二级", "三级"),
    ("甲级", "乙级", "丙级"),
)
_GRADE_RE = re.compile(r"特级|一级|二级|三级|甲级|乙级|丙级")

# 主体词族：正文里的数值/等级必须与条款讲的是同一件事才算数。
FAMILY_KEYWORDS = {
    # 「项目」是宽泛词，但业绩约束必须靠它才能识别「相近项目 / 类似项目」这类改写。
    # 不会误伤「42 个机柜」：那个子句里既没有「项目」也没有「业绩」。
    "achievement": ("业绩", "案例", "同类项目", "类似项目", "相近项目", "项目", "合同", "中标"),
    "qualification": ("资质", "资格", "建造师", "证书", "执照", "认证", "等级"),
    "response": ("响应", "到场", "到达", "回复", "服务"),
    "warranty": ("质保", "保修", "质量保证", "免费"),
    "bandwidth": ("带宽", "速率", "吞吐", "链路", "传输"),
}


def _cn_number(token: str) -> int | None:
    """把「三 / 六 / 十二 / 二十 / 一百」解析为整数；解析不了返回 None。"""

    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    token = token.strip()
    if token.isdigit():
        return int(token)
    if token in digits:
        return digits[token]
    if token == "十":
        return 10
    if "百" in token:
        head, _, tail = token.partition("百")
        hundreds = _cn_number(head) if head else 1
        rest = _cn_number(tail) if tail else 0
        if hundreds is None or rest is None:
            return None
        return hundreds * 100 + rest
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = _cn_number(head) if head else 1
        ones = _cn_number(tail) if tail else 0
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return None


def _grade_rank(grade: str) -> tuple[int, int] | None:
    for scale in _GRADE_SCALES:
        if grade in scale:
            return (scale.index(grade), len(scale))
    return None


def _infer_family(text: str) -> str:
    for family, keywords in FAMILY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return family
    return ""


def negation_before(clause: str, pos: int) -> bool:
    """判断 clause[:pos] 里是否含否定词（比较级表达除外）。"""

    segment = _NOT_NEGATION.sub("", clause[:pos])
    return any(word in segment for word in _NEGATIONS)


def _clause_bounds(body: str, pos: int) -> tuple[int, int]:
    start = 0
    end = len(body)
    for match in _CLAUSE_SPLIT.finditer(body):
        if match.end() <= pos:
            start = match.end()
        elif match.start() >= pos:
            end = match.start()
            break
    return start, end


def clause_at(body: str, pos: int) -> str:
    """返回 pos 所在的子句，供否定判定与举证使用。"""

    start, end = _clause_bounds(body, pos)
    return body[start:end]


def negation_at(body: str, pos: int) -> bool:
    """判断 body 中 pos 之前（同一子句内）是否含否定词。"""

    start, _ = _clause_bounds(body, pos)
    return negation_before(body[start:pos], pos - start)


def _to_base(value: float, unit: str) -> float:
    """归一到可比较的数值：带宽统一到 Mbps，年限统一到月。"""

    unit = (unit or "").strip()
    if unit == "Gbps":
        return value * 1000
    if unit == "年":
        return value * 12
    return value


# 同量纲单位（用于挑选正文里与约束同一维度的数值，避免拿「近三年」的
# 「三」去应答「不少于3项业绩」这种串味匹配）。
_UNIT_GROUPS = (
    {"项", "个", "份", "例"},
    {"年", "月", "小时", "天", "分钟"},
    {"Mbps", "Gbps"},
)


def _unit_compatible(left: str, right: str) -> bool:
    if not left or not right:
        return True
    if left == right:
        return True
    return any(left in group and right in group for group in _UNIT_GROUPS)


@dataclass(frozen=True)
class Constraint:
    """从条款里抽出的一条可比较约束。"""

    kind: str           # "numeric" | "grade"
    comparator: str     # ">=" | "<=" | "grade>="
    threshold: float
    unit: str
    family: str
    grade: str
    label: str          # 面向人的说明，写入 issue.evidence


@dataclass(frozen=True)
class ConstraintVerdict:
    """一条约束对某段正文的判定结果。

    state:
      - "satisfied" 正文给出满足该约束的值；
      - "violated"  正文给出了反向的值，或肯定断言被否定词推翻；
      - "absent"    正文根本没提这件事（无法证实，按保守口径不算覆盖）。
    """

    constraint: Constraint
    state: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {
            "label": self.constraint.label,
            "state": self.state,
            "detail": self.detail[:120],
        }


# 方向比较：要求 >= / <= 某个数。单位留空表示由数值上下文推断。
_DIRECTION_PATTERNS = (
    (r"(?:不小于|不少于|不低于|大于等于|至少|≥)\s*(" + _NUM + r")\s*(项|个|份|例|年|月|小时|天|分钟|Mbps|Gbps|mm|cm|米|kV|kW|%)?", ">="),
    (r"(?:不大于|不超过|小于等于|最多|≤)\s*(" + _NUM + r")\s*(项|个|份|例|年|月|小时|天|分钟|Mbps|Gbps|mm|cm|米|kV|kW|%)?", "<="),
)


def extract_constraints(text: str) -> list[Constraint]:
    """从一条条款文本里抽出全部可比较约束；复合条款会返回多条。"""

    source = text or ""
    family = _infer_family(source)
    constraints: list[Constraint] = []

    for pattern, comparator in _DIRECTION_PATTERNS:
        for match in re.finditer(pattern, source):
            value = _cn_number(match.group(1))
            if value is None:
                continue
            unit = match.group(2) or ""
            label = f"{'至少' if comparator == '>=' else '至多'} {match.group(1)}{unit}"
            constraints.append(
                Constraint("numeric", comparator, float(value), unit, family, "", label)
            )

    # 质保 N 年 = 至少 N 年（越长越好）。「质保5年」与「5年免费质保」都要认。
    _warranty_patterns = (
        r"(?:质保|保修|质量保证|免费保修)\s*(" + _NUM + r")\s*(年|月)",
        r"(" + _NUM + r")\s*(年|月)\s*(?:免费)?\s*(?:质保|保修|质量保证)",
    )
    for pattern in _warranty_patterns:
        for match in re.finditer(pattern, source):
            value = _cn_number(match.group(1))
            if value is None:
                continue
            constraints.append(
                Constraint("numeric", ">=", float(value), match.group(2), "warranty", "",
                           f"质保不少于 {match.group(1)}{match.group(2)}")
            )

    # N 小时内响应 / 响应时间不超过 N 小时 = 至多 N 小时（越短越好）。
    for match in re.finditer(
        r"(" + _NUM + r")\s*小时\s*(?:内|以内)?[^，。；]{0,6}(?:响应|到场|到达|回复)", source
    ):
        value = _cn_number(match.group(1))
        if value is not None:
            constraints.append(
                Constraint("numeric", "<=", float(value), "小时", "response", "",
                           f"响应不超过 {match.group(1)} 小时")
            )
    for match in re.finditer(
        r"(?:响应|到场|到达)\s*(?:时间)?\s*(?:不超过|小于等于|≤)?\s*(" + _NUM + r")\s*小时", source
    ):
        value = _cn_number(match.group(1))
        if value is not None:
            constraints.append(
                Constraint("numeric", "<=", float(value), "小时", "response", "",
                           f"响应不超过 {match.group(1)} 小时")
            )

    # 资质等级：一级 >= 二级；只比较等级序，不做跨量纲（甲级/一级）比较。
    for match in _GRADE_RE.finditer(source):
        grade = match.group()
        rank = _grade_rank(grade)
        if rank is None:
            continue
        constraints.append(
            Constraint("grade", "grade>=", float(rank[0]), "", "qualification", grade,
                       f"资质等级不低于 {grade}")
        )

    # 同一比较被多个模式命中时去重（标签相同视为同一条）。
    seen: set[str] = set()
    unique: list[Constraint] = []
    for item in constraints:
        if item.label in seen:
            continue
        seen.add(item.label)
        unique.append(item)
    return unique


def _family_matches(constraint: Constraint, clause: str) -> bool:
    if not constraint.family:
        return True
    keywords = FAMILY_KEYWORDS.get(constraint.family, ())
    return any(keyword in clause for keyword in keywords)


def _evaluate_one(constraint: Constraint, clauses: list[str]) -> ConstraintVerdict:
    for clause in clauses:
        if not _family_matches(constraint, clause):
            continue

        if constraint.kind == "grade":
            hit = _GRADE_RE.search(clause)
            if hit is None:
                continue
            grade = hit.group()
            rank = _grade_rank(grade)
            if rank is None or rank[1] != _grade_rank(constraint.grade)[1]:
                continue  # 不同量纲（甲级 vs 一级）无法比较，跳过
            if negation_before(clause, hit.start()):
                return ConstraintVerdict(constraint, "violated", clause.strip())
            state = "satisfied" if rank[0] <= int(constraint.threshold) else "violated"
            return ConstraintVerdict(constraint, state, clause.strip())

        # 数值：挑与约束同量纲的值做比较。
        # 「近三年」是时间窗描述，不当作业绩数量——先按量纲分组筛，再回退。
        hits = list(_VALUE_RE.finditer(clause))
        usable = [hit for hit in hits if not (hit.start() > 0 and clause[hit.start() - 1] == "近")]
        ordered = [hit for hit in usable if _unit_compatible(hit.group(2) or "", constraint.unit)] or usable
        for hit in ordered:
            value = _cn_number(hit.group(1))
            if value is None:
                continue
            if negation_before(clause, hit.start()):
                return ConstraintVerdict(constraint, "violated", clause.strip())
            unit = hit.group(2) or constraint.unit
            base = _to_base(float(value), unit)
            threshold = _to_base(constraint.threshold, constraint.unit)
            ok = base >= threshold if constraint.comparator == ">=" else base <= threshold
            return ConstraintVerdict(constraint, "satisfied" if ok else "violated", clause.strip())

    return ConstraintVerdict(constraint, "absent", "")


def evaluate_constraints(text: str, body: str) -> list[ConstraintVerdict]:
    """把条款的每条约束拿到正文里核对，返回逐条判定（供调用方举证）。"""

    constraints = extract_constraints(text)
    if not constraints or not body:
        return []
    clauses = [clause.strip() for clause in _CLAUSE_SPLIT.split(body) if clause.strip()]
    return [_evaluate_one(item, clauses) for item in constraints]


def constraint_blocked(verdicts: list[ConstraintVerdict]) -> bool:
    """是否存在硬伤：任一约束被违反，或存在量化约束但没被证实。"""

    return any(
        verdict.state == "violated"
        or (verdict.constraint.kind in ("numeric", "grade") and verdict.state == "absent")
        for verdict in verdicts
    )
