"""v0.31.2 阶段 6：质量指标的 sample / coverage / mismatch 明细（只增不改口径）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bidcraft.db import session_scope
from bidcraft.evaluation import collect_quality_metrics, collect_tender_quality
from bidcraft.evaluation.report import build_quality_report
from bidcraft.models import (
    BoqSnapshot,
    Material,
    OutlineNode,
    Project,
    QualityFinding,
    QualityReport,
    Section,
)


def _project(db, name: str) -> Project:
    row = Project(name=name, package="weakcurrent-cabling")
    db.add(row)
    db.flush()
    return row


def _section(db, project_id: str, body: str, revision: int) -> None:
    node = OutlineNode(project_id=project_id, heading=f"heading-{revision}", position=revision)
    db.add(node)
    db.flush()
    db.add(Section(node_id=node.id, body=body, revision=revision, words=len(body.split())))
    db.flush()


def _finding(db, report_id: str, level: str, status: str) -> None:
    db.add(
        QualityFinding(
            report_id=report_id, level=level, status=status, title=f"{level}-{status}"
        )
    )
    db.flush()


def _item(row: int, name: str, spec: str) -> dict[str, Any]:
    return {
        "row": row,
        "seq": str(row),
        "name": name,
        "spec": spec,
        "unit": "个",
        "qty": 1,
        "note": "",
    }


def _seed_project(db) -> str:
    project = _project(db, "v0312-quality-details")
    for body, revision in (
        ("正文A", 1),
        ("正文B", 2),
        ("正文C", 4),
        (" ".join(["word"] * 200), 1),
        ("   ", 9),
    ):
        _section(db, project.id, body, revision)
    report = QualityReport(project_id=project.id, report_version=1, summary={})
    db.add(report)
    db.flush()
    for level, status in (
        ("blocking", "open"),
        ("major", "open"),
        ("major", "未解决"),
        ("blocking", "new"),
        ("major", ""),
    ):
        _finding(db, report.id, level, status)
    _finding(db, report.id, "major", "handled")
    for name, spec in (("网络面板", "双口 Cat6A"), ("配线架", "24 口"), ("机柜", "42U")):
        db.add(Material(project_id=project.id, name=name, spec=spec, unit="个", qty=10))
    db.add(
        BoqSnapshot(
            project_id=project.id,
            filename="清单.xlsx",
            sheet="工程量清单",
            items=[
                _item(1, "网络面板", "双口 Cat6A"),
                _item(2, "配线架", "24 口"),
                _item(3, "机柜", "42U"),
                _item(4, "未知设备", ""),
            ],
        )
    )
    db.flush()
    return project.id


def test_project_metrics_expose_coverage_samples_and_mismatches(db) -> None:
    with session_scope() as sess:
        project_id = _seed_project(sess)
        metrics = collect_quality_metrics(sess, project_id)

    # 三个比率口径不变（2/4、5/209×1000、3/4）。
    assert metrics["manual_edit_rate"] == 0.5
    assert metrics["issue_density"] == round(5 / 209 * 1000, 4)
    assert metrics["boq_match_rate"] == 0.75

    coverage = metrics["coverage"]
    assert coverage["manual_edit_rate"]["numerator"] == 2
    assert coverage["manual_edit_rate"]["denominator"] == 4
    assert coverage["issue_density"]["numerator"] == 5
    assert coverage["issue_density"]["denominator"] == 209
    assert coverage["boq_match_rate"]["numerator"] == 3
    assert coverage["boq_match_rate"]["denominator"] == 4

    samples = metrics["samples"]
    assert len(samples["manual_edit_rate"]) == 4
    assert sorted(s["edited"] for s in samples["manual_edit_rate"]) == [
        False,
        False,
        True,
        True,
    ]
    assert len(samples["issue_density"]) == 5
    assert all(s["status"] in {"open", "new", "未解决", ""} for s in samples["issue_density"])
    assert len(samples["boq_match_rate"]) == 4
    assert any(s["status"] == "missing_in_ledger" for s in samples["boq_match_rate"])

    mismatches = metrics["mismatches"]
    assert sum(m["metric"] == "issue_density" for m in mismatches) == 5
    assert sum(m["metric"] == "boq_match_rate" for m in mismatches) == 1
    assert next(m for m in mismatches if m["metric"] == "boq_match_rate")["status"] == "missing_in_ledger"


def test_tender_quality_exposes_mismatch_and_coverage_details(tmp_path: Path) -> None:
    hit = "投标人须具备独立法人资格"
    missed = "投标人须提供近三年类似业绩证明"
    tender = tmp_path / "redacted.md"
    tender.write_text(
        f"# 招标公告\n★{hit}，预算金额 1000万元。\n",
        encoding="utf-8",
    )
    manifest = {
        "schema": 1,
        "label": "v0312-details",
        "files": [
            {
                "path": "redacted.md",
                "sha256": "",
                "annotations": {
                    "required_clauses": [hit, missed],
                    "required_values": [
                        {"key": "预算金额", "value": "1000万元"},
                        {"key": "保证金", "value": "50万元"},
                    ],
                    "expected_verdicts": {hit: "satisfied"},
                    "responses": {hit: "我方正在办理独立法人资格，暂未取得证书。"},
                },
            }
        ],
    }
    (tmp_path / "dataset.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = collect_tender_quality(tmp_path)
    assert result["status"] == "已测量"
    file_result = result["files"][0]
    assert file_result["coverage"]["clauses"]["total"] == 2
    assert file_result["coverage"]["values"]["total"] == 2
    assert file_result["coverage"]["verdict"]["compared"] == 1
    assert len(file_result["samples"]["verdict_checks"]) == 1
    assert file_result["mismatches"]

    verdict_mismatches = [
        m for m in file_result["mismatches"] if m["kind"] == "verdict"
    ]
    assert verdict_mismatches
    assert verdict_mismatches[0]["expected"] == "satisfied"
    assert verdict_mismatches[0]["actual"] != "satisfied"
    assert any(m["kind"] == "clause_not_extracted" for m in file_result["mismatches"])
    assert any(m["kind"] == "value_not_parsed" for m in file_result["mismatches"])
    assert all(m["file"] == file_result["path"] for m in result["mismatches"])
    assert result["coverage"]["clauses"]["files"] == 1
    assert result["coverage"]["verdict"]["compared"] == 1


def test_unmeasured_tender_quality_has_empty_detail_containers(tmp_path: Path) -> None:
    result = collect_tender_quality(tmp_path)
    assert result["status"] == "未测量"
    assert result["coverage"] == {}
    assert result["samples"] == {}
    assert result["mismatches"] == []


def test_report_summary_keeps_metric_details(db) -> None:
    with session_scope() as sess:
        project_id = _seed_project(sess)
        report = build_quality_report(sess, project_id, created_by="tester", note="detail")
        metrics = report.summary["metrics"]
        tender = report.summary["tender_quality"]
        assert metrics["coverage"]["manual_edit_rate"]["denominator"] == 4
        assert metrics["samples"]["manual_edit_rate"]
        assert metrics["mismatches"]
        assert tender["status"] == "未测量"
        assert tender["coverage"] == {}
        assert tender["samples"] == {}
        assert tender["mismatches"] == []
