"""工作流编排器：状态机、任务派发、重试、取消、恢复与预算熔断。

设计要点：

* 状态唯一真相在 ``workflow_runs`` / ``workflow_tasks``；Job 只负责唤醒，
  审批挂起时 Job 正常结束（``waiting_approval`` 不占租约），恢复时另起 Job。
* 任务幂等以 ``(run_id, task_key)`` + 任务终态为准：已 done/skipped 的任务
  重复派发直接返回，不重复执行。
* 失败任务保持 ``failed`` 并写入 ``error``，绝不伪装成功；只有显式
  :func:`retry_agent_task` 才允许重新执行。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import jobs
from ..authorization import authorize_project
from ..clock import utc_now
from ..db import session_scope
from ..models import Job, Project, WorkflowApproval, WorkflowRun, WorkflowTask
from .approval import approval_payload, request_approval, resume_after_approval
from .clock_budget import active_seconds, pause_active_clock, start_active_clock
from .planner import build_workflow, tool_policy
from .registry import AgentOutput, get_agent

logger = logging.getLogger("bidcraft.orchestration")

TERMINAL_RUN_STATUSES = frozenset({"done", "failed", "cancelled"})
TERMINAL_TASK_STATUSES = frozenset({"done", "skipped"})
DEFAULT_BUDGET = {"max_steps": 50, "max_tokens": 200000, "max_seconds": 3600}


class OrchestrationError(ValueError):
    """工作流状态 / 依赖 / 预算不合法。"""


class BudgetExceeded(OrchestrationError):
    """预算熔断。"""


# --------------------------------------------------------------------------- #
# 读取 / 序列化
# --------------------------------------------------------------------------- #
def get_run(db: Session, run_id: str) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise LookupError(f"工作流不存在: {run_id}")
    return run


def _tasks(db: Session, run_id: str) -> list[WorkflowTask]:
    return list(
        db.scalars(
            select(WorkflowTask)
            .where(WorkflowTask.run_id == run_id)
            .order_by(WorkflowTask.seq, WorkflowTask.id)
        )
    )


def _approvals(db: Session, run_id: str) -> list[WorkflowApproval]:
    return list(
        db.scalars(
            select(WorkflowApproval)
            .where(WorkflowApproval.run_id == run_id)
            .order_by(WorkflowApproval.submitted_at, WorkflowApproval.id)
        )
    )


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def task_payload(task: WorkflowTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "run_id": task.run_id,
        "seq": task.seq,
        "task_key": task.task_key,
        "agent_type": task.agent_type,
        "status": task.status,
        "input": task.input or {},
        "output": task.output or {},
        "error": task.error or "",
        "attempts": task.attempts or 0,
        "started_at": _iso(task.started_at),
        "completed_at": _iso(task.completed_at),
    }


def run_payload(
    run: WorkflowRun,
    *,
    tasks: list[WorkflowTask] | None = None,
    approvals: list[WorkflowApproval] | None = None,
) -> dict[str, Any]:
    rows = tasks or []
    done = sum(1 for task in rows if task.status in TERMINAL_TASK_STATUSES)
    payload: dict[str, Any] = {
        "id": run.id,
        "project_id": run.project_id,
        "job_id": run.job_id,
        "workflow_type": run.workflow_type,
        "status": run.status,
        "goal": run.goal or "",
        "input": run.input_snapshot or {},
        "result": run.result or {},
        "stats": run.stats or {},
        "budget": run.budget or {},
        "created_by": run.created_by or "",
        "created_at": _iso(run.created_at),
        "updated_at": _iso(run.updated_at),
        "completed_at": _iso(run.completed_at),
        "progress": round(done / len(rows), 4) if rows else 0.0,
        "task_count": len(rows),
        "tasks_done": done,
    }
    if tasks is not None:
        payload["tasks"] = [task_payload(task) for task in rows]
    if approvals is not None:
        payload["approvals"] = [approval_payload(row) for row in approvals]
    return payload


def get_workflow_status(db: Session, run_id: str) -> dict[str, Any]:
    run = get_run(db, run_id)
    return run_payload(
        run,
        tasks=_tasks(db, run_id),
        approvals=_approvals(db, run_id),
    )


# --------------------------------------------------------------------------- #
# 创建 / 调度 Job
# --------------------------------------------------------------------------- #
def _normalize_budget(budget: dict[str, Any] | None) -> dict[str, int]:
    raw = dict(budget or {})

    def _value(keys: tuple[str, ...], default: int) -> int:
        for key in keys:
            if key in raw and raw[key] is not None:
                try:
                    return max(0, int(raw[key]))
                except (TypeError, ValueError):
                    pass
        return default

    return {
        "max_steps": _value(("max_steps", "steps"), DEFAULT_BUDGET["max_steps"]),
        "max_tokens": _value(("max_tokens", "tokens"), DEFAULT_BUDGET["max_tokens"]),
        "max_seconds": _value(("max_seconds", "seconds"), DEFAULT_BUDGET["max_seconds"]),
    }


def start_workflow(
    db: Session,
    project: Project,
    *,
    workflow_type: str = "standard",
    goal: str = "",
    input_payload: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
    created_by: str = "",
) -> WorkflowRun:
    """Create a run and its tasks.  Raises ``PlanError`` on an invalid DAG."""

    payload = dict(input_payload or {})
    planned = build_workflow(workflow_type, payload)
    run = WorkflowRun(
        project_id=project.id,
        workflow_type=workflow_type,
        status="queued",
        goal=goal or "",
        input_snapshot=payload,
        result={},
        stats={
            "steps": 0,
            "tokens": 0,
            "tasks_total": len(planned),
            "tasks_done": 0,
            "tasks_failed": 0,
        },
        budget=_normalize_budget(budget),
        created_by=created_by,
    )
    db.add(run)
    db.flush()
    for item in planned:
        db.add(
            WorkflowTask(
                run_id=run.id,
                seq=int(item["seq"]),
                task_key=item["task_key"],
                agent_type=item["agent_type"],
                status="pending",
                input=item.get("input") or {},
                output={},
                attempts=0,
            )
        )
    db.flush()
    return run


def schedule_workflow(
    db: Session,
    run: WorkflowRun,
    *,
    owner_id: str = "",
    priority: int = 1,
) -> Job:
    """Create the ``agent`` job row that will drive this run (no submit)."""

    job = Job(
        project_id=run.project_id,
        kind="agent",
        status="queued",
        priority=priority,
        message="Agent 工作流排队中…",
        payload={
            "run_id": run.id,
            "owner_id": owner_id or run.created_by,
            "user_id": run.created_by,
        },
    )
    db.add(job)
    db.flush()
    run.job_id = job.id
    jobs.publish_event(
        job.id, "queued", jobs.job_payload(job), db=db
    )
    db.flush()
    return job


# --------------------------------------------------------------------------- #
# 依赖 / 预算 / 权限
# --------------------------------------------------------------------------- #
def _dependencies_done(task: WorkflowTask, by_key: dict[str, WorkflowTask]) -> bool:
    for key in (task.input or {}).get("_depends_on") or []:
        dependency = by_key.get(key)
        if dependency is None or dependency.status not in TERMINAL_TASK_STATUSES:
            return False
    return True


def _dependency_keys(db: Session, run_id: str) -> dict[str, list[str]]:
    """Dependencies live in the run's planned graph, not in task input.

    ``build_workflow`` is deterministic, so rebuilding the graph from the run's
    snapshot gives the exact ``depends_on`` map without persisting a second copy.
    """

    run = db.get(WorkflowRun, run_id)
    if run is None:
        return {}
    try:
        planned = build_workflow(run.workflow_type, dict(run.input_snapshot or {}))
    except Exception:  # noqa: BLE001 - a corrupted snapshot blocks safely
        return {}
    return {item["task_key"]: list(item["depends_on"]) for item in planned}


def _ready_tasks(
    db: Session, run: WorkflowRun, tasks: list[WorkflowTask]
) -> list[WorkflowTask]:
    graph = _dependency_keys(db, run.id)
    by_key = {task.task_key: task for task in tasks}
    ready: list[WorkflowTask] = []
    for task in tasks:
        if task.status != "pending":
            continue
        deps = graph.get(task.task_key, [])
        if all(
            dep in by_key and by_key[dep].status in TERMINAL_TASK_STATUSES
            for dep in deps
        ):
            ready.append(task)
    return ready


def _budget_error(run: WorkflowRun) -> str:
    budget = {**DEFAULT_BUDGET, **(run.budget or {})}
    stats = run.stats or {}
    if int(stats.get("steps") or 0) >= int(budget["max_steps"]):
        return f"预算熔断：步骤数已达上限 {budget['max_steps']}"
    if int(stats.get("tokens") or 0) >= int(budget["max_tokens"]):
        return f"预算熔断：token 用量已达上限 {budget['max_tokens']}"
    # 只统计 Agent 实际执行时间；审批挂起 / Job 排队期间停表。
    elapsed = active_seconds(run)
    if elapsed >= int(budget["max_seconds"]):
        return f"预算熔断：运行时长已达上限 {budget['max_seconds']} 秒"
    return ""


def _authorize_actor(
    db: Session,
    run: WorkflowRun,
    actor: dict[str, Any] | None,
    *,
    task: WorkflowTask | None = None,
) -> None:
    """Read-only/no-tool 路径保持旧行为；写工具没有显式 actor 时直接拒绝。"""

    if not actor:
        if task is not None and tool_policy(run.workflow_type, task.agent_type).mutating:
            raise OrchestrationError("写工具需要显式用户身份，禁止匿名执行")
        return
    authorize_project(db, run.project_id, action="write", user=actor)


def _fail_run(
    db: Session, run: WorkflowRun, message: str, *, tool_calls: list[dict] | None = None
) -> None:
    pause_active_clock(run)
    run.status = "failed"
    run.completed_at = utc_now()
    result = {**(run.result or {}), "error": message}
    if tool_calls:
        result["tool_calls"] = tool_calls
    run.result = result
    stats = dict(run.stats or {})
    stats["error"] = message
    run.stats = stats
    for task in _tasks(db, run.id):
        if task.status in {"pending", "running"}:
            task.status = "skipped"
            task.error = message
            task.completed_at = utc_now()
    db.flush()


def _complete_run(db: Session, run: WorkflowRun, tasks: list[WorkflowTask]) -> None:
    pause_active_clock(run)
    warnings: list[str] = []
    for task in tasks:
        for warning in (task.output or {}).get("warnings") or []:
            warnings.append(f"{task.task_key}: {warning}")
    run.status = "done"
    run.completed_at = utc_now()
    run.stats = {
        **(run.stats or {}),
        "tasks_done": sum(1 for task in tasks if task.status in TERMINAL_TASK_STATUSES),
        "tasks_failed": sum(1 for task in tasks if task.status == "failed"),
        "completed_at": utc_now().isoformat(),
    }
    tool_calls: list[dict[str, Any]] = []
    for task in tasks:
        for trace in (task.output or {}).get("tool_calls") or []:
            if len(tool_calls) >= 100:
                break
            if isinstance(trace, dict):
                tool_calls.append(trace)
    run.result = {
        "tasks": {task.task_key: task.status for task in tasks},
        "warnings": warnings[:50],
        "goal": run.goal or "",
        "tool_calls": tool_calls,
    }
    db.flush()


#: 用户请求体里任何试图控制工具策略的键都必须丢弃；策略只来自服务端配置。
_USER_TOOL_CONTROL_KEYS = frozenset(
    {"tools", "tool_policy", "tool_calls", "allowed_tools", "required_tools"}
)


def _task_payload_for_agent(
    run: WorkflowRun, task: WorkflowTask
) -> dict[str, Any]:
    snapshot = dict(run.input_snapshot or {})
    snapshot.pop("tasks", None)
    snapshot.pop("_tasks", None)
    snapshot = {
        key: value
        for key, value in snapshot.items()
        if key not in _USER_TOOL_CONTROL_KEYS
        and not str(key).startswith("_tool")
    }
    task_input = {
        key: value
        for key, value in (task.input or {}).items()
        if key not in _USER_TOOL_CONTROL_KEYS
        and not str(key).startswith("_tool")
    }
    return {**snapshot, **task_input}


def _upstream_outputs(
    db: Session, run: WorkflowRun, task: WorkflowTask
) -> dict[str, dict[str, Any]]:
    """All completed ancestor outputs, not just direct dependencies.

    The standard DAG funnels through an ``approval_gate`` before ``delivery``;
    giving the delivery agent only that gate's output hides the four real
    upstream results (tender/technical/commercial/compliance).  Walking the
    transitive closure keeps gates and reviewers honest for any custom DAG.
    """

    graph = _dependency_keys(db, run.id)
    by_key = {row.task_key: row for row in _tasks(db, run.id)}
    collected: set[str] = set()
    stack = list(graph.get(task.task_key, []))
    while stack:
        key = stack.pop()
        if key in collected:
            continue
        collected.add(key)
        stack.extend(graph.get(key, []))
    return {
        key: dict(by_key[key].output or {})
        for key in sorted(collected)
        if key in by_key
    }


def _record_output_stats(run: WorkflowRun, output: AgentOutput) -> None:
    stats = dict(run.stats or {})
    tokens = output.result.get("tokens") or 0
    if not tokens:
        usage = output.result.get("usage") or {}
        if isinstance(usage, dict):
            tokens = usage.get("total_tokens") or (
                int(usage.get("prompt_tokens") or 0)
                + int(usage.get("completion_tokens") or 0)
            )
    try:
        stats["tokens"] = int(stats.get("tokens") or 0) + int(tokens or 0)
    except (TypeError, ValueError):
        pass
    run.stats = stats


# --------------------------------------------------------------------------- #
# 派发
# --------------------------------------------------------------------------- #
def dispatch_task(
    db: Session,
    task: WorkflowTask,
    *,
    run: WorkflowRun | None = None,
    actor: dict[str, Any] | None = None,
) -> WorkflowTask:
    """Execute (or gate) one task.  Idempotent for terminal tasks."""

    run = run or get_run(db, task.run_id)
    if run.status == "cancelled":
        if task.status in {"pending", "running"}:
            task.status = "skipped"
            task.error = "工作流已取消"
            task.completed_at = utc_now()
        return task
    if task.status in TERMINAL_TASK_STATUSES:
        return task
    if task.status == "failed":
        return task
    if run.status in TERMINAL_RUN_STATUSES:
        return task

    by_key = {row.task_key: row for row in _tasks(db, run.id)}
    graph = _dependency_keys(db, run.id)
    for key in graph.get(task.task_key, []):
        dependency = by_key.get(key)
        if dependency is None or dependency.status not in TERMINAL_TASK_STATUSES:
            raise OrchestrationError(f"任务 {task.task_key} 的依赖 {key} 尚未完成")

    reason = _budget_error(run)
    if reason:
        _fail_run(db, run, reason)
        raise BudgetExceeded(reason)

    _authorize_actor(db, run, actor, task=task)

    # 审批闸门：不执行 Agent，只创建审批并挂起。
    if (task.input or {}).get("approval"):
        task.attempts = (task.attempts or 0) + 1
        task.started_at = task.started_at or utc_now()
        approval = request_approval(
            db,
            run,
            task,
            approval_type=str(
                (task.input or {}).get("approval_type") or "delivery"
            ),
            submitted_by=run.created_by or "",
        )
        task.output = {
            "approval_id": approval.id,
            "approval_type": approval.approval_type,
            "status": "pending",
        }
        db.flush()
        return task

    project = db.get(Project, run.project_id)
    if project is None:
        task.status = "failed"
        task.error = "项目不存在"
        task.completed_at = utc_now()
        _fail_run(db, run, f"任务 {task.task_key} 失败：项目不存在")
        return task

    try:
        agent = get_agent(task.agent_type)
    except LookupError as exc:
        task.status = "failed"
        task.error = str(exc)
        task.completed_at = utc_now()
        _fail_run(db, run, f"任务 {task.task_key} 失败：{exc}")
        return task

    task.status = "running"
    task.attempts = (task.attempts or 0) + 1
    task.started_at = task.started_at or utc_now()
    task.error = ""
    stats = dict(run.stats or {})
    stats["steps"] = int(stats.get("steps") or 0) + 1
    run.stats = stats
    if run.status in {"queued", "waiting_approval"}:
        run.status = "running"
    start_active_clock(run)
    # Release the write lock before the Agent opens its own short sessions.
    db.commit()

    upstream = _upstream_outputs(db, run, task)
    payload = _task_payload_for_agent(run, task)
    policy = tool_policy(run.workflow_type, task.agent_type)
    try:
        output = agent.run(
            db,
            project,
            payload,
            upstream=upstream,
            run={
                "id": run.id,
                "task_id": task.id,
                "goal": run.goal,
                "workflow_type": run.workflow_type,
                "project_id": run.project_id,
                "tool_policy": policy.as_dict(),
            },
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a failed task
        logger.exception("Agent 任务失败 run=%s task=%s", run.id, task.task_key)
        task.status = "failed"
        task.error = str(exc) or exc.__class__.__name__
        task.completed_at = utc_now()
        _fail_run(db, run, f"任务 {task.task_key} 失败：{task.error}")
        return task

    _record_output_stats(run, output)
    task.output = output.model_dump()
    if output.status == "failed":
        task.status = "failed"
        task.error = str(output.result.get("error") or "Agent 执行失败")
        task.completed_at = utc_now()
        _fail_run(
            db,
            run,
            f"任务 {task.task_key} 失败：{task.error}",
            tool_calls=output.tool_calls,
        )
        return task

    if output.requires_approval:
        approval = request_approval(
            db,
            run,
            task,
            approval_type=output.approval_type or "delivery",
            submitted_by=run.created_by or "",
        )
        task.output = {
            **task.output,
            "approval_id": approval.id,
            "approval_type": approval.approval_type,
        }
        db.flush()
        return task

    task.status = "skipped" if output.status == "skipped" else "done"
    task.completed_at = utc_now()
    task.error = ""
    db.flush()
    return task


def execute_agent_task(
    run_id: str,
    task_id: str | None = None,
    *,
    task_key: str = "",
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Own-session wrapper around :func:`dispatch_task` for workers/tests."""

    with session_scope() as db:
        run = get_run(db, run_id)
        task: WorkflowTask | None = None
        if task_id:
            task = db.get(WorkflowTask, task_id)
        elif task_key:
            task = db.scalar(
                select(WorkflowTask).where(
                    WorkflowTask.run_id == run_id,
                    WorkflowTask.task_key == task_key,
                )
            )
        if task is None:
            raise LookupError("工作流任务不存在")
        dispatch_task(db, task, run=run, actor=actor)
        return task_payload(task)


# --------------------------------------------------------------------------- #
# 运行循环
# --------------------------------------------------------------------------- #
def _reset_interrupted_tasks(db: Session, run: WorkflowRun) -> None:
    """A fresh runner owns the run: tasks left ``running`` were interrupted."""

    approvals = _approvals(db, run.id)
    awaiting = {
        row.task_id
        for row in approvals
        if row.status == "pending" and row.task_id
    }
    changed = False
    for task in _tasks(db, run.id):
        if task.status == "running" and task.id not in awaiting:
            task.status = "pending"
            task.error = "上次执行中断，已重新排队"
            task.started_at = None
            changed = True
    if changed:
        db.flush()


def _snapshot(db: Session, run_id: str) -> dict[str, Any]:
    run = get_run(db, run_id)
    return {
        "status": run.status,
        "run": run_payload(run),
    }


def run_workflow(
    run_id: str,
    *,
    job_id: str = "",
    actor: dict[str, Any] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    max_iterations: int = 200,
) -> dict[str, Any]:
    """Drive a run to a terminal state, an approval gate, or a failure.

    A gate returns ``waiting_approval`` (the caller must end its Job); resuming
    creates a new Job and calls this function again.
    """

    with session_scope() as db:
        run = get_run(db, run_id)
        if run.status not in TERMINAL_RUN_STATUSES and run.status != "waiting_approval":
            _reset_interrupted_tasks(db, run)
            if run.status == "queued":
                run.status = "running"
            if run.status == "running":
                start_active_clock(run)
    if progress is not None:
        with session_scope() as db:
            progress(run_payload(get_run(db, run_id)))

    for _ in range(max_iterations):
        with session_scope() as db:
            run = get_run(db, run_id)
            if run.status == "waiting_approval":
                return _snapshot(db, run_id)
            if run.status in TERMINAL_RUN_STATUSES:
                return _snapshot(db, run_id)
            reason = _budget_error(run)
            if reason:
                _fail_run(db, run, reason)
                return _snapshot(db, run_id)

            tasks = _tasks(db, run_id)
            pending = [task for task in tasks if task.status == "pending"]
            running = [task for task in tasks if task.status == "running"]
            if not pending:
                if running:
                    # Interrupted task without a pending gate: reset next round.
                    for task in running:
                        task.status = "pending"
                        task.error = "上次执行中断，已重新排队"
                elif all(
                    task.status in TERMINAL_TASK_STATUSES for task in tasks
                ):
                    _complete_run(db, run, tasks)
                    result = _snapshot(db, run_id)
                else:
                    failed = [task.task_key for task in tasks if task.status == "failed"]
                    _fail_run(
                        db,
                        run,
                        "工作流失败：" + ("、".join(failed) if failed else "无待执行任务"),
                    )
                    result = _snapshot(db, run_id)
                if progress is not None:
                    progress(result["run"])
                return result
            ready = _ready_tasks(db, run, tasks)
            if not ready:
                failed = [task.task_key for task in tasks if task.status == "failed"]
                if failed:
                    _fail_run(db, run, "工作流失败：" + "、".join(failed))
                else:
                    _fail_run(db, run, "工作流依赖无法满足（可能存在未完成的依赖）")
                result = _snapshot(db, run_id)
                if progress is not None:
                    progress(result["run"])
                return result
            ready_ids = [task.id for task in ready]

        for task_id in ready_ids:
            with session_scope() as db:
                run = get_run(db, run_id)
                if run.status in TERMINAL_RUN_STATUSES or run.status == "waiting_approval":
                    break
                task = db.get(WorkflowTask, task_id)
                if task is None or task.status != "pending":
                    continue
                dispatch_task(db, task, run=run, actor=actor)
            if progress is not None:
                with session_scope() as db:
                    progress(run_payload(get_run(db, run_id)))

    with session_scope() as db:
        _fail_run(get_run(db, run_id), "工作流迭代次数超限")
        return _snapshot(db, run_id)


# --------------------------------------------------------------------------- #
# 重试 / 取消 / 恢复
# --------------------------------------------------------------------------- #
def retry_agent_task(db: Session, task_id: str) -> WorkflowTask:
    """Reset one failed task to pending; the run becomes resumable."""

    task = db.get(WorkflowTask, task_id)
    if task is None:
        raise LookupError("工作流任务不存在")
    run = get_run(db, task.run_id)
    if task.status in TERMINAL_TASK_STATUSES:
        return task
    if task.status != "failed":
        raise OrchestrationError("只有失败任务可以重试")
    if run.status == "cancelled":
        raise OrchestrationError("已取消的工作流不能重试任务")
    task.status = "pending"
    task.error = ""
    task.completed_at = None
    task.started_at = None
    run.status = "running"
    start_active_clock(run)
    run.completed_at = None
    run.result = {
        key: value for key, value in (run.result or {}).items() if key != "error"
    }
    db.flush()
    return task


def cancel_workflow(
    db: Session,
    run_id: str,
    *,
    actor: dict[str, Any] | None = None,
) -> WorkflowRun:
    """Cancel a run.  Terminal ``done`` runs cannot be cancelled."""

    run = get_run(db, run_id)
    _authorize_actor(db, run, actor)
    if run.status == "cancelled":
        return run
    if run.status == "done":
        raise OrchestrationError("已完成的工作流不能取消")
    pause_active_clock(run)
    run.status = "cancelled"
    run.completed_at = utc_now()
    run.result = {**(run.result or {}), "cancelled": True}
    for task in _tasks(db, run.id):
        if task.status in {"pending", "running"}:
            task.status = "skipped"
            task.error = "工作流已取消"
            task.completed_at = utc_now()
    for approval in _approvals(db, run.id):
        if approval.status == "pending":
            approval.status = "rejected"
            approval.review_note = "工作流已取消"
            approval.reviewed_by = str((actor or {}).get("username") or "system")
            approval.reviewed_at = utc_now()
    db.flush()
    return run


def resume_workflow(
    db: Session,
    run_id: str,
    *,
    actor: dict[str, Any] | None = None,
) -> WorkflowRun:
    """Resume after approval, or re-open a failed run for retry."""

    run = get_run(db, run_id)
    _authorize_actor(db, run, actor)
    if run.status == "cancelled":
        raise OrchestrationError("已取消的工作流不能恢复")
    if run.status == "done":
        return run
    if run.status == "waiting_approval":
        resume_after_approval(db, run_id)
        start_active_clock(run)
        db.flush()
        return run
    if run.status == "failed":
        for task in _tasks(db, run.id):
            if task.status == "failed":
                task.status = "pending"
                task.error = ""
                task.completed_at = None
                task.started_at = None
        run.status = "running"
        start_active_clock(run)
        run.completed_at = None
        run.result = {
            key: value for key, value in (run.result or {}).items() if key != "error"
        }
        db.flush()
        return run
    run.status = "running"
    start_active_clock(run)
    db.flush()
    return run


__all__ = [
    "BudgetExceeded",
    "OrchestrationError",
    "cancel_workflow",
    "dispatch_task",
    "execute_agent_task",
    "get_run",
    "get_workflow_status",
    "resume_workflow",
    "retry_agent_task",
    "run_payload",
    "run_workflow",
    "schedule_workflow",
    "start_workflow",
    "task_payload",
]
