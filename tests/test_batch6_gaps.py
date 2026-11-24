"""Batch 6：知识库生命周期/溯源、查重重排与异步任务的回归测试。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

pytest.importorskip("fastapi")


def _make_kb(db, name: str, location: str = ""):
    from bidcraft.models import KnowledgeBase

    row = KnowledgeBase(name=name, kind="local", provider="local", location=location)
    db.add(row)
    db.flush()
    return row


def _login():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return client


def _make_project(client, name: str) -> str:
    response = client.post("/projects", json={"name": name, "package": "weakcurrent-cabling"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --------------------------------------------------------------- P1-1 生命周期


def test_search_local_and_trust_gate_enforce_document_lifecycle(db):
    from bidcraft import material_policy
    from bidcraft.db import session_scope
    from bidcraft.knowledge import Passage, ingest_document, search_local
    from bidcraft.models import KbDocument

    with session_scope() as session:
        kb = _make_kb(session, "生命周期库")
        valid = ingest_document(
            session, kb, "valid.md", "资质证书正文 甲乙丙丁", kind="qualification"
        )
        expired = ingest_document(
            session,
            kb,
            "expired.md",
            "过期资质证书正文 戊己庚辛",
            kind="qualification",
            valid_to="2000-01-01",
        )
        unready = ingest_document(
            session, kb, "unready.md", "未就绪证书正文 壬癸甲乙", kind="qualification"
        )
        unready.status = "indexing"
        material_policy.set_meta(
            session, kb, "expired.md", {"kind": "qualification", "cert_no": "D-OLD", "valid_to": "2099-01-01"}
        )
        material_policy.set_meta(
            session, kb, "unready.md", {"kind": "qualification", "cert_no": "D-NEW", "valid_to": "2099-01-01"}
        )
        session.flush()

        passages = search_local(session, kb, "资质证书", 10)
        sources = {item.source for item in passages}
        assert "valid.md" in sources
        assert "expired.md" not in sources
        assert "unready.md" not in sources

        # 即使片段由旧调用方构造，信任分级也按 KbDocument 的权威元数据降级。
        expired_passage = Passage(
            kb=kb.name,
            kb_id=kb.id,
            source="expired.md",
            text="过期正文不应注入",
            score=0.9,
            document_id=expired.id,
        )
        block, downgraded = material_policy.context_block(session, [expired_passage])
        assert downgraded == 1
        assert "过期正文不应注入" not in block
        assert "已过期" in block

        unready_passage = Passage(
            kb=kb.name,
            kb_id=kb.id,
            source="unready.md",
            text="未就绪正文不应注入",
            score=0.9,
            document_id=unready.id,
        )
        block, downgraded = material_policy.context_block(session, [unready_passage])
        assert downgraded == 1
        assert "未就绪正文不应注入" not in block
        assert "尚未就绪" in block

        _, meta = material_policy.find_meta(
            session, "expired.md", kb_id=kb.id, document_id=expired.id
        )
        assert meta["kind"] == "qualification"
        assert meta["valid_to"] == "2000-01-01"  # KbDocument 覆盖 legacy 的未来日期
        assert not material_policy.assess(meta).ok
        assert session.get(KbDocument, valid.id).status == "ready"


def test_find_meta_uses_kb_id_to_disambiguate_same_source(db):
    from bidcraft import material_policy
    from bidcraft.db import session_scope
    from bidcraft.knowledge import ingest_document

    with session_scope() as session:
        kb_a = _make_kb(session, "库A")
        kb_b = _make_kb(session, "库B")
        doc_a = ingest_document(session, kb_a, "同名.md", "内容A", kind="qualification")
        doc_b = ingest_document(session, kb_b, "同名.md", "内容B", kind="performance")
        material_policy.set_meta(
            session,
            kb_a,
            "同名.md",
            {"kind": "qualification", "cert_no": "A1", "valid_to": "2099-01-01"},
        )
        material_policy.set_meta(
            session,
            kb_b,
            "同名.md",
            {"kind": "performance", "contract_no": "B1", "owner": "业主", "amount": "1", "acceptance": "合格"},
        )
        session.flush()

        kb_a_found, meta_a = material_policy.find_meta(session, "同名.md", kb_id=kb_a.id)
        kb_b_found, meta_b = material_policy.find_meta(session, "同名.md", kb_id=kb_b.id)
        assert kb_a_found.id == kb_a.id and meta_a["document_id"] == doc_a.id
        assert kb_b_found.id == kb_b.id and meta_b["document_id"] == doc_b.id
        assert material_policy.assess(meta_a).ok
        assert material_policy.assess(meta_b).ok
        assert meta_a["cert_no"] == "A1" and meta_b["contract_no"] == "B1"


# --------------------------------------------------------------- P1-2 页码溯源


def _write_two_page_pdf(path) -> None:
    """无额外依赖的两页 PDF（pypdf 可提取文字）。"""

    streams = {
        6: b"BT /F1 14 Tf 72 700 Td (Page one code ZB001) Tj ET",
        7: b"BT /F1 14 Tf 72 700 Td (Page two code ZB002) Tj ET",
    }
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 6 0 R >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        5: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 7 0 R >>",
        6: b"<< /Length " + str(len(streams[6])).encode() + b" >>\nstream\n" + streams[6] + b"\nendstream",
        7: b"<< /Length " + str(len(streams[7])).encode() + b" >>\nstream\n" + streams[7] + b"\nendstream",
    }
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for number in sorted(objects):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(out)


def test_two_page_pdf_keeps_page_numbers_and_trace(db, tmp_path):
    from bidcraft import material_policy
    from bidcraft.db import session_scope
    from bidcraft.knowledge import context_block, index_local, search
    from bidcraft.models import KbChunk, KbDocument
    from bidcraft.parsing.loader import load_document

    _write_two_page_pdf(tmp_path / "two.pdf")
    parsed = load_document(tmp_path / "two.pdf")
    assert len(parsed.pages) == 2  # loader 保留真实页数组

    with session_scope() as session:
        kb = _make_kb(session, "PDF 库", str(tmp_path))
        index_local(session, kb)
        doc = session.scalar(
            select(KbDocument).where(KbDocument.kb_id == kb.id)
        )
        assert doc is not None and doc.pages == 2
        chunks = list(
            session.scalars(select(KbChunk).where(KbChunk.kb_id == kb.id))
        )
        assert {chunk.page_no for chunk in chunks} == {1, 2}

        passages = search(session, "ZB002", limit=5)
        assert passages
        assert passages[0].page == 2
        assert passages[0].document_id == doc.id
        block = context_block(passages)
        assert f"文档 {doc.id}" in block and "页码 2" in block

        trust_block, downgraded = material_policy.context_block(session, passages)
        assert downgraded >= 1
        assert "页码 2" in trust_block


# --------------------------------------------------------------- P2-3/4/5 查重


def test_hit_payload_exposes_evidence_and_rerank_order(db, monkeypatch):
    from bidcraft.db import session_scope
    from bidcraft.similarity import service

    def _fake_run(db_session, project_id, source_project_id, threshold):
        return {
            "hits": [
                {
                    "kind": "semantic",
                    "source_a": "低原文分",
                    "source_b": "B",
                    "similarity": 0.5,
                    "evidence": {
                        "rerank_score": 0.93,
                        "vector_similarity": 0.99,
                        "reasonable_duplicate": True,
                        "degraded": False,
                    },
                },
                {
                    "kind": "text",
                    "source_a": "高原文分",
                    "source_b": "B",
                    "similarity": 0.8,
                    "evidence": {"rerank_score": 0.71, "common": False},
                },
            ],
            "stats": {"hits": 2},
            "skipped": [],
        }

    monkeypatch.setitem(service._RUNNERS, "text", _fake_run)
    client = _login()
    project_id = _make_project(client, "重排测试")
    response = client.post(
        f"/projects/{project_id}/similarity/check",
        json={"mode": "text", "threshold": 0.5},
    )
    assert response.status_code == 202, response.text
    report_id = response.json()["report_id"]

    hits = client.get(f"/projects/{project_id}/similarity/reports/{report_id}/hits").json()
    assert [item["source_a"] for item in hits] == ["低原文分", "高原文分"]
    assert hits[0]["rerank_score"] == pytest.approx(0.93)
    assert hits[0]["evidence"]["reasonable_duplicate"] is True
    assert hits[0]["evidence"]["vector_similarity"] == pytest.approx(0.99)
    from bidcraft.models import SimilarityReport

    with session_scope() as session:
        report = session.get(SimilarityReport, report_id)
        assert report.completed_at is not None


def test_similarity_check_enqueues_job_without_blocking(db, monkeypatch):
    from bidcraft import jobqueue
    from bidcraft.db import session_scope
    from bidcraft.models import Job
    from bidcraft.workers.similarity import _run_similarity_job

    monkeypatch.setattr(jobqueue, "workers_running", lambda: True)
    monkeypatch.setattr(jobqueue, "submit", lambda **kwargs: None)
    client = _login()
    project_id = _make_project(client, "异步查重")
    response = client.post(
        f"/projects/{project_id}/similarity/check",
        json={"mode": "text", "threshold": 0.8},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued" and body["job_id"]
    report_id = body["report_id"]

    with session_scope() as session:
        job = session.get(Job, body["job_id"])
        assert job.kind == "similarity"
        assert job.payload["report_id"] == report_id
        job.claim_token = "batch6-test-token"
        session.flush()

    with jobqueue.claim_context("batch6-test-token"):
        _run_similarity_job(body["job_id"])

    report = client.get(f"/projects/{project_id}/similarity/reports/{report_id}").json()
    assert report["status"] == "done"
    with session_scope() as session:
        job = session.get(Job, body["job_id"])
        assert job.status == "done" and job.result["report_id"] == report_id


# --------------------------------------------------------------- P2-6/P3-7/8


def test_search_local_uses_sql_prefilter_and_bounded_limit(db):
    from sqlalchemy import event

    from bidcraft.db import get_engine, session_scope
    from bidcraft.knowledge import ingest_document, search_local

    with session_scope() as session:
        kb = _make_kb(session, "预筛库")
        ingest_document(session, kb, "a.md", "智能布线系统实施规范", kind="other")
        ingest_document(session, kb, "b.md", "无关内容", kind="other")
        session.flush()

        statements: list[str] = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            hits = search_local(session, kb, "智能布线", 3)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert hits and hits[0].source == "a.md"
        chunk_sql = [sql for sql in statements if "kb_chunks" in sql]
        assert chunk_sql, statements
        assert any("LIKE" in sql.upper() and "LIMIT" in sql.upper() for sql in chunk_sql)
        assert any("kb_documents" in sql for sql in chunk_sql)


def test_admin_kb_lifecycle_cleans_documents_and_exposes_preview(db, tmp_path):
    from sqlalchemy import func, select

    from bidcraft.db import session_scope
    from bidcraft.knowledge import ingest_document
    from bidcraft.models import KbChunk, KbDocument, KnowledgeBase

    root = tmp_path / "kb"
    root.mkdir()
    (root / "doc.md").write_text("智能布线系统实施规范", encoding="utf-8")
    client = _login()
    created = client.post(
        "/admin/kbs",
        json={
            "name": "管理测试库",
            "kind": "local",
            "provider": "local",
            "location": str(root),
            "options": {},
            "enabled": True,
            "is_default": False,
        },
    )
    assert created.status_code == 201, created.text
    kb_id = created.json()["id"]
    with session_scope() as session:
        kb = session.get(KnowledgeBase, kb_id)
        ingest_document(session, kb, "doc.md", "智能布线系统实施规范", kind="other")

    documents = client.get(f"/admin/kbs/{kb_id}/documents")
    assert documents.status_code == 200, documents.text
    assert documents.json()["items"][0]["source"] == "doc.md"

    preview = client.get(f"/admin/kbs/{kb_id}/preview", params={"q": "智能布线"})
    assert preview.status_code == 200, preview.text
    item = preview.json()["items"][0]
    assert item["document_id"] and item["kb_id"] == kb_id
    assert item["source"] == "doc.md"

    patched = client.patch(f"/admin/kbs/{kb_id}", json={"location": str(tmp_path / "moved")})
    assert patched.status_code == 200, patched.text
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(KbDocument).where(KbDocument.kb_id == kb_id)) == 0
        assert session.scalar(select(func.count()).select_from(KbChunk).where(KbChunk.kb_id == kb_id)) == 0

    deleted = client.delete(f"/admin/kbs/{kb_id}")
    assert deleted.status_code == 200, deleted.text
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(KbDocument).where(KbDocument.kb_id == kb_id)) == 0
