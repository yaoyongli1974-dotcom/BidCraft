"""v0.34.0 P1：审批链 JSON 并发读写的真实线程/真实数据库验证。

两个审批人在同一级会签阶段同时提交批准。旧实现各自读取
``approvals`` 列表后原地 append 并整体写回，后提交者会覆盖先提交者；本文件
要求两次审批都持久化、状态正确定稿，且业务规则（同一个人不能重复签、未授权
不能签）保持不变。所有断言直接查询真实 SQLite 数据库。
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import func, select

from bidcraft import boq, commercial
from bidcraft.db import session_scope
from bidcraft.models import AuditLog, BoqQuoteVersion, CommercialDoc, Project
from bidcraft.pipeline import create_project

CHAIN = [
    {
        "approvers": [
            {"id": "v0340-approver-a", "name": "审批甲"},
            {"id": "v0340-approver-b", "name": "审批乙"},
        ],
        "mode": "all",
    }
]


def _seed_project(db, name: str) -> str:
    project = create_project(db, name)
    meta = dict(project.meta or {})
    meta["approval_chain"] = CHAIN
    project.meta = meta
    db.flush()
    return project.id


def _pair(left, right):
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def run(name, fn):
        try:
            barrier.wait(timeout=10)
            results[name] = fn()
        except BaseException as exc:  # noqa: BLE001 - surface real thread failures
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("a", left), name="v0340-approval-a"),
        threading.Thread(target=run, args=("b", right), name="v0340-approval-b"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "审批并发线程未在 30 秒内结束"
    assert not errors, f"unexpected thread errors: {errors!r}"
    return results["a"], results["b"]


@pytest.mark.parametrize("round_no", range(6))
def test_boq_concurrent_countersign_both_persist(db, round_no):
    with session_scope() as session:
        project_id = _seed_project(session, f"v0340 boq 会签 {round_no}")
        version = BoqQuoteVersion(
            project_id=project_id,
            name="并发报价",
            status="submitted",
            summary={"total_amount": 1000},
            prices={},
            basis={},
            approvals=[],
        )
        session.add(version)
        session.flush()
        version_id = version.id

    def approve(actor_id: str, actor: str):
        with session_scope() as session:
            project = session.get(Project, project_id)
            return boq.review_version(
                session,
                project,
                version_id,
                decision="approve",
                actor=actor,
                actor_id=actor_id,
                role="editor",
            )

    first, second = _pair(
        lambda: approve("v0340-approver-a", "审批甲"),
        lambda: approve("v0340-approver-b", "审批乙"),
    )
    assert sorted([first["status"], second["status"]]) == ["approved", "submitted"]
    with session_scope() as session:
        row = session.get(BoqQuoteVersion, version_id)
        approvals = list(row.approvals or [])
        assert row.status == "approved"
        assert len(approvals) == 2
        assert {a["user_id"] for a in approvals} == {
            "v0340-approver-a",
            "v0340-approver-b",
        }
        assert all(a["decision"] == "approve" and a["stage"] == 0 for a in approvals)
        assert str(row.reviewed_by) in {"审批甲", "审批乙"}
        assert row.reviewed_at is not None


@pytest.mark.parametrize("round_no", range(6))
def test_commercial_concurrent_countersign_both_persist(db, round_no):
    with session_scope() as session:
        project_id = _seed_project(session, f"v0340 commercial 会签 {round_no}")
        doc = CommercialDoc(
            project_id=project_id,
            name="并发商务标",
            status="submitted",
            package_id=None,
            sections={},
            validation={
                "approvals": [],
                "quote": {"total_amount": 1000, "currency": "CNY"},
            },
        )
        session.add(doc)
        session.flush()
        doc_id = doc.id

    def approve(actor_id: str, actor: str):
        with session_scope() as session:
            project = session.get(Project, project_id)
            return commercial.review_commercial(
                session,
                project,
                doc_id,
                decision="approve",
                actor={"username": actor_id, "display_name": actor},
                actor_id=actor_id,
                role="editor",
            )

    first, second = _pair(
        lambda: approve("v0340-approver-a", "审批甲"),
        lambda: approve("v0340-approver-b", "审批乙"),
    )
    assert sorted([first["status"], second["status"]]) == ["approved", "submitted"]
    with session_scope() as session:
        row = session.get(CommercialDoc, doc_id)
        approvals = list((row.validation or {}).get("approvals") or [])
        assert row.status == "approved"
        assert len(approvals) == 2
        assert {a["user_id"] for a in approvals} == {
            "v0340-approver-a",
            "v0340-approver-b",
        }
        assert all(a["decision"] == "approve" and a["stage"] == 0 for a in approvals)
        assert (row.validation or {}).get("review", {}).get("decision") == "approve"
        actions = list(
            session.scalars(
                select(AuditLog.action).where(AuditLog.project_id == project_id)
            )
        )
        assert actions.count("commercial.approve") == 2


def test_boq_duplicate_signer_still_forbidden_after_concurrency_fix(db):
    """The claim loop must re-evaluate authorization, not just append blindly."""
    with session_scope() as session:
        project_id = _seed_project(session, "v0340 boq 重复签名")
        version = BoqQuoteVersion(
            project_id=project_id,
            name="重复签名报价",
            status="submitted",
            summary={"total_amount": 1000},
            prices={},
            basis={},
            approvals=[],
        )
        session.add(version)
        session.flush()
        version_id = version.id

    with session_scope() as session:
        project = session.get(Project, project_id)
        boq.review_version(
            session,
            project,
            version_id,
            decision="approve",
            actor="审批甲",
            actor_id="v0340-approver-a",
            role="editor",
        )
    with session_scope() as session:
        project = session.get(Project, project_id)
        with pytest.raises(boq.BoqForbiddenError):
            boq.review_version(
                session,
                project,
                version_id,
                decision="approve",
                actor="审批甲",
                actor_id="v0340-approver-a",
                role="editor",
            )
    with session_scope() as session:
        row = session.get(BoqQuoteVersion, version_id)
        assert len(row.approvals or []) == 1
        assert row.status == "submitted"


def test_approval_claim_helper_guards_stale_json(db):
    """A conditional claim must fail when the JSON changed after the read."""
    from bidcraft.boq import claim_json_revision, json_column_stamp

    with session_scope() as session:
        project_id = _seed_project(session, "v0340 claim 守卫")
        version = BoqQuoteVersion(
            project_id=project_id,
            name="claim 守卫报价",
            status="submitted",
            summary={},
            prices={},
            basis={},
            approvals=[],
        )
        session.add(version)
        session.flush()
        version_id = version.id
        stale_stamp = json_column_stamp(session, BoqQuoteVersion.approvals, version_id)

    with session_scope() as session:
        assert claim_json_revision(
            session,
            BoqQuoteVersion,
            version_id,
            BoqQuoteVersion.approvals,
            expected_stamp=stale_stamp,
            from_status="submitted",
            approvals=[{"stage": 0, "user_id": "v0340-approver-a"}],
        )
    with session_scope() as session:
        assert claim_json_revision(
            session,
            BoqQuoteVersion,
            version_id,
            BoqQuoteVersion.approvals,
            expected_stamp=stale_stamp,
            from_status="submitted",
            approvals=[{"stage": 0, "user_id": "v0340-approver-b"}],
        ) is False
        row = session.get(BoqQuoteVersion, version_id)
        assert [str(a.get("user_id")) for a in (row.approvals or [])] == [
            "v0340-approver-a"
        ]
        assert int(
            session.scalar(
                select(func.count()).select_from(BoqQuoteVersion).where(
                    BoqQuoteVersion.id == version_id
                )
            )
            or 0
        ) == 1
