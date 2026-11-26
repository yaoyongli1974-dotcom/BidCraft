"""v0.33.0 RC-04：知识资产生命周期回归（真库，不依赖 mock 成功路径）。

覆盖四项审计缺口：重入库重置审核状态、企业匹配过滤未发布文档、
删除文档/知识库清理查重孤儿行、向量化跳过不可发布块。
"""

from __future__ import annotations

import hashlib
import uuid

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import func, select  # noqa: E402

from bidcraft import knowledge  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.enterprise import assets, matching  # noqa: E402
from bidcraft.knowledge_assets import (  # noqa: E402
    APPROVED,
    DRAFT,
    PUBLISHED,
    REJECTED,
    REVIEW,
    find_duplicate_candidates,
    propose_document_assets,
    review_asset,
)
from bidcraft.models import (  # noqa: E402
    KbAssetDuplicateGroup,
    KbAssetDuplicateMember,
    KbChunk,
    KbDocument,
    KnowledgeBase,
    ModelConfig,
    User,
)


def _kb(db, name: str = "") -> KnowledgeBase:
    row = KnowledgeBase(
        name=name or f"kb-{uuid.uuid4().hex}", kind="local", provider="local", enabled=True
    )
    db.add(row)
    db.flush()
    return row


def _document(db, kb: KnowledgeBase, source: str, content: str) -> KbDocument:
    row = KbDocument(
        kb_id=kb.id,
        source=source,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        size=len(content.encode("utf-8")),
        status="ready",
        version=1,
        kind="equipment",
        pages=1,
    )
    db.add(row)
    db.flush()
    db.add(
        KbChunk(
            kb_id=kb.id,
            document_id=row.id,
            source=source,
            ordinal=0,
            chunk_index=0,
            text=content,
            tokens=" ".join(knowledge.tokens_for(content)),
            heading="设备清单",
            page_no=1,
            hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
    )
    db.flush()
    return row


def _publish(db, kb: KnowledgeBase, doc: KbDocument) -> None:
    propose_document_assets(db, kb.id, doc.id)
    for decision in (REVIEW, APPROVED, PUBLISHED):
        review_asset(db, kb, doc.id, decision, "", "rc04-admin")


def _visible_ids(db, kb: KnowledgeBase, query: str) -> set[str]:
    return {
        passage.document_id
        for passage in knowledge.search(db, query, limit=10, kb_ids=[kb.id])
        if passage.document_id
    }


def test_reingest_changed_content_resets_lifecycle_and_search_gate(db):
    """B6：内容变化必须撤销发布痕迹，新文本不得立刻进入检索。"""
    with session_scope() as session:
        kb = _kb(session, "生命周期库")
        original = "证书原文 智能布线验收记录"
        doc = knowledge.ingest_document(session, kb, "证书.md", original, kind="qualification")
        _publish(session, kb, doc)
        assert doc.review_status == PUBLISHED
        assert doc.reviewed_by == "rc04-admin"
        assert doc.reviewed_at is not None and doc.published_at is not None
        assert doc.id in _visible_ids(session, kb, "智能布线验收记录")

        changed = original + " 补充年检结论"
        updated = knowledge.ingest_document(session, kb, "证书.md", changed, kind="qualification")
        assert updated.id == doc.id
        assert updated.version == 2
        assert updated.review_status in (DRAFT, None)
        assert updated.reviewed_by is None
        assert updated.reviewed_at is None
        assert updated.published_at is None
        assert updated.rejected_reason in (None, "")
        assert doc.id not in _visible_ids(session, kb, "补充年检结论")

        # 重新走审核发布后，新内容恢复可检索。
        _publish(session, kb, updated)
        assert doc.id in _visible_ids(session, kb, "补充年检结论")


def test_reingest_same_digest_keeps_published_lifecycle(db):
    """B6 兼容面：digest 未变时不得重置审核痕迹（非本次变更行为）。"""
    with session_scope() as session:
        kb = _kb(session, "同摘要库")
        content = "资质说明 电子与智能化工程专业承包二级"
        doc = knowledge.ingest_document(session, kb, "资质.md", content, kind="qualification")
        _publish(session, kb, doc)
        before = (
            doc.version,
            doc.review_status,
            doc.reviewed_by,
            doc.reviewed_at,
            doc.published_at,
        )
        chunk_ids = list(session.scalars(select(KbChunk.id).where(KbChunk.document_id == doc.id)))

        same = knowledge.ingest_document(session, kb, "资质.md", content, kind="qualification")
        assert (
            same.version,
            same.review_status,
            same.reviewed_by,
            same.reviewed_at,
            same.published_at,
        ) == before
        assert list(
            session.scalars(select(KbChunk.id).where(KbChunk.document_id == doc.id))
        ) == chunk_ids
        assert doc.id in _visible_ids(session, kb, "电子与智能化工程专业承包二级")


def test_index_local_reingest_resets_lifecycle(db, tmp_path, monkeypatch):
    """B6：index_local 重索引走 ingest_document，同样必须撤销发布状态。"""
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "offline", True)
    root = tmp_path / "kb-lifecycle"
    root.mkdir()
    path = root / "cert.md"
    path.write_text("证书原文 综合布线测试规范", encoding="utf-8")
    with session_scope() as session:
        kb = _kb(session, "本地索引库")
        kb.location = str(root)
        assert knowledge.index_local(session, kb) == 1
        doc = knowledge.list_documents(session, kb)[0]
        _publish(session, kb, doc)
        assert doc.review_status == PUBLISHED
        assert doc.id in _visible_ids(session, kb, "综合布线测试规范")

        path.write_text("证书原文 综合布线测试规范 新增条款", encoding="utf-8")
        assert knowledge.index_local(session, kb) == 1
        same_doc = knowledge.list_documents(session, kb)[0]
        assert same_doc.id == doc.id
        assert same_doc.review_status in (DRAFT, None)
        assert same_doc.published_at is None
        assert doc.id not in _visible_ids(session, kb, "新增条款")


@pytest.mark.parametrize("blocked_status", [DRAFT, REVIEW, "approved", REJECTED, "archived"])
def test_matching_ignores_non_published_document_chunks(db, blocked_status):
    """B7：企业资产匹配不得引用 draft/review/approved/rejected/archived 的块。"""
    with session_scope() as session:
        user = User(
            username=f"rc04-match-{blocked_status}",
            display_name="RC04",
            password_hash="not-used",
            role="manager",
        )
        session.add(user)
        session.flush()
        enterprise = assets.create_enterprise(session, {"id": user.id}, "RC04企业")
        kb = assets.enterprise_kb(session, enterprise, create=True)

        published = knowledge.ingest_document(
            session, kb, "可发布设备.md", "全站仪 TS-60 检定设备", kind="equipment"
        )
        _publish(session, kb, published)
        blocked = knowledge.ingest_document(
            session, kb, "被拦截设备.md", "全站仪 TS-80 检定设备", kind="equipment"
        )
        blocked.review_status = blocked_status
        legacy = knowledge.ingest_document(
            session, kb, "历史设备.md", "全站仪 TS-90 检定设备", kind="equipment"
        )
        assert legacy.review_status is None
        session.flush()

        result = matching.match_equipment(
            session,
            {"id": user.id},
            "全站仪",
            enterprise_ids=[enterprise.id],
        )
        by_id = {item["id"]: item for item in result["results"]}
        assert published.id in by_id
        assert legacy.id in by_id
        assert blocked.id not in by_id
        assert by_id[published.id]["matched"] is True
        assert by_id[legacy.id]["matched"] is True
        assert result["matched"] is True


def test_delete_document_cleans_duplicate_members_and_empty_groups(db):
    """B8：删文档先清成员，再清空的组；仍有成员的组必须保留。"""
    with session_scope() as session:
        kb = _kb(session, "删除清理库")
        digest = "rc04-shared-digest"
        first = _document(session, kb, "one.pdf", "同摘要内容甲")
        second = _document(session, kb, "two.pdf", "同摘要内容乙")
        third = _document(session, kb, "three.pdf", "同摘要内容丙")
        first.sha256 = second.sha256 = third.sha256 = digest
        survivor = _document(session, kb, "survivor.pdf", "另一摘要内容")
        survivor2 = _document(session, kb, "survivor2.pdf", "另一摘要内容副本")
        survivor.sha256 = survivor2.sha256 = "rc04-other-digest"
        session.flush()

        all_groups = find_duplicate_candidates(session, kb.id)
        assert len(all_groups) == 2
        # 两组分别对应两个不同摘要，按成员区分，避免依赖遍历顺序。
        group_id = next(
            group.id
            for group in all_groups
            if any(member.document_id == first.id for member in group.members)
        )
        survivor_group_id = next(
            group.id
            for group in all_groups
            if any(member.document_id == survivor.id for member in group.members)
        )
        assert group_id != survivor_group_id
        assert session.scalar(
            select(func.count())
            .select_from(KbAssetDuplicateMember)
            .where(KbAssetDuplicateMember.group_id == group_id)
        ) == 3

        knowledge.delete_document(session, kb, first.id)
        assert session.get(KbDocument, first.id) is None
        assert session.scalar(
            select(func.count())
            .select_from(KbAssetDuplicateMember)
            .where(KbAssetDuplicateMember.document_id == first.id)
        ) == 0
        assert session.get(KbAssetDuplicateGroup, group_id) is not None

        knowledge.delete_document(session, kb, second.id)
        assert session.get(KbAssetDuplicateGroup, group_id) is not None  # 第三个成员仍在
        assert session.scalar(
            select(func.count())
            .select_from(KbAssetDuplicateMember)
            .where(KbAssetDuplicateMember.group_id == group_id)
        ) == 1

        knowledge.delete_document(session, kb, third.id)
        assert session.get(KbAssetDuplicateGroup, group_id) is None
        assert session.scalar(
            select(func.count())
            .select_from(KbAssetDuplicateMember)
            .where(KbAssetDuplicateMember.group_id == group_id)
        ) == 0
        assert session.get(KbAssetDuplicateGroup, survivor_group_id) is not None
        assert session.scalar(
            select(func.count())
            .select_from(KbAssetDuplicateMember)
            .where(KbAssetDuplicateMember.group_id == survivor_group_id)
        ) == 2
        assert session.get(KbDocument, survivor.id) is not None
        assert session.get(KbDocument, survivor2.id) is not None


def test_delete_kb_route_cleans_duplicate_rows(db):
    """B8：admin 删除知识库路径同样不能留下查重孤儿行。"""
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    resp = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert resp.status_code == 200, resp.text
    created = client.post(
        "/admin/kbs",
        json={"name": f"rc04-route-{uuid.uuid4().hex}", "kind": "local", "provider": "local"},
    )
    assert created.status_code == 201, created.text
    kb_id = created.json()["id"]

    with session_scope() as session:
        kb = session.get(KnowledgeBase, kb_id)
        docs = [
            _document(session, kb, f"dup-{index}.pdf", f"重复内容 {index}") for index in range(2)
        ]
        for doc in docs:
            doc.sha256 = "rc04-route-digest"
        session.flush()
        groups = find_duplicate_candidates(session, kb.id)
        assert len(groups) == 1
        group_id = groups[0].id

    deleted = client.delete(f"/admin/kbs/{kb_id}")
    assert deleted.status_code == 200, deleted.text
    with session_scope() as session:
        assert session.get(KnowledgeBase, kb_id) is None
        assert session.get(KbAssetDuplicateGroup, group_id) is None
        assert session.scalar(
            select(func.count())
            .select_from(KbAssetDuplicateMember)
            .where(KbAssetDuplicateMember.group_id == group_id)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(KbDocument).where(KbDocument.kb_id == kb_id)
        ) == 0


def test_build_vectors_skips_non_releasable_chunks(db, monkeypatch):
    """B9：向量化请求里只能出现 legacy/published 块，草稿与驳回块必须缺席。"""
    from bidcraft import knowledge as knowledge_module
    from bidcraft.config import settings

    monkeypatch.setattr(settings, "offline", False)
    with session_scope() as session:
        session.add(
            ModelConfig(
                name="RC04向量模型",
                kind="embedding",
                provider="openai",
                base_url="http://fake/v1",
                model="rc04-emb",
                enabled=True,
                is_default=True,
            )
        )
        session.flush()

    captured: list[str] = []

    def fake_embed(endpoint, texts):
        captured.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(knowledge_module, "embed_texts", fake_embed)

    with session_scope() as session:
        kb = _kb(session, "向量生命周期库")
        published = knowledge.ingest_document(session, kb, "ok.md", "公开块文本")
        _publish(session, kb, published)
        draft = knowledge.ingest_document(session, kb, "draft.md", "草稿块文本")
        draft.review_status = DRAFT
        rejected = knowledge.ingest_document(session, kb, "rejected.md", "驳回块文本")
        rejected.review_status = REJECTED
        legacy = knowledge.ingest_document(session, kb, "legacy.md", "历史块文本")
        assert legacy.review_status is None
        session.flush()

        result = knowledge.build_vectors(session, kb)
        assert result["embedded"] == 2
        assert set(captured) == {"公开块文本", "历史块文本"}
        assert "草稿块文本" not in captured
        assert "驳回块文本" not in captured

        keyed = {
            row.text: row.embed_key
            for row in session.scalars(select(KbChunk).where(KbChunk.kb_id == kb.id))
        }
        assert {text for text, key in keyed.items() if key} == {"公开块文本", "历史块文本"}
