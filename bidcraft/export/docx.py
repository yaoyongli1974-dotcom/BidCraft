from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .. import clock
from ..documents.document_ir import (
    IMAGE_PATTERN,
    DocumentCover,
    DocumentHeaderFooter,
    DocumentPageLayout,
    DocumentStyle,
    DocumentTOC,
    from_markdown_sections,
)
from ..documents.renderer import (
    MAX_IMAGES,
    MAX_NODES,
    add_hyperlink,
    guard_export_limits,
    render_document,
    save_document_verified,
    set_table_kind,
)
from ..schemas import FactLedger

PLACEHOLDER = re.compile(r"(\{\{待填:[^}]*\}\}|\{\{图片占位:[^}]*\}\})")
_LINK = r"\[([^\]]+)\]\(((?:https?://|mailto:)[^)\s]+)\)"
_TEXT_TOKEN = re.compile(rf"{PLACEHOLDER.pattern}|({_LINK})")

# 上限统一定义在 documents.renderer（单一守卫层），这里 re-export 保持兼容。


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


def _add_cover(document: Document, ledger: FactLedger, *, as_of: date | None = None) -> None:
    # 段前留白代替连续空段落：既保留封面视觉，又不产生空白页/校验警告。
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(80)
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

    info = [
        ("项目编号", ledger.value("project.number") or "{{待填:项目编号}}"),
        ("招标人", ledger.value("project.buyer") or "{{待填:招标人}}"),
        ("投标人", ledger.value("bidder.name") or "{{待填:投标人名称}}"),
        ("日期", (as_of or clock.today()).strftime("%Y 年 %m 月 %d 日")),
    ]
    for label, value in info:
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(f"{label}：{value}")
        run.font.size = Pt(14)


def _set_run_font(run, name: str) -> None:
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:eastAsia"), name)


def _iter_text_tokens(text: str):
    """切分文本：占位符原样高亮，Markdown 链接写成外部超链接。"""

    cursor = 0
    for match in _TEXT_TOKEN.finditer(text):
        if match.start() > cursor:
            yield "text", text[cursor : match.start()]
        if match.group(1):
            yield "placeholder", match.group(1)
        else:
            yield "link", match.group(3), match.group(4)
        cursor = match.end()
    if cursor < len(text):
        yield "text", text[cursor:]


def _add_placeholder_runs(paragraph, text: str) -> None:
    for token in _iter_text_tokens(text):
        if token[0] == "placeholder":
            run = paragraph.add_run(token[1])
            run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
            run.font.bold = True
        elif token[0] == "link":
            add_hyperlink(paragraph, token[1], token[2])
        else:
            paragraph.add_run(token[1])


def _add_table(document: Document, rows: list[list[str]], *, kind: str = "layout") -> None:
    """简单引擎的表格写入器。

    默认 ``layout``：商务标封面键值表/字段表等复用本函数，首行可以留空；
    Markdown 数据表格由 :func:`_add_body` 显式传入 ``kind="data"``。
    """

    if not rows:
        return
    width = max(len(row) for row in rows)
    table = document.add_table(rows=len(rows), cols=width)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    set_table_kind(table, kind)
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


# 与 document_ir 的图片解析共用同一正则；旧的私有名称保留为别名。
IMAGE_LINE = IMAGE_PATTERN

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


def _policy_width_cm(figure_state: dict) -> float:
    policy = figure_state["policy"]
    try:
        width_cm = float(policy.get("width_cm") or DEFAULT_IMAGE_POLICY["width_cm"])
    except (TypeError, ValueError):
        return float(DEFAULT_IMAGE_POLICY["width_cm"])
    return width_cm


def _add_image(document: Document, path: Path, alt: str, figure_state: dict) -> None:
    policy = figure_state["policy"]
    document.add_picture(str(path), width=Cm(_policy_width_cm(figure_state)))
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


_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+")


def _body_heading_offset(nodes: list[dict]) -> int:
    """章节标题是 H1；正文最小 Markdown 标题级别统一映射为 H2。"""

    levels = [
        len(match.group(1))
        for node in nodes
        for line in str(node.get("body") or "").splitlines()
        if (match := _MARKDOWN_HEADING_RE.match(line.strip()))
    ]
    return 2 - min(levels) if levels else 0


def _add_body(
    document: Document,
    body: str,
    heading: str,
    image_resolver: Callable[[str], Path | None] | None = None,
    figure_state: dict | None = None,
    heading_offset: int = 0,
) -> None:
    if figure_state is None:
        figure_state = _figure_state(None)
    lines = (body or "").splitlines()
    index = 0
    previous_level = 1  # 章节标题 H1
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if image_resolver is not None and (match := IMAGE_PATTERN.match(stripped)):
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
            _add_table(document, _parse_table_block(block), kind="data")
            continue
        # 章节标题本身是 H1；正文标题相对映射后仍须逐级递进，不能跳级。
        if match := re.match(r"^(#{1,6})\s+(.+)$", stripped):
            raw_level = len(match.group(1)) + heading_offset
            level = max(2, min(9, raw_level, previous_level + 1))
            previous_level = level
            document.add_heading(match.group(2).strip(), level=level)
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


def _professional_styles(preset: dict) -> dict[str, DocumentStyle]:
    """把排版预设映射成专业引擎的样式（含四级标题）。"""

    base = float(preset["body_size"])
    styles = {
        "Normal": DocumentStyle(
            name="Normal",
            font_name=str(preset["body_font"]),
            font_size_pt=base,
            line_spacing=float(preset["line_spacing"]),
            space_after=6.0,
        )
    }
    for level, increment in ((1, 6), (2, 3), (3, 1), (4, 0.5)):
        styles[f"Heading {level}"] = DocumentStyle(
            name=f"Heading {level}",
            font_name=str(preset["heading_font"]),
            font_size_pt=base + increment,
            bold=True,
            line_spacing=float(preset["line_spacing"]),
            space_before=12.0,
            space_after=8.0,
        )
    return styles


def _guard_limits(nodes: list[dict[str, Any]], *, max_nodes: int, max_images: int) -> None:
    """简单引擎的入口守卫；实现与专业引擎共用 renderer.guard_export_limits。"""

    guard_export_limits(
        [str(node.get("body") or "") for node in nodes],
        max_nodes=max_nodes,
        max_images=max_images,
    )


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
    professional: bool = False,
    base_template: Path | str | None = None,
    as_of: date | None = None,
    max_nodes: int | None = None,
    max_images: int | None = None,
) -> Path:
    from .templates import normalize

    preset = normalize(template)
    output = Path(path)
    figure_state = _figure_state(image_policy)
    top, bottom, left, right = preset["margins"]

    if professional:
        # 专业引擎：IR → 模板继承渲染 + 导出后 CRC 校验（含超链接、表头、目录域）。
        page_layout = None
        if base_template is None:
            page_layout = DocumentPageLayout(
                page_width_cm=21.0,
                page_height_cm=29.7,
                margin_top_cm=float(top),
                margin_bottom_cm=float(bottom),
                margin_left_cm=float(left),
                margin_right_cm=float(right),
            )
        cover = None
        if include_cover and preset["cover"]:
            cover = DocumentCover(
                title=ledger.value("project.name") or "{{待填:招标项目名称}}",
                lines=[
                    ("项目编号", ledger.value("project.number") or "{{待填:项目编号}}"),
                    ("招标人", ledger.value("project.buyer") or "{{待填:招标人}}"),
                    ("投标人", ledger.value("bidder.name") or "{{待填:投标人名称}}"),
                    ("日期", (as_of or clock.today()).strftime("%Y 年 %m 月 %d 日")),
                ],
            )
        ir = from_markdown_sections(
            nodes,
            title=ledger.value("project.name") or "",
            author=ledger.value("bidder.name") or "",
            styles=_professional_styles(preset),
            page_layout=page_layout,
            header_footer=DocumentHeaderFooter(page_number=bool(preset["page_numbers"])),
            core_properties={
                "title": ledger.value("project.name") or "投标文件",
                "author": ledger.value("bidder.name") or "",
            },
            cover=cover,
            toc=DocumentTOC() if include_toc and preset["toc"] else None,
        )
        for section in ir.sections:
            for image in section.images:
                resolver_path = (
                    image_resolver(str(image.get("path") or ""))
                    if image_resolver is not None
                    else None
                )
                if resolver_path is not None:
                    image["path"] = str(resolver_path)
                image["width_cm"] = _policy_width_cm(figure_state)
        # 专业引擎不在导出层重复校验：IR 渲染入口统一守卫，并透传调用方上限。
        return render_document(
            ir,
            output,
            base_template=base_template,
            max_nodes=max_nodes,
            max_images=max_images,
        )

    _guard_limits(
        nodes,
        max_nodes=MAX_NODES if max_nodes is None else int(max_nodes),
        max_images=MAX_IMAGES if max_images is None else int(max_images),
    )
    document = Document()
    section = document.sections[0]
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

    has_front_matter = False
    if include_cover and preset["cover"]:
        _add_cover(document, ledger, as_of=as_of)
        has_front_matter = True

    if include_toc and preset["toc"]:
        heading = document.add_paragraph()
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = heading.add_run("目  录")
        run.font.size = Pt(18)
        run.font.bold = True
        _set_run_font(run, "黑体")
        _add_field(document.add_paragraph(), 'TOC \\o "1-3" \\h \\z \\u', "（在 Word 中右键更新目录）")
        has_front_matter = True

    heading_offset = _body_heading_offset(nodes)
    for index, node in enumerate(nodes):
        # 用段前分页代替「空段落 + 手动分页符」，大文档不再积累空白段落。
        node_heading = document.add_heading(str(node.get("heading") or ""), level=1)
        if has_front_matter or index:
            node_heading.paragraph_format.page_break_before = True
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
                heading_offset,
            )

    return save_document_verified(document, output)


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
