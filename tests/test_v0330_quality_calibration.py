"""v0.33.0 RC-07：专家评分校准（纯 Spearman/Pearson/Brier + 分桶）与未测量语义。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bidcraft.evaluation import quality_calibration as qc

FIXTURES = Path(__file__).parent / "fixtures"

PAIRS = [(55.0, 60.0), (65.0, 70.0), (70.0, 80.0), (80.0, 90.0), (85.0, 75.0), (95.0, 100.0)]


def _write_manifest(root: Path, payload: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "dataset.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _real_manifest() -> dict:
    """临时自检评分（仅 tmp_path 内使用，不提交为真实专家评分）。"""
    return {
        "schema": qc.DATASET_SCHEMA,
        "label": "framework-self-check",
        "synthetic": False,
        "pairs": [[p, r] for p, r in PAIRS],
    }


def test_spearman_pearson_brier_exact_hand_computed_values() -> None:
    # Spearman 基于名次相关 0.828571...；Pearson 基于原分数相关 0.869301...
    assert qc.spearman_correlation(PAIRS) == 0.8286
    assert qc.pearson_correlation(PAIRS) == 0.8693
    # Brier = mean(((p-r)/100)^2) = (25+25+100+100+100+25)/(6*10000)。
    assert qc.brier_score(PAIRS) == 0.0063


def test_tied_scores_use_average_ranks() -> None:
    pairs = [(80.0, 90.0), (80.0, 70.0), (70.0, 60.0)]
    # 预测分并列 80 → 平均名次 2.5；Spearman 与 Pearson 因此不同。
    assert qc.spearman_correlation(pairs) == 0.866
    assert qc.pearson_correlation(pairs) == 0.7559


def test_binned_calibration_is_exact_and_empty_bins_are_none() -> None:
    bins = qc.binned_calibration(PAIRS)
    assert len(bins) == len(qc.CALIBRATION_BINS)
    assert bins[0] == {
        "range": "[0, 60)",
        "n": 1,
        "mean_predicted": 55.0,
        "mean_expert": 60.0,
        "gap": 5.0,
    }
    assert bins[1] == {
        "range": "[60, 75)",
        "n": 2,
        "mean_predicted": 67.5,
        "mean_expert": 75.0,
        "gap": 7.5,
    }
    assert bins[2] == {
        "range": "[75, 90)",
        "n": 2,
        "mean_predicted": 82.5,
        "mean_expert": 82.5,
        "gap": 0.0,
    }
    assert bins[3]["n"] == 1
    assert bins[3]["mean_expert"] == 100.0
    empty = qc.binned_calibration([(10.0, 20.0)])
    assert [item["n"] for item in empty] == [1, 0, 0, 0]
    assert empty[1]["mean_predicted"] is None
    assert empty[1]["gap"] is None


def test_less_than_two_pairs_or_zero_variance_is_unmeasured() -> None:
    for pairs in ([], [(80.0, 90.0)], [(80.0, 90.0), (80.0, 70.0)], [(60.0, 80.0), (90.0, 80.0)]):
        result = qc.calibrate_ratings(pairs)
        assert result["measured"] is False
        assert result["n"] == len(pairs)
        assert result["spearman"] is None
        assert result["pearson"] is None
        assert result["brier_score"] is None
    assert qc.spearman_correlation([(80.0, 90.0)]) is None
    assert qc.pearson_correlation([(80.0, 90.0)]) is None
    assert qc.brier_score([(80.0, 90.0)]) is None


def test_invalid_rating_pairs_are_rejected() -> None:
    with pytest.raises(ValueError, match="预测分与专家评分"):
        qc.calibrate_ratings([(80.0,)])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="0..100"):
        qc.calibrate_ratings([(120.0, 90.0)])
    with pytest.raises(ValueError, match="0..100"):
        qc.calibrate_ratings([(80.0, -1.0)])


def test_ready_dataset_returns_exact_calibration(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _real_manifest())
    result = qc.collect_quality_calibration(tmp_path)
    assert result["status"] == qc.STATUS_MEASURED
    assert result["measured"] is True
    assert result["dataset"]["ready"] is True
    assert result["n"] == len(PAIRS)
    assert set(result["metrics"]) == set(qc.METRIC_KEYS)
    assert result["metrics"] == {
        "spearman": 0.8286,
        "pearson": 0.8693,
        "brier_score": 0.0063,
    }
    assert result["bins"][1]["gap"] == 7.5


def test_no_real_expert_ratings_is_unmeasured_with_all_none(tmp_path: Path) -> None:
    status = qc.expert_status(tmp_path)
    result = qc.collect_quality_calibration(tmp_path)
    assert status["ready"] is False
    assert "dataset.json" in status["reason"]
    assert result["status"] == qc.STATUS_UNMEASURED
    assert result["measured"] is False
    assert result["n"] == 0
    assert result["bins"] == []
    for value in result["metrics"].values():
        assert value is None  # 未测量绝不能写成 0.0
    assert result["notes"]


def test_empty_ratings_manifest_is_not_ready(tmp_path: Path) -> None:
    payload = _real_manifest()
    payload["pairs"] = []
    _write_manifest(tmp_path, payload)
    assert qc.expert_status(tmp_path)["ready"] is False
    assert "用户提供" in qc.expert_status(tmp_path)["reason"]
    assert qc.collect_quality_calibration(tmp_path)["measured"] is False


def test_synthetic_expert_fixture_is_rejected_as_real(tmp_path: Path) -> None:
    fixture = FIXTURES / "expert_ratings.json"
    assert fixture.is_file()
    (tmp_path / "dataset.json").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    assert qc.expert_status(tmp_path)["ready"] is False
    assert "synthetic" in qc.expert_status(tmp_path)["reason"]
    with pytest.raises(ValueError, match="synthetic"):
        qc.load_ratings(tmp_path)
    # 合成夹具只用于算法自检，不冒充真实专家评分。
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    checked = qc.calibrate_ratings([(float(p), float(r)) for p, r in payload["pairs"]])
    assert checked["measured"] is True
    assert checked["spearman"] == 0.8286


def test_repository_ships_no_real_expert_ratings() -> None:
    root = FIXTURES / "expert_ratings_real"
    assert not (root / "dataset.json").exists(), "真实专家评分清单不得提交到仓库"
    assert (root / "README.md").is_file()
    assert (root / "dataset.example.json").is_file()
    assert qc.expert_status(root)["ready"] is False


@pytest.mark.usefixtures("db")
def test_report_summary_injects_calibration(monkeypatch) -> None:
    pytest.importorskip("fastapi")
    from bidcraft.db import session_scope
    from bidcraft.evaluation import report as report_module
    from bidcraft.models import Project

    sentinel = {"status": qc.STATUS_MEASURED, "measured": True, "metrics": {"pearson": 1.0}}
    monkeypatch.setattr(report_module, "collect_quality_calibration", lambda: sentinel)
    with session_scope() as db:
        project = Project(name="calibration-summary", package="weakcurrent-cabling")
        db.add(project)
        db.flush()
        report = report_module.build_quality_report(db, project.id, note="校准摘要")
        assert report.summary["calibration"] == sentinel
        assert report.summary["note"] == "校准摘要"
        assert report.summary["retrieval_benchmark"]["status"] == "未测量"
        payload = report_module.report_payload(report)
        assert payload["summary"]["calibration"] == sentinel
        assert payload["summary"]["coverage"] == report.summary["coverage"]
