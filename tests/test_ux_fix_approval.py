"""审批四眼原则回归：提交人不得自审，审批意见必须落库。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import select  # noqa: E402

from bidcraft.db import session_scope  # noqa: E402
from bidcraft.models import ProjectMember, User, WorkflowApproval, WorkflowRun  # noqa: E402
from bidcraft.orchestration.approval import (  # noqa: E402
    ApprovalError,
    approve_task,
    reject_task,
)
from bidcraft.pipeline import create_project  # noqa: E402


def _setup(db):
    with session_scope() as sess:
        alice = User(username="ux-alice", display_name="提交人", password_hash="x", role="manager")
        bob = User(username="ux-bob", display_name="审批人", password_hash="x", role="manager")
        sess.add_all([alice, bob])
        sess.flush()
        project = create_project(sess, "ux-fix-approval")
        project.owner_id = alice.id
        sess.add(ProjectMember(project_id=project.id, user_id=bob.id, role="editor"))
        run = WorkflowRun(
            project_id=project.id,
            workflow_type="standard",
            status="waiting_approval",
            created_by=alice.username,
        )
        sess.add(run)
        sess.flush()
        approval = WorkflowApproval(
            run_id=run.id,
            approval_type="delivery",
            status="pending",
            submitted_by=alice.username,
        )
        sess.add(approval)
        sess.flush()
        return {
            "project_id": project.id,
            "run_id": run.id,
            "approval_id": approval.id,
            "alice": {"id": alice.id, "username": alice.username, "role": "manager"},
            "bob": {"id": bob.id, "username": bob.username, "role": "manager"},
        }


def test_submitter_cannot_approve_or_reject_own_request(db):
    ids = _setup(db)
    with session_scope() as sess:
        with pytest.raises(ApprovalError, match="不能审批自己"):
            approve_task(sess, ids["approval_id"], actor=ids["alice"], note="自审")
        with pytest.raises(ApprovalError, match="不能审批自己"):
            reject_task(sess, ids["approval_id"], actor=ids["alice"], note="自审")
        approval = sess.get(WorkflowApproval, ids["approval_id"])
        assert approval.status == "pending"
        assert approval.review_note == ""


def test_other_member_approves_with_note(db):
    ids = _setup(db)
    with session_scope() as sess:
        updated = approve_task(
            sess, ids["approval_id"], actor=ids["bob"], note=" 已复核交付范围 "
        )
        assert updated.status == "approved"
        assert updated.reviewed_by == ids["bob"]["username"]
        assert updated.review_note == "已复核交付范围"
        run = sess.get(WorkflowRun, ids["run_id"])
        assert run.status == "running"


def test_other_member_rejects_with_reason(db):
    ids = _setup(db)
    with session_scope() as sess:
        updated = reject_task(
            sess, ids["approval_id"], actor=ids["bob"], note="缺少业绩证明"
        )
        assert updated.status == "rejected"
        assert updated.review_note == "缺少业绩证明"
        assert sess.get(WorkflowRun, ids["run_id"]).status == "failed"


def test_super_admin_may_act_on_behalf(db):
    ids = _setup(db)
    super_admin = {"id": "sa-1", "username": ids["alice"]["username"], "role": "super_admin"}
    with session_scope() as sess:
        updated = approve_task(sess, ids["approval_id"], actor=super_admin, note="代审")
        assert updated.status == "approved"
        assert updated.review_note == "代审"


def test_legacy_request_without_submitter_keeps_old_behavior(db):
    ids = _setup(db)
    with session_scope() as sess:
        approval = sess.get(WorkflowApproval, ids["approval_id"])
        approval.submitted_by = ""
        sess.flush()
    with session_scope() as sess:
        updated = approve_task(sess, ids["approval_id"], actor=ids["alice"], note="历史数据")
        assert updated.status == "approved"
        assert sess.scalar(
            select(WorkflowApproval).where(WorkflowApproval.id == ids["approval_id"])
        ).reviewed_by == ids["alice"]["username"]
