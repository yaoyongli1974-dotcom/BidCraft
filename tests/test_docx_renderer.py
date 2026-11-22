import pytest
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from bidcraft.documents.document_ir import (
    DocumentHeaderFooter,
    DocumentIR,
    DocumentPageLayout,
    DocumentParagraph,
    DocumentSection,
    DocumentStyle,
    DocumentTable,
)
from bidcraft.documents.renderer import render_document


def _set_east_asia(style, font_name: str) -> None:
    style.font.name = "Times New Roman"
    run_properties = style.element.get_or_add_rPr()
    fonts = run_properties.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        run_properties.insert(0, fonts)
    fonts.set(qn("w:eastAsia"), font_name)


def _east_asia(style) -> str | None:
    run_properties = style.element.find(qn("w:rPr"))
    if run_properties is None:
        return None
    fonts = run_properties.find(qn("w:rFonts"))
    return fonts.get(qn("w:eastAsia")) if fonts is not None else None


def _full_ir() -> DocumentIR:
    return DocumentIR(
        sections=[
            DocumentSection(
                heading="第一章 技术方案",
                paragraphs=[
                    DocumentParagraph("项目概况正文。", style="Normal"),
                    DocumentParagraph("实施方案", style="Heading 2", level=2),
                    DocumentParagraph("关键步骤", style="Heading 3", level=3),
                    DocumentParagraph("项目经理：{{待填:项目经理}}", style="Normal"),
                ],
                tables=[
                    DocumentTable(
                        rows=[["名称", "数量"], ["{{待填:品牌}}", "2"]],
                        caption="主要设备清单",
                        widths_cm=[6.0, 3.0],
                    )
                ],
            ),
            DocumentSection(
                heading="第二章 商务响应",
                paragraphs=[DocumentParagraph("商务正文。", style="Normal")],
                landscape=True,
                page_break_before=True,
            ),
        ],
        styles={
            "Normal": DocumentStyle("Normal", "宋体", 12.0, False, 1.5, 0, 6),
            "Heading 1": DocumentStyle("Heading 1", "黑体", 18.0, True, 1.5, 12, 8),
            "Heading 2": DocumentStyle("Heading 2", "黑体", 16.0, True, 1.5, 10, 6),
        },
        page_layout=DocumentPageLayout(
            page_width_cm=21.0,
            page_height_cm=29.7,
            margin_top_cm=2.5,
            margin_bottom_cm=2.5,
            margin_left_cm=3.0,
            margin_right_cm=2.5,
            orientation="portrait",
        ),
        header_footer=DocumentHeaderFooter(
            header_text="示例项目投标文件",
            footer_text="投标人：示例公司",
            different_first_page=True,
            different_odd_even=True,
            page_number=True,
        ),
        core_properties={
            "title": "技术投标文件",
            "author": "示例公司",
            "subject": "投标响应",
            "keywords": "投标,技术方案",
        },
    )


def test_render_without_template_writes_structure_and_ooxml(tmp_path):
    output = render_document(_full_ir(), tmp_path / "rendered.docx")

    assert output.exists()
    assert output.stat().st_size > 0

    document = Document(str(output))

    # 东亚字体写入 eastAsia
    assert _east_asia(document.styles["Normal"]) == "宋体"
    assert _east_asia(document.styles["Heading 1"]) == "黑体"
    assert _east_asia(document.styles["Heading 3"]) == "黑体"
    assert document.styles["Heading 1"].font.size.pt == pytest.approx(18.0)
    assert document.styles["Heading 2"].font.size.pt == pytest.approx(16.0)

    # 多级标题与正文
    pairs = [(paragraph.style.name, paragraph.text) for paragraph in document.paragraphs]
    assert ("Heading 1", "第一章 技术方案") in pairs
    assert ("Heading 2", "实施方案") in pairs
    assert ("Heading 3", "关键步骤") in pairs
    assert ("Normal", "项目概况正文。") in pairs
    assert ("Normal", "项目经理：{{待填:项目经理}}") in pairs
    assert ("Heading 1", "第二章 商务响应") in pairs

    # core properties
    assert document.core_properties.title == "技术投标文件"
    assert document.core_properties.author == "示例公司"
    assert document.core_properties.subject == "投标响应"
    assert document.core_properties.keywords == "投标,技术方案"

    # 分节与方向
    assert len(document.sections) == 2
    assert document.sections[0].orientation == WD_ORIENT.PORTRAIT
    assert document.sections[1].orientation == WD_ORIENT.LANDSCAPE
    assert document.sections[1].page_width > document.sections[1].page_height
    assert document.sections[1].page_width.cm == pytest.approx(29.7, abs=0.1)

    # 页眉/页脚：文本 + 首页不同 + 奇偶不同 + 页码域
    assert document.sections[0].header.paragraphs[0].text == "示例项目投标文件"
    footer_text = "\n".join(p.text for p in document.sections[0].footer.paragraphs)
    assert "投标人：示例公司" in footer_text
    assert document.sections[0].different_first_page_header_footer is True
    assert document.sections[0].first_page_header.paragraphs[0].text == "示例项目投标文件"
    assert document.settings.odd_and_even_pages_header_footer is True
    assert document.sections[0].even_page_header.paragraphs[0].text == "示例项目投标文件"
    footer_xml = document.sections[0].footer._element.xml
    assert "PAGE" in footer_xml
    assert "NUMPAGES" in footer_xml

    # 表格：表头加粗、重复标题行、防跨页拆分、列宽、题注
    assert len(document.tables) == 1
    table = document.tables[0]
    assert table.rows[0].cells[0].text == "名称"
    header_run = table.rows[0].cells[0].paragraphs[0].runs[0]
    assert header_run.font.bold is True
    header_properties = table.rows[0]._tr.find(qn("w:trPr"))
    assert header_properties is not None
    assert header_properties.find(qn("w:tblHeader")) is not None
    for row in table.rows:
        row_properties = row._tr.find(qn("w:trPr"))
        assert row_properties is not None
        assert row_properties.find(qn("w:cantSplit")) is not None
    assert table.rows[0].cells[0].width.cm == pytest.approx(6.0, abs=0.1)
    assert table.rows[0].cells[1].width.cm == pytest.approx(3.0, abs=0.1)
    assert "主要设备清单" in [paragraph.text for paragraph in document.paragraphs]
    assert table.cell(1, 0).text == "{{待填:品牌}}"

    # 占位符保留
    all_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "{{待填:项目经理}}" in all_text


def _build_base_template(path):
    document = Document()
    normal = document.styles["Normal"]
    _set_east_asia(normal, "仿宋")
    normal.font.size = Pt(11)
    document.styles["Heading 1"].font.size = Pt(20)

    document.core_properties.title = "模板标题"
    document.core_properties.author = "模板作者"

    section = document.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(3.2)
    section.header.paragraphs[0].text = "模板页眉"
    section.footer.paragraphs[0].text = "模板页脚"

    document.add_paragraph("模板旧正文")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "旧表格"

    document.save(str(path))
    return path


def test_render_with_base_template_clears_body_and_inherits_styles(tmp_path):
    template = _build_base_template(tmp_path / "base.docx")
    ir = DocumentIR(
        sections=[
            DocumentSection(
                heading="新章节",
                paragraphs=[DocumentParagraph("新正文，联系人：{{待填:李四}}")],
            )
        ],
        core_properties={"author": "新作者"},
    )

    output = render_document(ir, tmp_path / "from-template.docx", base_template=template)
    document = Document(str(output))

    # 模板正文被清空，只剩新内容
    texts = [paragraph.text for paragraph in document.paragraphs]
    assert "模板旧正文" not in texts
    assert "新正文，联系人：{{待填:李四}}" in texts
    assert "新章节" in texts
    assert not document.tables
    assert "{{待填:李四}}" in "\n".join(texts)

    # 模板样式、页面设置、页眉页脚被继承
    assert _east_asia(document.styles["Normal"]) == "仿宋"
    assert document.styles["Normal"].font.size.pt == pytest.approx(11.0)
    assert document.styles["Heading 1"].font.size.pt == pytest.approx(20.0)
    assert document.sections[0].page_width.cm == pytest.approx(21.0, abs=0.1)
    assert document.sections[0].left_margin.cm == pytest.approx(3.2, abs=0.1)
    assert document.sections[0].header.paragraphs[0].text == "模板页眉"
    assert document.sections[0].footer.paragraphs[0].text == "模板页脚"

    # 模板 core properties 保留，IR 显式字段覆盖
    assert document.core_properties.title == "模板标题"
    assert document.core_properties.author == "新作者"


def test_render_with_base_template_allows_style_and_layout_override(tmp_path):
    template = _build_base_template(tmp_path / "base.docx")
    ir = DocumentIR(
        sections=[
            DocumentSection(
                heading="横向章节",
                paragraphs=[DocumentParagraph("覆盖样式后的正文")],
                landscape=True,
            )
        ],
        styles={"Normal": DocumentStyle("Normal", "思源宋体", 10.5, False, 1.5, 0, 4)},
        page_layout=DocumentPageLayout(
            page_width_cm=21.0,
            page_height_cm=29.7,
            margin_top_cm=2.0,
            margin_bottom_cm=2.0,
            margin_left_cm=2.0,
            margin_right_cm=2.0,
        ),
    )
    output = render_document(ir, tmp_path / "override.docx", base_template=template)
    document = Document(str(output))

    assert _east_asia(document.styles["Normal"]) == "思源宋体"
    assert document.styles["Normal"].font.size.pt == pytest.approx(10.5)
    assert document.sections[0].orientation == WD_ORIENT.LANDSCAPE
    assert document.sections[0].page_width.cm == pytest.approx(29.7, abs=0.1)
    assert document.sections[0].top_margin.cm == pytest.approx(2.0, abs=0.1)


def test_render_raises_for_missing_base_template(tmp_path):
    with pytest.raises(FileNotFoundError):
        render_document(DocumentIR(), tmp_path / "out.docx", base_template=tmp_path / "nope.docx")


def test_render_centered_paragraph_alignment(tmp_path):
    ir = DocumentIR(
        sections=[
            DocumentSection(
                paragraphs=[DocumentParagraph("居中标题", alignment="center")],
            )
        ]
    )
    output = render_document(ir, tmp_path / "align.docx")
    document = Document(str(output))
    assert document.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER
