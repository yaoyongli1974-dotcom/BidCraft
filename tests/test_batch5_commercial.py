"""Batch 5 商务标守护：包件选择、导出一致性、共享资产、状态机与审批收件箱。"""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("fastapi")

from bidcraft import boq, commercial, qualifications  # noqa: E402
from bidcraft.db import session_scope  # noqa: E402
from bidcraft.models import (  # noqa: E402
    AuditLog,
    BidPackage,
    BoqQuoteVersion,
    CommercialDoc,
    Enterprise,
    EnterpriseMember,
    Fact,
    Personnel,
    Project,
    Qualification,
    User,
    utcnow,
)

TOTAL = 250.5
TAX = 32.56
TOTAL_WITH_TAX = 283.06


def _seed(db, *, package_id=None, approved=True, currency="CNY", user_id=None):
    user = db.get(User, user_id) if user_id else None
    if user is None:
        user = User(
            username="b5-owner", display_name="商务经理", password_hash="x", role="manager"
        )
        db.add(user)
        db.flush()
    project = Project(name="批次五项目", owner_id=user.id, buyer="招标人")
    db.add(project)
    db.flush()
    for key, value in (
        ("project.number", "B5-2026"),
        ("bidder.name", "投标人甲"),
        ("project.bid_validity", "90 天"),
    ):
        db.add(Fact(project_id=project.id, key=key, label=key, value=value, status="confirmed"))
    version = _version(
        db,
        project,
        package_id=package_id,
        approved=approved,
        currency=currency,
        name="V1",
    )
    return {"project": project, "user": user, "version": version}


def _version(db, project, *, package_id=None, approved=True, currency="CNY", name="V"):
    version = BoqQuoteVersion(
        project_id=project.id,
        package_id=package_id,
        name=name,
        status="approved" if approved else "draft",
        prices={"清单!2": 100.0, "清单!3": 50.5},
        summary={
            "total_amount": TOTAL,
            "tax_amount": TAX,
            "total_with_tax": TOTAL_WITH_TAX,
        },
        basis={
            "qty": {"清单!2": 2.0, "清单!3": 1.0},
            "tax_rate": 0.13,
            "currency": currency,
            "exchange_rate": 1.0,
            "factor": 1.0,
            "round_digits": 2,
        },
        reviewed_by="审批人" if approved else "",
        reviewed_at=utcnow() if approved else None,
    )
    db.add(version)
    db.flush()
    return version


def _login():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return client


def _approval_user(db, username):
    user = User(username=username, display_name=username, password_hash="x", role="manager")
    db.add(user)
    db.flush()
    return user


@pytest.mark.usefixtures("db")
def test_enabled_packages_require_package_id_in_both_queries_and_create():
    """包件项目的 versions 列表与商务标创建都必须携带 package_id。"""

    client = _login()
    with session_scope() as db:
        seed = _seed(db)
        project_id = seed["project"].id
        package_a = BidPackage(project_id=project_id, name="一标段", position=1)
        package_b = BidPackage(project_id=project_id, name="二标段", position=2)
        db.add_all([package_a, package_b])
        db.flush()
        # 把 V1 挪到包件 A，并为包件 B 准备另一份定稿版本。
        seed["version"].package_id = package_a.id
        version_b = _version(db, seed["project"], package_id=package_b.id, name="B-V1")
        ids = {
            "a": package_a.id,
            "b": package_b.id,
            "version_a": seed["version"].id,
            "version_b": version_b.id,
        }

    missing = client.get(f"/projects/{project_id}/boq/versions")
    assert missing.status_code == 400, missing.text
    assert "请指定包件" in missing.json()["detail"]

    listed_a = client.get(
        f"/projects/{project_id}/boq/versions", params={"package_id": ids["a"]}
    )
    assert listed_a.status_code == 200, listed_a.text
    assert [item["id"] for item in listed_a.json()] == [ids["version_a"]]
    listed_b = client.get(
        f"/projects/{project_id}/boq/versions", params={"package_id": ids["b"]}
    )
    assert [item["id"] for item in listed_b.json()] == [ids["version_b"]]

    created_without = client.post(
        f"/projects/{project_id}/commercial",
        json={"name": "无包件", "quote_version_id": ids["version_a"]},
    )
    # 指定版本时后端可推断包件；不指定版本才必须显式给出包件。
    assert created_without.status_code == 201, created_without.text

    no_version_no_package = client.post(
        f"/projects/{project_id}/commercial", json={"name": "无包件无版本"}
    )
    assert no_version_no_package.status_code == 400, no_version_no_package.text

    created = client.post(
        f"/projects/{project_id}/commercial",
        json={
            "name": "包件B商务标",
            "quote_version_id": ids["version_b"],
            "package_id": ids["b"],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["package_id"] == ids["b"]

    listed_docs = client.get(
        f"/projects/{project_id}/commercial", params={"package_id": ids["b"]}
    )
    assert [doc["id"] for doc in listed_docs.json()] == [created.json()["id"]]


@pytest.mark.usefixtures("db")
def test_export_gate_blocks_stale_quote_and_force_needs_reason():
    """导出前必须重新校验；旧版本需 force + 审计原因。"""

    with session_scope() as db:
        seed = _seed(db)
        project, version = seed["project"], seed["version"]
        saved = commercial.save_commercial(
            db, project, quote_version_id=version.id, actor={"id": seed["user"].id}
        )
        doc = db.get(CommercialDoc, saved["id"])
        payload = commercial.export_payload(db, project, doc)
        assert payload["validation"]["ok"] is True

        # 新版本顶替旧版本：旧商务标引用已失效。
        version.status = "draft"
        replacement = _version(db, project, name="V2")
        payload = commercial.export_payload(db, project, doc)
        assert payload["validation"]["ok"] is False
        assert any("旧版本" in err or "旧版" in err for err in payload["validation"]["errors"])
        with pytest.raises(commercial.CommercialConflictError) as exc:
            commercial.ensure_commercial_export_allowed(db, project, doc, fmt="xlsx")
        assert "不能导出" in str(exc.value)

        with pytest.raises(ValueError):
            commercial.ensure_commercial_export_allowed(
                db, project, doc, fmt="xlsx", force=True, force_reason="x"
            )
        commercial.ensure_commercial_export_allowed(
            db,
            project,
            doc,
            fmt="xlsx",
            force=True,
            force_reason="客户书面确认使用旧报价",
            actor={"id": seed["user"].id, "display_name": "商务经理"},
        )
        forced = [
            row
            for row in db.query(AuditLog)
            .filter(AuditLog.project_id == project.id)
            .all()
            if row.action == "commercial.export_force"
        ]
        assert len(forced) == 1
        assert forced[0].detail["reason"] == "客户书面确认使用旧报价"
        assert forced[0].detail["doc_id"] == doc.id
        assert replacement.id != version.id


@pytest.mark.usefixtures("db")
def test_submitted_doc_is_frozen_for_put():
    """送审中的章节不可通过 PUT 偷改。"""

    client = _login()
    with session_scope() as db:
        seed = _seed(db)
        saved = commercial.save_commercial(
            db, seed["project"], quote_version_id=seed["version"].id
        )
        project_id = seed["project"].id
        doc_id = saved["id"]

    submitted = client.post(f"/projects/{project_id}/commercial/{doc_id}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"

    changed = client.put(
        f"/projects/{project_id}/commercial/{doc_id}", json={"name": "送审后改名"}
    )
    assert changed.status_code == 409, changed.text
    with session_scope() as db:
        assert db.get(CommercialDoc, doc_id).name == "商务标"
        assert db.get(CommercialDoc, doc_id).status == "submitted"


@pytest.mark.usefixtures("db")
def test_quote_version_delete_rejects_approved_and_referenced():
    """已定稿或被商务标引用的报价版本不可删除。"""

    with session_scope() as db:
        seed = _seed(db)
        project, version = seed["project"], seed["version"]
        with pytest.raises(boq.BoqConflictError) as approved_exc:
            boq.delete_version(db, project, version.id)
        assert "已定稿" in str(approved_exc.value)

        version.status = "draft"
        saved = commercial.save_commercial(
            db, project, quote_version_id=version.id
        )
        assert saved["quote_version_id"] == version.id
        with pytest.raises(boq.BoqConflictError) as referenced_exc:
            boq.delete_version(db, project, version.id)
        assert "商务标引用" in str(referenced_exc.value)
        assert db.get(BoqQuoteVersion, version.id) is not None

        # 未定稿且无人引用时仍可删除（保持既有行为）。
        orphan = _version(db, project, name="孤立草稿", approved=False)
        boq.delete_version(db, project, orphan.id)
        assert db.get(BoqQuoteVersion, orphan.id) is None


@pytest.mark.usefixtures("db")
def test_approval_currency_uses_version_basis_not_current_policy():
    """USD 版本在 CNY 策略下仍走 USD 审批分支。"""

    with session_scope() as db:
        seed = _seed(db, currency="CNY")
        project = seed["project"]
        alice = _approval_user(db, "b5-usd-alice")
        bob = _approval_user(db, "b5-usd-bob")
        project.meta = {
            "price_policy": {"currency": "CNY"},
            "approval_chain": [
                {
                    "approvers": [{"id": alice.id, "name": "Alice"}],
                    "mode": "all",
                    "currencies": ["USD"],
                },
                {
                    "approvers": [{"id": bob.id, "name": "Bob"}],
                    "mode": "all",
                },
            ],
        }
        version = _version(db, project, currency="USD", name="USD版")
        version.status = "submitted"
        assert boq.version_currency(version, project) == "USD"
        result = boq.review_version(
            db,
            project,
            version.id,
            actor="Alice",
            actor_id=alice.id,
            role="editor",
            decision="approve",
        )
        assert result["approvals"][0]["stage"] == 0
        assert result["status"] == "submitted"  # 仍是中间审批级
        # 第二级审批人可完成定稿。
        final = boq.review_version(
            db,
            project,
            version.id,
            actor="Bob",
            actor_id=bob.id,
            role="editor",
            decision="approve",
        )
        assert final["status"] == "approved"
        assert [record["stage"] for record in final["approvals"]] == [0, 1]


@pytest.mark.usefixtures("db")
def test_shared_enterprise_assets_appear_in_commercial_and_preflight():
    """企业成员登记共享资质/人员/业绩后，项目负责人可见。"""

    with session_scope() as db:
        seed = _seed(db)
        owner, project = seed["user"], seed["project"]
        member = _approval_user(db, "b5-ent-member")
        enterprise = Enterprise(name="批次五企业", owner_id=owner.id)
        db.add(enterprise)
        db.flush()
        db.add(
            EnterpriseMember(enterprise_id=enterprise.id, user_id=member.id, role="member")
        )
        db.add_all(
            [
                Qualification(
                    owner_id=member.id,
                    enterprise_id=enterprise.id,
                    kind="license",
                    name="企业共享资质",
                    holder="投标人甲",
                    valid_to=utcnow() + timedelta(days=365),
                ),
                Qualification(
                    owner_id=member.id,
                    enterprise_id=enterprise.id,
                    kind="performance",
                    name="企业共享业绩",
                    holder="发包人乙",
                ),
                Personnel(
                    owner_id=member.id,
                    enterprise_id=enterprise.id,
                    name="共享人员李四",
                    title="技术负责人",
                ),
            ]
        )
        payload = commercial.build_commercial_payload(
            db, project, quote_version_id=seed["version"].id
        )
        checklist = payload["sections"]["qualification_checklist"]
        performance = payload["sections"]["performance_table"]
        people = payload["sections"]["personnel_table"]
        assert any(row["name"] == "企业共享资质" for row in checklist["rows"])
        assert any(row["shared"] for row in checklist["rows"])
        assert any(row["name"] == "企业共享业绩" for row in performance["rows"])
        assert any(row["name"] == "共享人员李四" for row in people["rows"])

        preflight = qualifications.preflight(db, {"id": owner.id})
        shared_items = [item for item in preflight["items"] if item["name"] == "企业共享资质"]
        assert shared_items
        assert shared_items[0]["status"] == "ok"


@pytest.mark.usefixtures("db")
def test_expired_qualification_and_locked_personnel_are_flagged():
    """过期资质与在建锁定人员在商务表里有 status 和 warnings。"""

    with session_scope() as db:
        seed = _seed(db)
        project = seed["project"]
        db.add(
            Qualification(
                owner_id=seed["user"].id,
                kind="license",
                name="过期资质",
                valid_to=utcnow() - timedelta(days=5),
            )
        )
        db.add(
            Personnel(
                owner_id=seed["user"].id,
                name="锁定人员",
                title="项目经理",
                locked=True,
            )
        )
        payload = commercial.build_commercial_payload(
            db, project, quote_version_id=seed["version"].id
        )
        rows = payload["sections"]["qualification_checklist"]["rows"]
        expired = next(row for row in rows if row["name"] == "过期资质")
        assert expired["status"] == "expired"
        assert expired["status_label"] == "已过期"
        assert any("已过期" in note for note in payload["sections"]["qualification_checklist"]["notes"])
        assert any("已过期" in warn for warn in payload["validation"]["warnings"])

        people_section = payload["sections"]["personnel_table"]
        locked = next(row for row in people_section["rows"] if row["name"] == "锁定人员")
        assert locked["status"] == "locked"
        assert any("在建锁定" in note for note in people_section["notes"])
        assert any("在建锁定" in warn for warn in payload["validation"]["warnings"])


@pytest.mark.usefixtures("db")
def test_submitted_commercial_lands_in_global_pending_inbox():
    """已提交商务标进入 /approvals/pending，与报价版本同列。"""

    client = _login()
    with session_scope() as db:
        seed = _seed(db)
        saved = commercial.save_commercial(
            db, seed["project"], quote_version_id=seed["version"].id
        )
        commercial.submit_commercial(
            db,
            seed["project"],
            saved["id"],
            actor={"id": seed["user"].id, "display_name": "商务经理"},
            actor_id=seed["user"].id,
            role="owner",
        )
        inbox = commercial.pending_for_user(
            db, {"id": seed["user"].id, "role": "manager"}
        )
        assert [item["doc_id"] for item in inbox] == [saved["id"]]
        assert inbox[0]["version_id"] == saved["id"]
        assert inbox[0]["stage_count"] == 1
        outsider = commercial.pending_for_user(db, {"id": "not-a-member", "role": "manager"})
        assert outsider == []

    response = client.get("/approvals/pending")
    assert response.status_code == 200, response.text
    assert any(item["doc_id"] == saved["id"] for item in response.json())
