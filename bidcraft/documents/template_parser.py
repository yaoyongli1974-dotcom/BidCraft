"""解析已有 DOCX 模板，提取样式、页面设置与页眉页脚。

对外接口全部是「不抛异常」风格：损坏文件、非 docx 文件返回 ``{"error": ...}``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn

TWIPS_PER_CM = 566.9291338582677
_EMU_PER_CM = 360000


def _twips_to_cm(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) / TWIPS_PER_CM, 2)
    except (TypeError, ValueError):
        return None


def _emu_to_cm(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(int(value)) / _EMU_PER_CM, 2)
    except (TypeError, ValueError):
        return None


def _container_text(container) -> str:
    """读取页眉/页脚容器中的段落与表格文本。"""

    lines: list[str] = []
    for paragraph in container.paragraphs:
        text = paragraph.text.strip()
        if text:
            lines.append(text)
    for table in container.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if text:
                    lines.append(text)
    return "\n".join(lines)


def _margin_cm(page_margins, name: str, fallback) -> float | None:
    if page_margins is not None and page_margins.get(qn(f"w:{name}")) is not None:
        return _twips_to_cm(page_margins.get(qn(f"w:{name}")))
    return _emu_to_cm(getattr(fallback, "emu", None))


def extract_page_layout(doc) -> list[dict[str, Any]]:
    """按节提取页面尺寸、方向与页边距（读取 OXML 的 ``w:pgSz`` / ``w:pgMar``）。"""

    layouts: list[dict[str, Any]] = []
    for index, section in enumerate(doc.sections):
        section_properties = section._sectPr
        page_size = section_properties.find(qn("w:pgSz"))
        page_margins = section_properties.find(qn("w:pgMar"))

        width_cm = _twips_to_cm(page_size.get(qn("w:w"))) if page_size is not None else None
        height_cm = _twips_to_cm(page_size.get(qn("w:h"))) if page_size is not None else None
        if width_cm is None:
            width_cm = _emu_to_cm(getattr(section.page_width, "emu", None))
        if height_cm is None:
            height_cm = _emu_to_cm(getattr(section.page_height, "emu", None))

        orientation = "portrait"
        if page_size is not None:
            orient_attr = page_size.get(qn("w:orient"))
            if orient_attr:
                orientation = "landscape" if orient_attr == "landscape" else "portrait"
            elif width_cm is not None and height_cm is not None and width_cm > height_cm:
                orientation = "landscape"
        elif width_cm is not None and height_cm is not None and width_cm > height_cm:
            orientation = "landscape"

        layouts.append(
            {
                "index": index,
                "page_width_cm": width_cm,
                "page_height_cm": height_cm,
                "margin_top_cm": _margin_cm(page_margins, "top", section.top_margin),
                "margin_bottom_cm": _margin_cm(page_margins, "bottom", section.bottom_margin),
                "margin_left_cm": _margin_cm(page_margins, "left", section.left_margin),
                "margin_right_cm": _margin_cm(page_margins, "right", section.right_margin),
                "orientation": orientation,
            }
        )
    return layouts


def extract_header_footer(doc) -> dict[str, Any]:
    """提取页眉页脚文本及「首页不同 / 奇偶不同」设置。"""

    sections: list[dict[str, Any]] = []
    for section in doc.sections:
        header_text = (
            "" if section.header.is_linked_to_previous else _container_text(section.header)
        )
        footer_text = (
            "" if section.footer.is_linked_to_previous else _container_text(section.footer)
        )
        sections.append(
            {
                "header_text": header_text,
                "footer_text": footer_text,
                "different_first_page": bool(section.different_first_page_header_footer),
                "different_odd_even": bool(doc.settings.odd_and_even_pages_header_footer),
            }
        )

    first = sections[0] if sections else {"header_text": "", "footer_text": ""}
    return {
        "header_text": first["header_text"],
        "footer_text": first["footer_text"],
        "different_first_page": first.get("different_first_page", False),
        "different_odd_even": first.get("different_odd_even", False),
        "sections": sections,
    }


def extract_template_styles(doc) -> dict[str, dict[str, Any]]:
    """从 ``document.styles`` 提取字体（含 eastAsia）与字号。"""

    styles: dict[str, dict[str, Any]] = {}
    for style in doc.styles:
        if style.type not in (WD_STYLE_TYPE.PARAGRAPH, WD_STYLE_TYPE.CHARACTER):
            continue
        try:
            run_properties = style.element.find(qn("w:rPr"))
            font_elements = (
                run_properties.find(qn("w:rFonts")) if run_properties is not None else None
            )
            east_asia = font_elements.get(qn("w:eastAsia")) if font_elements is not None else None
            latin = font_elements.get(qn("w:ascii")) if font_elements is not None else None
            size = style.font.size.pt if style.font.size is not None else None
            bold = style.font.bold
        except (AttributeError, ValueError, KeyError):
            continue
        entry = {
            "font_name": latin or style.font.name or "",
            "east_asia_font": east_asia or "",
            "font_size_pt": size,
            "bold": bool(bold) if bold is not None else None,
        }
        if any(value not in (None, "") for value in entry.values()):
            styles[style.name] = entry
    return styles


def _core_properties(doc) -> dict[str, str]:
    properties = doc.core_properties
    return {
        "title": str(properties.title or ""),
        "author": str(properties.author or ""),
        "subject": str(properties.subject or ""),
        "keywords": str(properties.keywords or ""),
    }


def parse_docx_template(path: str | Path) -> dict[str, Any]:
    """解析 DOCX 模板；任何打开/解析失败都返回 ``{"error": ...}``。"""

    template_path = Path(path)
    if not template_path.exists():
        return {"error": f"文件不存在：{template_path}"}
    try:
        doc = Document(str(template_path))
    except Exception as exc:  # noqa: BLE001 - 模板解析必须优雅降级
        return {"error": f"{type(exc).__name__}: {exc}"}

    try:
        return {
            "path": str(template_path),
            "styles": extract_template_styles(doc),
            "sections": extract_page_layout(doc),
            "header_footer": extract_header_footer(doc),
            "tables": len(doc.tables),
            "paragraphs": len(doc.paragraphs),
            "core_properties": _core_properties(doc),
        }
    except Exception as exc:  # noqa: BLE001 - 防御异常文档结构
        return {"error": f"{type(exc).__name__}: {exc}"}


def validate_template(path: str | Path) -> dict[str, Any]:
    """检查模板是否可用，返回 ``{"valid": bool, "issues": [...]}``。"""

    template_path = Path(path)
    issues: list[dict[str, Any]] = []
    if not template_path.exists():
        return {
            "valid": False,
            "issues": [
                {
                    "code": "missing_file",
                    "severity": "error",
                    "message": f"模板文件不存在：{template_path}",
                }
            ],
        }

    parsed = parse_docx_template(template_path)
    if "error" in parsed:
        return {
            "valid": False,
            "issues": [
                {
                    "code": "invalid_docx",
                    "severity": "error",
                    "message": f"无法解析 DOCX 模板：{parsed['error']}",
                }
            ],
        }

    if not parsed["sections"]:
        issues.append(
            {
                "code": "no_sections",
                "severity": "error",
                "message": "模板没有任何节（section）定义",
            }
        )
    if not parsed["styles"]:
        issues.append(
            {
                "code": "no_styles",
                "severity": "warning",
                "message": "模板未定义可识别的段落样式",
            }
        )
    style_names = set(parsed["styles"])
    missing_headings = [
        name for name in ("Heading 1", "Heading 2", "Heading 3") if name not in style_names
    ]
    if missing_headings:
        issues.append(
            {
                "code": "missing_heading_styles",
                "severity": "warning",
                "message": "模板缺少标题样式：" + "、".join(missing_headings),
            }
        )
    if parsed["tables"] == 0 and parsed["paragraphs"] == 0:
        issues.append(
            {
                "code": "empty_body",
                "severity": "warning",
                "message": "模板正文为空，可能是空白文档",
            }
        )

    valid = not any(issue["severity"] == "error" for issue in issues)
    return {"valid": valid, "issues": issues, "path": str(template_path)}
