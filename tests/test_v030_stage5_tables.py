"""v0.30 Stage 5 A：表格分类标记、图片上限统一与正文字符守卫。"""

from __future__ import annotations

import pytest
from docx import Document
from docx.oxml.ns import qn

from bidcraft.documents import renderer as renderer_module
from bidcraft.documents.document_ir import (
    TABLE_KIND_PREFIX,
    DocumentIR,
    DocumentParagraph,
    DocumentSection,
    DocumentTable,
    table_kind_from_marker,
)
from bidcraft.documents.renderer import render_document, save_document_verified
from bidcraft.documents.validator import validate_docx_package
from bidcraft.export.docx import _add_table, export_docx
from bidcraft.schemas import FactItem, FactLedger

DATA_TABLE_ISSUES = {"table_missing_header", "table_no_data", "table_header_not_repeated"}


def _ledger() -> FactLedger:
    return FactLedger(
        project_id="p1",
        facts=[
            FactItem(key="project.name", value="分类测试项目", status="confirmed"),
            FactItem(key="bidder.name", value="示例公司", status="confirmed"),
        ],
    )


def _ir_with_table(kind: str) -> DocumentIR:
    return DocumentIR(
        sections=[
            DocumentSection(
                heading="第一章 商务响应",
                paragraphs=[DocumentParagraph("正文内容。")],
                tables=[
                    DocumentTable(
                        rows=[["", "填写值"], ["签字", "张三"]],
                        kind=kind,
                    )
                ],
            )
        ],
        core_properties={"title": "表格分类测试", "author": "测试人"},
    )


def _marker(table) -> str | None:
    properties = table._tbl.find(qn("w:tblPr"))
    if properties is None:
        return None
    description = properties.find(qn("w:tblDescription"))
    return description.get(qn("w:val")) if description is not None else None


def _codes(result: dict) -> set[str]:
    return {issue["code"] for issue in result["issues"]}


@pytest.mark.parametrize("kind", ["layout", "form", "content"])
def test_non_data_tables_with_blank_first_row_pass_validator(tmp_path, kind):
    path = render_document(_ir_with_table(kind), tmp_path / f"{kind}.docx")

    document = Document(str(path))
    assert _marker(document.tables[0]) == f"{TABLE_KIND_PREFIX}{kind}"
    assert table_kind_from_marker(_marker(document.tables[0])) == kind

    result = validate_docx_package(path)
    assert not (DATA_TABLE_ISSUES & _codes(result))
    assert result["valid"] is True


def test_data_table_with_blank_header_still_errors(tmp_path):
    path = render_document(_ir_with_table("data"), tmp_path / "data.docx")

    document = Document(str(path))
    assert _marker(document.tables[0]) == f"{TABLE_KIND_PREFIX}data"

    result = validate_docx_package(path)
    codes = _codes(result)
    assert "table_missing_header" in codes
    assert result["valid"] is False
    issue = next(item for item in result["issues"] if item["code"] == "table_missing_header")
    assert issue["severity"] == "error"


def test_marker_survives_professional_and_simple_export(tmp_path):
    nodes = [
        {
            "heading": "第一章 设备清单",
            "body": "| 名称 | 数量 |\n| --- | --- |\n| 信息点 | 480 |",
        }
    ]
    for professional in (True, False):
        path = export_docx(
            tmp_path / f"export-{professional}.docx",
            _ledger(),
            nodes,
            professional=professional,
        )
        document = Document(str(path))
        assert len(document.tables) == 1
        assert _marker(document.tables[0]) == f"{TABLE_KIND_PREFIX}data"
        result = validate_docx_package(path)
        # 简单引擎历来不写跨页重复表头（仅为 warning）；数据表格的报错级检查
        # 仍须通过，标记也必须被校验器识别为 data。
        assert "table_missing_header" not in _codes(result)
        assert "table_no_data" not in _codes(result)
        assert table_kind_from_marker(_marker(document.tables[0])) == "data"


def test_simple_export_writer_marker_is_read_by_validator(tmp_path):
    document = Document()
    # 直接复用简单引擎的表格写入器，模拟商务标封面键值表（首行留空）。
    _add_table(document, [["", "填写值"], ["签字", "张三"]], kind="form")
    path = save_document_verified(document, tmp_path / "form.docx")

    assert _marker(Document(str(path)).tables[0]) == f"{TABLE_KIND_PREFIX}form"
    result = validate_docx_package(path)
    assert not (DATA_TABLE_ISSUES & _codes(result))


@pytest.mark.parametrize("professional", [False, True])
@pytest.mark.parametrize("limit", [0, None])
def test_generic_image_lines_respect_cap(tmp_path, professional, limit):
    body = "\n".join(f"![](relative-{index}.png)" for index in range(501))
    nodes = [{"heading": "第一章 图片", "body": body}]

    with pytest.raises(ValueError, match="图片数超过"):
        export_docx(
            tmp_path / f"images-{professional}-{limit}.docx",
            _ledger(),
            nodes,
            professional=professional,
            max_images=limit,
        )


def test_body_char_caps_boundary_and_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(renderer_module, "MAX_TOTAL_BODY_CHARS", 100)
    monkeypatch.setattr(renderer_module, "MAX_NODE_BODY_CHARS", 100)
    within = export_docx(
        tmp_path / "within.docx",
        _ledger(),
        [{"heading": "章节", "body": "x" * 100}],
    )
    assert within.exists()

    with pytest.raises(ValueError, match="正文总字符数超过导出上限 100"):
        export_docx(
            tmp_path / "too-long.docx",
            _ledger(),
            [{"heading": "章节", "body": "x" * 101}],
        )


def test_per_node_char_cap_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(renderer_module, "MAX_TOTAL_BODY_CHARS", 1000)
    monkeypatch.setattr(renderer_module, "MAX_NODE_BODY_CHARS", 100)

    with pytest.raises(ValueError, match="第 1 章正文字符数超过导出上限 100"):
        render_document(
            DocumentIR(
                sections=[
                    DocumentSection(
                        heading="章节",
                        paragraphs=[DocumentParagraph("x" * 101)],
                    )
                ]
            ),
            tmp_path / "node-too-long.docx",
        )


def test_direct_render_document_rejects_excessive_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(renderer_module, "MAX_NODES", 1)
    ir = DocumentIR(
        sections=[DocumentSection(heading="第一章"), DocumentSection(heading="第二章")]
    )

    with pytest.raises(ValueError, match="章节数超过导出上限 1"):
        render_document(ir, tmp_path / "many-sections.docx")


def test_direct_render_document_rejects_excessive_images(tmp_path, monkeypatch):
    monkeypatch.setattr(renderer_module, "MAX_IMAGES", 0)
    ir = DocumentIR(sections=[DocumentSection(images=[{"path": "relative.png"}])])

    with pytest.raises(ValueError, match="图片数超过导出上限 0"):
        render_document(ir, tmp_path / "many-images.docx")
