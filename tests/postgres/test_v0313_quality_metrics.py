"""v0.31.3 阶段 4：真实件质量口径与 PG 项目指标验证。

重点不是再造指标，而是验证：无真实样本时一律未测量、合成样本被拒绝、
有标注时才计算；项目级指标在真实 PostgreSQL 数据上按既有分母计算。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

from bidcraft.db import session_scope
from bidcraft.evaluation import quality_metrics, tender_dataset, tender_quality
from bidcraft.models import (
    BoqSnapshot,
    Material,
    OutlineNode,
    Project,
    QualityFinding,
    QualityReport,
    Section,
)


def _seed_project():
    with session_scope() as db:
        project_id = str(uuid.uuid4())
        db.add(Project(id=project_id, name="PG quality", owner_id=None))
        db.flush()
        for index in range(2):
            node_id = str(uuid.uuid4())
            db.add(
                OutlineNode(
                    id=node_id,
                    project_id=project_id,
                    heading=f"章节{index}",
                    status="drafted",
                )
            )
            db.add(
                Section(
                    id=str(uuid.uuid4()),
                    node_id=node_id,
                    body="甲乙",
                    status="drafted",
                    revision=2 if index == 0 else 1,
                    words=2,
                )
            )
        report_id = str(uuid.uuid4())
        db.add(
            QualityReport(
                id=report_id,
                project_id=project_id,
                report_version=1,
                overall_score=80.0,
                grade="B",
                verdict="warn",
            )
        )
        db.flush()
        db.add(
            QualityFinding(
                id=str(uuid.uuid4()),
                report_id=report_id,
                level="major",
                status="open",
                title="待处置问题",
            )
        )
        db.add(
            Material(
                id=str(uuid.uuid4()),
                project_id=project_id,
                name="电缆",
                spec="YJV",
                unit="m",
                qty=100,
            )
        )
        db.add(
            BoqSnapshot(
                id=str(uuid.uuid4()),
                project_id=project_id,
                package_id=None,
                sheet="清单",
                items=[
                    {"row": 1, "name": "电缆", "spec": "YJV", "unit": "m", "qty": 100},
                    {"row": 2, "name": "未匹配", "spec": "", "unit": "", "qty": 1},
                ],
                ignored=[],
            )
        )
    return project_id


def test_project_metrics_on_postgres_use_documented_denominators(pg_engine):
    project_id = _seed_project()
    with session_scope() as db:
        metrics = quality_metrics.collect_quality_metrics(db, project_id)

    assert set(metrics) >= {
        "manual_edit_rate",
        "issue_density",
        "boq_match_rate",
        "coverage",
        "samples",
        "mismatches",
        "notes",
    }
    assert metrics["manual_edit_rate"] == 0.5
    assert metrics["coverage"]["manual_edit_rate"]["denominator"] == 2
    # 两节各 2 个汉字 = 4 词，1 条未处置 major → 250/千词。
    assert metrics["issue_density"] == 250.0
    assert metrics["coverage"]["issue_density"]["denominator"] == 4
    assert metrics["boq_match_rate"] == 0.5
    assert metrics["coverage"]["boq_match_rate"]["denominator"] == 2
    assert metrics["mismatches"]  # 未匹配 BOQ 行与 severe finding 可抽查


def test_unmeasured_dataset_is_explicit_and_never_fabricated(tmp_path: Path):
    summary = tender_quality.collect_tender_quality(tmp_path)
    assert summary["status"] == "未测量"
    assert summary["clause_recall"] is None
    assert summary["value_parse_rate"] is None
    assert summary["verdict_agreement"] is None
    assert summary["coverage"] == {} and summary["samples"] == {}
    assert "数据集清单不存在" in " ".join(summary["notes"])


def test_synthetic_marker_cannot_be_promoted_to_real_sample(tmp_path: Path):
    (tmp_path / "sample.md").write_text("<!-- SYNTHETIC -->必须提供营业执照", encoding="utf-8")
    (tmp_path / tender_dataset.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema": tender_dataset.DATASET_SCHEMA,
                "files": [
                    {
                        "path": "sample.md",
                        "sha256": "",
                        "source": "user-provided",
                        "annotations": {
                            "required_clauses": ["必须提供营业执照"],
                            "required_values": [{"key": "预算", "value": "壹佰万元"}],
                            "expected_verdicts": {"必须提供营业执照": "satisfied"},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert tender_dataset.dataset_status(tmp_path)["ready"] is False
    assert "合成" in tender_dataset.dataset_status(tmp_path)["reason"]


def test_annotated_dataset_measures_clauses_values_and_verdict(pg_engine, monkeypatch, tmp_path: Path):
    clause = "必须提供营业执照"
    (tmp_path / "real.md").write_text(clause + " 预算壹佰万元。", encoding="utf-8")
    (tmp_path / tender_dataset.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema": tender_dataset.DATASET_SCHEMA,
                "files": [
                    {
                        "path": "real.md",
                        "sha256": "",
                        "source": "user-provided",
                        "annotations": {
                            "required_clauses": [clause],
                            "required_values": [{"key": "预算", "value": "壹佰万元"}],
                            "expected_verdicts": {clause: "satisfied"},
                            "responses": {clause: "我方提供营业执照，满足要求。"},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        tender_quality,
        "load_document",
        lambda path: SimpleNamespace(text=clause + " 预算壹佰万元。", pages=[], tables=[]),
    )
    monkeypatch.setattr(
        tender_quality,
        "extract",
        lambda *a, **k: SimpleNamespace(
            requirements=[SimpleNamespace(text=clause)], fields={"预算": "壹佰万元"}
        ),
    )
    summary = tender_quality.collect_tender_quality(tmp_path)
    assert summary["status"] == "已测量"
    assert summary["clause_recall"] == 1.0
    assert summary["value_parse_rate"] == 1.0
    assert summary["verdict_agreement"] == 1.0
    assert summary["mismatches"] == []
