"""第三轮审计：所有入队路径都要写出可索引的 Job.owner_id（单用户背压）。"""

from __future__ import annotations

import pytest

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


def _admin_id() -> str:
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import User

    with session_scope() as session:
        return session.scalar(select(User.id).where(User.username == "admin"))


def test_model_derives_owner_from_payload_and_project(db):
    from bidcraft.db import session_scope
    from bidcraft.models import Job, Project

    with session_scope() as session:
        # 旧调用方只写 user_id（generate_all 曾如此）。
        legacy = Job(kind="generate", status="queued", payload={"user_id": "u-legacy"})
        session.add(legacy)
        # 完全没有归属信息时，退回项目 owner。
        project = Project(name="归属回退项目", owner_id="owner-7")
        session.add(project)
        session.flush()
        derived = Job(kind="images", status="queued", project_id=project.id, payload={})
        session.add(derived)
        # 显式列值优先级最高。
        explicit = Job(
            kind="agent", status="queued", owner_id="explicit-9",
            payload={"owner_id": "other"},
        )
        session.add(explicit)
        session.flush()
        assert legacy.owner_id == "u-legacy"
        assert derived.owner_id == "owner-7"
        assert explicit.owner_id == "explicit-9"
        assert legacy.id and derived.id and explicit.id


def test_generate_all_queues_job_with_owner_id(db, tender_path, monkeypatch):
    """generate_all 只能在 payload 里放 user_id 时，owner_id 列必须被回填。"""

    from bidcraft.config import settings

    # 只验证入队行，不让 worker 真跑生成（否则 job 会很快变成 done）。
    monkeypatch.setattr(settings, "embedded_workers", False)
    client = _client()
    project = client.post(
        "/projects", json={"name": "归属回归-生成", "package": "weakcurrent-cabling"}
    ).json()
    with tender_path.open("rb") as handle:
        uploaded = client.post(
            f"/projects/{project['id']}/sources",
            files={"file": ("tender.txt", handle, "text/plain")},
        )
    assert uploaded.status_code == 200, uploaded.text
    assert client.post(
        f"/projects/{project['id']}/outline", json={"mode": "package"}
    ).status_code == 200

    started = client.post(f"/projects/{project['id']}/generate", json={})
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]

    from bidcraft.db import session_scope
    from bidcraft.models import Job

    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.owner_id == _admin_id()
        assert job.payload["owner_id"] == _admin_id()
        assert job.payload["user_id"] == _admin_id()


def test_images_batch_queues_job_with_owner_id(db, monkeypatch):
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "embedded_workers", False)
    client = _client()
    project = client.post(
        "/projects", json={"name": "归属回归-配图", "package": "weakcurrent-cabling"}
    ).json()
    project_id = project["id"]

    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    with session_scope() as session:
        node = OutlineNode(project_id=project_id, heading="机房工程", position=1)
        session.add(node)
        session.flush()
        body = "{{图片占位:拓扑图}}\n"
        session.add(Section(node_id=node.id, body=body, status="written", words=len(body)))

    started = client.post(f"/projects/{project_id}/images/generate-missing", json={})
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]

    from bidcraft.db import session_scope
    from bidcraft.models import Job

    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.owner_id == _admin_id()
        assert job.payload["owner_id"] == _admin_id()
