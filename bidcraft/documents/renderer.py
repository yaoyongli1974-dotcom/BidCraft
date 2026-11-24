"""把 :class:`~bidcraft.documents.document_ir.DocumentIR` 渲染成 DOCX。

所有 OXML 操作都收敛在本模块，避免污染 ``bidcraft.export.docx``。
"""

from __future__ import annotations

import copy
import os
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .document_ir import (
    DEFAULT_BODY_FONT,
    DEFAULT_HEADING_FONT,
    DocumentCover,
    DocumentHeaderFooter,
    DocumentIR,
    DocumentPageLayout,
    DocumentParagraph,
    DocumentSection,
    DocumentStyle,
    DocumentTable,
    DocumentTOC,
    count_markdown_images,
    default_styles,
    heading_level,
    iter_text_tokens,
    table_kind_marker,
)

#: 固定 ZIP 时间戳：同一份 IR 两次导出应得到逐字节一致的文件。
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _env_int_limit(name: str, default: int) -> int:
    """读取导出上限：环境变量覆盖，非法值回退默认值，下限为 0。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return default


#: 单篇文档章节（IR section / 原始 node）数量上限。
MAX_NODES = _env_int_limit("BIDCRAFT_DOCX_MAX_NODES", 5000)
#: 单篇文档 Markdown 图片行数量上限。
MAX_IMAGES = _env_int_limit("BIDCRAFT_DOCX_MAX_IMAGES", 500)
#: 单篇文档正文字符总量上限，防止超大输入拖垮渲染与交付校验。
MAX_TOTAL_BODY_CHARS = _env_int_limit("BIDCRAFT_DOCX_MAX_TOTAL_BODY_CHARS", 20_000_000)
#: 单个章节正文字符上限。
MAX_NODE_BODY_CHARS = _env_int_limit("BIDCRAFT_DOCX_MAX_NODE_BODY_CHARS", 200_000)


def guard_export_limits(
    bodies: Iterable[str],
    image_counts: Iterable[int] | None = None,
    *,
    max_nodes: int | None = None,
    max_images: int | None = None,
) -> None:
    """导出前统一校验章节数、图片数与正文字符数。

    这是两个引擎共用的唯一一层限制：``export_docx`` 的简单路径直接调用它，
    专业路径与直接渲染 IR 的调用方则由 :func:`render_document` 调用它。
    ``image_counts`` 省略时按 :data:`~bidcraft.documents.document_ir.IMAGE_PATTERN`
    从正文统计，保证与 IR 的图片解析规则一致。
    """

    body_list = [str(body or "") for body in bodies]
    node_limit = MAX_NODES if max_nodes is None else int(max_nodes)
    image_limit = MAX_IMAGES if max_images is None else int(max_images)
    total_limit = MAX_TOTAL_BODY_CHARS
    node_char_limit = MAX_NODE_BODY_CHARS

    if len(body_list) > node_limit:
        raise ValueError(
            f"章节数超过导出上限 {node_limit}（当前 {len(body_list)}，请拆分章节或减少节点）"
        )

    counts = (
        [int(value) for value in image_counts]
        if image_counts is not None
        else [count_markdown_images(body) for body in body_list]
    )
    if sum(counts) > image_limit:
        raise ValueError(
            f"图片数超过导出上限 {image_limit}（当前 {sum(counts)}，"
            "请减少 Markdown 图片行或提高 BIDCRAFT_DOCX_MAX_IMAGES）"
        )

    total_chars = sum(len(body) for body in body_list)
    if total_chars > total_limit:
        raise ValueError(
            f"正文总字符数超过导出上限 {total_limit}（当前 {total_chars}，"
            "请拆分文档或提高 BIDCRAFT_DOCX_MAX_TOTAL_BODY_CHARS）"
        )
    for index, body in enumerate(body_list, start=1):
        if len(body) > node_char_limit:
            raise ValueError(
                f"第 {index} 章正文字符数超过导出上限 {node_char_limit}（当前 {len(body)}，"
                "请拆分该章节或提高 BIDCRAFT_DOCX_MAX_NODE_BODY_CHARS）"
            )


def _ir_section_bodies(ir: DocumentIR) -> list[str]:
    """把 IR 每节的可导出文本拼成与原始 ``body`` 等价的计数单位。"""

    bodies: list[str] = []
    for section in ir.sections:
        parts: list[str] = [section.heading]
        parts.extend(paragraph.text for paragraph in section.paragraphs)
        for table in section.tables:
            if table.caption:
                parts.append(table.caption)
            for row in table.rows:
                parts.extend(str(cell) for cell in row)
        parts.extend(
            str(image.get("alt") or "")
            for image in section.images
            if isinstance(image, dict)
        )
        bodies.append("\n".join(parts))
    return bodies


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
    "h4": "Heading 4",
    "heading4": "Heading 4",
    "标题4": "Heading 4",
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


def add_hyperlink(paragraph, text: str, url: str) -> None:
    """写入外部超链接（``w:hyperlink`` + external relationship）。"""

    relationship_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    properties.append(color)
    properties.append(underline)
    run.append(properties)
    content = OxmlElement("w:t")
    content.set(qn("xml:space"), "preserve")
    content.text = text
    run.append(content)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _add_text_runs(paragraph, text: Any) -> None:
    """写入文本：占位符高亮，Markdown 链接写成真正的 Word 超链接。"""

    for token in iter_text_tokens(text):
        if token[0] == "placeholder":
            run = paragraph.add_run(token[1])
            run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
            run.font.bold = True
        elif token[0] == "link":
            add_hyperlink(paragraph, token[1], token[2])
        else:
            _add_plain_text(paragraph, token[1])


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


def set_table_kind(table, kind: str) -> None:
    """在 ``w:tblPr/w:tblDescription`` 写入稳定的表格类型标记。

    校验器据此区分数据表格与版式/表单/内容表格，避免签字栏、封面键值表等
    因首行留空而误报缺失表头。
    """

    properties = table._tbl.tblPr
    description = properties.find(qn("w:tblDescription"))
    if description is None:
        description = OxmlElement("w:tblDescription")
        properties.append(description)
    description.set(qn("w:val"), table_kind_marker(kind))


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

    # 放在其他 tblPr 修改之后，保证 w:tblDescription 位于 schema 规定的末尾。
    set_table_kind(table, spec.kind)
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


def _render_cover(document, spec: DocumentCover) -> None:
    """渲染封面；用段前间距代替连续空段落，避免产生空白页警告。"""

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(80)
    _add_text_runs(title, spec.title)
    for run in title.runs:
        run.font.size = Pt(26)
        run.font.bold = True
        _set_fonts(run._r, DEFAULT_HEADING_FONT)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle_run = subtitle.add_run(spec.subtitle)
    subtitle_run.font.size = Pt(36)
    subtitle_run.font.bold = True
    _set_fonts(subtitle_run._r, DEFAULT_HEADING_FONT)

    for label, value in spec.lines:
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        info = paragraph.add_run(f"{label}：")
        _add_text_runs(paragraph, value)
        info.font.size = Pt(14)
        for run in paragraph.runs:
            run.font.size = Pt(14)


def _render_toc(document, spec: DocumentTOC) -> None:
    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if spec.page_break:
        heading.paragraph_format.page_break_before = True
    run = heading.add_run(spec.title)
    run.font.size = Pt(18)
    run.font.bold = True
    _set_fonts(run._r, DEFAULT_HEADING_FONT)
    _add_field(
        document.add_paragraph(),
        f'TOC \\o "{spec.levels}" \\h \\z \\u',
        spec.placeholder,
    )


def _render_section(document, spec: DocumentSection, *, page_break_before: bool = False) -> None:
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
    if (spec.page_break_before or page_break_before) and created:
        created[0].paragraph_format.page_break_before = True


def _apply_core_properties(document, properties: dict[str, Any]) -> None:
    core = document.core_properties
    for key in ("title", "author", "subject", "keywords", "comments", "category"):
        value = properties.get(key)
        if value:
            setattr(core, key, str(value))


def _rewrite_zip_timestamps(path: Path) -> None:
    """把所有 ZIP 条目时间固定为 1980-01-01，消除 wall-clock 造成的字节差异。"""

    temp_path = path.with_name(f".{path.name}.normalized")
    try:
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(
            temp_path, "w", zipfile.ZIP_DEFLATED
        ) as target:
            for info in source.infolist():
                payload = source.read(info.filename)
                entry = copy.copy(info)
                entry.date_time = ZIP_TIMESTAMP
                target.writestr(entry, payload)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def save_document_verified(document, path: str | Path) -> Path:
    """先写临时文件并校验 ZIP CRC，再原子替换目标文件。

    同时归一化 ZIP 时间戳，保证相同输入的导出结果逐字节一致。校验失败时目标
    文件保持旧内容（或不存在），不会留下半写坏的交付件。
    """

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.", suffix=".docx"
    )
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        document.save(str(temp_path))
        _rewrite_zip_timestamps(temp_path)
        with zipfile.ZipFile(temp_path, "r") as archive:
            broken = archive.testzip()
        if broken is not None:
            raise ValueError(f"DOCX 包 CRC 校验失败：{broken}")
        os.replace(temp_path, output)
    finally:
        temp_path.unlink(missing_ok=True)
    return output


def render_document(
    ir: DocumentIR,
    path: str | Path,
    *,
    base_template: Path | str | None = None,
    max_nodes: int | None = None,
    max_images: int | None = None,
) -> Path:
    """按 IR 渲染 DOCX，返回输出路径。

    ``base_template`` 存在时继承模板的样式/主题/页眉页脚/页面设置，清空正文后
    再按 IR 重建；不存在时使用 python-docx 默认文档。所有导出必经此处的统一
    上限校验，直接构造 IR 的调用方无法绕过。
    """

    guard_export_limits(
        _ir_section_bodies(ir),
        image_counts=[len(section.images) for section in ir.sections],
        max_nodes=max_nodes,
        max_images=max_images,
    )

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

    if ir.cover is not None:
        _render_cover(document, ir.cover)
    if ir.toc is not None:
        _render_toc(document, ir.toc)

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
        # 封面/目录与第一节同属一个 section，用 page_break_before 换页；
        # 后续节本身由 NEW_PAGE 换页，不重复加段前分页以免产生空白页。
        _render_section(
            document,
            section_spec,
            page_break_before=(index == 0 and (ir.cover is not None or ir.toc is not None)),
        )

    _apply_core_properties(document, ir.core_properties or {})
    return save_document_verified(document, path)


__all__ = [
    "add_hyperlink",
    "guard_export_limits",
    "render_document",
    "save_document_verified",
    "set_table_kind",
]
