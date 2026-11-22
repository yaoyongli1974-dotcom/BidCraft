"""把 :class:`~bidcraft.documents.document_ir.DocumentIR` 渲染成 DOCX。

所有 OXML 操作都收敛在本模块，避免污染 ``bidcraft.export.docx``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .document_ir import (
    DEFAULT_BODY_FONT,
    DEFAULT_HEADING_FONT,
    PLACEHOLDER_PATTERN,
    DocumentHeaderFooter,
    DocumentIR,
    DocumentPageLayout,
    DocumentParagraph,
    DocumentSection,
    DocumentStyle,
    DocumentTable,
    default_styles,
    heading_level,
)

_ALIGNMENTS = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "centre": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    "both": WD_ALIGN_PARAGRAPH.JUSTIFY,
}

_DEFAULT_WIDTH_CM = 15.0

# 样式名别名 → Word 内置样式名
_STYLE_ALIASES = {
    "body": "Normal",
    "正文": "Normal",
    "normal": "Normal",
    "h1": "Heading 1",
    "heading1": "Heading 1",
    "标题1": "Heading 1",
    "h2": "Heading 2",
    "heading2": "Heading 2",
    "标题2": "Heading 2",
    "h3": "Heading 3",
    "heading3": "Heading 3",
    "标题3": "Heading 3",
}


def _canonical_style_name(name: str) -> str:
    stripped = str(name or "").strip()
    if not stripped:
        return "Normal"
    alias = _STYLE_ALIASES.get(stripped.lower())
    if alias:
        return alias
    level = heading_level(stripped)
    if level is not None:
        return f"Heading {level}"
    return stripped


def _get_style(document, name: str, *, create: bool = False):
    for style in document.styles:
        if style.name == name:
            return style
    if create:
        return document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    return None


def _set_fonts(element, font_name: str) -> None:
    """同时写入 ascii / hAnsi / eastAsia / cs，确保中文不回退。"""

    run_properties = element.get_or_add_rPr()
    fonts = run_properties.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        run_properties.insert(0, fonts)
    for attribute in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attribute), font_name)


def _coerce_style(value: Any, name: str) -> DocumentStyle:
    if isinstance(value, DocumentStyle):
        if not value.name:
            value.name = name
        return value
    data = dict(value) if isinstance(value, dict) else {}
    return DocumentStyle(
        name=str(data.get("name") or name),
        font_name=str(data.get("font_name") or DEFAULT_BODY_FONT),
        font_size_pt=float(data.get("font_size_pt") or 12.0),
        bold=bool(data.get("bold", False)),
        line_spacing=data.get("line_spacing"),
        space_before=float(data.get("space_before") or 0.0),
        space_after=float(data.get("space_after") or 0.0),
    )


def _apply_styles(
    document,
    styles: dict[str, Any] | None,
    *,
    inherit_defaults: bool,
) -> None:
    specified = {_canonical_style_name(key): value for key, value in (styles or {}).items()}
    if inherit_defaults:
        # 模板继承时只覆盖调用方显式给出的样式，其余保持模板定义。
        if not specified:
            return
    else:
        merged = default_styles()
        merged.update(specified)
        specified = merged

    for name, value in specified.items():
        style = _get_style(document, name, create=True)
        if style is None:
            continue
        spec = _coerce_style(value, name)
        font_name = spec.font_name or (
            DEFAULT_HEADING_FONT if heading_level(name) else DEFAULT_BODY_FONT
        )
        style.font.name = font_name
        _set_fonts(style.element, font_name)
        if spec.font_size_pt:
            style.font.size = Pt(float(spec.font_size_pt))
        style.font.bold = bool(spec.bold)
        paragraph_format = style.paragraph_format
        if spec.line_spacing is not None:
            paragraph_format.line_spacing = float(spec.line_spacing)
        paragraph_format.space_before = Pt(float(spec.space_before or 0.0))
        paragraph_format.space_after = Pt(float(spec.space_after or 0.0))


def _clear_body(document) -> None:
    body = document.element.body
    for child in list(body):
        if child.tag == qn("w:sectPr"):
            continue
        body.remove(child)


def _update_fields_on_open(document) -> None:
    settings = document.settings.element
    update = settings.find(qn("w:updateFields"))
    if update is None:
        update = OxmlElement("w:updateFields")
        settings.append(update)
    update.set(qn("w:val"), "true")


def _add_field(paragraph, instruction: str, placeholder: str = "1") -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction_element = OxmlElement("w:instrText")
    instruction_element.set(qn("xml:space"), "preserve")
    instruction_element.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = placeholder
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instruction_element, separate, text, end):
        run._r.append(element)


def _add_plain_text(paragraph, part: str) -> None:
    lines = part.split("\n")
    for index, line in enumerate(lines):
        if index:
            paragraph.add_run().add_break()
        if line:
            paragraph.add_run(line)


def _add_text_runs(paragraph, text: Any) -> None:
    """写入文本，占位符 ``{{待填:...}}`` 原样保留并高亮。"""

    content = "" if text is None else str(text)
    if not content:
        return
    cursor = 0
    for match in PLACEHOLDER_PATTERN.finditer(content):
        if match.start() > cursor:
            _add_plain_text(paragraph, content[cursor : match.start()])
        run = paragraph.add_run(match.group(0))
        run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
        run.font.bold = True
        cursor = match.end()
    if cursor < len(content):
        _add_plain_text(paragraph, content[cursor:])


def _clear_paragraph(paragraph) -> None:
    for child in list(paragraph._p):
        if child.tag == qn("w:pPr"):
            continue
        paragraph._p.remove(child)


def _first_paragraph(container):
    if container.paragraphs:
        return container.paragraphs[0]
    return container.add_paragraph()


def _write_header(container, text: str) -> None:
    paragraph = _first_paragraph(container)
    _clear_paragraph(paragraph)
    _add_text_runs(paragraph, text)


def _write_footer(container, text: str, page_number: bool) -> None:
    paragraph = _first_paragraph(container)
    _clear_paragraph(paragraph)
    if text:
        _add_text_runs(paragraph, text)
        if page_number:
            paragraph.add_run("    ")
    if page_number:
        paragraph.add_run("第 ").font.size = Pt(9)
        _add_field(paragraph, "PAGE", "1")
        paragraph.add_run(" 页 / 共 ").font.size = Pt(9)
        _add_field(paragraph, "NUMPAGES", "1")
        paragraph.add_run(" 页").font.size = Pt(9)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _apply_header_footer(document, section, spec: DocumentHeaderFooter) -> None:
    if spec.header_text:
        _write_header(section.header, spec.header_text)
    if spec.footer_text or spec.page_number:
        _write_footer(section.footer, spec.footer_text, spec.page_number)

    if spec.different_first_page:
        section.different_first_page_header_footer = True
        if spec.header_text:
            _write_header(section.first_page_header, spec.header_text)
        if spec.footer_text or spec.page_number:
            _write_footer(section.first_page_footer, spec.footer_text, spec.page_number)

    if spec.different_odd_even:
        document.settings.odd_and_even_pages_header_footer = True
        if spec.header_text:
            _write_header(section.even_page_header, spec.header_text)
        if spec.footer_text or spec.page_number:
            _write_footer(section.even_page_footer, spec.footer_text, spec.page_number)


def _apply_page_layout(
    section,
    layout: DocumentPageLayout | None,
    *,
    landscape: bool = False,
) -> None:
    if layout is None:
        # 未显式给出页面设置时继承模板（或默认）方向，仅按需把本节转横向。
        if landscape:
            _set_orientation(section, landscape=True)
        return

    want_landscape = bool(landscape or layout.is_landscape)
    width = float(layout.page_width_cm or 21.0)
    height = float(layout.page_height_cm or 29.7)
    if want_landscape:
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = Cm(max(width, height))
        section.page_height = Cm(min(width, height))
    else:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Cm(min(width, height))
        section.page_height = Cm(max(width, height))
    section.top_margin = Cm(float(layout.margin_top_cm))
    section.bottom_margin = Cm(float(layout.margin_bottom_cm))
    section.left_margin = Cm(float(layout.margin_left_cm))
    section.right_margin = Cm(float(layout.margin_right_cm))


def _set_orientation(section, *, landscape: bool) -> None:
    width = section.page_width.cm if section.page_width is not None else 21.0
    height = section.page_height.cm if section.page_height is not None else 29.7
    if landscape:
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = Cm(max(width, height))
        section.page_height = Cm(min(width, height))
    else:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Cm(min(width, height))
        section.page_height = Cm(max(width, height))


def _render_paragraph(document, spec: DocumentParagraph):
    style_name = _canonical_style_name(spec.style)
    level = int(spec.level or heading_level(style_name) or 0)
    if 1 <= level <= 9:
        paragraph = document.add_heading("", level=level)
    else:
        paragraph = document.add_paragraph()
        style = _get_style(document, style_name)
        if style is not None:
            paragraph.style = style
    _add_text_runs(paragraph, spec.text)
    alignment = _ALIGNMENTS.get(str(spec.alignment or "").strip().lower())
    if alignment is not None:
        paragraph.alignment = alignment
    return paragraph


def _mark_header_row(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def _prevent_row_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:cantSplit")) is None:
        tr_pr.append(OxmlElement("w:cantSplit"))


def _set_fixed_layout(table) -> None:
    table_properties = table._tbl.tblPr
    layout = table_properties.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        table_properties.append(layout)
    layout.set(qn("w:type"), "fixed")


def _render_table(document, spec: DocumentTable, index: int) -> None:
    rows = [list(row) for row in (spec.rows or []) if row]
    if not rows:
        return
    if spec.caption:
        caption = document.add_paragraph()
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _add_text_runs(caption, spec.caption)
        for run in caption.runs:
            run.font.size = Pt(10)
            run.font.bold = True
        caption.paragraph_format.space_before = Pt(6)
        caption.paragraph_format.space_after = Pt(3)

    columns = max(len(row) for row in rows)
    table = document.add_table(rows=len(rows), cols=columns)
    if _get_style(document, "Table Grid") is not None:
        table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    if spec.widths_cm:
        table.autofit = False
        _set_fixed_layout(table)
        for row in table.rows:
            for column_index, cell in enumerate(row.cells):
                if column_index < len(spec.widths_cm):
                    cell.width = Cm(float(spec.widths_cm[column_index]))

    for row_index, values in enumerate(rows):
        for column_index in range(columns):
            value = values[column_index] if column_index < len(values) else ""
            cell = table.cell(row_index, column_index)
            paragraph = cell.paragraphs[0]
            _add_text_runs(paragraph, value)
            if row_index == 0 and spec.header_row:
                for run in paragraph.runs:
                    run.font.bold = True
                    run.font.size = Pt(10)

    if spec.header_row and spec.repeat_header:
        _mark_header_row(table.rows[0])
    for row in table.rows:
        _prevent_row_split(row)


def _render_images(document, images: list[Any]) -> None:
    for item in images or []:
        if isinstance(item, str):
            path, alt, width_cm = item, "", _DEFAULT_WIDTH_CM
        else:
            data = dict(item)
            path = str(data.get("path") or "")
            alt = str(data.get("alt") or "")
            width_cm = float(data.get("width_cm") or _DEFAULT_WIDTH_CM)
        image_path = Path(path) if path else None
        if image_path is not None and image_path.exists():
            try:
                document.add_picture(str(image_path), width=Cm(width_cm))
            except Exception:  # noqa: BLE001 - 图片损坏时降级为占位文字
                paragraph = document.add_paragraph()
                _add_text_runs(paragraph, f"{{{{图片占位:{alt or path}}}}}")
                continue
            document.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            if alt:
                caption = document.add_paragraph()
                caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _add_text_runs(caption, alt)
                for run in caption.runs:
                    run.font.size = Pt(9)
                    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
            continue
        paragraph = document.add_paragraph()
        _add_text_runs(paragraph, f"{{{{图片占位:{alt or path or '未提供'}}}}}")
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _render_section(document, spec: DocumentSection) -> None:
    created = []
    if spec.heading:
        level = int(spec.level or 1)
        heading = document.add_heading("", level=max(1, min(level, 9)))
        _add_text_runs(heading, spec.heading)
        created.append(heading)
    for paragraph in spec.paragraphs:
        created.append(_render_paragraph(document, paragraph))
    for table_index, table in enumerate(spec.tables, start=1):
        _render_table(document, table, table_index)
    _render_images(document, spec.images)
    if spec.page_break_before and created:
        created[0].paragraph_format.page_break_before = True


def _apply_core_properties(document, properties: dict[str, Any]) -> None:
    core = document.core_properties
    for key in ("title", "author", "subject", "keywords", "comments", "category"):
        value = properties.get(key)
        if value:
            setattr(core, key, str(value))


def render_document(
    ir: DocumentIR,
    path: str | Path,
    *,
    base_template: Path | str | None = None,
) -> Path:
    """按 IR 渲染 DOCX，返回输出路径。

    ``base_template`` 存在时继承模板的样式/主题/页眉页脚/页面设置，清空正文后
    再按 IR 重建；不存在时使用 python-docx 默认文档。
    """

    if base_template is not None:
        template_path = Path(base_template)
        if not template_path.exists():
            raise FileNotFoundError(f"基础模板不存在：{template_path}")
        document = Document(str(template_path))
    else:
        document = Document()

    _clear_body(document)
    _update_fields_on_open(document)
    _apply_styles(document, ir.styles, inherit_defaults=base_template is not None)

    sections = list(ir.sections) or [DocumentSection()]
    inherited_layout = ir.page_layout
    if inherited_layout is None and base_template is None:
        # 无模板时给中文文档一个合理的 A4 默认版式。
        inherited_layout = DocumentPageLayout()
    for index, section_spec in enumerate(sections):
        if index == 0:
            section = document.sections[0]
        else:
            section = document.add_section(WD_SECTION.NEW_PAGE)
        _apply_page_layout(
            section,
            inherited_layout,
            landscape=bool(section_spec.landscape),
        )
        if ir.header_footer is not None:
            _apply_header_footer(document, section, ir.header_footer)
        _render_section(document, section_spec)

    _apply_core_properties(document, ir.core_properties or {})

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output))
    return output


__all__ = ["render_document"]
