from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import select  # noqa: E402


def _client(username: str = "docuser"):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={"username": username, "password": "doc-pass-1", "display_name": username},
    )
    assert response.status_code == 201, response.text
    return client, response.json()["user"]


def _upload(client, filename: str, content: bytes):
    return client.post("/my/docs", files={"file": (filename, content)})


def test_upload_rejects_suffix_and_size(db, monkeypatch):
    from bidcraft import user_docs

    client, _user = _client()
    bad = _upload(client, "payload.exe", b"MZ")
    assert bad.status_code == 400
    assert "仅支持" in bad.json()["detail"]

    monkeypatch.setattr(user_docs, "MAX_USER_DOC_BYTES", 4)
    oversized = _upload(client, "large.txt", b"12345")
    assert oversized.status_code == 400
    assert oversized.json()["detail"] == "文档不能超过 50MB"


def test_upload_creates_personal_kb_file_and_reindex_job(db, monkeypatch):
    from bidcraft import api
    from bidcraft.db import session_scope
    from bidcraft.models import Job, KnowledgeBase, UserDoc

    monkeypatch.setattr(api, "_run_kb_reindex_job", lambda job_id, kb_id: None)
    client, user = _client()
    response = _upload(client, "个人资料.txt", "Cat6A 布线测试记录".encode())
    assert response.status_code == 202, response.text
    body = response.json()
    doc = body["doc"]
    assert doc["filename"] == "个人资料.txt"
    assert doc["size"] == len("Cat6A 布线测试记录".encode())
    assert doc["status"] == "ready"
    assert doc["kb_id"]
    assert body["job_id"]

    with session_scope() as sess:
        row = sess.get(UserDoc, doc["id"])
        assert row is not None
        assert row.user_id == user["id"]
        from pathlib import Path

        path = Path(row.stored_path)
        assert path.is_file()
        kb = sess.get(KnowledgeBase, row.kb_id)
        assert kb.name == f"个人知识库-{user['id']}"
        assert kb.kind == "local"
        assert kb.provider == "local"
        assert kb.options == {"personal": True, "user_id": user["id"]}
        assert str(path.parent) == kb.location

        job = sess.get(Job, body["job_id"])
        assert job is not None
        assert job.kind == "kb_reindex"
        assert job.project_id is None
        assert job.payload["kb_id"] == kb.id


def test_list_download_delete_and_user_isolation(db, monkeypatch):
    from bidcraft import api
    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog, UserDoc

    monkeypatch.setattr(api, "_run_kb_reindex_job", lambda job_id, kb_id: None)
    owner, _owner_user = _client("docowner")
    other, _other_user = _client("docother")

    uploaded = _upload(owner, "报价说明.md", "苹果专属关键词".encode())
    assert uploaded.status_code == 202, uploaded.text
    doc_id = uploaded.json()["doc"]["id"]

    assert owner.get("/my/docs").json()[0]["id"] == doc_id
    assert other.get("/my/docs").json() == []

    downloaded = owner.get(f"/my/docs/{doc_id}/file")
    assert downloaded.status_code == 200
    assert downloaded.content == "苹果专属关键词".encode()

    assert other.get(f"/my/docs/{doc_id}/file").status_code == 404
    assert other.delete(f"/my/docs/{doc_id}").status_code == 404

    with session_scope() as sess:
        row = sess.get(UserDoc, doc_id)
        stored_path = row.stored_path

    deleted = owner.delete(f"/my/docs/{doc_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": doc_id}
    assert owner.get("/my/docs").json() == []

    from pathlib import Path

    assert not Path(stored_path).exists()
    with session_scope() as sess:
        assert sess.get(UserDoc, doc_id) is None
        actions = [
            row.action
            for row in sess.scalars(
                select(AuditLog).where(
                    AuditLog.action.in_(["user_doc.upload", "user_doc.delete"])
                )
            )
        ]
    assert actions.count("user_doc.upload") == 1
    assert actions.count("user_doc.delete") == 1


def test_search_excludes_personal_kb_by_default(db, monkeypatch):
    from bidcraft import api, knowledge, user_docs

    monkeypatch.setattr(api, "_run_kb_reindex_job", lambda job_id, kb_id: None)
    alpha, alpha_user = _client("docalpha")
    beta, beta_user = _client("docbeta")

    assert _upload(alpha, "alpha.txt", "阿尔法专属关键词".encode()).status_code == 202
    assert _upload(beta, "beta.txt", "贝塔专属关键词".encode()).status_code == 202

    from bidcraft.db import session_scope

    with session_scope() as sess:
        kb_alpha = user_docs.personal_kb(sess, alpha_user["id"])
        kb_beta = user_docs.personal_kb(sess, beta_user["id"])
        assert knowledge.index_local(sess, kb_alpha) == 1
        assert knowledge.index_local(sess, kb_beta) == 1

        assert knowledge.search(sess, "阿尔法专属关键词") == []
        assert knowledge.search(sess, "贝塔专属关键词") == []

        alpha_hits = knowledge.search(
            sess,
            "阿尔法专属关键词",
            kb_ids=[kb_alpha.id],
            include_personal=True,
        )
        assert alpha_hits
        assert all("贝塔" not in hit.text for hit in alpha_hits)
        assert all(hit.kb == kb_alpha.name for hit in alpha_hits)
