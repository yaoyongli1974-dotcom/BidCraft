"""后台任务与导出路由共享的支撑函数与常量。

从 ``api.py`` 下沉而来；这里只依赖底层模块，不 import ``api.py``，
因此依赖方向保持单向：``api → services/workers → 底层模块``。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Self

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import jobs, knowledge, material_policy, model_config, user_docs
from ..config import settings
from ..db import session_scope
from ..generation.agent import ModelClient
from ..models import Job

BATCH_STALE_SECONDS = 2 * 3600
KB_REINDEX_STALE_SECONDS = 2 * 3600
STARTUP_INTERRUPTED_MESSAGE = "服务重启，任务已中断"

logger = logging.getLogger("bidcraft.api")


def _fail_job(db: Session, job: Job, message: str, error: str | None = None) -> bool:
    """Mark a job failed and publish the matching SSE event.

    A charged ``generate`` job is refunded in this same transaction, before the
    failed event is published.  The refund helper is idempotent (claim ledger in
    the DB), so the reaper, startup recovery and a retrying worker can all call
    this without ever refunding twice.  Uncharged job kinds are a no-op.
    """

    from .. import jobqueue

    detail = error or message
    # 终态任务的重复回调只能补做尚未领取的退款，绝不能重复发失败事件。
    was_terminal = job.status in {"done", "failed", "cancelled"}
    if was_terminal and job.refund_claimed:
        return False
    lease_token = str((job.payload or {}).get("generation_lease_token") or "")
    refunded = 0
    refund_error: str | None = None
    try:
        # Savepoint keeps the caller's transaction usable if the refund fails
        # mid-write, so failure bookkeeping still commits in that case.
        with db.begin_nested():
            refunded = int(jobqueue.refund_charged_job(db, job) or 0)
    except Exception as exc:  # noqa: BLE001 - 退款失败不能阻断失败落库与事件
        refund_error = str(exc) or exc.__class__.__name__
        logger.exception("失败任务计费退还失败 job=%s", job.id)

    if not was_terminal:
        # 与 Reaper 的 _set_failed 使用同一条数据库条件状态转换：只有真正抢到
        # running/queued → failed 的一方才写终态并发事件，避免两个并发失败路径
        # 各写一条 failed 事件。clear_claim 同时把 token/租约清空，hung worker
        # 的 fenced_job() 将不再能覆盖这条终态。
        fresh = jobqueue.transition_job(
            db,
            job.id,
            from_statuses=("queued", "running"),
            to_status="failed",
            token=job.claim_token or None,
            message=message,
            result={"error": detail},
            clear_claim=True,
        )
        if fresh is None:
            # We lost the transition to another failure path.  The caller must
            # treat the job as still owned/blocking, never enqueue a duplicate.
            return False
        event_detail: dict[str, Any] = {**jobs.job_payload(fresh), "error": detail}
        if refunded:
            event_detail["refunded_credits"] = refunded
        jobs.publish_event(fresh.id, "failed", event_detail, db=db)
        if refund_error is not None:
            jobs.publish_event(
                fresh.id,
                "refund_failed",
                {
                    "job_id": fresh.id,
                    "error": refund_error,
                    "message": "失败任务计费退还失败，请人工对账",
                },
                db=db,
            )
    if lease_token:
        # A stale auto-fail must not leave the project mutex held until TTL.
        # Release is idempotent and safe when another path already released it.
        jobqueue.release_generation_lease(db, str(job.project_id or ""), lease_token)
    db.flush()
    return True


def _cancel_stale_jobs(
    db: Session,
    kinds: list[str],
    timeout_seconds: int,
    *,
    project_id: str | None = None,
    stale_message: str = "任务超时中断，已允许重新执行",
) -> Job | None:
    """Return the live job for ``kinds``; auto-fail it once it is stale.

    ``project_id=None`` matches project-less batch jobs.  A ``None`` return means
    the caller may enqueue a new job.
    """

    statement = select(Job).where(
        Job.kind.in_(kinds), Job.status.in_(["queued", "running"])
    )
    if project_id is None:
        statement = statement.where(Job.project_id.is_(None))
    else:
        statement = statement.where(Job.project_id == project_id)
    running = db.scalar(statement.order_by(Job.created_at.desc()))
    if running is None:
        return None
    last = running.updated_at or running.created_at
    if last is not None and datetime.now(UTC).replace(tzinfo=None) - last < timedelta(
        seconds=timeout_seconds
    ):
        return running
    won = _fail_job(
        db,
        running,
        stale_message,
        (
            f"任务超过 {timeout_seconds // 3600} 小时无进度，已标记为中断"
            if timeout_seconds >= 3600
            else f"任务超过 {max(1, timeout_seconds // 60)} 分钟无进度，已标记为中断"
        ),
    )
    if not won:
        # Another failure path already took the terminal transition.  Return
        # the fresh row so the caller does not enqueue a second job.
        return db.get(Job, running.id)
    return None


def _active_kb_reindex(db: Session, kb_id: str) -> Job | None:
    """Return a live reindex job for one knowledge base, auto-failing stale ones.

    Reindex jobs are project-less (``project_id=None``), so the generic
    :func:`_cancel_stale_jobs` cannot scope them per KB; the ``payload.kb_id``
    filter is applied in Python because SQLite JSON lookups are not portable.
    """

    rows = list(
        db.scalars(
            select(Job)
            .where(Job.kind == "kb_reindex", Job.status.in_(["queued", "running"]))
            .order_by(Job.created_at.desc())
        )
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    for row in rows:
        if str((row.payload or {}).get("kb_id") or "") != kb_id:
            continue
        last = row.updated_at or row.created_at
        if last is not None and now - last < timedelta(seconds=KB_REINDEX_STALE_SECONDS):
            return row
        _fail_job(
            db,
            row,
            "知识库索引任务超时中断，已允许重新提交",
            f"任务超过 {max(1, KB_REINDEX_STALE_SECONDS // 60)} 分钟无进度，已标记为中断",
        )
    return None


def _recover_interrupted_jobs() -> int:
    """Fail every job that was queued/running when the process last stopped."""

    with session_scope() as db:
        rows = list(
            db.scalars(select(Job).where(Job.status.in_(["queued", "running"])))
        )
        for row in rows:
            _fail_job(db, row, STARTUP_INTERRUPTED_MESSAGE, STARTUP_INTERRUPTED_MESSAGE)
        return len(rows)


def _report_payload(report) -> dict[str, Any]:
    return {
        "written": report.written,
        "fallback": report.fallback,
        "failed": report.failed,
        "suspicious_numbers": report.suspicious,
        "suspicious_count": getattr(report, "suspicious_count", 0),
        "knowledge_downgrades": getattr(report, "knowledge_downgrades", 0),
        "verification": getattr(report, "verification", {}),
        "usage": report.usage,
    }


def _generation_client(db) -> ModelClient:
    endpoint = model_config.resolve_chat_endpoint(db)
    return ModelClient(endpoint if endpoint is not None else settings)


class KnowledgeContext(tuple):
    """生成链检索结果：兼容旧的 ``(text, downgrades)`` 二元组，并携带溯源。

    旧调用方（writer / chapter_plan）通过 ``isinstance(..., tuple)`` 与下标取文本；
    ``__iter__`` 同样只产出 ``(text, downgrades)``。新调用方读 ``refs`` /
    ``diagnostics`` 做逐条审计。
    """

    def __new__(
        cls,
        *,
        text: str,
        downgrades: int,
        refs: list[dict[str, Any]],
        diagnostics: dict[str, Any],
    ) -> Self:
        result = super().__new__(cls, (text, downgrades))
        result.text = text
        result.downgrades = int(downgrades)
        result.refs = list(refs)
        result.diagnostics = dict(diagnostics)
        return result

    def __iter__(self):
        return iter((self.text, self.downgrades))


def _passage_ref(passage, verdict) -> dict[str, Any]:
    """Passage → 真实溯源引用；没有 provider ID 时留空，不编造归属。"""

    return {
        "id": passage.chunk_id or passage.document_id or "",
        "kb": passage.kb,
        "kb_id": passage.kb_id,
        "chunk_id": passage.chunk_id,
        "chunk_index": passage.chunk_index,
        "document_id": passage.document_id,
        "page": passage.page,
        "heading": passage.heading or "",
        "source": passage.source or "",
        "snippet": str(passage.text or "")[:300],
        "score": passage.score,
        "rerank_score": passage.rerank_score,
        "trust_tier": verdict.tier,
        "degraded": bool(verdict.downgraded),
    }


def knowledge_context(
    db,
    query: str,
    *,
    limit: int = 4,
    user_id: str | None = None,
    project_id: str | None = None,
    package_id: str | None = None,
    extra_kb_ids: list[str] | None = None,
) -> KnowledgeContext:
    """Public retrieval entry for generation and downstream agents.

    Returns :class:`KnowledgeContext`: ``(text, downgrades)`` for legacy callers,
    plus ``text`` / ``downgrades`` / ``refs`` / ``diagnostics`` attributes.
    ``project_id`` / ``package_id`` are forwarded to :func:`knowledge.search` so
    scoped knowledge bases are gated fail-closed.
    """

    extra = [str(item) for item in (extra_kb_ids or []) if item]
    if user_id:
        extra.append(user_docs.personal_kb(db, str(user_id)).id)
        try:
            from ..enterprise.assets import enterprise_kb_ids

            extra.extend(enterprise_kb_ids(db, str(user_id)))
        except Exception:  # noqa: BLE001 - 企业库不可用不应阻断生成
            logger.debug("解析企业知识库失败", exc_info=True)

    # 用追加语义：全局已启用企业库照常参与，再纳入当前用户个人库。
    found = knowledge.search(
        db,
        query,
        limit=limit,
        extra_kb_ids=extra or None,
        project_id=project_id,
        package_id=package_id,
        with_diagnostics=True,
    )
    passages = list(found)
    diagnostics = dict(getattr(found, "diagnostics", {}) or {})
    block, downgraded = material_policy.context_block(db, passages)
    verdicts = (
        material_policy.passage_verdicts(db, passages) if passages else []
    )
    refs = [
        _passage_ref(passage, verdict)
        for passage, verdict in zip(passages, verdicts, strict=True)
    ]
    return KnowledgeContext(
        text=block,
        downgrades=downgraded,
        refs=refs,
        diagnostics=diagnostics,
    )


def _knowledge_provider(
    db,
    user_id: str | None = None,
    *,
    project_id: str | None = None,
    package_id: str | None = None,
):
    """Build the generation retrieval callback for one user (and optional scope)."""

    def provide(query: str) -> KnowledgeContext:
        return knowledge_context(
            db,
            query,
            user_id=user_id,
            project_id=project_id,
            package_id=package_id,
        )

    return provide


def _job_refund(db, job: Job, written: int | None = None) -> None:
    """Settle an async ``generate`` charge (thin wrapper over the shared helper).

    ``written`` is optional: when omitted, the authoritative
    ``payload['done_steps']`` ledger is used, so resumed runs and exception
    paths refund only for chapters that were genuinely never persisted.
    """

    from ..jobqueue import refund_charged_job

    refund_charged_job(db, job, written)
