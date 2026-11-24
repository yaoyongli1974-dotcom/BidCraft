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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update

from . import billing
from .clock import utc_now
from .config import settings
from .db import session_scope
from .models import Job, PendingCharge, Section, User

logger = logging.getLogger("bidcraft.jobs")

# 背压 check + insert 的跨进程串行化（仅 PostgreSQL；SQLite 单写者天然串行）。
_BACKPRESSURE_LOCK_KEY = 0x42494352514C4F43  # "BICRQLC"
# 未结扣费的 TTL 由 settings.pending_charge_ttl_seconds 配置（BIDCRAFT_PENDING_CHARGE_TTL，
# 默认 1800 秒）；启动恢复、reaper 周期扫描与 CLI 对账共用同一策略。

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


class JobAborted(Exception):
    """Runner stopped because its lease was lost or shutdown was requested.

    Raised by the abort guard the generation runner installs at chapter
    boundaries.  It is *not* a business failure: ``_execute`` leaves the job in
    ``running`` so the reaper/requeue path returns it to ``queued`` and another
    worker resumes from the idempotency ledger (no ``failed`` status, no refund
    bookkeeping on an interrupted run).
    """


# Per-execution abort signal.  The heartbeat thread sets it when the lease was
# reclaimed; SIGTERM/SIGINT (via request_shutdown) makes workers check the global
# event.  ContextVar keeps it scoped to the executing worker thread.
_abort_event: ContextVar[threading.Event | None] = ContextVar(
    "bidcraft_abort_event", default=None
)
_shutdown = threading.Event()


def job_abort_event() -> threading.Event | None:
    return _abort_event.get()


def request_shutdown() -> None:
    """Ask all workers to stop claiming and let in-flight jobs wind down."""

    _shutdown.set()
    _stop.set()
    with _lock:
        _lock.notify_all()


def is_shutdown_requested() -> bool:
    return _shutdown.is_set()


def workers_running() -> bool:
    """Whether an in-process worker pool was started (vs API-only deployment)."""

    return _workers_started


@contextmanager
def execution_context(token: str, abort: threading.Event) -> Iterator[None]:
    """Bind both fencing token and abort signal for a runner thread."""

    token_reset = _claim_token.set(token)
    abort_reset = _abort_event.set(abort)
    try:
        yield
    finally:
        _abort_event.reset(abort_reset)
        _claim_token.reset(token_reset)


def raise_if_aborted() -> None:
    """Checkpoint helper for runners: bail out the moment the lease is gone."""

    abort = _abort_event.get()
    if (abort is not None and abort.is_set()) or _shutdown.is_set():
        raise JobAborted("租约失效或服务正在关闭")


def step_status(db, job_id: str, step_key: str) -> bool | None:
    """Idempotency ledger lookup for one unit of work inside a job.

    Completed step keys live in ``Job.payload['done_steps']`` and survive
    requeues/restarts, so a resumed worker skips work already finished by an
    earlier attempt.  Returns ``True`` (done → skip), ``False`` (todo → run), or
    ``None`` if this worker no longer owns the claim (→ abort).
    """

    job = fenced_job(db, job_id)
    if job is None:
        return None
    done = (job.payload or {}).get("done_steps") or []
    return step_key in done


def mark_step_done(db, job_id: str, step_key: str, token: str) -> None:
    """Record one completed unit; only the current token holder may write."""

    job = fenced_job(db, job_id)
    if job is None or (job.claim_token or "") != token:
        return
    payload = dict(job.payload or {})
    done = list(payload.get("done_steps") or [])
    if step_key not in done:
        done.append(step_key)
        payload["done_steps"] = done
        job.payload = payload
        db.flush()


_lock = threading.Condition()
_workers_started = False
_worker_id = f"{uuid.uuid4().hex[:12]}"
_stop = threading.Event()

# Reaper cadence (seconds) and idle poll interval when the queue is empty.
_REAP_INTERVAL = 30.0
_IDLE_WAIT = 5.0
# 未结扣费的周期对账间隔：复用 reaper 线程，独立计时以免每次 reap 都扫全表。
_RECONCILE_INTERVAL = 300.0

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
    "agent": "_run_agent_job",
    "evaluation": "_run_evaluation_job",
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

    # 事务级咨询锁覆盖全局与单用户两道计数：调用方在同一事务里 INSERT Job，
    # 锁持有到提交，杜绝并发检查都通过后再一起入队（PG 专用，SQLite 单写者）。
    _lock_backpressure(db)
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
        # 归一化列（0009 迁移回填）让 COUNT 走索引；PostgreSQL 的 payload 是
        # JSON 而非 JSONB，旧版 payload['owner_id'] 过滤无法用索引。
        mine = db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.status == "queued",
                Job.owner_id == owner_id,
            )
        )
        if int(mine or 0) >= user_cap:
            return f"您的排队任务已达上限（{user_cap}），请等待现有任务完成"
    return None


def _lock_backpressure(db) -> None:
    """Hold a transaction-scoped advisory lock across count+insert on PG."""

    if db.get_bind().dialect.name != "postgresql":
        return
    from sqlalchemy import text

    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _BACKPRESSURE_LOCK_KEY})


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


def _claim_statement(now, lease_until, token: str):
    """构造原子认领语句；独立出来便于跨方言编译校验。

    外层带 ``status='queued'`` 是并发正确性的关键：PostgreSQL 下两个事务
    若选出同一行，后提交者会在 EvalPlanQual 重检时因状态已变而更新 0 行。
    """

    return (
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
    statement = _claim_statement(now, lease_until, token)
    with session_scope() as db:
        row = db.execute(statement).first()
        if row is None:
            return None
        claimed = db.get(Job, row[0])
        if claimed is None:
            return None
        db.expunge(claimed)
        return claimed


def _heartbeat(
    job_id: str,
    stop: threading.Event,
    token: str,
    abort: threading.Event | None = None,
) -> None:
    """Renew the lease only while our claim token is still current.

    ``token``/``abort`` are passed explicitly because ContextVar does not
    propagate into this thread.  A zero-row update means the lease was reclaimed;
    stop renewing *and* set the abort signal so the runner stops at its next
    checkpoint instead of continuing to burn model calls into the void.
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
                    # Lease no longer ours — stop renewing and tell the runner to
                    # abort at its next checkpoint (the new owner took over).
                    if abort is not None:
                        abort.set()
                    return
        except Exception:  # noqa: BLE001 - heartbeat must never kill the worker
            logger.debug("心跳更新失败 job=%s", job_id, exc_info=True)


def _set_failed(db, job: Job, message: str) -> None:
    from . import jobs

    # A worker that dies between claim and result never reaches its own exception
    # handler, so a charged ``generate`` job would be failed with the debit left
    # stranded.  Settle it here from the idempotency ledger — no-op when the job
    # was never charged or was already refunded (reaper/startup paths included).
    try:
        refund_charged_job(db, job)
    except Exception:  # noqa: BLE001 - never block failure bookkeeping
        logger.exception("失败任务计费退还失败 job=%s", job.id)
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
    # 同时收口「已扣费但生成中断」的残留 marker（CLI serve/worker 都会调本函数）。
    try:
        reconcile_pending_charges()
    except Exception:  # noqa: BLE001 - 对账失败不能阻断启动，下次启动会重试
        logger.exception("未结章节计费对账失败，将在下次启动时重试")
    return requeued, failed


def begin_pending_charge(
    db,
    user: User | None,
    project_id: str,
    credits: int,
    chapters: int,
    *,
    note: str = "",
) -> str:
    """Record a just-committed debit in the SAME transaction as the debit.

    Returns the marker id; empty when nothing was charged (billing-exempt user
    or zero chapters), in which case the settle helper is a no-op.
    """

    if not credits or user is None or not getattr(user, "id", None):
        return ""
    marker = PendingCharge(
        user_id=str(user.id),
        # The debit hit the parent account for sub-accounts; record it so the
        # refund can still find the payer after the sub-account is deleted.
        billing_account_id=str(getattr(user, "parent_id", None) or user.id),
        project_id=str(project_id or ""),
        credits=int(credits),
        chapters=max(1, int(chapters or 1)),
        note=note[:500],
        status="pending",
    )
    db.add(marker)
    db.flush()
    return marker.id


def _refund_for(marker: PendingCharge, written: int) -> int:
    """Refund every credit not earned by the chapters actually written.

    A VIP/discounted charge is often not divisible by ``chapters`` (e.g. 5 credits
    for 3 chapters).  The old ``(credits // chapters) * unwritten`` formula kept
    the rounding remainder and under-refunded partial failures.  Charge for the
    written chapters is ``ceil(credits * written / chapters)``, so the refund is
    ``credits - ceil(...)``; integer arithmetic avoids float rounding surprises.
    """

    total = max(1, int(marker.chapters or 1))
    done = max(0, min(int(written or 0), total))
    credits = int(marker.credits or 0)
    spent = (credits * done + total - 1) // total  # ceil(credits * done / total)
    return max(0, credits - spent)


def _chapter_done_steps(payload: dict | None) -> int | None:
    """Count chapters recorded in the idempotency ledger, or ``None`` if absent.

    ``done_steps`` is the authoritative record of work that actually committed:
    the generation worker appends ``chapter:<node_id>`` only *after* the Section
    write is flushed.  Counting those keys (instead of trusting a per-attempt
    report) avoids both a full refund after a mid-run exception (earlier chapters
    already persisted) and an over-refund on resume (earlier chapters skipped).
    """

    done = (payload or {}).get("done_steps")
    if not isinstance(done, (list, tuple)):
        return None
    return sum(1 for key in done if isinstance(key, str) and key.startswith("chapter:"))


def _persisted_sections(db, project_id: str | None) -> int:
    """Legacy fallback: chapters with a persisted, non-empty Section body."""

    if not project_id:
        return 0
    return int(
        db.scalar(
            select(func.count())
            .select_from(Section)
            .where(Section.project_id == project_id, Section.body != "")
        )
        or 0
    )


def refund_charged_job(db, job: Job, written: int | None = None) -> int:
    """Idempotently refund unearned credits of a charged ``generate`` job.

    ``payload['done_steps']`` is the authoritative count of chapters actually
    persisted; an explicit ``written`` is only honored when the ledger is absent
    (legacy rows), with persisted Sections as a final fallback.  The
    ``payload['refunded']`` flag makes repeated calls no-ops, so worker handlers,
    the reaper and startup recovery can all call this safely.

    Returns the number of credits refunded (0 when nothing was due).
    """

    payload = dict(job.payload or {})
    charged = int(payload.get("charged") or 0)
    chapters = int(payload.get("chapters") or 0)
    if not charged or not chapters or payload.get("refunded"):
        return 0
    done = _chapter_done_steps(payload)
    if done is not None:
        written = done
    elif written is None:
        written = _persisted_sections(db, job.project_id)
    written = max(0, min(int(written or 0), chapters))
    refund = (chapters - written) * charged // chapters
    if refund <= 0:
        return 0
    user = db.get(User, str(payload.get("user_id") or ""))
    if user is None:
        return 0
    billing.refund_chapters(db, user, refund)
    payload["refunded"] = True
    payload["refunded_credits"] = refund
    job.payload = payload
    db.flush()
    return refund


def _marker_payee(db, marker: PendingCharge, user: User | None = None) -> User | None:
    """Resolve the account that must receive a marker's refund.

    A sub-account's charge hits its parent (``billing.billing_account``), so
    :func:`begin_pending_charge` records that paying account on the marker.
    Legacy markers fall back to ``user_id`` and then to the caller's user.
    ``None`` means no payable account exists: callers must leave the marker
    pending rather than silently dropping the refund.
    """

    account_id = getattr(marker, "billing_account_id", None) or marker.user_id
    account = db.get(User, str(account_id)) if account_id else None
    if account is None and user is not None and getattr(user, "id", None):
        account = db.get(User, str(user.id))
    return account


def settle_pending_charge(
    db,
    marker_id: str,
    user: User | None,
    project_id: str,
    chapters: int,
    written: int,
) -> int:
    """Clear a marker and refund the unwritten chapters, atomically & idempotently.

    A conditional ``UPDATE ... WHERE status='pending'`` claims the row before any
    refund is issued, mirroring :func:`reconcile_pending_charges`.  Two settles —
    or a settle racing the TTL reconciler — can therefore never both refund.
    Returns the number of credits refunded; repeated calls are no-ops.
    """

    if not marker_id:
        return 0
    marker = db.get(PendingCharge, marker_id)
    if marker is None:
        return 0
    refund = _refund_for(marker, written)
    payee = _marker_payee(db, marker, user)
    if refund > 0 and payee is None:
        # No payable account (e.g. deleted sub-account): leave it pending for a
        # later retry instead of clearing the marker with a zero refund.
        return 0
    # Claim first; the refund and the status change share this transaction.
    claimed = db.execute(
        update(PendingCharge)
        .where(PendingCharge.id == marker_id, PendingCharge.status == "pending")
        .values(status="processing", updated_at=utc_now())
    )
    if not claimed.rowcount:
        return 0
    if refund > 0:
        billing.refund_chapters(db, payee, refund, project_id=str(project_id or ""))
    marker.status = "refunded" if refund > 0 else "cleared"
    marker.refunded_credits = refund
    marker.updated_at = utc_now()
    db.flush()
    return refund


def _project_has_live_job(db, project_id: str | None) -> bool:
    """Whether a project still has a queued/running job doing work for it.

    A marker can outlive its TTL while a long generation is still in flight.
    Refunding it then would let the work finish for free.  Tying liveness to
    the job row (rather than the wall-clock age alone) closes that window.
    """

    if not project_id:
        return False
    return bool(
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.project_id == project_id,
                Job.status.in_(["queued", "running"]),
            )
        )
    )


def reconcile_pending_charges(
    *, older_than_seconds: int | None = None, db=None
) -> int:
    """Refund charge markers left ``pending`` by a crash between debit and result.

    The marker is created in the debit transaction (see :func:`begin_pending_charge`)
    and cleared/refunded in the same transaction as the generation result.  Only
    a process death (OOM/restart) can leave it pending; this pass settles those
    rows after a TTL, **and only when the project has no live job** — a long
    generation must never be refunded by mistake while it is still running.
    The async ``generate_all`` path is already durable: its charge and Job row
    commit together and the worker refunds via ``payload['charged']``; it needs
    no marker here.

    Called automatically from :func:`recover_on_startup` (used by `serve`/`worker`),
    periodically from the worker reaper, and explicitly from maintenance jobs::

        bidcraft reconcile-charges                    # CLI, uses configured TTL

        from bidcraft.jobqueue import reconcile_pending_charges
        refunded_markers = reconcile_pending_charges()

    Returns the number of markers settled.  A transaction-scoped conditional
    UPDATE claims each row, so concurrent instances never refund twice.  Markers
    with no payable account are deliberately left ``pending`` for a later retry.
    """

    ttl = older_than_seconds
    if ttl is None:
        ttl = int(settings.pending_charge_ttl_seconds)
    cutoff = utc_now() - timedelta(seconds=max(0, ttl))

    if db is None:
        with session_scope() as own_db:
            return reconcile_pending_charges(older_than_seconds=ttl, db=own_db)

    rows = list(
        db.scalars(
            select(PendingCharge).where(
                PendingCharge.status == "pending",
                PendingCharge.created_at < cutoff,
            )
        )
    )
    settled = 0
    for marker in rows:
        # A live generation on the same project owns this charge; leave it alone.
        if _project_has_live_job(db, marker.project_id):
            continue
        refund = _refund_for(marker, 0)
        payee = _marker_payee(db, marker)
        if refund > 0 and payee is None:
            # No payable account — refuse to clear with a zero refund.
            continue
        # Claim first; the refund and the status change share this transaction.
        claimed = db.execute(
            update(PendingCharge)
            .where(PendingCharge.id == marker.id, PendingCharge.status == "pending")
            .values(status="processing", updated_at=utc_now())
        )
        if not claimed.rowcount:
            continue
        if refund > 0:
            billing.refund_chapters(db, payee, refund, project_id=marker.project_id)
        marker.status = "refunded"
        marker.refunded_credits = refund
        marker.updated_at = utc_now()
        settled += 1
    db.flush()
    return settled


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
    # Bind the fencing token AND an abort signal for this worker thread.  Every
    # runner write is fenced (a reclaimed lease makes fenced_job return None),
    # and the abort signal is set by the heartbeat when the lease is lost or by
    # request_shutdown() on SIGTERM — runners that install the guard stop at
    # their next checkpoint.  Idempotency is completed by the per-unit ledger
    # (step_status/mark_step_done): a resumed run skips units already done.
    token = job.claim_token or ""
    abort = threading.Event()
    with execution_context(token, abort):
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
            args=(job.id, stop, token, abort),
            daemon=True,
            name="bidcraft-hb",
        )
        hb.start()
        try:
            runner(*args)
        except JobAborted:
            # Lease lost or graceful shutdown: leave status 'running'.  The
            # reaper (or restart recovery) returns it to queued, and the new
            # attempt resumes from done_steps.  Never mark this as failed.
            logger.info("任务已中止，等待重新排队 job=%s", job.id)
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
    if kind == "agent":
        return (job.id, payload.get("run_id") or "")
    if kind == "evaluation":
        return (
            job.id,
            project_id,
            str(payload.get("note") or ""),
            str(payload.get("created_by") or ""),
        )
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
    own schedule whether or not any worker is idle.  It also sweeps crash-window
    pending charges on a slower cadence: startup recovery alone cannot catch a
    crash that happens days after a long-lived worker started.
    """

    next_reconcile = 0.0  # 首次 tick 就做一次；此后按固定间隔，失败下次再试。
    while True:
        try:
            reap_expired()
        except Exception:  # noqa: BLE001 - a DB hiccup must not kill the reaper
            logger.debug("reaper 异常", exc_info=True)
        now = time.monotonic()
        if now >= next_reconcile:
            try:
                # 不额外加锁：reconcile 自身用行级条件 UPDATE 抢占，且 TTL 保证
                # 不会误退正在执行的生成；失败不影响 reaper 的租约回收。
                reconcile_pending_charges()
            except Exception:  # noqa: BLE001 - 对账失败不能停掉 reaper
                logger.debug("未结章节计费周期对账失败，将在下个周期重试", exc_info=True)
            next_reconcile = now + _RECONCILE_INTERVAL
        if _stop.wait(_REAP_INTERVAL):
            break


_worker_threads: list[threading.Thread] = []
_reaper_thread: threading.Thread | None = None


def _start_reaper() -> None:
    """Start the single reaper thread (idempotent)."""

    global _reaper_started, _reaper_thread
    with _lock:
        if _reaper_started:
            return
        _reaper_started = True
    _reaper_thread = threading.Thread(
        target=_reaper_loop, daemon=True, name="bidcraft-reaper"
    )
    _reaper_thread.start()


def _ensure_workers() -> None:
    global _workers_started
    with _lock:
        if _workers_started:
            return
        # A shutdown request is terminal; never resurrect workers after one.
        if not _shutdown.is_set():
            _stop.clear()
        _workers_started = True
        _start_reaper()
        for _ in range(_max_workers()):
            thread = threading.Thread(target=_worker, daemon=True, name="bidcraft-job")
            _worker_threads.append(thread)
            thread.start()


def wait_for_workers(timeout: float) -> None:
    """Join worker/reaper threads after a shutdown request (bounded drain).

    In-flight runners see the abort signal at their next checkpoint and return
    quickly; ``timeout`` is an upper bound so shutdown never hangs forever.
    """

    deadline = time.monotonic() + timeout
    threads = list(_worker_threads)
    if _reaper_thread is not None:
        threads.append(_reaper_thread)
    for thread in threads:
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(remaining)


def _reset() -> None:
    """Test helper: stop any running workers and reset the start flag.

    DB rows are the queue now, so there is no in-memory heap to clear.  Tests
    must stop lingering workers (which share the reset engine) before seeding
    jobs, otherwise a worker from a previous test could claim them.
    """

    global _workers_started, _reaper_started, _reaper_thread
    _shutdown.clear()
    _stop.set()
    with _lock:
        _workers_started = False
        _reaper_started = False
        _worker_threads.clear()
        _reaper_thread = None
        _lock.notify_all()
