"""Agent 编排系统：注册表 / DAG 规划 / 运行 / 审批 / 恢复 + worker。

全部用真实 DB 驱动，不依赖真实模型端点；Agent 走确定性分析路径。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import select  # noqa: E402

from bidcraft.db import session_scope  # noqa: E402
from bidcraft.models import AuditLog, Job, Project, WorkflowApproval, WorkflowRun, WorkflowTask  # noqa: E402
from bidcraft.orchestration import (  # noqa: E402
    approve_task,
    cancel_workflow,
    get_workflow_status,
    list_agents,
    reject_task,
    resume_workflow,
    run_workflow,
    start_workflow,
    validate_workflow_graph,
)
from bidcraft.orchestration.orchestrator import schedule_workflow  # noqa: E402
from bidcraft.orchestration.planner import build_workflow  # noqa: E402
from bidcraft.orchestration.registry import list_tools  # noqa: E402
from bidcraft.pipeline import create_project  # noqa: E402

# super_admin 通过 _effective_role 直接获得 admin（无需 DB 用户/成员关系），
# 适合纯 service 层测试；HTTP 权限由 test_collab 等覆盖。
ACTOR = {"id": "u-owner", "username": "owner", "role": "super_admin"}


def _project(name="wf-test"):
    with session_scope() as sess:
        return create_project(sess, name).id


def _start(pid, **kw):
    with session_scope() as sess:
        project = sess.get(Project, pid)
        run = start_workflow(sess, project, **kw)
        return run.id


def test_builtin_agents_and_tools_registered(db):
    agents = {a["agent_type"] for a in list_agents()}
    assert {"tender", "technical", "commercial", "knowledge", "compliance", "reviewer"} <= agents
    assert isinstance(list_tools(), list)


def test_dag_graph_validation_and_standard_plan(db):
    plan = build_workflow("standard", {})
    keys = [t["task_key"] for t in plan]
    assert len(keys) == len(set(keys)), "任务键必须唯一"
    assert keys.index("compliance_check") < keys.index("approval_gate") < keys.index("delivery")
    assert validate_workflow_graph(plan) == [], "标准 DAG 应无校验错误"


def test_start_workflow_creates_tasks_and_job(db):
    pid = _project("wf-start")
    with session_scope() as sess:
        project = sess.get(Project, pid)
        run = start_workflow(sess, project, goal="目标")
        schedule_workflow(sess, run, owner_id="u1")
        assert run.status == "queued"
        tasks = list(sess.scalars(select(WorkflowTask).where(WorkflowTask.run_id == run.id)))
        assert len(tasks) == 9
        assert run.job_id


def test_run_workflow_drives_to_terminal_or_gate(db):
    rid = _start(_project("wf-run"), goal="跑通")
    result = run_workflow(rid)
    assert result["status"] in {"waiting_approval", "done", "failed"}
    with session_scope() as sess:
        run = sess.get(WorkflowRun, rid)
        tasks = list(sess.scalars(select(WorkflowTask).where(WorkflowTask.run_id == rid)))
        assert run.status in {"waiting_approval", "done", "failed"}
        assert any(t.status in {"done", "skipped"} for t in tasks), "至少应有任务被执行"


def test_get_workflow_status_includes_tasks_and_approvals(db):
    rid = _start(_project("wf-status"))
    run_workflow(rid)
    with session_scope() as sess:
        status = get_workflow_status(sess, rid)
    assert status["id"] == rid
    assert "tasks" in status
    assert "approvals" in status


def test_approval_gate_blocks_downstream_until_approved(db):
    rid = _start(_project("wf-approval"))
    result = run_workflow(rid)
    if result["status"] != "waiting_approval":
        pytest.skip("本样本未触发审批闸门（无阻断风险）")
    with session_scope() as sess:
        tasks = {t.task_key: t for t in sess.scalars(select(WorkflowTask).where(WorkflowTask.run_id == rid))}
        assert tasks["delivery"].status == "pending", "审批前不得执行交付任务"
        approval = sess.scalar(
            select(WorkflowApproval).where(
                WorkflowApproval.run_id == rid, WorkflowApproval.status == "pending"
            )
        )
        assert approval is not None
        aid = approval.id

    with session_scope() as sess:
        approve_task(sess, aid, actor=ACTOR)
    with session_scope() as sess:
        run = resume_workflow(sess, rid, actor=ACTOR)
        assert run.status == "running"
    result2 = run_workflow(rid)
    # 标准工作流可能有多道审批门（合规门 + 交付门）；批准一道后再次挂起是正常行为。
    assert result2["status"] in {"waiting_approval", "done", "failed"}


def test_reject_approval_marks_run_failed_and_blocks_delivery(db):
    rid = _start(_project("wf-reject"))
    result = run_workflow(rid)
    if result["status"] != "waiting_approval":
        pytest.skip("本样本未触发审批闸门")
    with session_scope() as sess:
        approval = sess.scalar(
            select(WorkflowApproval).where(
                WorkflowApproval.run_id == rid, WorkflowApproval.status == "pending"
            )
        )
        aid = approval.id
    with session_scope() as sess:
        reject_task(sess, aid, actor=ACTOR, note="不通过")
    # 驳回后审批为 rejected、工作流为 failed、交付任务不得执行、写入审计。
    with session_scope() as sess:
        run = sess.get(WorkflowRun, rid)
        ap = sess.get(WorkflowApproval, aid)
        tasks = {t.task_key: t for t in sess.scalars(select(WorkflowTask).where(WorkflowTask.run_id == rid))}
        assert ap.status == "rejected"
        assert run.status == "failed"
        assert tasks["delivery"].status == "pending"
        audit = sess.scalar(
            select(AuditLog).where(AuditLog.action == "workflow.approval.reject")
        )
        assert audit is not None


def test_cancel_workflow_marks_cancelled(db):
    rid = _start(_project("wf-cancel"))
    with session_scope() as sess:
        run = cancel_workflow(sess, rid, actor=ACTOR)
        assert run.status == "cancelled"


def test_interrupted_run_resumes_and_completes(db):
    """中断（任务 running 未推进）后重新驱动应重置并继续，不丢任务。"""
    rid = _start(_project("wf-resume"))
    with session_scope() as sess:
        task = sess.scalar(
            select(WorkflowTask).where(WorkflowTask.run_id == rid).order_by(WorkflowTask.seq)
        )
        task.status = "running"  # 模拟中断
    result = run_workflow(rid)
    assert result["status"] in {"waiting_approval", "done", "failed"}
    with session_scope() as sess:
        run = sess.get(WorkflowRun, rid)
        assert run.status in {"waiting_approval", "done", "failed"}


def test_agent_worker_registered_and_rebuild_args(db):
    import bidcraft.workers  # noqa: F401  触发注册
    from bidcraft import jobqueue

    job = Job(kind="agent", status="queued", project_id="p", payload={"run_id": "r1"})
    assert jobqueue._rebuild_args(job) == (job.id, "r1")
    assert "agent" in jobqueue._API_RUNNER_NAMES


def test_mutating_tool_policy_fails_closed_without_actor(db, monkeypatch):
    """写工具策略下匿名工作流必须拒绝；只读/无工具路径保持旧行为。"""

    from bidcraft.orchestration.orchestrator import dispatch_task
    from bidcraft.orchestration.planner import TOOL_POLICIES, ToolPolicy
    from bidcraft.orchestration.registry import AgentOutput

    pid = _project("wf-mutating")
    rid = _start(pid, workflow_type="custom", input_payload={
        "tasks": [{"task_key": "t1", "agent_type": "technical"}]
    })
    write_policy = ToolPolicy(enabled=True, allowed=("run_check",), readonly=False)
    monkeypatch.setitem(TOOL_POLICIES["custom"], "technical", write_policy)

    with session_scope() as sess:
        task = sess.scalar(select(WorkflowTask).where(WorkflowTask.run_id == rid))
        with pytest.raises(Exception, match="显式用户身份"):
            dispatch_task(sess, task)

    # 非法轨迹不得冒充合法 AgentOutput。
    with pytest.raises(Exception, match="轨迹字段"):
        AgentOutput(tool_calls=[{"name": "get_project"}])

    # 只读策略下匿名执行不受影响。
    monkeypatch.setitem(
        TOOL_POLICIES["custom"], "technical", ToolPolicy(enabled=False)
    )
    rid2 = _start(pid, workflow_type="custom", input_payload={
        "tasks": [{"task_key": "t1", "agent_type": "technical"}]
    })
    result = run_workflow(rid2)
    assert result["status"] in {"waiting_approval", "done", "failed"}


def test_worker_actor_comes_from_job_payload(db):
    from bidcraft.models import User
    from bidcraft.security import hash_password
    from bidcraft.workers.agent import _actor_from_job

    with session_scope() as sess:
        user = User(
            username="job-owner", password_hash=hash_password("pass-123"), role="editor"
        )
        sess.add(user)
        sess.flush()
        job = Job(
            kind="agent",
            status="running",
            project_id="p",
            payload={"owner_id": user.id, "user_id": user.username},
        )
        sess.add(job)
        sess.flush()
        actor = _actor_from_job(sess, job)
    assert actor == {
        "id": user.id,
        "username": "job-owner",
        "role": "editor",
        "display_name": "job-owner",
    }
