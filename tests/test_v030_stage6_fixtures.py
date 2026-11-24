"""Stage 6 A：全合成回归夹具 + 逐文件值/表格下限基线的契约测试。"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from bidcraft.calibrate import (
    calibrate_dir,
    compare_baseline,
    load_baseline,
    save_baseline,
)

FIXTURES = Path(__file__).parent / "fixtures" / "regression"
BASELINE = FIXTURES / "baseline.json"
SENTINEL = "<!-- SYNTHETIC: no real data -->"

PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
COMPANY_RE = re.compile(r"有限公司|有限责任公司|股份有限公司|集团有限公司|集团公司|公司|集团|股份")


def all_fixtures() -> list[Path]:
    return sorted(FIXTURES.glob("*.md"))


def synthetic_fixtures() -> list[Path]:
    return [path for path in all_fixtures() if path.name.startswith("synthetic-")]


def write_baseline(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ---------- 夹具本体 ----------


def test_every_fixture_starts_with_synthetic_sentinel():
    fixtures = all_fixtures()
    assert len(fixtures) >= 10  # 6 份行业件 + 4 份 Stage 6 合成件
    for path in fixtures:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines and lines[0] == SENTINEL, f"{path.name} 首行缺少 SYNTHETIC 哨兵"


def test_stage6_adds_three_to_six_synthetic_fixtures():
    assert 3 <= len(synthetic_fixtures()) <= 6


def test_synthetic_fixtures_have_no_real_identifiers():
    """合成件不得出现手机号/身份证号/邮箱/真实公司后缀，编号必须 SYN- 前缀。"""

    paths = synthetic_fixtures()
    assert paths
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not PHONE_RE.search(text), f"{path.name} 含疑似手机号"
        assert not ID_RE.search(text), f"{path.name} 含疑似身份证号"
        assert not EMAIL_RE.search(text), f"{path.name} 含邮箱地址"
        assert not COMPANY_RE.search(text), f"{path.name} 含公司/集团/股份后缀"

        numbers = re.findall(
            r"^(?:采购)?(?:项目|招标)编号[：:]\s*(\S+)", text, re.MULTILINE
        )
        assert numbers, f"{path.name} 缺少合成项目编号"
        assert all(number.startswith("SYN-") for number in numbers), (
            f"{path.name} 编号不是 SYN- 前缀：{numbers}"
        )


def test_synthetic_fixtures_keep_tender_noise_features():
    """合成件必须真的覆盖表格、分页/OCR 噪声、大写金额与跨章引用。"""

    for path in synthetic_fixtures():
        text = path.read_text(encoding="utf-8")
        assert "| ---" in text, f"{path.name} 缺表格"
        assert "大写" in text, f"{path.name} 缺中文大写金额"
        assert re.search(r"[壹贰叁肆伍陆柒捌玖拾]", text), f"{path.name} 缺中文数字"
        assert "第 " in text and "页" in text, f"{path.name} 缺分页噪声"
        assert "章" in text and ("见第" in text or "以第" in text), (
            f"{path.name} 缺跨章引用"
        )


# ---------- 基线契约 ----------


def test_committed_baseline_has_per_fixture_expectations():
    data = load_baseline(BASELINE)
    assert data["schema"] == 1
    specs = data.get("fixtures") or {}
    assert set(specs) == {path.name for path in synthetic_fixtures()}
    for name, spec in specs.items():
        assert spec["expected_values"], f"{name} 缺 expected_values"
        assert isinstance(spec["expected_values"], dict)
        assert "min_tables" in spec and "min_materials" in spec


def test_all_fixtures_ok_and_expected_keys_present():
    report = calibrate_dir(FIXTURES)
    assert report["aggregate"]["files"] == len(all_fixtures())
    assert report["aggregate"]["ok"] == len(all_fixtures())
    assert report["aggregate"]["failed"] == 0
    by_file = {item["file"]: item for item in report["per_file"]}
    assert all(item["error"] is None for item in by_file.values())

    specs = load_baseline(BASELINE)["fixtures"]
    for name, spec in specs.items():
        item = by_file[name]
        assert set(spec["expected_values"]) <= set(item["values"]), name
        for key, expected in spec["expected_values"].items():
            assert item["values"][key] == expected, f"{name}::{key}"
        assert item["tables"] >= spec["min_tables"], name
        assert item["materials"] >= spec["min_materials"], name


def test_save_baseline_fixtures_is_optional(tmp_path):
    report = calibrate_dir(FIXTURES)
    old_style = save_baseline(report, tmp_path / "old.json")
    assert "fixtures" not in json.loads(old_style.read_text(encoding="utf-8"))

    specs = load_baseline(BASELINE)["fixtures"]
    new_style = save_baseline(report, tmp_path / "new.json", fixtures=specs)
    data = load_baseline(new_style)
    assert data["fixtures"] == specs
    assert data["aggregate"] == report["aggregate"]


# ---------- compare_baseline 的新判定 ----------


def baseline_payload(tmp_path: Path, aggregate: dict, fixtures: dict) -> Path:
    return write_baseline(
        tmp_path,
        {
            "schema": 1,
            "saved_at": "2026-11-24T00:00:00+00:00",
            "aggregate": aggregate,
            "fixtures": fixtures,
        },
    )


def test_compare_detects_value_drift(tmp_path):
    report = calibrate_dir(FIXTURES)
    target = "synthetic-appendix-tender.md"
    expected = load_baseline(BASELINE)["fixtures"][target]["expected_values"][
        "project.budget"
    ]

    mutated = copy.deepcopy(report)
    item = next(item for item in mutated["per_file"] if item["file"] == target)
    item["values"]["project.budget"] = "人民币 零元整"

    path = baseline_payload(
        tmp_path,
        report["aggregate"],
        {target: {"expected_values": {"project.budget": expected}}},
    )
    result = compare_baseline(mutated, path)
    assert result["ok"] is False
    reg = next(
        r
        for r in result["regressions"]
        if r["metric"] == f"values.{target}.project.budget"
    )
    assert "字段值漂移" in reg["message"]
    assert reg["baseline"] == expected
    assert reg["current"] == "人民币 零元整"


def test_compare_detects_missing_value(tmp_path):
    report = calibrate_dir(FIXTURES)
    target = "synthetic-boq-tender.md"
    expected = load_baseline(BASELINE)["fixtures"][target]["expected_values"][
        "project.number"
    ]

    mutated = copy.deepcopy(report)
    item = next(item for item in mutated["per_file"] if item["file"] == target)
    del item["values"]["project.number"]

    path = baseline_payload(
        tmp_path,
        report["aggregate"],
        {target: {"expected_values": {"project.number": expected}}},
    )
    result = compare_baseline(mutated, path)
    assert result["ok"] is False
    reg = next(
        r
        for r in result["regressions"]
        if r["metric"] == f"values.{target}.project.number"
    )
    assert "字段值缺失" in reg["message"]


def test_compare_detects_table_and_material_count_drift(tmp_path):
    report = calibrate_dir(FIXTURES)
    target = "synthetic-crossref-tender.md"
    path = baseline_payload(
        tmp_path,
        report["aggregate"],
        {target: {"min_tables": 2, "min_materials": 3}},
    )
    result = compare_baseline(report, path)
    assert result["ok"] is False
    by_metric = {reg["metric"]: reg for reg in result["regressions"]}
    assert "表格数回归" in by_metric[f"tables.{target}"]["message"]
    assert "材料行数回归" in by_metric[f"materials.{target}"]["message"]
    assert by_metric[f"tables.{target}"]["baseline"] == 2
    assert by_metric[f"materials.{target}"]["baseline"] == 3


def test_compare_detects_missing_fixture(tmp_path):
    report = calibrate_dir(FIXTURES)
    target = "synthetic-pagination-tender.md"
    path = baseline_payload(
        tmp_path, report["aggregate"], {target: {"min_tables": 0}}
    )
    mutated = copy.deepcopy(report)
    mutated["per_file"] = [
        item for item in mutated["per_file"] if item["file"] != target
    ]
    result = compare_baseline(mutated, path)
    assert result["ok"] is False
    assert any(
        reg["metric"] == f"fixture.{target}.present" for reg in result["regressions"]
    )


def test_old_baseline_without_new_keys_still_passes(tmp_path):
    """旧基线（schema=1、无 fixtures）必须逐字节兼容，跳过逐文件判定。"""

    report = calibrate_dir(FIXTURES)
    path = write_baseline(
        tmp_path,
        {
            "schema": 1,
            "saved_at": "2026-11-18T02:28:09.061027+00:00",
            "aggregate": report["aggregate"],
        },
    )
    result = compare_baseline(report, path)
    assert result["ok"] is True
    assert result["regressions"] == []
    assert result["improvements"] == []
