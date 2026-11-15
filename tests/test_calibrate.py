from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from openpyxl import Workbook
from rich.table import Table
from typer.testing import CliRunner

from bidcraft.calibrate import calibrate_dir, render_report
from bidcraft.cli import app
from bidcraft.parsing.extract import FIELD_ALIAS_AUGMENT, extract
from bidcraft.parsing.loader import load_document
from bidcraft.rules.loader import load_package

FIXTURES = Path(__file__).parent / "fixtures" / "regression"
BOQ_HEADER = ["序号", "名称", "规格型号", "单位", "数量"]
BOQ_ROWS = [
    [1, "信息点面板", "双口 Cat6A", "个", 480],
    [2, "六类非屏蔽双绞线", "Cat6A", "米", 12000],
    [3, "配线架", "24 口", "个", 20],
    [4, "理线架", "24 口", "个", 20],
    [5, "机柜", "42U", "台", 4],
    [6, "桥架", "200×100", "米", 860],
]


def make_boq_xlsx(path: Path) -> Path:
    """Generate the BOQ fixture described in fixtures/regression/README."""
    workbook = Workbook()
    cover = workbook.active
    cover.title = "封面"
    for line in [
        "项目名称：某园区弱电智能化工程（一期）",
        "采购项目编号：WZ-2026-0715",
        "采购人：某园区开发建设有限公司",
        "预算金额：人民币 420 万元",
        "投标截止时间：2026 年 12 月 18 日 09:30",
        "项目地点：某市某区产业园 A 区",
        "计划工期：120 日历天",
        "质量保证期：3 年",
        "投标有效期：90 天",
        "投标保证金：人民币 8 万元",
        "资格要求：投标人须具备电子与智能化工程专业承包二级及以上资质",
        "评标办法：综合评分法",
        "招标范围：综合布线系统、视频监控系统的设备采购与安装调试",
        "信息点数量：480 个",
        "线缆等级：Cat6A",
        "测试标准：GB 50311-2016",
    ]:
        cover.append([line])

    boq = workbook.create_sheet("工程量清单")
    boq.append(BOQ_HEADER)
    for row in BOQ_ROWS:
        boq.append(row)
    workbook.save(path)
    return path


def make_regression_dir(base: Path) -> Path:
    root = base / "regression"
    root.mkdir()
    for name in ("weakcurrent-tender.md", "fire-tender.md"):
        shutil.copy(FIXTURES / name, root / name)
    make_boq_xlsx(root / "boq-weakcurrent.xlsx")
    return root


def test_calibrate_dir_three_fixtures(tmp_path):
    root = make_regression_dir(tmp_path)
    report = calibrate_dir(root)

    aggregate = report["aggregate"]
    assert aggregate["files"] == 3
    assert aggregate["ok"] == 3
    assert aggregate["failed"] == 0
    assert aggregate["required_hit_rate"] >= 0.8
    assert aggregate["ocr_files"] == 0

    files = {item["file"]: item for item in report["per_file"]}
    assert set(files) == {
        "weakcurrent-tender.md",
        "fire-tender.md",
        "boq-weakcurrent.xlsx",
    }
    assert all(item["error"] is None for item in files.values())
    assert files["boq-weakcurrent.xlsx"]["materials"] >= 5
    assert files["boq-weakcurrent.xlsx"]["tables"] >= 1
    assert files["weakcurrent-tender.md"]["materials"] == 0  # Markdown 无结构表

    for key in (
        "project.number",
        "project.buyer",
        "project.duration",
        "project.warranty",
        "project.deposit",
        "project.bid_validity",
    ):
        assert aggregate["field_hits"][key] == 3


def test_fixture_key_facts_and_new_aliases(tmp_path):
    weak = load_document(FIXTURES / "weakcurrent-tender.md")
    weak_result = extract(
        weak.text,
        load_package("weakcurrent-cabling"),
        pages=weak.pages,
        tables=weak.tables,
    )
    assert weak_result.fields["project.number"].value == "WZ-2026-0715"
    assert weak_result.fields["project.buyer"].value == "某园区开发建设有限公司"
    assert weak_result.fields["project.duration"].value.startswith("120")
    assert weak_result.fields["project.warranty"].value == "3 年"
    assert weak_result.fields["project.deposit"].value.startswith("人民币 8 万元")

    fire = load_document(FIXTURES / "fire-tender.md")
    fire_result = extract(
        fire.text,
        load_package("fire-protection"),
        pages=fire.pages,
        tables=fire.tables,
    )
    assert fire_result.fields["project.number"].value == "XF-2026-0233"
    assert fire_result.fields["project.buyer"].value == "某市卫生健康委员会"
    assert fire_result.fields["project.duration"].value.startswith("180")
    # 缺陷责任期是本次新增别名，消防件用它作为质保口径。
    assert fire_result.fields["project.warranty"].value == "24 个月"
    assert fire_result.fields["fire.sprinkler_count"].value.startswith("1860")
    assert fire_result.fields["fire.detector_count"].value.startswith("920")


@pytest.mark.parametrize(
    ("key", "line", "expected"),
    [
        ("project.number", "采购编号：CG-2026-01", "CG-2026-01"),
        ("project.buyer", "建设单位：某建设单位", "某建设单位"),
        ("project.duration", "计划工期：45 日历天", "45"),
        ("project.warranty", "缺陷责任期：18 个月", "18"),
        ("project.warranty", "免费维保期：2 年", "2"),
        ("project.deposit", "磋商保证金：人民币 1 万元", "人民币 1 万元"),
        ("project.bid_validity", "报价有效期：60 天", "60"),
    ],
)
def test_alias_augmentation_hits_variants(key, line, expected):
    text = f"项目名称：别名校准测试工程\n{line}"
    package = load_package("common-bid-facts")
    result = extract(text, package, pages=[text])
    assert expected in result.fields[key].value


def test_alias_augmentation_is_additive():
    assert "缺陷责任期" in FIELD_ALIAS_AUGMENT["project.warranty"]
    assert "磋商保证金" in FIELD_ALIAS_AUGMENT["project.deposit"]
    assert "计划工期" in FIELD_ALIAS_AUGMENT["project.duration"]
    assert "免费维保期" in FIELD_ALIAS_AUGMENT["project.warranty"]
    # 既有规则包别名仍在，未被覆盖。
    package = load_package("common-bid-facts")
    warranty = package.field_map()["project.warranty"]
    assert "质保期" in warranty.aliases
    assert "保修期" in warranty.aliases


def test_bad_pdf_records_error_and_continues(tmp_path):
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-1.4\nnot a real pdf\x00\x01")
    shutil.copy(FIXTURES / "weakcurrent-tender.md", tmp_path / "weak.md")

    report = calibrate_dir(tmp_path)
    assert report["aggregate"]["files"] == 2
    assert report["aggregate"]["ok"] == 1
    assert report["aggregate"]["failed"] == 1
    bad = next(item for item in report["per_file"] if item["file"] == "bad.pdf")
    assert bad["error"]
    assert bad["needs_ocr"] is True
    assert report["aggregate"]["required_hit_rate"] >= 0.8


def test_recursive_scan(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    shutil.copy(FIXTURES / "weakcurrent-tender.md", nested / "weak.md")

    assert calibrate_dir(root)["aggregate"]["files"] == 0
    report = calibrate_dir(root, recursive=True)
    assert report["aggregate"]["files"] == 1
    assert report["per_file"][0]["file"] == "nested/weak.md"
    assert report["recursive"] is True


def test_render_report_returns_rich_table(tmp_path):
    report = calibrate_dir(make_regression_dir(tmp_path))
    table = render_report(report)
    assert isinstance(table, Table)
    assert table.row_count == 4  # 3 个文件 + 合计行


def test_cli_calibrate_writes_json(tmp_path):
    root = make_regression_dir(tmp_path)
    output = tmp_path / "report.json"
    result = CliRunner().invoke(
        app, ["calibrate", str(root), "--output", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert "回归校准" in result.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["aggregate"]["files"] == 3
    assert data["aggregate"]["required_hit_rate"] >= 0.8


def test_cli_calibrate_missing_dir_exits_one(tmp_path):
    result = CliRunner().invoke(app, ["calibrate", str(tmp_path / "missing")])
    assert result.exit_code == 1
    assert "目录不存在" in result.output
