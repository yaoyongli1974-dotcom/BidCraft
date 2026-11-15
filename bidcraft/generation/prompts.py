from __future__ import annotations

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
) -> list[dict[str, str]]:
    knowledge = f"\n{knowledge_text}\n" if knowledge_text else ""
    user = f"""项目：{project_name}
行业包：{package_title}
当前章节：{heading}
章节意图：{intent or '按招标文件与本行业惯例展开'}
目标字数：约 {target_words} 字

{ledger_text}

【全书大纲】
{outline_text}

【已写章节摘要（避免重复）】
{previous_summary or '（本章为开篇）'}
{knowledge}
写作要求：
- 直接输出本章正文，不要输出章节标题以外的解释。
- 先用一段话说明本章响应了招标文件的哪些要求，再分层展开。
- 涉及清单、点位、参数、容量计算时使用 Markdown 表格，表头与数据行完整。
- 篇幅不少于目标字数的 80%。"""
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
