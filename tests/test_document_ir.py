from bidcraft.documents.document_ir import (
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

BODY = """
## 第一节 项目概况

本项目为某数据中心建设项目。

### 子项说明

- 要点一
- 要点二

1. 步骤一
2. 步骤二

| 名称 | 数量 |
| --- | --- |
| 服务器 | 2 |
| 交换机 | 4 |

![拓扑图](/tmp/topology.png)

{{待填:项目经理}}
"""


def test_from_markdown_sections_parses_headings_tables_and_lists():
    ir = from_markdown_sections([{"heading": "第一章 总体方案", "body": BODY}], title="投标文件")

    assert isinstance(ir, DocumentIR)
    assert len(ir.sections) == 1
    section = ir.sections[0]
    assert section.heading == "第一章 总体方案"
    assert section.level == 1

    triples = [(p.style, p.level, p.text) for p in section.paragraphs]
    assert ("Heading 2", 2, "第一节 项目概况") in triples
    assert ("Heading 3", 3, "子项说明") in triples
    assert ("Normal", 0, "本项目为某数据中心建设项目。") in triples
    assert ("List Bullet", 0, "要点一") in triples
    assert ("List Bullet", 0, "要点二") in triples
    assert ("List Number", 0, "步骤一") in triples
    assert ("List Number", 0, "步骤二") in triples

    assert len(section.tables) == 1
    table = section.tables[0]
    assert table.rows == [["名称", "数量"], ["服务器", "2"], ["交换机", "4"]]
    assert table.header_row is True
    assert table.repeat_header is True

    assert section.images == [{"path": "/tmp/topology.png", "alt": "拓扑图"}]
    assert any(p.text == "{{待填:项目经理}}" for p in section.paragraphs)
    assert ir.core_properties["title"] == "投标文件"
    assert ir.count_placeholders() == 1


def test_from_markdown_sections_accepts_multiple_nodes_and_title_fallback():
    ir = from_markdown_sections(
        [
            {"heading": "第一章", "body": "正文一"},
            {"heading": "第二章", "body": "## 小节\n\n正文二"},
        ],
        author="示例公司",
    )
    assert [section.heading for section in ir.sections] == ["第一章", "第二章"]
    assert ir.core_properties["title"] == "第一章"
    assert ir.core_properties["author"] == "示例公司"
    assert ir.sections[1].paragraphs[0].style == "Heading 2"


def test_document_ir_defaults_and_counts():
    ir = DocumentIR()
    assert ir.sections == []
    assert ir.styles == {}
    assert ir.page_layout is None
    assert ir.header_footer is None
    assert ir.count_placeholders() == 0

    section = ir.add_section(
        DocumentSection(
            heading="{{待填:章节名}}",
            paragraphs=[
                DocumentParagraph("{{待填:a}} 与 {{待填:b}}"),
                DocumentParagraph("普通文本"),
            ],
            tables=[
                DocumentTable(rows=[["{{待填:c}}", "值"]], caption="{{待填:表题}}"),
            ],
        )
    )
    assert ir.sections[0] is section
    assert ir.count_placeholders() == 5
    locations = dict(ir.iter_placeholders())
    assert locations["section:0:heading"] == 1
    assert locations["section:0:paragraph:0"] == 2
    assert locations["section:0:table:0:caption"] == 1


def test_default_style_objects_and_heading_level_helper():
    styles = default_styles()
    assert styles["Normal"].font_name == "宋体"
    assert styles["Heading 1"].font_name == "黑体"
    assert isinstance(styles["Heading 1"], DocumentStyle)

    assert heading_level("Heading 2") == 2
    assert heading_level("heading3") == 3
    assert heading_level("标题 1") == 1
    assert heading_level("Normal") is None
    assert heading_level(None) is None


def test_page_and_header_footer_dataclasses():
    layout = DocumentPageLayout(page_width_cm=29.7, page_height_cm=21.0, orientation="landscape")
    assert layout.is_landscape is True

    header_footer = DocumentHeaderFooter(
        header_text="页眉",
        footer_text="页脚",
        different_first_page=True,
        different_odd_even=True,
        page_number=True,
    )
    assert header_footer.page_number is True
    assert header_footer.different_first_page is True
