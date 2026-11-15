from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

pytest.importorskip("fastapi")

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
JPEG_1X1 = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + b"\x00" * 32
    + b"\xff\xd9"
)
MISSING = object()


class FakeImageServer:
    """OpenAI-compatible ``/images/generations`` stand-in."""

    def __init__(
        self,
        *,
        payload: bytes | object = MISSING,
        status: int = 200,
        use_url: bool = False,
        url: str = "https://fake.example/out.png",
        prefix: str = "",
    ):
        self.requests: list[dict] = []
        self.payload = PNG_1X1 if payload is MISSING else payload
        self.status = status
        self.use_url = use_url
        self.url = url
        self.prefix = prefix

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "provider down"}})
        if self.use_url:
            return httpx.Response(200, json={"data": [{"url": self.url}]})
        encoded = base64.b64encode(self.payload).decode()  # type: ignore[arg-type]
        return httpx.Response(200, json={"data": [{"b64_json": f"{self.prefix}{encoded}"}]})


class FakeDownloadServer:
    def __init__(self, content: bytes = PNG_1X1, status: int = 200):
        self.content = content
        self.status = status
        self.requests: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if self.status != 200:
            return httpx.Response(self.status, text="download failed")
        return httpx.Response(200, content=self.content)


def _fake_transport(server) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        response = server.handle(request)
        response.request = request
        return response

    return httpx.MockTransport(handler)


def _patch_http(monkeypatch, server, download=None) -> None:
    from bidcraft import images

    def fake_post(url, **kwargs):
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        request = httpx.Request("POST", url, **kwargs)
        return _fake_transport(server).handle_request(request)

    monkeypatch.setattr(images.httpx, "post", fake_post)
    if download is not None:

        class _Stream:
            def __init__(self, response):
                self.response = response

            def __enter__(self):
                return self.response

            def __exit__(self, *_exc):
                return False

        def fake_stream(method, url, **kwargs):
            kwargs.pop("timeout", None)
            kwargs.pop("follow_redirects", None)
            request = httpx.Request(method, url)
            return _Stream(_fake_transport(download).handle_request(request))

        monkeypatch.setattr(images.httpx, "stream", fake_stream)


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    login = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert login.status_code == 200
    return client


def _make_project(client, name: str = "图片项目") -> str:
    response = client.post("/projects", json={"name": name, "package": "weakcurrent-cabling"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _configure_model(client, **options) -> None:
    response = client.post(
        "/admin/models",
        json={
            "name": "图片模型",
            "kind": "image",
            "provider": "openai",
            "base_url": "http://fake/v1",
            "model": "demo-image",
            "is_default": True,
            "options": options,
        },
    )
    assert response.status_code == 201, response.text


def _add_chapter(project_id: str, heading: str = "视频监控系统") -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    body = (
        f"## {heading}\n\n"
        "本系统采用 IP 摄像机，![示意图](/projects/x/images/a.png)，"
        "前端点位覆盖出入口与周界。{{待填:品牌}} "
        "**重点** 区域采用 4K 摄像机，录像保存 30 天。"
    )
    with session_scope() as db:
        node = OutlineNode(project_id=project_id, heading=heading, position=1)
        db.add(node)
        db.flush()
        db.add(Section(node_id=node.id, body=body, status="written", words=120))
        return node.id


def _create_image(client, project_id: str, **payload):
    body = {"label": "拓扑图", **payload}
    return client.post(f"/projects/{project_id}/images", json=body)


def _upload(client, project_id: str, content: bytes = PNG_1X1, name: str = "camera.png", **data):
    return client.post(
        f"/projects/{project_id}/images/upload",
        data=data,
        files={"file": (name, content, "image/png")},
    )


def test_image_asset_crud_and_file(db, monkeypatch):
    """Image create/list/file/delete through the API, against a fake provider."""
    from fastapi.testclient import TestClient

    from bidcraft import images
    from bidcraft.api import create_app

    server = FakeImageServer()
    _patch_http(monkeypatch, server)

    # Patch the module-level httpx used by images.generate_image
    original_post = images.httpx.post

    client = TestClient(create_app())
    login = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert login.status_code == 200

    project = client.post(
        "/projects", json={"name": "图片项目", "package": "weakcurrent-cabling"}
    ).json()
    project_id = project["id"]

    # No image model configured yet
    missing = client.post(
        f"/projects/{project_id}/images", json={"label": "拓扑图"}
    )
    assert missing.status_code == 503

    # Configure a default image model
    model = client.post(
        "/admin/models",
        json={
            "name": "图片模型",
            "kind": "image",
            "provider": "openai",
            "base_url": "http://fake/v1",
            "model": "demo-image",
            "is_default": True,
        },
    )
    assert model.status_code == 201

    created = client.post(f"/projects/{project_id}/images", json={"label": "拓扑图"})
    assert created.status_code == 201, created.text
    asset = created.json()
    assert asset["status"] == "ready"
    assert asset["url"].endswith(".png")
    assert asset["prompt"].strip()
    assert asset["options"]["source"] == "generated"

    listed = client.get(f"/projects/{project_id}/images")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    # File endpoint serves PNG bytes
    file_url = asset["url"]
    served = client.get(file_url)
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/png")
    assert served.content[:4] == b"\x89PNG"

    # DELETE removes row and file
    deleted = client.delete(f"/projects/{project_id}/images/{asset['id']}")
    assert deleted.status_code == 200
    assert client.get(file_url).status_code == 404
    assert client.get(f"/projects/{project_id}/images").json() == []

    images.httpx.post = original_post  # not used but restored for clarity


def test_prompt_style_size_sent_to_provider(db, monkeypatch):
    client = _client()
    project_id = _make_project(client)
    node_id = _add_chapter(project_id)
    server = FakeImageServer()
    _patch_http(monkeypatch, server)
    _configure_model(client, image_quality="high", image_size="1792x1024")

    created = _create_image(
        client,
        project_id,
        label="摄像机安装示意",
        node_id=node_id,
        heading="这个标题应被后端章节标题覆盖",
        prompt="壁挂安装示意图",
        style="工程示意",
        size="1024x1024",
    )
    assert created.status_code == 201, created.text
    asset = created.json()

    body = server.requests[0]
    assert body["model"] == "demo-image"
    assert body["n"] == 1
    assert body["size"] == "1024x1024"
    assert body["quality"] == "high"
    assert body["response_format"] == "b64_json"

    prompt = body["prompt"]
    assert "壁挂安装示意图" in prompt
    assert "画面风格：工程示意" in prompt
    assert "摄像机安装示意" in prompt
    assert "视频监控系统" in prompt and "这个标题应被后端章节标题覆盖" not in prompt
    assert "IP 摄像机" in prompt
    assert "专业包：" in prompt
    assert "安全约束" in prompt
    for noise in ("![", "{{待填", "**", "## "):
        assert noise not in prompt
    assert asset["prompt"] == prompt

    options = asset["options"]
    assert options["source"] == "generated"
    assert options["size"] == "1024x1024"
    assert options["style"] == "工程示意"
    assert options["user_prompt"] == "壁挂安装示意图"
    assert options["quality"] == "high"


def test_size_and_response_format_defaults_from_config(db, monkeypatch):
    from bidcraft import images

    client = _client()
    project_id = _make_project(client)
    _add_chapter(project_id)
    server = FakeImageServer()
    _patch_http(monkeypatch, server)
    _configure_model(client, image_size="1792x1024")

    created = _create_image(client, project_id, label="系统架构")
    assert created.status_code == 201, created.text
    asset = created.json()
    assert server.requests[0]["size"] == "1792x1024"
    assert "response_format" in server.requests[0]
    assert asset["options"]["size"] == "1792x1024"
    assert "user_prompt" not in asset["options"]
    assert "style" not in asset["options"]
    assert "主题：系统架构" in asset["prompt"]

    # Explicitly empty response_format means "do not send the field".
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import ModelConfig

    with session_scope() as session:
        config = session.scalar(select(ModelConfig).where(ModelConfig.kind == "image"))
        assert config is not None
        config.options = {"response_format": ""}
        session.flush()

    second = _create_image(client, project_id, label="极简信息图", size="auto")
    assert second.status_code == 201, second.text
    assert "response_format" not in server.requests[1]
    assert server.requests[1]["size"] == "auto"
    assert images.normalize_size("") == "1024x1024"


@pytest.mark.parametrize("size", ["100x100", "9000x9000", "8000x4000", "big"])
def test_invalid_size_is_a_request_error(db, monkeypatch, size):
    client = _client()
    project_id = _make_project(client)
    server = FakeImageServer()
    _patch_http(monkeypatch, server)
    _configure_model(client)

    response = _create_image(client, project_id, size=size)
    assert response.status_code == 400, response.text
    assert "尺寸" in response.json()["detail"]
    assert server.requests == []
    assert client.get(f"/projects/{project_id}/images").json() == []


def test_jpeg_extension_data_uri_and_serving(db, monkeypatch):
    from bidcraft import images

    client = _client()
    project_id = _make_project(client)
    server = FakeImageServer(payload=JPEG_1X1, prefix="data:image/jpeg;base64,")
    _patch_http(monkeypatch, server)
    _configure_model(client)

    created = _create_image(client, project_id, label="现场照片")
    assert created.status_code == 201, created.text
    asset = created.json()
    assert asset["status"] == "ready"
    assert asset["filename"].endswith(".jpg")
    assert asset["url"].endswith(".jpg")

    served = client.get(asset["url"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/jpeg")
    assert served.content[:3] == b"\xff\xd8\xff"

    path = images.resolve_image_path(project_id, asset["filename"])
    assert path is not None and path.suffix == ".jpg"


def test_url_response_is_downloaded(db, monkeypatch):
    client = _client()
    project_id = _make_project(client)
    server = FakeImageServer(use_url=True)
    download = FakeDownloadServer(content=PNG_1X1)
    _patch_http(monkeypatch, server, download)
    _configure_model(client)

    created = _create_image(client, project_id, label="下载图")
    assert created.status_code == 201, created.text
    asset = created.json()
    assert asset["status"] == "ready"
    assert asset["filename"].endswith(".png")
    assert download.requests == ["https://fake.example/out.png"]


def test_failed_assets_persist_with_error(db, monkeypatch):
    client = _client()
    project_id = _make_project(client)
    server = FakeImageServer(status=500)
    _patch_http(monkeypatch, server)
    _configure_model(client)

    failed = _create_image(client, project_id, label="失败图")
    assert failed.status_code == 201, failed.text
    asset = failed.json()
    assert asset["status"] == "failed"
    assert asset["last_error"]
    assert asset["filename"] == ""
    assert asset["url"] == ""
    assert asset["options"]["source"] == "generated"

    # Non-image bytes are also a persisted failure, never a 5xx.
    server_bad = FakeImageServer(payload=b"GIF89a-not-an-image")
    _patch_http(monkeypatch, server_bad)
    bad = _create_image(client, project_id, label="坏图")
    assert bad.status_code == 201, bad.text
    assert bad.json()["status"] == "failed"
    assert "PNG" in bad.json()["last_error"]

    # Invalid download scheme is a provider failure, not a crash.
    server_url = FakeImageServer(use_url=True, url="ftp://fake.example/out.png")
    _patch_http(monkeypatch, server_url)
    scheme = _create_image(client, project_id, label="协议图")
    assert scheme.status_code == 201, scheme.text
    assert scheme.json()["status"] == "failed"
    assert "http" in scheme.json()["last_error"]

    listed = client.get(f"/projects/{project_id}/images").json()
    assert len(listed) == 3
    assert {item["status"] for item in listed} == {"failed"}
    assert all(item["options"] for item in listed)


def test_regenerate_creates_new_asset(db, monkeypatch):
    client = _client()
    project_id = _make_project(client)
    node_id = _add_chapter(project_id)
    server = FakeImageServer()
    _patch_http(monkeypatch, server)
    _configure_model(client)

    created = _create_image(
        client,
        project_id,
        label="配线架示意",
        node_id=node_id,
        prompt="机柜内配线架",
        style="系统架构",
        size="1024x1792",
    )
    assert created.status_code == 201, created.text
    source = created.json()

    response = client.post(f"/projects/{project_id}/images/{source['id']}/regenerate")
    assert response.status_code == 201, response.text
    regenerated = response.json()
    assert regenerated["id"] != source["id"]
    assert regenerated["label"] == source["label"]
    assert regenerated["node_id"] == node_id
    assert regenerated["status"] == "ready"
    assert regenerated["options"]["style"] == "系统架构"
    assert regenerated["options"]["size"] == "1024x1792"
    assert regenerated["options"]["user_prompt"] == "机柜内配线架"

    body = server.requests[1]
    assert body["size"] == "1024x1792"
    assert "机柜内配线架" in body["prompt"]
    assert "画面风格：系统架构" in body["prompt"]

    listed = client.get(f"/projects/{project_id}/images").json()
    assert len(listed) == 2

    # Unknown asset ids are 404.
    missing = client.post(f"/projects/{project_id}/images/nope/regenerate")
    assert missing.status_code == 404


def test_upload_png_jpeg_and_validation(db, monkeypatch):
    client = _client()
    project_id = _make_project(client)
    node_id = _add_chapter(project_id)

    png = _upload(client, project_id, label="现场照片", node_id=node_id)
    assert png.status_code == 201, png.text
    png_asset = png.json()
    assert png_asset["status"] == "ready"
    assert png_asset["provider"] == "upload"
    assert png_asset["model"] == ""
    assert png_asset["prompt"] == ""
    assert png_asset["options"]["source"] == "upload"
    assert png_asset["options"]["original_name"] == "camera.png"
    assert png_asset["filename"].startswith("现场照片-")
    assert png_asset["filename"].endswith(".png")
    assert client.get(png_asset["url"]).content == PNG_1X1

    jpg = _upload(client, project_id, content=JPEG_1X1, name="photo.jpeg", label="现场照")
    assert jpg.status_code == 201, jpg.text
    assert jpg.json()["filename"].endswith(".jpg")
    assert client.get(jpg.json()["url"]).headers["content-type"].startswith("image/jpeg")

    # Unsupported type and empty label.
    bad = _upload(client, project_id, content=b"GIF89a-bad", name="bad.gif", label="坏图")
    assert bad.status_code == 400
    assert "PNG" in bad.json()["detail"]
    assert _upload(client, project_id, label="  ").status_code == 400

    # Oversized upload -> 413.
    huge = b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024)
    too_big = _upload(client, project_id, content=huge, label="大图")
    assert too_big.status_code == 413, too_big.text


def test_node_must_belong_to_project(db, monkeypatch):
    client = _client()
    own_project = _make_project(client, "本项目")
    other_project = _make_project(client, "别的项目")
    foreign_node = _add_chapter(other_project, heading="别人的章节")
    server = FakeImageServer()
    _patch_http(monkeypatch, server)
    _configure_model(client)

    created = _create_image(client, own_project, node_id=foreign_node)
    assert created.status_code == 400
    assert "章节不存在或不属于当前项目" in created.json()["detail"]
    assert server.requests == []

    uploaded = _upload(client, own_project, node_id=foreign_node, label="越权图")
    assert uploaded.status_code == 400
    assert "章节不存在或不属于当前项目" in uploaded.json()["detail"]


def test_regenerate_without_image_model_returns_503(db, monkeypatch):
    client = _client()
    project_id = _make_project(client)
    uploaded = _upload(client, project_id, label="已有图")
    assert uploaded.status_code == 201
    asset_id = uploaded.json()["id"]

    response = client.post(f"/projects/{project_id}/images/{asset_id}/regenerate")
    assert response.status_code == 503


def test_viewer_cannot_regenerate_or_upload(db, monkeypatch):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _client()
    project_id = _make_project(admin)
    asset_id = _upload(admin, project_id, label="已有图").json()["id"]
    created = admin.post(
        "/admin/users",
        json={
            "username": "img-viewer",
            "display_name": "只读同事",
            "password": "viewer-pass-1",
            "role": "viewer",
        },
    )
    assert created.status_code == 201

    viewer = TestClient(create_app())
    login = viewer.post(
        "/auth/login", json={"username": "img-viewer", "password": "viewer-pass-1"}
    )
    assert login.status_code == 200

    regenerate = viewer.post(f"/projects/{project_id}/images/{asset_id}/regenerate")
    assert regenerate.status_code == 403
    upload = viewer.post(
        f"/projects/{project_id}/images/upload",
        data={"label": "越权上传"},
        files={"file": ("x.png", PNG_1X1, "image/png")},
    )
    assert upload.status_code == 403


def test_docx_export_image_substitution(db, monkeypatch, tender_path):
    """An image markdown line is replaced by an embedded picture in DOCX."""
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app
    from bidcraft.export.docx import export_docx

    client = TestClient(create_app())
    client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    project_id = client.post(
        "/projects", json={"name": "图片导出项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    with tender_path.open("rb") as handle:
        client.post(
            f"/projects/{project_id}/sources", files={"file": ("t.txt", handle, "text/plain")}
        )
    # Add an outline node manually via API? Simplest: use existing pipeline build via /outline.
    client.post(f"/projects/{project_id}/outline", json={"mode": "package"})
    chapters = client.get(f"/projects/{project_id}/outline").json()
    assert chapters

    nodes = chapters["chapters"]
    # Build a markdown body referencing an image; resolver must map to a file.
    import tempfile
    import zipfile
    from pathlib import Path

    from bidcraft import images as images_mod

    path = images_mod.image_dir(project_id) / "demo.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_1X1)
    nodes[0]["body"] = f"![拓扑图](/projects/{project_id}/images/demo.png)\n\n图片说明"

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "bid.docx"
        export_docx(
            out,
            ledger=None,
            nodes=nodes,
            include_cover=False,
            image_resolver=images_mod.url_resolver(project_id),
        )
        with zipfile.ZipFile(out) as package:
            assert any("media/image" in name for name in package.namelist()), package.namelist()

    client.delete(f"/projects/{project_id}")


def test_image_plan_reports_ready_failed_and_missing(db, monkeypatch):
    client = _client()
    project_id = _make_project(client, "配图计划")
    _configure_model(client)
    _patch_http(monkeypatch, FakeImageServer())
    ready = _create_image(client, project_id, label="拓扑图")
    assert ready.status_code == 201, ready.text
    assert ready.json()["status"] == "ready"

    _patch_http(monkeypatch, FakeImageServer(status=500))
    failed = _create_image(client, project_id, label="失败图")
    assert failed.status_code == 201, failed.text
    assert failed.json()["status"] == "failed"

    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    with session_scope() as session:
        first = OutlineNode(project_id=project_id, heading="视频监控系统", position=1)
        session.add(first)
        session.flush()
        session.add(
            Section(
                node_id=first.id,
                body="摄像机点位图：{{图片占位:拓扑图}}，补充：{{图片占位:缺失图}}。",
                status="written",
                words=40,
            )
        )
        second = OutlineNode(project_id=project_id, heading="机房工程", position=2)
        session.add(second)
        session.flush()
        session.add(
            Section(
                node_id=second.id,
                body="机柜布置：{{图片占位:失败图}}。",
                status="written",
                words=20,
            )
        )

    plan = client.get(f"/projects/{project_id}/images/plan")
    assert plan.status_code == 200, plan.text
    payload = plan.json()
    assert payload["summary"] == {
        "placeholders": 3,
        "ready": 1,
        "failed": 1,
        "missing": 1,
    }
    assert len(payload["chapters"]) == 2
    first_chapter, second_chapter = payload["chapters"]
    assert first_chapter["heading"] == "视频监控系统"
    assert first_chapter["total"] == 2
    assert first_chapter["ready"] == 1
    assert first_chapter["missing"] == 1
    assert first_chapter["budget"] == 1
    assert first_chapter["over_budget"] is True
    assert first_chapter["placeholders"][0]["token"] == "{{图片占位:拓扑图}}"
    assert first_chapter["placeholders"][0]["status"] == "ready"
    assert first_chapter["placeholders"][0]["asset_id"] == ready.json()["id"]
    assert first_chapter["placeholders"][1]["status"] == "missing"
    assert second_chapter["failed"] == 1
    assert second_chapter["placeholders"][0]["last_error"]
    assert second_chapter["placeholders"][0]["asset_id"] == failed.json()["id"]


def test_apply_image_plan_inserts_ready_and_respects_budget(db, monkeypatch):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import Section

    client = _client()
    project_id = _make_project(client, "配图编排项目")
    _configure_model(client)
    _patch_http(monkeypatch, FakeImageServer())
    for label in ("拓扑图", "预算图"):
        created = _create_image(client, project_id, label=label)
        assert created.status_code == 201, created.text
        assert created.json()["status"] == "ready"

    _add_placeholder_chapters(project_id, [("机房工程", ["拓扑图", "预算图"])])
    plan = client.get(f"/projects/{project_id}/images/plan").json()
    chapter = plan["chapters"][0]
    assert chapter["budget"] == 1
    assert chapter["over_budget"] is True

    applied = client.post(f"/projects/{project_id}/images/apply-plan")
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["applied"] == 1
    assert result["skipped_over_budget"] == 1
    assert result["applied_labels"] == ["拓扑图"]
    assert result["chapters"] == [chapter["id"]]

    with session_scope() as session:
        section = session.scalar(select(Section).where(Section.node_id == chapter["id"]))
        assert section is not None
        assert "![拓扑图](" in section.body
        assert "{{图片占位:预算图}}" in section.body
        assert section.status == "edited"

    after = client.get(f"/projects/{project_id}/images/plan").json()
    assert after["summary"] == {
        "placeholders": 1,
        "ready": 1,
        "failed": 0,
        "missing": 0,
    }


def test_apply_image_plan_requires_ready_image(db):
    client = _client()
    project_id = _make_project(client, "无图编排项目")
    _add_placeholder_chapters(project_id, [("机房工程", ["缺失图"])])
    response = client.post(f"/projects/{project_id}/images/apply-plan")
    assert response.status_code == 409
    assert "已生成" in response.json()["detail"]


def _add_placeholder_chapters(project_id: str, groups: list[tuple[str, list[str]]]) -> None:
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    with session_scope() as session:
        for position, (heading, labels) in enumerate(groups, start=1):
            node = OutlineNode(project_id=project_id, heading=heading, position=position)
            session.add(node)
            session.flush()
            body = "".join(f"{{{{图片占位:{label}}}}}\n" for label in labels)
            session.add(
                Section(node_id=node.id, body=body, status="written", words=len(body))
            )


def _wait_image_job(client, job_id: str) -> dict:
    status: dict = {}
    for _ in range(120):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in {"done", "failed"}:
            return status
        time.sleep(0.05)
    raise AssertionError(status)


def test_generate_missing_images_job(db, monkeypatch):
    client = _client()
    project_id = _make_project(client, "批量配图项目")
    _configure_model(client)
    _add_placeholder_chapters(
        project_id,
        [
            ("视频监控系统", ["拓扑图", "拓扑图"]),
            ("机房工程", ["缺失图"]),
        ],
    )
    server = FakeImageServer()
    _patch_http(monkeypatch, server)

    started = client.post(
        f"/projects/{project_id}/images/generate-missing", json={}
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    status = _wait_image_job(client, job_id)
    assert status["status"] == "done", status
    assert status["progress"] == 100
    assert status["result"]["total"] == 2
    assert status["result"]["succeeded"] == 2
    assert status["result"]["failed"] == 0
    assert len(server.requests) == 2

    plan = client.get(f"/projects/{project_id}/images/plan").json()
    assert plan["summary"] == {
        "placeholders": 3,
        "ready": 3,
        "failed": 0,
        "missing": 0,
    }

    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import JobEvent

    with session_scope() as session:
        kinds = [
            event.kind
            for event in session.scalars(
                select(JobEvent)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.id)
            )
        ]
    assert kinds[0] == "queued"
    assert "status" in kinds
    assert kinds.count("image") == 2
    assert kinds[-1] == "done"

    listed = client.get(f"/projects/{project_id}/jobs?kind=images")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == job_id

    again = client.post(f"/projects/{project_id}/images/generate-missing", json={})
    assert again.status_code == 409, again.text


def test_generate_missing_images_retries_failed(db, monkeypatch):
    client = _client()
    project_id = _make_project(client, "失败重试项目")
    _configure_model(client)
    _add_placeholder_chapters(project_id, [("机房工程", ["失败图"])])
    _patch_http(monkeypatch, FakeImageServer(status=500))
    failed = _create_image(client, project_id, label="失败图")
    assert failed.status_code == 201
    assert failed.json()["status"] == "failed"

    skipped = client.post(f"/projects/{project_id}/images/generate-missing", json={})
    assert skipped.status_code == 409, skipped.text

    _patch_http(monkeypatch, FakeImageServer())
    started = client.post(
        f"/projects/{project_id}/images/generate-missing",
        json={"include_failed": True},
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    status = _wait_image_job(client, job_id)
    assert status["status"] == "done", status
    assert status["result"]["succeeded"] == 1
    assert status["result"]["failed"] == 0

    plan = client.get(f"/projects/{project_id}/images/plan").json()
    assert plan["summary"] == {
        "placeholders": 1,
        "ready": 1,
        "failed": 0,
        "missing": 0,
    }
    images = client.get(f"/projects/{project_id}/images").json()
    assert len(images) == 2
    assert {item["status"] for item in images} == {"ready", "failed"}


def test_generate_missing_images_rejects_active_job(db, monkeypatch):
    from bidcraft.db import session_scope
    from bidcraft.models import Job

    client = _client()
    project_id = _make_project(client, "配图任务冲突项目")
    _configure_model(client)
    _add_placeholder_chapters(project_id, [("机房工程", ["待生成图"])])
    _patch_http(monkeypatch, FakeImageServer())
    with session_scope() as session:
        session.add(Job(project_id=project_id, kind="images", status="running"))
    response = client.post(f"/projects/{project_id}/images/generate-missing", json={})
    assert response.status_code == 409
    assert "配图任务" in response.json()["detail"]

