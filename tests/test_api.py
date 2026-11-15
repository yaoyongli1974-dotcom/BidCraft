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


def test_auth_is_required(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    assert client.get("/projects").status_code == 401
    assert client.get("/health").status_code == 200
    assert client.post("/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401


def test_api_project_lifecycle(db, tender_path):


    client = _client()
    assert client.get("/health").json()["version"]

    created = client.post(
        "/projects", json={"name": "API 测试项目", "package": "weakcurrent-cabling"}
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    with tender_path.open("rb") as handle:
        uploaded = client.post(
            f"/projects/{project_id}/sources", files={"file": ("tender.txt", handle, "text/plain")}
        )
    assert uploaded.status_code == 200
    assert uploaded.json()["ledger_summary"]["fields_filled"] >= 20

    detail = client.get(f"/projects/{project_id}").json()
    assert detail["missing_required"] == []
    assert detail["checks_total"] >= 10

    outline = client.post(f"/projects/{project_id}/outline", json={"mode": "package"})
    assert outline.status_code == 200
    assert len(outline.json()["chapters"]) >= 10

    written = client.post(f"/projects/{project_id}/write")
    assert written.status_code == 200
    assert written.json()["failed"] == []

    checked = client.post(f"/projects/{project_id}/check")
    assert checked.status_code == 200
    assert checked.json()["verdict"] in {"pass", "warn"}
    assert checked.json()["summary"]["total"] >= 10

    exported = client.post(f"/projects/{project_id}/export", json={"format": "docx"})
    assert exported.status_code == 200
    assert exported.content[:2] == b"PK"

    ledger = client.post(f"/projects/{project_id}/export", json={"format": "xlsx"})
    assert ledger.status_code == 200
    assert ledger.content[:2] == b"PK"


def test_api_unknown_project(db):


    client = _client()
    assert client.get("/projects/nope").status_code == 404


def _new_parsed_project(client, tender_path):
    project_id = client.post(
        "/projects", json={"name": "Web 客户端项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    with tender_path.open("rb") as handle:
        response = client.post(
            f"/projects/{project_id}/sources",
            files={"file": ("tender.txt", handle, "text/plain")},
        )
    assert response.status_code == 200
    return project_id


def test_api_ledger_outline_and_chapter_editing(db, tender_path):


    client = _client()
    project_id = _new_parsed_project(client, tender_path)

    ledger = client.get(f"/projects/{project_id}/ledger").json()
    assert ledger["summary"]["fields_filled"] >= 20
    assert any(fact["key"] == "project.name" for fact in ledger["facts"])
    assert "materials" in ledger and "scoring" in ledger

    sources = client.get(f"/projects/{project_id}/sources").json()
    assert sources[0]["filename"] == "tender.txt"
    assert sources[0]["parse_state"] == "parsed"

    outline = client.post(f"/projects/{project_id}/outline", json={"mode": "package"}).json()
    chapters = outline["chapters"]
    assert len(chapters) >= 10

    first = chapters[0]
    saved = client.put(
        f"/projects/{project_id}/outline",
        json={
            "chapters": [
                {
                    "id": first["id"],
                    "heading": first["heading"] + "（修订）",
                    "intent": "人工调整",
                    "target_words": 1500,
                },
                {"heading": "新增章节", "intent": "补充", "target_words": 800},
            ]
        },
    )
    assert saved.status_code == 200
    assert saved.json()["chapters"][-1]["heading"] == "新增章节"
    assert saved.json()["chapters"][0]["heading"].endswith("（修订）")

    detail = client.get(f"/projects/{project_id}/chapters/{first['id']}").json()
    assert detail["heading"].endswith("（修订）")
    edited = client.put(
        f"/projects/{project_id}/chapters/{first['id']}",
        json={"body": "## 人工正文\n\n这是人工编辑的章节。"},
    )
    assert edited.status_code == 200
    assert edited.json()["words"] > 0
    assert edited.json()["status"] == "edited"

    checked = client.post(f"/projects/{project_id}/check")
    assert checked.status_code == 200
    issues = client.get(f"/projects/{project_id}/issues").json()
    assert isinstance(issues, list)


def test_api_background_generation_job(db, tender_path):
    import time


    client = _client()
    project_id = _new_parsed_project(client, tender_path)
    client.post(f"/projects/{project_id}/outline", json={"mode": "package"})

    started = client.post(f"/projects/{project_id}/generate", json={})
    assert started.status_code == 202
    job_id = started.json()["job_id"]

    status = {}
    for _ in range(100):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert status["status"] == "done", status
    assert status["progress"] == 100
    assert status["payload"]["total"] >= 10
    assert status["result"]["failed"] == []

    detail = client.get(f"/projects/{project_id}").json()
    assert detail["stats"]["written"] >= 10
    assert detail["stats"]["missing_required"] == 0


def test_api_delete_project(db, tender_path):


    client = _client()
    project_id = _new_parsed_project(client, tender_path)
    assert client.delete(f"/projects/{project_id}").json()["deleted"] is True
    assert client.get(f"/projects/{project_id}").status_code == 404


def test_web_client_is_served_when_built(db):
    from pathlib import Path

    import pytest


    index = Path(__file__).resolve().parents[1] / "bidcraft" / "web_dist" / "index.html"
    if not index.is_file():
        pytest.skip("web client not built")

    client = _client()
    root = client.get("/")
    assert root.status_code == 200
    assert "text/html" in root.headers["content-type"]
    deep = client.get("/app/projects/example")
    assert deep.status_code == 200
    assert "text/html" in deep.headers["content-type"]
    assert client.get("/projects/nope").status_code == 404
