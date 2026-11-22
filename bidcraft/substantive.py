"""实质性条款 → 正文响应的覆盖判定。

背景：评分办法里的「实质性要求」过去只被折叠成 ``mandatory`` 布尔，
核对时最多检查「有没有一个含『废标』的章节」，无法回答「这一条到底响应了没有」。
这里用可解释的关键词覆盖做**保守**判定：宁可判为未响应（人工可标「已响应 / 不适用」），
也不把明显没写的条款算成已响应。

不做 LLM 判定：条款响应是否成立要能复核、能举证，关键词覆盖虽然笨，但同一份正文
每次的结果都一样，处置留痕也才站得住。
"""

from __future__ import annotations

import re

from .constraint_check import (
    constraint_blocked,
    evaluate_constraints,
    negation_at,
)
from .schemas import CheckResult

# ★ / ▲ 是招标文件里最常见的「实质性条款」标记。
MARKERS = ("★", "▲")

# 切词用的标点与连接词；中文没有空格，只能靠这些边界切出候选词。
_SPLIT = re.compile(r"[的|和|与|及|、，,；;：:（）()【】\[\]《》<>“”\"'「」/]+")
_QUOTE = re.compile(r"[「“\"]([^」”\"]{2,40})[」”\"]")
# 行首编号、项目符号与标记本身不参与比较。
_LEAD_NOISE = re.compile(r"^[\s\d、.．)）(（一二三四五六七八九十\-*•●◆■★▲]+")

# 这些词几乎出现在任何一份标书里，拿它们判断「已响应」等于没判断。
STOPWORDS = {
    "投标人",
    "招标人",
    "投标",
    "招标",
    "本项",
    "本次",
    "项目",
    "要求",
    "应当",
    "必须",
    "不得",
    "提供",
    "具有",
    "相关",
    "以上",
    "以下",
    "以及",
    "进行",
    "按照",
    "符合",
    "满足",
    "有效",
    "文件",
    "内容",
    "情况",
    "其他",
    "不得低于",
}


def marker_of(text: str) -> str:
    """取条款文本里的实质性标记（★ 优先于 ▲）。"""

    for marker in MARKERS:
        if marker in text:
            return marker
    return ""


def clean_text(text: str) -> str:
    return _LEAD_NOISE.sub("", (text or "").strip()).strip()


def requirement_core(text: str) -> str:
    """去掉编号/标点后的核心短语，用于整句包含判定。"""

    cleaned = _SPLIT.sub("", clean_text(text))
    cleaned = re.sub(r"[\s　]+", "", cleaned)
    return cleaned


def requirement_keywords(text: str) -> list[str]:
    """从条款里切出可比较的关键词（去重保序）。

    规则刻意简单：引号内的词组优先，其次按连接词切出的 2 字以上片段，最后补上
    核心短语前 12 字。调用方拿覆盖率判断，而不是要求逐字命中。
    """

    source = clean_text(text)
    keywords: list[str] = []

    def push(value: str) -> None:
        value = value.strip()
        if len(value) < 2 or value in STOPWORDS or value in keywords:
            return
        keywords.append(value)

    for quoted in _QUOTE.findall(source):
        push(quoted)
    for chunk in _SPLIT.split(source):
        chunk = chunk.strip()
        # 单个汉字区分度太低；超过 20 字的片段正文几乎不可能逐字重复。
        if 2 <= len(chunk) <= 20:
            push(chunk)
    core = requirement_core(source)
    if 4 <= len(core) <= 40:
        push(core)
    elif len(core) > 40:
        push(core[:20])
    return keywords[:12]


# ---------------------------------------------------------- 条款类型结构化判定
# 3-gram 对「改写过但意思到了」的响应偏保守：数值换了、同义词换了就判未响应。
# 这里为可识别的条款类型补一条结构化锚点命中路径——锚点是「条款和正文都出现的
# 模式」（如「近N年」「数字+单位」「资质等级」），比精确字符串更能容忍改写，
# 但只统计条款自身也有的模式，避免正文随便写个数字就算响应。
REQ_TYPE_QUALIFICATION = "qualification"  # 资质 / 资格 / 证书
REQ_TYPE_ACHIEVEMENT = "achievement"      # 业绩 / 案例 / 合同
REQ_TYPE_PARAMETER = "parameter"          # 技术参数 / 阈值 / 规格
REQ_TYPE_COMMITMENT = "commitment"        # 承诺 / 服务 / 质保
REQ_TYPE_GENERAL = "general"              # 无法归类，回退到 3-gram

REQUIREMENT_TYPES = (
    REQ_TYPE_QUALIFICATION,
    REQ_TYPE_ACHIEVEMENT,
    REQ_TYPE_PARAMETER,
    REQ_TYPE_COMMITMENT,
    REQ_TYPE_GENERAL,
)
REQ_TYPE_LABELS = {
    REQ_TYPE_QUALIFICATION: "资质资格",
    REQ_TYPE_ACHIEVEMENT: "业绩案例",
    REQ_TYPE_PARAMETER: "技术参数",
    REQ_TYPE_COMMITMENT: "承诺服务",
    REQ_TYPE_GENERAL: "通用条款",
}

# 类型信号词：条款文本命中任一组才归类；刻意要求具体词，避免「要求/提供」这类泛词。
_TYPE_SIGNALS: dict[str, tuple[str, ...]] = {
    REQ_TYPE_QUALIFICATION: (
        "资质", "资格", "证书", "执照", "许可证", "认证", "等级", "甲级", "乙级",
        "一级", "二级", "特级", "ISO", "CMMI", "ISO9001", "执业", "注册",
    ),
    REQ_TYPE_ACHIEVEMENT: (
        "业绩", "案例", "类似项目", "同类项目", "已完成", "承担过", "合同", "中标",
    ),
    REQ_TYPE_PARAMETER: (
        "参数", "规格", "型号", "技术指标", "技术参数", "不低于", "不小于", "不大于",
        "大于等于", "小于等于", "毫米", "千伏", "千瓦", "分辨率", "带宽", "容量",
    ),
    REQ_TYPE_COMMITMENT: (
        "承诺", "保证", "免费", "质保", "售后", "响应", "到场", "无条件", "服务期",
        "保质期", "保修", "培训", "响应时间", "上门",
    ),
}

# 锚点用正则以容忍改写：数值可变（3 项 → 5 项）、同义可换（类似 → 同类），
# 但模式本身必须具体，不能是「项目」「要求」这种满篇都是的词。
# 招标文本里「近三年」「不少于六项」和「近 3 年」「≥3 项」都常见，锚点两种数字都认。
_CN_NUM = r"(?:\d+|[一二三四五六七八九十百]{1,3})"

_TYPE_ANCHORS: dict[str, tuple[str, ...]] = {
    REQ_TYPE_QUALIFICATION: (
        r"(?:特级|一级|二级|三级|甲级|乙级|丙级)",
        r"(?:ISO|CMMI|GB/T|OHSAS)\s*[-]?\s*\d+",
        r"(?:注册|执业|建造师|工程师|资质|证书|认证|许可证|执照)",
    ),
    REQ_TYPE_ACHIEVEMENT: (
        rf"近\s*{_CN_NUM}\s*年",
        rf"{_CN_NUM}\s*(?:项|个|份|例)",
        r"(?:类似|同类|相同|相近)\s*项目",
        r"(?:业绩|案例|合同|中标)",
    ),
    REQ_TYPE_PARAMETER: (
        r"\d+(?:\.\d+)?\s*(?:mm|cm|m|kV|kW|V|A|%|路|口|个|米|吨|条|台|套|Mbps|Gbps)",
        rf"(?:不低于|不小于|不大于|≥|≤|大于等于|小于等于)\s*{_CN_NUM}",
        rf"{_CN_NUM}\s*(?:小时|天|年|分钟)",
    ),
    REQ_TYPE_COMMITMENT: (
        r"(?:承诺|保证|免费|质保|售后|保修|无条件|响应|到场|培训)",
        rf"{_CN_NUM}\s*(?:小时|天|年|分钟)[^，。；]{{0,10}}(?:内|响应|到场|质保|保修)",
    ),
}


def classify_requirement(text: str) -> str:
    """按条款文本推断类型；只有命中具体信号词才归类，否则 general。

    归类只影响判定路径（结构化锚点），不改变条款的实质性与处置规则。
    """

    source = clean_text(text)
    for req_type in (
        REQ_TYPE_QUALIFICATION,
        REQ_TYPE_ACHIEVEMENT,
        REQ_TYPE_PARAMETER,
        REQ_TYPE_COMMITMENT,
    ):
        if any(signal in source for signal in _TYPE_SIGNALS[req_type]):
            return req_type
    return REQ_TYPE_GENERAL


def type_anchor_patterns(text: str, req_type: str) -> list[str]:
    """返回「条款自身也出现」的锚点模式——正文里凭空出现的模式不算响应。"""

    patterns = _TYPE_ANCHORS.get(req_type, ())
    return [pattern for pattern in patterns if re.search(pattern, text or "")]


def _match_is_affirmative(body: str, match: re.Match) -> bool:
    """锚点/整句命中是否落在肯定句里——「不具备 X」不算「具备 X」。"""

    return not negation_at(body, match.start())


def anchor_coverage(text: str, body: str, req_type: str) -> float:
    """结构化锚点命中率（0–1）；无相关锚点时返回 0，回退到 3-gram 判定。

    否定句里的锚点不算命中：正文写「不具备一级建造师资格」时，「一级建造师」
    这个锚点即使字面出现，也不是对「须具备一级建造师资格」的响应。
    """

    if not body or req_type == REQ_TYPE_GENERAL:
        return 0.0
    relevant = type_anchor_patterns(text, req_type)
    if not relevant:
        return 0.0
    hit = 0
    for pattern in relevant:
        for match in re.finditer(pattern, body):
            if _match_is_affirmative(body, match):
                hit += 1
                break
    return hit / len(relevant)


# 中文没有词边界，逐字比较会把「改写过但意思到了」的响应判成未响应；
# 用字符 3-gram 覆盖率衡量「这段话有没有被正面回答」，比关键词命中稳。
_NGRAM = 3
_CJK = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def _ngrams(text: str, size: int = _NGRAM) -> set[str]:
    cleaned = _CJK.sub("", text or "")
    if len(cleaned) < size:
        return {cleaned} if cleaned else set()
    return {cleaned[index : index + size] for index in range(len(cleaned) - size + 1)}


def coverage_ratio(
    text: str, body: str, req_type: str = REQ_TYPE_GENERAL, check_constraints: bool = True
) -> float:
    """条款在某一章正文里的覆盖率（0–1）。

    基线是 3-gram；对可归类的条款再算结构化锚点命中率，取较高者：
    - 锚点全中：关键事实（资质等级 / 业绩数量 / 参数阈值）在正文明确出现，
      即便措辞改写也视为已覆盖（0.75，高于长条款 0.45 阈值）；
    - 部分命中：给 0.6 倍加成，需多数锚点命中才够阈值；
    - 通用条款或无关锚点：完全回退到 3-gram，行为与旧版一致。
    """

    if not body:
        return 0.0
    core = requirement_core(text)
    core_hit = len(core) >= 6 and core in body
    if core_hit:
        # 整句字面包含也可能是反话：「我方不具备一级建造师资格」包含
        # 「具备一级建造师资格」。否定句里的整句命中不算覆盖。
        position = body.find(core)
        if negation_at(body, position):
            return 0.0
        return 1.0
    grams = _ngrams(core)
    gram_ratio = len(grams & _ngrams(body)) / len(grams) if grams else 0.0
    anchor = anchor_coverage(text, body, req_type)
    ratio = gram_ratio
    if anchor >= 0.999:
        ratio = max(ratio, 0.75)
    elif anchor > 0:
        ratio = max(ratio, anchor * 0.6)

    # 约束否决：即使字面/锚点命中，只要量化约束被违反或有约束未证实，就不算覆盖。
    # find_covering_section 会对合并正文统一做一次，传 check_constraints=False 避免重复。
    if ratio > 0 and check_constraints:
        return 0.0 if constraint_blocked(evaluate_constraints(text, body)) else ratio
    return ratio


def find_covering_section(
    text: str, nodes: list[dict], req_type: str = REQ_TYPE_GENERAL
) -> dict | None:
    """返回覆盖该条款的章节（position 最小者），没有则 None。

    阈值取 0.45（长条款）或 1.0（短条款必须整句包含）：单靠一个泛词不算响应。
    req_type 决定是否在 3-gram 之外启用结构化锚点判定。
    """

    core = requirement_core(text)
    threshold = 0.45 if len(core) >= 8 else 1.0

    # 约束比较针对整份正文（合并各章），而不是单章：复合条款的三条约束
    # 可能分散在不同章节响应。违反或未证实的约束直接判未覆盖。
    combined = "\n".join((node.get("body") or "") for node in nodes)
    verdicts = evaluate_constraints(text, combined)
    if constraint_blocked(verdicts):
        return None

    best: tuple[float, int, dict] | None = None
    fallback: tuple[float, int, dict] | None = None
    for order, node in enumerate(nodes):
        body = node.get("body") or ""
        if not body:
            continue
        ratio = coverage_ratio(text, body, req_type, check_constraints=False)
        position = node.get("position")
        if not isinstance(position, int):
            position = order
        candidate = (ratio, -position, node)
        # 即使单章没到阈值也记一个候选：复合条款的约束满足后，各章可能只响应了
        # 其中一部分，单章分数上不去；此时选正文最相关的章作为跳转锚点。
        if fallback is None or candidate[:2] > fallback[:2]:
            fallback = candidate
        if ratio >= threshold and (best is None or candidate[:2] > best[:2]):
            best = candidate
    if best is not None:
        return best[2]
    # 只有当确实存在已证实的量化约束时才用锚点兜底，避免泛词误覆盖。
    if verdicts and all(item.state == "satisfied" for item in verdicts):
        return fallback[2] if fallback else None
    return None


def is_substantive(requirement) -> bool:
    """是否属于「实质性条款」：带 ★/▲ 标记，或解析期判为 mandatory。"""

    return bool(getattr(requirement, "marker", "")) or bool(getattr(requirement, "mandatory", False))


def requirement_issues(requirements, nodes: list[dict]) -> list[CheckResult]:
    """未响应的实质性条款 → 核对问题。

    ★/▲ 条款判为 blocking（最高优先级，直接进导出门禁）；仅 mandatory 的判为 critical。
    已人工确认 responded / na 的条款不再产出问题。
    """

    # 延迟导入，避免 verdict ↔ substantive 的循环依赖。
    from .verdict import SATISFIED, judge

    results: list[CheckResult] = []
    # 按条文本排序，保证重复核对时编号稳定，人工处置不会被无故重开。
    # 排序即编号依据：★ 在前、▲ 次之、仅 mandatory 的在最后，同档按条文本排序。
    # 这样确认某一条不会改动其它条的编号，人工处置不会因编号漂移被重开。
    marker_rank = {"★": 0, "▲": 1, "": 2}
    candidates = sorted(
        (item for item in requirements if is_substantive(item)),
        key=lambda item: (
            marker_rank.get(getattr(item, "marker", "") or "", 2),
            item.text or "",
        ),
    )
    for index, requirement in enumerate(candidates, start=1):
        if (getattr(requirement, "response_status", "pending") or "pending") != "pending":
            continue
        text = (requirement.text or "").strip()
        if not text:
            continue
        marker = getattr(requirement, "marker", "") or marker_of(text)
        req_type = classify_requirement(text)

        verdict = judge(requirement, nodes)
        if verdict.state == SATISFIED:
            # 五层判定确认全部硬性约束满足且证据充分——不再产出阻断问题。
            continue

        # 四态映射：满足 → 无问题；不满足/部分满足 → blocking/critical（阻塞）；
        # 人工复核 → info（可见但不阻塞，沿用风险总览的 info 档）。
        evidence = {
            "requirement_id": getattr(requirement, "id", ""),
            "marker": marker,
            "requirement_type": req_type,
            "keywords": requirement_keywords(text),
            "verdict": verdict.state,
            "verdict_label": verdict.label,
            # 五层证据：提取 / 原子拆分 / 结构化约束 / 多维验证 / 判定。
            "layers": verdict.evidence,
            # 兼容键：旧前端/测试从顶层读逐条约束判定。
            "constraints": verdict.evidence["layer4"]["constraints"],
        }
        results.append(
            CheckResult(
                code=f"SUB-{index:03d}",
                level=verdict.level or "critical",
                category="实质性响应",
                message=f"实质性条款{verdict.label}：{text[:60]}（{verdict.reason}）",
                location=(getattr(requirement, "source_quote", "") or "")[:255],
                finding=text[:200],
                recommendation=(
                    "在正文对应章节逐条响应这一条（引用条款原文，给出承诺、参数或方案），"
                    "或在风险总览里标记「已响应 / 不适用」并填写原因。"
                ),
                evidence=evidence,
            )
        )
    return results
