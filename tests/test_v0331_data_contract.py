"""v0.33.1 Phase 2：真实数据阻塞下的诚实状态与未测量契约。"""

from __future__ import annotations

from pathlib import Path

from bidcraft.evaluation.quality_calibration import (
    DEFAULT_ROOT as EXPERT_ROOT,
)
from bidcraft.evaluation.quality_calibration import (
    METRIC_KEYS as EXPERT_KEYS,
)
from bidcraft.evaluation.quality_calibration import (
    collect_quality_calibration,
    expert_status,
)
from bidcraft.evaluation.retrieval_metrics import (
    DEFAULT_ROOT as RETRIEVAL_ROOT,
)
from bidcraft.evaluation.retrieval_metrics import (
    METRIC_KEYS as RETRIEVAL_KEYS,
)
from bidcraft.evaluation.retrieval_metrics import (
    collect_retrieval_metrics,
    retrieval_status,
)
from bidcraft.evaluation.tender_dataset import DEFAULT_ROOT as TENDER_ROOT
from bidcraft.evaluation.tender_dataset import dataset_status

ROOT = Path(__file__).resolve().parents[1]


def test_repository_ships_no_real_dataset_manifests():
    for root in (TENDER_ROOT, RETRIEVAL_ROOT, EXPERT_ROOT):
        assert not (ROOT / root / "dataset.json").exists()
        assert (ROOT / root / "README.md").exists()
        assert (ROOT / root / "dataset.example.json").exists()


def test_missing_real_data_is_reported_as_unmeasured_not_zero():
    tender = dataset_status(ROOT / TENDER_ROOT)
    assert tender["ready"] is False
    assert tender["reason"]

    retrieval = collect_retrieval_metrics(ROOT / RETRIEVAL_ROOT)
    assert retrieval_status(ROOT / RETRIEVAL_ROOT)["ready"] is False
    assert retrieval["measured"] is False
    assert retrieval["status"] == "未测量"
    assert retrieval["metrics"] == {key: None for key in RETRIEVAL_KEYS}
    assert "0.0" not in str(retrieval["metrics"])

    expert = collect_quality_calibration(ROOT / EXPERT_ROOT)
    assert expert_status(ROOT / EXPERT_ROOT)["ready"] is False
    assert expert["measured"] is False
    assert expert["status"] == "未测量"
    assert expert["metrics"] == {key: None for key in EXPERT_KEYS}
    assert expert["bins"] == []


def test_ci_reports_all_three_real_data_statuses_without_gating():
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    tender_at = text.index("Real tender dataset status (non-gating)")
    retrieval_at = text.index("Real retrieval qrels status (non-gating)")
    expert_at = text.index("Real expert ratings status (non-gating)")
    assert tender_at < retrieval_at < expert_at
    assert "DATA BLOCKED (skip)" in text
    assert "retrieval_status" in text
    assert "expert_status" in text
