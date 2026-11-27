from __future__ import annotations

from bidcraft.boq import align
from bidcraft.ledger import _dedupe_materials
from bidcraft.models import Material
from bidcraft.parsing.extract import extract_materials
from bidcraft.parsing.loader import Table
from bidcraft.schemas import MaterialItem


def _table() -> Table:
    return Table(
        page=1,
        rows=[
            ["序号", "名称", "规格型号", "单位", "数量"],
            ["1", "电梯专用摄像机", "400万像素，广角≥110°", "台", "20"],
            ["2", " 电梯专用摄像机 ", "400万像素，广角≥110° ", "台", "20"],
            ["3", "（按清单序号4至24逐项填列）", "", "", ""],
            ["4", "—", "", "", ""],
            ["5", "半球网络摄像机", "400万像素，H.265", "台", "34"],
        ],
    )


def test_extract_materials_filters_noise_and_dedupes():
    items = extract_materials([_table()], "")
    names = [item.name for item in items]
    assert names == ["电梯专用摄像机", "半球网络摄像机"]
    assert all(item.qty is not None for item in items)
    assert items[0].qty == 20
    assert items[1].spec == "400万像素，H.265"


def test_dedupe_materials_accepts_stable_variants_only():
    items = [
        MaterialItem(name="电梯专用摄像机", spec="400万像素", unit="台", qty=20),
        MaterialItem(name=" 电梯专用摄像机 ", spec=" 400万像素 ", unit="台", qty=20),
        MaterialItem(name="（按清单序号4至24逐项填列）", spec="", unit="", qty=None),
        MaterialItem(name="—", spec="", unit="", qty=None),
        MaterialItem(name="半球网络摄像机", spec="H.265", unit="台", qty=34),
        MaterialItem(name="半球网络摄像机", spec="H.265 红外", unit="台", qty=34),
    ]
    result = _dedupe_materials(items)
    assert [(item.name, item.spec) for item in result] == [
        ("电梯专用摄像机", "400万像素"),
        ("半球网络摄像机", "H.265"),
        ("半球网络摄像机", "H.265 红外"),
    ]


def test_align_skips_existing_noise_but_keeps_real_duplicate_rows():
    materials = [
        Material(name="电梯专用摄像机", spec="400万像素，广角≥110°", unit="台", qty=20),
        Material(name="（按清单序号4至24逐项填列）", spec="", unit="", qty=None),
        Material(name="—", spec="", unit="", qty=None),
        Material(name=" 电梯专用摄像机 ", spec="400万像素，广角≥110° ", unit="台", qty=20),
    ]
    items = [
        {
            "row": 1,
            "name": "电梯专用摄像机",
            "spec": "400万像素，广角≥110°",
            "unit": "台",
            "qty": 20,
        }
    ]
    rows = align(items, materials, [], "工程量清单")
    names = [(row["ledger"] or {}).get("name", "") for row in rows]
    assert "（按清单序号4至24逐项填列）" not in names
    assert "—" not in names
    # 已被清单行占用的重复台账行参与匹配后，剩余重复行只展示一条
    assert names.count("电梯专用摄像机") + names.count(" 电梯专用摄像机 ") == 1
    assert rows[0]["status"] == "matched"
