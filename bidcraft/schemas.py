from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    file: str = ""
    page: int = 0
    quote: str = ""


class FactItem(BaseModel):
    key: str
    label: str = ""
    value: str = ""
    unit: str = ""
    status: Literal["extracted", "confirmed", "manual", "missing", "conflict"] = "extracted"
    confidence: float = 0.0
    required: bool = False
    source: SourceRef = Field(default_factory=SourceRef)


class MaterialItem(BaseModel):
    name: str
    spec: str = ""
    unit: str = ""
    qty: float | None = None
    note: str = ""
    source_quote: str = ""


class ParameterItem(BaseModel):
    name: str
    value: str = ""
    unit: str = ""
    requirement: str = ""
    category: str = ""
    mandatory: bool = True
    source_quote: str = ""


class ScoringItemModel(BaseModel):
    name: str
    score: float | None = None
    criteria: str = ""
    category: str = ""
    section_hint: str = ""
    source_quote: str = ""


class RequirementItem(BaseModel):
    kind: str = "substantive"
    text: str
    mandatory: bool = True
    source_quote: str = ""


class RiskItem(BaseModel):
    level: str = "major"
    text: str
    source_quote: str = ""


class FactLedger(BaseModel):
    """The C1 fact ledger: the only source of truth used by generation."""

    schema_version: str = "1.0"
    package: str = "weakcurrent-cabling"
    project: dict[str, Any] = Field(default_factory=dict)
    facts: list[FactItem] = Field(default_factory=list)
    materials: list[MaterialItem] = Field(default_factory=list)
    parameters: list[ParameterItem] = Field(default_factory=list)
    scoring: list[ScoringItemModel] = Field(default_factory=list)
    requirements: list[RequirementItem] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)
    template_sections: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)

    def fact_map(self) -> dict[str, FactItem]:
        return {item.key: item for item in self.facts}

    def value(self, key: str, default: str = "") -> str:
        item = self.fact_map().get(key)
        if item is None or item.status == "missing":
            return default
        return item.value or default

    def confirmed(self) -> list[FactItem]:
        return [f for f in self.facts if f.value and f.status in {"extracted", "confirmed", "manual"}]

    def missing_required(self) -> list[FactItem]:
        return [f for f in self.facts if f.required and not f.value]


class OutlineItem(BaseModel):
    heading: str
    intent: str = ""
    target_words: int = 1200
    needs: list[str] = Field(default_factory=list)
    children: list[OutlineItem] = Field(default_factory=list)


class OutlineSpec(BaseModel):
    title: str = ""
    mode: Literal["package", "scoring", "tender-format", "generic"] = "package"
    items: list[OutlineItem] = Field(default_factory=list)


class CheckResult(BaseModel):
    code: str
    level: Literal["blocking", "critical", "major", "minor", "info"] = "major"
    category: str = ""
    message: str = ""
    location: str = ""
    finding: str = ""
    recommendation: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    passed: bool = False
    #: 大纲锚定信息（check_project 填充；匹配不到时保持默认，旧调用方不受影响）。
    node_id: str | None = None
    section_id: str | None = None
    heading: str = ""
    position: int | None = None
