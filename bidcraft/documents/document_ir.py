"""DOCX 文档中间表示（IR）。

本模块只描述文档结构，不依赖 python-docx，便于业务层、服务层与测试之间传递。
渲染器（``renderer``）、模板解析器（``template_parser``）与校验器（``validator``）
都只依赖这里的数据类。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

#: 待填占位符，例如 ``{{待填:项目经理}}``。渲染时必须原样保留。
PLACEHOLDER_PATTERN = re.compile(r"\{\{待填:[^}]*\}\}")

DEFAULT_BODY_FONT = "宋体"
DEFAULT_HEADING_FONT = "黑体"

_HEADING_STYLE_PATTERN = re.compile(r"^(?:heading|标题)\s*([1-9])$", re.IGNORECASE)
_TABLE_DIVIDER_PATTERN = re.compile(r"^:?-{2,}:?$")
_BULLET_PATTERN = re.compile(r"^[-*•]\s+")
_NUMBERED_PATTERN = re.compile(r"^\d+[.、)]\s+")
_IMAGE_PATTERN = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)$")


def heading_level(style_name: str | None) -> int | None:
    """把 ``Heading 2`` / ``标题 3`` / ``heading2`` 之类的样式名解析成层级。"""

    if not style_name:
        return None
    match = _HEADING_STYLE_PATTERN.match(str(style_name).strip())
    return int(match.group(1)) if match else None


@dataclass
class DocumentStyle:
    """单个样式（正文或标题）的排版参数。"""

    name: str = "Normal"
    font_name: str = DEFAULT_BODY_FONT
    font_size_pt: float = 12.0
    bold: bool = False
    line_spacing: float | None = 1.5
    space_before: float = 0.0
    space_after: float = 0.0


@dataclass
class DocumentPageLayout:
    """页面尺寸与页边距（单位：厘米）。"""

    page_width_cm: float = 21.0
    page_height_cm: float = 29.7
    margin_top_cm: float = 2.54
    margin_bottom_cm: float = 2.54
    margin_left_cm: float = 3.17
    margin_right_cm: float = 3.17
    orientation: str = "portrait"

    @property
    def is_landscape(self) -> bool:
        return str(self.orientation).strip().lower().startswith(("land", "横"))


@dataclass
class DocumentHeaderFooter:
    """页眉页脚设置。"""

    header_text: str = ""
    footer_text: str = ""
    different_first_page: bool = False
    different_odd_even: bool = False
    page_number: bool = False


@dataclass
class DocumentTable:
    """表格。``rows`` 为纯文本二维表，首行默认视为表头。"""

    rows: list[list[str]] = field(default_factory=list)
    header_row: bool = True
    repeat_header: bool = True
    widths_cm: list[float] | None = None
    caption: str = ""


@dataclass
class DocumentParagraph:
    """段落。``style`` 为 Word 样式名，``level`` 是标题层级（1-9）。"""

    text: str = ""
    style: str = "Normal"
    level: int = 0
    alignment: str | None = None


@dataclass
class DocumentSection:
    """一节（对应 Word 的一个 section）。"""

    heading: str = ""
    paragraphs: list[DocumentParagraph] = field(default_factory=list)
    tables: list[DocumentTable] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    page_break_before: bool = False
    landscape: bool = False
    level: int = 1


@dataclass
class DocumentIR:
    """整篇文档的中间表示。"""

    sections: list[DocumentSection] = field(default_factory=list)
    styles: dict[str, DocumentStyle] = field(default_factory=dict)
    page_layout: DocumentPageLayout | None = None
    header_footer: DocumentHeaderFooter | None = None
    core_properties: dict[str, str] = field(default_factory=dict)

    def add_section(self, section: DocumentSection | None = None) -> DocumentSection:
        section = section or DocumentSection()
        self.sections.append(section)
        return section

    def iter_texts(self) -> Iterator[tuple[str, str]]:
        """按位置遍历所有文本，供占位符统计与报告使用。"""

        for section_index, section in enumerate(self.sections):
            if section.heading:
                yield f"section:{section_index}:heading", section.heading
            for paragraph_index, paragraph in enumerate(section.paragraphs):
                yield f"section:{section_index}:paragraph:{paragraph_index}", paragraph.text
            for table_index, table in enumerate(section.tables):
                if table.caption:
                    yield f"section:{section_index}:table:{table_index}:caption", table.caption
                for row_index, row in enumerate(table.rows):
                    for column_index, cell in enumerate(row):
                        yield (
                            (
                                f"section:{section_index}:table:{table_index}"
                                f":r{row_index}c{column_index}"
                            ),
                            str(cell),
                        )
            for image_index, image in enumerate(section.images):
                alt = image.get("alt", "") if isinstance(image, dict) else ""
                if alt:
                    yield f"section:{section_index}:image:{image_index}", str(alt)

    def iter_placeholders(self) -> Iterator[tuple[str, int]]:
        """遍历占位符位置与数量。"""

        for location, text in self.iter_texts():
            count = len(PLACEHOLDER_PATTERN.findall(text or ""))
            if count:
                yield location, count

    def count_placeholders(self) -> int:
        return sum(count for _, count in self.iter_placeholders())


def default_styles() -> dict[str, DocumentStyle]:
    """一套中文投标文件常用的默认样式。"""

    return {
        "Normal": DocumentStyle(
            name="Normal",
            font_name=DEFAULT_BODY_FONT,
            font_size_pt=12.0,
            bold=False,
            line_spacing=1.5,
            space_before=0.0,
            space_after=6.0,
        ),
        "Heading 1": DocumentStyle(
            name="Heading 1",
            font_name=DEFAULT_HEADING_FONT,
            font_size_pt=18.0,
            bold=True,
            line_spacing=1.5,
            space_before=12.0,
            space_after=8.0,
        ),
        "Heading 2": DocumentStyle(
            name="Heading 2",
            font_name=DEFAULT_HEADING_FONT,
            font_size_pt=16.0,
            bold=True,
            line_spacing=1.5,
            space_before=10.0,
            space_after=6.0,
        ),
        "Heading 3": DocumentStyle(
            name="Heading 3",
            font_name=DEFAULT_HEADING_FONT,
            font_size_pt=14.0,
            bold=True,
            line_spacing=1.5,
            space_before=8.0,
            space_after=4.0,
        ),
    }


def _parse_table_block(lines: Iterable[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in lines:
        stripped = line.strip().strip("|")
        cells = [cell.strip() for cell in stripped.split("|")]
        if cells and all(not cell or _TABLE_DIVIDER_PATTERN.match(cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


def from_markdown_sections(
    nodes: Iterable[dict[str, Any]],
    *,
    title: str = "",
    author: str = "",
    subject: str = "",
    keywords: str = "",
    styles: dict[str, DocumentStyle] | None = None,
    page_layout: DocumentPageLayout | None = None,
    header_footer: DocumentHeaderFooter | None = None,
) -> DocumentIR:
    """把 ``[{"heading": ..., "body": "markdown"}]`` 转成 :class:`DocumentIR`。

    ``body`` 中：

    * ``#`` / ``##`` / ``###`` 行 → Heading 1/2/3 段落
    * ``| a | b |`` 连续行 → 表格（自动跳过 ``---`` 分隔行）
    * ``-`` / ``*`` / ``•`` 开头 → List Bullet
    * ``1.`` / ``1、`` 开头 → List Number
    * ``![alt](path)`` → 图片条目
    * 其余非空行 → Normal 段落
    """

    sections: list[DocumentSection] = []
    for node in nodes:
        body = str(node.get("body") or "")
        section = DocumentSection(heading=str(node.get("heading") or "").strip())
        lines = body.splitlines()
        index = 0
        while index < len(lines):
            line = lines[index].rstrip()
            stripped = line.strip()
            if not stripped:
                index += 1
                continue

            if stripped.startswith("|") and stripped.endswith("|"):
                block: list[str] = []
                while index < len(lines) and lines[index].strip().startswith("|"):
                    block.append(lines[index])
                    index += 1
                rows = _parse_table_block(block)
                if rows:
                    section.tables.append(DocumentTable(rows=rows))
                continue

            image_match = _IMAGE_PATTERN.match(stripped)
            if image_match:
                section.images.append({"path": image_match.group(2), "alt": image_match.group(1)})
                index += 1
                continue

            if stripped.startswith("### "):
                section.paragraphs.append(
                    DocumentParagraph(text=stripped[4:].strip(), style="Heading 3", level=3)
                )
            elif stripped.startswith("## "):
                section.paragraphs.append(
                    DocumentParagraph(text=stripped[3:].strip(), style="Heading 2", level=2)
                )
            elif stripped.startswith("# "):
                section.paragraphs.append(
                    DocumentParagraph(text=stripped[2:].strip(), style="Heading 1", level=1)
                )
            elif _BULLET_PATTERN.match(stripped):
                section.paragraphs.append(
                    DocumentParagraph(
                        text=_BULLET_PATTERN.sub("", stripped),
                        style="List Bullet",
                    )
                )
            elif _NUMBERED_PATTERN.match(stripped):
                section.paragraphs.append(
                    DocumentParagraph(
                        text=_NUMBERED_PATTERN.sub("", stripped),
                        style="List Number",
                    )
                )
            else:
                section.paragraphs.append(DocumentParagraph(text=stripped))
            index += 1

        sections.append(section)

    core_properties = {
        "title": title or (sections[0].heading if sections else ""),
        "author": author,
        "subject": subject,
        "keywords": keywords,
    }
    return DocumentIR(
        sections=sections,
        styles=styles if styles is not None else default_styles(),
        page_layout=page_layout,
        header_footer=header_footer,
        core_properties={key: value for key, value in core_properties.items() if value},
    )
