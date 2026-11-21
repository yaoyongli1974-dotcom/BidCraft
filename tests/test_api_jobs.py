from __future__ import annotations

import time

import pytest
from sqlalchemy import select

pytest.importorskip("fastapi")


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": "admin", "password": "test-admin"}
    )
    assert response.status_code == 200, response.text
    return client


def _job_with_events(db, project_id: str):
    from bidcraft.db import session_scope
    from bidcraft.jobs import publish_event
    from bidcraft.models import Job

    with session_scope() as session:
        job = Job(project_id=project_id, kind="generate", status="done", progress=100)
        session.add(job)
        session.flush()
        job_id = job.id
        publish_event(job_id, "queued", {"status": "queued"}, db=session)
        publish_event(
            job_id,
            "chapter_start",
            {"heading": "第一章 项目理解", "index": 1, "total": 2},
            db=session,
        )
        publish_event(
            job_id,
            "delta",
            {"heading": "第一章 项目理解", "text": "本项目围绕"},
            db=session,
        )
        publish_event(
            job_id,
            "done",
            {"status": "done", "report": {"written": 1}},
            db=session,
        )
    return job_id


def test_sse_replays_events_and_resumes_after_last_event_id(db):
    client = _client()
    project = client.post(
        "/projects", json={"name": "SSE 回放项目", "package": "weakcurrent-cabling"}
    ).json()
    job_id = _job_with_events(db, project["id"])

    response = client.get(f"/jobs/{job_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "retry: 3000" in body
    assert "event: snapshot" in body
    assert "event: queued" in body
    assert "event: chapter_start" in body
    assert "event: delta" in body
    assert "event: done" in body
    assert "本项目围绕" in body
    assert "\\u" not in body

    ids = [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]
    assert ids == sorted(ids)
    assert ids

    resumed = client.get(
        f"/jobs/{job_id}/events", headers={"Last-Event-ID": str(ids[0])}
    )
    assert resumed.status_code == 200
    resumed_body = resumed.text
    assert "event: queued" not in resumed_body
    assert "event: chapter_start" in resumed_body
    assert "event: done" in resumed_body


def test_sse_rejects_other_users_job(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _client()
    project = admin.post(
        "/projects", json={"name": "SSE 权限项目", "package": "weakcurrent-cabling"}
    ).json()
    job_id = _job_with_events(db, project["id"])

    other = TestClient(create_app())
    registered = other.post(
        "/auth/register",
        json={"username": "sse-viewer", "password": "viewer-pass", "display_name": "只读", "accept_terms": True},
    )
    assert registered.status_code == 201, registered.text
    assert other.get(f"/jobs/{job_id}").status_code == 404
    assert other.get(f"/jobs/{job_id}/events").status_code == 404


def test_generation_job_emits_events_and_lists_recent_jobs(db, tender_path):
    client = _client()
    project = client.post(
        "/projects", json={"name": "事件生成项目", "package": "weakcurrent-cabling"}
    ).json()
    with tender_path.open("rb") as handle:
        uploaded = client.post(
            f"/projects/{project['id']}/sources",
            files={"file": ("tender.txt", handle, "text/plain")},
        )
    assert uploaded.status_code == 200, uploaded.text
    outline = client.post(f"/projects/{project['id']}/outline", json={"mode": "package"})
    assert outline.status_code == 200, outline.text

    started = client.post(f"/projects/{project['id']}/generate", json={})
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    status = {}
    for _ in range(100):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert status["status"] == "done", status

    from bidcraft.db import session_scope
    from bidcraft.models import JobEvent

    with session_scope() as session:
        events = list(
            session.scalars(
                select(JobEvent)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.id)
            )
        )
    kinds = [event.kind for event in events]
    assert kinds[0] == "queued"
    assert "status" in kinds
    assert "chapter_start" in kinds
    assert "chapter" in kinds
    assert kinds[-1] == "done"
    assert any(event.data.get("heading") for event in events if event.kind == "chapter")

    listed = client.get(f"/projects/{project['id']}/jobs")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == job_id
    assert listed.json()[0]["status"] == "done"
