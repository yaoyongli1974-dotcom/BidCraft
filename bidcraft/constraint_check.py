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

import math
import re
from dataclasses import dataclass

# 阿拉伯数字与常见中文数字（「近三年」「六项」在招标文本里很常见）。
_NUM = r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十百]{1,3})"
# 单位表按「最长优先」排列，保证 MB/s 先于 MB、GHz 先于 Hz 之类不被截断。
_UNITS = (
    # 字节率先于存储：100MB/s 必须整体匹配为 byte_rate，不能被拆成 MB；
    # 存储容量（无 /s）与字节率是不同的量纲，绝不可互相比较。
    # 二进制容量（KiB/MiB/GiB/TiB，1024 进制）与十进制（KB/MB/GB/TB，1000 进制）
    # 分开登记，避免「1TB == 1TiB」这类错误换算。
    r"GB/s|MB/s|KB/s|B/s|TiB|GiB|MiB|KiB|TB|GB|MB|KB|B|Gbps|Mbps|Kbps|bps|GHz|MHz|KHz|Hz|"
    r"kV|kW|mm|cm|km|米|个月|项|个|份|例|台|套|路|条|家|人|"
    r"分钟|小时|天|day|min|年|月|m|s|V|W|A|%"
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
    # R02/R04：存储容量独立成族，避免「1000GB 存储」被归到带宽主体。
    "storage": ("存储", "容量", "硬盘", "磁盘", "内存"),
    "frequency": ("频率", "主频", "处理器", "CPU"),
}


def _cn_number(token: str) -> int | None:
    """把「三 / 六 / 十二 / 二十 / 一百」解析为整数；解析不了返回 None。"""

    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
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
    # 存储容量：基准 B（与带宽/频率/字节率都不同量纲）。
    # 注意 GB（存储）与 Gbps（带宽）、GB/s（字节率）三者互不可比。
    # 十进制容量（1000 进制）：1TB = 1000GB。
    "B": ("storage", 1.0),
    "KB": ("storage", 1000.0),
    "MB": ("storage", 1_000_000.0),
    "GB": ("storage", 1_000_000_000.0),
    "TB": ("storage", 1_000_000_000_000.0),
    # 二进制容量（1024 进制）：1TiB = 1024GiB，与十进制 GB/TB 不可混为等值。
    "KiB": ("storage", 1024.0),
    "MiB": ("storage", 1024.0**2),
    "GiB": ("storage", 1024.0**3),
    "TiB": ("storage", 1024.0**4),
    # 频率：基准 MHz（与带宽无关）
    "Hz": ("frequency", 1 / 1_000_000),
    "KHz": ("frequency", 1 / 1000),
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
    "s": ("duration", 1 / (30 * 24 * 3600)),
    "min": ("duration", 1 / (30 * 24 * 60)),
    "分钟": ("duration", 1 / (30 * 24 * 60)),
    "小时": ("duration", 1 / (30 * 24)),
    "天": ("duration", 1 / 30),
    "day": ("duration", 1 / 30),
    "月": ("duration", 1.0),
    "个月": ("duration", 1.0),
    "年": ("duration", 12.0),
    # 长度：基准 mm
    "mm": ("length", 1.0),
    "cm": ("length", 10.0),
    "m": ("length", 1000.0),
    "米": ("length", 1000.0),
    "km": ("length", 1_000_000.0),
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


# 单位关系的三种结果（T02）：明确可比的「兼容」、跨量纲的「不可兼容」、
# 单位缺失/无法识别的「未知」。调用方据此决定：只有 compatible 才做数值比较；
# incompatible / unknown 都转人工复核，且都不计入「不满足」硬伤。
UNIT_COMPATIBLE = "compatible"
UNIT_INCOMPATIBLE = "incompatible"
UNIT_UNKNOWN = "unknown"


def unit_relation(left: str, right: str) -> str:
    """返回两个单位的量纲关系：compatible / incompatible / unknown。

    - 任一侧单位缺失或未登记 → ``unknown``（裸写的「500」可能是台/处理器/带宽）；
    - 两边都登记但量纲不同 → ``incompatible``（Mbps vs MB/s、GB vs GHz 等）；
    - 两边都登记且量纲一致 → ``compatible``，此时才允许数值比较与换算。
    """

    left_info = _unit_info(left)
    right_info = _unit_info(right)
    if left_info is None or right_info is None:
        return UNIT_UNKNOWN
    return UNIT_COMPATIBLE if left_info[0] == right_info[0] else UNIT_INCOMPATIBLE


def _unit_compatible(left: str, right: str) -> bool:
    """兼容性的布尔视图：只有 ``unit_relation == compatible`` 才为 True。

    保留此函数供既有调用方使用；三态判断请用 :func:`unit_relation`。
    """

    return unit_relation(left, right) == UNIT_COMPATIBLE


@dataclass(frozen=True)
class Constraint:
    """从条款里抽出的一条可比较约束。

    R02：复合条款里每条约束**独立**持有自己的主体/指标定位字段，不再共享整条
    文本的单一 family。这样「近三年3项类似项目业绩，且带宽不低于1000Mbps」里
    业绩用 achievement、带宽用 bandwidth，各自只关联自己的证据。
    """

    kind: str           # "numeric" | "grade"
    comparator: str     # ">=" | "<=" | "grade>="
    threshold: float
    unit: str
    family: str         # 局部子句推断出的主体词族（R02）
    grade: str
    label: str          # 面向人的说明，写入 issue.evidence
    # R02 逐约束定位（可选，带默认值以兼容既有的位置参数构造）。
    metric: str = ""        # 指标名（数值/比较词，如「不小于」「至少」）
    subject: str = ""       # 主体对象（如「类似项目」「链路带宽」）
    object: str = ""        # 宾语/对象（同 subject，供前端展示）
    quantity: str = ""      # 阈值原文（如「3」「1000」）
    source_span: str = ""   # 该约束在条款原文中的完整片段，保证可追溯


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
# R04：方向正则直接复用完整单位注册表 _UNITS（最长优先），保证「已注册的单位
# 一定能被方向约束提取」——GB/TB/MB/s/GHz/Kbps 等不再出现「注册了却提取不到」。
_DIRECTION_PATTERNS = (
    (r"(?:不小于|不少于|不低于|大于等于|至少|≥)\s*(" + _NUM + r")\s*(" + _UNITS + r")?", ">="),
    (r"(?:不大于|不超过|小于等于|最多|≤)\s*(" + _NUM + r")\s*(" + _UNITS + r")?", "<="),
)


def extract_constraints(text: str) -> list[Constraint]:
    """从一条条款文本里抽出全部可比较约束；复合条款会返回多条。"""

    source = text or ""
    global_family = _infer_family(source)
    constraints: list[Constraint] = []

    def _subject_of(family: str, clause: str) -> str:
        for keyword in FAMILY_KEYWORDS.get(family, ()):
            if keyword in clause:
                return keyword
        return ""

    # R02：方向约束的主体取**局部子句**推断值，复合条款里业绩/带宽各归各族。
    for pattern, comparator in _DIRECTION_PATTERNS:
        for match in re.finditer(pattern, source):
            value = _cn_number(match.group(1))
            if value is None:
                continue
            unit = match.group(2) or ""
            local_family = _local_family(source, match.start(), global_family)
            clause = _local_window(source, match.start())
            label = f"{'至少' if comparator == '>=' else '至多'} {match.group(1)}{unit}"
            constraints.append(
                Constraint("numeric", comparator, float(value), unit, local_family, "", label,
                           metric=f"{'≥' if comparator == '>=' else '≤'}",
                           subject=_subject_of(local_family, clause), object=clause.strip()[:24],
                           quantity=match.group(1), source_span=_source_span(source, match.start(), len(match.group(0))))
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
                           f"质保不少于 {match.group(1)}{match.group(2)}",
                           metric="质保", subject=_subject_of("warranty", source), object="质保",
                           quantity=match.group(1), source_span=_source_span(source, match.start(), len(match.group(0))))
            )

    # N 小时内响应 / 响应时间不超过 N 小时 = 至多 N 小时（越短越好）。
    for match in re.finditer(
        r"(" + _NUM + r")\s*小时\s*(?:内|以内)?[^，。；]{0,6}(?:响应|到场|到达|回复)", source
    ):
        value = _cn_number(match.group(1))
        if value is not None:
            constraints.append(
                Constraint("numeric", "<=", float(value), "小时", "response", "",
                           f"响应不超过 {match.group(1)} 小时", metric="响应时间", subject="服务响应",
                           object="响应", quantity=match.group(1),
                           source_span=_source_span(source, match.start(), len(match.group(0))))
            )
    for match in re.finditer(
        r"(?:响应|到场|到达)\s*(?:时间)?\s*(?:不超过|小于等于|≤)?\s*(" + _NUM + r")\s*小时", source
    ):
        value = _cn_number(match.group(1))
        if value is not None:
            constraints.append(
                Constraint("numeric", "<=", float(value), "小时", "response", "",
                           f"响应不超过 {match.group(1)} 小时", metric="响应时间", subject="服务响应",
                           object="响应", quantity=match.group(1),
                           source_span=_source_span(source, match.start(), len(match.group(0))))
            )

    # 资质等级：一级 >= 二级；只比较等级序，不做跨量纲（甲级/一级）比较。
    for match in _GRADE_RE.finditer(source):
        grade = match.group()
        rank = _grade_rank(grade)
        if rank is None:
            continue
        constraints.append(
            Constraint("grade", "grade>=", float(rank[0]), "", "qualification", grade,
                       f"资质等级不低于 {grade}", metric="资质等级", subject="资质", object="资质",
                       quantity=grade, source_span=grade)
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


# R11：连接词也是约束边界，不能只按标点切。无标点的复合条款
# 「近三年三项类似项目业绩且网络带宽不低于1000Mbps」里，带宽约束的局部
# 范围应从「且」之前截断，否则业绩词族会污染带宽约束。
_LOCAL_CONNECTOR = re.compile(r"并提供|并满足|并且|同时|以及|还须|均应|分别|其中|须同时|并|且|但须|但|另外|并且")


def _local_window(source: str, pos: int) -> str:
    """返回 pos 所在的局部上下文：以标点或连接词为边界（R11）。"""

    start, end = _clause_bounds(source, pos)  # 标点边界
    segment = source[start:end]
    rel = pos - start
    lo, hi = 0, len(segment)
    for match in _LOCAL_CONNECTOR.finditer(segment):
        if match.end() <= rel:
            lo = max(lo, match.end())
        elif match.start() >= rel:
            hi = min(hi, match.start())
    return segment[lo:hi]


def _local_family(source: str, pos: int, fallback: str) -> str:
    """R02/R11：用约束所在的**局部上下文**推断主体词族，而非整条文本。

    局部边界同时考虑标点与连接词（R11），复合条款里业绩与带宽共存时各自归族；
    取不到词族时才退回调用方给的 fallback。
    """

    family = _infer_family(_local_window(source, pos))
    return family or fallback


def _source_span(source: str, pos: int, length: int) -> str:
    return source[pos : pos + length].strip()


def _family_matches(constraint: Constraint, clause: str) -> bool:
    if not constraint.family:
        return True
    keywords = FAMILY_KEYWORDS.get(constraint.family, ())
    return any(keyword in clause for keyword in keywords)


# R11：主备链路 / 主备设备对象限定词——候选必须带同一个限定词才算关联。
_OBJECT_QUALIFIERS = ("主链路", "备链路", "主机", "备机", "主用", "备用", "主设备", "备设备")


def _object_qualifier(text: str) -> str:
    for token in _OBJECT_QUALIFIERS:
        if token in (text or ""):
            return token
    return ""


def _association_score(clause: str, pos: int, family: str, object_hint: str = "") -> int:
    """候选数值与约束主体（指标名 / 对象名）的上下文关联强度。

    T04/R11：不能只取第一个数值，也不能简单取最大值。一个候选若与约束讲的是同一
    件事（同一主体词族 + 对象限定词出现在附近上下文），关联更强。用这个分数在
    多个候选中做归因，而不是靠顺序或数值大小。
    """

    keywords = FAMILY_KEYWORDS.get(family, ())
    if not keywords:
        return 0
    lo, hi = max(0, pos - 16), min(len(clause), pos + 16)
    window = clause[lo:hi]
    score = sum(1 for keyword in keywords if keyword in window)
    needed = _object_qualifier(object_hint)
    if needed:
        score += 10 if needed in window else -10
    return score


# R13：浮点换算后的数值比较容差。换算系数含 1/1000、1024**4 等，直接比较会
# 遇到二进制浮点误差；相对容差 1e-9 足以吸收换算误差，又不会把真正不同的值判等。
_FLOAT_REL_TOL = 1e-9
_FLOAT_ABS_TOL = 1e-9


def _cmp_with_tolerance(value: float, threshold: float, comparator: str) -> bool:
    """带容差的数值比较：边界（相等）视为满足，明确的大小差异才分出胜负。"""

    if math.isclose(value, threshold, rel_tol=_FLOAT_REL_TOL, abs_tol=_FLOAT_ABS_TOL):
        return True
    return value >= threshold if comparator == ">=" else value <= threshold


def _evaluate_one(constraint: Constraint, clauses: list[str]) -> ConstraintVerdict:
    """把一条约束拿到正文各子句里核对，综合全部候选后给出判定。

    T04 候选关联流程：
    1. 只在主体词族匹配的子句里找值（避免「500 台设备」答「1000Mbps 带宽」）；
    2. 收集**所有**同量纲候选，各自比较得出 satisfied/violated，并算主体关联分；
    3. 结论一致 → 采纳；结论冲突 → 取关联分更高的一组；仍冲突 → ``unclear`` 转人工；
    4. 数值缺失 / 跨量纲 → 收集为歧义，转人工复核，绝不套用约束单位去比较。
    """

    ambiguous: list[str] = []
    # 每个候选：(state, 原文子句, 关联分, 是否被否定)
    candidates: list[tuple[str, str, int, bool]] = []

    for clause in clauses:
        if not _family_matches(constraint, clause):
            continue

        if constraint.kind == "grade":
            for hit in _GRADE_RE.finditer(clause):
                grade = hit.group()
                rank = _grade_rank(grade)
                if rank is None or rank[1] != _grade_rank(constraint.grade)[1]:
                    continue  # 不同量纲（甲级 vs 一级）无法比较，跳过
                negated = negation_before(clause, hit.start())
                state = "satisfied" if rank[0] <= int(constraint.threshold) else "violated"
                candidates.append(
                    (state, clause.strip(), _association_score(clause, hit.start(), constraint.family, constraint.object), negated)
                )
            continue

        # 「近三年」是时间窗描述，不当作业绩数量——先剔除，再按量纲筛。
        hits = list(_VALUE_RE.finditer(clause))
        usable = [hit for hit in hits if not (hit.start() > 0 and clause[hit.start() - 1] == "近")]
        for hit in usable:
            value = _cn_number(hit.group(1))
            if value is None:
                continue
            relation = unit_relation(hit.group(2) or "", constraint.unit)
            if relation != UNIT_COMPATIBLE:
                # 单位缺失（unknown）或跨量纲（incompatible）都不足以比较。
                ambiguous.append(f"{value}{hit.group(2) or '（无单位）'}")
                continue
            negated = negation_before(clause, hit.start())
            base = _to_base(float(value), hit.group(2) or "")
            threshold = _to_base(constraint.threshold, constraint.unit)
            # R13：浮点换算（1TB=1000GB 等）可能产生 0.30000000000000004 之类误差，
            # 用相对容差比较，避免边界数值被误判。
            ok = _cmp_with_tolerance(base, threshold, constraint.comparator)
            state = "satisfied" if ok else "violated"
            candidates.append(
                (state, clause.strip(), _association_score(clause, hit.start(), constraint.family, constraint.object), negated)
            )

    if candidates:
        # 否定优先：任一候选被明确否定，按「否定语义」原则判违反（保守）。
        if any(item[3] for item in candidates):
            return ConstraintVerdict(constraint, "violated", next(c[1] for c in candidates if c[3]))
        states = {item[0] for item in candidates}
        if len(states) == 1:
            return ConstraintVerdict(constraint, candidates[0][0], candidates[0][1])
        # 结论冲突：用主体关联分归因。分高的一组若结论一致则采纳，否则转人工。
        best_score = max(item[2] for item in candidates)
        top_states = {item[0] for item in candidates if item[2] == best_score}
        if len(top_states) == 1:
            chosen = next(item for item in candidates if item[2] == best_score)
            return ConstraintVerdict(constraint, chosen[0], chosen[1])
        detail = "；".join(f"{item[0]}:{item[1]}" for item in candidates[:3])
        return ConstraintVerdict(constraint, "unclear", f"多个候选数值与约束关联不明确：{detail}")

    if ambiguous:
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


def unit_registry_issues() -> list[str]:
    """R13：注册表完整性自检——列出「已登记但方向正则无法提取」的单位。

    返回空列表表示每个已登记单位都能被 _VALUE_RE / _DIRECTION_PATTERNS 正确识别。
    供测试与 CI 校验，防止「注册了却提取不到」（v0.21.0 R04 遗留风险）。
    """

    issues: list[str] = []
    for unit in _UNIT_DIMENSIONS:
        probe = f"指标不低于 1{unit}"
        extracted = extract_constraints(probe)
        labs = " ".join(item.label for item in extracted)
        if unit not in labs:
            issues.append(unit)
    return issues
