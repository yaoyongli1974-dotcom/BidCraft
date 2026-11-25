"""v0.30 Stage 6 C: 项目级质量指标与报告摘要集成（精确分母/零分母/旧报告兼容）。"""

from __future__ import annotations

from bidcraft.db import session_scope
from bidcraft.evaluation import collect_quality_metrics
from bidcraft.evaluation.report import build_quality_report, report_payload
from bidcraft.models import (
    BoqSnapshot,
    Material,
    OutlineNode,
    Project,
    QualityFinding,
    QualityReport,
    Section,
    SectionRevision,
)

METRIC_KEYS = {
    "manual_edit_rate",
    "issue_density",
    "boq_match_rate",
    "coverage",
    "samples",
    "mismatches",
    "notes",
}


def _project(db, name: str) -> Project:
    row = Project(name=name, package="weakcurrent-cabling")
    db.add(row)
    db.flush()
    return row


def _section(
    db,
    project_id: str,
    body: str,
    revision: int,
    *,
    change_type: str | None = None,
    note: str = "",
) -> Section:
    node = OutlineNode(project_id=project_id, heading=f"章节-{revision}", position=revision)
    db.add(node)
    db.flush()
    section = Section(node_id=node.id, body=body, revision=revision, words=len(body.split()))
    db.add(section)
    db.flush()
    if change_type:
        db.add(
            SectionRevision(
                project_id=project_id,
                node_id=node.id,
                revision=revision,
                change_type=change_type,
                change_note=note,
                changed_by="tester",
            )
        )
        db.flush()
    return section


def _report(db, project_id: str) -> QualityReport:
    report = QualityReport(project_id=project_id, report_version=1, summary={})
    db.add(report)
    db.flush()
    return report


def _finding(db, report_id: str, level: str, status: str) -> QualityFinding:
    finding = QualityFinding(report_id=report_id, level=level, status=status, title="测试问题")
    db.add(finding)
    db.flush()
    return finding


def _item(row: int, name: str, spec: str = "", unit: str = "个", qty: float = 1) -> dict:
    return {
        "row": row,
        "seq": str(row),
        "name": name,
        "spec": spec,
        "unit": unit,
        "qty": qty,
        "note": "",
    }


def _seed_sections_and_findings(db, project_id: str) -> None:
    # 3 个有正文章节，其中 2 个 revision > 1；空白正文不进分母。
    _section(db, project_id, "正文A", 1)
    _section(db, project_id, "正文B", 2, change_type="edit", note="人工修订")
    _section(db, project_id, "正文C", 4, change_type="edit", note="再次修订")
    _section(db, project_id, "   ", 9)
    _section(db, project_id, "", 7)
    # 200 词正文 + 三个短标题（共 209 词）+ 5 条未处置 severe finding。
    _section(db, project_id, " ".join(["word"] * 200), 1)
    report = _report(db, project_id)
    for level, status in (
        ("blocking", "open"),
        ("major", "open"),
        ("major", "未解决"),
        ("blocking", "new"),
        ("major", ""),
    ):
        _finding(db, report.id, level, status)
    # 已处置/非严重级别不计入。
    for level, status in (
        ("major", "handled"),
        ("major", "ignored"),
        ("major", "已处理"),
        ("minor", "open"),
        ("blocking", "resolved"),
    ):
        _finding(db, report.id, level, status)


def _seed_boq(db, project_id: str) -> None:
    for name, spec, unit, qty in (
        ("网络面板", "双口 Cat6A", "个", 480),
        ("配线架", "24 口", "个", 20),
        ("机柜", "42U", "台", 4),
    ):
        db.add(
            Material(project_id=project_id, name=name, spec=spec, unit=unit, qty=qty)
        )
    db.add(
        BoqSnapshot(
            project_id=project_id,
            filename="清单.xlsx",
            sheet="工程量清单",
            items=[
                _item(1, "网络面板", "双口 Cat6A", "个", 480),  # matched
                _item(2, "配线架", "24 口", "个", 20),  # matched
                _item(3, "机柜", "42U 标准", "台", 4),  # spec_diff（仍算已匹配）
                _item(4, "未知设备", "", "个", 1),  # missing_in_ledger
            ],
        )
    )
    db.flush()


def test_manual_edit_rate_exact_ratio_and_zero_denominator(db):
    with session_scope() as sess:
        project = _project(sess, "metrics-edit")
        _seed_sections_and_findings(sess, project.id)
        metrics = collect_quality_metrics(sess, project.id)
        assert set(metrics) == METRIC_KEYS
        assert metrics["manual_edit_rate"] == round(2 / 4, 4)  # 0.5
        assert any("4 个有正文的章节" in note for note in metrics["notes"])

        empty = _project(sess, "metrics-edit-empty")
        zero = collect_quality_metrics(sess, empty.id)
        assert zero["manual_edit_rate"] == 0.0
        assert any("0 个有正文的章节" in note for note in zero["notes"])


def test_manual_edit_rate_counts_only_edit_revisions_not_restore(db):
    with session_scope() as sess:
        project = _project(sess, "metrics-restore-excluded")
        _section(sess, project.id, "人工修订正文", 3, change_type="edit", note="复核后修订")
        # revision 被回滚抬高，但快照只有 restore/generate，不能算人工修改。
        _section(sess, project.id, "恢复正文", 6, change_type="restore", note="回滚到旧版本")
        _section(sess, project.id, "再生成正文", 4, change_type="generate", note="重新生成")

        metrics = collect_quality_metrics(sess, project.id)
        assert metrics["manual_edit_rate"] == round(1 / 3, 4)
        assert metrics["coverage"]["manual_edit_rate"] == {
            "numerator": 1,
            "denominator": 3,
            "sample_count": 3,
            "ratio": round(1 / 3, 4),
        }
        assert sorted(
            sample["edited"] for sample in metrics["samples"]["manual_edit_rate"]
        ) == [False, False, True]
        assert any("restore/generate 不计" in note for note in metrics["notes"])


def test_issue_density_counts_severe_unresolved_per_1000_words(db):
    with session_scope() as sess:
        project = _project(sess, "metrics-density")
        _seed_sections_and_findings(sess, project.id)
        metrics = collect_quality_metrics(sess, project.id)
        # 200 个 word + 三个短标题（正文A/B/C 各 3 词）= 209 词。
        assert metrics["issue_density"] == round(5 / 209 * 1000, 4)
        assert any("209 词" in note for note in metrics["notes"])

        no_words = _project(sess, "metrics-density-empty")
        report = _report(sess, no_words.id)
        _finding(sess, report.id, "blocking", "open")
        _section(sess, no_words.id, "", 1)
        assert collect_quality_metrics(sess, no_words.id)["issue_density"] == 0.0


def test_boq_match_rate_uses_canonical_align_marker_and_zero_denominator(db):
    with session_scope() as sess:
        project = _project(sess, "metrics-boq")
        _seed_boq(sess, project.id)
        metrics = collect_quality_metrics(sess, project.id)
        assert metrics["boq_match_rate"] == 0.75  # 2 matched + 1 spec_diff / 4
        assert any("boq.align()" in note for note in metrics["notes"])

        empty = _project(sess, "metrics-boq-empty")
        zero = collect_quality_metrics(sess, empty.id)
        assert zero["boq_match_rate"] == 0.0
        assert any("没有 BOQ 清单行" in note for note in zero["notes"])


def test_build_quality_report_stores_metrics_and_preserves_summary(db):
    with session_scope() as sess:
        project = _project(sess, "metrics-report")
        _seed_sections_and_findings(sess, project.id)
        _seed_boq(sess, project.id)
        report = build_quality_report(sess, project.id, created_by="tester", note="例行")
        metrics = report.summary["metrics"]
        assert set(metrics) == METRIC_KEYS
        assert metrics["manual_edit_rate"] == 0.5
        assert metrics["issue_density"] == round(5 / 209 * 1000, 4)
        assert metrics["boq_match_rate"] == 0.75
        # 既有摘要键一个不少。
        assert {"coverage", "recommendations", "note"} <= set(report.summary)
        assert report.summary["note"] == "例行"
        payload = report_payload(report)
        assert payload["summary"]["metrics"] == metrics

        # 重新加载确认 JSON 字段已持久化（而非仅内存对象）。
        sess.expire_all()
        stored = sess.get(QualityReport, report.id)
        assert stored.summary["metrics"]["boq_match_rate"] == 0.75
        assert stored.summary["coverage"] == report.summary["coverage"]


def test_old_report_without_metrics_still_renders(db):
    with session_scope() as sess:
        project = _project(sess, "metrics-legacy")
        legacy = QualityReport(
            project_id=project.id,
            report_version=1,
            summary={"coverage": {"chapter": {"ratio": 1.0}}, "note": "旧报告"},
            dimensions=[],
            risk_summary={"issues_open": 0},
        )
        sess.add(legacy)
        sess.flush()
        payload = report_payload(legacy)
        assert "metrics" not in payload["summary"]
        assert payload["summary"]["note"] == "旧报告"
        assert payload["summary"]["coverage"]["chapter"]["ratio"] == 1.0
        assert payload["risk_summary"] == {"issues_open": 0}


def test_collect_quality_metrics_never_raises_on_missing_data(db):
    assert collect_quality_metrics(None, "missing-db")["manual_edit_rate"] == 0.0

    class BrokenDB:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("boom")

        def scalars(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    metrics = collect_quality_metrics(BrokenDB(), "broken-db")  # type: ignore[arg-type]
    assert set(metrics) == METRIC_KEYS
    assert metrics["manual_edit_rate"] == 0.0
    assert metrics["issue_density"] == 0.0
    assert metrics["boq_match_rate"] == 0.0
    assert len(metrics["notes"]) == 2
