"""Whole-document image policy, budget and DOCX layout (R1 contract)."""

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

DEFAULT_POLICY = {
    "max_total": 12,
    "style": "",
    "size": "auto",
    "captions": True,
    "caption_prefix": "图",
    "width_cm": 15.0,
}


class FakeImageServer:
    """OpenAI-compatible ``/images/generations`` stand-in (see test_images)."""

    def __init__(self, *, payload: bytes = PNG_1X1, status: int = 200):
        self.requests: list[dict] = []
        self.payload = payload
        self.status = status

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "provider down"}})
        encoded = base64.b64encode(self.payload).decode()
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})


def _patch_http(monkeypatch, server) -> None:
    from bidcraft import images

    def fake_post(url, **kwargs):
        kwargs.pop("timeout", None)
        kwargs.pop("follow_redirects", None)
        request = httpx.Request("POST", url, **kwargs)
        response = server.handle(request)
        response.request = request
        return httpx.MockTransport(lambda _request: response).handle_request(request)

    monkeypatch.setattr(images.httpx, "post", fake_post)


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    login = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert login.status_code == 200
    return client


def _make_project(client, name: str = "配图策略项目") -> str:
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


def _add_chapters(project_id: str, groups: list[tuple[str, str]]) -> list[str]:
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    node_ids: list[str] = []
    with session_scope() as session:
        for position, (heading, body) in enumerate(groups, start=1):
            node = OutlineNode(project_id=project_id, heading=heading, position=position)
            session.add(node)
            session.flush()
            session.add(
                Section(node_id=node.id, body=body, status="written", words=len(body))
            )
            node_ids.append(node.id)
    return node_ids


def _add_ready_assets(project_id: str, labels: list[str]) -> None:
    from bidcraft.db import session_scope
    from bidcraft.models import ImageAsset

    with session_scope() as session:
        for label in labels:
            session.add(
                ImageAsset(
                    project_id=project_id,
                    label=label,
                    status="ready",
                    filename=f"{label}.png",
                    file_path="",
                    size_bytes=len(PNG_1X1),
                )
            )


def _get_policy(client, project_id: str) -> dict:
    response = client.get(f"/projects/{project_id}/image-policy")
    assert response.status_code == 200, response.text
    return response.json()


def _put_policy(client, project_id: str, payload: dict):
    return client.put(f"/projects/{project_id}/image-policy", json=payload)


def test_policy_defaults_clamps_unknown_keys_and_audit(db):
    client = _client()
    project_id = _make_project(client)

    assert _get_policy(client, project_id) == DEFAULT_POLICY

    updated = _put_policy(
        client,
        project_id,
        {
            "max_total": 99,
            "width_cm": 3,
            "style": "工" * 100,
            "size": "16:9",
            "captions": False,
            "caption_prefix": "",
            "unknown_key": "ignored",
        },
    )
    assert updated.status_code == 200, updated.text
    policy = updated.json()
    assert policy["max_total"] == 60
    assert policy["width_cm"] == 8.0
    assert len(policy["style"]) == 80
    assert policy["size"] == "auto"
    assert policy["captions"] is False
    assert policy["caption_prefix"] == "图"
    assert "unknown_key" not in policy

    # Partial updates keep the previously stored values.
    partial = _put_policy(client, project_id, {"max_total": 0, "width_cm": 19})
    assert partial.status_code == 200
    assert partial.json()["max_total"] == 0
    assert partial.json()["width_cm"] == 19.0
    assert partial.json()["captions"] is False

    audit_items = client.get(f"/projects/{project_id}/audit").json()["items"]
    actions = [item["action"] for item in audit_items]
    assert "image.policy" in actions
    entry = next(item for item in audit_items if item["action"] == "image.policy")
    assert "keys" in entry["detail"]
    assert "max_total" in entry["detail"]["keys"]


def test_viewer_can_read_policy_but_not_write(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _client()
    project_id = _make_project(admin)
    created = admin.post(
        "/admin/users",
        json={
            "username": "policy-viewer",
            "display_name": "只读同事",
            "password": "viewer-pass-1",
            "role": "viewer",
        },
    )
    assert created.status_code == 201
    member = admin.post(
        f"/projects/{project_id}/members",
        json={"username": "policy-viewer", "role": "viewer"},
    )
    assert member.status_code == 201

    viewer = TestClient(create_app())
    login = viewer.post(
        "/auth/login", json={"username": "policy-viewer", "password": "viewer-pass-1"}
    )
    assert login.status_code == 200
    assert _get_policy(viewer, project_id) == DEFAULT_POLICY
    assert _put_policy(viewer, project_id, {"max_total": 3}).status_code == 403


def test_plan_document_counts_embedded_images_and_over_total(db):
    client = _client()
    project_id = _make_project(client, "全文预算项目")
    _add_chapters(
        project_id,
        [
            (
                "视频监控系统",
                (
                    f"![拓扑图](/projects/{project_id}/images/a.png)\n\n"
                    f"![点位图](/projects/{project_id}/images/b.png)"
                ),
            ),
            ("机房工程", f"机柜示意 ![机柜图](/projects/{project_id}/images/c.png)"),
        ],
    )

    plan = client.get(f"/projects/{project_id}/images/plan").json()
    assert plan["policy"] == DEFAULT_POLICY
    document = plan["document"]
    assert document["used"] == 3
    assert document["max_total"] == 12
    assert document["remaining"] == 9
    assert document["over_total"] is False
    assert document["placeholders"] == 0

    assert _put_policy(client, project_id, {"max_total": 2}).status_code == 200
    document = client.get(f"/projects/{project_id}/images/plan").json()["document"]
    assert document["used"] == 3
    assert document["remaining"] == 0
    assert document["over_total"] is True

    assert _put_policy(client, project_id, {"max_total": 0}).status_code == 200
    document = client.get(f"/projects/{project_id}/images/plan").json()["document"]
    assert document["remaining"] is None
    assert document["over_total"] is False


def test_apply_plan_respects_whole_document_budget(db):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import Section

    client = _client()
    project_id = _make_project(client, "全文预算编排")
    _add_ready_assets(project_id, ["图甲", "图乙"])
    node_ids = _add_chapters(
        project_id,
        [
            ("视频监控系统", "{{图片占位:图甲}}"),
            ("机房工程", "{{图片占位:图乙}}"),
        ],
    )
    assert _put_policy(client, project_id, {"max_total": 1}).status_code == 200

    response = client.post(f"/projects/{project_id}/images/apply-plan")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["applied"] == 1
    assert result["skipped_over_total"] == 1
    assert result["skipped_over_budget"] == 0
    assert result["applied_labels"] == ["图甲"]
    assert result["policy"]["max_total"] == 1

    with session_scope() as session:
        first = session.scalar(select(Section).where(Section.node_id == node_ids[0]))
        second = session.scalar(select(Section).where(Section.node_id == node_ids[1]))
        assert "![图甲](" in first.body
        assert "{{图片占位:图乙}}" in second.body
        assert "![" not in second.body


def test_docx_caption_numbering_captions_off_and_width(db, tmp_path):
    from docx import Document
    from docx.shared import Cm

    from bidcraft.export.docx import export_docx

    image_a = tmp_path / "a.png"
    image_b = tmp_path / "b.png"
    image_a.write_bytes(PNG_1X1)
    image_b.write_bytes(PNG_1X1)
    files = {
        "/projects/p1/images/a.png": image_a,
        "/projects/p1/images/b.png": image_b,
    }
    nodes = [
        {"heading": "视频监控系统", "body": "![A](/projects/p1/images/a.png)"},
        {"heading": "机房工程", "body": "![B](/projects/p1/images/b.png)"},
    ]

    out = tmp_path / "numbered.docx"
    export_docx(
        out,
        None,
        nodes,
        include_cover=False,
        include_toc=False,
        image_resolver=lambda url: files.get(url),
        image_policy={"width_cm": 10.0, "captions": True, "caption_prefix": "图"},
    )
    document = Document(str(out))
    texts = [paragraph.text for paragraph in document.paragraphs]
    assert "图1\u3000A" in texts
    assert "图2\u3000B" in texts
    assert len(document.inline_shapes) == 2
    assert int(document.inline_shapes[0].width) == int(Cm(10))

    out_plain = tmp_path / "plain.docx"
    export_docx(
        out_plain,
        None,
        nodes,
        include_cover=False,
        include_toc=False,
        image_resolver=lambda url: files.get(url),
        image_policy={"captions": False},
    )
    plain = Document(str(out_plain))
    plain_texts = [paragraph.text for paragraph in plain.paragraphs]
    assert not any(text.startswith("图1") for text in plain_texts)
    assert "A" not in plain_texts and "B" not in plain_texts
    # Default width 15cm applies when the policy omits it.
    assert int(plain.inline_shapes[0].width) == int(Cm(15))


def test_batch_generation_uses_policy_style_and_size(db, monkeypatch):
    client = _client()
    project_id = _make_project(client, "批量策略默认")
    _configure_model(client)
    _add_chapters(project_id, [("视频监控系统", "{{图片占位:拓扑图}}")])
    assert (
        _put_policy(
            client,
            project_id,
            {"style": "工程示意", "size": "1280x720"},
        ).status_code
        == 200
    )

    server = FakeImageServer()
    _patch_http(monkeypatch, server)
    started = client.post(f"/projects/{project_id}/images/generate-missing", json={})
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]

    status: dict = {}
    for _ in range(120):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert status.get("status") == "done", status

    assert len(server.requests) == 1
    body = server.requests[0]
    assert body["size"] == "1280x720"
    assert "画面风格：工程示意" in body["prompt"]

    listed = client.get(f"/projects/{project_id}/images").json()
    assert listed[0]["status"] == "ready"
    assert listed[0]["options"]["style"] == "工程示意"
    assert listed[0]["options"]["size"] == "1280x720"


def test_batch_generation_default_policy_follows_model_size(db, monkeypatch):
    client = _client()
    project_id = _make_project(client, "批量默认策略")
    _configure_model(client, image_size="1792x1024")
    _add_chapters(project_id, [("视频监控系统", "{{图片占位:拓扑图}}")])

    server = FakeImageServer()
    _patch_http(monkeypatch, server)
    started = client.post(f"/projects/{project_id}/images/generate-missing", json={})
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]

    status: dict = {}
    for _ in range(120):
        status = client.get(f"/jobs/{job_id}").json()
        if status["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert status.get("status") == "done", status

    body = server.requests[0]
    assert body["size"] == "1792x1024"
    assert "画面风格：" not in body["prompt"]


def test_count_embedded_helper_and_policy_meta_preserved(db):
    from bidcraft import images
    from bidcraft.db import session_scope
    from bidcraft.models import Project

    client = _client()
    project_id = _make_project(client)
    _add_chapters(
        project_id,
        [("章节", f"![一](/projects/{project_id}/images/a.png)\n正文\n![二](/projects/{project_id}/images/b.png)")],
    )
    with session_scope() as session:
        project = session.get(Project, project_id)
        project.meta = {"source": "keep-me"}
        session.flush()
        assert images.count_embedded(session, project) == 2
        images.set_policy(session, project, {"caption_prefix": "附图"})
        assert project.meta["source"] == "keep-me"
        assert project.meta["image_policy"]["caption_prefix"] == "附图"
