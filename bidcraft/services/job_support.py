"""后台任务与导出路由共享的支撑函数与常量。

从 ``api.py`` 下沉而来；这里只依赖底层模块，不 import ``api.py``，
因此依赖方向保持单向：``api → services/workers → 底层模块``。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

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


def _fail_job(db: Session, job: Job, message: str, error: str | None = None) -> None:
    """Mark a job failed and publish the matching SSE event."""

    detail = error or message
    job.status = "failed"
    job.message = message
    job.result = {"error": detail}
    jobs.publish_event(job.id, "failed", {**jobs.job_payload(job), "error": detail}, db=db)
    db.flush()


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
    _fail_job(
        db,
        running,
        stale_message,
        (
            f"任务超过 {timeout_seconds // 3600} 小时无进度，已标记为中断"
            if timeout_seconds >= 3600
            else f"任务超过 {max(1, timeout_seconds // 60)} 分钟无进度，已标记为中断"
        ),
    )
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


def _knowledge_provider(db, user_id: str | None = None):
    """Build the generation retrieval callback for one user.

    Global knowledge bases stay available to everyone; the caller's personal
    knowledge base is added explicitly (and therefore included even though
    :func:`knowledge.search` otherwise skips personal bases).
    """

    extra_kb_ids: list[str] = []
    if user_id:
        extra_kb_ids.append(user_docs.personal_kb(db, str(user_id)).id)
        try:
            from ..enterprise.assets import enterprise_kb_ids

            extra_kb_ids.extend(enterprise_kb_ids(db, str(user_id)))
        except Exception:  # noqa: BLE001 - 企业库不可用不应阻断生成
            logger.debug("解析企业知识库失败", exc_info=True)

    def provide(query: str) -> str:
        # 用追加语义：全局已启用企业库照常参与，再纳入当前用户个人库。
        # 旧写法把个人库 id 塞进 kb_ids（排他）导致全局库被排除，与注释矛盾。
        passages = knowledge.search(
            db,
            query,
            limit=4,
            extra_kb_ids=extra_kb_ids or None,
        )
        block, downgraded = material_policy.context_block(db, passages)
        # Forward the advisory downgrade count to the writer so it reaches the
        # generation report instead of being silently discarded.
        return block, downgraded

    return provide


def _job_refund(db, job: Job, written: int | None = None) -> None:
    """Settle an async ``generate`` charge (thin wrapper over the shared helper).

    ``written`` is optional: when omitted, the authoritative
    ``payload['done_steps']`` ledger is used, so resumed runs and exception
    paths refund only for chapters that were genuinely never persisted.
    """

    from ..jobqueue import refund_charged_job

    refund_charged_job(db, job, written)
