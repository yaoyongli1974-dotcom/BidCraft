from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..constraint_check import _VALUE_RE
from ..generation.outline import is_scoring_noise, normalize_scoring
from ..rules.loader import FieldSpec, Package
from ..schemas import MaterialItem, ParameterItem, RequirementItem, RiskItem, ScoringItemModel
from .clean import (
    detect_sections,
    is_heading,
    is_material_noise,
    normalize_material_key,
    section_slices,
    strip_noise,
)
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


_WS_RE = re.compile(r"\s+")


def _compact_needle(quote: str) -> str:
    """引用的匹配键。刻意不调用 :func:`compact`：紧凑页只应在每轮解析里算一次。"""

    return _WS_RE.sub("", quote or "")[:40]


def _prepare_pages(pages: list[str]) -> list[str]:
    """一次把每页压成紧凑形式，供所有引用定位复用。"""

    return [compact(page) for page in pages]


def _page_for_quote(compacted_pages: list[str], quote: str) -> int:
    needle = _compact_needle(quote)
    if not needle:
        return 0
    for index, page in enumerate(compacted_pages, start=1):
        if needle in page:
            return index
    return 0


def extract_fields(
    text: str,
    pages: list[str],
    package: Package,
    *,
    compacted_pages: list[str] | None = None,
) -> tuple[dict[str, ExtractedField], list[dict[str, str]]]:
    compacted_pages = _prepare_pages(pages) if compacted_pages is None else compacted_pages
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
                    page=_page_for_quote(compacted_pages, line.strip()),
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


_NUM_SCORE_RE = re.compile(r"^\d+(?:\.\d+)?\s*分?$")


def _num(value: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", (value or "").replace(",", ""))
    return float(match.group(0)) if match else None


def _score_text(value: str) -> str:
    """评分单元格必须是明确分值，避免把清单金额/日期误当评分。"""

    cleaned = re.sub(r"\s+", "", clean_value(str(value or "")))
    return cleaned if _NUM_SCORE_RE.fullmatch(cleaned) else ""


def _score_name(value: str) -> str:
    name = clean_value(str(value or "")).strip()
    if is_scoring_noise(name):
        return ""
    return name[:120]


def _score_category(name: str) -> str:
    if re.search(r"价格|报价|金额|费率", name):
        return "价格"
    if re.search(r"商务|资质|业绩|人员|合同|付款|承诺|管理|质保|售后", name):
        return "商务"
    if re.search(r"技术|方案|施工|实施|设计|参数|设备|系统|服务|运维|质量|点位", name):
        return "技术"
    return ""


def _normalize_scoring_models(items: list[ScoringItemModel]) -> list[ScoringItemModel]:
    rows, _ = normalize_scoring([item.model_dump() for item in items])
    return [ScoringItemModel(**row) for row in rows[:80]]


def extract_scoring(tables: list[Table], text: str) -> list[ScoringItemModel]:
    items: list[ScoringItemModel] = []

    for table in tables:
        header = table.header()
        name_col = _find_col(header, ["评审因素", "评分因素", "评分项", "评审项目", "评分项目", "内容"])
        score_col = _find_col(header, ["分值", "分数", "得分", "权重"])
        criteria_col = _find_col(header, ["评分标准", "评审标准", "评分细则", "标准", "说明"], exclude=["分值"])
        if name_col is None and score_col is None:
            continue
        for row in table.as_dicts():
            values = list(row.values())
            name = _score_name(values[name_col] if name_col is not None and name_col < len(values) else "")
            score_text = _score_text(values[score_col] if score_col is not None and score_col < len(values) else "")
            if not name or not score_text:
                continue
            if re.search(r"合计|总分|满分", name):
                continue
            score = _num(score_text)
            if score is None:
                continue
            category = _score_category(name)
            criteria = ""
            if criteria_col is not None and criteria_col < len(values):
                criteria = clean_value(str(values[criteria_col] or ""))[:500]
            items.append(
                ScoringItemModel(
                    name=name,
                    score=score,
                    criteria=criteria,
                    category=category,
                    section_hint="",
                    source_quote=" | ".join(str(value or "") for value in values[:8])[:300],
                )
            )

    if items:
        return _normalize_scoring_models(items)

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
        name = _score_name(match.group(1))
        if not name:
            continue
        items.append(
            ScoringItemModel(
                name=name,
                score=float(match.group(2)),
                criteria=clean_value(stripped)[:400],
                category=_score_category(name),
                section_hint="",
                source_quote=stripped[:300],
            )
        )
    return _normalize_scoring_models(items)


def extract_materials(tables: list[Table], text: str) -> list[MaterialItem]:
    merged: dict[str, MaterialItem] = {}
    order: list[str] = []
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
            name = clean_value(str(values[name_col] or "")) if name_col < len(values) else ""
            spec = (
                clean_value(str(values[spec_col] or ""))
                if spec_col is not None and spec_col < len(values)
                else ""
            )
            if not name or re.search(r"^(?:合计|小计|总计|序号)$", name):
                continue
            if is_material_noise(name, spec):
                continue
            key = normalize_material_key(name, spec)
            qty = _num(str(values[qty_col] or "")) if qty_col is not None and qty_col < len(values) else None
            if key in merged:
                existing = merged[key]
                if existing.qty is None or (qty is not None and qty > existing.qty):
                    existing.qty = qty
                continue
            merged[key] = MaterialItem(
                name=name[:200],
                spec=spec[:200],
                unit=(
                    clean_value(str(values[unit_col] or ""))
                    if unit_col is not None and unit_col < len(values)
                    else ""
                )[:20],
                qty=qty,
                source_quote=" | ".join(str(value or "") for value in values[:8])[:300],
            )
            order.append(key)
    return [merged[key] for key in order][:400]


def _detect_parameter_unit(value: str) -> str:
    """从参数要求值里取「数字+单位」的单位（注册表 longest-first 匹配）。

    单位写进 ParameterItem 后，rules.expr 才能做同量纲数值比较；取不到就
    留空，参数响应退回字符串口径，不臆造量纲。
    """

    for match in _VALUE_RE.finditer(value or ""):
        if match.group(2):
            return match.group(2)
    return ""


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
                    unit=_detect_parameter_unit(value),
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
                    unit=_detect_parameter_unit(value),
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
    text: str,
    package: Package,
    pages: list[str] | None = None,
    tables: list[Table] | None = None,
    *,
    compacted_pages: list[str] | None = None,
) -> tuple[list[RequirementItem], list[RiskItem]]:
    pages = pages or [text]
    compacted_pages = _prepare_pages(pages) if compacted_pages is None else compacted_pages
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
                    page=_page_for_quote(compacted_pages, stripped),
                    clause_no=clause_match.group(0) if clause_match else "",
                )
            )
            if len(requirements) >= 120:
                break

    # 表格里的 ★/▲/必须/不得 条款过去只喂给参数/评分/材料提取，要求列表会
    # 整行漏掉（实质性条款因此不进导出门禁）。这里按行补提并与正文行去重。
    for table in tables or []:
        for row in table.as_dicts():
            cells = [str(value or "").strip() for value in row.values()]
            row_text = " | ".join(cell for cell in cells if cell)
            if not (6 <= len(row_text) <= 500):
                continue
            if not (_REQ_MARKER.search(row_text) or re.search(r"必须|不得", row_text)):
                continue
            # 同一要求在不同表格里常只换序号，序号不应参与去重。
            dedupe_key = re.sub(r"^\s*\d{1,3}\s*\|\s*", "", row_text)
            if dedupe_key in seen_req:
                continue
            seen_req.add(dedupe_key)
            marker_cell = next(
                (cell for cell in cells if _REQ_MARKER.search(cell) or re.search(r"必须|不得", cell)),
                row_text,
            )
            marker_match = _REQ_MARKER.search(row_text)
            clause_match = _CLAUSE_NO_RE.search(row_text)
            requirements.append(
                RequirementItem(
                    kind="substantive",
                    text=row_text[:500],
                    mandatory=True,
                    source_quote=row_text[:300],
                    marker=marker_match.group(0) if marker_match else "",
                    page=_page_for_quote(compacted_pages, marker_cell),
                    clause_no=clause_match.group(0) if clause_match else "",
                )
            )
            if len(requirements) >= 120:
                break
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
    # 引用定位过去每条候选都要 compact 每一页（页数 × 条款数）。整轮解析只做一次。
    compacted_pages = _prepare_pages(pages)
    fields, sections = extract_fields(text, pages, package, compacted_pages=compacted_pages)
    result = ExtractionResult(fields=fields, sections=sections)
    result.scoring = extract_scoring(tables, text)
    result.materials = extract_materials(tables, text)
    result.parameters = extract_parameters(tables, text, package.parameter_vocab())
    result.requirements, result.risks = extract_requirements_and_risks(
        text, package, pages, tables, compacted_pages=compacted_pages
    )
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
