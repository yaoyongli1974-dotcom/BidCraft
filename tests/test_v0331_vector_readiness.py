"""v0.33.1 Phase 1：vector_ready 只统计可发布块，不被草稿/驳回块误报 stale。"""

from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

from sqlalchemy import update

from bidcraft import knowledge
from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.models import KbChunk, KbDocument, KnowledgeBase


def _chunk(db, kb, doc, *, text, embed_key=None):
    db.add(
        KbChunk(
            kb_id=kb.id,
            document_id=doc.id,
            source=doc.source,
            ordinal=0,
            chunk_index=0,
            text=text,
            tokens=" ".join(knowledge.tokens_for(text)),
            heading="测试段落",
            page_no=1,
            hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            embed_key=embed_key,
            embed_dim=3 if embed_key else 0,
        )
    )


def test_vector_readiness_counts_only_releasable_chunks(db, monkeypatch):
    monkeypatch.setattr(settings, "kb_vector_enabled", True)
    monkeypatch.setattr(
        knowledge,
        "resolve_embedding_endpoint",
        lambda _db: SimpleNamespace(model_name="emb-test"),
    )
    monkeypatch.setattr(knowledge, "_embed_key", lambda _endpoint: "emb-test")

    with session_scope() as session:
        kb = KnowledgeBase(
            name=f"kb-{uuid.uuid4().hex}",
            kind="local",
            provider="local",
            enabled=True,
            # 故意写成陈旧总数：API 应以真实可发布块为准。
            chunk_count=4,
        )
        session.add(kb)
        session.flush()

        published = KbDocument(
            kb_id=kb.id,
            source="published.md",
            sha256="a" * 64,
            size=10,
            status="ready",
            version=1,
            review_status="published",
        )
        legacy = KbDocument(
            kb_id=kb.id,
            source="legacy.md",
            sha256="b" * 64,
            size=10,
            status="ready",
            version=1,
            review_status=None,
        )
        draft = KbDocument(
            kb_id=kb.id,
            source="draft.md",
            sha256="c" * 64,
            size=10,
            status="ready",
            version=1,
            review_status="draft",
        )
        rejected = KbDocument(
            kb_id=kb.id,
            source="rejected.md",
            sha256="d" * 64,
            size=10,
            status="ready",
            version=1,
            review_status="rejected",
        )
        session.add_all([published, legacy, draft, rejected])
        session.flush()
        _chunk(session, kb, published, text="已发布正文", embed_key="emb-test")
        _chunk(session, kb, legacy, text="历史正文", embed_key="emb-test")
        _chunk(session, kb, draft, text="草稿正文")
        _chunk(session, kb, rejected, text="驳回正文")
        session.flush()

        fields = knowledge._vector_fields(kb, session)
        assert fields["vector_total"] == 2
        assert fields["vector_embedded"] == 2
        assert fields["vector_excluded"] == 2
        assert fields["vector_ready"] is True
        assert fields["vector_stale"] is False
        assert fields["vector_dim"] == 3
        assert kb.chunk_count == 4  # 原始统计值不被改写

        # 一个可发布块换模型后只剩一个新向量，必须报告 stale。
        session.execute(
            update(KbChunk)
            .where(KbChunk.document_id == legacy.id)
            .values(embed_key="old-model", embed_dim=0)
        )
        session.flush()
        stale = knowledge._vector_fields(kb, session)
        assert stale["vector_total"] == 2
        assert stale["vector_embedded"] == 1
        assert stale["vector_ready"] is False
        assert stale["vector_stale"] is True
