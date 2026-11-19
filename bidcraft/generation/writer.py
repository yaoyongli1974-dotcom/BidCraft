from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import OutlineNode, Project, Section
from ..rules.loader import Package
from ..schemas import FactLedger
from .agent import ModelClient, ModelUnavailable
from .outline import outline_text
from .prompts import chapter_messages, continue_messages

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；\n])")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_TABLE_ROW = re.compile(r"^\s*\|")


def count_words(text: str) -> int:
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    latin = len(re.findall(r"[A-Za-z0-9]+", text or ""))
    return chinese + latin


def _normalize_block(text: str) -> str:
    return re.sub(r"[\s，。；：、,.!:;*#|>-]", "", text or "")


def dedupe_body(body: str, previous_texts: list[str]) -> str:
    if not previous_texts:
        return body
    previous = set()
    for text in previous_texts:
        for piece in _SENTENCE_SPLIT.split(text):
            normalized = _normalize_block(piece)
            if len(normalized) >= 24:
                previous.add(normalized)
    kept: list[str] = []
    for block in body.split("\n"):
        if _TABLE_ROW.match(block):
            kept.append(block)
            continue
        pieces = _SENTENCE_SPLIT.split(block)
        kept.append(
            "".join(
                piece
                for piece in pieces
                if not (len(_normalize_block(piece)) >= 24 and _normalize_block(piece) in previous)
            )
        )
    return "\n".join(kept).strip()


def strip_leading_heading(body: str, heading: str) -> str:
    lines = body.lstrip().splitlines()
    if lines and _normalize_block(lines[0]) == _normalize_block(heading):
        lines = lines[1:]
    text = "\n".join(lines).strip()
    text = re.sub(r"^```(?:markdown)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _ends_mid_sentence(text: str) -> bool:
    stripped = (text or "").rstrip()
    if not stripped:
        return True
    if _TABLE_ROW.match(stripped.splitlines()[-1]):
        return False
    return not stripped.endswith(("。", "！", "？", "；", ":", "：", "）", ")", "】", "|"))


def _overlap_join(left: str, right: str) -> str:
    left = left.rstrip()
    right = right.lstrip()
    for size in range(min(200, len(left), len(right)), 0, -1):
        if left[-size:] == right[:size]:
            return left + right[size:]
    return left + "\n" + right


def suspicious_numbers(body: str, ledger: FactLedger) -> list[str]:
    allowed: set[str] = set()
    for item in ledger.facts:
        allowed.update(_NUMBER.findall(f"{item.value} {item.unit}"))
    for collection in (ledger.materials, ledger.parameters, ledger.scoring):
        for row in collection:
            dump = row.model_dump()
            allowed.update(_NUMBER.findall(" ".join(str(v) for v in dump.values())))
    flagged: list[str] = []
    for token in _NUMBER.findall(body or ""):
        if len(token.replace(".", "")) < 3:
            continue
        if token in allowed:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", token):
            continue
        flagged.append(token)
    return list(dict.fromkeys(flagged))[:20]


def _fact_table(ledger: FactLedger, keys: list[str]) -> str:
    rows = ["| 项目 | 取值 |", "| --- | --- |"]
    fact_map = ledger.fact_map()
    for key in keys:
        item = fact_map.get(key)
        if item and item.value:
            rows.append(f"| {item.label} | {item.value}{(' ' + item.unit) if item.unit and item.unit not in item.value else ''} |")
    return "\n".join(rows) if len(rows) > 2 else ""


def _materials_table(ledger: FactLedger, limit: int = 30) -> str:
    if not ledger.materials:
        return ""
    rows = ["| 序号 | 名称 | 规格型号 | 单位 | 数量 |", "| --- | --- | --- | --- | --- |"]
    for index, item in enumerate(ledger.materials[:limit], start=1):
        qty = "" if item.qty is None else f"{item.qty:g}"
        rows.append(f"| {index} | {item.name} | {item.spec} | {item.unit} | {qty} |")
    return "\n".join(rows)


def _parameters_table(ledger: FactLedger, limit: int = 40) -> str:
    if not ledger.parameters:
        return ""
    rows = ["| 序号 | 技术参数 | 响应值/要求 | 偏离 |", "| --- | --- | --- | --- |"]
    for index, item in enumerate(ledger.parameters[:limit], start=1):
        rows.append(f"| {index} | {item.name} | {item.value or item.requirement} | 无偏离 |")
    return "\n".join(rows)


def fallback_chapter(ledger: FactLedger, heading: str, intent: str, needs: list[str]) -> str:
    """Deterministic draft used when no model is configured or the call fails.

    It never invents facts: unknown values stay as {{待填:...}} placeholders.
    """
    project = ledger.value("project.name") or "{{待填:招标项目名称}}"
    number = ledger.value("project.number") or "{{待填:项目编号}}"
    parts: list[str] = []
    parts.append(
        f"本章依据招标文件《{project}》（项目编号：{number}）编制，"
        f"围绕「{heading}」逐项响应招标要求。{intent or ''}".strip()
    )

    table = _fact_table(ledger, needs)
    if table:
        parts.append("本章引用的项目事实如下，正文口径与事实台账保持一致：\n\n" + table)

    if re.search(r"清单|点位|材料|设备|报价", heading + intent):
        materials = _materials_table(ledger)
        if materials:
            parts.append(
                "本章材料与工程量按招标清单口径列示，数量、单位与清单逐项对应；"
                "未在招标文件中给出数量的项目不自行估算。\n\n" + materials
            )
        else:
            parts.append(
                "材料与工程量清单以招标文件为准，待清单导入后在 {{待填:材料清单}} 中逐项列示，"
                "并保证名称、规格、单位、数量与招标清单一致。"
            )

    if re.search(r"参数|技术|规格|响应|设计", heading + intent):
        parameters = _parameters_table(ledger)
        if parameters:
            parts.append(
                "技术参数响应坚持逐条对应原则，不以「完全响应」「无偏离」概括替代具体响应值：\n\n"
                + parameters
            )
        else:
            parts.append(
                "技术参数响应表按招标文件技术规格书逐条编制；未取得原文参数的条目在 "
                "{{待填:参数名称及响应值}} 中列示，待技术负责人确认后补齐。"
            )

    scoring = [
        item
        for item in ledger.scoring
        if item.name and item.name in (heading + intent)
    ]
    if scoring:
        lines = ["本章对应的评分因素及分值："]
        for item in scoring:
            lines.append(f"- {item.name}：{item.score if item.score is not None else '未标注'} 分；{item.criteria}")
        parts.append("\n".join(lines))

    if re.search(r"废标|实质性|否决|无效|风险", heading + intent) and (
        ledger.risks or ledger.requirements
    ):
        lines = [
            (
                "本章逐条对照招标文件的实质性条款与废标/否决情形，形成投标内部检查清单；"
                "下列条款均为招标文件原文口径，响应措施在定稿前由商务与技术负责人复核。"
            )
        ]
        seen_texts: set[str] = set()
        for risk in ledger.risks[:12]:
            lines.append(f"- [{risk.level}] {risk.text}")
            seen_texts.add(risk.text)
        mandatory = [item for item in ledger.requirements if item.mandatory][:10]
        for item in mandatory:
            if item.text in seen_texts:
                continue
            lines.append(f"- [实质性] {item.text}")
            seen_texts.add(item.text)
        parts.append("\n".join(lines))

    parts.append(
        "实施安排上，本章内容与施工组织、进度计划、质量与安全管理章节相互衔接："
        "先完成深化设计与点位复核，再按工序穿插施工，隐蔽工程经验收合格后进入下一道工序；"
        "系统调试按单机、子系统、整体联调三级推进，测试记录与竣工资料同步归档。"
    )
    parts.append(
        "质量与验收口径以招标文件及现行国家标准为准；招标文件未明确的检测比例、"
        "仪器型号与人员配置，在 {{待填:检测比例及资源配置}} 中由项目技术负责人确认，"
        "不在正文中自行设定量化承诺。"
    )
    if re.search(r"售后|质保|运维|服务", heading + intent):
        warranty = ledger.value("project.warranty") or "{{待填:质保期}}"
        parts.append(
            f"售后服务按质保期 {warranty} 执行，提供故障响应、备品备件与定期巡检；"
            "具体响应时限与驻场安排以合同谈判结果为准，详见售后服务承诺表。"
        )

    body = "\n\n".join(part for part in parts if part)
    return body


@dataclass
class GenerationReport:
    written: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    fallback: list[str] = field(default_factory=list)
    suspicious: dict[str, list[str]] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)


def write_chapter(
    client: ModelClient,
    ledger: FactLedger,
    package: Package,
    node: dict[str, Any],
    outline: list[dict[str, Any]],
    previous_summary: str,
    *,
    stream: bool = False,
    on_delta: Callable[[str], None] | None = None,
    knowledge_text: str = "",
    facts_text: str = "",
) -> tuple[str, str]:
    heading = str(node["heading"])
    intent = str(node.get("intent") or "")
    target = int(node.get("target_words") or 1200)
    if not client.available:
        return fallback_chapter(ledger, heading, intent, node.get("needs") or []), "fallback"

    from ..ledger import render_ledger

    messages = chapter_messages(
        project_name=ledger.value("project.name") or "{{待填:招标项目名称}}",
        heading=heading,
        intent=intent,
        target_words=target,
        ledger_text=render_ledger(ledger),
        outline_text=outline_text(outline),
        previous_summary=previous_summary,
        package_title=package.title,
        knowledge_text=knowledge_text,
        facts_text=facts_text,
    )
    body = client.complete(
        messages,
        max_tokens=max(2000, min(8000, target * 2)),
        stream=stream,
        on_delta=on_delta,
    )
    body = strip_leading_heading(body, heading)

    attempts = 0
    while _ends_mid_sentence(body) and count_words(body) < target and attempts < 2:
        attempts += 1
        try:
            extra = client.complete(
                continue_messages(body, body[-1200:]),
                max_tokens=max(1200, min(4000, target)),
                temperature=0.3,
                stream=stream,
                on_delta=on_delta,
            )
        except ModelUnavailable:
            break
        if not extra.strip():
            break
        body = _overlap_join(body, strip_leading_heading(extra, heading))
    return body, "model"


def generate_document(
    db: Session,
    project_id: str,
    client: ModelClient,
    ledger: FactLedger,
    package: Package,
    *,
    stream: bool = False,
    on_delta: Callable[[str], None] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    on_chapter_start: Callable[[dict[str, Any]], None] | None = None,
    only_node: str | None = None,
    force: bool = False,
    knowledge_provider: Callable[[str], str] | None = None,
) -> GenerationReport:
    from .outline import load_outline

    nodes = load_outline(db, project_id)
    project = db.get(Project, project_id)
    facts_text = ""
    if project is not None:
        from ..profile import facts_block
        from ..profile import load as load_profile

        facts_text = facts_block(load_profile(project))
    report = GenerationReport()
    previous_texts: list[str] = []
    summaries: list[str] = []
    for index, node in enumerate(nodes, start=1):
        if only_node and node["id"] != only_node:
            if node.get("body"):
                previous_texts.append(node["body"])
                summaries.append(f"{node['heading']}：{node['body'][:120]}")
            continue
        if node.get("body") and not only_node and not force:
            previous_texts.append(node["body"])
            summaries.append(f"{node['heading']}：{node['body'][:120]}")
            continue
        chapter_words = 0
        chapter_source = ""
        chapter_failed = False
        if on_chapter_start:
            on_chapter_start(
                {
                    "heading": node["heading"],
                    "index": index,
                    "total": len(nodes),
                    "target_words": int(node.get("target_words") or 0),
                }
            )
        try:
            knowledge_text = ""
            if knowledge_provider is not None:
                try:
                    knowledge_text = knowledge_provider(
                        f"{node['heading']} {node.get('intent') or ''}".strip()
                    )
                except Exception:  # noqa: BLE001 - knowledge is optional context
                    knowledge_text = ""
            body, source = write_chapter(
                client,
                ledger,
                package,
                node,
                nodes,
                "\n".join(summaries[-8:]),
                stream=stream,
                on_delta=on_delta,
                knowledge_text=knowledge_text,
                facts_text=facts_text,
            )
            body = dedupe_body(body, previous_texts)
            if not body.strip():
                raise ModelUnavailable("模型返回空正文")
            words = count_words(body)
            chapter_words = words
            chapter_source = source
            section = db.scalar(select(Section).where(Section.id == node["section_id"]))
            outline_node = db.get(OutlineNode, node["id"])
            if section is not None:
                section.body = body
                section.words = words
                section.status = "fallback" if source == "fallback" else "drafted"
            if outline_node is not None:
                outline_node.status = "fallback" if source == "fallback" else "drafted"
            db.flush()
            previous_texts.append(body)
            summaries.append(f"{node['heading']}：{body[:120]}")
            report.written.append(node["heading"])
            if source == "fallback":
                report.fallback.append(node["heading"])
            flagged = suspicious_numbers(body, ledger)
            if flagged:
                report.suspicious[node["heading"]] = flagged
        except ModelUnavailable as exc:
            chapter_failed = True
            section = db.scalar(select(Section).where(Section.id == node["section_id"]))
            if section is not None:
                section.status = "failed"
            report.failed.append({"heading": node["heading"], "error": str(exc)})
        if on_progress:
            total = len(nodes)
            done = len(report.written) + len(report.failed)
            on_progress(
                {
                    "heading": node["heading"],
                    "progress": int(done * 100 / total) if total else 100,
                    "done": done,
                    "total": total,
                    "words": chapter_words,
                    "source": chapter_source or ("failed" if chapter_failed else ""),
                    "failed": chapter_failed,
                }
            )
    report.usage = {
        "prompt_tokens": client.usage.prompt_tokens,
        "completion_tokens": client.usage.completion_tokens,
    }
    return report
