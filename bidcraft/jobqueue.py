"""DB-driven background job queue.

The ``jobs`` table is the single source of truth: a row with ``status='queued'``
*is* the queue.  Workers atomically claim a queued row (a conditional ``UPDATE``
that can only succeed in one process), dispatch it to the registered runner by
``job.kind``, and renew a lease via a heartbeat thread while it runs.  A reaper
reclaims rows whose lease expired (worker died) and either re-queues them or,
after ``max_attempts``, fails them.

This replaces the old in-process min-heap.  Because state lives in the database,
a crash between ``INSERT Job`` and the old in-memory ``submit()`` no longer loses
work, and multiple ``--workers`` / processes never claim the same row twice.

Backwards compatibility: :func:`submit` keeps its old signature so existing
call sites still work, but the ``target``/``args`` are ignored — the runner is
rebuilt from the persisted row.  :func:`position` now counts queued DB rows.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text, update

from .clock import utc_now
from .config import settings
from .db import session_scope
from .models import Job

logger = logging.getLogger("bidcraft.jobs")

_lock = threading.Condition()
_workers_started = False
_worker_id = f"{uuid.uuid4().hex[:12]}"
_stop = threading.Event()

# Reaper cadence (seconds) and idle poll interval when the queue is empty.
_REAP_INTERVAL = 30.0
_IDLE_WAIT = 5.0

# kind -> runner.  Registered lazily by :func:`_runner_for` to avoid a circular
# import (workers import jobs which used to import this module).
_RUNNERS: dict[str, Callable[..., Any]] = {}


def _register(kind: str, runner: Callable[..., Any]) -> None:
    _RUNNERS[kind] = runner


#: kind -> the api-namespace name tests may monkeypatch (lazy runtime lookup keeps
#: monkeypatch semantics — mirrors the routes' ``_core = sys.modules[...]`` pattern).
_API_RUNNER_NAMES = {
    "batch_check": "_run_batch_check_job",
    "batch_export": "_run_batch_export_job",
    "generate": "_run_generation_job",
    "images": "_run_image_batch_job",
    "kb_reindex": "_run_kb_reindex_job",
}


def _runner_for(kind: str) -> Callable[..., Any] | None:
    """Resolve the runner for ``kind``, importing workers lazily on first use."""

    runner = _RUNNERS.get(kind)
    if runner is None:
        try:
            from . import workers  # noqa: F401 - import populates the registry
        except Exception:  # noqa: BLE001 - defensive; workers are always present
            return None
        runner = _RUNNERS.get(kind)
    if runner is None:
        return None
    # 兼容 monkeypatch：测试会替换 api 名字空间里的 runner（如 kb_reindex 重复提交用例），
    # 这里在运行期查找；若被替换（不再是注册的同一对象）则优先用 patch 版本。
    api_name = _API_RUNNER_NAMES.get(kind)
    if api_name:
        core = sys.modules.get("bidcraft.api.app")
        patched = getattr(core, api_name, None) if core is not None else None
        if patched is not None and patched is not runner:
            return patched
    return runner


def _max_workers() -> int:
    try:
        value = int(settings.max_concurrent_jobs)
    except (TypeError, ValueError):
        value = 2
    return max(1, min(8, value))


def _lease_seconds() -> int:
    try:
        return max(30, int(settings.job_lease_seconds))
    except (TypeError, ValueError):
        return 180


def _heartbeat_seconds() -> int:
    try:
        lease = _lease_seconds()
        return max(1, min(int(settings.job_heartbeat_seconds), lease // 2))
    except (TypeError, ValueError):
        return 60


def _max_attempts() -> int:
    try:
        return max(1, int(settings.job_max_attempts))
    except (TypeError, ValueError):
        return 3


def submit(
    target: Callable[..., Any] | None = None,
    args: tuple = (),
    *,
    priority: int = 1,
    job_id: str = "",
) -> None:
    """Wake the worker pool.  ``target``/``args`` are kept for call-site
    compatibility but ignored — the job row is the source of truth."""

    _ensure_workers()
    with _lock:
        _lock.notify_all()


def check_backpressure(db, owner_id: str = "") -> str | None:
    """Return an error message if the queue is full, else ``None``.

    Enforces a global cap and an optional per-user cap on *queued* rows.  Call
    this inside the same transaction that creates the Job so the check and the
    insert are atomic with respect to other writers.
    """

    try:
        global_cap = int(settings.max_queued_jobs)
    except (TypeError, ValueError):
        global_cap = 100
    if global_cap > 0:
        total = db.scalar(
            select(func.count()).select_from(Job).where(Job.status == "queued")
        )
        if int(total or 0) >= global_cap:
            return f"系统排队任务已满（上限 {global_cap}），请稍后再试"
    try:
        user_cap = int(settings.max_user_queued_jobs)
    except (TypeError, ValueError):
        user_cap = 10
    if user_cap > 0 and owner_id:
        rows = db.scalars(
            select(Job.payload).where(Job.status == "queued")
        ).all()
        mine = sum(
            1 for payload in rows if (payload or {}).get("owner_id") == owner_id
        )
        if mine >= user_cap:
            return f"您的排队任务已达上限（{user_cap}），请等待现有任务完成"
    return None


def position(job_id: str) -> int:
    """1-based position among queued rows ordered by (priority, created_at).

    Returns 0 for a running/unknown job.  Opens a short session so callers that
    already hold a ``db`` do not need to thread it through ``job_payload``.
    """

    if not job_id:
        return 0
    with session_scope() as db:
        target = db.get(Job, job_id)
        if target is None or target.status != "queued":
            return 0
        ahead = db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.status == "queued",
                (Job.priority < (target.priority or 0))
                | (
                    (Job.priority == (target.priority or 0))
                    & (Job.created_at < target.created_at)
                ),
            )
        )
        return int(ahead or 0) + 1


def claim_next() -> Job | None:
    """Atomically claim one queued row for this worker.

    The conditional ``UPDATE`` is single-statement atomic in SQLite: even if two
    processes select the same candidate, only the first to commit flips
    ``status`` to ``running``; the second matches zero rows.  Priority (lower
    first) and FIFO break ties.  ``attempts`` is incremented on every claim so
    the reaper can cap retries.
    """

    now = utc_now()
    lease_until = now + timedelta(seconds=_lease_seconds())
    statement = (
        update(Job)
        .where(
            Job.id
            == select(Job.id)
            .where(Job.status == "queued")
            .order_by(Job.priority.asc(), Job.created_at.asc())
            .limit(1)
            .scalar_subquery(),
            Job.status == "queued",
        )
        .values(
            status="running",
            claimed_at=now,
            lease_expires_at=lease_until,
            heartbeat_at=now,
            worker_id=_worker_id,
            attempts=Job.attempts + 1,
        )
        .returning(Job.id)
    )
    with session_scope() as db:
        row = db.execute(statement).first()
        if row is None:
            return None
        claimed = db.get(Job, row[0])
        if claimed is None:
            return None
        db.expunge(claimed)
        return claimed


def _heartbeat(job_id: str, stop: threading.Event) -> None:
    interval = _heartbeat_seconds()
    while not stop.wait(interval):
        now = utc_now()
        lease_until = now + timedelta(seconds=_lease_seconds())
        try:
            with session_scope() as db:
                result = db.execute(
                    text(
                        "UPDATE jobs SET heartbeat_at=:now, lease_expires_at=:lease "
                        "WHERE id=:id AND status='running' AND worker_id=:worker"
                    ),
                    {
                        "now": now,
                        "lease": lease_until,
                        "id": job_id,
                        "worker": _worker_id,
                    },
                )
                if result.rowcount:
                    db.commit()
        except Exception:  # noqa: BLE001 - heartbeat must never kill the worker
            logger.debug("心跳更新失败 job=%s", job_id, exc_info=True)


def _set_failed(db, job: Job, message: str) -> None:
    from . import jobs

    job.status = "failed"
    job.message = message
    job.result = {"error": message}
    jobs.publish_event(
        job.id, "failed", {**jobs.job_payload(job), "error": message}, db=db
    )


def _requeue(db, job: Job, message: str) -> bool:
    """Reset a claimed/expired job to queued.  Returns True if re-queued."""

    from . import jobs

    cap = min(_max_attempts(), job.max_attempts or _max_attempts())
    if (job.attempts or 0) >= cap:
        _set_failed(db, job, message)
        return False
    job.status = "queued"
    job.message = message
    job.claimed_at = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    job.worker_id = None
    jobs.publish_event(job.id, "queued", jobs.job_payload(job), db=db)
    return True


def reap_expired() -> tuple[int, int]:
    """Requeue running jobs whose lease expired; fail those past retry limit.

    Returns ``(requeued, failed)`` counts.
    """

    now = utc_now()
    requeued = failed = 0
    with session_scope() as db:
        rows = list(
            db.scalars(
                select(Job).where(
                    Job.status == "running",
                    (Job.lease_expires_at.is_(None))
                    | (Job.lease_expires_at < now),
                )
            )
        )
        for row in rows:
            if _requeue(db, row, "任务租约已过期，正在重新排队"):
                requeued += 1
            else:
                failed += 1
    return requeued, failed


def recover_on_startup() -> tuple[int, int]:
    """Startup hook: queued rows stay queued; orphaned running rows are re-queued.

    Replaces the old "fail every queued/running job" behaviour.  On startup this
    process has no workers running any job, so every ``running`` row is orphaned
    and re-queued regardless of lease (the previous process died).  Workers are
    then started so re-queued rows are picked up immediately.
    """

    requeued = failed = 0
    with session_scope() as db:
        rows = list(db.scalars(select(Job).where(Job.status == "running")))
        for row in rows:
            if _requeue(db, row, "服务重启，任务重新排队"):
                requeued += 1
            else:
                failed += 1
    # Worker startup is deliberately separate: create_app() runs in every test,
    # and starting poll threads here would race with queued-job assertions.  The
    # real server entry point (CLI `serve`) calls start_workers() explicitly.
    return requeued, failed


def start_workers() -> None:
    """Start the worker pool.  Called by the real server entry point only."""

    _ensure_workers()


def _finalize_after_run(job_id: str) -> None:
    """Safety net for a run that returned while still ``running``.

    Runners own their terminal state: they set ``done`` on success and ``failed``
    on a deliberate business error (e.g. missing ledger facts).  Those are left
    alone — retrying a deterministic failure would just spam events.  Only a job
    left in ``running`` (unexpected early return) is treated as interrupted and
    re-queued with the attempts cap, mirroring the reaper's crash recovery.
    """

    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None or job.status != "running":
            return
        cap = min(_max_attempts(), job.max_attempts or _max_attempts())
        if (job.attempts or 0) < cap:
            _requeue(db, job, "任务意外中断，正在重新排队")
        else:
            _set_failed(db, job, "任务重试次数已用尽")


def _execute(job: Job) -> None:
    runner = _runner_for(job.kind)
    if runner is None:
        logger.error("未知任务类型 %s (job=%s)", job.kind, job.id)
        with session_scope() as db:
            row = db.get(Job, job.id)
            if row is not None:
                _set_failed(db, row, f"未知任务类型：{job.kind}")
        return

    args = _rebuild_args(job)
    stop = threading.Event()
    hb = threading.Thread(target=_heartbeat, args=(job.id, stop), daemon=True, name="bidcraft-hb")
    hb.start()
    try:
        runner(*args)
    except Exception:  # noqa: BLE001 - runner owns its error status; fallback only
        logger.exception("任务执行异常 job=%s kind=%s", job.id, job.kind)
        with session_scope() as db:
            row = db.get(Job, job.id)
            if row is not None and row.status not in {"done", "failed", "cancelled"}:
                _set_failed(db, row, "任务执行异常")
    finally:
        stop.set()
    _finalize_after_run(job.id)


def _rebuild_args(job: Job) -> tuple:
    """Rebuild a runner's positional args from the persisted row."""

    payload = dict(job.payload or {})
    kind = job.kind
    project_id = job.project_id or ""
    if kind == "batch_check":
        return (job.id, list(payload.get("ids") or []))
    if kind == "batch_export":
        return (job.id, list(payload.get("ids") or []), payload.get("format") or "docx")
    if kind == "images":
        return (job.id, project_id, list(payload.get("targets") or []))
    if kind == "generate":
        return (job.id, project_id, bool(payload.get("force")))
    if kind == "kb_reindex":
        return (job.id, payload.get("kb_id") or "")
    return (job.id,)


def _worker() -> None:
    last_reap = time.monotonic()
    while not _stop.is_set():
        job = claim_next()
        if job is None:
            with _lock:
                _lock.wait(_IDLE_WAIT)
            if _stop.is_set():
                break
            now_mono = time.monotonic()
            if now_mono - last_reap >= _REAP_INTERVAL:
                last_reap = now_mono
                try:
                    reap_expired()
                except Exception:  # noqa: BLE001
                    logger.debug("reaper 异常", exc_info=True)
            continue
        try:
            _execute(job)
        except Exception:  # noqa: BLE001 - keep the worker alive no matter what
            logger.exception("worker 处理任务失败 job=%s", job.id)


def _ensure_workers() -> None:
    global _workers_started
    with _lock:
        if _workers_started:
            return
        _stop.clear()
        _workers_started = True
        for _ in range(_max_workers()):
            threading.Thread(target=_worker, daemon=True, name="bidcraft-job").start()


def _reset() -> None:
    """Test helper: stop any running workers and reset the start flag.

    DB rows are the queue now, so there is no in-memory heap to clear.  Tests
    must stop lingering workers (which share the reset engine) before seeding
    jobs, otherwise a worker from a previous test could claim them.
    """

    global _workers_started
    _stop.set()
    with _lock:
        _workers_started = False
        _lock.notify_all()
