"""Background-job progress events and Server-Sent Events helpers.

The browser used to poll ``GET /jobs/{id}``.  Generation now also writes an
append-only :class:`JobEvent` stream.  ``GET /jobs/{id}/events`` replays events
after ``Last-Event-ID`` and then tails the table, so a dropped connection can
resume without losing chapter boundaries.  Persisting events keeps the design
single-process and dependency-free while still surviving a browser refresh.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import jobqueue
from .db import session_scope
from .models import Job, JobEvent

TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def job_payload(job: Job) -> dict[str, Any]:
    """Stable JSON shape shared by the polling and SSE endpoints."""

    return {
        "id": job.id,
        "project_id": job.project_id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "payload": job.payload or {},
        "result": job.result or {},
        "error": (job.result or {}).get("error", ""),
        "priority": getattr(job, "priority", 1),
        "queue_position": jobqueue.position(job.id) if job.status == "queued" else 0,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
    }


def publish_event(
    job_id: str,
    kind: str,
    data: dict[str, Any] | None = None,
    *,
    db: Session | None = None,
) -> int:
    """Append one event and return its id.

    Pass ``db`` when the caller already owns a transaction (for example the
    ``queued`` event created with the job row).  Otherwise a short-lived session
    is opened so the generation worker does not hold a write transaction while
    the model is streaming.
    """

    payload = data or {}
    if db is not None:
        event = JobEvent(job_id=job_id, kind=kind, data=payload)
        db.add(event)
        db.flush()
        return int(event.id)
    with session_scope() as session:
        event = JobEvent(job_id=job_id, kind=kind, data=payload)
        session.add(event)
        session.flush()
        return int(event.id)


def events_since(job_id: str, after_id: int = 0, limit: int = 200) -> list[JobEvent]:
    """Events with ``id > after_id`` in ascending order."""

    with session_scope() as db:
        rows = list(
            db.scalars(
                select(JobEvent)
                .where(JobEvent.job_id == job_id, JobEvent.id > int(after_id or 0))
                .order_by(JobEvent.id)
                .limit(max(1, min(int(limit), 1000)))
            )
        )
        for row in rows:
            # Detach JSON/values before the session closes.
            db.expunge(row)
        return rows


def sse_frame(event_id: int | None, kind: str, data: dict[str, Any]) -> str:
    """Format one Server-Sent Event frame (UTF-8, newline escaped by JSON)."""

    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {kind}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)
