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


def render_ledger(ledger: FactLedger, *, max_chars: int = 16000) -> str:
    """Render the ledger into prompt text. Only confirmed/extracted facts are used."""
    lines: list[str] = ["【项目事实（唯一口径，不得改写、不得编造）】"]
    for item in ledger.facts:
        if item.value:
            unit = f" {item.unit}" if item.unit and item.unit not in item.value else ""
            lines.append(f"- {item.label}（{item.key}）：{item.value}{unit}")

    if ledger.scoring:
        lines.append("\n【评分项（篇幅与章节应对齐）】")
        for item in ledger.scoring[:40]:
            score = f"{item.score:g}分" if item.score is not None else "未标注分值"
            lines.append(f"- {item.name}：{score}" + (f"；{item.criteria[:200]}" if item.criteria else ""))

    if ledger.materials:
        lines.append("\n【材料/工程量清单（数量与单位必须一致）】")
        for item in ledger.materials[:200]:
            qty = "" if item.qty is None else f" {item.qty:g}"
            spec = f" {item.spec}" if item.spec else ""
            unit = f" {item.unit}" if item.unit else ""
            lines.append(f"- {item.name}{spec}：{qty}{unit}")

    if ledger.parameters:
        lines.append("\n【技术参数（必须逐条响应）】")
        for item in ledger.parameters[:150]:
            value = f"：{item.value}" if item.value else ""
            lines.append(f"- {item.name}{value}")

    if ledger.requirements:
        lines.append("\n【实质性/资格要求】")
        for item in ledger.requirements[:80]:
            mark = "（强制）" if item.mandatory else ""
            lines.append(f"- [{item.kind}]{mark} {item.text}")

    if ledger.risks:
        lines.append("\n【废标/否决风险】")
        for item in ledger.risks[:40]:
            lines.append(f"- [{item.level}] {item.text}")

    missing = [item for item in ledger.facts if item.required and not item.value]
    if missing:
        lines.append("\n【缺失必填事实（正文必须写 {{待填:字段名}}，不得猜测）】")
        for item in missing:
            lines.append(f"- {item.label}（{item.key}）")

    lines.append(
        "\n【写作口径】\n"
        "1. 只使用上述事实；没有来源的价格、证书编号、业绩、人员姓名、专利号、资质一律写 "
        "{{待填:具体信息}}。\n"
        "2. 数量与单位按台账原文；同一对象前后不得矛盾。\n"
        "3. 招标参数必须逐条响应，不得用「完全响应」「无偏离」代替具体响应值。\n"
        "4. 需要插图的位置只写一行 {{图片占位:图名与盖章/截图要求}}。\n"
        "5. 不把计划性措施写成企业已经具备的事实，不新增无依据的量化承诺。"
    )
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[:max_chars]
    return text


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
