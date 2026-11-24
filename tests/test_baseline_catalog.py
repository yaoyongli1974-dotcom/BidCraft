"""真实件基线的溯源元数据、文件指纹与索引。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bidcraft.calibrate import (
    file_manifest,
    index_add,
    load_baseline,
    load_index,
    resolve_baseline,
    save_baseline,
    verify_baseline,
)
from bidcraft.cli import app

FIXTURES = Path(__file__).parent / "fixtures" / "regression"


def make_report(root: Path, files: list[str]) -> dict:
    return {
        "root": str(root),
        "recursive": False,
        "per_file": [{"file": name} for name in files],
        "aggregate": {
            "files": len(files),
            "ok": len(files),
            "failed": 0,
            "field_hits": {},
            "by_type": {},
            "required_hit_rate": 1.0,
            "avg_materials": 0.0,
            "ocr_files": 0,
        },
    }


# ---------- 文件指纹清单 ----------


def test_file_manifest_has_fingerprints_but_no_content(tmp_path):
    (tmp_path / "real.txt").write_text("机密招标文件正文", encoding="utf-8")
    manifest = file_manifest(make_report(tmp_path, ["real.txt"]))
    assert manifest == [
        {
            "file": "real.txt",
            "sha256": manifest[0]["sha256"],
            "size": len("机密招标文件正文".encode()),
            "missing": False,
        }
    ]
    assert len(manifest[0]["sha256"]) == 64
    assert "机密招标文件正文" not in json.dumps(manifest, ensure_ascii=False)


def test_file_manifest_marks_missing_file(tmp_path):
    manifest = file_manifest(make_report(tmp_path, ["gone.pdf"]))
    assert manifest[0]["missing"] is True
    assert manifest[0]["sha256"] is None


# ---------- 溯源元数据（向后兼容） ----------


def test_save_baseline_provenance_is_optional_and_backward_compatible(tmp_path):
    report = make_report(FIXTURES, ["weakcurrent-tender.md"])

    plain = save_baseline(report, tmp_path / "plain.json")
    plain_data = load_baseline(plain)
    assert plain_data["schema"] == 1
    assert "provenance" not in plain_data

    rich = save_baseline(
        report,
        tmp_path / "rich.json",
        label="real-2026-11",
        note="第 3 批公开招标文件",
        manifest=True,
    )
    data = load_baseline(rich)
    assert data["schema"] == 1
    assert data["aggregate"] == report["aggregate"]
    assert data["provenance"]["label"] == "real-2026-11"
    assert data["provenance"]["note"] == "第 3 批公开招标文件"
    assert data["provenance"]["file_manifest"][0]["file"] == "weakcurrent-tender.md"
    assert "root" not in data and "per_file" not in data


# ---------- 指纹核对 ----------


def test_verify_baseline_detects_change_and_missing(tmp_path):
    source = tmp_path / "real.txt"
    source.write_text("原始正文", encoding="utf-8")
    baseline = save_baseline(
        make_report(tmp_path, ["real.txt"]),
        tmp_path / "baseline.json",
        manifest=True,
    )
    assert verify_baseline(baseline, root=tmp_path)["ok"] is True

    source.write_text("被替换的正文", encoding="utf-8")
    changed = verify_baseline(baseline, root=tmp_path)
    assert changed["ok"] is False
    assert changed["files"][0]["ok"] is False and not changed["files"][0]["missing"]

    source.unlink()
    missing = verify_baseline(baseline, root=tmp_path)
    assert missing["ok"] is False
    assert missing["files"][0]["missing"] is True


def test_verify_old_baseline_without_manifest(tmp_path):
    baseline = save_baseline(make_report(tmp_path, []), tmp_path / "old.json")
    result = verify_baseline(baseline, root=tmp_path)
    assert result["has_manifest"] is False
    assert result["ok"] is True


# ---------- 索引 ----------


def test_index_add_resolve_and_dedup(tmp_path):
    baseline = save_baseline(make_report(FIXTURES, []), tmp_path / "baseline.json")
    index = tmp_path / "index.json"
    entry = index_add(index, baseline, label="real-2026-11")
    assert entry["label"] == "real-2026-11"
    assert len(entry["id"]) == 12
    assert load_index(index)["entries"][0]["path"] == "baseline.json"

    assert resolve_baseline(index, "real-2026-11") == baseline
    assert resolve_baseline(index, entry["id"]) == baseline
    assert resolve_baseline(index, entry["id"][:6]) == baseline
    assert resolve_baseline(index, "baseline.json") == baseline

    # 同一份基线再次登记只更新，不重复
    index_add(index, baseline, label="renamed")
    assert len(load_index(index)["entries"]) == 1
    assert resolve_baseline(index, "renamed") == baseline


def test_resolve_unknown_label_raises(tmp_path):
    index = tmp_path / "index.json"
    try:
        resolve_baseline(index, "missing")
    except KeyError:
        return
    raise AssertionError("未知标签应抛 KeyError")


# ---------- CLI ----------


def test_cli_baselines_list_add_verify(tmp_path):
    source = tmp_path / "real.txt"
    source.write_text("原始正文", encoding="utf-8")
    baseline = save_baseline(
        make_report(tmp_path, ["real.txt"]),
        tmp_path / "baseline.json",
        label="batch-a",
        manifest=True,
    )
    index = tmp_path / "index.json"
    runner = CliRunner()

    added = runner.invoke(app, ["baselines", "--index", str(index), "--add", str(baseline)])
    assert added.exit_code == 0, added.output
    assert "基线已登记" in added.output

    listed = runner.invoke(app, ["baselines", "--index", str(index), "--list"])
    assert listed.exit_code == 0, listed.output
    assert "batch-a" in listed.output

    verified = runner.invoke(
        app, ["baselines", "--index", str(index), "--verify", "batch-a", "--root", str(tmp_path)]
    )
    assert verified.exit_code == 0, verified.output
    assert "真实件指纹全部一致" in verified.output

    source.write_text("篡改", encoding="utf-8")
    failed = runner.invoke(
        app, ["baselines", "--index", str(index), "--verify", "batch-a", "--root", str(tmp_path)]
    )
    assert failed.exit_code == 1, failed.output
    assert "真实件已变更或缺失" in failed.output


def test_cli_calibrate_saves_label_manifest_and_registers(tmp_path):
    baseline = tmp_path / "baseline.json"
    index = tmp_path / "index.json"
    result = CliRunner().invoke(
        app,
        [
            "calibrate",
            str(FIXTURES),
            "--save-baseline",
            str(baseline),
            "--label",
            "real-fixtures",
            "--note",
            "仿真件",
            "--baseline-index",
            str(index),
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert data["provenance"]["label"] == "real-fixtures"
    # 回归集数量会随阶段 6 的合成样本扩充而增长，按目录实测断言避免硬编码过期。
    assert len(data["provenance"]["file_manifest"]) == len(list(FIXTURES.glob("*.md")))
    assert "溯源标签：real-fixtures" in result.output
    assert "基线已登记" in result.output
    assert load_index(index)["entries"][0]["label"] == "real-fixtures"


def test_cli_calibrate_no_manifest_flag(tmp_path):
    baseline = tmp_path / "baseline.json"
    result = CliRunner().invoke(
        app,
        [
            "calibrate",
            str(FIXTURES),
            "--save-baseline",
            str(baseline),
            "--no-manifest",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "provenance" not in json.loads(baseline.read_text(encoding="utf-8"))
