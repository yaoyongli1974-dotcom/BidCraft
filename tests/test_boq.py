from __future__ import annotations

import pytest
from openpyxl import Workbook, load_workbook

from bidcraft.boq import (
    align,
    match_score,
    normalize,
    parse_boq,
    parse_qty,
)
from bidcraft.export.boq_xlsx import ALIGN_HEADERS, export_boq_xlsx
from bidcraft.models import Material


def _material(
    id_: str,
    name: str,
    spec: str = "",
    unit: str = "",
    qty: float | None = None,
    note: str = "",
) -> Material:
    return Material(
        id=id_, project_id="p1", name=name, spec=spec, unit=unit, qty=qty, note=note
    )


def _item(
    row: int,
    name: str,
    spec: str = "",
    unit: str = "",
    qty: float | None = None,
    seq: str = "",
    note: str = "",
) -> dict:
    return {
        "row": row,
        "seq": seq or str(row),
        "name": name,
        "spec": spec,
        "unit": unit,
        "qty": qty,
        "note": note,
    }


# --------------------------------------------------------------------- parser


def _write_boq(path, sheets: dict[str, list[list]]) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    workbook.save(path)


def test_parser_preserves_blank_columns_and_merged_headers(tmp_path):
    path = tmp_path / "boq.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    sheet.append(["某园区弱电工程工程量清单"])  # title row above the header
    sheet.merge_cells("A1:G1")
    sheet.append(["序号", "材料名称", "", "规格型号", "单位", "数量", "备注"])
    sheet.merge_cells("B2:C2")  # merged header spanning two columns
    sheet.append([1, "网络面板", "", "双口 Cat6A", "个", "1,200", ""])
    sheet.append([2, "六类非屏蔽双绞线", "", "Cat6A UTP", "", "12套", "含桥架"])
    sheet.append(["序号", "材料名称", "", "规格型号", "单位", "数量", ""])  # repeated header
    sheet.append([3, "机柜", "", "42U", "台", "约 3.5", ""])
    workbook.save(path)

    parsed = parse_boq(path)

    assert parsed.sheet == "工程量清单"
    assert parsed.header_row == 2
    assert [item["row"] for item in parsed.items] == [3, 4, 6]
    assert [item["seq"] for item in parsed.items] == ["1", "2", "3"]
    assert parsed.items[0]["name"] == "网络面板"
    assert parsed.items[0]["qty"] == 1200.0
    # blank unit column stays empty instead of shifting the note into it
    assert parsed.items[1]["unit"] == ""
    assert parsed.items[1]["spec"] == "Cat6A UTP"
    assert parsed.items[1]["qty"] == 12.0
    assert parsed.items[1]["note"] == "含桥架"
    assert parsed.items[2]["qty"] == 3.5
    assert parsed.warnings == []


def test_parser_warns_on_unparseable_qty(tmp_path):
    path = tmp_path / "boq.xlsx"
    _write_boq(
        path,
        {
            "清单": [
                ["序号", "名称", "规格型号", "单位", "数量"],
                [1, "桥架", "200x100", "米", "待定"],
                [2, "线管", "", "米", "１２０"],
            ]
        },
    )
    parsed = parse_boq(path)
    assert parsed.items[0]["qty"] is None
    assert parsed.items[1]["qty"] == 120.0
    assert len(parsed.warnings) == 1
    assert "数量无法识别" in parsed.warnings[0]
    assert "待定" in parsed.warnings[0]


def test_parser_chooses_sheet_with_most_rows(tmp_path):
    path = tmp_path / "boq.xlsx"
    _write_boq(
        path,
        {
            "说明": [["工程量清单说明"], ["本表仅作说明"]],
            "主清单": [
                ["序号", "名称", "规格型号", "单位", "数量"],
                [1, "网络面板", "双口", "个", 10],
                [2, "配线架", "24口", "个", 2],
                [3, "机柜", "42U", "台", 1],
            ],
            "补充": [
                ["序号", "名称", "规格型号", "单位", "数量"],
                [1, "跳线", "3米", "根", 5],
            ],
        },
    )
    parsed = parse_boq(path)
    assert parsed.sheet == "主清单"
    assert len(parsed.items) == 3

    chosen = parse_boq(path, sheet="补充")
    assert chosen.sheet == "补充"
    assert len(chosen.items) == 1


def test_parser_missing_sheet_lists_available(tmp_path):
    path = tmp_path / "boq.xlsx"
    _write_boq(
        path,
        {"清单": [["序号", "名称", "规格型号", "单位", "数量"], [1, "面板", "双口", "个", 4]]},
    )
    with pytest.raises(ValueError) as excinfo:
        parse_boq(path, sheet="不存在")
    message = str(excinfo.value)
    assert "不存在" in message
    assert "清单" in message


def test_parser_errors_without_header(tmp_path):
    path = tmp_path / "boq.xlsx"
    _write_boq(path, {"Sheet1": [["一些说明文字"], ["没有表头"], ["1", "2", "3"]]})
    with pytest.raises(ValueError) as excinfo:
        parse_boq(path)
    message = str(excinfo.value)
    assert "工程量清单" in message
    assert "名称" in message


def test_parser_rejects_non_xlsx(tmp_path):
    path = tmp_path / "boq.csv"
    path.write_text("名称,数量\n面板,1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_boq(path)


def test_parse_qty_variants():
    assert parse_qty("1,200") == (1200.0, True)
    assert parse_qty("12套") == (12.0, True)
    assert parse_qty("约 3.5") == (3.5, True)
    assert parse_qty("１２０") == (120.0, True)
    assert parse_qty("—") == (None, True)
    assert parse_qty(None) == (None, True)
    assert parse_qty(18) == (18.0, True)
    assert parse_qty("待定") == (None, False)


def test_parser_extracts_unit_prices_and_amount_fallback(tmp_path):
    path = tmp_path / "priced.xlsx"
    _write_boq(
        path,
        {
            "清单": [
                ["序号", "材料名称", "规格型号", "单位", "数量", "单价（元）", "合价（元）"],
                [1, "网络面板", "双口 Cat6A", "个", "10", "￥12.50", "125.00"],
                [2, "配线架", "24 口", "个", "20", "1,234.56", "24,691.20"],
                [3, "跳线", "3 米", "根", "5", "", "37.50"],
                [4, "机柜", "42U", "台", "2", "待定", ""],
            ]
        },
    )

    parsed = parse_boq(path)

    assert parsed.prices == {
        "清单!2": 12.5,
        "清单!3": 1234.56,
        "清单!4": 7.5,
    }
    assert len(parsed.warnings) == 1
    assert "单价无法识别" in parsed.warnings[0]
    assert "待定" in parsed.warnings[0]


def test_parser_price_header_requires_exact_or_prefix_match(tmp_path):
    path = tmp_path / "boq.xlsx"
    _write_boq(
        path,
        {
            "清单": [
                ["序号", "名称", "规格型号", "单位", "数量", "含税单价", "备注"],
                [1, "面板", "双口", "个", "10", "12.5", ""],
            ]
        },
    )

    parsed = parse_boq(path)

    # "含税单价" merely contains 单价; only exact/prefix aliases are recognized.
    assert parsed.prices == {}


def test_parser_prefers_priced_sheet_on_tie(tmp_path):
    path = tmp_path / "boq.xlsx"
    header = ["序号", "名称", "规格型号", "单位", "数量"]
    rows = [
        [1, "网络面板", "双口", "个", 10],
        [2, "配线架", "24口", "个", 20],
    ]
    _write_boq(
        path,
        {
            "无价": [header, *rows],
            "有价": [
                header + ["投标单价"],
                *[row + [12.5] for row in rows],
            ],
        },
    )

    parsed = parse_boq(path)
    assert parsed.sheet == "有价"
    assert parsed.prices == {"有价!2": 12.5, "有价!3": 12.5}
    assert parse_boq(path, sheet="无价").prices == {}


# -------------------------------------------------------------------- matcher


def test_normalize_strips_punctuation_and_case():
    assert normalize(" 六类（UTP）线缆 A-1 ") == "六类utp线缆a1"


def test_match_exact_name_and_spec():
    item = _item(3, "网络面板", spec="双口 Cat6A", unit="个", qty=480)
    material = _material("m1", "网络面板", "双口 Cat6A", "个", 480)
    assert match_score(item, material) == 1.0
    rows = align([item], [material], [], "清单")
    assert rows[0]["status"] == "matched"
    assert rows[0]["match_score"] == 1.0
    assert rows[0]["ledger"]["material_id"] == "m1"
    assert rows[0]["diff"] == {"qty": None, "unit": None, "spec": None}


def test_match_spec_diff_and_qty_diff():
    item = _item(4, "配线架", spec="24 口", unit="个", qty=20)
    material = _material("m2", "配线架", "48 口", "个", 20)
    rows = align([item], [material], [], "清单")
    row = rows[0]
    assert row["status"] == "spec_diff"
    assert row["match_score"] == pytest.approx(0.90)
    assert row["diff"]["spec"] == {"boq": "24 口", "ledger": "48 口"}

    item_qty = _item(5, "配线架", spec="24 口", unit="个", qty=30)
    rows = align([item_qty], [material], [], "清单")
    assert rows[0]["status"] == "qty_diff"
    assert rows[0]["diff"]["qty"] == {"boq": 30.0, "ledger": 20.0, "delta": 10.0}


def test_qty_tolerance_is_relative():
    item = _item(6, "线缆", spec="Cat6A", unit="米", qty=1000)
    within = _material("m3", "线缆", "Cat6A", "米", 1004.9)
    assert align([item], [within], [], "清单")[0]["status"] == "matched"
    outside = _material("m4", "线缆", "Cat6A", "米", 1006)
    assert align([item], [outside], [], "清单")[0]["status"] == "qty_diff"


def test_integral_qty_off_by_one_is_a_diff():
    item = _item(8, "信息点面板", unit="个", qty=481)
    material = _material("m8", "信息点面板", "", "个", 480)
    rows = align([item], [material], [], "清单")
    assert rows[0]["status"] == "qty_diff"
    assert rows[0]["diff"]["qty"] == {"boq": 481.0, "ledger": 480.0, "delta": 1.0}


def test_unit_diff_priority_over_spec_diff():
    item = _item(7, "机柜", spec="42U", unit="台", qty=4)
    material = _material("m5", "机柜", "42U 标准", "个", 4)
    rows = align([item], [material], [], "清单")
    assert rows[0]["status"] == "unit_diff"
    assert rows[0]["diff"]["unit"] == {"boq": "台", "ledger": "个"}
    assert rows[0]["diff"]["spec"] == {"boq": "42U", "ledger": "42U 标准"}


def test_one_to_one_assignment_never_reuses_material():
    items = [_item(2, "网络面板", unit="个", qty=10), _item(3, "网络面板", unit="个", qty=10)]
    materials = [
        _material("m6", "网络面板", "", "个", 10),
        _material("m7", "网络面板", "", "个", 10),
    ]
    rows = align(items, materials, [], "清单")
    ids = [row["ledger"]["material_id"] for row in rows]
    assert ids == ["m6", "m7"]
    assert [row["status"] for row in rows] == ["matched", "matched"]

    single = align(items, materials[:1], [], "清单")
    assert [row["status"] for row in single] == ["matched", "missing_in_ledger"]


def test_missing_on_both_sides():
    item = _item(2, "完全陌生的设备", spec="XYZ", unit="台", qty=1)
    material = _material("m8", "台账独有材料", "ABC", "米", 12)
    rows = align([item], [material], [], "清单")
    assert rows[0]["status"] == "missing_in_ledger"
    assert rows[0]["key"] == "清单!2"
    assert rows[0]["ledger"] is None
    assert rows[1]["status"] == "missing_in_boq"
    assert rows[1]["key"] == "ledger:m8"
    assert rows[1]["boq_row"] is None
    assert rows[1]["ledger"]["material_id"] == "m8"
    assert rows[1]["ledger"]["qty"] == 12


def test_ignored_rows_keep_computed_status():
    item = _item(2, "没有台账的设备")
    rows = align([item], [], ["清单!2"], "清单")
    assert rows[0]["status"] == "missing_in_ledger"
    assert rows[0]["ignored"] is True
    assert align([item], [], [], "清单")[0]["ignored"] is False


def test_fuzzy_similarity_and_threshold():
    item = _item(2, "光模块", unit="个", qty=2)
    material = _material("m9", "光模块A", "", "个", 2)
    score = match_score(item, material)
    assert 0.75 <= score < 0.90
    assert align([item], [material], [], "清单")[0]["status"] == "matched"

    unrelated = _material("m10", "消防主机", "", "台", 1)
    assert match_score(item, unrelated) == 0.0
    assert align([item], [unrelated], [], "清单")[1]["status"] == "missing_in_boq"


def test_containment_match_requires_short_side():
    item = _item(2, "24口光纤配线架", unit="个", qty=2)
    material = _material("m11", "光纤配线架", "", "个", 2)
    assert match_score(item, material) == pytest.approx(0.82)
    # a three-character name is too short to trust containment
    short = _material("m12", "配线架", "", "个", 2)
    assert match_score(_item(3, "24口配线架"), short) == 0.0


def test_alignment_attaches_user_price():
    items = [
        _item(2, "网络面板", unit="个", qty=100),
        _item(3, "配线架", unit="个", qty=None),
    ]
    rows = align(items, [], [], "清单", {"清单!2": 12.5, "清单!3": 8})
    assert rows[0]["price"] == {"unit_price": 12.5, "amount": 1250.0}
    assert rows[1]["price"] == {"unit_price": 8.0, "amount": None}


# --------------------------------------------------------------------- export


def _payload() -> dict:
    items = [
        _item(3, "网络面板", spec="双口 Cat6A", unit="个", qty=480, seq="1"),
        _item(4, "配线架", spec="24 口", unit="个", qty=20, seq="2"),
        _item(5, "缺失设备", spec="XX", unit="台", qty=1, seq="3"),
    ]
    materials = [
        _material("m1", "网络面板", "双口 Cat6A", "个", 480),
        _material("m2", "配线架", "24 口", "个", 20),
        _material("m3", "台账独有材料", "YY", "米", 8),
    ]
    rows = align(
        items,
        materials,
        ["清单!5"],
        "清单",
        {"清单!3": 12.5, "清单!4": 100},
    )
    summary = {
        "boq_rows": 3,
        "matched": 2,
        "qty_diff": 0,
        "unit_diff": 0,
        "spec_diff": 0,
        "missing_in_ledger": 1,
        "missing_in_boq": 1,
        "ignored": 1,
        "total_diffs": 0,
        "match_rate": 0.667,
        "priced": 2,
        "unpriced": 0,
        "quantity_missing": 0,
        "total_amount": 8000.0,
    }
    return {
        "snapshot": {"id": "s1", "filename": "清单.xlsx"},
        "rows": rows,
        "summary": summary,
        "ledger_summary": {"materials": 3, "matched": 2, "missing_in_boq": 1},
    }


def test_export_sheets_values_and_fills(tmp_path):
    path = export_boq_xlsx(tmp_path / "out" / "boq.xlsx", _payload())
    workbook = load_workbook(path)
    assert workbook.sheetnames == ["清单对齐", "报价汇总", "差异汇总"]

    sheet = workbook["清单对齐"]
    assert [cell.value for cell in sheet[1]] == ALIGN_HEADERS
    assert sheet["A2"].value == "1"
    assert sheet["B2"].value == 3
    assert sheet["C2"].value == "网络面板"
    assert sheet["F2"].value == 480
    assert sheet["G2"].value == 12.5
    assert sheet["H2"].value == 6000
    assert sheet["I2"].value == "双口 Cat6A"
    assert sheet["K2"].value == 480
    assert sheet["M2"].value == "一致"
    assert sheet["N2"].value == 1.0
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref is not None
    assert sheet["M2"].fill.fgColor.rgb == "00C6EFCE"

    # ignored row keeps its computed status but the gray fill wins
    assert sheet["M4"].value == "清单有·台账无（已忽略）"
    assert sheet["A4"].fill.fgColor.rgb == "00D9D9D9"

    # ledger-only row
    assert sheet["M5"].value == "台账有·清单无"
    assert sheet["C5"].value == "台账独有材料"
    assert sheet["D5"].value in (None, "")
    assert sheet["I5"].value == "YY"
    assert sheet["A5"].fill.fgColor.rgb == "00FFEB9C"

    quote = workbook["报价汇总"]
    assert [cell.value for cell in quote[1]] == ["指标", "数值"]
    quote_mapping = {row[0].value: row[1].value for row in quote.iter_rows(min_row=2)}
    assert quote_mapping["已报价"] == 2
    assert quote_mapping["未报价"] == 0
    assert quote_mapping["报价合计（元）"] == 8000.0

    summary = workbook["差异汇总"]
    assert [cell.value for cell in summary[1]] == ["指标", "数量"]
    mapping = {row[0].value: row[1].value for row in summary.iter_rows(min_row=2)}
    assert mapping["清单条目"] == 3
    assert mapping["已匹配"] == 2
    assert mapping["已忽略"] == 1
    assert mapping["匹配率"] == 0.667


def test_export_qty_diff_fill_and_delta(tmp_path):
    item = _item(3, "线缆", spec="Cat6A", unit="米", qty=1000)
    material = _material("m1", "线缆", "Cat6A", "米", 800)
    payload = {
        "snapshot": {"id": "s1", "filename": "清单.xlsx"},
        "rows": align([item], [material], [], "清单"),
        "summary": {
            "boq_rows": 1,
            "matched": 0,
            "qty_diff": 1,
            "unit_diff": 0,
            "spec_diff": 0,
            "missing_in_ledger": 0,
            "missing_in_boq": 0,
            "ignored": 0,
            "total_diffs": 1,
            "match_rate": 1.0,
        },
        "ledger_summary": {},
    }
    path = export_boq_xlsx(tmp_path / "boq.xlsx", payload)
    sheet = load_workbook(path)["清单对齐"]
    assert sheet["M2"].value == "数量差异"
    assert sheet["L2"].value == 200.0
    assert sheet["A2"].fill.fgColor.rgb == "00FCE4D6"
