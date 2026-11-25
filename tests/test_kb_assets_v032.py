"""v0.32 Phase 2 Knowledge Asset Engine：backend DB/service 真库回归。

全部用例使用仓库 ``db`` fixture（SQLite 真库 + Alembic head），不做网络调用，
不经过 API/前端；所有断言都落在真实读写的表行上。
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import func, inspect, select

from bidcraft import material_policy
from bidcraft.db import get_engine, session_scope
from bidcraft.knowledge import search, tokens_for
from bidcraft.knowledge_assets import (
    APPROVED,
    ARCHIVED,
    DRAFT,
    PUBLISHED,
    REJECTED,
    REVIEW,
    STATES,
    VALID_TRANSITIONS,
    asset_payload,
    find_duplicate_candidates,
    propose_document_assets,
    resolve_duplicate_group,
    review_asset,
)
from bidcraft.models import (
    KbAssetDuplicateGroup,
    KbAssetDuplicateMember,
    KbChunk,
    KbDocument,
    KnowledgeBase,
    Project,
    SourceFile,
)

CERT_TEXT = (
    "安全生产许可证\n"
    "标题：安全生产许可证\n"
    "证书编号：AQ-2026-001\n"
    "有效期至 2030-06-30\n"
    "业主：某园区管委会\n"
    "金额：人民币 350 万元\n"
    "出处：https://example.gov.cn/cert/1\n"
    "备注：可在官方平台核验\n"
    "伪造块 ID：fake-chunk-id\n"
)


def _kb(db, name: str = "") -> KnowledgeBase:
    row = KnowledgeBase(
        name=name or f"kb-{uuid.uuid4().hex}", kind="local", enabled=True
    )
    db.add(row)
    db.flush()
    return row


def _doc(
    db,
    kb: KnowledgeBase,
    *,
    source: str = "cert.pdf",
    sha256: str = "",
    kind: str = "qualification",
    structured: dict | None = None,
    review_status: str | None = None,
    source_file_id: str = "",
) -> KbDocument:
    row = KbDocument(
        kb_id=kb.id,
        source=source,
        sha256=sha256,
        status="ready",
        kind=kind,
        structured=structured,
        review_status=review_status,
        source_file_id=source_file_id,
    )
    db.add(row)
    db.flush()
    return row


def _chunk(
    db,
    doc: KbDocument,
    text: str,
    *,
    heading: str = "安全生产许可证",
    ordinal: int = 0,
    page: int = 1,
    chunk_id: str | None = None,
) -> KbChunk:
    row = KbChunk(
        id=chunk_id or str(uuid.uuid4()),
        kb_id=doc.kb_id,
        document_id=doc.id,
        source=doc.source,
        ordinal=ordinal,
        text=text,
        tokens=" ".join(tokens_for(text)),
        heading=heading,
        page_no=page,
        chunk_index=ordinal,
        hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    db.add(row)
    db.flush()
    return row


def test_migration_adds_asset_columns_and_tables(db):
    """0014 的列/表/索引/唯一约束都真实落在库里。"""
    engine = get_engine()
    with engine.connect() as connection:
        inspector = inspect(connection)
        doc_cols = {c["name"] for c in inspector.get_columns("kb_documents")}
        tables = set(inspector.get_table_names())
        group_cols = {c["name"] for c in inspector.get_columns("kb_asset_duplicate_groups")}
        member_cols = {c["name"] for c in inspector.get_columns("kb_asset_duplicate_members")}
        group_indexes = {
            idx["name"] for idx in inspector.get_indexes("kb_asset_duplicate_groups")
        }
        member_indexes = {
            idx["name"] for idx in inspector.get_indexes("kb_asset_duplicate_members")
        }
        group_uniques = {
            item["name"]
            for item in inspector.get_unique_constraints("kb_asset_duplicate_groups")
        }
        member_uniques = {
            item["name"]
            for item in inspector.get_unique_constraints("kb_asset_duplicate_members")
        }
    assert {
        "structured", "review_status", "reviewed_by", "reviewed_at",
        "published_at", "rejected_reason", "source_file_id",
    } <= doc_cols
    assert {"kb_asset_duplicate_groups", "kb_asset_duplicate_members"} <= tables
    assert {
        "id", "kb_id", "fingerprint", "similarity", "status", "reason",
        "created_by", "created_at", "reviewed_by", "reviewed_at",
    } <= group_cols
    assert {"id", "group_id", "document_id", "score", "role"} <= member_cols
    assert "ix_kb_asset_duplicate_groups_kb_status" in group_indexes
    assert "ix_kb_asset_duplicate_members_document_id" in member_indexes
    assert "uq_kb_asset_duplicate_group" in group_uniques
    assert "uq_kb_asset_duplicate_member" in member_uniques


def test_state_machine_covers_all_transitions(db):
    """六个状态、全部合法路径与审核痕迹都必须真实落库。"""
    assert STATES == {DRAFT, REVIEW, APPROVED, PUBLISHED, REJECTED, ARCHIVED}
    assert VALID_TRANSITIONS[DRAFT] == {REVIEW, REJECTED}
    assert VALID_TRANSITIONS[REVIEW] == {APPROVED, REJECTED, DRAFT}
    assert VALID_TRANSITIONS[APPROVED] == {PUBLISHED, REJECTED}
    assert VALID_TRANSITIONS[PUBLISHED] == {ARCHIVED}
    assert VALID_TRANSITIONS[REJECTED] == {DRAFT}
    assert VALID_TRANSITIONS[ARCHIVED] == {DRAFT}

    with session_scope() as db_session:
        kb = _kb(db_session)
        doc = _doc(db_session, kb)
        propose_document_assets(db_session, kb.id, doc.id)
        assert doc.review_status == DRAFT

        steps = (
            (REVIEW, "提交审核"),
            (APPROVED, "审核通过"),
            (PUBLISHED, "发布"),
            (ARCHIVED, "归档"),
            (DRAFT, "重新起草"),
            (REJECTED, "驳回"),
            (DRAFT, "重新打开"),
        )
        for decision, actor in steps:
            payload = review_asset(db_session, kb, doc.id, decision, "说明", actor)
            assert payload["review_status"] == decision
            assert doc.review_status == decision
            assert doc.reviewed_by == actor
            assert doc.reviewed_at is not None
        assert doc.published_at is not None  # 归档后保留发布历史
        # 最后一次是 draft，驳回原因已清理。
        assert doc.rejected_reason == ""


def test_state_machine_rejects_illegal_transitions_without_writing(db):
    """非法跃迁必须 ValueError 且不改库；未知决策同样拒绝。"""
    illegal = (
        (DRAFT, PUBLISHED),
        (DRAFT, APPROVED),
        (REVIEW, PUBLISHED),
        (APPROVED, ARCHIVED),
        (PUBLISHED, DRAFT),
        (REJECTED, APPROVED),
        (ARCHIVED, PUBLISHED),
    )
    with session_scope() as db_session:
        kb = _kb(db_session)
        for start, target in illegal:
            doc = _doc(db_session, kb, source=f"illegal-{start}-{target}.pdf")
            doc.review_status = start
            db_session.flush()
            with pytest.raises(ValueError, match="非法资产状态转换"):
                review_asset(db_session, kb, doc.id, target, "尝试越级", "tester")
            assert doc.review_status == start
            assert doc.reviewed_at is None

        doc = _doc(db_session, kb, source="unknown-decision.pdf")
        with pytest.raises(ValueError, match="未知审核动作"):
            review_asset(db_session, kb, doc.id, "teleport", "", "tester")
        assert doc.review_status is None


def test_extraction_is_deterministic_and_never_invents_chunk_ids(db):
    """抽取确定性、只引用真实块 ID，文本里的伪 ID 不得进入载荷。"""
    with session_scope() as db_session:
        kb = _kb(db_session)
        doc = _doc(db_session, kb)
        chunk = _chunk(db_session, doc, CERT_TEXT, chunk_id="real-chunk-v032")
        first = propose_document_assets(db_session, kb.id, doc.id)
        second = propose_document_assets(db_session, kb.id, doc.id)

        assert first == second
        assert first["fields"] == {
            "title": "安全生产许可证",
            "cert_no": "AQ-2026-001",
            "contract_no": "",
            "amount": "350 万元",
            "valid_to": "2030-06-30",
            "owner": "某园区管委会",
            "source_url": "https://example.gov.cn/cert/1",
            "note": "可在官方平台核验",
        }
        assert doc.structured == first["fields"]
        assert doc.review_status == DRAFT
        assert [item["id"] for item in first["chunks"]] == [chunk.id]
        assert first["chunks"][0]["page"] == 1
        assert first["chunks"][0]["heading"] == "安全生产许可证"

        real_ids = set(
            db_session.scalars(
                select(KbChunk.id).where(KbChunk.document_id == doc.id)
            )
        )
        trace_ids = {item["id"] for item in first["chunks"]}
        assert trace_ids == {chunk.id} <= real_ids
        assert "fake-chunk-id" not in str(first["fields"])
        assert "fake-chunk-id" not in str(first["chunks"])
        assert first["document_id"] == doc.id
        assert first["kb_id"] == kb.id


def test_retrieval_rejects_draft_rejected_but_allows_legacy_and_published(db):
    """检索门禁：NULL/published 放行，draft/rejected 拦截，旧块继续放行。"""
    text = "安全生产许可证与智能化资质说明"
    with session_scope() as db_session:
        kb = _kb(db_session, "检索门禁库")
        doc = _doc(db_session, kb)
        _chunk(db_session, doc, text)
        # 迁移前的无溯源块：文档被拦截时仍应可检索。
        legacy = KbChunk(
            kb_id=kb.id, document_id=None, source="legacy.md", text=text,
            tokens=" ".join(tokens_for(text)),
        )
        db_session.add(legacy)
        db_session.flush()

        def found_ids():
            return {
                passage.document_id
                for passage in search(db_session, "安全生产许可证", limit=10, kb_ids=[kb.id])
            }

        assert doc.id in found_ids()  # legacy NULL 放行
        propose_document_assets(db_session, kb.id, doc.id)
        assert doc.review_status == DRAFT
        assert doc.id not in found_ids()  # draft 拦截
        review_asset(db_session, kb, doc.id, REJECTED, "资料过期", "tester")
        assert doc.id not in found_ids()  # rejected 拦截
        review_asset(db_session, kb, doc.id, DRAFT, "重新抽取", "tester")
        review_asset(db_session, kb, doc.id, REVIEW, "", "tester")
        review_asset(db_session, kb, doc.id, APPROVED, "", "tester")
        review_asset(db_session, kb, doc.id, PUBLISHED, "", "tester")
        assert doc.id in found_ids()  # published 放行
        review_asset(db_session, kb, doc.id, ARCHIVED, "", "tester")
        assert doc.id not in found_ids()  # archived 拦截
        # 全过程中无溯源旧块始终可检索。
        assert any(
            passage.document_id is None
            for passage in search(db_session, "安全生产许可证", limit=10, kb_ids=[kb.id])
        )


def test_duplicate_exact_sha_group(db):
    """同 SHA256 的两份文档进同一候选组，成员角色稳定。"""
    with session_scope() as db_session:
        kb = _kb(db_session)
        digest = hashlib.sha256(b"same-content").hexdigest()
        first = _doc(db_session, kb, source="a.pdf", sha256=digest)
        second = _doc(db_session, kb, source="b.pdf", sha256=digest)
        _doc(db_session, kb, source="c.pdf", sha256="other")

        groups = find_duplicate_candidates(db_session, kb.id)
        assert len(groups) == 1
        group = groups[0]
        assert group.status == "pending"
        assert group.similarity == 1.0
        assert "SHA256" in group.reason
        assert group.id and group.fingerprint
        assert {member.document_id for member in group.members} == {first.id, second.id}
        assert {member.role for member in group.members} == {"primary", "candidate"}
        assert all(member.score == 1.0 for member in group.members)

        stored = db_session.get(KbAssetDuplicateGroup, group.id)
        assert stored is not None
        assert db_session.scalar(
            select(KbAssetDuplicateMember).where(
                KbAssetDuplicateMember.group_id == group.id
            )
        ) is not None


def test_duplicate_meta_group(db):
    """SHA 不同但标题/证书号规范化一致时进同一候选组。"""
    with session_scope() as db_session:
        kb = _kb(db_session)
        first = _doc(
            db_session, kb, source="cert-a.pdf", sha256="sha-a",
            structured={"title": "安全生产许可证", "cert_no": "AQ-2026-001"},
        )
        second = _doc(
            db_session, kb, source="cert-b.pdf", sha256="sha-b",
            structured={"title": " 安全生产许可证 ", "cert_no": "aq 2026 001"},
        )
        _doc(
            db_session, kb, source="cert-c.pdf", sha256="sha-c",
            structured={"title": "不同标题", "cert_no": "AQ-2026-001"},
        )
        groups = find_duplicate_candidates(db_session, kb.id)
        assert len(groups) == 1
        assert groups[0].similarity == 0.9
        assert {member.document_id for member in groups[0].members} == {first.id, second.id}
        assert first.id != second.id


def test_duplicate_groups_do_not_mix_across_kbs(db):
    """同指纹在不同库可共存，但成员绝不跨库。"""
    with session_scope() as db_session:
        left = _kb(db_session, "左库")
        right = _kb(db_session, "右库")
        digest = "shared-digest"
        left_docs = [
            _doc(db_session, left, source=f"left-{index}.pdf", sha256=digest)
            for index in range(2)
        ]
        right_docs = [
            _doc(db_session, right, source=f"right-{index}.pdf", sha256=digest)
            for index in range(2)
        ]

        left_groups = find_duplicate_candidates(db_session, left.id)
        right_groups = find_duplicate_candidates(db_session, right.id)
        assert len(left_groups) == len(right_groups) == 1
        assert left_groups[0].id != right_groups[0].id
        assert left_groups[0].fingerprint == right_groups[0].fingerprint
        assert {m.document_id for m in left_groups[0].members} == {d.id for d in left_docs}
        assert {m.document_id for m in right_groups[0].members} == {d.id for d in right_docs}
        assert not (
            {m.document_id for m in left_groups[0].members}
            & {d.id for d in right_docs}
        )
        assert all(group.kb_id == left.id for group in left_groups)
        assert all(group.kb_id == right.id for group in right_groups)


def test_duplicate_recompute_is_idempotent(db):
    """重复生组不新增行；集合变化后复用同一组 ID。"""
    with session_scope() as db_session:
        kb = _kb(db_session)
        digest = "idempotent-sha"
        _doc(db_session, kb, source="one.pdf", sha256=digest)
        _doc(db_session, kb, source="two.pdf", sha256=digest)

        first = find_duplicate_candidates(db_session, kb.id)
        member_ids = {member.id for member in first[0].members}
        second = find_duplicate_candidates(db_session, kb.id)
        assert [(g.id, g.fingerprint) for g in second] == [
            (g.id, g.fingerprint) for g in first
        ]
        assert {member.id for member in second[0].members} == member_ids
        assert db_session.scalar(
            select(func.count())
            .select_from(KbAssetDuplicateGroup)
            .where(KbAssetDuplicateGroup.kb_id == kb.id)
        ) == 1

        third = _doc(db_session, kb, source="three.pdf", sha256=digest)
        recomputed = find_duplicate_candidates(db_session, kb.id)
        assert len(recomputed) == 1
        assert recomputed[0].id == first[0].id
        assert third.id in {member.document_id for member in recomputed[0].members}
        assert len(recomputed[0].members) == 3


def test_resolve_duplicate_group_is_idempotent(db):
    """resolve/dismiss 幂等：重复裁决不覆盖裁决人和时间。"""
    with session_scope() as db_session:
        kb = _kb(db_session)
        digest = "resolve-sha"
        _doc(db_session, kb, source="r1.pdf", sha256=digest)
        _doc(db_session, kb, source="r2.pdf", sha256=digest)
        group = find_duplicate_candidates(db_session, kb.id)[0]

        resolved = resolve_duplicate_group(
            db_session, kb.id, group.id, "resolve", "判定为重复上传", "admin-a"
        )
        assert resolved.status == "resolved"
        assert resolved.reviewed_by == "admin-a"
        assert resolved.reviewed_at
        assert resolved.reason == "判定为重复上传"

        again = resolve_duplicate_group(
            db_session, kb.id, group.id, "resolve", "不应覆盖", "admin-b"
        )
        assert again == resolved

        dismissed = resolve_duplicate_group(
            db_session, kb.id, group.id, "dismiss", "误报", "admin-b"
        )
        assert dismissed.status == "dismissed"
        assert dismissed.reviewed_by == "admin-b"
        assert dismissed.reason == "误报"
        assert resolve_duplicate_group(
            db_session, kb.id, group.id, "dismiss", "再次", "admin-c"
        ) == dismissed

        with pytest.raises(LookupError):
            resolve_duplicate_group(db_session, "other-kb", group.id, "resolve", "", "x")
        with pytest.raises(ValueError, match="未知查重裁决"):
            resolve_duplicate_group(db_session, kb.id, group.id, "explode", "", "x")


def test_asset_payload_assessment_matches_material_policy(db):
    """载荷中的 ok/reason 与 material_policy.assess 对同一元数据结论一致。"""
    with session_scope() as db_session:
        kb = _kb(db_session)
        complete = _doc(
            db_session, kb, source="complete.pdf",
            structured={
                "title": "安全生产许可证", "cert_no": "AQ-001",
                "valid_to": "2030-06-30", "source_url": "https://example.gov.cn/1",
            },
        )
        incomplete = _doc(
            db_session, kb, source="incomplete.pdf",
            structured={"title": "安全生产许可证", "valid_to": "2030-06-30"},
        )
        good = asset_payload(complete, db_session)
        bad = asset_payload(incomplete, db_session)

        assert good["assessment"]["ok"] is True
        assert good["ok"] is True
        _, meta = material_policy.find_meta(
            db_session, complete.source, kb_id=kb.id, document_id=complete.id
        )
        assert material_policy.assess(meta).ok is True
        assert good["assessment"]["reason"] == material_policy.assess(meta).reason

        assert bad["assessment"]["ok"] is False
        assert bad["reason"]
        assert "证书编号" in bad["reason"]
        assert bad["fields"]["source_url"] == ""  # 字段白名单固定，未抽到即空串


def test_source_file_id_is_traced_in_payload(db):
    """source_file_id 必须引用真实 SourceFile，且抽取不覆盖溯源。"""
    with session_scope() as db_session:
        project = Project(name="溯源项目")
        db_session.add(project)
        db_session.flush()
        source_file = SourceFile(
            project_id=project.id, filename="cert.pdf", stored_path="/tmp/cert.pdf"
        )
        db_session.add(source_file)
        db_session.flush()

        kb = _kb(db_session)
        doc = _doc(
            db_session, kb, source_file_id=source_file.id,
            structured={"title": "安全生产许可证", "cert_no": "AQ-1"},
        )
        payload = asset_payload(doc, db_session)
        assert payload["source_file_id"] == source_file.id
        assert payload["document_id"] == doc.id
        assert doc.source_file_id == source_file.id

        proposed = propose_document_assets(db_session, kb.id, doc.id)
        assert proposed["source_file_id"] == source_file.id
        assert db_session.get(KbDocument, doc.id).source_file_id == source_file.id


def test_review_asset_verifies_kb_scope(db):
    """审核不允许跨库操作文档。"""
    with session_scope() as db_session:
        owner = _kb(db_session)
        other = _kb(db_session)
        doc = _doc(db_session, owner)
        propose_document_assets(db_session, owner.id, doc.id)
        with pytest.raises(ValueError, match="不属于该知识库"):
            review_asset(db_session, other, doc.id, REVIEW, "", "tester")
        assert doc.review_status == DRAFT
        with pytest.raises(ValueError, match="不属于该知识库"):
            propose_document_assets(db_session, other.id, doc.id)
        with pytest.raises(LookupError):
            propose_document_assets(db_session, owner.id, "missing-document")
