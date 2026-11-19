from typing import ClassVar

from docx import Document

from bidcraft.export import templates
from bidcraft.export.docx import export_docx


def _nodes(body="正文内容，用于验证样式。"):
    return [{"heading": "第一章 项目理解", "body": body}]


def test_preset_list_and_normalize():
    items = templates.preset_list()
    ids = [item["id"] for item in items]
    assert ids[:2] == ["standard", "gov"]
    assert all("margins" not in item for item in items)

    fallback = templates.normalize(None)
    assert fallback["id"] == "standard"

    custom = templates.normalize(
        {
            "id": "gov",
            "body_font": "  楷体  ",
            "body_size": 99,
            "line_spacing": 0.1,
            "cover": False,
            "margins": [1.0, 9.0, 2.0, 2.0],
        }
    )
    assert custom["body_font"] == "楷体"
    assert custom["body_size"] == 18.0
    assert custom["line_spacing"] == templates.LINE_SPACINGS[0]
    assert custom["cover"] is False
    assert custom["margins"] == [1.0, 4.0, 2.0, 2.0]


def test_unknown_template_rejected(tmp_path):
    class Project:
        meta: ClassVar[dict] = {}

    try:
        templates.save(Project(), "nope")
    except ValueError as exc:
        assert "未知导出模板" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_save_and_load_roundtrip(tmp_path):
    class Project:
        meta: ClassVar[dict] = {}

    project = Project()
    assert templates.load(project)["id"] == "standard"
    templates.save(project, "tech")
    assert templates.load(project)["id"] == "tech"
    assert project.meta[templates.SETTING_KEY] == "tech"


def test_docx_applies_template_fonts(tmp_path):
    path = export_docx(
        tmp_path / "gov.docx",
        _ledger(),
        _nodes(),
        template={"id": "gov"},
    )
    document = Document(str(path))
    assert document.styles["Normal"].font.size.pt == 16
    east_asian = document.styles["Normal"].element.rPr.rFonts.get(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia"
    )
    assert east_asian == "仿宋_GB2312"
    assert document.styles["Heading 1"].font.size.pt == 22


def test_docx_compact_skips_cover_and_toc(tmp_path):
    path = export_docx(
        tmp_path / "compact.docx",
        _ledger(),
        _nodes(),
        template={"id": "compact"},
    )
    text = "\n".join(p.text for p in Document(str(path)).paragraphs)
    # compact 模板保留目录但不要封面
    assert "目  录" in text
    assert "投标文件" not in text.split("第一章")[0]


def _ledger():
    from bidcraft.ledger import FactLedger

    return FactLedger(project_id="p1", facts=[])
