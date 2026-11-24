"""v0.30 阶段 4：通用 fencing / abort 检查点。

覆盖两条不变量：

1. ``assert_claim_current`` 用新 Session 读取 token，绕过旧 Session 的 identity
   map —— 租约被回收后旧 worker 不能再写终态；
2. 6 个 worker runner 在关闭/租约丢失时以 ``JobAborted`` 退出，既不把 Job 标成
   failed，也不发 ``failed`` 事件（交给 reaper 重新排队）。
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import select

from bidcraft import jobqueue
from bidcraft.db import session_scope
from bidcraft.jobqueue import JobAborted, claim_context, execution_context, fenced_job
from bidcraft.models import Job, JobEvent

TOKEN = "stage4-token"


def _seed_job(*, kind: str = "generate", token: str = TOKEN) -> str:
    with session_scope() as db:
        job = Job(kind=kind, status="running", claim_token=token)
        db.add(job)
        db.flush()
        return job.id


def _status(job_id: str) -> str:
    with session_scope() as db:
        return str(db.get(Job, job_id).status)


def _event_kinds(job_id: str) -> list[str]:
    with session_scope() as db:
        return list(
            db.scalars(
                select(JobEvent.kind)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.id)
            )
        )


# ---------------------------------------------------------------- identity map


def test_assert_claim_current_bypasses_stale_identity_map(db):
    """旧 Session 的 identity map 仍返回旧 token，但新 Session 必须拒绝写入。"""

    job_id = _seed_job(token="old-token")
    abort = threading.Event()
    with execution_context("old-token", abort), session_scope() as stale_db:
        cached = stale_db.get(Job, job_id)
        assert cached is not None and cached.claim_token == "old-token"

        # 另一个 worker 回收租约并提交新 token。
        with session_scope() as reaper_db:
            reaper_db.get(Job, job_id).claim_token = "new-token"

        # 同一个 Session 仍从 identity map 拿到旧对象 —— 证明 fenced_job 会被骗。
        assert stale_db.get(Job, job_id) is cached
        assert cached.claim_token == "old-token"
        assert fenced_job(stale_db, job_id) is cached

        # 新 Session 的 assert_claim_current 必须识破失效的认领。
        with pytest.raises(JobAborted, match="任务认领已失效"):
            jobqueue.assert_claim_current(job_id)


def test_assert_claim_current_accepts_current_token_and_rejects_no_claim(db):
    job_id = _seed_job()
    with claim_context(TOKEN):
        jobqueue.assert_claim_current(job_id)  # 不抛异常
    with pytest.raises(JobAborted):
        jobqueue.assert_claim_current(job_id)  # 没有 claim_context


def test_api_runner_names_include_similarity():
    assert jobqueue._API_RUNNER_NAMES["similarity"] == "_run_similarity_job"


# ---------------------------------------------------------------- 6 个 runner


def _runner_cases():
    from bidcraft.workers.agent import _run_agent_job
    from bidcraft.workers.compliance import _run_batch_check_job
    from bidcraft.workers.evaluation import _run_evaluation_job
    from bidcraft.workers.export import _run_batch_export_job
    from bidcraft.workers.images import _run_image_batch_job
    from bidcraft.workers.knowledge import _run_kb_reindex_job

    return [
        pytest.param(_run_agent_job, ("run-1",), id="agent"),
        pytest.param(_run_batch_check_job, (["project-1"],), id="batch_check"),
        pytest.param(_run_evaluation_job, ("project-1",), id="evaluation"),
        pytest.param(_run_batch_export_job, (["project-1"], "docx"), id="batch_export"),
        pytest.param(
            _run_image_batch_job, ("project-1", [{"label": "配图"}]), id="images"
        ),
        pytest.param(_run_kb_reindex_job, ("kb-1",), id="kb_reindex"),
    ]


@pytest.mark.parametrize("runner,args", _runner_cases())
def test_runners_abort_on_shutdown_without_failure(db, runner, args):
    """全局关闭信号让 runner 立即退出，不写 failed 也不发 failed 事件。"""

    job_id = _seed_job()
    jobqueue.request_shutdown()
    try:
        with claim_context(TOKEN):
            runner(job_id, *args)
        assert _status(job_id) == "running"
        assert "failed" not in _event_kinds(job_id)
    finally:
        jobqueue._shutdown.clear()
        jobqueue._stop.clear()


@pytest.mark.parametrize("runner,args", _runner_cases())
def test_runners_abort_when_claim_token_changed(db, runner, args):
    """租约被回收后，旧 token 下的 runner 不得写终态或发 failed 事件。"""

    job_id = _seed_job(token="old-token")
    with session_scope() as db2:
        db2.get(Job, job_id).claim_token = "new-token"

    with claim_context("old-token"):
        runner(job_id, *args)

    assert _status(job_id) == "running"
    assert "failed" not in _event_kinds(job_id)


def test_batch_check_aborts_between_items(db, monkeypatch):
    """长循环中的 abort 不能被内层 except Exception 当成单项失败吞掉。"""

    from bidcraft.models import Project
    from bidcraft.workers import compliance

    job_id = _seed_job(kind="batch_check")
    with session_scope() as setup_db:
        setup_db.add(Project(id="p1", name="中止测试项目"))
    abort = threading.Event()

    def fake_check(db, project_id):
        abort.set()  # 第一项执行期间租约丢失/收到关闭信号
        return [], {}

    monkeypatch.setattr(compliance, "check_project", fake_check)
    with execution_context(TOKEN, abort):
        compliance._run_batch_check_job(job_id, ["p1", "p2"])

    assert _status(job_id) == "running"
    assert "failed" not in _event_kinds(job_id)
    # 第二项被循环顶部的检查点拦住，未写进度/结果。
    assert "batch_item" not in _event_kinds(job_id)
