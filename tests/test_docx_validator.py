from docx import Document

from bidcraft.documents.document_ir import (
    DocumentIR,
    DocumentParagraph,
    DocumentSection,
    DocumentTable,
)
from bidcraft.documents.renderer import render_document
from bidcraft.documents.validator import build_document_report, validate_docx_package


def _render(ir, path):
    output = render_document(ir, path)
    assert output.exists()
    return output


def test_validator_detects_heading_skip_and_placeholders(tmp_path):
    ir = DocumentIR(
        sections=[
            DocumentSection(
                heading="第一章 概述",
                paragraphs=[
                    DocumentParagraph("概述正文。"),
                    DocumentParagraph("跳过二级直接到三级", style="Heading 3", level=3),
                    DocumentParagraph("联系人：{{待填:项目经理}}"),
                ],
            )
        ],
        core_properties={"title": "校验测试文档", "author": "测试人"},
    )
    output = _render(ir, tmp_path / "skip.docx")

    result = validate_docx_package(output)

    assert result["valid"] is False
    codes = [issue["code"] for issue in result["issues"]]
    assert "heading_skip" in codes
    assert "placeholder" in codes

    heading_issue = next(issue for issue in result["issues"] if issue["code"] == "heading_skip")
    assert heading_issue["severity"] == "error"
    assert heading_issue["from_level"] == 1
    assert heading_issue["to_level"] == 3

    placeholder_issue = next(issue for issue in result["issues"] if issue["code"] == "placeholder")
    assert placeholder_issue["severity"] == "warning"
    assert placeholder_issue["count"] == 1
    assert "{{待填:项目经理}}" in placeholder_issue["text"]
    assert placeholder_issue["paragraph_index"] is not None


def test_validator_accepts_clean_document(tmp_path):
    ir = DocumentIR(
        sections=[
            DocumentSection(
                heading="第一章 概述",
                paragraphs=[
                    DocumentParagraph("正文内容。"),
                    DocumentParagraph("二级小节", style="Heading 2", level=2),
                    DocumentParagraph("三级细节", style="Heading 3", level=3),
                ],
                tables=[
                    DocumentTable(
                        rows=[["名称", "数量"], ["服务器", "2"]],
                        caption="设备清单",
                    )
                ],
            )
        ],
        core_properties={"title": "干净文档", "author": "作者"},
    )
    output = _render(ir, tmp_path / "clean.docx")

    result = validate_docx_package(output)

    assert result["valid"] is True
    assert [issue for issue in result["issues"] if issue["severity"] == "error"] == []
    assert not [issue for issue in result["issues"] if issue["code"] == "placeholder"]
    assert not [issue for issue in result["issues"] if issue["code"] == "heading_skip"]


def test_validator_flags_missing_table_header_and_core_properties(tmp_path):
    ir = DocumentIR(
        sections=[
            DocumentSection(
                heading="第一章 概述",
                paragraphs=[DocumentParagraph("正文。")],
                tables=[DocumentTable(rows=[["", "数量"], ["服务器", "2"]], repeat_header=False)],
            )
        ]
    )
    output = _render(ir, tmp_path / "bad-table.docx")

    result = validate_docx_package(output)
    codes = [issue["code"] for issue in result["issues"]]

    assert "table_missing_header" in codes
    assert "table_header_not_repeated" in codes
    assert "missing_core_properties" in codes
    assert result["valid"] is False


def test_validator_reports_invalid_package(tmp_path):
    corrupt = tmp_path / "corrupt.docx"
    corrupt.write_bytes(b"definitely not a docx package")

    result = validate_docx_package(corrupt)
    assert result["valid"] is False
    assert result["issues"][0]["code"] == "invalid_docx"

    missing = validate_docx_package(tmp_path / "missing.docx")
    assert missing["valid"] is False
    assert missing["issues"][0]["code"] == "missing_file"


def test_validator_flags_blank_document(tmp_path):
    path = tmp_path / "blank.docx"
    Document().save(str(path))

    result = validate_docx_package(path)
    assert result["valid"] is False
    assert "blank_document" in [issue["code"] for issue in result["issues"]]


def test_build_document_report_summarizes_ir_and_validation(tmp_path):
    ir = DocumentIR(
        sections=[
            DocumentSection(
                heading="第一章 技术方案",
                paragraphs=[
                    DocumentParagraph("正文 {{待填:甲}}"),
                    DocumentParagraph("更多 {{待填:乙}}"),
                ],
                tables=[DocumentTable(rows=[["名称"], ["服务器"]])],
                images=[{"path": str(tmp_path / "missing.png"), "alt": "示意图"}],
            )
        ],
        core_properties={"title": "报告测试文档"},
    )

    report = build_document_report(ir, {"valid": True, "issues": []})

    assert report["title"] == "报告测试文档"
    assert report["sections"] == 1
    assert report["headings"] == ["第一章 技术方案"]
    assert report["paragraph_count"] == 2
    assert report["table_count"] == 1
    assert report["image_count"] == 1
    assert report["placeholder_count"] == 2
    assert len(report["placeholder_locations"]) == 2
    assert report["missing_images"] == [str(tmp_path / "missing.png")]
    assert report["valid"] is False
    assert "ir_missing_image" in [issue["code"] for issue in report["issues"]]
    assert "报告测试文档" in report["summary"]
    assert "未通过" in report["summary"]
    assert report["validation"] == {"valid": True, "issues": []}
