"""R18：正文修改使人工确认失效时，必须留存完整快照（Append-only 审计）。

正文一改，旧确认不能继续放行新正文（R15）；但「修改前系统为什么认为满足、
谁确认的、依据哪一版正文」必须仍可追溯。本文件验证 R18 的快照机制：
- 原始自动判定 / 人工确认 / 正文版本 / 操作人写入独立审计行；
- 再次确认产生新快照，不覆盖旧快照；
- 与正文保存同事务（失败整体回滚）；
- 数据隔离与审计读取权限。
"""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "snap-pass-1"
OLD_BODY = "旧正文：系统带宽1000Mbps，质保5年。"
NEW_BODY = "新正文：系统带宽500Mbps，质保3年。"


def _login(username, password=PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture()
def actors(db):
    admin = _login(*ADMIN)
    for username in ("snap-owner", "snap-editor", "snap-viewer", "snap-outsider"):
        r = admin.post(
            "/admin/users",
            json={
                "username": username,
                "display_name": username,
                "password": PASSWORD,
                "role": "manager",
            },
        )
        assert r.status_code == 201, r.text

    owner = _login("snap-owner")
    p = owner.post("/projects", json={"name": "快照项目", "package": "weakcurrent-cabling"})
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    for username, role in (
        ("snap-editor", "editor"),
        ("snap-viewer", "viewer"),
    ):
        r = owner.post(f"/projects/{pid}/members", json={"username": username, "role": role})
        assert r.status_code in {200, 201}, r.text
    return {
        "admin": admin,
        "owner": owner,
        "editor": _login("snap-editor"),
        "viewer": _login("snap-viewer"),
        "outsider": _login("snap-outsider"),
        "project_id": pid,
    }


def _seed_confirmed(project_id: str, *, body=OLD_BODY, responded_by="确认人甲"):
    """造一个已人工确认的 ★ 条款 + 绑定章节，返回 (node_id, req_id)。"""
    from datetime import datetime

    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Requirement, Section

    with session_scope() as session:
        node = OutlineNode(project_id=project_id, heading="技术方案", position=1)
        session.add(node)
        session.flush()
        session.add(
            Section(
                node_id=node.id,
                body=body,
                status="written",
                words=len(body),
                review_status="draft",
                revision=3,
            )
        )
        req = Requirement(
            project_id=project_id,
            kind="substantive",
            text="★ 带宽不小于1000Mbps且质保5年",
            mandatory=True,
            source_quote="带宽不小于1000Mbps且质保5年",
            marker="★",
            page=12,
            clause_no="3.2.1",
            response_status="responded",
            response_note="已核对招标要求，确认满足",
            responded_by=responded_by,
            responded_at=datetime(2026, 11, 20, 10, 0, 0),
            response_verdict="satisfied",
            response_verdict_label="满足",
            response_verdict_reason="正文写明带宽1000Mbps、质保5年",
            node_id=node.id,
            auto_covered=True,
        )
        session.add(req)
        session.flush()
        return node.id, req.id


def _snapshots(project_id: str) -> list[dict]:
    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    with session_scope() as db:
        rows = (
            db.query(AuditLog)
            .filter_by(project_id=project_id, action="requirement.confirmation_invalidated")
            .order_by(AuditLog.id.asc())
            .all()
        )
        return [dict(row.detail or {}) for row in rows]


def test_chapter_edit_preserves_original_auto_verdict(actors):
    node_id, req_id = _seed_confirmed(actors["project_id"])
    saved = actors["owner"].put(
        f"/projects/{actors['project_id']}/chapters/{node_id}", json={"body": NEW_BODY}
    )
    assert saved.status_code == 200, saved.text
    snaps = _snapshots(actors["project_id"])
    assert len(snaps) == 1
    s = snaps[0]
    assert s["requirement_id"] == req_id
    assert s["original_auto_verdict"] == "satisfied"
    assert s["original_verdict_label"] == "满足"
    assert s["original_verdict_reason"] == "正文写明带宽1000Mbps、质保5年"


def test_chapter_edit_preserves_manual_confirmation_snapshot(actors):
    node_id, _ = _seed_confirmed(actors["project_id"])
    actors["owner"].put(
        f"/projects/{actors['project_id']}/chapters/{node_id}", json={"body": NEW_BODY}
    )
    s = _snapshots(actors["project_id"])[0]
    assert s["original_response_status"] == "responded"
    assert s["original_response_note"] == "已核对招标要求，确认满足"
    assert s["confirmed_by"] == "确认人甲"
    assert s["confirmed_at"] and "+08:00" in s["confirmed_at"]
    assert s["original_verdict_evidence"] == {
        "source_quote": "带宽不小于1000Mbps且质保5年",
        "page": 12,
        "clause_no": "3.2.1",
    }


def test_confirmation_snapshot_contains_body_revision(actors):
    node_id, _ = _seed_confirmed(actors["project_id"])
    actors["owner"].put(
        f"/projects/{actors['project_id']}/chapters/{node_id}", json={"body": NEW_BODY}
    )
    s = _snapshots(actors["project_id"])[0]
    assert s["section_revision"] == 3  # 改动前的修订号
    assert s["body_hash"] == hashlib.sha256(OLD_BODY.encode("utf-8")).hexdigest()
    assert s["node_id"] == node_id


def test_confirmation_snapshot_contains_operator(actors):
    node_id, _ = _seed_confirmed(actors["project_id"])
    # editor 改正文（有写权限），快照操作人应为 editor。
    actors["editor"].put(
        f"/projects/{actors['project_id']}/chapters/{node_id}", json={"body": NEW_BODY}
    )
    s = _snapshots(actors["project_id"])[0]
    assert s["invalidated_by"] == "snap-editor"
    assert s["invalidated_at"] and "+08:00" in s["invalidated_at"]
    assert s["invalidation_reason"]


def test_reconfirmation_creates_new_snapshot_not_overwrite(actors):
    node_id, req_id = _seed_confirmed(actors["project_id"])
    pid = actors["project_id"]
    actors["owner"].put(f"/projects/{pid}/chapters/{node_id}", json={"body": NEW_BODY})
    first = _snapshots(pid)[0]

    # 重新确认（owner 可确认 ★）后再次改正文 → 应新增第二条快照，第一条不变。
    r = actors["owner"].patch(
        f"/projects/{pid}/requirements/{req_id}",
        json={"response_status": "responded", "note": "按新正文重新确认"},
    )
    assert r.status_code == 200, r.text
    # 重新确认后，自动匹配（check_project）会再次把条款绑定到本章；测试里模拟这一步。
    from bidcraft.db import session_scope
    from bidcraft.models import Requirement

    with session_scope() as session:
        row = session.get(Requirement, req_id)
        row.node_id = node_id
        row.auto_covered = True
    actors["owner"].put(
        f"/projects/{pid}/chapters/{node_id}", json={"body": NEW_BODY + " 再次调整。"}
    )
    snaps = _snapshots(pid)
    assert len(snaps) == 2
    assert snaps[0] == first, "旧快照不得被覆盖"


def test_failed_chapter_save_rolls_back_snapshot(actors, monkeypatch):
    import importlib

    modules_mod = importlib.import_module("bidcraft.api.routes.projects")
    node_id, _ = _seed_confirmed(actors["project_id"])
    before = _snapshots(actors["project_id"])

    real_apply = modules_mod._apply_requirement_status

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟确认失效写入失败")

    monkeypatch.setattr(modules_mod, "_apply_requirement_status", _boom)
    # TestClient 默认向上传播服务端异常；用 raises 捕获后验证事务回滚。
    with pytest.raises(RuntimeError, match="模拟确认失效写入失败"):
        actors["owner"].put(
            f"/projects/{actors['project_id']}/chapters/{node_id}", json={"body": NEW_BODY}
        )
    monkeypatch.setattr(modules_mod, "_apply_requirement_status", real_apply)
    assert _snapshots(actors["project_id"]) == before, "失败不应留下快照审计"


def test_snapshot_isolated_between_projects(actors):
    node_id, _ = _seed_confirmed(actors["project_id"])
    other = actors["owner"].post(
        "/projects", json={"name": "另一个项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    actors["owner"].put(
        f"/projects/{actors['project_id']}/chapters/{node_id}", json={"body": NEW_BODY}
    )
    assert len(_snapshots(actors["project_id"])) == 1
    assert _snapshots(other) == []


def test_viewer_can_read_allowed_audit_snapshot(actors):
    node_id, _ = _seed_confirmed(actors["project_id"])
    actors["owner"].put(
        f"/projects/{actors['project_id']}/chapters/{node_id}", json={"body": NEW_BODY}
    )
    r = actors["viewer"].get(f"/projects/{actors['project_id']}/audit")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    snap = [i for i in items if i["action"] == "requirement.confirmation_invalidated"]
    assert snap and snap[0]["detail"]["original_auto_verdict"] == "satisfied"


def test_unauthorized_user_cannot_read_snapshot(actors):
    node_id, _ = _seed_confirmed(actors["project_id"])
    actors["owner"].put(
        f"/projects/{actors['project_id']}/chapters/{node_id}", json={"body": NEW_BODY}
    )
    # 非项目成员读取项目审计应被拒（404 隐藏项目存在性）。
    r = actors["outsider"].get(f"/projects/{actors['project_id']}/audit")
    assert r.status_code in {403, 404}
