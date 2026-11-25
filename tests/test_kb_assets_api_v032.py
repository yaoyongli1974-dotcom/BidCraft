"""v0.32 Phase 2 知识资产引擎：HTTP API 真实 DB 回归。

覆盖 admin / enterprise 两套共 10 条路由：抽取、审核状态机、查重裁决、
权限隔离与审计。全部使用仓库 ``db`` fixture（SQLite 真库 + 当前模型建表），
不 mock 服务层、不做网络调用；成功路径均为真实读写。
"""

from __future__ import annotations

import hashlib
import uuid

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import select  # noqa: E402

from bidcraft.api import create_app  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.models import (  # noqa: E402
    AuditLog,
    KbAssetDuplicateGroup,
    KbChunk,
    KbDocument,
)

ADMIN_PASS = "test-admin"
USER_PASS = "v032-api-pass"

CONTENT = (
    "标题：智能弱电系统技术方案\n"
    "证书编号：DZ-2026-001\n"
    "合同金额：人民币 350 万元\n"
    "有效期至：2027-12-31\n"
    "业主：某科技园区管理委员会\n"
    "备注：来源为招标文件附件\n"
)


def _client(username: str = "admin", password: str = ADMIN_PASS):
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def _new_user(admin, username: str):
    resp = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": USER_PASS,
            "role": "manager",
        },
    )
    assert resp.status_code == 201, resp.text
    return _client(username, USER_PASS)


def _kb(admin) -> str:
    resp = admin.post(
        "/admin/kbs",
        json={"name": f"kb-{uuid.uuid4().hex}", "kind": "local", "provider": "local"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _seed_doc(kb_id: str, source: str, *, sha256: str | None = None) -> str:
    """直接落一条真实 KbDocument + KbChunk（带可抽取字段）。"""

    digest = sha256 or hashlib.sha256(source.encode("utf-8")).hexdigest()
    with session_scope() as db:
        doc = KbDocument(
            kb_id=kb_id,
            source=source,
            sha256=digest,
            kind="other",
            status="ready",
            version=1,
        )
        db.add(doc)
        db.flush()
        db.add(
            KbChunk(
                kb_id=kb_id,
                document_id=doc.id,
                source=source,
                ordinal=0,
                chunk_index=0,
                text=CONTENT,
                heading="技术资料",
                hash=hashlib.sha256(CONTENT.encode("utf-8")).hexdigest(),
            )
        )
        db.flush()
        return doc.id


def _enterprise(client, name: str) -> str:
    resp = client.post("/enterprise", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _ingest(client, enterprise_id: str, source: str, content: str = CONTENT) -> str:
    resp = client.post(
        f"/enterprise/{enterprise_id}/documents",
        json={"source": source, "content": content, "kind": "other"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------- admin（5 条路由）


def test_admin_assets_list(db):
    admin = _client()
    kb_id = _kb(admin)
    doc_id = _seed_doc(kb_id, "技术标.pdf")

    resp = admin.get(f"/admin/kbs/{kb_id}/assets")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["document_id"] == doc_id
    assert items[0]["kb_id"] == kb_id
    assert items[0]["review_status"] is None  # 历史文档未进入状态机


def test_admin_extract_builds_draft_from_real_chunks(db):
    admin = _client()
    kb_id = _kb(admin)
    doc_id = _seed_doc(kb_id, "技术标.pdf")

    resp = admin.post(f"/admin/kbs/{kb_id}/assets/{doc_id}/extract")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["review_status"] == "draft"
    assert payload["document_id"] == doc_id
    assert payload["fields"]["title"] == "智能弱电系统技术方案"
    assert payload["fields"]["cert_no"] == "DZ-2026-001"
    assert payload["chunks"] and payload["chunks"][0]["source"] == "技术标.pdf"

    with session_scope() as session:
        row = session.get(KbDocument, doc_id)
        assert row.review_status == "draft"
        assert row.structured["title"] == "智能弱电系统技术方案"


def test_admin_review_full_happy_path(db):
    admin = _client()
    kb_id = _kb(admin)
    doc_id = _seed_doc(kb_id, "技术标.pdf")
    assert admin.post(f"/admin/kbs/{kb_id}/assets/{doc_id}/extract").status_code == 200

    for decision, expected in (
        ("review", "review"),
        ("approve", "approved"),
        ("publish", "published"),
    ):
        resp = admin.post(
            f"/admin/kbs/{kb_id}/assets/{doc_id}/review", json={"decision": decision}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["review_status"] == expected

    with session_scope() as session:
        row = session.get(KbDocument, doc_id)
        assert row.review_status == "published"
        assert row.reviewed_by == "admin"
        assert row.published_at is not None


def test_admin_illegal_transition_returns_400(db):
    admin = _client()
    kb_id = _kb(admin)
    doc_id = _seed_doc(kb_id, "技术标.pdf")
    assert admin.post(f"/admin/kbs/{kb_id}/assets/{doc_id}/extract").status_code == 200

    # draft 只能进 review/rejected，直接 publish 必须 400 且不落库。
    resp = admin.post(
        f"/admin/kbs/{kb_id}/assets/{doc_id}/review", json={"decision": "publish"}
    )
    assert resp.status_code == 400, resp.text
    with session_scope() as session:
        assert session.get(KbDocument, doc_id).review_status == "draft"


def test_admin_duplicates_detect_and_resolve(db):
    admin = _client()
    kb_id = _kb(admin)
    digest = "a" * 64
    first = _seed_doc(kb_id, "副本A.pdf", sha256=digest)
    second = _seed_doc(kb_id, "副本B.pdf", sha256=digest)

    resp = admin.get(f"/admin/kbs/{kb_id}/assets/duplicates")
    assert resp.status_code == 200, resp.text
    groups = resp.json()["items"]
    assert len(groups) == 1
    group = groups[0]
    assert group["status"] == "pending"
    assert {m["document_id"] for m in group["members"]} == {first, second}
    group_id = group["id"]

    resolved = admin.post(
        f"/admin/kbs/{kb_id}/assets/duplicates/{group_id}/resolve",
        json={"decision": "resolve", "note": "确认为同一文件的两个副本"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"

    # 重复执行保持幂等且仍只有一组。
    again = admin.post(
        f"/admin/kbs/{kb_id}/assets/duplicates/{group_id}/resolve",
        json={"decision": "resolve"},
    )
    assert again.status_code == 200, again.text
    assert len(admin.get(f"/admin/kbs/{kb_id}/assets/duplicates").json()["items"]) == 1
    with session_scope() as session:
        row = session.get(KbAssetDuplicateGroup, group_id)
        assert row.status == "resolved"
        assert row.reviewed_by == "admin"


def test_admin_duplicate_unknown_group_404(db):
    admin = _client()
    kb_id = _kb(admin)
    resp = admin.post(
        f"/admin/kbs/{kb_id}/assets/duplicates/no-such-group/resolve",
        json={"decision": "dismiss"},
    )
    assert resp.status_code == 404, resp.text


def test_admin_assets_require_super(db):
    regular = _new_user(_client(), "kbapi-regular")
    resp = regular.get("/admin/kbs/any/assets")
    assert resp.status_code == 403, resp.text


# ------------------------------------------------------- enterprise（5 条路由）


def test_enterprise_owner_list_extract_review(db):
    owner = _client()
    enterprise_id = _enterprise(owner, "知识资产企业")
    doc_id = _ingest(owner, enterprise_id, "企业技术资料.pdf")

    listed = owner.get(f"/enterprise/{enterprise_id}/assets")
    assert listed.status_code == 200, listed.text
    assert any(item["document_id"] == doc_id for item in listed.json()["items"])

    extracted = owner.post(f"/enterprise/{enterprise_id}/assets/{doc_id}/extract")
    assert extracted.status_code == 200, extracted.text
    assert extracted.json()["review_status"] == "draft"

    for decision, expected in (("review", "review"), ("approve", "approved")):
        resp = owner.post(
            f"/enterprise/{enterprise_id}/assets/{doc_id}/review",
            json={"decision": decision},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["review_status"] == expected


def test_enterprise_member_can_read_but_not_write(db):
    admin = _client()
    owner = _client()
    enterprise_id = _enterprise(owner, "成员隔离企业")
    doc_id = _ingest(owner, enterprise_id, "成员可见.pdf")

    member = _new_user(admin, "kbapi-member")
    added = owner.post(
        f"/enterprise/{enterprise_id}/members",
        json={"username": "kbapi-member", "role": "member"},
    )
    assert added.status_code == 201, added.text

    assert member.get(f"/enterprise/{enterprise_id}/assets").status_code == 200
    blocked = member.post(f"/enterprise/{enterprise_id}/assets/{doc_id}/extract")
    assert blocked.status_code == 403, blocked.text
    blocked = member.post(
        f"/enterprise/{enterprise_id}/assets/{doc_id}/review",
        json={"decision": "review"},
    )
    assert blocked.status_code == 403, blocked.text


def test_enterprise_outsider_read_404(db):
    owner = _client()
    enterprise_id = _enterprise(owner, "局外人企业")
    outsider = _new_user(_client(), "kbapi-outsider")

    resp = outsider.get(f"/enterprise/{enterprise_id}/assets")
    assert resp.status_code == 404, resp.text
    resp = outsider.get(f"/enterprise/{enterprise_id}/assets/duplicates")
    assert resp.status_code == 404, resp.text


def test_cross_enterprise_document_cannot_be_extracted(db):
    owner = _client()
    ent_a = _enterprise(owner, "企业A")
    ent_b = _enterprise(owner, "企业B")
    doc_b = _ingest(owner, ent_b, "B的文档.pdf")

    # 路由不接受调用方指定的 kb_id：文档 B 不属于企业 A 的 KB，必须失败。
    resp = owner.post(f"/enterprise/{ent_a}/assets/{doc_b}/extract")
    assert resp.status_code in (400, 404), resp.text
    with session_scope() as session:
        assert session.get(KbDocument, doc_b).review_status is None


def test_enterprise_duplicates_resolve_by_owner(db):
    owner = _client()
    enterprise_id = _enterprise(owner, "查重企业")
    _ingest(owner, enterprise_id, "副本1.pdf", CONTENT)
    _ingest(owner, enterprise_id, "副本2.pdf", CONTENT)

    resp = owner.get(f"/enterprise/{enterprise_id}/assets/duplicates")
    assert resp.status_code == 200, resp.text
    groups = resp.json()["items"]
    assert len(groups) == 1
    group_id = groups[0]["id"]

    resolved = owner.post(
        f"/enterprise/{enterprise_id}/assets/duplicates/{group_id}/resolve",
        json={"decision": "dismiss"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "dismissed"


# ------------------------------------------------------------- 契约 / 审计


def test_invalid_decision_rejected_by_schema(db):
    admin = _client()
    kb_id = _kb(admin)
    doc_id = _seed_doc(kb_id, "技术标.pdf")
    assert admin.post(f"/admin/kbs/{kb_id}/assets/{doc_id}/extract").status_code == 200

    bad_review = admin.post(
        f"/admin/kbs/{kb_id}/assets/{doc_id}/review", json={"decision": "unknown"}
    )
    assert bad_review.status_code == 422, bad_review.text
    bad_resolve = admin.post(
        f"/admin/kbs/{kb_id}/assets/duplicates/g/resolve", json={"decision": "unknown"}
    )
    assert bad_resolve.status_code == 422, bad_resolve.text


def test_audit_rows_cover_every_mutation(db):
    admin = _client()
    kb_id = _kb(admin)
    doc_id = _seed_doc(kb_id, "审计文档.pdf")
    admin.post(f"/admin/kbs/{kb_id}/assets/{doc_id}/extract")
    admin.post(
        f"/admin/kbs/{kb_id}/assets/{doc_id}/review", json={"decision": "review"}
    )
    digest = "b" * 64
    a = _seed_doc(kb_id, "审计A.pdf", sha256=digest)
    b = _seed_doc(kb_id, "审计B.pdf", sha256=digest)
    groups = admin.get(f"/admin/kbs/{kb_id}/assets/duplicates").json()["items"]
    assert {m["document_id"] for m in groups[0]["members"]} == {a, b}
    admin.post(
        f"/admin/kbs/{kb_id}/assets/duplicates/{groups[0]['id']}/resolve",
        json={"decision": "resolve"},
    )

    owner = _client()
    ent = _enterprise(owner, "审计企业")
    ent_doc = _ingest(owner, ent, "企业审计.pdf")
    owner.post(f"/enterprise/{ent}/assets/{ent_doc}/extract")
    owner.post(
        f"/enterprise/{ent}/assets/{ent_doc}/review", json={"decision": "review"}
    )
    _ingest(owner, ent, "企业审计副本.pdf", CONTENT)
    ent_groups = owner.get(f"/enterprise/{ent}/assets/duplicates").json()["items"]
    owner.post(
        f"/enterprise/{ent}/assets/duplicates/{ent_groups[0]['id']}/resolve",
        json={"decision": "resolve"},
    )

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.action.in_(
                        {
                            "kb.document_extract",
                            "kb.document_review",
                            "kb.duplicate_resolve",
                            "enterprise.document_extract",
                            "enterprise.document_review",
                            "enterprise.duplicate_resolve",
                        }
                    )
                )
            )
        )
        by_action: dict[str, list[dict]] = {}
        for row in rows:
            by_action.setdefault(row.action, []).append(row.detail or {})
        assert "kb.document_extract" in by_action
        assert "kb.document_review" in by_action
        assert "kb.duplicate_resolve" in by_action
        assert "enterprise.document_extract" in by_action
        assert "enterprise.document_review" in by_action
        assert "enterprise.duplicate_resolve" in by_action
        assert any(d.get("decision") == "review" for d in by_action["kb.document_review"])
        assert all(d.get("actor") == "admin" for d in by_action["kb.document_review"])
        assert any(
            d.get("document_id") == doc_id for d in by_action["kb.document_extract"]
        )
        assert any(
            d.get("group_id") == groups[0]["id"]
            for d in by_action["kb.duplicate_resolve"]
        )
        assert any(
            d.get("kb_id") and d.get("enterprise_id") and d.get("actor") == "admin"
            for d in by_action["enterprise.document_review"]
        )


def test_legacy_kb_documents_untouched(db):
    """历史文档（review_status IS NULL）仍可被列出且检索行为不变。"""

    admin = _client()
    kb_id = _kb(admin)
    doc_id = _seed_doc(kb_id, "历史文档.pdf")
    with session_scope() as session:
        assert session.get(KbDocument, doc_id).review_status is None
    resp = admin.get(f"/admin/kbs/{kb_id}/assets")
    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json()["items"] if i["document_id"] == doc_id)
    assert item["review_status"] is None
    assert item["status"] == "ready"
