"""商务标 Word 合订本：复用 export.docx 的样式与表格能力。

内容由 :mod:`bidcraft.commercial` 装配，本模块只负责排版；未填字段保留
``{{待填:...}}``，不生成任何价格、资质或人员信息。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.shared import Cm, Pt

from .docx import (
    _add_page_number_footer,
    _add_table,
    _set_run_font,
    _setup_styles,
    _update_fields_on_open,
)
from .templates import normalize

SECTION_ORDER = (
    ("bid_letter", "投标函"),
    ("opening_sheet", "开标一览表"),
    ("price_schedule", "分项报价表"),
    ("qualification_checklist", "资格响应表"),
    ("performance_table", "业绩表"),
    ("personnel_table", "人员表"),
    ("deviation_table", "商务偏离表"),
)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _cover(document: Document, payload: dict[str, Any]) -> None:
    project = payload.get("project") or {}
    quote = payload.get("quote") or {}
    for _ in range(4):
        document.add_paragraph()
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(_text(project.get("name")) or "{{待填:招标项目名称}}")
    run.font.size = Pt(24)
    run.font.bold = True
    _set_run_font(run, "黑体")

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = subtitle.add_run("商 务 标")
    sub_run.font.size = Pt(36)
    sub_run.font.bold = True
    _set_run_font(sub_run, "黑体")

    document.add_paragraph()
    info = [
        ["项目编号", _text(project.get("number")) or "{{待填:项目编号}}"],
        ["招标人", _text(project.get("buyer")) or "{{待填:招标人}}"],
        ["投标人", "{{待填:投标人名称}}"],
        ["报价版本", _text(quote.get("name")) or "{{待填:已审批报价版本}}"],
        ["审批人", _text(quote.get("reviewed_by")) or "{{待填:审批人}}"],
        ["审批时间", _text(quote.get("reviewed_at")) or "{{待填:审批时间}}"],
        ["日期", "{{待填:编制日期}}"],
    ]
    # 投标人优先从投标函字段取（可能来自事实台账）。
    letter_fields = {
        entry.get("key"): entry.get("value")
        for entry in ((payload.get("sections") or {}).get("bid_letter") or {}).get(
            "fields"
        )
        or []
        if isinstance(entry, dict)
    }
    bidder = _text(letter_fields.get("bidder"))
    if bidder and not bidder.startswith("{{待填:"):
        info[2][1] = bidder
    _add_table(document, info)


def _add_fields(document: Document, section: dict[str, Any]) -> None:
    rows = [
        [entry.get("label") or entry.get("key") or "", _text(entry.get("value"))]
        for entry in section.get("fields") or []
        if isinstance(entry, dict)
    ]
    if rows:
        _add_table(document, [["字段", "内容"], *rows])


def _add_section_table(document: Document, section: dict[str, Any]) -> None:
    columns = [entry for entry in section.get("columns") or [] if isinstance(entry, dict)]
    headers = [entry.get("label") or entry.get("key") or "" for entry in columns]
    keys = [str(entry.get("key") or "") for entry in columns]
    rows = [
        [_text(row.get(key)) for key in keys]
        for row in section.get("rows") or []
        if isinstance(row, dict)
    ]
    if not headers:
        return
    _add_table(document, [headers, *rows])
    totals = section.get("totals") or {}
    if section.get("key") == "price_schedule" and totals.get("total_amount") is not None:
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = paragraph.add_run(f"合计：{totals['total_amount']}")
        run.font.bold = True


def export_commercial_docx(
    path: str | Path, payload: dict[str, Any], *, template: dict | None = None
) -> Path:
    """Write the commercial bid DOCX and return its path."""
    preset = normalize(template)
    document = Document()
    section = document.sections[0]
    top, bottom, left, right = preset["margins"]
    section.page_height = Cm(29.7)
    section.page_width = Cm(21.0)
    section.top_margin = Cm(top)
    section.bottom_margin = Cm(bottom)
    section.left_margin = Cm(left)
    section.right_margin = Cm(right)
    _setup_styles(document, preset)
    if preset["page_numbers"]:
        _add_page_number_footer(section)
    _update_fields_on_open(document)

    _cover(document, payload)
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    sections = payload.get("sections") or {}
    for key, title in SECTION_ORDER:
        data = sections.get(key) or {}
        document.add_heading(title, level=1)
        _add_fields(document, data)
        if data.get("kind") == "table":
            _add_section_table(document, data)
        for note in data.get("notes") or []:
            paragraph = document.add_paragraph()
            run = paragraph.add_run(_text(note))
            run.font.size = Pt(9)
        document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    validation = payload.get("validation") or {}
    if not validation.get("ok"):
        document.add_heading("编制校验提示", level=1)
        for error in validation.get("errors") or []:
            document.add_paragraph(_text(error), style="List Bullet")

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output))
    return output
