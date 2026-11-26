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
_MIN_CONTINUE_WORDS = 40
_UNDERLENGTH_RATIO = 0.8
_ADHERENCE_MIN = 0.15


def is_pending(node: dict[str, Any]) -> bool:
    """A chapter still needs work if it is empty, failed/fallback, or too short.

    Body presence alone is not completion: a timed-out or truncated draft that
    was persisted with a few dozen characters must be eligible for regeneration.
    """

    if not str(node.get("body") or "").strip():
        return True
    status = str(
        node.get("section_status") or node.get("status") or ""
    ).strip().lower()
    if status in {"failed", "fallback", "error"}:
        return True
    words = int(node.get("words") or 0)
    target = int(node.get("target_words") or 0)
    return words <= 0 or (target > 0 and words < 0.5 * target)


def _adherence(anchor: str, body: str) -> float:
    """Containment of the chapter heading/intent bigrams in the body."""

    def bigrams(text: str) -> set[str]:
        value = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or "")
        return {value[i : i + 2] for i in range(len(value) - 1)}

    expected = bigrams(anchor)
    if not expected:
        return 1.0
    actual = bigrams(body)
    return round(len(expected & actual) / len(expected), 4)


def verify_chapter(node: dict[str, Any], body: str) -> dict[str, Any]:
    """Check a drafted chapter against its target length and outline intent."""

    target = max(1, int(node.get("target_words") or 1200))
    words = count_words(body)
    ratio = round(words / target, 4)
    anchor = f"{node.get('heading') or ''} {node.get('intent') or ''}".strip()
    adherence = _adherence(anchor, body)
    return {
        "words": words,
        "target_words": target,
        "word_ratio": ratio,
        "adherence": adherence,
        "underlength": ratio < _UNDERLENGTH_RATIO,
        "off_topic": bool(anchor) and adherence < _ADHERENCE_MIN,
    }


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


def _plan_ref_numbers(plan: Any) -> set[str]:
    """正文生成时只把规划真实引用涉及的数字当作白名单来源。"""

    from .chapter_plan import chapter_plan_payload

    if plan is None:
        return set()
    payload = chapter_plan_payload(plan)
    allowed: set[str] = set()
    for field_name in ("fact_refs", "knowledge_refs", "score_points", "requirements"):
        for row in payload.get(field_name) or []:
            if not isinstance(row, dict):
                continue
            allowed.update(
                _NUMBER.findall(
                    " ".join(
                        str(row.get(key) or "")
                        for key in ("value", "key", "label", "snippet", "text", "criteria",
                                    "name", "heading", "source", "trace")
                    )
                )
            )
    return allowed


def suspicious_numbers(body: str, ledger: FactLedger, plan: Any = None) -> list[str]:
    allowed: set[str] = set()
    for item in ledger.facts:
        allowed.update(_NUMBER.findall(f"{item.value} {item.unit}"))
    for collection in (ledger.materials, ledger.parameters, ledger.scoring):
        for row in collection:
            dump = row.model_dump()
            allowed.update(_NUMBER.findall(" ".join(str(v) for v in dump.values())))
    if plan is not None:
        # 有已确认规划时收紧白名单：只有规划真实引用（事实/知识/评分锚点）
        # 覆盖的数字才免于标记；其余台账数字仍需人工复核来源，不阻断生成。
        allowed = _plan_ref_numbers(plan)
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


def unused_plan_refs(body: str, plan: Any) -> dict[str, list[str]]:
    """正文中未落地的规划引用（事实/知识/评分锚点），供复核不阻断生成。"""

    from .chapter_plan import chapter_plan_payload

    if plan is None:
        return {}
    payload = chapter_plan_payload(plan)
    haystack = _normalize_block(body)
    result: dict[str, list[str]] = {}
    specs = (
        ("facts", "fact_refs", ("key", "label", "value")),
        ("knowledge", "knowledge_refs", ("id", "heading", "source", "snippet")),
        ("scores", "score_points", ("name", "criteria")),
    )
    for name, field_name, keys in specs:
        missed: list[str] = []
        for row in payload.get(field_name) or []:
            if not isinstance(row, dict):
                continue
            ident = str(row.get("id") or "")
            labels = [_text_value(row.get(key)) for key in keys]
            if any(label and _normalize_block(label) in haystack for label in labels):
                continue
            missed.append(ident or (labels[0] if labels else ""))
        if missed:
            result[name] = missed
    return result


def _text_value(value: Any) -> str:
    return str(value or "").strip()


@dataclass
class GenerationReport:
    written: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    fallback: list[str] = field(default_factory=list)
    suspicious: dict[str, list[str]] = field(default_factory=dict)
    suspicious_count: int = 0
    knowledge_downgrades: int = 0
    verification: dict[str, dict[str, Any]] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    #: 正文未使用的规划引用：{章节: {"facts": [...], "knowledge": [...], "scores": [...]}}
    unused_plan_refs: dict[str, dict[str, list[str]]] = field(default_factory=dict)


def continuity_digest(chapters: list[tuple[str, str]], *, recent: int = 3, cap: int = 4000) -> str:
    """Build a compact cross-chapter digest for long documents.

    Long-range section keeps every heading with a one-line gist so the model
    knows the whole structure; the recent tail keeps fuller excerpts so the next
    chapter can pick up terminology, numbers and commitments without repeating
    them. The result is capped so it cannot crowd out the outline.
    """
    if not chapters:
        return ""
    long_range = []
    for heading, body in chapters:
        gist = " ".join(str(body or "").split())[:60]
        long_range.append(f"- {heading}：{gist}" if gist else f"- {heading}")
    detail = []
    for heading, body in chapters[-recent:]:
        text = " ".join(str(body or "").split())[:300]
        if text:
            detail.append(f"【{heading}】{text}")
    parts = []
    if detail:
        parts.append("近期章节（承接术语、数字与承诺，勿重复）：\n" + "\n".join(detail))
    if long_range:
        parts.append("全书已写章节索引：\n" + "\n".join(long_range))
    text = "\n\n".join(parts)
    return text[:cap]


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
    chapter_plan: Any = None,
    plan_text: str = "",
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
        needs=node.get("needs") or [],
        chapter_plan=chapter_plan,
        plan_text=plan_text,
        context_tokens=getattr(getattr(client, "settings", None), "model_context_tokens", None),
        max_tokens=max(2000, min(8000, target * 2)),
    )
    body = client.complete(
        messages,
        max_tokens=max(2000, min(8000, target * 2)),
        stream=stream,
        on_delta=on_delta,
    )
    body = strip_leading_heading(body, heading)

    # Verify against the outline before the chapter is marked drafted: an
    # under-target or off-topic draft is continued first, not shipped as-is.
    verification = verify_chapter(node, body)
    attempts = 0
    while (
        attempts < 2
        and count_words(body) >= _MIN_CONTINUE_WORDS
        and (verification["underlength"] or _ends_mid_sentence(body))
    ):
        attempts += 1
        previous = body
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
        verification = verify_chapter(node, body)
        if len(body) <= len(previous):
            break
    node["_verification"] = verification
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
    before_chapter: Callable[[dict[str, Any], Session], bool] | None = None,
    after_chapter: Callable[[dict[str, Any], Session], None] | None = None,
    commit_chapter: Callable[[dict[str, Any], str, str], None] | None = None,
    fail_chapter: Callable[[dict[str, Any]], None] | None = None,
    plan_provider: Callable[[str], Any] | None = None,
) -> GenerationReport:
    """Generate chapters in outline order.

    ``before_chapter``/``after_chapter`` are optional reliability hooks.  The
    worker uses them as (a) an abort checkpoint — they may raise to stop the run
    when the lease is lost/shutdown is requested — and (b) a per-unit
    idempotency ledger — returning ``False`` skips a unit already completed by
    an earlier attempt, ``after_chapter`` records it on success.  ``fail_chapter``
    is the matching fenced failure hook; production uses it instead of writing
    ``Section.status`` from this long session.  They receive
    the live session so the body write and the ledger mark commit atomically.

    ``commit_chapter``, when supplied, takes over the body write and ledger mark
    in a NEW short transaction owned by the caller (the background worker uses
    it for chapter-level fencing).  In that mode the long generation session no
    longer writes Section/OutlineNode at all.
    """
    from ..evaluation.learning import LESSON_LABEL, lessons_block
    from .chapter_plan import plan_context_text
    from .outline import load_outline

    nodes = load_outline(db, project_id)
    project = db.get(Project, project_id)
    facts_text = ""
    # 历史处置经验是「未审核建议」，只作为提示词里的显式附段，绝不并入台账事实。
    # 只读聚合不写 KB/企业资产；按项目取一次，章节级过滤交给规划提示词。
    lessons_text = lessons_block(db, project) if project is not None else ""
    if project is not None:
        from ..profile import facts_block
        from ..profile import load as load_profile

        facts_text = facts_block(load_profile(project))
        from ..scoring import hints as score_hints

        extra = score_hints(db, project)
        if extra:
            facts_text = f"{facts_text}\n{extra}" if facts_text else extra
        if lessons_text:
            facts_text = (
                f"{facts_text}\n\n" if facts_text else ""
            ) + (
                f"【{LESSON_LABEL}（非核实事实，仅供人工复核参考，不得作为正文事实引用）】\n"
                f"{lessons_text}\n"
                "以上建议经验结束；不属于事实台账，正文中不得当作已核实事实使用。"
            )
    report = GenerationReport()
    previous_texts: list[str] = []
    chapters: list[tuple[str, str]] = []
    for index, node in enumerate(nodes, start=1):
        info = {
            "node_id": node["id"],
            "section_id": node.get("section_id") or "",
            "heading": node["heading"],
            "index": index,
            "total": len(nodes),
            "target_words": int(node.get("target_words") or 0),
        }
        plan = plan_provider(node["id"]) if plan_provider is not None else None
        plan_text = (
            plan_context_text(plan, lessons_text=lessons_text) if plan is not None else ""
        )
        if plan is not None:
            info.update(
                {
                    "chapter_plan_id": plan.id,
                    "planning_version": int(plan.version or 0),
                    "planning_status": plan.status or "",
                    "planning_prompt_version": plan.prompt_version or "",
                }
            )
        if only_node and node["id"] != only_node:
            if node.get("body"):
                previous_texts.append(node["body"])
                chapters.append((node["heading"], node["body"]))
            continue
        already_written = not is_pending(node) and not only_node and not force
        # Abort checkpoint + idempotency gate happen before the expensive model
        # call, including on chapters we would otherwise skip by body presence.
        should_generate = True
        if before_chapter is not None:
            should_generate = before_chapter(info, db)
        if already_written or not should_generate:
            if node.get("body"):
                previous_texts.append(node["body"])
                chapters.append((node["heading"], node["body"]))
            continue
        chapter_words = 0
        chapter_source = ""
        chapter_failed = False
        chapter_downgrades = 0
        if on_chapter_start:
            on_chapter_start(info)
        try:
            knowledge_text = ""
            if knowledge_provider is not None:
                try:
                    retrieved = knowledge_provider(
                        f"{node['heading']} {node.get('intent') or ''}".strip()
                    )
                    # KnowledgeContext exposes attributes; legacy providers
                    # return (text, downgrades) or plain text.
                    from .chapter_plan import normalize_retrieval

                    knowledge_text, chapter_downgrades, _, _ = normalize_retrieval(retrieved)
                except Exception:  # noqa: BLE001 - knowledge is optional context
                    knowledge_text = ""
            report.knowledge_downgrades += chapter_downgrades
            body, source = write_chapter(
                client,
                ledger,
                package,
                node,
                nodes,
                continuity_digest(chapters),
                stream=stream,
                on_delta=on_delta,
                knowledge_text=knowledge_text,
                facts_text=facts_text,
                chapter_plan=plan,
                plan_text=plan_text,
            )
            body = dedupe_body(body, previous_texts)
            if not body.strip():
                raise ModelUnavailable("模型返回空正文")
            words = count_words(body)
            chapter_words = words
            chapter_source = source
            info["words"] = words
            if commit_chapter is not None:
                # Fenced short transaction owned by the caller: no body write
                # happens in the long generation session, so a reclaimed worker
                # cannot commit a chapter it no longer owns.
                commit_chapter(info, body, source)
            else:
                section = db.scalar(select(Section).where(Section.id == node["section_id"]))
                outline_node = db.get(OutlineNode, node["id"])
                if section is not None:
                    section.body = body
                    section.words = words
                    section.status = "fallback" if source == "fallback" else "drafted"
                if outline_node is not None:
                    outline_node.status = "fallback" if source == "fallback" else "drafted"
                db.flush()
                if after_chapter is not None:
                    after_chapter(info, db)
            previous_texts.append(body)
            chapters.append((node["heading"], body))
            report.written.append(node["heading"])
            if source == "fallback":
                report.fallback.append(node["heading"])
            report.verification[node["heading"]] = node.get("_verification") or verify_chapter(
                node, body
            )
            flagged = suspicious_numbers(body, ledger, plan)
            if flagged:
                report.suspicious[node["heading"]] = flagged
                report.suspicious_count += len(flagged)
            unused = unused_plan_refs(body, plan)
            if unused:
                report.unused_plan_refs[node["heading"]] = unused
        except ModelUnavailable as exc:
            chapter_failed = True
            if fail_chapter is not None:
                # Production supplies a fenced short-transaction hook so a lost
                # lease or aborted run cannot be overwritten by this long
                # generation session.  Legacy callers keep the old local write.
                fail_chapter(info)
            else:
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
