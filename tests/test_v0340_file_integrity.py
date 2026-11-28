"""v0.34.0 P1：文件完整性、上传原子性与证据生命周期（真实文件 / 真实 DB）。

所有用例都走真实 SQLite、真实目录与 FastAPI TestClient；不 mock 文件写入、
不 mock 数据库事务，只在 I/O 边界注入一次失败以验证不留孤儿。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

PNG = b"\x89PNG\r\n\x1a\n" + b"v0340-evidence-payload"


def _client(username: str, *, raise_server_exceptions: bool = True):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(
        create_app(), raise_server_exceptions=raise_server_exceptions
    )
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "password": "v0340-pass-1",
            "display_name": username,
            "accept_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    return client, response.json()["user"]


def _checksum(db, doc_id: str) -> str:
    from sqlalchemy import text

    return str(
        db.execute(text("SELECT checksum FROM user_docs WHERE id = :id"), {"id": doc_id}).scalar()
        or ""
    )


def test_add_source_rollback_removes_staged_snapshot(db, tmp_path):
    """同事务解析可读暂存文件；回滚后项目目录不留孤儿快照。"""

    from sqlalchemy import select

    from bidcraft import pipeline
    from bidcraft.config import settings
    from bidcraft.db import session_scope
    from bidcraft.models import Project, SourceFile

    with session_scope() as sess:
        project = Project(name="暂存解析项目")
        sess.add(project)
    project_id = project.id
    source_path = tmp_path / "tender.txt"
    payload = "招标文件正文：Cat6A 布线".encode()
    source_path.write_bytes(payload)

    with pytest.raises(RuntimeError), session_scope() as sess:
        project = sess.get(Project, project_id)
        pipeline.add_source(sess, project, source_path)
        _package, specs = pipeline.read_parse_inputs(sess, project_id)
        assert Path(specs[0]["stored_path"]).read_bytes() == payload
        raise RuntimeError("injected DB failure")

    root = settings.project_dir(project_id) / "sources"
    assert not root.exists() or not list(root.iterdir())
    with session_scope() as sess:
        assert not list(sess.scalars(select(SourceFile)))


def test_user_doc_upload_backpressure_leaves_no_orphan(db, monkeypatch):
    import importlib

    from bidcraft import user_docs
    from bidcraft.db import session_scope
    from bidcraft.models import KnowledgeBase, UserDoc

    my_routes = importlib.import_module("bidcraft.api.routes.my")
    monkeypatch.setattr(
        my_routes.jobqueue,
        "check_backpressure",
        lambda _db, _uid: "队列已满，请稍后重试",
    )
    client, user = _client("v0340-orphan-doc")
    response = client.post(
        "/my/docs", files={"file": ("原子性.txt", b"no-orphan-payload")}
    )
    assert response.status_code == 429, response.text

    from sqlalchemy import select

    root = user_docs.user_dir(user["id"])
    assert not root.exists() or not list(root.iterdir())
    with session_scope() as sess:
        assert not list(
            sess.scalars(select(UserDoc).where(UserDoc.user_id == user["id"]))
        )
        assert not list(
            sess.scalars(
                select(KnowledgeBase).where(
                    KnowledgeBase.name == f"个人知识库-{user['id']}"
                )
            )
        )


def test_evidence_upload_failure_leaves_no_orphan(db, monkeypatch):
    import importlib

    from bidcraft import qualifications
    from bidcraft.db import session_scope
    from bidcraft.models import Qualification

    my_routes = importlib.import_module("bidcraft.api.routes.my")
    client, user = _client("v0340-orphan-evidence", raise_server_exceptions=False)
    created = client.post(
        "/my/qualifications",
        json={"kind": "license", "name": "原子性证书"},
    )
    assert created.status_code == 201, created.text
    qid = created.json()["id"]

    def _boom(db, project_id, action, detail=None):  # noqa: ANN001, ARG001
        if action == "qualification.evidence":
            raise RuntimeError("injected audit failure")

    monkeypatch.setattr(my_routes, "audit", _boom)
    response = client.post(
        f"/my/qualifications/{qid}/evidence",
        files={"file": ("原子.png", PNG)},
    )
    assert response.status_code == 500

    root = qualifications.evidence_dir(user["id"])
    assert not list(root.iterdir())
    with session_scope() as sess:
        row = sess.get(Qualification, qid)
        assert row is not None and not row.evidence


def test_magic_whitelist_rejects_disguised_uploads(db):
    client, _user = _client("v0340-magic")

    disguised = client.post(
        "/my/docs", files={"file": ("伪装.pdf", b"MZ\x90\x00PE\x00\x00")}
    )
    assert disguised.status_code == 400, disguised.text
    assert "魔数" in disguised.json()["detail"] or "伪装" in disguised.json()["detail"]

    script = client.post(
        "/my/docs", files={"file": ("脚本.txt", b"#!/bin/sh\necho hijack\n")}
    )
    assert script.status_code == 400
    assert "脚本.txt" not in client.get("/my/docs").text

    ok = client.post(
        "/my/docs", files={"file": ("说明.txt", "正常中文文本".encode())}
    )
    assert ok.status_code == 202, ok.text
    assert ok.json()["doc"]["filename"] == "说明.txt"


def test_user_doc_checksum_is_stored_and_verified_on_download(db):
    from bidcraft.db import session_scope
    from bidcraft.models import UserDoc

    client, _user = _client("v0340-user-checksum")
    content = b"checksum-verified-payload"
    uploaded = client.post("/my/docs", files={"file": ("校验.txt", content)})
    assert uploaded.status_code == 202, uploaded.text
    doc_id = uploaded.json()["doc"]["id"]

    with session_scope() as sess:
        row = sess.get(UserDoc, doc_id)
        stored = Path(row.stored_path)
        expected = hashlib.sha256(content).hexdigest()
        assert _checksum(sess, doc_id) == expected
        assert stored.read_bytes() == content

    assert client.get(f"/my/docs/{doc_id}/file").content == content

    stored.write_bytes(b"tampered-by-attacker")
    rejected = client.get(f"/my/docs/{doc_id}/file")
    assert rejected.status_code == 404, rejected.text
    with session_scope() as sess:
        assert sess.get(UserDoc, doc_id) is not None


def test_qualification_evidence_checksum_is_stored_and_verified_on_download(db):
    from bidcraft.db import session_scope
    from bidcraft.models import Qualification

    client, _user = _client("v0340-evidence-checksum")
    created = client.post(
        "/my/qualifications", json={"kind": "license", "name": "校验用证书"}
    )
    assert created.status_code == 201, created.text
    qid = created.json()["id"]

    upload = client.post(
        f"/my/qualifications/{qid}/evidence",
        files={"file": ("校验.png", PNG)},
    )
    assert upload.status_code == 201, upload.text
    entry = upload.json()["evidence"][0]
    assert entry["sha256"] == hashlib.sha256(PNG).hexdigest()
    stored = Path(entry["path"])

    assert (
        client.get(f"/my/qualifications/{qid}/evidence/{entry['filename']}").content == PNG
    )
    stored.write_bytes(b"tampered-evidence")
    rejected = client.get(f"/my/qualifications/{qid}/evidence/{entry['filename']}")
    assert rejected.status_code == 404, rejected.text
    with session_scope() as sess:
        assert sess.get(Qualification, qid) is not None


def test_evidence_with_executable_magic_is_rejected(db):
    client, _user = _client("v0340-evidence-magic")
    created = client.post(
        "/my/qualifications", json={"kind": "license", "name": "伪装证据证书"}
    )
    assert created.status_code == 201, created.text
    qid = created.json()["id"]

    rejected = client.post(
        f"/my/qualifications/{qid}/evidence",
        files={"file": ("木马.png", b"MZ\x90\x00fake-exe")},
    )
    assert rejected.status_code == 400, rejected.text
    assert client.get("/my/qualifications").json()[0]["evidence_count"] == 0


def test_shared_evidence_transfer_and_user_deletion_move_or_delete_files(db):
    """共享证据随 owner 变更搬家；账号注销时私有证据文件必须清空。"""

    from fastapi.testclient import TestClient

    from bidcraft.api import create_app
    from bidcraft.compliance import delete_account
    from bidcraft.db import session_scope
    from bidcraft.models import Enterprise, Qualification, User
    from bidcraft.qualifications import evidence_dir

    admin = TestClient(create_app())
    assert (
        admin.post(
            "/auth/login", json={"username": "admin", "password": "test-admin"}
        ).status_code
        == 200
    )
    owner = admin.post(
        "/admin/users",
        json={
            "username": "v0340-ent-owner",
            "password": "v0340-pass-1",
            "role": "manager",
        },
    ).json()
    member = admin.post(
        "/admin/users",
        json={
            "username": "v0340-ent-member",
            "password": "v0340-pass-1",
            "role": "manager",
        },
    ).json()

    owner_client = TestClient(create_app())
    assert (
        owner_client.post(
            "/auth/login",
            json={"username": owner["username"], "password": "v0340-pass-1"},
        ).status_code
        == 200
    )
    member_client = TestClient(create_app())
    assert (
        member_client.post(
            "/auth/login",
            json={"username": member["username"], "password": "v0340-pass-1"},
        ).status_code
        == 200
    )

    created = owner_client.post("/enterprise", json={"name": "证据移交企业"})
    assert created.status_code == 201, created.text
    eid = created.json()["id"]
    added = owner_client.post(
        f"/enterprise/{eid}/members",
        json={"username": member["username"], "role": "member"},
    )
    assert added.status_code == 201, added.text

    from sqlalchemy import select

    with session_scope() as sess:
        sess.add(
            Qualification(
                owner_id=member["id"],
                enterprise_id=eid,
                kind="license",
                name="共享移交证书",
            )
        )
    with session_scope() as sess:
        row = sess.scalar(
            select(Qualification).where(Qualification.name == "共享移交证书")
        )
        qid = row.id

    uploaded = member_client.post(
        f"/my/qualifications/{qid}/evidence",
        files={"file": ("共享证据.png", PNG)},
    )
    assert uploaded.status_code == 201, uploaded.text
    entry = uploaded.json()["evidence"][0]
    old_path = Path(entry["path"])
    assert old_path.is_file() and old_path.parent == evidence_dir(member["id"])

    with session_scope() as sess:
        user = sess.get(User, member["id"])
        removed = delete_account(sess, user)

    with session_scope() as sess:
        moved = sess.get(Qualification, qid)
        assert moved is not None
        assert moved.owner_id == owner["id"]
        new_entry = moved.evidence[0]
        new_path = Path(new_entry["path"])
        assert new_path.is_file()
        assert new_path.parent == evidence_dir(owner["id"])
        assert new_path.read_bytes() == PNG
        assert new_entry["sha256"] == entry["sha256"]
    assert not old_path.exists()
    assert removed["assets_transferred"] >= 1

    # 私有证据随注销一起删除。
    created = owner_client.post(
        "/my/qualifications", json={"kind": "license", "name": "私有待删证书"}
    )
    assert created.status_code == 201, created.text
    private_id = created.json()["id"]
    private_upload = owner_client.post(
        f"/my/qualifications/{private_id}/evidence",
        files={"file": ("私有证据.png", PNG)},
    )
    assert private_upload.status_code == 201, private_upload.text
    private_path = Path(private_upload.json()["evidence"][0]["path"])
    assert private_path.is_file()

    with session_scope() as sess:
        user = sess.get(User, owner["id"])
        delete_account(sess, user)
    assert not private_path.exists()
    with session_scope() as sess:
        assert sess.get(Enterprise, eid) is None
