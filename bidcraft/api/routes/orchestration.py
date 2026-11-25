"""API routes: /orchestration（对标易标第三批 v0.26.0）。

Agent 注册表、工作流运行/任务/审批。业务实现位于
:mod:`bidcraft.orchestration`；本模块只做鉴权、参数与 HTTP 语义映射。
运行中的工作流由 agent Job 驱动；审批挂起不占 Job 租约，审批通过后
重新 schedule 一个 agent Job 继续。
"""

from __future__ import annotations

import sys
from typing import Any

from fastapi import APIRouter, HTTPException, Query

_core = sys.modules['bidcraft.api.app']  # noqa: F841 运行期查找，保持 monkeypatch 生效

from ...models import WorkflowApproval, WorkflowRun  # noqa: E402
from ...orchestration.approval import (  # noqa: E402
    ApprovalError,
    approval_payload,
    approve_task,
    reject_task,
)
from ...orchestration.orchestrator import (  # noqa: E402
    OrchestrationError,
    cancel_workflow,
    get_workflow_status,
    resume_workflow,
    run_payload,
    schedule_workflow,
    start_workflow,
    task_payload,
)
from ...orchestration.planner import PlanError  # noqa: E402
from ...orchestration.registry import get_agent, list_agents, list_tools  # noqa: E402
from ..schemas import WorkflowApprovalReview, WorkflowCreate  # noqa: E402
from ..shared import (  # noqa: E402,F401
    _project,
    _require_write,
    audit,
    auth,
    current_user,
    session_scope,
)

router = APIRouter()

# MCP 内置工具中只读的四项；其余内置工具与插件工具都可能有副作用。
_READONLY_TOOL_NAMES = frozenset(
    {"list_projects", "get_project", "get_ledger", "search_knowledge"}
)


def _actor() -> dict:
    return current_user() or {}


def _username() -> str:
    return str(_actor().get("username") or "")


def _run_for_project(db, project_id: str, run_id: str) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None or run.project_id != project_id:
        raise LookupError(f"工作流不存在: {run_id}")
    return run


def _error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _detail(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _tool_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """Normalize a registry tool spec into the public catalog shape.

    MCP specs use ``inputSchema`` while the HTTP contract exposes
    ``input_schema``; ``readonly`` / ``side_effect`` are derived from the
    built-in tool names when the spec does not declare them.
    """

    name = str(spec.get("name") or "")
    readonly = spec.get("readonly")
    if readonly is None:
        readonly = name in _READONLY_TOOL_NAMES
    readonly = bool(readonly)
    side_effect = spec.get("side_effect")
    if side_effect is None:
        side_effect = "none" if readonly else "write"
    input_schema = spec.get("input_schema") or spec.get("inputSchema")
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    return {
        "name": name,
        "description": str(spec.get("description") or ""),
        "input_schema": input_schema,
        "side_effect": str(side_effect),
        "readonly": readonly,
    }


def _tool_calls(value: Any) -> list[dict[str, Any]]:
    """Copy only real trace dicts; never synthesize verification or success."""

    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _trace_from(payload: Any) -> list[dict[str, Any]]:
    """Read ``tool_calls`` from an output/result dict without trusting its shape."""

    if not isinstance(payload, dict):
        return []
    return _tool_calls(payload.get("tool_calls"))


def _check_backpressure(db, owner_id: str) -> None:
    """Gate workflow Job creation with the shared queue cap (same transaction).

    ``jobqueue.check_backpressure`` is the single source of truth for the global
    and per-user queued caps; keep the call here (instead of in the queue module)
    so every workflow route that creates a new agent Job is covered.
    """

    from ... import jobqueue

    error = jobqueue.check_backpressure(db, owner_id)
    if error:
        raise HTTPException(status_code=429, detail=error)


# --------------------------------------------------------------------------- #
# Agent 注册表
# --------------------------------------------------------------------------- #
@router.get("/agents")
def agent_catalog() -> dict:
    return {"agents": list_agents()}


@router.get("/agents/{agent_type}")
def agent_detail(agent_type: str) -> dict:
    try:
        agent = get_agent(agent_type)
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc
    return {"agent_type": agent_type, "description": getattr(agent, "description", "")}


@router.get("/agents/{agent_type}/tools")
def agent_tools(agent_type: str, ref: str | None = Query(default=None)) -> dict:
    """Read-only tool catalog for one agent type.

    With ``?ref=`` the caller only needs project visibility (viewers included).
    Without a project context, global write roles are required. Like every
    other GET route here, the read is *not* audited (no mutating side effect).
    """

    try:
        get_agent(agent_type)
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc
    with session_scope() as db:
        if ref is not None:
            _project(db, ref)
        else:
            actor = _actor()
            if actor.get("role") not in {auth.ROLE_MANAGER, auth.ROLE_SUPER_ADMIN}:
                raise HTTPException(status_code=403, detail="只读成员需提供项目上下文")
        tools = [_tool_payload(spec) for spec in list_tools()]
    return {"agent_type": agent_type, "tools": tools}


# --------------------------------------------------------------------------- #
# 工作流运行
# --------------------------------------------------------------------------- #
@router.get("/projects/{ref}/workflows")
def workflows(ref: str, limit: int = Query(50, ge=1, le=200)) -> dict:
    with session_scope() as db:
        project, _role = _project(db, ref)
        rows = list(
            db.query(WorkflowRun)
            .filter(WorkflowRun.project_id == project.id)
            .order_by(WorkflowRun.created_at.desc())
            .limit(limit)
        )
        return {"items": [run_payload(r) for r in rows]}


@router.post("/projects/{ref}/workflows", status_code=202)
def create_workflow(ref: str, payload: WorkflowCreate) -> dict:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        actor = _actor()
        _check_backpressure(db, str(actor.get("id") or ""))
        try:
            run = start_workflow(
                db,
                project,
                workflow_type=payload.workflow_type or "standard",
                goal=payload.goal or "",
                input_payload=dict(payload.input or {}),
                budget=dict(payload.budget or {}),
                created_by=_username(),
            )
            schedule_workflow(db, run, owner_id=str(actor.get("id") or ""), priority=1)
        except (OrchestrationError, PlanError) as exc:
            raise _error(exc) from exc
        audit(db, project.id, "workflow.create", {"run_id": run.id, "workflow_type": run.workflow_type})
        result = run_payload(run)
    # submit 在事务提交后唤醒 worker；失败不影响 run 落库（worker/reaper 会认领）。
    from ... import jobqueue

    jobqueue.submit()
    return result


@router.get("/projects/{ref}/workflows/{run_id}")
def workflow_detail(ref: str, run_id: str) -> dict:
    with session_scope() as db:
        project, _role = _project(db, ref)
        run = _run_for_project(db, project.id, run_id)
        status = get_workflow_status(db, run.id)
        # 轨迹落在 WorkflowTask.output / run.result；在 HTTP 边界额外暴露
        # 顶层 tool_calls，前端无需猜测嵌套位置（缺失一律空列表）。
        for task in status.get("tasks") or []:
            task["tool_calls"] = _trace_from(task.get("output"))
        status["tool_calls"] = _trace_from(status.get("result"))
        return status


@router.get("/projects/{ref}/workflows/{run_id}/tasks")
def workflow_tasks(ref: str, run_id: str) -> dict:
    with session_scope() as db:
        project, _role = _project(db, ref)
        run = _run_for_project(db, project.id, run_id)
        from ...models import WorkflowTask

        tasks = list(
            db.query(WorkflowTask)
            .filter(WorkflowTask.run_id == run.id)
            .order_by(WorkflowTask.seq)
        )
        items = [task_payload(t) for t in tasks]
        for item in items:
            item["tool_calls"] = _trace_from(item.get("output"))
        return {"items": items}


@router.post("/projects/{ref}/workflows/{run_id}/cancel")
def cancel(ref: str, run_id: str) -> dict:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        run = _run_for_project(db, project.id, run_id)
        try:
            cancel_workflow(db, run.id, actor=_actor())
        except OrchestrationError as exc:
            raise _detail(exc) from exc
        audit(db, project.id, "workflow.cancel", {"run_id": run.id})
        return run_payload(run)


@router.post("/projects/{ref}/workflows/{run_id}/resume")
def resume(ref: str, run_id: str) -> dict:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        run = _run_for_project(db, project.id, run_id)
        _check_backpressure(db, str(_actor().get("id") or ""))
        try:
            run = resume_workflow(db, run.id, actor=_actor())
        except OrchestrationError as exc:
            raise _detail(exc) from exc
        # 重新调度一个 agent Job 继续驱动（审批挂起不占旧 Job 租约）。
        job = schedule_workflow(db, run, owner_id=str(_actor().get("id") or ""), priority=1)
        audit(db, project.id, "workflow.resume", {"run_id": run.id, "job_id": job.id})
        result = run_payload(run)
        job_id = job.id
    from ... import jobqueue

    jobqueue.submit()
    return {**result, "job_id": job_id}


# --------------------------------------------------------------------------- #
# 人工审批
# --------------------------------------------------------------------------- #
@router.get("/projects/{ref}/workflow-approvals")
def approvals(ref: str, limit: int = Query(50, ge=1, le=200)) -> dict:
    with session_scope() as db:
        project, _role = _project(db, ref)
        run_ids = [r.id for r in db.query(WorkflowRun.id).filter(WorkflowRun.project_id == project.id)]
        if not run_ids:
            return {"items": []}
        rows = (
            db.query(WorkflowApproval)
            .filter(WorkflowApproval.run_id.in_(run_ids))
            .order_by(WorkflowApproval.submitted_at.desc())
            .limit(limit)
        )
        return {"items": [approval_payload(a) for a in rows]}


@router.post("/projects/{ref}/workflow-approvals/{approval_id}/review")
def review_approval(ref: str, approval_id: str, payload: WorkflowApprovalReview) -> dict:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        approval = db.get(WorkflowApproval, approval_id)
        if approval is None or _run_for_project(db, project.id, approval.run_id).id != approval.run_id:
            raise LookupError(f"审批不存在: {approval_id}")
        rejecting = (payload.decision or "approve").lower() in {"reject", "rejected", "deny"}
        if not rejecting:
            # Only approval re-schedules an agent Job; enforce the same cap.
            _check_backpressure(db, str(_actor().get("id") or ""))
        try:
            if rejecting:
                updated = reject_task(db, approval_id, actor=_actor(), note=payload.note or "")
            else:
                updated = approve_task(db, approval_id, actor=_actor(), note=payload.note or "")
        except ApprovalError as exc:
            raise _detail(exc) from exc
        audit(db, project.id, "workflow.approval.review", {"approval_id": approval_id, "decision": payload.decision})
        result = approval_payload(updated)
        run_id = updated.run_id
        approved = updated.status == "approved"
        job_id = ""
        if approved:
            # 审批通过后 run 回到 running，但旧 agent Job 在挂起时已结束；和
            # /resume 一样必须新建一条排队 Job，否则 run 永久停在 running。
            run = _run_for_project(db, project.id, run_id)
            job = schedule_workflow(
                db, run, owner_id=str(_actor().get("id") or ""), priority=1
            )
            audit(
                db,
                project.id,
                "workflow.approval.resume",
                {"approval_id": approval_id, "run_id": run_id, "job_id": job.id},
            )
            job_id = job.id
    if approved:
        from ... import jobqueue

        jobqueue.submit()
    return {**result, "run_id": run_id, "job_id": job_id}
