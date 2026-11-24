"""API routes: /approvals (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ... import commercial  # noqa: E402
from ..shared import Any, boq, current_user, session_scope  # noqa: F401

router = APIRouter()


@router.get("/approvals/pending")
def approvals_pending() -> list[dict[str, Any]]:
    """待我审批：跨项目聚合待审报价版本与已提交商务标（平铺数组）。"""
    with session_scope() as db:
        user = current_user() or {}
        items = boq.pending_for_user(db, user)
        items.extend(commercial.pending_for_user(db, user))
        items.sort(key=lambda item: item.get("submitted_at") or "", reverse=True)
        return items[: boq.PENDING_APPROVALS_LIMIT]


@router.get("/approvals/workflows/pending")
def workflow_approvals_pending(limit: int = 100) -> list[dict[str, Any]]:
    """跨项目工作流审批工作台：聚合当前用户有写权限项目的待审工作流闸门。

    与报价版本待办分开暴露，避免改动既有 ``/approvals/pending`` 的响应格式。
    逐条走 ``authorize_project``（write 口径）：超管/项目所有者/编辑可见，
    非成员或只读成员的项目直接跳过，不泄露其它项目是否存在待审项。

    仅交付 API；前端工作台 UI 本批次不做（batch8 前端无法接收新 UI），
    待后续前端批次接入。
    """
    from sqlalchemy import select

    from ...authorization import Forbidden, authorize_project
    from ...models import Project, WorkflowApproval, WorkflowRun
    from ...orchestration.approval import approval_payload

    user = current_user() or {}
    with session_scope() as db:
        rows = db.execute(
            select(WorkflowApproval, WorkflowRun, Project)
            .join(WorkflowRun, WorkflowRun.id == WorkflowApproval.run_id)
            .join(Project, Project.id == WorkflowRun.project_id)
            .where(WorkflowApproval.status == "pending")
            .order_by(WorkflowApproval.submitted_at.desc())
            .limit(max(1, min(int(limit), 200)))
        ).all()
        items: list[dict[str, Any]] = []
        for approval, run, project in rows:
            try:
                authorize_project(db, project.id, action="write", user=user)
            except (LookupError, Forbidden):
                continue
            items.append(
                {
                    **approval_payload(approval),
                    "project_id": project.id,
                    "project_name": project.name,
                    "workflow_type": run.workflow_type,
                    "run_status": run.status,
                    "goal": run.goal or "",
                }
            )
        return items


