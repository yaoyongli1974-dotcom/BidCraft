"""KbDocument 增量索引、文档级元数据与联合检索（Lane A）。"""

from __future__ import annotations

from pathlib import Path


def _make_kb(db, name: str = "企业库") -> str:
    from bidcraft.models import KnowledgeBase

    row = KnowledgeBase(name=name, kind="local", provider="local", location="")
    db.add(row)
    db.flush()
    return row.id


def test_ingest_document_is_incremental_and_versions_on_change(db):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.knowledge import (
        delete_document,
        document_payload,
        ingest_document,
        list_documents,
    )
    from bidcraft.models import KbChunk, KnowledgeBase

    content = "# 安全生产许可证\n\n证书编号：AQ-001，有效期至 2030-01-01。"
    with session_scope() as session:
        kb = session.get(KnowledgeBase, _make_kb(session))
        first = ingest_document(
            session, kb, "safety.md", content, kind="license", valid_to="2030-01-01"
        )
        first_id = first.id
        assert first.version == 1
        assert first.pages == 1
        assert kb.chunk_count == 1
        chunk_ids = list(
            session.scalars(
                select(KbChunk.id).where(KbChunk.document_id == first_id)
            )
        )
        chunk = session.scalar(select(KbChunk).where(KbChunk.document_id == first_id))
        assert chunk.page_no == 1
        assert chunk.chunk_index == 0
        assert chunk.hash
        assert chunk.heading.startswith("安全生产许可证")

    with session_scope() as session:
        kb = session.get(KnowledgeBase, kb.id)
        same = ingest_document(session, kb, "safety.md", content, kind="license")
        assert same.id == first_id
        assert same.version == 1
        # 同 sha256 不重建：块 id 与数量保持不变，向量缓存不会被清掉。
        assert list(
            session.scalars(
                select(KbChunk.id).where(KbChunk.document_id == first_id)
            )
        ) == chunk_ids
        assert kb.chunk_count == 1

    changed = content + "\n\n补充：新增年检记录。"
    with session_scope() as session:
        kb = session.get(KnowledgeBase, kb.id)
        updated = ingest_document(session, kb, "safety.md", changed, kind="license")
        assert updated.id == first_id
        assert updated.version == 2
        assert session.get(KbChunk, chunk_ids[0]) is None
        new_chunks = list(
            session.scalars(
                select(KbChunk).where(KbChunk.document_id == first_id)
            )
        )
        assert new_chunks and new_chunks[0].id != chunk_ids[0]
        assert kb.chunk_count == len(new_chunks)
        assert len(list_documents(session, kb)) == 1
        payload = document_payload(updated)
        assert payload["version"] == 2
        assert payload["kind"] == "license"

    with session_scope() as session:
        kb = session.get(KnowledgeBase, kb.id)
        delete_document(session, kb, first_id)
        assert session.get(type(updated), first_id) is None
        assert list(
            session.scalars(select(KbChunk).where(KbChunk.document_id == first_id))
        ) == []
        assert kb.chunk_count == 0


def test_document_pages_and_search_passage_metadata(db):
    from bidcraft.db import session_scope
    from bidcraft.knowledge import ingest_document, search
    from bidcraft.models import KnowledgeBase

    text = (
        "# 第一页\n\n电子与智能化工程专业承包二级资质说明。"
        "\f# 第二页\n\n安全生产许可证与项目负责人证书。"
    )
    with session_scope() as session:
        kb = session.get(KnowledgeBase, _make_kb(session, "分页库"))
        doc = ingest_document(session, kb, "分页.md", text, kind="license")
        assert doc.pages == 2
        passages = search(session, "电子与智能化工程专业承包二级资质", limit=5)
    assert passages
    passage = passages[0]
    assert passage.source == "分页.md"
    assert passage.document_id == doc.id
    assert passage.page in (1, 2)
    assert passage.heading


def test_joint_search_keeps_global_and_personal_bases(db, tmp_path):
    from bidcraft.db import session_scope
    from bidcraft.knowledge import index_local, search
    from bidcraft.models import KnowledgeBase

    global_root = Path(tmp_path) / "global"
    personal_root = Path(tmp_path) / "personal"
    global_root.mkdir()
    personal_root.mkdir()
    (global_root / "global.md").write_text("全局库：智能化工程实施规范。", encoding="utf-8")
    (personal_root / "personal.md").write_text("个人库：智能化工程调试记录。", encoding="utf-8")

    with session_scope() as session:
        global_kb = KnowledgeBase(
            name="全局企业库", kind="local", provider="local", location=str(global_root)
        )
        personal_kb = KnowledgeBase(
            name="个人库",
            kind="local",
            provider="local",
            location=str(personal_root),
            options={"personal": True},
        )
        session.add_all([global_kb, personal_kb])
        session.flush()
        index_local(session, global_kb)
        index_local(session, personal_kb)
        passages = search(
            session,
            "智能化工程",
            limit=10,
            extra_kb_ids=[personal_kb.id],
        )
    sources = {item.source for item in passages}
    assert "global.md" in sources
    assert "personal.md" in sources
