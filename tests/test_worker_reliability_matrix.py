"""v0.31 阶段 4：全 Worker 可靠性矩阵。

两条不变量：

1. 8 个 runner 的终态写入全部走 ``commit_fenced_result``（直接或经
   ``finalize_job_result``），把 fencing UPDATE 与业务写入放在同一事务；
2. 终态写入失败（token 失效/丢失）时不得落盘 status/result/event，
   也不得把任务标成 failed —— 由 reaper 重新排队。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from bidcraft import jobqueue
from bidcraft.db import session_scope
from bidcraft.jobqueue import JobAborted, claim_context
from bidcraft.models import Job, JobEvent

TOKEN = "stage4-terminal-token"


def _seed_job(*, kind: str = "agent") -> str:
    with session_scope() as db:
        job = Job(kind=kind, status="running", claim_token=TOKEN)
        db.add(job)
        db.flush()
        return job.id


def _snapshot(job_id: str) -> tuple[str, dict | None, list[str]]:
    with session_scope() as db:
        job = db.get(Job, job_id)
        kinds = list(
            db.scalars(
                select(JobEvent.kind)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.id)
            )
        )
        return str(job.status), job.result, kinds


@pytest.mark.parametrize(
    "module",
    [
        "agent",
        "compliance",
        "evaluation",
        "export",
        "generation",
        "images",
        "knowledge",
        "similarity",
    ],
)
def test_worker_terminal_write_is_fenced(module: str) -> None:
    source = (Path(__file__).parents[1] / "bidcraft" / "workers" / f"{module}.py").read_text(
        encoding="utf-8"
    )
    assert (
        "commit_fenced_result(" in source or "finalize_job_result(" in source
    ), f"{module} worker 未使用事务级 fencing 写终态"
    assert "raise_if_aborted(" in source, f"{module} worker 缺少中止检查点"
    assert "except JobAborted" in source, f"{module} worker 未区分中止与业务失败"


def test_finalize_job_result_persists_status_result_and_event(db) -> None:
    job_id = _seed_job()
    with claim_context(TOKEN):
        jobqueue.finalize_job_result(
            job_id, result={"ok": 1}, message="完成", event_kind="done"
        )
    status, result, events = _snapshot(job_id)
    assert status == "done"
    assert result == {"ok": 1}
    assert events == ["done"]


def test_finalize_job_result_rejects_stale_token_without_partial_write(db) -> None:
    job_id = _seed_job()
    with session_scope() as writer_db:
        writer_db.get(Job, job_id).claim_token = "new-token"
    with claim_context(TOKEN), pytest.raises(JobAborted, match="认领已失效"):
        jobqueue.finalize_job_result(job_id, result={"must": "not persist"})
    status, result, events = _snapshot(job_id)
    assert status == "running"
    assert not result
    assert events == []


def test_finalize_job_result_rejects_missing_claim_without_partial_write(db) -> None:
    job_id = _seed_job()
    with pytest.raises(JobAborted):
        jobqueue.finalize_job_result(job_id, result={"must": "not persist"})
    status, result, events = _snapshot(job_id)
    assert status == "running"
    assert not result
    assert events == []
