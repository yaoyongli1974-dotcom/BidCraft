"""后端可靠性/安全/校验加固回归测试。"""

from __future__ import annotations

import io
import time
from datetime import timedelta

import httpx
import pytest

pytest.importorskip("fastapi")

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    login = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert login.status_code == 200, login.text
    return client


def _project(client, name: str = "加固测试项目") -> str:
    response = client.post(
        "/projects", json={"name": name, "package": "weakcurrent-cabling"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _boq_bytes() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    sheet.append(["序号", "材料名称", "规格型号", "单位", "数量", "备注"])
    sheet.append([1, "面板", "双口", "个", "2", ""])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class _Stream:
    def __init__(self, response: httpx.Response):
        self.response = response

    def __enter__(self) -> httpx.Response:
        return self.response

    def __exit__(self, *_exc) -> bool:
        return False


def _patch_stream(monkeypatch, handler):
    from bidcraft import images

    calls: list[str] = []

    def fake_stream(method, url, **kwargs):
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        calls.append(str(url))
        request = httpx.Request(method, url)
        response = httpx.MockTransport(handler).handle_request(request)
        response.request = request
        return _Stream(response)

    monkeypatch.setattr(images.httpx, "stream", fake_stream)
    return calls


# ----------------------------------------------------------------- uploads


def test_upload_size_suffix_and_fallback(db, monkeypatch):
    from bidcraft import api

    client = _client()
    project_id = _project(client)

    monkeypatch.setattr(api, "MAX_TENDER_UPLOAD_BYTES", 1024)
    too_big = client.post(
        f"/projects/{project_id}/sources",
        files={"file": ("big.txt", b"x" * 2048, "text/plain")},
    )
    assert too_big.status_code == 413, too_big.text
    assert "50MB" in too_big.json()["detail"]

    bad_suffix = client.post(
        f"/projects/{project_id}/sources",
        files={"file": ("malware.exe", b"x", "application/octet-stream")},
    )
    assert bad_suffix.status_code == 400, bad_suffix.text
    assert "仅支持" in bad_suffix.json()["detail"]

    monkeypatch.setattr(api, "MAX_BOQ_UPLOAD_BYTES", 512)
    big_boq = client.post(
        f"/projects/{project_id}/boq",
        files={"file": ("boq.xlsx", b"x" * 1024, XLSX_MIME)},
    )
    assert big_boq.status_code == 413, big_boq.text
    assert "20MB" in big_boq.json()["detail"]

    bad_boq = client.post(
        f"/projects/{project_id}/boq",
        files={"file": ("boq.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert bad_boq.status_code == 400, bad_boq.text
    assert ".xlsx" in bad_boq.json()["detail"]

    assert api._safe_filename("", "upload.bin") == "upload.bin"
    assert api._safe_filename("/", "boq.xlsx") == "boq.xlsx"
    assert api._safe_filename("../secret.txt", "upload.bin") == "secret.txt"

    monkeypatch.setattr(api, "MAX_BOQ_UPLOAD_BYTES", 20 * 1024 * 1024)
    # A pure "/" filename falls back to the safe BOQ workbook name and still parses.
    fallback = client.post(
        f"/projects/{project_id}/boq",
        files={"file": ("/", _boq_bytes(), XLSX_MIME)},
    )
    assert fallback.status_code == 200, fallback.text
    assert fallback.json()["snapshot"] is not None


def test_failed_parse_removes_copied_source(db):
    from bidcraft.config import settings

    client = _client()
    project_id = _project(client)

    response = client.post(
        f"/projects/{project_id}/sources",
        files={"file": ("empty.txt", b"   \n\n  ", "text/plain")},
    )
    assert response.status_code == 400, response.text
    assert client.get(f"/projects/{project_id}/sources").json() == []
    sources_dir = settings.home / "projects" / project_id / "sources"
    assert list(sources_dir.glob("*")) == []


# ------------------------------------------------------- orphan job recovery


def test_startup_requeues_running_jobs_and_keeps_queued(db):
    """DB 驱动队列：启动恢复把孤儿 running 重排队，queued 保持不变，done 不动。"""

    from bidcraft.db import session_scope
    from bidcraft.models import Job

    with session_scope() as session:
        queued = Job(kind="generate", status="queued", message="排队中")
        running = Job(kind="images", status="running", message="运行中", attempts=1)
        batch_check = Job(kind="batch_check", status="running", attempts=1)
        batch_export = Job(kind="batch_export", status="queued")
        done = Job(kind="generate", status="done", message="完成")
        session.add_all([queued, running, batch_check, batch_export, done])
        session.flush()
        queued_id, running_id = queued.id, running.id
        batch_check_id, batch_export_id = batch_check.id, batch_export.id
        done_id = done.id

    # 恢复不再由 create_app() 触发（避免 ASGI 多实例各自恢复）：
    # 由 worker 进程入口显式调用。这里只验证恢复本身，不起 worker。
    from bidcraft import jobqueue

    jobqueue.recover_on_startup()

    with session_scope() as session:
        # queued 保持
        assert session.get(Job, queued_id).status == "queued"
        assert session.get(Job, batch_export_id).status == "queued"
        # running（孤儿）重排队，租约字段清空（attempts 在下次认领时才 +1）
        for job_id in (running_id, batch_check_id):
            row = session.get(Job, job_id)
            assert row.status == "queued"
            assert row.claimed_at is None and row.lease_expires_at is None
            assert "重新排队" in row.message
        assert session.get(Job, done_id).status == "done"


def test_batch_submit_cleans_stale_job(db):
    from bidcraft.db import session_scope
    from bidcraft.models import Job, utcnow

    client = _client()
    project_id = _project(client)
    with session_scope() as session:
        stale = Job(
            kind="batch_check",
            status="running",
            created_at=utcnow() - timedelta(hours=3),
            updated_at=utcnow() - timedelta(hours=3),
            payload={"owner_id": ""},
        )
        session.add(stale)
        session.flush()
        stale_id = stale.id

    response = client.post("/projects/batch-check", json={"ids": [project_id]})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    for _ in range(100):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in {"done", "failed"}:
            break
        time.sleep(0.02)
    assert status["status"] in {"done", "failed"}
    with session_scope() as session:
        row = session.get(Job, stale_id)
        assert row.status == "failed"
        assert "超时" in row.message


# ------------------------------------------------------ download hardening


def test_redirect_to_private_address_is_rejected(db, monkeypatch):
    from bidcraft import images

    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/internal"})

    calls = _patch_stream(monkeypatch, handler)
    with pytest.raises(images.ImageError) as excinfo:
        images._download("https://cdn.example/out.png")
    assert "内网" in str(excinfo.value)
    assert calls == ["https://cdn.example/out.png"]


def test_initial_private_provider_url_is_allowed(db, monkeypatch):
    from bidcraft import images

    def handler(request):
        return httpx.Response(200, content=b"image-bytes")

    _patch_stream(monkeypatch, handler)
    assert images._download("http://127.0.0.1:8080/out.png") == b"image-bytes"


def test_download_stream_stops_over_limit(db, monkeypatch):
    from bidcraft import images

    def handler(request):
        return httpx.Response(200, content=b"x" * 100)

    _patch_stream(monkeypatch, handler)
    monkeypatch.setattr(images, "MAX_GENERATED_BYTES", 10)
    with pytest.raises(images.ImageError) as excinfo:
        images._download("https://cdn.example/big.png")
    assert "超过" in str(excinfo.value)


# ------------------------------------------------------------- validation


def test_input_validation_never_returns_500(db):
    client = _client()

    assert client.post("/projects", json={"name": "x" * 81}).status_code == 422
    project_id = _project(client)

    chapter = client.put(
        f"/projects/{project_id}/chapters/nope", json={"body": "x" * 200001}
    )
    assert chapter.status_code == 422, chapter.text

    assert (
        client.post(f"/projects/{project_id}/outline", json={"words": 999}).status_code
        == 422
    )
    assert (
        client.post(
            f"/projects/{project_id}/outline", json={"words": 200001}
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/projects/{project_id}/outline",
            json={"chapters": [{"heading": f"章{i}"} for i in range(201)]},
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/projects/{project_id}/outline",
            json={"chapters": [{"heading": "x" * 201}]},
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/projects/{project_id}/outline",
            json={"chapters": [{"heading": "章", "target_words": 99}]},
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/projects/{project_id}/facts/project.name",
            json={"value": "x", "status": "bogus"},
        ).status_code
        == 422
    )

    user_id = client.get("/auth/me").json()["user"]["id"]
    huge = client.post(
        f"/admin/users/{user_id}/credits", json={"amount": 10**30}
    )
    assert huge.status_code == 422, huge.text
    assert (
        client.post(f"/admin/users/{user_id}/credits", json={"amount": 0}).status_code
        == 400
    )


# ---------------------------------------------------------- path boundaries


def test_source_and_batch_download_path_boundaries(db, tmp_path):
    from bidcraft.config import settings
    from bidcraft.db import session_scope
    from bidcraft.models import Job, SourceFile

    client = _client()
    project_id = _project(client)
    admin_id = client.get("/auth/me").json()["user"]["id"]

    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with session_scope() as session:
        source = SourceFile(
            project_id=project_id,
            role="tender",
            filename="outside.txt",
            stored_path=str(outside),
        )
        session.add(source)
        session.flush()
        source_id = source.id

    assert (
        client.get(f"/projects/{project_id}/sources/{source_id}/file").status_code
        == 404
    )

    outside_zip = settings.home / "outside.zip"
    outside_zip.write_bytes(b"PK-not-really")
    with session_scope() as session:
        bad_job = Job(
            kind="batch_export",
            status="done",
            payload={"owner_id": admin_id},
            result={"zip_path": str(outside_zip)},
        )
        session.add(bad_job)
        session.flush()
        bad_job_id = bad_job.id

    assert client.get(f"/batch-jobs/{bad_job_id}/download").status_code == 404

    output = settings.home / "output"
    output.mkdir(parents=True, exist_ok=True)
    good_zip = output / "batch-good.zip"
    good_zip.write_bytes(b"PK-not-really")
    with session_scope() as session:
        good_job = Job(
            kind="batch_export",
            status="done",
            payload={"owner_id": admin_id},
            result={"zip_path": str(good_zip)},
        )
        session.add(good_job)
        session.flush()
        good_job_id = good_job.id
    assert client.get(f"/batch-jobs/{good_job_id}/download").status_code == 200


# --------------------------------------------------------------- pagination


def test_pagination_limit_offset_and_defaults(db):
    client = _client()
    created = {_project(client, f"分页项目{i}") for i in range(3)}

    default = client.get("/projects")
    assert default.status_code == 200
    assert {item["id"] for item in default.json()} == created

    first = client.get("/projects", params={"limit": 2})
    assert len(first.json()) == 2
    assert {item["id"] for item in first.json()} <= created

    tail = client.get("/projects", params={"limit": 2, "offset": 2})
    assert len(tail.json()) == 1

    for index, name in enumerate(("Q1", "Q2", "Q3"), start=1):
        response = client.post(
            "/admin/quotas", json={"name": name, "unit_price": float(index)}
        )
        assert response.status_code == 201, response.text

    quotas = client.get("/admin/quotas").json()
    assert [item["name"] for item in quotas] == ["Q1", "Q2", "Q3"]
    page = client.get("/admin/quotas", params={"limit": 1, "offset": 1}).json()
    assert [item["name"] for item in page] == ["Q2"]


# ----------------------------------------------------------- session purge


def _add_expired_session(user_id: str, label: str) -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import AuthSession, utcnow
    from bidcraft.security import token_hash

    with session_scope() as session:
        row = AuthSession(
            user_id=user_id,
            token_hash=token_hash(label),
            expires_at=utcnow() - timedelta(days=1),
        )
        session.add(row)
        session.flush()
        return row.id


def test_expired_sessions_are_purged_on_startup_and_login(db):
    from sqlalchemy import select

    from bidcraft.api import create_app
    from bidcraft.db import session_scope
    from bidcraft.models import AuthSession, User

    create_app()  # ensures the admin exists
    with session_scope() as session:
        admin = session.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        admin_id = admin.id

    startup_row = _add_expired_session(admin_id, "startup-expired")
    create_app()
    with session_scope() as session:
        assert session.get(AuthSession, startup_row) is None

    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    login_row = _add_expired_session(admin_id, "login-expired")
    assert client.post(
        "/auth/login", json={"username": "admin", "password": "test-admin"}
    ).status_code == 200
    with session_scope() as session:
        assert session.get(AuthSession, login_row) is None


# ------------------------------------------------------- upstream redaction


def test_knowledge_error_body_is_not_exposed(db, monkeypatch):
    from bidcraft import knowledge

    client = _client()
    created = client.post(
        "/admin/kbs",
        json={
            "name": "远端知识库",
            "kind": "api",
            "provider": "generic",
            "location": "https://kb.example/search",
        },
    )
    assert created.status_code == 201, created.text
    kb_id = created.json()["id"]

    def fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(500, text="upstream-secret-body")
        )
        response = transport.handle_request(request)
        response.request = request
        return response

    monkeypatch.setattr(knowledge.httpx, "post", fake_post)
    tested = client.post(f"/admin/kbs/{kb_id}/test")
    assert tested.status_code == 200, tested.text
    payload = tested.json()
    assert payload["ok"] is False
    assert "upstream-secret-body" not in payload["message"]
    assert "HTTP 500" in payload["message"]


def test_create_app_does_not_recover_jobs(db):
    """create_app() 是纯工厂：ASGI 多实例各自调用它，不能触发任务恢复。"""

    from datetime import timedelta

    from bidcraft.api import create_app
    from bidcraft.db import session_scope
    from bidcraft.models import Job, utcnow

    with session_scope() as session:
        stale = Job(
            kind="generate",
            status="running",
            attempts=1,
            claimed_at=utcnow() - timedelta(minutes=2),
            lease_expires_at=utcnow() - timedelta(seconds=1),
        )
        session.add(stale)
        session.flush()
        stale_id = stale.id

    create_app()
    with session_scope() as session:
        # 恢复由 worker 进程入口显式执行，create_app() 不动任务状态
        assert session.get(Job, stale_id).status == "running"
