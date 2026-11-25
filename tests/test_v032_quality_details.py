"""v0.32 阶段6：质量细节口径（检索引用质量 / 事实确认盖章 / 审计失败载荷）。"""

from __future__ import annotations

import datetime as dt

from bidcraft import clock, provenance
from bidcraft.db import session_scope
from bidcraft.evaluation import (
    calculate_evidence_coverage,
    collect_coverage_findings,
    collect_tool_audit_failures,
)
from bidcraft.models import AuditLog, ChapterPlan, Fact, OutlineNode, Project, Section


def _project(db, name: str) -> Project:
    row = Project(name=name, package="weakcurrent-cabling")
    db.add(row)
    db.flush()
    return row


def _chapter(db, project_id: str, heading: str, body: str) -> OutlineNode:
    node = OutlineNode(project_id=project_id, heading=heading, position=1)
    db.add(node)
    db.flush()
    db.add(Section(node_id=node.id, body=body, review_status="confirmed"))
    db.flush()
    return node


def test_knowledge_ref_ratios_and_stable_keys_from_real_plan(db) -> None:
    with session_scope() as sess:
        project = _project(sess, "v032-ref-details")
        node = _chapter(sess, project.id, "技术章", "正文")
        plan = ChapterPlan(
            project_id=project.id,
            node_id=node.id,
            version=1,
            status="CONFIRMED",
            knowledge_refs=[
                {
                    "id": "k1",
                    "rerank_score": 0.42,
                    "trust_tier": "title_only",
                    "degraded": True,
                    "document_id": "doc-1",
                    "page": 3,
                    "heading": "施工规范",
                    "source": "公司知识库",
                },
                {
                    "id": "k2",
                    "rerank_score": 0.91,
                    "trust_tier": "full",
                    "degraded": False,
                    "chunk_id": "chunk-2",
                },
                {"id": "k3", "trust_tier": None, "degraded": None},
            ],
        )
        sess.add(plan)
        sess.flush()

        evidence = calculate_evidence_coverage(sess, project.id)
        assert evidence["plan_refs"] == 3
        assert evidence["degraded_refs"] == 1
        assert evidence["low_trust_refs"] == 1
        assert evidence["missing_trace_refs"] == 1
        assert evidence["degraded_ratio"] == round(1 / 3, 4)
        assert evidence["low_trust_ratio"] == round(1 / 3, 4)
        assert evidence["missing_trace_ratio"] == round(1 / 3, 4)
        assert evidence["ref_measured"] is True

        findings = collect_coverage_findings(sess, project.id)
        refs = {f["evidence"]["stable_key"]: f for f in findings if f["category"] == "知识引用"}
        assert set(refs) == {
            f"chapter_plan:{plan.id}:ref:k1",
            f"chapter_plan:{plan.id}:ref:k3",
        }
        degraded = refs[f"chapter_plan:{plan.id}:ref:k1"]
        assert degraded["level"] == "major"
        assert degraded["node_id"] == node.id
        assert degraded["evidence"]["document_id"] == "doc-1"
        assert degraded["evidence"]["page"] == 3
        assert degraded["evidence"]["rerank_score"] == 0.42
        assert degraded["evidence"]["trust_tier"] == "title_only"
        missing = refs[f"chapter_plan:{plan.id}:ref:k3"]
        assert missing["level"] == "info"
        assert missing["evidence"]["issues"] == ["missing_trace"]


def test_fact_confirmation_evidence_and_unconfirmed_cited_finding(db) -> None:
    with session_scope() as sess:
        project = _project(sess, "v032-fact-confirmation")
        node = _chapter(sess, project.id, "商务章", "预算金额 350万元，质保期 2年。")
        stamp = clock.utc_now()
        confirmed = Fact(
            project_id=project.id,
            key="预算金额",
            label="预算金额",
            value="350万元",
            status="confirmed",
            confirmed_by="alice",
            confirmed_at=stamp,
        )
        unconfirmed = Fact(
            project_id=project.id,
            key="质保期",
            label="质保期",
            value="2年",
            status="extracted",
        )
        sess.add_all([confirmed, unconfirmed])
        sess.flush()
        provenance.refresh_project(sess, project.id)

        evidence = calculate_evidence_coverage(sess, project.id)
        assert evidence["facts_cited"] == 2
        assert evidence["facts_confirmed"] == 1
        assert evidence["facts_unconfirmed"] == 1
        assert evidence["confirmed_ratio"] == 0.5
        assert evidence["unconfirmed_fact_keys"] == ["质保期"]
        by_key = {item["fact_key"]: item for item in evidence["fact_confirmations"]}
        assert by_key["预算金额"]["confirmed_by"] == "alice"
        assert by_key["预算金额"]["confirmed_at"] == clock.iso_beijing(stamp)
        assert by_key["质保期"]["confirmed_by"] is None
        assert by_key["质保期"]["confirmed_at"] is None

        findings = collect_coverage_findings(sess, project.id)
        cited = [f for f in findings if f["title"] == "正文引用的事实尚未人工确认"]
        assert len(cited) == 1
        assert cited[0]["evidence"]["stable_key"] == f"fact:{unconfirmed.id}"
        assert cited[0]["evidence"]["fact_key"] == "质保期"
        assert cited[0]["evidence"]["confirmed_by"] is None
        assert cited[0]["evidence"]["confirmed_at"] is None
        assert cited[0]["evidence"]["node_ids"] == [node.id]

        # 真实盖章后不再产生 finding；确认金不凭空生成。
        unconfirmed.status = "manual"
        unconfirmed.confirmed_by = "bob"
        unconfirmed.confirmed_at = dt.datetime(2026, 1, 2, 3, 4, 5)
        sess.flush()
        evidence = calculate_evidence_coverage(sess, project.id)
        assert evidence["confirmed_ratio"] == 1.0
        assert not [
            f for f in collect_coverage_findings(sess, project.id)
            if f["title"] == "正文引用的事实尚未人工确认"
        ]


def test_tool_audit_failure_payload_variants(db) -> None:
    with session_scope() as sess:
        project = _project(sess, "v032-audit-payload")
        rows = []
        for detail in (
            {"tool": "a", "isError": True},
            {"tool": "b", "is_error": True},
            {"tool": "c", "ok": False, "reason": "拒绝"},
            {"tool": "d", "isError": False, "reason": ""},
        ):
            rows.append(
                AuditLog(
                    project_id=project.id,
                    action="agent.tool.call",
                    detail=detail,
                    username="tester",
                )
            )
        sess.add_all(rows)
        sess.flush()

        failures = collect_tool_audit_failures(sess, project.id)
        assert [item["id"] for item in failures] == [row.id for row in rows[:3]]
        assert failures[0]["tool"] == "a"
        assert failures[2]["detail"]["reason"] == "拒绝"
