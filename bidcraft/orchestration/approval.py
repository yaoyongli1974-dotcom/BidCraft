"""人工审批闸门。

审批复用项目成员权限（``authorize_project`` 的 write 口径）并写 ``audit_logs``；
无权限用户不能审批，未审批时受控后续任务不会执行。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authorization import Forbidden, authorize_project
from ..clock import iso_beijing, utc_now
from ..context import current_user
from ..models import WorkflowApproval, WorkflowRun, WorkflowTask
from .registry import APPROVAL_TYPES


class ApprovalError(ValueError):
    """审批状态不合法（重复审批 / 无待审项 / 已驳回后恢复等）。"""


def _run(db: Session, run_id: str) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise LookupError(f"工作流不存在: {run_id}")
    return run


def _resolve_actor(actor: dict[str, Any] | None) -> dict[str, Any]:
    resolved = dict(actor or current_user() or {})
    if not resolved.get("id"):
        raise Forbidden("请先登录后再审批")
    return resolved


def _check_permission(db: Session, run: WorkflowRun, actor: dict[str, Any]) -> str:
    """Approval requires project write permission; returns effective role."""

    _project, role = authorize_project(db, run.project_id, action="write", user=actor)
    return role


def _check_separation(
    approval: WorkflowApproval, actor: dict[str, Any], role: str
) -> None:
    """审批人不得是提交人（四眼原则）。

    ``submitted_by`` 为空时视为历史数据/系统触发，无从判定提交人，跳过；
    超级管理员沿用报价审批链的「可代审」口径（effective role ``admin``）。
    """

    if role == "admin":
        return
    requester = str(approval.submitted_by or "").strip()
    reviewer = str(actor.get("username") or "").strip()
    if requester and requester == reviewer:
        raise ApprovalError("不能审批自己提交的审批项，请由其他有权限的成员处理")


def _pending_approval(
    db: Session, run_id: str, task_id: str | None
) -> WorkflowApproval | None:
    statement = select(WorkflowApproval).where(
        WorkflowApproval.run_id == run_id,
        WorkflowApproval.status == "pending",
    )
    if task_id is None:
        statement = statement.where(WorkflowApproval.task_id.is_(None))
    else:
        statement = statement.where(WorkflowApproval.task_id == task_id)
    return db.scalar(statement.order_by(WorkflowApproval.submitted_at.desc()))


def request_approval(
    db: Session,
    run: WorkflowRun,
    task: WorkflowTask | None = None,
    *,
    approval_type: str = "delivery",
    submitted_by: str = "",
) -> WorkflowApproval:
    """Create (or return the existing) pending approval and suspend the run.

    Idempotent per ``(run_id, task_id)`` so a worker retry cannot create two
    pending gates for the same task.
    """

    kind = str(approval_type or "delivery")
    if kind not in APPROVAL_TYPES:
        raise ApprovalError(f"未知审批类型: {kind}")
    existing = _pending_approval(db, run.id, task.id if task is not None else None)
    if existing is not None:
        run.status = "waiting_approval"
        db.flush()
        return existing
    approval = WorkflowApproval(
        run_id=run.id,
        task_id=task.id if task is not None else None,
        approval_type=kind,
        status="pending",
        submitted_by=submitted_by or run.created_by or "",
    )
    db.add(approval)
    if task is not None:
        task.status = "running"
        task.started_at = task.started_at or utc_now()
    run.status = "waiting_approval"
    db.flush()
    record_audit(
        db,
        run.project_id,
        "workflow.approval.request",
        {
            "run_id": run.id,
            "task_id": task.id if task is not None else None,
            "approval_type": kind,
        },
    )
    return approval


def approve_task(
    db: Session,
    approval_id: str,
    *,
    actor: dict[str, Any] | None = None,
    note: str = "",
    reviewer: str = "",
) -> WorkflowApproval:
    """Approve one gate; requires project write permission."""

    approval = db.get(WorkflowApproval, approval_id)
    if approval is None:
        raise LookupError("审批记录不存在")
    run = _run(db, approval.run_id)
    resolved = _resolve_actor(actor)
    role = _check_permission(db, run, resolved)
    _check_separation(approval, resolved, role)
    if approval.status != "pending":
        raise ApprovalError("该审批已处理")
    reviewer_name = reviewer or str(resolved.get("username") or "")
    approval.status = "approved"
    approval.reviewed_by = reviewer_name
    approval.review_note = (note or "").strip()
    approval.reviewed_at = utc_now()
    if approval.task_id:
        task = db.get(WorkflowTask, approval.task_id)
        if task is not None:
            task.status = "done"
            task.completed_at = utc_now()
            task.error = ""
            task.output = {
                **(task.output or {}),
                "approval": {
                    "id": approval.id,
                    "status": "approved",
                    "reviewed_by": reviewer_name,
                    "note": approval.review_note,
                },
            }
    remaining = (
        db.scalar(
            select(WorkflowApproval.id).where(
                WorkflowApproval.run_id == run.id,
                WorkflowApproval.status == "pending",
            )
        )
        is not None
    )
    if not remaining and run.status == "waiting_approval":
        run.status = "running"
    record_audit(
        db,
        run.project_id,
        "workflow.approval.approve",
        {
            "run_id": run.id,
            "approval_id": approval.id,
            "approval_type": approval.approval_type,
            "role": role,
            "note": approval.review_note,
        },
        actor=resolved,
    )
    db.flush()
    return approval


def reject_task(
    db: Session,
    approval_id: str,
    *,
    actor: dict[str, Any] | None = None,
    note: str = "",
    reviewer: str = "",
) -> WorkflowApproval:
    """Reject one gate; the workflow fails and downstream tasks never run."""

    approval = db.get(WorkflowApproval, approval_id)
    if approval is None:
        raise LookupError("审批记录不存在")
    run = _run(db, approval.run_id)
    resolved = _resolve_actor(actor)
    role = _check_permission(db, run, resolved)
    _check_separation(approval, resolved, role)
    if approval.status != "pending":
        raise ApprovalError("该审批已处理")
    approval.status = "rejected"
    approval.reviewed_by = reviewer or str(resolved.get("username") or "")
    approval.review_note = (note or "").strip()
    approval.reviewed_at = utc_now()
    if approval.task_id:
        task = db.get(WorkflowTask, approval.task_id)
        if task is not None:
            task.status = "failed"
            task.error = f"审批驳回：{approval.review_note or '未填写原因'}"
            task.completed_at = utc_now()
    run.status = "failed"
    run.completed_at = utc_now()
    run.result = {
        **(run.result or {}),
        "error": f"审批驳回（{approval.approval_type}）",
        "rejected_approval_id": approval.id,
    }
    record_audit(
        db,
        run.project_id,
        "workflow.approval.reject",
        {
            "run_id": run.id,
            "approval_id": approval.id,
            "approval_type": approval.approval_type,
            "role": role,
            "note": approval.review_note,
        },
        actor=resolved,
    )
    db.flush()
    return approval


def resume_after_approval(db: Session, run_id: str) -> WorkflowRun:
    """Resume a suspended run once every pending gate is approved."""

    run = _run(db, run_id)
    if run.status == "cancelled":
        raise ApprovalError("已取消的工作流不能恢复")
    pending = list(
        db.scalars(
            select(WorkflowApproval).where(
                WorkflowApproval.run_id == run_id,
                WorkflowApproval.status == "pending",
            )
        )
    )
    if pending:
        raise ApprovalError("仍有待审批项，不能恢复")
    rejected = db.scalar(
        select(WorkflowApproval.id).where(
            WorkflowApproval.run_id == run_id,
            WorkflowApproval.status == "rejected",
        )
    )
    if rejected is not None:
        raise ApprovalError("审批已驳回，不能恢复")
    if run.status in {"done", "failed"}:
        return run
    run.status = "running"
    db.flush()
    return run


def approval_payload(approval: WorkflowApproval) -> dict[str, Any]:
    return {
        "id": approval.id,
        "run_id": approval.run_id,
        "task_id": approval.task_id,
        "approval_type": approval.approval_type,
        "status": approval.status,
        "submitted_by": approval.submitted_by,
        "reviewed_by": approval.reviewed_by or "",
        "review_note": approval.review_note or "",
        "submitted_at": iso_beijing(approval.submitted_at),
        "reviewed_at": iso_beijing(approval.reviewed_at),
    }


__all__ = [
    "ApprovalError",
    "approval_payload",
    "approve_task",
    "reject_task",
    "request_approval",
    "resume_after_approval",
]
