from __future__ import annotations

import json

from bidcraft import jobs
from bidcraft.db import session_scope
from bidcraft.models import Job, JobEvent


def test_events_since_orders_filters_and_limits(db):
    with session_scope() as session:
        job = Job(kind="test", status="queued", payload={}, result={})
        other = Job(kind="test", status="queued", payload={}, result={})
        session.add_all([job, other])
        session.flush()
        event_ids = []
        for kind in ("queued", "status", "done"):
            event = JobEvent(job_id=job.id, kind=kind, data={"kind": kind})
            session.add(event)
            session.flush()
            event_ids.append(int(event.id))
        session.add(JobEvent(job_id=other.id, kind="queued", data={}))
        session.flush()
        job_id = job.id

    rows = jobs.events_since(job_id)
    assert [row.kind for row in rows] == ["queued", "status", "done"]
    assert [int(row.id) for row in rows] == event_ids

    after_first = jobs.events_since(job_id, after_id=event_ids[0])
    assert [row.kind for row in after_first] == ["status", "done"]

    limited = jobs.events_since(job_id, after_id=event_ids[1], limit=1)
    assert [row.kind for row in limited] == ["done"]

    assert jobs.events_since(job_id, after_id=event_ids[-1]) == []


def test_events_since_clamps_limit(db):
    with session_scope() as session:
        job = Job(kind="test", status="queued", payload={}, result={})
        session.add(job)
        session.flush()
        for index in range(3):
            session.add(JobEvent(job_id=job.id, kind="status", data={"index": index}))
        session.flush()
        job_id = job.id

    assert len(jobs.events_since(job_id, limit=0)) == 1
    assert len(jobs.events_since(job_id, limit=-5)) == 1
    assert len(jobs.events_since(job_id, limit=10_000)) == 3


def test_sse_frame_with_and_without_id():
    frame = jobs.sse_frame(7, "status", {"done": 1, "text": "中文"})

    assert frame.startswith("id: 7\n")
    assert "event: status\n" in frame
    data_line = next(line for line in frame.splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: ")) == {"done": 1, "text": "中文"}
    assert frame.endswith("\n\n")

    anonymous = jobs.sse_frame(None, "ping", {})
    assert not anonymous.startswith("id:")
    assert "event: ping\n" in anonymous
    assert "data: {}\n" in anonymous
    assert anonymous.endswith("\n\n")


def test_job_payload_shape(db):
    with session_scope() as session:
        job = Job(
            project_id=None,
            kind="kb_reindex",
            status="failed",
            progress=40,
            message="索引失败",
            payload={"owner_id": "user-1", "kb_id": "kb-1"},
            result={"error": "稳定文案"},
        )
        session.add(job)
        session.flush()
        payload = jobs.job_payload(job)

    assert set(payload) == {
        "id",
        "project_id",
        "kind",
        "status",
        "progress",
        "message",
        "payload",
        "result",
        "error",
        "priority",
        "queue_position",
        "created_at",
        "updated_at",
    }
    assert payload["project_id"] is None
    assert payload["priority"] == 1
    assert payload["queue_position"] == 0
    assert payload["kind"] == "kb_reindex"
    assert payload["status"] == "failed"
    assert payload["progress"] == 40
    assert payload["message"] == "索引失败"
    assert payload["payload"] == {"owner_id": "user-1", "kb_id": "kb-1"}
    assert payload["result"] == {"error": "稳定文案"}
    assert payload["error"] == "稳定文案"
    assert payload["created_at"] is not None
    assert payload["updated_at"] is not None


def test_job_payload_defaults(db):
    with session_scope() as session:
        job = Job(kind="test", status="queued")
        session.add(job)
        session.flush()
        payload = jobs.job_payload(job)

    assert payload["payload"] == {}
    assert payload["result"] == {}
    assert payload["error"] == ""
