"""v0.31.1 阶段 4：动态 runner 矩阵（注册发现 + 中止不写终态）。"""

from __future__ import annotations

import inspect
import threading

import pytest
from sqlalchemy import select

# 导入即注册；只取生产 runner，不依赖测试里临时 monkeypatch 的 api 命名空间。
import bidcraft.workers  # noqa: F401
from bidcraft import jobqueue
from bidcraft.db import session_scope
from bidcraft.jobqueue import JobAborted, execution_context
from bidcraft.models import Job, JobEvent

TOKEN = "dynamic-matrix-token"

ARGS = {
    "agent": ("run-1",),
    "batch_check": ([],),
    "batch_export": ([], "xlsx"),
    "evaluation": ("", "", ""),
    "generate": ("", False),
    "images": ("", []),
    "kb_reindex": ("kb-1",),
    "similarity": (),
}


def _seed_job(kind: str) -> str:
    with session_scope() as db:
        job = Job(kind=kind, status="running", claim_token=TOKEN)
        db.add(job)
        db.flush()
        return job.id


def _snapshot(job_id: str) -> tuple[str, list[str]]:
    with session_scope() as db:
        return (
            str(db.get(Job, job_id).status),
            list(
                db.scalars(
                    select(JobEvent.kind).where(JobEvent.job_id == job_id)
                )
            ),
        )


@pytest.mark.parametrize("kind", sorted(jobqueue._API_RUNNER_NAMES))
def test_registered_runner_has_fence_and_abort_checkpoint(kind: str) -> None:
    runner = jobqueue._RUNNERS.get(kind)
    assert runner is not None, f"{kind} 未注册 runner"
    source = inspect.getsource(runner)
    module_source = inspect.getsource(inspect.getmodule(runner))
    assert "raise_if_aborted(" in source, f"{kind} 缺少入口 abort 检查点"
    assert (
        "commit_fenced_result(" in module_source
        or "finalize_job_result(" in module_source
        or "fenced_chapter_write(" in module_source
    ), f"{kind} 终态/章节写入未使用事务级 fencing"


@pytest.mark.parametrize("kind", sorted(jobqueue._API_RUNNER_NAMES))
def test_aborted_runner_never_writes_terminal_state(kind: str) -> None:
    job_id = _seed_job(kind)
    abort = threading.Event()
    abort.set()
    with execution_context(TOKEN, abort, job_id=job_id, kind=kind):
        try:
            jobqueue._RUNNERS[kind](job_id, *ARGS[kind])
        except JobAborted:
            # 有些 runner 直接向外传播中止信号；另一些在内部捕获后返回。
            pass
    assert _snapshot(job_id) == ("running", [])
