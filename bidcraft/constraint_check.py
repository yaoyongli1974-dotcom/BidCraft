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
# 单位表按「最长优先」排列，保证 MB/s 先于 MB、GHz 先于 Hz 之类不被截断。
_UNITS = (
    r"GB/s|MB/s|KB/s|B/s|Gbps|Mbps|Kbps|bps|GHz|MHz|Hz|"
    r"kV|kW|mm|cm|米|项|个|份|例|台|套|路|条|家|人|"
    r"年|月|小时|天|分钟|V|W|%"
)
_VALUE_RE = re.compile(rf"({_NUM})\s*({_UNITS})?")

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


# 单位 → (量纲, 归一到该量纲基准的转换系数)。
# 跨量纲**禁止比较**：带宽(Mbps) 与字节率(MB/s)、带宽(Mbps) 与频率(MHz)
# 不是一回事，正文写「500MHz 处理器」不能拿去应答「带宽不小于1000Mbps」。
# 换算关系从这里一处声明，不在比较逻辑里散落。
_UNIT_DIMENSIONS: dict[str, tuple[str, float]] = {
    # 带宽：基准 Mbps
    "bps": ("bandwidth", 1 / 1_000_000),
    "Kbps": ("bandwidth", 1 / 1000),
    "Mbps": ("bandwidth", 1.0),
    "Gbps": ("bandwidth", 1000.0),
    # 字节率：基准 MB/s（与 bandwidth 不同量纲，8 bit = 1 byte 也不自动换算）
    "B/s": ("byte_rate", 1 / 1_000_000),
    "KB/s": ("byte_rate", 1 / 1000),
    "MB/s": ("byte_rate", 1.0),
    "GB/s": ("byte_rate", 1000.0),
    # 频率：基准 MHz（与带宽无关）
    "Hz": ("frequency", 1 / 1_000_000),
    "MHz": ("frequency", 1.0),
    "GHz": ("frequency", 1000.0),
    # 计数：基准「个/项」
    "项": ("count", 1.0),
    "个": ("count", 1.0),
    "份": ("count", 1.0),
    "例": ("count", 1.0),
    "台": ("count", 1.0),
    "套": ("count", 1.0),
    "路": ("count", 1.0),
    "条": ("count", 1.0),
    "家": ("count", 1.0),
    "人": ("count", 1.0),
    # 时长：基准「月」（年=12月，质保5年 == 质保60个月）
    "分钟": ("duration", 1 / (30 * 24 * 60)),
    "小时": ("duration", 1 / (30 * 24)),
    "天": ("duration", 1 / 30),
    "月": ("duration", 1.0),
    "年": ("duration", 12.0),
    # 长度：基准 mm
    "mm": ("length", 1.0),
    "cm": ("length", 10.0),
    "米": ("length", 1000.0),
    # 电学：各自独立量纲
    "V": ("voltage", 1.0),
    "kV": ("voltage", 1000.0),
    "W": ("power", 1.0),
    "kW": ("power", 1000.0),
    "A": ("current", 1.0),
    "%": ("percent", 1.0),
}

# 无单位数值（如「500 台设备」在带宽约束里）绝不能套用约束的单位。
# 返回 (维度, 系数)；单位未登记时视为「不明确」而非可比较。
def _unit_info(unit: str) -> tuple[str, float] | None:
    unit = (unit or "").strip()
    return _UNIT_DIMENSIONS.get(unit)


def unit_dimension(unit: str) -> str:
    """公开查询单位所属量纲（未登记返回空串），供测试与外部判定使用。"""

    info = _unit_info(unit)
    return info[0] if info else ""


def _to_base(value: float, unit: str) -> float:
    """归一到该量纲基准；未登记单位返回原值（调用方须先确认量纲一致）。"""

    info = _unit_info(unit)
    return value * info[1] if info else value


def _unit_compatible(left: str, right: str) -> bool:
    """只有**两边单位都明确且量纲一致**才认为可比较。

    任一侧缺失 → False：正文里裸写的「500」可能是 500 台设备、500MHz 处理器，
    不能因为约束单位是 Mbps 就把它当成 500Mbps。单位不明确交给调用方转人工复核。
    """

    left_info = _unit_info(left)
    right_info = _unit_info(right)
    if left_info is None or right_info is None:
        return False
    return left_info[0] == right_info[0]


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
      - "absent"    正文根本没提这件事（无法证实，按保守口径不算覆盖）；
      - "unclear"   正文有数值，但单位缺失或跨量纲，无法安全比较 → 转人工复核。
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
    ambiguous: list[str] = []
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

        # 数值：只拿与约束**量纲一致**的值做比较（单位缺失 / 跨量纲一律不比）。
        # 「近三年」是时间窗描述，不当作业绩数量——先剔除，再按量纲筛。
        hits = list(_VALUE_RE.finditer(clause))
        usable = [hit for hit in hits if not (hit.start() > 0 and clause[hit.start() - 1] == "近")]
        compatible = [
            hit for hit in usable if _unit_compatible(hit.group(2) or "", constraint.unit)
        ]
        for hit in compatible:
            value = _cn_number(hit.group(1))
            if value is None:
                continue
            if negation_before(clause, hit.start()):
                return ConstraintVerdict(constraint, "violated", clause.strip())
            base = _to_base(float(value), hit.group(2) or "")
            threshold = _to_base(constraint.threshold, constraint.unit)
            ok = base >= threshold if constraint.comparator == ">=" else base <= threshold
            return ConstraintVerdict(constraint, "satisfied" if ok else "violated", clause.strip())

        # 正文有数值，但单位缺失或与约束不同量纲：不足以判定，记下来转人工复核。
        # 关键：这些值绝不拿来和约束阈值比较（避免「500台设备」被当成 500Mbps）。
        for hit in usable:
            value = _cn_number(hit.group(1))
            if value is None:
                continue
            ambiguous.append(f"{value}{hit.group(2) or '（无单位）'}")

    if ambiguous:
        # 兼容的数值已在上面 return；走到这里说明没有任何可比数值，只有歧义项。
        return ConstraintVerdict(
            constraint, "unclear", f"数值单位缺失或与约束量纲不同：{'、'.join(ambiguous[:3])}"
        )
    return ConstraintVerdict(constraint, "absent", "")


def evaluate_constraints(text: str, body: str) -> list[ConstraintVerdict]:
    """把条款的每条约束拿到正文里核对，返回逐条判定（供调用方举证）。"""

    constraints = extract_constraints(text)
    if not constraints or not body:
        return []
    clauses = [clause.strip() for clause in _CLAUSE_SPLIT.split(body) if clause.strip()]
    return [_evaluate_one(item, clauses) for item in constraints]


def constraint_blocked(verdicts: list[ConstraintVerdict]) -> bool:
    """是否存在硬伤：任一约束被违反，或存在量化约束但没被证实。

    ``unclear``（单位缺失/跨量纲）刻意**不算**硬伤——它不该被当成「不满足」
    直接拦死，而是让上层（verdict）走人工复核；但它同样不能算「已证实满足」，
    所以调用方（coverage_ratio / find_covering_section）不会因它放行。
    """

    return any(
        verdict.state == "violated"
        or (verdict.constraint.kind in ("numeric", "grade") and verdict.state == "absent")
        for verdict in verdicts
    )
