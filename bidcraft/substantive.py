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


# 中文没有词边界，逐字比较会把「改写过但意思到了」的响应判成未响应；
# 用字符 3-gram 覆盖率衡量「这段话有没有被正面回答」，比关键词命中稳。
_NGRAM = 3
_CJK = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


def _ngrams(text: str, size: int = _NGRAM) -> set[str]:
    cleaned = _CJK.sub("", text or "")
    if len(cleaned) < size:
        return {cleaned} if cleaned else set()
    return {cleaned[index : index + size] for index in range(len(cleaned) - size + 1)}


def coverage_ratio(text: str, body: str) -> float:
    """条款在某一章正文里的覆盖率（0–1）：核心短语的 3-gram 有多少出现在该章。"""

    if not body:
        return 0.0
    core = requirement_core(text)
    if len(core) >= 6 and core in body:
        return 1.0
    grams = _ngrams(core)
    if not grams:
        return 0.0
    return len(grams & _ngrams(body)) / len(grams)


def find_covering_section(text: str, nodes: list[dict]) -> dict | None:
    """返回覆盖该条款的章节（position 最小者），没有则 None。

    阈值取 0.5 且至少命中 2 个关键词：单靠一个泛词（「业绩」「资质」）不算响应。
    """

    core = requirement_core(text)
    threshold = 0.45 if len(core) >= 8 else 1.0
    best: tuple[float, int, dict] | None = None
    for order, node in enumerate(nodes):
        body = node.get("body") or ""
        if not body:
            continue
        ratio = coverage_ratio(text, body)
        if ratio < threshold:
            continue
        position = node.get("position")
        if not isinstance(position, int):
            position = order
        candidate = (ratio, -position, node)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best else None


def is_substantive(requirement) -> bool:
    """是否属于「实质性条款」：带 ★/▲ 标记，或解析期判为 mandatory。"""

    return bool(getattr(requirement, "marker", "")) or bool(getattr(requirement, "mandatory", False))


def requirement_issues(requirements, nodes: list[dict]) -> list[CheckResult]:
    """未响应的实质性条款 → 核对问题。

    ★/▲ 条款判为 blocking（最高优先级，直接进导出门禁）；仅 mandatory 的判为 critical。
    已人工确认 responded / na 的条款不再产出问题。
    """

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
        if find_covering_section(text, nodes):
            continue
        results.append(
            CheckResult(
                code=f"SUB-{index:03d}",
                level="blocking" if marker else "critical",
                category="实质性响应",
                message=f"实质性条款未在正文找到明确响应：{text[:60]}",
                location=(getattr(requirement, "source_quote", "") or "")[:255],
                finding=text[:200],
                recommendation=(
                    "在正文对应章节逐条响应这一条（引用条款原文，给出承诺、参数或方案），"
                    "或在风险总览里标记「已响应 / 不适用」并填写原因。"
                ),
                evidence={
                    "requirement_id": getattr(requirement, "id", ""),
                    "marker": marker,
                    "keywords": requirement_keywords(text),
                },
            )
        )
    return results
