import pytest
from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from bidcraft.documents.template_parser import (
    extract_header_footer,
    extract_page_layout,
    extract_template_styles,
    parse_docx_template,
    validate_template,
)


def _set_east_asia(style, font_name: str) -> None:
    style.font.name = "Times New Roman"
    run_properties = style.element.get_or_add_rPr()
    fonts = run_properties.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        run_properties.insert(0, fonts)
    fonts.set(qn("w:eastAsia"), font_name)


def _build_template(path):
    document = Document()
    normal = document.styles["Normal"]
    _set_east_asia(normal, "仿宋")
    normal.font.size = Pt(12)

    heading = document.styles["Heading 1"]
    _set_east_asia(heading, "黑体")
    heading.font.size = Pt(16)

    document.core_properties.title = "模板标题"
    document.core_properties.author = "模板作者"

    first = document.sections[0]
    first.page_width = Cm(21.0)
    first.page_height = Cm(29.7)
    first.top_margin = Cm(2.5)
    first.bottom_margin = Cm(2.2)
    first.left_margin = Cm(3.0)
    first.right_margin = Cm(2.8)
    first.different_first_page_header_footer = True
    first.header.paragraphs[0].text = "招标文件模板页眉"
    first.footer.paragraphs[0].text = "模板页脚"

    document.add_paragraph("模板正文段落")
    table = document.add_table(rows=2, cols=3)
    for row_index, values in enumerate((["名称", "规格", "数量"], ["服务器", "2U", "2"])):
        for column_index, value in enumerate(values):
            table.cell(row_index, column_index).text = value

    landscape = document.add_section(WD_SECTION.NEW_PAGE)
    landscape.orientation = WD_ORIENT.LANDSCAPE
    landscape.page_width = Cm(29.7)
    landscape.page_height = Cm(21.0)
    landscape.left_margin = Cm(1.5)
    landscape.right_margin = Cm(1.5)
    document.add_paragraph("横向页面正文")

    document.save(str(path))
    return path


def test_parse_docx_template_extracts_styles_layout_and_headers(tmp_path):
    template = _build_template(tmp_path / "template.docx")

    parsed = parse_docx_template(template)

    assert "error" not in parsed
    assert parsed["tables"] == 1
    assert parsed["core_properties"]["title"] == "模板标题"
    assert parsed["core_properties"]["author"] == "模板作者"

    assert parsed["styles"]["Normal"]["east_asia_font"] == "仿宋"
    assert parsed["styles"]["Normal"]["font_size_pt"] == pytest.approx(12.0)
    assert parsed["styles"]["Heading 1"]["east_asia_font"] == "黑体"
    assert parsed["styles"]["Heading 1"]["font_size_pt"] == pytest.approx(16.0)

    assert parsed["header_footer"]["header_text"] == "招标文件模板页眉"
    assert parsed["header_footer"]["footer_text"] == "模板页脚"
    assert parsed["header_footer"]["different_first_page"] is True
    # 第二节的页眉页脚继承第一节（linked to previous），不再单独定义。
    assert parsed["header_footer"]["sections"][1]["header_text"] == ""

    assert len(parsed["sections"]) == 2
    portrait, landscape = parsed["sections"]
    assert portrait["orientation"] == "portrait"
    assert portrait["page_width_cm"] == pytest.approx(21.0, abs=0.05)
    assert portrait["page_height_cm"] == pytest.approx(29.7, abs=0.05)
    assert portrait["margin_top_cm"] == pytest.approx(2.5, abs=0.05)
    assert portrait["margin_bottom_cm"] == pytest.approx(2.2, abs=0.05)
    assert portrait["margin_left_cm"] == pytest.approx(3.0, abs=0.05)
    assert portrait["margin_right_cm"] == pytest.approx(2.8, abs=0.05)

    assert landscape["orientation"] == "landscape"
    assert landscape["page_width_cm"] == pytest.approx(29.7, abs=0.05)
    assert landscape["page_height_cm"] == pytest.approx(21.0, abs=0.05)
    assert landscape["margin_left_cm"] == pytest.approx(1.5, abs=0.05)


def test_extract_helpers_work_on_open_document(tmp_path):
    template = _build_template(tmp_path / "template.docx")
    document = Document(str(template))

    layouts = extract_page_layout(document)
    assert [layout["orientation"] for layout in layouts] == ["portrait", "landscape"]

    header_footer = extract_header_footer(document)
    assert header_footer["sections"][0]["header_text"] == "招标文件模板页眉"
    assert header_footer["sections"][0]["footer_text"] == "模板页脚"

    styles = extract_template_styles(document)
    assert styles["Normal"]["font_name"] == "Times New Roman"
    assert styles["Normal"]["east_asia_font"] == "仿宋"


def test_parse_docx_template_returns_error_for_corrupt_or_missing_file(tmp_path):
    corrupt = tmp_path / "corrupt.docx"
    corrupt.write_bytes(b"this is definitely not a zip archive")

    parsed = parse_docx_template(corrupt)
    assert "error" in parsed
    assert parsed["error"]

    missing = parse_docx_template(tmp_path / "missing.docx")
    assert "error" in missing


def test_validate_template_reports_valid_and_invalid(tmp_path):
    template = _build_template(tmp_path / "template.docx")
    result = validate_template(template)
    assert result["valid"] is True
    assert not [issue for issue in result["issues"] if issue["severity"] == "error"]

    corrupt = tmp_path / "corrupt.docx"
    corrupt.write_bytes(b"not a docx")
    invalid = validate_template(corrupt)
    assert invalid["valid"] is False
    assert [issue["code"] for issue in invalid["issues"]] == ["invalid_docx"]

    missing = validate_template(tmp_path / "missing.docx")
    assert missing["valid"] is False
    assert missing["issues"][0]["code"] == "missing_file"
