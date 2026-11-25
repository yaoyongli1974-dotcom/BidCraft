from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .audit import record_audit
from .models import (
    Fact,
    FactCitation,
    Material,
    Parameter,
    Project,
    Requirement,
    Risk,
    ScoringItem,
    Section,
    SourceFile,
    utcnow,
)
from .parsing.extract import ExtractionResult
from .rules.loader import Package
from .schemas import (
    FactItem,
    FactLedger,
    MaterialItem,
    ParameterItem,
    RequirementItem,
    RiskItem,
    ScoringItemModel,
    SourceRef,
)


class FactItemV032(FactItem):
    """带人工确认元信息的台账条目。

    确认字段只出现在内存/HTTP 表示中，不改写 C1 交换格式的 JSON Schema：
    ``bidcraft.schemas.FactItem`` 与 checked-in schema 保持向后兼容。
    """

    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    source_file_id: str | None = None
    source_file_name: str = ""
    source_quote: str = ""
    source_page: int = 0


class FactLedgerV032(FactLedger):
    """使用 :class:`FactItemV032` 的台账，其余字段与 C1 台账完全一致。"""

    facts: list[FactItemV032] = Field(default_factory=list)


def _compact(value: str | None) -> str:
    """引文比对用的确定性归一化：只去空白，不做语义推断。"""

    return re.sub(r"\s+", "", value or "")


def _actor_dict(actor: dict | str | None) -> dict | None:
    """CLI 传 ``"cli"``，HTTP 传 request user；统一成 record_audit 的 actor。"""

    if isinstance(actor, dict):
        return actor
    if isinstance(actor, str) and actor:
        return {"username": actor, "display_name": actor}
    return None


def _actor_name(actor: dict | None) -> str:
    return str(
        (actor or {}).get("display_name") or (actor or {}).get("username") or "system"
    )[:80]


def _payload_get(payload, name: str, default=None):
    if isinstance(payload, dict):
        return payload.get(name, default)
    return getattr(payload, name, default)

# 招标截止日期别名与归一化只由 verdict 维护；台账侧再导出，避免两处口径漂移。
# bid_opening_time / 投标截止日期 / deadline 等别名都能被 resolve_deadline 解析，
# 解析失败返回 None（绝不用服务器当前时间兜底）。
from .verdict import DEADLINE_KEYS, is_deadline_key, resolve_deadline  # noqa: F401,E402


def build_ledger(
    package: Package,
    extracted: ExtractionResult,
    *,
    source_filename: str = "",
    source_file_id: str | None = None,
) -> FactLedgerV032:
    """纯计算阶段构建台账；来源文件 ID 仅在调用方能给出时携带。

    未给出 ID 时由 :func:`save_ledger` 在真实 DB 中按文件名/引文解析，
    绝不自行编造来源。
    """

    facts: list[FactItemV032] = []
    for spec in package.fields:
        hit = extracted.fields.get(spec.key)
        if hit and hit.value:
            quote = hit.quote[:300]
            facts.append(
                FactItemV032(
                    key=spec.key,
                    label=spec.label,
                    value=hit.value,
                    unit=spec.unit,
                    status="extracted",
                    confidence=hit.confidence,
                    required=spec.required,
                    source=SourceRef(file=source_filename, page=hit.page, quote=quote),
                    source_file_id=source_file_id,
                    source_file_name=source_filename if source_file_id else "",
                    source_quote=quote,
                    source_page=hit.page,
                )
            )
        else:
            facts.append(
                FactItemV032(
                    key=spec.key,
                    label=spec.label,
                    value="",
                    unit=spec.unit,
                    status="missing",
                    confidence=0.0,
                    required=spec.required,
                    source=SourceRef(file=source_filename),
                    source_file_id=source_file_id,
                    source_file_name=source_filename if source_file_id else "",
                )
            )

    project = {item.key: item.value for item in facts if item.value}
    return FactLedgerV032(
        package=package.name,
        project=project,
        facts=facts,
        materials=extracted.materials,
        parameters=extracted.parameters,
        scoring=extracted.scoring,
        requirements=extracted.requirements,
        risks=extracted.risks,
        template_sections=extracted.template_sections,
        stats=dict(extracted.stats),
    )


def _resolve_source_file(
    item: FactItemV032, sources: list[SourceFile], by_id: dict[str, SourceFile]
) -> SourceFile | None:
    """把每条命中映射到本项目里**真实存在**的 SourceFile。

    优先使用调用方给出的真实 ID；其次按文件名精确匹配；最后按引文在文件
    文本里做确定性包含校验。找不到就保持空值，绝不编造来源。
    """

    if item.source_file_id:
        return by_id.get(item.source_file_id)
    name = (item.source.file or "").strip()
    if name:
        for source in sources:
            if source.filename == name:
                return source
    needle = _compact(item.source.quote or item.source_quote)[:80]
    if needle:
        for source in sources:
            if needle in _compact(source.text_content or ""):
                return source
    return None


def save_ledger(
    db: Session,
    project_id: str,
    ledger: FactLedger,
    *,
    force: bool = False,
    actor: dict | str | None = None,
    now: datetime | None = None,
) -> None:
    """回写台账。confirmed/manual 事实默认保留；仅 ``force=True`` 才覆盖。

    覆盖已人工确认的事实时走 :func:`update_fact`：清空确认盖章并在同一事务
    写审计/章节失效，确保「重解析不能静默改写人工确认」。
    """

    sources = list(db.scalars(select(SourceFile).where(SourceFile.project_id == project_id)))
    by_id = {source.id: source for source in sources}
    existing = {row.key: row for row in db.scalars(select(Fact).where(Fact.project_id == project_id))}
    for item in ledger.facts:
        source = _resolve_source_file(item, sources, by_id)  # type: ignore[arg-type]
        if source is not None:
            item.source_file_id = source.id
            item.source_file_name = source.filename
            item.source.file = source.filename
        item.source_quote = item.source.quote or item.source_quote
        item.source_page = item.source.page or item.source_page
        row = existing.get(item.key)
        if row is not None and row.status in {"confirmed", "manual"} and not force:
            # 人工确认过的事实是权威口径，重解析不得覆盖。
            continue
        if row is None:
            row = Fact(project_id=project_id, key=item.key)
            db.add(row)
            db.flush()
        update_fact(
            db,
            project_id,
            {
                "key": item.key,
                "label": item.label,
                "value": item.value,
                "unit": item.unit,
                "status": item.status,
                "confidence": item.confidence,
                "required": item.required,
                "source_file_id": item.source_file_id,
                "source_quote": item.source_quote,
                "source_page": item.source_page,
            },
            actor=actor,
            now=now,
            force=force,
        )

    db.execute(delete(Material).where(Material.project_id == project_id))
    db.execute(delete(Parameter).where(Parameter.project_id == project_id))
    db.execute(delete(ScoringItem).where(ScoringItem.project_id == project_id))
    db.execute(delete(Requirement).where(Requirement.project_id == project_id))
    db.execute(delete(Risk).where(Risk.project_id == project_id))

    db.add_all(
        [
            Material(
                project_id=project_id,
                name=item.name,
                spec=item.spec,
                unit=item.unit,
                qty=item.qty,
                note=item.note,
                source_quote=item.source_quote,
            )
            for item in ledger.materials
        ]
    )
    db.add_all(
        [
            Parameter(
                project_id=project_id,
                name=item.name,
                value=item.value,
                unit=item.unit,
                requirement=item.requirement,
                category=item.category,
                mandatory=item.mandatory,
                source_quote=item.source_quote,
            )
            for item in ledger.parameters
        ]
    )
    db.add_all(
        [
            ScoringItem(
                project_id=project_id,
                name=item.name,
                score=item.score,
                criteria=item.criteria,
                category=item.category,
                section_hint=item.section_hint,
                source_quote=item.source_quote,
            )
            for item in ledger.scoring
        ]
    )
    db.add_all(
        [
            Requirement(
                project_id=project_id,
                kind=item.kind,
                text=item.text,
                mandatory=item.mandatory,
                source_quote=item.source_quote,
                marker=item.marker,
                page=item.page,
                clause_no=item.clause_no,
            )
            for item in ledger.requirements
        ]
    )
    db.add_all(
        [
            Risk(
                project_id=project_id,
                level=item.level,
                text=item.text,
                source_quote=item.source_quote,
            )
            for item in ledger.risks
        ]
    )


def load_ledger(db: Session, project_id: str) -> FactLedgerV032:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    facts = list(db.scalars(select(Fact).where(Fact.project_id == project_id).order_by(Fact.key)))
    source_files = {
        row.id: row
        for row in db.scalars(select(SourceFile).where(SourceFile.project_id == project_id))
    }
    materials = list(db.scalars(select(Material).where(Material.project_id == project_id)))
    parameters = list(db.scalars(select(Parameter).where(Parameter.project_id == project_id)))
    scoring = list(db.scalars(select(ScoringItem).where(ScoringItem.project_id == project_id)))
    requirements = list(db.scalars(select(Requirement).where(Requirement.project_id == project_id)))
    risks = list(db.scalars(select(Risk).where(Risk.project_id == project_id)))

    ledger = FactLedgerV032(
        package=project.package,
        facts=[
            FactItemV032(
                key=row.key,
                label=row.label,
                value=row.value,
                unit=row.unit,
                status=row.status,  # type: ignore[arg-type]
                confidence=row.confidence,
                required=row.required,
                source=SourceRef(
                    file=(
                        source_files[row.source_file_id].filename
                        if row.source_file_id in source_files
                        else ""
                    ),
                    page=row.source_page,
                    quote=row.source_quote,
                ),
                confirmed_by=row.confirmed_by,
                confirmed_at=row.confirmed_at,
                source_file_id=row.source_file_id,
                source_file_name=(
                    source_files[row.source_file_id].filename
                    if row.source_file_id in source_files
                    else ""
                ),
                source_quote=row.source_quote,
                source_page=row.source_page,
            )
            for row in facts
        ],
        materials=[
            MaterialItem(
                name=row.name,
                spec=row.spec,
                unit=row.unit,
                qty=row.qty,
                note=row.note,
                source_quote=row.source_quote,
            )
            for row in materials
        ],
        parameters=[
            ParameterItem(
                name=row.name,
                value=row.value,
                unit=row.unit,
                requirement=row.requirement,
                category=row.category,
                mandatory=row.mandatory,
                source_quote=row.source_quote,
            )
            for row in parameters
        ],
        scoring=[
            ScoringItemModel(
                name=row.name,
                score=row.score,
                criteria=row.criteria,
                category=row.category,
                section_hint=row.section_hint,
                source_quote=row.source_quote,
            )
            for row in scoring
        ],
        requirements=[
            RequirementItem(
                kind=row.kind,
                text=row.text,
                mandatory=row.mandatory,
                source_quote=row.source_quote,
                marker=row.marker or "",
            )
            for row in requirements
        ],
        risks=[
            RiskItem(level=row.level, text=row.text, source_quote=row.source_quote)
            for row in risks
        ],
    )
    ledger.project = {item.key: item.value for item in ledger.facts if item.value}
    return ledger


def fact_context(ledger: FactLedger) -> dict[str, str]:
    return {item.key: item.value for item in ledger.facts}


#: 单节超长时的截断标记；截断只发生在行边界，绝不切出半行。
_TRUNCATION_MARKER = "…（内容已截断）"


def _clip_lines(lines: list[str], cap: int) -> list[str]:
    """Keep whole lines within ``cap``; append a marker when content was dropped."""

    if sum(len(line) for line in lines) + max(0, len(lines) - 1) <= cap:
        return list(lines)
    if cap <= len(_TRUNCATION_MARKER) + 1:
        return [lines[0][: max(0, cap)]]
    room = max(0, cap - len(_TRUNCATION_MARKER))
    kept: list[str] = []
    used = 0
    for line in lines:
        added = len(line) + (1 if kept else 0)
        if used + added > room:
            break
        kept.append(line)
        used += added
    if not kept and lines:
        # At minimum keep a (possibly shortened) section header so the reader
        # still knows what the truncated block was about.
        kept = [lines[0][:room]]
    kept.append(_TRUNCATION_MARKER)
    return kept


def _fit_lines(lines: list[str], cap: int) -> list[str]:
    """Clip until the rendered block is strictly within ``cap`` chars."""

    block = _clip_lines(lines, cap)
    while cap > 0 and len("\n".join(block)) > cap:
        block = _clip_lines(lines, cap - (len("\n".join(block)) - cap) - 1)
    return block


def render_ledger(ledger: FactLedger, *, max_chars: int = 16000) -> str:
    """Render the ledger into prompt text. Only confirmed/extracted facts are used.

    Long ledgers are compressed section by section (proportional caps) instead of
    slicing the final string: the closing 口径 block survives intact and comes
    last, fact keys stay visible, and truncation only ever happens on a line
    boundary so no half-line is fed to the model.
    """
    sections: list[list[str]] = []
    facts_lines: list[str] = ["【项目事实（唯一口径，不得改写、不得编造）】"]
    for item in ledger.facts:
        if item.value:
            unit = f" {item.unit}" if item.unit and item.unit not in item.value else ""
            facts_lines.append(f"- {item.label}（{item.key}）：{item.value}{unit}")
    sections.append(facts_lines)

    if ledger.scoring:
        scoring_lines = ["【评分项（篇幅与章节应对齐）】"]
        for item in ledger.scoring[:40]:
            score = f"{item.score:g}分" if item.score is not None else "未标注分值"
            scoring_lines.append(
                f"- {item.name}：{score}" + (f"；{item.criteria[:200]}" if item.criteria else "")
            )
        sections.append(scoring_lines)

    if ledger.materials:
        material_lines = ["【材料/工程量清单（数量与单位必须一致）】"]
        for item in ledger.materials[:200]:
            qty = "" if item.qty is None else f" {item.qty:g}"
            spec = f" {item.spec}" if item.spec else ""
            unit = f" {item.unit}" if item.unit else ""
            material_lines.append(f"- {item.name}{spec}：{qty}{unit}")
        sections.append(material_lines)

    if ledger.parameters:
        parameter_lines = ["【技术参数（必须逐条响应）】"]
        for item in ledger.parameters[:150]:
            value = f"：{item.value}" if item.value else ""
            parameter_lines.append(f"- {item.name}{value}")
        sections.append(parameter_lines)

    if ledger.requirements:
        requirement_lines = ["【实质性/资格要求】"]
        for item in ledger.requirements[:80]:
            mark = "（强制）" if item.mandatory else ""
            badge = f"{item.marker}" if item.marker else ""
            requirement_lines.append(f"- {badge}[{item.kind}]{mark} {item.text}")
        sections.append(requirement_lines)

    if ledger.risks:
        risk_lines = ["【废标/否决风险】"]
        for item in ledger.risks[:40]:
            risk_lines.append(f"- [{item.level}] {item.text}")
        sections.append(risk_lines)

    missing = [item for item in ledger.facts if item.required and not item.value]
    if missing:
        missing_lines = ["【缺失必填事实（正文必须写 {{待填:字段名}}，不得猜测）】"]
        for item in missing:
            missing_lines.append(f"- {item.label}（{item.key}）")
        sections.append(missing_lines)

    # 口径块最后追加，且永不参与按比例压缩：它是防编造的硬约束。
    guidance_lines = [
        "【写作口径】",
        "1. 只使用上述事实；没有来源的价格、证书编号、业绩、人员姓名、专利号、资质一律写 {{待填:具体信息}}。",
        "2. 数量与单位按台账原文；同一对象前后不得矛盾。",
        "3. 招标参数必须逐条响应，不得用「完全响应」「无偏离」代替具体响应值。",
        "4. 需要插图的位置只写一行 {{图片占位:图名与盖章/截图要求}}。",
        "5. 不把计划性措施写成企业已经具备的事实，不新增无依据的量化承诺。",
    ]
    guidance = "\n".join(guidance_lines)

    def assemble(chunks: list[list[str]]) -> str:
        return "\n\n".join("\n".join(chunk) for chunk in chunks + [guidance_lines])

    lengths = [sum(len(line) for line in block) + len(block) - 1 for block in sections]
    if sum(lengths) + 2 * len(sections) + len(guidance) <= max_chars:
        return assemble(sections)

    # Reserve room for the guidance block and the blank lines between sections,
    # then split the remaining budget in proportion to each section's size.
    budget = max_chars - len(guidance) - 2 * len(sections)
    if budget <= 0:
        return guidance[:max_chars]
    weights = [max(1, size) for size in lengths]
    total_weight = sum(weights)
    caps = [budget * weight // total_weight for weight in weights]
    kept = [_fit_lines(block, cap) for block, cap in zip(sections, caps)]
    used = sum(len("\n".join(block)) for block in kept)
    leftover = budget - used
    # Redistribute unused cap to the most important sections first: facts,
    # scoring, materials, parameters, requirements, risks, missing.
    for index, block in enumerate(sections):
        if leftover <= 0:
            break
        # Grow relative to the block's *actual* size, not its original cap:
        # the unused part of the proportional cap is already in ``leftover``.
        grown = _fit_lines(block, len("\n".join(kept[index])) + leftover)
        gain = len("\n".join(grown)) - len("\n".join(kept[index]))
        if gain > 0:
            kept[index] = grown
            leftover -= gain
    return assemble(kept)


def _verify_evidence(
    db: Session, project_id: str, payload, status: str, row: Fact
) -> tuple[str | None, str, int]:
    """校验确认证据确实来自本项目的真实文件，拒绝凭空填写的引文/页码。

    完全未提交新证据时（旧客户端只改取值），已确认事实沿用台账里既有的
    证据，不引入任何客户端伪造内容；从未确认过的事实必须提交真实证据。
    """

    file_id = str(_payload_get(payload, "source_file_id", "") or "").strip()
    quote = str(_payload_get(payload, "source_quote", "") or "").strip()
    try:
        page = int(_payload_get(payload, "source_page", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("来源页码必须是正整数") from exc
    if status not in {"confirmed", "manual"}:
        return (file_id or None), quote, max(0, page)
    if not (file_id or quote or page):
        if row.status in {"confirmed", "manual"}:
            # 兼容既有「只改取值」的调用：证据沿用台账既有值，不新增伪造来源。
            return row.source_file_id, row.source_quote, row.source_page
        raise ValueError("人工确认必须提供真实的来源文件、引文与页码")
    if not file_id or not quote or page <= 0:
        raise ValueError("人工确认必须提供真实的来源文件、引文与页码")
    source = db.get(SourceFile, file_id)
    if source is None or source.project_id != project_id:
        raise ValueError("来源文件不存在或不属于本项目，不能据此确认事实")
    text = _compact(source.text_content or "")
    if not text:
        raise ValueError("来源文件没有可核验的文本内容，不能据此确认事实")
    if _compact(quote)[:80] not in text:
        raise ValueError("引文未出现在来源文件中，拒绝伪造证据")
    if source.page_count and page > source.page_count:
        raise ValueError("来源页码超出文件页数，拒绝伪造证据")
    return file_id, quote, page


def _invalidate_sections(
    db: Session,
    project_id: str,
    fact: Fact,
    node_ids: list[str],
    *,
    actor: dict | None,
    now: datetime,
    reason: str,
) -> None:
    """事实口径被改写：引用章节退回复核，确认盖章失效。

    与事实更新共用同一个事务；只把状态置为 review，不解锁、不确认，导出门禁
    依旧按原规则拦截，不存在绕过路径。
    """

    if not node_ids:
        return
    sections = list(db.scalars(select(Section).where(Section.node_id.in_(node_ids))))
    for section in sections:
        section.review_status = "review"
        section.confirmed_at = None
        section.confirmed_by = None
    record_audit(
        db,
        project_id,
        "fact.invalidation",
        {
            "fact_id": fact.id,
            "key": fact.key,
            "node_ids": node_ids,
            "reason": reason,
            "invalidated_sections": [section.id for section in sections],
        },
        actor=actor,
    )


def update_fact(
    db: Session,
    project: Project | str,
    payload,
    *,
    actor: dict | str | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> Fact:
    """更新一条事实，确认盖章/证据校验/章节失效与审计在同一事务内完成。

    ``project`` 支持 Project 对象或项目 ID；``payload`` 支持 Pydantic 模型与
    dict。``force=True`` 只允许重解析显式覆盖已人工确认的事实（默认保留）。
    """

    project_id = project.id if isinstance(project, Project) else str(project)
    key = str(_payload_get(payload, "key", "") or "")
    row = db.scalar(select(Fact).where(Fact.project_id == project_id, Fact.key == key))
    if row is None:
        raise ValueError(f"事实不存在: {key}")

    value = _payload_get(payload, "value", None)
    unit = _payload_get(payload, "unit", None)
    status = str(_payload_get(payload, "status", row.status) or row.status)
    new_value = row.value if value is None else str(value)
    new_unit = row.unit if unit is None else str(unit)
    if status not in {"extracted", "confirmed", "manual", "missing", "conflict"}:
        raise ValueError(f"事实状态非法: {status}")
    if (
        row.status in {"confirmed", "manual"}
        and status not in {"confirmed", "manual"}
        and not force
    ):
        raise ValueError("已确认事实不能被重解析覆盖；请使用人工确认或 force=True")

    file_id, quote, page = _verify_evidence(db, project_id, payload, status, row)
    changed_value = new_value != (row.value or "")
    changed_unit = new_unit != (row.unit or "")
    changed = changed_value or changed_unit
    stamp_at = now or utcnow()
    actor_dict = _actor_dict(actor)
    actor_name = _actor_name(actor_dict)

    citation_rows = list(
        db.scalars(
            select(FactCitation).where(
                FactCitation.project_id == project_id, FactCitation.fact_key == key
            )
        )
    )
    affected = sorted(
        {
            item.node_id
            for item in citation_rows
            if changed_unit or (item.value or "") != new_value
        }
    )

    row.value = new_value
    row.unit = new_unit
    row.status = status
    for field in ("label", "confidence", "required"):
        incoming = _payload_get(payload, field, None)
        if incoming is not None:
            setattr(row, field, incoming)
    if file_id:
        row.source_file_id = file_id
        row.source_quote = quote
        row.source_page = page
    if changed:
        # 旧确认不再对应当前口径：先清空，再按新状态决定是否重新盖章。
        row.confirmed_by = None
        row.confirmed_at = None
    if status in {"confirmed", "manual"}:
        row.confirmed_by = actor_name
        row.confirmed_at = stamp_at

    record_audit(
        db,
        project_id,
        "fact.update",
        {
            "key": key,
            "status": status,
            "value": new_value[:200],
            "changed": changed,
            "forced": bool(force),
            "confirmed_by": row.confirmed_by,
            "source_file_id": file_id,
            "stale_citations": len(affected),
        },
        actor=actor_dict,
    )
    if changed:
        _invalidate_sections(
            db,
            project_id,
            row,
            affected,
            actor=actor_dict,
            now=stamp_at,
            reason="unit_changed" if changed_unit and not changed_value else "value_changed",
        )
    return row


def ledger_summary(ledger: FactLedger) -> dict[str, Any]:
    total = len(ledger.facts)
    filled = sum(1 for f in ledger.facts if f.value)
    required = sum(1 for f in ledger.facts if f.required)
    missing_required = sum(1 for f in ledger.facts if f.required and not f.value)
    return {
        "fields_total": total,
        "fields_filled": filled,
        "required": required,
        "missing_required": missing_required,
        "materials": len(ledger.materials),
        "parameters": len(ledger.parameters),
        "scoring": len(ledger.scoring),
        "requirements": len(ledger.requirements),
        "risks": len(ledger.risks),
    }
