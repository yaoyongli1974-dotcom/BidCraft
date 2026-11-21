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
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update

from .clock import utc_now
from .config import settings
from .db import session_scope
from .models import Job

logger = logging.getLogger("bidcraft.jobs")

# Fencing token: every claim gets a fresh token.  A worker may only write job
# state while it still holds that token (a reaper reclaim changes it), so a
# slow worker whose lease expired cannot clobber the new worker's run.
# ContextVar is thread-local: the worker thread sets it for the runner; the
# heartbeat thread gets the token passed explicitly (it does not inherit it).
_claim_token: ContextVar[str] = ContextVar("bidcraft_claim_token", default="")


@contextmanager
def claim_context(token: str) -> Iterator[None]:
    """Bind ``token`` for the current thread and reset it on exit."""

    reset = _claim_token.set(token)
    try:
        yield
    finally:
        _claim_token.reset(reset)


def current_claim_token() -> str:
    return _claim_token.get()


def fenced_job(db, job_id: str) -> Job | None:
    """Return the job only if this worker still owns the current claim.

    Used at every worker write point in place of ``db.get(Job, job_id)``.  When
    the lease was reclaimed the token differs (or is cleared), so the stale
    worker gets ``None`` and skips the write entirely.  Returns ``None`` too
    when called outside a claim context (e.g. directly from a test).
    """

    token = _claim_token.get()
    if not token:
        return None
    job = db.get(Job, job_id)
    if job is None or (job.claim_token or "") != token:
        return None
    return job


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
    compatibility but ignored — the job row is the source of truth.

    In single-process mode (``bidcraft serve``) the API process also executes
    jobs, so submitting lazily starts local workers.  In a separated deployment
    (``BIDCRAFT_EMBEDDED_WORKERS=0``) the API only enqueues; a standalone
    ``bidcraft worker`` process owns the worker threads and reaper.
    """

    if settings.embedded_workers:
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
    # Fresh token on every claim: after a reaper reclaim the old worker's token
    # no longer matches, so all of its subsequent writes are fenced out.
    token = uuid.uuid4().hex
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
            claim_token=token,
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


def _heartbeat(job_id: str, stop: threading.Event, token: str) -> None:
    """Renew the lease only while our claim token is still current.

    ``token`` is passed explicitly because ContextVar does not propagate into
    this thread.  A zero-row update means the lease was reclaimed; stop renewing
    so the new owner's heartbeat is the only one extending it.
    """

    interval = _heartbeat_seconds()
    while not stop.wait(interval):
        now = utc_now()
        lease_until = now + timedelta(seconds=_lease_seconds())
        try:
            with session_scope() as db:
                result = db.execute(
                    update(Job)
                    .where(
                        Job.id == job_id,
                        Job.status == "running",
                        Job.worker_id == _worker_id,
                        Job.claim_token == token,
                    )
                    .values(heartbeat_at=now, lease_expires_at=lease_until)
                )
                if result.rowcount:
                    db.commit()
                else:
                    # Lease no longer ours — stop renewing (the new owner took over).
                    return
        except Exception:  # noqa: BLE001 - heartbeat must never kill the worker
            logger.debug("心跳更新失败 job=%s", job_id, exc_info=True)


def _set_failed(db, job: Job, message: str) -> None:
    from . import jobs

    job.status = "failed"
    job.message = message
    job.result = {"error": message}
    job.claim_token = None
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
    job.claim_token = None
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
    """Startup: requeue only running rows whose lease already expired.

    A multi-worker deployment starts a new process while other workers may be
    actively executing a job.  Requeueing every ``running`` row here would hand
    a live job to a second worker and run it twice (duplicate generation /
    export / charges).  An unexpired lease means another worker still owns the
    job, so we leave it alone and let the reaper reclaim it only if it truly
    dies.  Rows with no lease (legacy) are treated as expired.
    """

    now = utc_now()
    requeued = failed = 0
    with session_scope() as db:
        rows = list(
            db.scalars(
                select(Job).where(
                    Job.status == "running",
                    (Job.lease_expires_at.is_(None)) | (Job.lease_expires_at < now),
                )
            )
        )
        for row in rows:
            if _requeue(db, row, "服务重启且租约已过期，任务重新排队"):
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
        job = fenced_job(db, job_id)
        if job is None or job.status != "running":
            return
        cap = min(_max_attempts(), job.max_attempts or _max_attempts())
        if (job.attempts or 0) < cap:
            _requeue(db, job, "任务意外中断，正在重新排队")
        else:
            _set_failed(db, job, "任务重试次数已用尽")


def _execute(job: Job) -> None:
    # Bind the claimed token for this worker thread so every runner write is
    # fenced.  If the lease was reclaimed mid-run, fenced_job() returns None
    # and the stale worker skips its writes (business work may already be in
    # flight — this is at-least-once, not exactly-once, execution).
    token = job.claim_token or ""
    with claim_context(token):
        runner = _runner_for(job.kind)
        if runner is None:
            logger.error("未知任务类型 %s (job=%s)", job.kind, job.id)
            with session_scope() as db:
                row = fenced_job(db, job.id)
                if row is not None:
                    _set_failed(db, row, f"未知任务类型：{job.kind}")
            return

        args = _rebuild_args(job)
        stop = threading.Event()
        hb = threading.Thread(
            target=_heartbeat,
            args=(job.id, stop, token),
            daemon=True,
            name="bidcraft-hb",
        )
        hb.start()
        try:
            runner(*args)
        except Exception:  # noqa: BLE001 - runner owns its error status; fallback only
            logger.exception("任务执行异常 job=%s kind=%s", job.id, job.kind)
            with session_scope() as db:
                row = fenced_job(db, job.id)
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
    while not _stop.is_set():
        job = claim_next()
        if job is None:
            with _lock:
                _lock.wait(_IDLE_WAIT)
            continue
        try:
            _execute(job)
        except Exception:  # noqa: BLE001 - keep the worker alive no matter what
            logger.exception("worker 处理任务失败 job=%s", job.id)


_reaper_started = False


def _reaper_loop() -> None:
    """Dedicated reaper thread: fixed cadence, independent of worker activity.

    Reaping used to live in the worker's idle branch (``claim_next() is None``).
    A steadily busy queue — or workers all stuck in long-running jobs — could
    starve it forever, leaving expired leases unclaimed.  This loop runs on its
    own schedule whether or not any worker is idle.
    """

    while True:
        try:
            reap_expired()
        except Exception:  # noqa: BLE001 - a DB hiccup must not kill the reaper
            logger.debug("reaper 异常", exc_info=True)
        if _stop.wait(_REAP_INTERVAL):
            break


def _start_reaper() -> None:
    """Start the single reaper thread (idempotent)."""

    global _reaper_started
    with _lock:
        if _reaper_started:
            return
        _reaper_started = True
    threading.Thread(target=_reaper_loop, daemon=True, name="bidcraft-reaper").start()


def _ensure_workers() -> None:
    global _workers_started
    with _lock:
        if _workers_started:
            return
        _stop.clear()
        _workers_started = True
        _start_reaper()
        for _ in range(_max_workers()):
            threading.Thread(target=_worker, daemon=True, name="bidcraft-job").start()


def _reset() -> None:
    """Test helper: stop any running workers and reset the start flag.

    DB rows are the queue now, so there is no in-memory heap to clear.  Tests
    must stop lingering workers (which share the reset engine) before seeding
    jobs, otherwise a worker from a previous test could claim them.
    """

    global _workers_started, _reaper_started
    _stop.set()
    with _lock:
        _workers_started = False
        _reaper_started = False
        _lock.notify_all()
