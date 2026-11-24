from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import (
    Fact,
    Material,
    Parameter,
    Project,
    Requirement,
    Risk,
    ScoringItem,
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

# 招标截止日期别名与归一化只由 verdict 维护；台账侧再导出，避免两处口径漂移。
# bid_opening_time / 投标截止日期 / deadline 等别名都能被 resolve_deadline 解析，
# 解析失败返回 None（绝不用服务器当前时间兜底）。
from .verdict import DEADLINE_KEYS, is_deadline_key, resolve_deadline  # noqa: F401,E402


def build_ledger(
    package: Package, extracted: ExtractionResult, *, source_filename: str = ""
) -> FactLedger:
    facts: list[FactItem] = []
    for spec in package.fields:
        hit = extracted.fields.get(spec.key)
        if hit and hit.value:
            facts.append(
                FactItem(
                    key=spec.key,
                    label=spec.label,
                    value=hit.value,
                    unit=spec.unit,
                    status="extracted",
                    confidence=hit.confidence,
                    required=spec.required,
                    source=SourceRef(
                        file=source_filename, page=hit.page, quote=hit.quote[:300]
                    ),
                )
            )
        else:
            facts.append(
                FactItem(
                    key=spec.key,
                    label=spec.label,
                    value="",
                    unit=spec.unit,
                    status="missing",
                    confidence=0.0,
                    required=spec.required,
                    source=SourceRef(file=source_filename),
                )
            )

    project = {item.key: item.value for item in facts if item.value}
    return FactLedger(
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


def save_ledger(db: Session, project_id: str, ledger: FactLedger) -> None:
    existing = {row.key: row for row in db.scalars(select(Fact).where(Fact.project_id == project_id))}
    for item in ledger.facts:
        row = existing.get(item.key)
        if row is None:
            row = Fact(project_id=project_id, key=item.key)
            db.add(row)
        row.label = item.label
        row.value = item.value
        row.unit = item.unit
        row.status = item.status
        row.confidence = item.confidence
        row.required = item.required
        row.source_page = item.source.page
        row.source_quote = item.source.quote

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


def load_ledger(db: Session, project_id: str) -> FactLedger:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    facts = list(db.scalars(select(Fact).where(Fact.project_id == project_id).order_by(Fact.key)))
    materials = list(db.scalars(select(Material).where(Material.project_id == project_id)))
    parameters = list(db.scalars(select(Parameter).where(Parameter.project_id == project_id)))
    scoring = list(db.scalars(select(ScoringItem).where(ScoringItem.project_id == project_id)))
    requirements = list(db.scalars(select(Requirement).where(Requirement.project_id == project_id)))
    risks = list(db.scalars(select(Risk).where(Risk.project_id == project_id)))

    ledger = FactLedger(
        package=project.package,
        facts=[
            FactItem(
                key=row.key,
                label=row.label,
                value=row.value,
                unit=row.unit,
                status=row.status,  # type: ignore[arg-type]
                confidence=row.confidence,
                required=row.required,
                source=SourceRef(file="", page=row.source_page, quote=row.source_quote),
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


def update_fact(
    db: Session, project_id: str, key: str, *, value: str | None = None, status: str | None = None
) -> Fact:
    row = db.scalar(select(Fact).where(Fact.project_id == project_id, Fact.key == key))
    if row is None:
        raise ValueError(f"事实不存在: {key}")
    if value is not None:
        row.value = value
        row.status = status or ("confirmed" if value else "missing")
    elif status is not None:
        row.status = status
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
