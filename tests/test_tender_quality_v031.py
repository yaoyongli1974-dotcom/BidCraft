"""v0.31 阶段 6：真实件质量指标口径测试（不编造真实样本）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bidcraft.evaluation import collect_tender_quality
from bidcraft.evaluation import tender_quality as tq


def test_no_real_dataset_reports_unmeasured(tmp_path: Path) -> None:
    result = collect_tender_quality(tmp_path)
    assert result["status"] == tq.STATUS_UNMEASURED
    assert result["clause_recall"] is None
    assert result["value_parse_rate"] is None
    assert result["verdict_agreement"] is None
    assert result["files"] == []
    assert result["dataset"]["ready"] is False
    assert result["notes"]


def test_ready_dataset_computes_recall_and_value_rate(tmp_path: Path) -> None:
    tender = tmp_path / "redacted.md"
    tender.write_text(
        "# 招标公告\n★投标人须具备独立法人资格，预算金额 1000万元。\n",
        encoding="utf-8",
    )
    clause = "投标人须具备独立法人资格"
    manifest = {
        "schema": 1,
        "label": "tmp-real-fixture",
        "files": [
            {
                "path": "redacted.md",
                "sha256": "",
                "annotations": {
                    "required_clauses": [clause],
                    "required_values": [{"key": "预算金额", "value": "1000万元"}],
                    "expected_verdicts": {clause: "SATISFIED"},
                },
            }
        ],
    }
    (tmp_path / "dataset.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = collect_tender_quality(tmp_path)
    assert result["status"] == tq.STATUS_MEASURED
    assert result["dataset"]["ready"] is True
    assert len(result["files"]) == 1
    assert 0.0 <= float(result["clause_recall"]) <= 1.0
    assert 0.0 <= float(result["value_parse_rate"]) <= 1.0
    # 没有应答文本标注，判定一致性必须明确为未测量，而不是 0。
    assert result["verdict_agreement"] is None
    assert any("应答文本" in note for note in result["notes"])


@pytest.mark.usefixtures("db")
def test_report_summary_includes_tender_quality(monkeypatch) -> None:
    pytest.importorskip("fastapi")
    from bidcraft.db import session_scope
    from bidcraft.evaluation import report as report_module
    from bidcraft.models import Project

    sentinel = {"status": tq.STATUS_UNMEASURED, "notes": [], "files": []}
    monkeypatch.setattr(report_module, "collect_tender_quality", lambda: sentinel)
    with session_scope() as db:
        project = Project(name="tender-quality-summary", package="weakcurrent-cabling")
        db.add(project)
        db.flush()
        report = report_module.build_quality_report(db, project.id)
        assert report.summary["tender_quality"] == sentinel
