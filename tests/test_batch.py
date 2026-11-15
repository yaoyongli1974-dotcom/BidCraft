"""Batch operations and bid-section support (backend lane, R3)."""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "batch-pass-1"


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _admin():
    return _login(*ADMIN)


def _make_user(admin, username: str, role: str = "manager") -> None:
    response = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": PASSWORD,
            "role": role,
        },
    )
    assert response.status_code == 201, response.text


def _make_project(client, name: str, bid_section: str | None = None) -> dict:
    payload: dict = {"name": name, "package": "weakcurrent-cabling"}
    if bid_section is not None:
        payload["bid_section"] = bid_section
    response = client.post("/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _give_body(client, project_id: str) -> None:
    saved = client.put(
        f"/projects/{project_id}/outline",
        json={
            "chapters": [
                {"heading": "第一章 项目理解", "intent": "", "target_words": 800}
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    node_id = saved.json()["chapters"][0]["id"]
    edited = client.put(
        f"/projects/{project_id}/chapters/{node_id}",
        json={"body": "## 项目理解\n\n这是用于批量导出的正文内容。"},
    )
    assert edited.status_code == 200, edited.text


def _wait(client, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    status: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/jobs/{job_id}")
        assert response.status_code == 200, response.text
        status = response.json()
        if status["status"] in {"done", "failed"}:
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {status}")


@pytest.fixture()
def team(db):
    admin = _admin()
    _make_user(admin, "batch-owner")
    _make_user(admin, "batch-viewer")
    _make_user(admin, "batch-outside")
    _make_user(admin, "batch-global-viewer", role="viewer")
    return {
        "admin": admin,
        "owner": _login("batch-owner"),
        "viewer": _login("batch-viewer"),
        "outside": _login("batch-outside"),
        "global_viewer": _login("batch-global-viewer"),
    }


def test_bid_section_create_patch_and_audit(team):
    owner = team["owner"]
    project = _make_project(owner, "标段项目", bid_section="一标段")
    assert project["bid_section"] == "一标段"

    listed = {item["id"]: item for item in owner.get("/projects").json()}
    assert listed[project["id"]]["bid_section"] == "一标段"
    detail = owner.get(f"/projects/{project['id']}").json()
    assert detail["bid_section"] == "一标段"

    updated = owner.patch(
        f"/projects/{project['id']}",
        json={"name": "标段项目（改名）", "bid_section": "包件1"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["name"] == "标段项目（改名）"
    assert body["bid_section"] == "包件1"
    assert body["my_role"] == "owner"

    audit = owner.get(f"/projects/{project['id']}/audit").json()["items"]
    assert audit[0]["action"] == "project.update"
    assert sorted(audit[0]["detail"]["keys"]) == ["bid_section", "name"]

    assert (
        owner.post(
            f"/projects/{project['id']}/members",
            json={"username": "batch-viewer", "role": "viewer"},
        ).status_code
        == 201
    )
    blocked = team["viewer"].patch(
        f"/projects/{project['id']}", json={"bid_section": "二标段"}
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "只读成员不能修改该项目"

    empty = owner.patch(f"/projects/{project['id']}", json={})
    assert empty.status_code == 400
    assert "没有需要更新" in empty.json()["detail"]

    too_long = owner.patch(
        f"/projects/{project['id']}", json={"bid_section": "x" * 61}
    )
    assert too_long.status_code == 400
    assert "标段" in too_long.json()["detail"]


def test_batch_check_filters_inaccessible_projects(team):
    owner = team["owner"]
    mine = _make_project(owner, "自有核对项目")
    theirs = _make_project(team["admin"], "他人核对项目")

    empty = owner.post("/projects/batch-check", json={"ids": []})
    assert empty.status_code == 400
    assert empty.json()["detail"] == "没有可操作的项目"

    started = owner.post(
        "/projects/batch-check",
        json={"ids": [mine["id"], theirs["id"], "missing-project"]},
    )
    assert started.status_code == 202, started.text
    job = _wait(owner, started.json()["job_id"])
    assert job["status"] == "done", job
    assert job["kind"] == "batch_check"
    assert job["progress"] == 100

    result = job["result"]
    assert result["total"] == 1
    assert result["ok"] == 1
    assert result["failed"] == 0
    assert [item["project_id"] for item in result["results"]] == [mine["id"]]
    item = result["results"][0]
    assert item["ok"] is True
    assert isinstance(item["issues"], int)
    assert isinstance(item["blocking"], int)


def test_batch_export_zip_partial_failure_and_download(team):
    admin = team["admin"]
    first = _make_project(admin, "导出项目A")
    second = _make_project(admin, "导出项目B")
    blank = _make_project(admin, "无正文项目")
    _give_body(admin, first["id"])
    _give_body(admin, second["id"])

    started = admin.post(
        "/projects/batch-export",
        json={"ids": [first["id"], second["id"], blank["id"]], "format": "docx"},
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    job = _wait(admin, job_id)
    assert job["status"] == "done", job
    assert job["kind"] == "batch_export"

    result = job["result"]
    assert result["total"] == 3
    assert result["ok"] == 2
    assert len(result["files"]) == 2
    assert len(result["failed"]) == 1
    assert result["failed"][0]["project_id"] == blank["id"]
    assert "正文" in result["failed"][0]["error"]

    zip_path = Path(result["zip_path"])
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as bundle:
        names = bundle.namelist()
    assert len(names) == 2
    assert all(name.endswith(".docx") for name in names)

    downloaded = admin.get(f"/batch-jobs/{job_id}/download")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content[:2] == b"PK"
    assert "attachment" in downloaded.headers["content-disposition"]

    # a manager outside the batch cannot read the job or its zip
    other = team["outside"]
    assert other.get(f"/batch-jobs/{job_id}/download").status_code == 404
    assert other.get(f"/jobs/{job_id}").status_code == 404


def test_batch_export_xlsx_without_body(team):
    admin = team["admin"]
    blank = _make_project(admin, "无正文台账项目")
    started = admin.post(
        "/projects/batch-export", json={"ids": [blank["id"]], "format": "xlsx"}
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    job = _wait(admin, job_id)
    assert job["status"] == "done", job

    result = job["result"]
    assert result["ok"] == 1
    assert result["failed"] == []
    with zipfile.ZipFile(result["zip_path"]) as bundle:
        names = bundle.namelist()
    assert len(names) == 1
    assert names[0].endswith(".xlsx")

    downloaded = admin.get(f"/batch-jobs/{job_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content[:2] == b"PK"


def test_batch_permission_gates(team):
    admin = team["admin"]
    project = _make_project(admin, "权限批量项目")

    blocked = team["global_viewer"].post(
        "/projects/batch-check", json={"ids": [project["id"]]}
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "只读成员不能执行此操作"

    blocked_export = team["global_viewer"].post(
        "/projects/batch-export",
        json={"ids": [project["id"]], "format": "docx"},
    )
    assert blocked_export.status_code == 403

    bad_format = admin.post(
        "/projects/batch-export",
        json={"ids": [project["id"]], "format": "pdf"},
    )
    assert bad_format.status_code == 400
    assert "format" in bad_format.json()["detail"]

    too_many = admin.post(
        "/projects/batch-check", json={"ids": [project["id"]] * 51}
    )
    assert too_many.status_code == 400
    assert "50" in too_many.json()["detail"]
