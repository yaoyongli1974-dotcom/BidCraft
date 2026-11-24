from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bidcraft.calibrate import (
    calibrate_dir,
    compare_baseline,
    load_baseline,
    save_baseline,
)
from bidcraft.cli import app

FIXTURES = Path(__file__).parent / "fixtures" / "regression"
COMMITTED_BASELINE = FIXTURES / "baseline.json"
ORIGINAL_FIXTURE_NAMES = (
    "weakcurrent-tender.md",
    "fire-tender.md",
    "installation-tender.md",
    "civil-tender.md",
    "hvac-tender.md",
    "decoration-tender.md",
)


def all_md_fixture_names() -> list[str]:
    return sorted(path.name for path in FIXTURES.glob("*.md"))


def make_original_fixtures_dir(base: Path) -> Path:
    """只放原始六份行业件，让既有 CLI 断言不受 Stage 6 合成件影响。"""

    root = base / "original"
    root.mkdir()
    for name in ORIGINAL_FIXTURE_NAMES:
        shutil.copy(FIXTURES / name, root / name)
    return root


def make_report(**aggregate_overrides):
    """合成校准报告（aggregate 可控），用于逐项断言回归/改进规则。"""
    aggregate = {
        "files": 7,
        "ok": 7,
        "failed": 0,
        "field_hits": {
            "project.number": 7,
            "project.buyer": 7,
            "project.duration": 6,
        },
        "by_type": {
            "weakcurrent-cabling": {"files": 7, "ok": 7, "required_hit_rate": 0.9}
        },
        "required_hit_rate": 0.9,
        "avg_materials": 5.0,
        "ocr_files": 0,
    }
    aggregate.update(aggregate_overrides)
    return {
        "root": "/data/bids",
        "recursive": False,
        "per_file": [{"file": "a.md"}],
        "aggregate": aggregate,
    }


def compare_mutated(tmp_path, **overrides):
    path = save_baseline(make_report(), tmp_path / "baseline.json")
    return compare_baseline(make_report(**overrides), path)


def make_mutated_dir(base: Path) -> Path:
    """复制夹具后删掉一份文件：ok 减少、字段命中数下降 → 必然回归。"""
    root = base / "mutated"
    root.mkdir()
    for md in sorted(FIXTURES.glob("*.md")):
        shutil.copy(md, root / md.name)
    (root / "decoration-tender.md").unlink()
    return root


# ---------- save / load ----------


def test_save_load_roundtrip(tmp_path):
    report = make_report()
    path = save_baseline(report, tmp_path / "nested" / "dir" / "baseline.json")
    assert path.is_file()  # 父目录自动创建

    data = load_baseline(path)
    assert data["schema"] == 1
    datetime.fromisoformat(data["saved_at"])  # isoformat 可解析
    assert data["aggregate"] == report["aggregate"]
    # 基线只存 aggregate，不含机器相关字段
    assert "root" not in data
    assert "recursive" not in data
    assert "per_file" not in data


@pytest.mark.parametrize("schema", [2, 0, None])
def test_load_rejects_wrong_schema(tmp_path, schema):
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps({"schema": schema, "saved_at": "x", "aggregate": {}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_baseline(path)


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_baseline(tmp_path / "missing.json")


# ---------- compare 规则 ----------


def test_compare_identical_report_ok(tmp_path):
    report = make_report()
    path = save_baseline(report, tmp_path / "baseline.json")
    other = copy.deepcopy(report)
    # root/recursive/per_file 与 saved_at 不参与比对
    other["root"] = "/elsewhere"
    other["recursive"] = True
    other["per_file"] = []
    result = compare_baseline(other, path)
    assert result["ok"] is True
    assert result["regressions"] == []
    assert result["improvements"] == []
    assert result["baseline_saved_at"] == load_baseline(path)["saved_at"]


def test_compare_missing_baseline_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        compare_baseline(make_report(), tmp_path / "missing.json")


def test_regression_missing_field_key(tmp_path):
    result = compare_mutated(
        tmp_path, field_hits={"project.number": 7, "project.buyer": 7}
    )
    assert result["ok"] is False
    reg = next(
        r for r in result["regressions"] if r["metric"] == "field_hits.project.duration"
    )
    assert reg["message"] == "字段不再命中：project.duration"
    assert reg["baseline"] == 6
    assert reg["current"] == 0


def test_regression_field_count_drop(tmp_path):
    result = compare_mutated(
        tmp_path,
        field_hits={"project.number": 7, "project.buyer": 7, "project.duration": 4},
    )
    assert result["ok"] is False
    reg = next(
        r for r in result["regressions"] if r["metric"] == "field_hits.project.duration"
    )
    assert reg["message"] == "字段命中下降：project.duration 6→4"
    assert reg["baseline"] == 6
    assert reg["current"] == 4


def test_regression_required_hit_rate_drop(tmp_path):
    result = compare_mutated(tmp_path, required_hit_rate=0.85)  # 降 0.05 > 0.02
    assert result["ok"] is False
    reg = next(r for r in result["regressions"] if r["metric"] == "required_hit_rate")
    assert "必填命中率下降" in reg["message"]
    assert reg["baseline"] == 0.9
    assert reg["current"] == 0.85


def test_tolerance_boundary(tmp_path):
    # 恰好降 0.02（== tolerance）不算回归
    at_limit = compare_mutated(tmp_path, required_hit_rate=0.88)
    assert at_limit["ok"] is True
    assert at_limit["regressions"] == []
    # 降 0.021（严格 > tolerance）算回归
    over_limit = compare_mutated(tmp_path, required_hit_rate=0.879)
    assert over_limit["ok"] is False
    assert any(r["metric"] == "required_hit_rate" for r in over_limit["regressions"])


def test_tolerance_is_configurable(tmp_path):
    path = save_baseline(make_report(), tmp_path / "baseline.json")
    result = compare_baseline(
        make_report(required_hit_rate=0.85), path, tolerance=0.05
    )
    assert result["ok"] is True  # 降 0.05 == 自定义 tolerance，不算


def test_regression_failed_up_and_ok_down(tmp_path):
    result = compare_mutated(tmp_path, failed=1, ok=6)
    assert result["ok"] is False
    by_metric = {r["metric"]: r for r in result["regressions"]}
    assert by_metric["failed"]["message"] == "失败文件增加：0→1"
    assert by_metric["failed"]["baseline"] == 0
    assert by_metric["failed"]["current"] == 1
    assert by_metric["ok"]["message"] == "成功文件减少：7→6"
    assert by_metric["ok"]["baseline"] == 7
    assert by_metric["ok"]["current"] == 6


def test_regression_avg_materials_drop_20_percent(tmp_path):
    result = compare_mutated(tmp_path, avg_materials=4.0)  # 相对下降 20% > 10%
    assert result["ok"] is False
    reg = next(r for r in result["regressions"] if r["metric"] == "avg_materials")
    assert "平均材料下降" in reg["message"]
    assert reg["baseline"] == 5.0
    assert reg["current"] == 4.0


def test_avg_materials_drop_exactly_10_percent_not_regression(tmp_path):
    result = compare_mutated(tmp_path, avg_materials=4.5)  # 恰好 10%，不算
    assert result["ok"] is True
    assert result["regressions"] == []


def test_improvements_new_key_rate_up_materials_up(tmp_path):
    result = compare_mutated(
        tmp_path,
        field_hits={
            "project.number": 7,
            "project.buyer": 7,
            "project.duration": 6,
            "project.deposit": 3,
        },
        required_hit_rate=0.95,
        avg_materials=6.0,
    )
    assert result["ok"] is True
    assert result["regressions"] == []
    by_metric = {i["metric"]: i for i in result["improvements"]}
    assert by_metric["field_hits.project.deposit"]["message"] == "新增字段命中：project.deposit"
    assert by_metric["field_hits.project.deposit"]["baseline"] == 0
    assert by_metric["field_hits.project.deposit"]["current"] == 3
    assert "必填命中率上升" in by_metric["required_hit_rate"]["message"]
    assert "平均材料上升" in by_metric["avg_materials"]["message"]


def test_regressions_win_over_improvements(tmp_path):
    result = compare_mutated(tmp_path, required_hit_rate=0.95, failed=1)
    assert result["ok"] is False
    assert result["improvements"]  # 有改进也不掩盖回归


# ---------- CLI ----------


def test_cli_save_baseline(tmp_path):
    path = tmp_path / "baseline.json"
    original = make_original_fixtures_dir(tmp_path)
    result = CliRunner().invoke(
        app, ["calibrate", str(original), "--save-baseline", str(path)]
    )
    assert result.exit_code == 0, result.output
    assert path.is_file()
    assert "基线已保存" in result.output
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == 1
    assert data["aggregate"]["files"] == 6
    assert "root" not in data and "per_file" not in data


def test_cli_compare_same_dir_exit_zero(tmp_path):
    path = tmp_path / "baseline.json"
    runner = CliRunner()
    saved = runner.invoke(
        app, ["calibrate", str(FIXTURES), "--save-baseline", str(path)]
    )
    assert saved.exit_code == 0, saved.output
    result = runner.invoke(
        app, ["calibrate", str(FIXTURES), "--compare-baseline", str(path)]
    )
    assert result.exit_code == 0, result.output
    assert "基线比对通过" in result.output


def test_cli_compare_mutated_dir_exit_one(tmp_path):
    path = tmp_path / "baseline.json"
    runner = CliRunner()
    saved = runner.invoke(
        app, ["calibrate", str(FIXTURES), "--save-baseline", str(path)]
    )
    assert saved.exit_code == 0, saved.output
    result = runner.invoke(
        app,
        [
            "calibrate",
            str(make_mutated_dir(tmp_path)),
            "--compare-baseline",
            str(path),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "回归" in result.output


def test_cli_compare_missing_baseline_exit_two(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "calibrate",
            str(FIXTURES),
            "--compare-baseline",
            str(tmp_path / "missing.json"),
        ],
    )
    assert result.exit_code == 2, result.output
    assert "基线文件不存在" in result.output


def test_cli_compare_then_save_on_regression(tmp_path):
    """两者同时给：先比对（决定退出码）再覆盖保存。"""
    path = tmp_path / "baseline.json"
    runner = CliRunner()
    saved = runner.invoke(
        app, ["calibrate", str(FIXTURES), "--save-baseline", str(path)]
    )
    assert saved.exit_code == 0, saved.output
    result = runner.invoke(
        app,
        [
            "calibrate",
            str(make_mutated_dir(tmp_path)),
            "--compare-baseline",
            str(path),
            "--save-baseline",
            str(path),
        ],
    )
    assert result.exit_code == 1, result.output  # 退出码取自比对
    data = json.loads(path.read_text(encoding="utf-8"))
    # 比对后仍覆盖保存了变异结果（全部夹具删掉一份）
    assert data["aggregate"]["ok"] == len(all_md_fixture_names()) - 1


def test_cli_compare_then_save_when_ok(tmp_path):
    path = tmp_path / "baseline.json"
    runner = CliRunner()
    runner.invoke(app, ["calibrate", str(FIXTURES), "--save-baseline", str(path)])
    result = runner.invoke(
        app,
        [
            "calibrate",
            str(FIXTURES),
            "--compare-baseline",
            str(path),
            "--save-baseline",
            str(path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "基线比对通过" in result.output
    assert "基线已保存" in result.output
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["aggregate"]["ok"] == len(all_md_fixture_names())


# ---------- 仓库门禁 ----------


def test_gate_fixtures_match_committed_baseline():
    """夹具目录回归即 CI 红：与提交的 baseline.json 必须无回归。

    生成与门禁使用相同参数（默认 recursive=False；夹具目录无子目录）。
    """
    assert COMMITTED_BASELINE.is_file(), "缺少提交的基线 tests/fixtures/regression/baseline.json"
    report = calibrate_dir(FIXTURES)
    result = compare_baseline(report, COMMITTED_BASELINE)
    assert result["ok"] is True, result["regressions"]
    assert result["regressions"] == []
