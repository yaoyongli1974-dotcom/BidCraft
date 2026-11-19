from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from ..schemas import FactLedger

PLACEHOLDER = re.compile(r"(\{\{待填:[^}]*\}\}|\{\{图片占位:[^}]*\}\})")


def _set_east_asian(style, font_name: str) -> None:
    style.font.name = font_name
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:eastAsia"), font_name)


def _setup_styles(document: Document, template: dict | None = None) -> None:
    from .templates import normalize

    preset = normalize(template)
    normal = document.styles["Normal"]
    _set_east_asian(normal, preset["body_font"])
    normal.font.size = Pt(preset["body_size"])
    normal.paragraph_format.line_spacing = preset["line_spacing"]
    normal.paragraph_format.space_after = Pt(6)
    base = float(preset["body_size"])
    for name, size in (
        ("Heading 1", base + 6),
        ("Heading 2", base + 3),
        ("Heading 3", base + 1),
    ):
        style = document.styles[name]
        _set_east_asian(style, preset["heading_font"])
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.paragraph_format.space_before = Pt(12)
        style.paragraph_format.space_after = Pt(8)


def _add_field(paragraph, instruction: str, placeholder: str = "") -> None:
    run = paragraph.add_run()
    fld_char_begin = OxmlElement("w:fldChar")
    fld_char_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    fld_char_sep = OxmlElement("w:fldChar")
    fld_char_sep.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = placeholder
    fld_char_end = OxmlElement("w:fldChar")
    fld_char_end.set(qn("w:fldCharType"), "end")
    for element in (fld_char_begin, instr, fld_char_sep, text, fld_char_end):
        run._r.append(element)


def _add_page_number_footer(section) -> None:
    footer = section.footer
    paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run("第 ")
    run.font.size = Pt(9)
    _add_field(paragraph, "PAGE", "1")
    paragraph.add_run(" 页").font.size = Pt(9)


def _update_fields_on_open(document: Document) -> None:
    settings = document.settings.element
    update = settings.find(qn("w:updateFields"))
    if update is None:
        update = OxmlElement("w:updateFields")
        settings.append(update)
    update.set(qn("w:val"), "true")


def _add_cover(document: Document, ledger: FactLedger) -> None:
    for _ in range(4):
        document.add_paragraph()
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(ledger.value("project.name") or "{{待填:招标项目名称}}")
    run.font.size = Pt(26)
    run.font.bold = True
    _set_run_font(run, "黑体")

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = subtitle.add_run("投 标 文 件")
    sub_run.font.size = Pt(36)
    sub_run.font.bold = True
    _set_run_font(sub_run, "黑体")

    document.add_paragraph()
    info = [
        ("项目编号", ledger.value("project.number") or "{{待填:项目编号}}"),
        ("招标人", ledger.value("project.buyer") or "{{待填:招标人}}"),
        ("投标人", ledger.value("bidder.name") or "{{待填:投标人名称}}"),
        ("日期", date.today().strftime("%Y 年 %m 月 %d 日")),
    ]
    for label, value in info:
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(f"{label}：{value}")
        run.font.size = Pt(14)

    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def _set_run_font(run, name: str) -> None:
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:eastAsia"), name)


def _add_placeholder_runs(paragraph, text: str) -> None:
    cursor = 0
    for match in PLACEHOLDER.finditer(text):
        if match.start() > cursor:
            paragraph.add_run(text[cursor : match.start()])
        run = paragraph.add_run(match.group(1))
        run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
        run.font.bold = True
        cursor = match.end()
    if cursor < len(text):
        paragraph.add_run(text[cursor:])


def _add_table(document: Document, rows: list[list[str]]) -> None:
    if not rows:
        return
    width = max(len(row) for row in rows)
    table = document.add_table(rows=len(rows), cols=width)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r, row in enumerate(rows):
        for c in range(width):
            cell = table.cell(r, c)
            cell.text = ""
            paragraph = cell.paragraphs[0]
            value = row[c] if c < len(row) else ""
            run = paragraph.add_run(value)
            run.font.size = Pt(9)
            if r == 0:
                run.font.bold = True
    document.add_paragraph()


def _parse_table_block(lines: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
            continue
        rows.append(cells)
    return rows


IMAGE_LINE = re.compile(r"^!\[([^\]]*)\]\((/projects/[^)\s]+/images/[^)\s]+)\)$")

# Local copy of ``bidcraft.images.DEFAULT_POLICY`` to avoid an import cycle.
DEFAULT_IMAGE_POLICY = {
    "max_total": 12,
    "style": "",
    "size": "auto",
    "captions": True,
    "caption_prefix": "图",
    "width_cm": 15.0,
}


def _figure_state(image_policy: dict | None) -> dict:
    """Mutable cross-chapter counter plus the effective export policy."""
    policy = dict(DEFAULT_IMAGE_POLICY)
    if isinstance(image_policy, dict):
        policy.update({key: value for key, value in image_policy.items() if key in policy})
    return {"n": 0, "policy": policy}


def _add_image(document: Document, path: Path, alt: str, figure_state: dict) -> None:
    policy = figure_state["policy"]
    try:
        width_cm = float(policy.get("width_cm") or DEFAULT_IMAGE_POLICY["width_cm"])
    except (TypeError, ValueError):
        width_cm = DEFAULT_IMAGE_POLICY["width_cm"]
    document.add_picture(str(path), width=Cm(width_cm))
    picture = document.paragraphs[-1]
    picture.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if policy.get("captions", True) and alt:
        figure_state["n"] = int(figure_state.get("n") or 0) + 1
        prefix = str(policy.get("caption_prefix") or "图")
        caption = document.add_paragraph()
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = caption.add_run(f"{prefix}{figure_state['n']}\u3000{alt}")
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)


def _add_body(
    document: Document,
    body: str,
    heading: str,
    image_resolver: Callable[[str], Path | None] | None = None,
    figure_state: dict | None = None,
) -> None:
    if figure_state is None:
        figure_state = _figure_state(None)
    lines = (body or "").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if image_resolver is not None and (match := IMAGE_LINE.match(stripped)):
            resolved = image_resolver(match.group(2))
            if resolved is not None:
                _add_image(document, resolved, match.group(1), figure_state)
                index += 1
                continue
        if stripped.startswith("|") and stripped.endswith("|"):
            block = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                block.append(lines[index])
                index += 1
            _add_table(document, _parse_table_block(block))
            continue
        if stripped.startswith("### "):
            document.add_heading(stripped[4:].strip(), level=3)
        elif stripped.startswith("## "):
            document.add_heading(stripped[3:].strip(), level=2)
        elif stripped.startswith("# "):
            document.add_heading(stripped[2:].strip(), level=2)
        elif re.match(r"^[-*•]\s+", stripped):
            paragraph = document.add_paragraph(style="List Bullet")
            _add_placeholder_runs(paragraph, re.sub(r"^[-*•]\s+", "", stripped))
        elif re.match(r"^\d+[.、)]\s+", stripped):
            paragraph = document.add_paragraph(style="List Number")
            _add_placeholder_runs(paragraph, re.sub(r"^\d+[.、)]\s+", "", stripped))
        else:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.first_line_indent = Cm(0.74)
            _add_placeholder_runs(paragraph, stripped)
        index += 1


def export_docx(
    path: str | Path,
    ledger: FactLedger,
    nodes: list[dict[str, Any]],
    *,
    include_cover: bool = True,
    include_toc: bool = True,
    image_resolver: Callable[[str], Path | None] | None = None,
    image_policy: dict | None = None,
    template: dict | None = None,
) -> Path:
    from .templates import normalize

    preset = normalize(template)
    document = Document()
    figure_state = _figure_state(image_policy)
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

    if include_cover and preset["cover"]:
        _add_cover(document, ledger)

    if include_toc and preset["toc"]:
        heading = document.add_paragraph()
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = heading.add_run("目  录")
        run.font.size = Pt(18)
        run.font.bold = True
        _set_run_font(run, "黑体")
        _add_field(document.add_paragraph(), 'TOC \\o "1-3" \\h \\z \\u', "（在 Word 中右键更新目录）")
        document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    for node in nodes:
        document.add_heading(str(node.get("heading") or ""), level=1)
        body = str(node.get("body") or "")
        if not body.strip():
            paragraph = document.add_paragraph()
            _add_placeholder_runs(paragraph, "{{待填:本章尚未生成正文}}")
        else:
            _add_body(
                document,
                body,
                str(node.get("heading") or ""),
                image_resolver,
                figure_state,
            )
        document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output))
    return output


def export_markdown(path: str | Path, ledger: FactLedger, nodes: list[dict[str, Any]]) -> Path:
    lines = [f"# {ledger.value('project.name') or '投标文件'}", ""]
    for node in nodes:
        lines.append(f"# {node.get('heading') or ''}")
        lines.append("")
        lines.append(str(node.get("body") or "{{待填:本章尚未生成正文}}"))
        lines.append("")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return output
