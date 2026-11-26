from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("bidcraft.generation.prompts")

#: Fallback context window when the model endpoint does not publish one.
DEFAULT_CONTEXT_TOKENS = 32768
DEFAULT_MAX_TOKENS = 4000
#: Every block keeps at least this much room so a huge high-priority block
#: cannot crowd out lower-priority facts (notably the mandatory needs block).
_MIN_BLOCK_CHARS = 160


def _clip_text(text: str, limit: int) -> str:
    """Clip on a line boundary; never emit a half-line of assembled context."""

    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    kept: list[str] = []
    used = 0
    for line in text.splitlines(keepends=True):
        if used + len(line) > limit:
            break
        kept.append(line)
        used += len(line)
    result = "".join(kept).rstrip()
    if not result:
        result = text[:limit].rstrip()
    marker = "\n…（已截断）"
    if len(result) + len(marker) <= limit:
        result += marker
    return result[:limit]


def build_chapter_context(
    *,
    project_name: str,
    heading: str,
    intent: str,
    target_words: int,
    ledger_text: str,
    outline_text: str,
    previous_summary: str,
    package_title: str,
    knowledge_text: str = "",
    facts_text: str = "",
    needs: list[str] | None = None,
    context_tokens: int | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    chapter_plan: Any = None,
    plan_text: str = "",
) -> str:
    """Assemble the chapter user prompt within a model context budget.

    The allocator is priority-ordered facts > needs > ledger > outline > digest
    > KB.  Each block keeps a small minimum, then the remaining room is handed
    out in priority order; truncation happens on line boundaries.  The final
    size is logged so an oversized prompt is observable in production.
    """

    try:
        window = int(context_tokens or DEFAULT_CONTEXT_TOKENS)
    except (TypeError, ValueError):
        window = DEFAULT_CONTEXT_TOKENS
    try:
        completion = int(max_tokens or 0)
    except (TypeError, ValueError):
        completion = DEFAULT_MAX_TOKENS
    budget = max(512, window - completion)

    header = (
        f"项目：{project_name}\n"
        f"行业包：{package_title}\n"
        f"当前章节：{heading}\n"
        f"章节意图：{intent or '按招标文件与本行业惯例展开'}\n"
        f"目标字数：约 {target_words} 字"
    )
    requirements = (
        "写作要求：\n"
        "- 直接输出本章正文，不要输出章节标题以外的解释。\n"
        "- 先用一段话说明本章响应了招标文件的哪些要求，再分层展开。\n"
        "- 涉及清单、点位、参数、容量计算时使用 Markdown 表格，表头与数据行完整。\n"
        "- 篇幅不少于目标字数的 80%。"
    )

    needs_block = ""
    if needs:
        lines = "\n".join(f"- {item}" for item in needs)
        needs_block = (
            "【本章必须引用的事实字段（逐项在正文体现，缺失写 {{待填:字段名}}）】\n"
            f"{lines}\n\n"
        )
    facts_block = (
        "【全局事实（必须严格遵守，前后一致）】\n"
        f"{facts_text}\n"
        "以上事实与台账冲突时，以台账为准；未列出的事实不得编造。"
        if facts_text
        else ""
    )
    if not plan_text and chapter_plan is not None:
        from .chapter_plan import plan_context_text

        plan_text = plan_context_text(chapter_plan)
    plan_block = f"{plan_text}\n" if plan_text else ""

    # (text, priority) - lower priority number is protected first.
    blocks: list[tuple[str, int]] = [
        (plan_block, 0),
        (facts_block, 0),
        (needs_block, 1),
        (str(ledger_text or ""), 2),
        (f"【全书大纲】\n{outline_text or ''}", 3),
        (f"【已写章节摘要（避免重复）】\n{previous_summary or '（本章为开篇）'}", 4),
        (f"\n{knowledge_text}\n" if knowledge_text else "", 5),
    ]

    fixed_len = len(header) + len(requirements) + 2 * (len(blocks) + 1)
    room = budget - fixed_len
    if room < len(blocks) * _MIN_BLOCK_CHARS:
        # Tiny budgets: keep every block's first line rather than dropping a
        # whole category; the final clip below enforces the hard cap.
        minimums = [min(len(text), 1) for text, _priority in blocks]
    else:
        minimums = [min(len(text), _MIN_BLOCK_CHARS) for text, _priority in blocks]
    remaining = max(0, room - sum(minimums))
    limits = list(minimums)
    for index in sorted(range(len(blocks)), key=lambda i: blocks[i][1]):
        if remaining <= 0:
            break
        needed = max(0, len(blocks[index][0]) - limits[index])
        add = min(needed, remaining)
        limits[index] += add
        remaining -= add

    parts = [
        _clip_text(text, limit)
        for (text, _priority), limit in zip(blocks, limits)
        if text.strip()
    ]
    user = f"{header}\n\n" + "\n\n".join(parts) + f"\n\n{requirements}"
    if len(user) > budget:
        user = _clip_text(user, budget)
    logger.info(
        "chapter prompt size: %d chars (budget %d, window %s, max_tokens %d)",
        len(user),
        budget,
        context_tokens,
        completion,
    )
    return user


SYSTEM_WRITER = """你是一名资深弱电/智能化行业投标文件编制工程师。
你的任务是根据给定的「项目事实台账」和「章节大纲」撰写技术标正文。

硬性规则：
1. 只使用台账中出现的事实。台账没有的价格、证书编号、企业业绩、人员姓名、专利号、资质，
   一律写 {{待填:具体信息}}，绝不编造。
2. 招标技术参数必须逐条响应，写出具体响应值或配置，不得用「完全响应」「无偏离」代替。
3. 数量、单位、期限必须与台账一致；同一对象前后不得矛盾。
4. 不把计划性措施写成企业已经具备的事实，不新增无依据的量化承诺。
5. 输出 Markdown 正文：二级标题用 ##，三级用 ###；表格用标准 Markdown 表格。
6. 不写代码块，不写与本章无关的章节，不重复其他章节已经展开的内容。
7. 需要插图时只写一行：{{图片占位:图名与盖章/截图要求}}。
8. 正文使用简体中文，语气专业、克制，避免营销话术。"""


def chapter_messages(
    *,
    project_name: str,
    heading: str,
    intent: str,
    target_words: int,
    ledger_text: str,
    outline_text: str,
    previous_summary: str,
    package_title: str,
    knowledge_text: str = "",
    facts_text: str = "",
    needs: list[str] | None = None,
    context_tokens: int | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    chapter_plan: Any = None,
    plan_text: str = "",
) -> list[dict[str, str]]:
    user = build_chapter_context(
        project_name=project_name,
        heading=heading,
        intent=intent,
        target_words=target_words,
        ledger_text=ledger_text,
        outline_text=outline_text,
        previous_summary=previous_summary,
        package_title=package_title,
        knowledge_text=knowledge_text,
        facts_text=facts_text,
        needs=needs,
        context_tokens=context_tokens,
        max_tokens=max_tokens,
        chapter_plan=chapter_plan,
        plan_text=plan_text,
    )
    return [
        {"role": "system", "content": SYSTEM_WRITER},
        {"role": "user", "content": user},
    ]


CONTINUE_PROMPT = """上一段输出在句子中间被截断。请从截断处继续写完本章剩余内容，
不要重复已经写过的段落，不要重新输出章节标题。"""


def continue_messages(previous: str, tail: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_WRITER},
        {"role": "user", "content": f"已生成内容（末尾）：\n...{tail[-1200:]}\n\n{CONTINUE_PROMPT}"},
    ]


SYSTEM_CHAPTER_PLANNER = """你是一名资深技术标编制规划师。只依据输入的项目真实数据做章节规划，
禁止编造评分项、事实、知识或章节 ID。输出严格 JSON 对象，不要 Markdown 代码块，
不要解释文字。可用字段：objective（字符串），score_points / requirements / fact_refs /
knowledge_refs（对象数组；每一项必须带输入中给出的真实 id，没有 id 的引用会被剔除），
conditions / challenges / strategy / measures / planned_tables / planned_diagrams /
forbidden_assumptions / quality_criteria（字符串或对象数组）。知识引用必须沿用输入中的
实体 ID、文档/页码、溯源、分值、可信度与降级标记，不得凭检索文本另行生成 ID。
strategy 必须是可直接指导正文写作的具体条目。"""


def chapter_plan_messages(context: dict) -> list[dict[str, str]]:
    """构造章节规划的模型输入（在 DB 短事务之外调用）。"""

    def brief(key: str, limit: int) -> str:
        rows = context.get(key) or []
        return "\n".join(
            json.dumps(row, ensure_ascii=False)[:400] for row in rows[:limit]
        )

    user = (
        f"项目：{context.get('project_id', '')}（行业包 {context.get('package', '')}）\n"
        f"章节：{context.get('heading', '')}\n"
        f"意图：{context.get('intent', '')}\n"
        f"目标字数：{context.get('target_words', 0)}\n"
        f"用户指令：{context.get('user_instruction', '')}\n\n"
        f"真实评分项（只能引用这里的 id）：\n{brief('scoring', 12)}\n\n"
        f"真实招标要求（只能引用这里的 id）：\n{brief('requirements', 15)}\n\n"
        f"真实事实台账（只能引用这里的 id/key）：\n{brief('facts', 12)}\n\n"
        f"真实知识条目（只能引用这里的 id，含文档/页码/溯源/可信度）：\n"
        f"{chr(10).join(json.dumps(row, ensure_ascii=False)[:600] for row in (context.get('knowledge') or [])[:4])}\n\n"
        f"检索文本（仅供参考，不能据此造出 ID）：\n{str(context.get('knowledge_text') or '')[:1200]}\n\n"
        + (
            "历史人工调整 / 未审核建议经验（仅供参考，非核实事实，不得写入正文作为事实）：\n"
            f"{str(context.get('lessons_text') or '')[:1200]}\n\n"
            if context.get("lessons_text")
            else ""
        )
        + "请输出 JSON 规划。"
    )
    return [
        {"role": "system", "content": SYSTEM_CHAPTER_PLANNER},
        {"role": "user", "content": user},
    ]
