from __future__ import annotations

from openpyxl import Workbook, load_workbook

from bidcraft.export.xlsx import export_xlsx
from bidcraft.parsing.extract import extract
from bidcraft.parsing.loader import load_document
from bidcraft.rules.loader import load_package
from bidcraft.schemas import FactItem, FactLedger, MaterialItem, ScoringItemModel


def _make_workbook(path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    sheet.append(["序号", "名称", "规格型号", "单位", "数量"])
    sheet.append([1, "信息面板", "双口 Cat6A", "个", 480])
    sheet.append([2, "水平线缆", "Cat6A UTP", "米", 12000])
    scoring = workbook.create_sheet("评分办法")
    scoring.append(["评审因素", "分值", "评分标准"])
    scoring.append(["技术方案", 40, "方案完整、针对性强"])
    scoring.append(["投标报价", 30, "低价优先"])
    workbook.save(path)


def test_read_xlsx_tables_and_extract(tmp_path):
    path = tmp_path / "tender.xlsx"
    _make_workbook(path)
    parsed = load_document(path)
    assert len(parsed.tables) == 2
    assert "信息面板" in parsed.text
    result = extract(parsed.text, load_package("weakcurrent-cabling"), pages=parsed.pages, tables=parsed.tables)
    assert any(item.name == "信息面板" and item.qty == 480 for item in result.materials)
    assert any(item.name == "技术方案" and item.score == 40 for item in result.scoring)


def test_export_xlsx_contains_all_sheets(tmp_path):
    ledger = FactLedger(
        facts=[FactItem(key="project.name", label="项目名称", value="某项目", status="confirmed")],
        materials=[MaterialItem(name="信息面板", spec="双口", unit="个", qty=480)],
        scoring=[ScoringItemModel(name="技术方案", score=40, criteria="完整")],
    )
    path = export_xlsx(tmp_path / "ledger.xlsx", ledger)
    workbook = load_workbook(path)
    assert workbook.sheetnames == ["事实台账", "材料清单", "技术参数", "评分响应", "风险与要求"]
    assert workbook["事实台账"]["C2"].value == "某项目"
    assert workbook["材料清单"]["E2"].value == 480
    assert workbook["评分响应"]["C2"].value == 40
