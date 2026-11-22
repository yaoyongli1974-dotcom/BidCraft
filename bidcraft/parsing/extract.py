from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..rules.loader import FieldSpec, Package
from ..schemas import MaterialItem, ParameterItem, RequirementItem, RiskItem, ScoringItemModel
from .clean import detect_sections, is_heading, section_slices, strip_noise
from .loader import Table, compact


@dataclass
class ExtractedField:
    key: str
    value: str
    quote: str = ""
    confidence: float = 0.0
    page: int = 0


@dataclass
class ExtractionResult:
    fields: dict[str, ExtractedField] = field(default_factory=dict)
    materials: list[MaterialItem] = field(default_factory=list)
    parameters: list[ParameterItem] = field(default_factory=list)
    scoring: list[ScoringItemModel] = field(default_factory=list)
    requirements: list[RequirementItem] = field(default_factory=list)
    risks: list[RiskItem] = field(default_factory=list)
    template_sections: list[str] = field(default_factory=list)
    sections: list[dict[str, str]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


# 真实招标文件里的同义标签补充。只追加到规则包已有 label/aliases 之后，
# 不覆盖、不删除既有别名；键为规则包字段 key，未命中的 key 会被忽略。
FIELD_ALIAS_AUGMENT: dict[str, tuple[str, ...]] = {
    "project.number": ("项目编号", "招标编号", "采购编号", "标段编号"),
    "tender.number": ("招标编号", "采购编号", "标段编号"),
    "project.buyer": ("招标人", "采购人", "建设单位", "业主"),
    "project.duration": ("工期", "计划工期", "服务期", "交货期"),
    "project.warranty": ("质保", "缺陷责任期", "免费维保期", "质量保证期"),
    "project.deposit": ("投标保证金", "磋商保证金"),
    "project.bid_validity": ("投标有效期", "报价有效期"),
}


def augmented_labels(spec: FieldSpec) -> list[str]:
    """Return a field's labels plus additive real-tender aliases (deduplicated)."""
    extras = FIELD_ALIAS_AUGMENT.get(spec.key, ())
    if not extras:
        return spec.all_labels
    return list(dict.fromkeys([*spec.all_labels, *extras]))


_MASKED = re.compile(r"^[\s*＊#＃xX×\-—_]+$")
_LEAD_JUNK = re.compile(r"^[\s:：\-—–~·、,，;；.．*#]+")
_TAIL_JUNK = re.compile(r"[\s:：\-—–~·、,，;；.．]+$")
_PROXY = re.compile(r"^(?:请)?(?:参见|详见|见|按照|按|以).{0,30}(?:前附表|附表|须知|章节|第.{1,8}章|公告|为准)")
_DEFINITION = re.compile(r"^(?:即|系指|是指).{0,40}(?:项目|工程|采购).{0,20}(?:名称|见)")


def flex(label: str) -> str:
    chars = [re.escape(ch) for ch in label if not ch.isspace()]
    return r"\s*".join(chars)


def _all_label_pattern(field_specs: Iterable[FieldSpec]) -> re.Pattern[str]:
    labels: list[str] = []
    for spec in field_specs:
        labels.extend(augmented_labels(spec))
    labels = sorted(set(labels), key=len, reverse=True)
    joined = "|".join(flex(label) for label in labels)
    return re.compile(rf"(?:{joined})\s*[:：]")


def clean_value(value: str, spec: FieldSpec | None = None) -> str:
    text = (value or "").replace("\u3000", " ")
    text = _LEAD_JUNK.sub("", text.strip())
    text = _TAIL_JUNK.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    if spec is not None:
        for pattern in spec.reject_patterns:
            if re.search(pattern, text):
                return ""
        if spec.max_len and len(text) > spec.max_len:
            cut = re.split(r"[。；;，,、]", text, maxsplit=1)
            text = cut[0][: spec.max_len]
    if _MASKED.match(text) or _PROXY.match(text) or _DEFINITION.match(text):
        return ""
    if text in {"无", "／", "/", "-", "--"}:
        return ""
    return text.strip()


def _type_ok(spec: FieldSpec, value: str) -> bool:
    if not value:
        return False
    kind = spec.type
    if kind == "money":
        return bool(re.search(r"\d", value)) and bool(re.search(r"元|万元|亿元|%", value))
    if kind == "datetime":
        return bool(re.search(r"\d{4}\s*年|\d{1,2}\s*[:：]\s*\d{2}|\d{4}-\d{1,2}-\d{1,2}", value))
    if kind == "duration":
        return bool(re.search(r"\d", value))
    if kind == "number":
        return bool(re.search(r"\d", value))
    return len(value) >= 2


def _cut_at_next_label(value: str, label_re: re.Pattern[str]) -> str:
    match = label_re.search(value)
    if match and match.start() > 0:
        return value[: match.start()].strip()
    return value


def _looks_like_label_line(line: str, label_re: re.Pattern[str]) -> bool:
    return bool(label_re.match(line.strip()))


def _next_value_line(lines: list[str], index: int, label_re: re.Pattern[str]) -> str:
    for j in range(index + 1, min(index + 4, len(lines))):
        candidate = lines[j].strip()
        if not candidate:
            continue
        if _looks_like_label_line(candidate, label_re):
            return ""
        if is_heading(candidate):
            return ""
        if _MASKED.match(candidate):
            continue
        return candidate
    return ""


def _page_for_quote(pages: list[str], quote: str) -> int:
    needle = compact(quote)[:40]
    if not needle:
        return 0
    for index, page in enumerate(pages, start=1):
        if needle in compact(page):
            return index
    return 0


def extract_fields(
    text: str, pages: list[str], package: Package
) -> tuple[dict[str, ExtractedField], list[dict[str, str]]]:
    lines = text.splitlines()
    label_re = _all_label_pattern(package.fields)
    sections = detect_sections(text, package.heading_patterns())
    result: dict[str, ExtractedField] = {}

    for spec in package.fields:
        best: ExtractedField | None = None
        for alias in augmented_labels(spec):
            pattern = re.compile(rf"{flex(alias)}\s*[:：]?\s*(.*)$")
            for index, line in enumerate(lines):
                match = pattern.search(line.strip())
                if not match:
                    continue
                # avoid matching a label embedded in a long sentence unless it has a colon
                if (
                    match.start() > 12
                    and not re.search(r"[:：]", line[: match.start() + 1])
                    and len(line.strip()) > 80
                ):
                    continue
                value = clean_value(match.group(1), spec)
                value = _cut_at_next_label(value, label_re)
                if not value:
                    value = clean_value(_next_value_line(lines, index, label_re), spec)
                if not value or not _type_ok(spec, value):
                    continue
                matched_segment = line.strip()[match.start() : match.end()]
                has_colon = ":" in matched_segment or "：" in matched_segment
                confidence = 0.97 if has_colon else 0.72
                candidate = ExtractedField(
                    key=spec.key,
                    value=value,
                    quote=line.strip()[:300],
                    confidence=confidence,
                    page=_page_for_quote(pages, line.strip()),
                )
                if best is None or candidate.confidence > best.confidence:
                    best = candidate
            if best is not None:
                break

        if best is None:
            fallback = _section_fallback(spec, sections)
            if fallback:
                best = fallback

        if best is not None:
            result[spec.key] = best

    if "project.content" not in result:
        tech = result.get("tech.requirements")
        if tech and re.search(r"本项目|建设范围|采购范围|建设内容|包括", tech.value):
            result["project.content"] = ExtractedField(
                key="project.content",
                value=tech.value,
                quote=tech.quote,
                confidence=0.55,
                page=tech.page,
            )

    return result, sections


def _section_fallback(spec: FieldSpec, sections: list[dict[str, str]]) -> ExtractedField | None:
    """Long-text fields are often introduced by a heading rather than a label."""
    if spec.type not in {"text", "entity"}:
        return None
    keys = augmented_labels(spec)
    for section in sections:
        heading = section.get("heading") or ""
        if not any(key and key in heading for key in keys):
            continue
        body = (section.get("body") or "").strip()
        if not body:
            continue
        excerpt = "\n".join(body.splitlines()[:12]).strip()
        value = clean_value(excerpt[:800], spec)
        if value:
            return ExtractedField(
                key=spec.key, value=value, quote=heading, confidence=0.6, page=0
            )
    return None


def _table_kind(table: Table, hints: dict[str, list[str]]) -> str | None:
    header = " ".join(table.header())
    if not header:
        return None
    for kind, words in hints.items():
        if any(word and word in header for word in words):
            return kind
    return None


def _find_col(header: list[str], words: list[str], exclude: list[str] | None = None) -> int | None:
    exclude = exclude or []
    for index, cell in enumerate(header):
        if any(word in cell for word in words) and not any(word in cell for word in exclude):
            return index
    return None


def _num(value: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", (value or "").replace(",", ""))
    return float(match.group(0)) if match else None


def extract_scoring(tables: list[Table], text: str) -> list[ScoringItemModel]:
    items: list[ScoringItemModel] = []
    seen: set[str] = set()

    for table in tables:
        header = table.header()
        name_col = _find_col(header, ["评审因素", "评分因素", "评分项", "评审项目", "评分项目", "内容"])
        score_col = _find_col(header, ["分值", "分数", "得分", "权重"])
        criteria_col = _find_col(header, ["评分标准", "评审标准", "评分细则", "标准", "说明"], exclude=["分值"])
        if name_col is None and score_col is None:
            continue
        for row in table.as_dicts():
            values = list(row.values())
            name = values[name_col].strip() if name_col is not None and name_col < len(values) else ""
            score_text = values[score_col].strip() if score_col is not None and score_col < len(values) else ""
            if not name or name in seen:
                continue
            if re.search(r"合计|总分|满分", name):
                continue
            score = _num(score_text)
            if score is None and name_col is None:
                continue
            criteria = ""
            if criteria_col is not None and criteria_col < len(values):
                criteria = values[criteria_col].strip()
            seen.add(name)
            items.append(
                ScoringItemModel(
                    name=name[:120],
                    score=score,
                    criteria=criteria[:500],
                    source_quote=" | ".join(values[:8])[:300],
                )
            )

    if items:
        return items[:80]

    # Text fallback: "技术方案 30 分" / "1. 技术方案（30分）"
    pattern = re.compile(
        r"^\s*(?:\d{1,2}[、.．)]?\s*)?([^\d\s][^|\n]{1,40}?)\s*[（(]?\s*(\d{1,3}(?:\.\d{1,2})?)\s*分"
    )
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) > 120 or re.search(r"合计|总分|满分", stripped):
            continue
        match = pattern.search(stripped)
        if not match:
            continue
        name = clean_value(match.group(1))
        if not name or name in seen or len(name) < 2:
            continue
        seen.add(name)
        items.append(
            ScoringItemModel(
                name=name[:120],
                score=float(match.group(2)),
                criteria=stripped[:400],
                source_quote=stripped[:300],
            )
        )
    return items[:80]


def extract_materials(tables: list[Table], text: str) -> list[MaterialItem]:
    items: list[MaterialItem] = []
    for table in tables:
        header = table.header()
        name_col = _find_col(header, ["名称", "设备", "材料", "项目", "货物"], exclude=["规格", "型号"])
        spec_col = _find_col(header, ["规格", "型号", "参数", "品牌"])
        unit_col = _find_col(header, ["单位"])
        qty_col = _find_col(header, ["数量", "工程量"])
        if name_col is None or (qty_col is None and unit_col is None):
            continue
        for row in table.as_dicts():
            values = list(row.values())
            name = values[name_col].strip() if name_col < len(values) else ""
            if not name or re.search(r"^(?:合计|小计|总计|序号)$", name):
                continue
            items.append(
                MaterialItem(
                    name=name[:200],
                    spec=(values[spec_col].strip() if spec_col is not None and spec_col < len(values) else "")[:200],
                    unit=(values[unit_col].strip() if unit_col is not None and unit_col < len(values) else "")[:20],
                    qty=_num(values[qty_col]) if qty_col is not None and qty_col < len(values) else None,
                    source_quote=" | ".join(values[:8])[:300],
                )
            )
    return items[:400]


def extract_parameters(tables: list[Table], text: str, vocab: list[str]) -> list[ParameterItem]:
    items: list[ParameterItem] = []
    seen: set[str] = set()
    for table in tables:
        header = table.header()
        name_col = _find_col(header, ["参数", "指标", "名称", "项目", "内容"], exclude=["序号"])
        value_col = _find_col(header, ["响应", "值", "要求", "指标", "规格"], exclude=["序号", "名称"])
        if name_col is None:
            continue
        if len(header) < 2:
            continue
        for row in table.as_dicts():
            values = list(row.values())
            name = values[name_col].strip() if name_col < len(values) else ""
            if not name or name in seen or len(name) < 2:
                continue
            if value_col is not None and value_col < len(values):
                value = values[value_col].strip()
            else:
                value = next((v for i, v in enumerate(values) if i != name_col and v.strip()), "")
            seen.add(name)
            items.append(
                ParameterItem(
                    name=name[:200],
                    value=value[:300],
                    requirement=value[:500],
                    source_quote=" | ".join(values[:8])[:300],
                )
            )

    if not items and vocab:
        vocab_re = re.compile("|".join(re.escape(word) for word in vocab))
        for line in text.splitlines():
            stripped = line.strip()
            if not (2 <= len(stripped) <= 160):
                continue
            if not vocab_re.search(stripped) or not re.search(r"\d", stripped):
                continue
            if re.match(r"^第?\d+(?:\.\d+)*[、.．\s]", stripped) and len(stripped) < 8:
                continue
            parts = re.split(r"[:：]", stripped, maxsplit=1)
            if len(parts) == 2:
                name, value = parts[0], parts[1]
            else:
                name = value = stripped
            if name in seen:
                continue
            seen.add(name)
            items.append(
                ParameterItem(
                    name=name.strip()[:200],
                    value=value.strip()[:300],
                    requirement=stripped[:500],
                    source_quote=stripped[:300],
                )
            )
    return items[:300]


_REQ_SECTION = re.compile(r"资格|资质|实质性|废标|否决|无效|商务要求|技术要求|符合性|评标|评审|评分")
# ★/▲ 是招标文件里最常见的「实质性条款」标记，必须保留符号本身而不只是布尔。
# 条款编号：1.2.3 / 第X条 / （一）/ 3.2.1 等招标文件常见写法。
_CLAUSE_NO_RE = re.compile(r"(?:第[一二三四五六七八九十百\d]+条|\d+(?:\.\d+){1,3}|[（(][一二三四五六七八九十\d]+[)）])")

_REQ_MARKER = re.compile(r"[★▲]")
_REQ_LINE = re.compile(
    r"^(?:\d{1,2}[、.．)]|[（(]\d{1,2}[）)]|[（(][一二三四五六七八九十]{1,3}[）)]|[-*•●◆■★▲])\s*"
)


def extract_requirements_and_risks(
    text: str, package: Package, pages: list[str] | None = None
) -> tuple[list[RequirementItem], list[RiskItem]]:
    pages = pages or [text]
    requirements: list[RequirementItem] = []
    risks: list[RiskItem] = []
    seen_req: set[str] = set()
    seen_risk: set[str] = set()

    slices = section_slices(text, package.heading_patterns())
    for heading, body, _ in slices:
        if not _REQ_SECTION.search(heading):
            continue
        kind = "qualification" if re.search(r"资格|资质", heading) else "substantive"
        for line in body.splitlines():
            stripped = line.strip()
            if not (6 <= len(stripped) <= 500):
                continue
            if not (_REQ_LINE.search(stripped) or re.search(r"必须|不得|应(?:当)?|须|★|▲", stripped)):
                continue
            if stripped in seen_req:
                continue
            seen_req.add(stripped)
            marker_match = _REQ_MARKER.search(stripped)
            clause_match = _CLAUSE_NO_RE.search(stripped)
            requirements.append(
                RequirementItem(
                    kind=kind,
                    text=stripped[:500],
                    mandatory=bool(re.search(r"必须|不得|★|▲|废标|否决|无效", stripped)),
                    # 原文行比章节标题有用：总览页要能指着这一条说「就是它没响应」。
                    source_quote=stripped[:300],
                    marker=marker_match.group(0) if marker_match else "",
                    page=_page_for_quote(pages, stripped),
                    clause_no=clause_match.group(0) if clause_match else "",
                )
            )
            if len(requirements) >= 120:
                break

    for line in text.splitlines():
        stripped = line.strip()
        if not (6 <= len(stripped) <= 400) or is_heading(stripped):
            continue
        if not re.search(r"废标|否决|无效投标|不予接受|拒绝其投标|按无效|★|▲", stripped):
            continue
        if stripped in seen_risk:
            continue
        seen_risk.add(stripped)
        level = "blocking" if re.search(r"废标|否决|无效", stripped) else "major"
        risks.append(RiskItem(level=level, text=stripped[:400], source_quote=stripped[:200]))
        if len(risks) >= 60:
            break

    return requirements, risks


_TEMPLATE_HEAD = re.compile(r"投标文件(?:的)?组成|响应文件(?:的)?组成|投标文件格式|文件组成|格式要求")


def extract_template_sections(text: str, package: Package) -> list[str]:
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if len(stripped) <= 80 and _TEMPLATE_HEAD.search(stripped):
            start = index
            break
    if start is None:
        return []

    headings: list[str] = []
    for line in lines[start + 1 : start + 160]:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^第[一二三四五六七八九十百0-9]+章", stripped):
            break
        if (
            re.match(r"^第[一二三四五六七八九十百0-9]+部分", stripped) or is_heading(stripped)
        ) and 2 <= len(stripped) <= 60:
            headings.append(stripped)
    return list(dict.fromkeys(headings))[:60]


def extract(
    raw_text: str, package: Package, *, pages: list[str] | None = None, tables: list[Table] | None = None
) -> ExtractionResult:
    text = strip_noise(raw_text)
    pages = pages or [raw_text]
    tables = tables or []
    fields, sections = extract_fields(text, pages, package)
    result = ExtractionResult(fields=fields, sections=sections)
    result.scoring = extract_scoring(tables, text)
    result.materials = extract_materials(tables, text)
    result.parameters = extract_parameters(tables, text, package.parameter_vocab())
    result.requirements, result.risks = extract_requirements_and_risks(text, package, pages)
    result.template_sections = extract_template_sections(text, package)
    result.stats = {
        "chars": len(text),
        "sections": len(sections),
        "tables": len(tables),
        "materials": len(result.materials),
        "parameters": len(result.parameters),
        "scoring": len(result.scoring),
        "requirements": len(result.requirements),
        "risks": len(result.risks),
        "template_sections": len(result.template_sections),
        "fields": len(fields),
    }
    return result
