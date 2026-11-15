from __future__ import annotations

from conftest import make_docx_tender

from bidcraft.parsing.extract import extract
from bidcraft.parsing.loader import load_document
from bidcraft.rules.loader import load_package


def test_extract_fields_from_text(tender_path):
    parsed = load_document(tender_path)
    package = load_package("weakcurrent-cabling")
    result = extract(parsed.text, package, pages=parsed.pages, tables=parsed.tables)

    assert result.fields["project.name"].value == "某园区智能化工程（一期）"
    assert result.fields["project.number"].value == "ZB-2026-0412"
    assert "350" in result.fields["project.budget"].value
    assert "480" in result.fields["cabling.point_count"].value
    assert result.fields["cabling.cable_grade"].value == "Cat6A"
    assert result.fields["cabling.test_standard"].value == "GB 50311-2016"
    assert "90" in result.fields["project.bid_validity"].value
    assert result.fields["project.content"].value
    assert len(result.risks) >= 3
    assert len(result.scoring) == 5
    assert any(item.name == "技术方案" and item.score == 40 for item in result.scoring)


def test_extract_materials_and_scoring_from_docx(tmp_path):
    path = make_docx_tender(tmp_path / "tender.docx")
    parsed = load_document(path)
    package = load_package("weakcurrent-cabling")
    result = extract(parsed.text, package, pages=parsed.pages, tables=parsed.tables)

    assert len(parsed.tables) >= 2
    names = {item.name for item in result.materials}
    assert "信息点面板" in names
    panel = next(item for item in result.materials if item.name == "信息点面板")
    assert panel.qty == 480
    assert panel.unit == "个"
    assert any(item.name == "技术方案" and item.score == 40 for item in result.scoring)
    assert result.fields["project.name"].value.startswith("某园区")


def test_template_sections_are_detected(tender_path):
    parsed = load_document(tender_path)
    package = load_package("weakcurrent-cabling")
    result = extract(parsed.text, package, pages=parsed.pages, tables=parsed.tables)
    assert len(result.template_sections) == 7
    assert any("投标函" in item for item in result.template_sections)
