"""专业 DOCX 文档引擎：文档中间表示、模板解析、渲染与导出后校验。"""

from .document_ir import (
    DEFAULT_BODY_FONT,
    DEFAULT_HEADING_FONT,
    DocumentHeaderFooter,
    DocumentIR,
    DocumentPageLayout,
    DocumentParagraph,
    DocumentSection,
    DocumentStyle,
    DocumentTable,
    default_styles,
    from_markdown_sections,
    heading_level,
)
from .renderer import render_document
from .template_parser import (
    extract_header_footer,
    extract_page_layout,
    extract_template_styles,
    parse_docx_template,
    validate_template,
)
from .validator import build_document_report, validate_docx_package

__all__ = [
    "DEFAULT_BODY_FONT",
    "DEFAULT_HEADING_FONT",
    "DocumentHeaderFooter",
    "DocumentIR",
    "DocumentPageLayout",
    "DocumentParagraph",
    "DocumentSection",
    "DocumentStyle",
    "DocumentTable",
    "build_document_report",
    "default_styles",
    "extract_header_footer",
    "extract_page_layout",
    "extract_template_styles",
    "from_markdown_sections",
    "heading_level",
    "parse_docx_template",
    "render_document",
    "validate_docx_package",
    "validate_template",
]
