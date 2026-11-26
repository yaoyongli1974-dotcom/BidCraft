"""商务标状态机、审批链复用与 HTTP 权限。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from bidcraft import commercial
from bidcraft.db import session_scope
from bidcraft.models import (
    AuditLog,
    BoqQuoteVersion,
    CommercialDoc,
    Project,
    User,
    utcnow,
)

pytest.importorskip("fastapi")

PASSWORD = "commercial-pass-1"
ADMIN = ("admin", "test-admin")


@pytest.fixture(autouse=True)
def _isolated_db(db):
    yield db


def _seed() -> dict[str, str]:
    with session_scope() as db:
        owner = User(
            username="comm-flow-owner",
            display_name="王经理",
            password_hash="x",
            role="manager",
        )
        approver_one = User(
            username="comm-approver-1",
            display_name="审批人甲",
            password_hash="x",
            role="manager",
        )
        approver_two = User(
            username="comm-approver-2",
            display_name="审批人乙",
            password_hash="x",
            role="manager",
        )
        db.add_all([owner, approver_one, approver_two])
        db.flush()
        project = Project(name="商务标流程项目", owner_id=owner.id)
        db.add(project)
        db.flush()
        version = BoqQuoteVersion(
            project_id=project.id,
            package_id=None,
            name="定稿报价V1",
            status="approved",
            prices={"清单!2": 100.0},
            summary={"total_amount": 200.0, "tax_amount": 26.0, "total_with_tax": 226.0},
            basis={
                "qty": {"清单!2": 2.0},
                "tax_rate": 0.13,
                "currency": "CNY",
                "exchange_rate": 1.0,
                "factor": 1.0,
                "round_digits": 2,
            },
            reviewed_by="李总",
            reviewed_at=utcnow(),
        )
        db.add(version)
        db.flush()
        return {
            "project_id": project.id,
            "version_id": version.id,
            "owner_id": owner.id,
            "approver_one": approver_one.id,
            "approver_two": approver_two.id,
        }


def _actor(user_id: str, name: str) -> dict[str, str]:
    return {"id": user_id, "username": name, "display_name": name}


def _actions(project_id: str) -> list[str]:
    with session_scope() as db:
        rows = db.scalars(
            select(AuditLog)
            .where(AuditLog.project_id == project_id)
            .order_by(AuditLog.id)
        )
        return [row.action for row in rows]


def test_single_approver_state_machine_and_audit():
    seed = _seed()
    actor = _actor(seed["owner_id"], "王经理")
    with session_scope() as db:
        project = db.get(Project, seed["project_id"])
        doc = commercial.save_commercial(
            db, project, quote_version_id=seed["version_id"], actor=actor
        )
        assert doc["status"] == "draft"
        assert doc["created_by"] == "王经理"

        submitted = commercial.submit_commercial(
            db,
            project,
            doc["id"],
            note="请审批",
            actor=actor,
            actor_id=seed["owner_id"],
            role="owner",
        )
        assert submitted["status"] == "submitted"
        assert submitted["submitted_by"] == "王经理"

        approved = commercial.review_commercial(
            db,
            project,
            doc["id"],
            decision="approve",
            note="同意",
            actor=actor,
            actor_id=seed["owner_id"],
            role="owner",
        )
        assert approved["status"] == "approved"
        assert approved["validation"]["review"]["by"] == "王经理"

        # 已审批不可修改/删除，但可撤销。
        with pytest.raises(commercial.CommercialConflictError):
            commercial.update_commercial(db, project, doc["id"], name="改名", actor=actor)
        with pytest.raises(commercial.CommercialConflictError):
            commercial.delete_commercial(db, project, doc["id"], actor=actor)

        withdrawn = commercial.unapprove_commercial(
            db, project, doc["id"], actor=actor
        )
        assert withdrawn["status"] == "draft"
        commercial.delete_commercial(db, project, doc["id"], actor=actor)

    assert _actions(seed["project_id"]) == [
        "commercial.save",
        "commercial.submit",
        "commercial.approve",
        "commercial.unapprove",
        "commercial.delete",
    ]


def test_reject_then_resubmit():
    seed = _seed()
    actor = _actor(seed["owner_id"], "王经理")
    with session_scope() as db:
        project = db.get(Project, seed["project_id"])
        doc = commercial.save_commercial(
            db, project, quote_version_id=seed["version_id"], actor=actor
        )
        commercial.submit_commercial(
            db, project, doc["id"], actor=actor, actor_id=seed["owner_id"], role="owner"
        )
        rejected = commercial.review_commercial(
            db,
            project,
            doc["id"],
            decision="reject",
            note="金额页需复核",
            actor=actor,
            actor_id=seed["owner_id"],
            role="owner",
        )
        assert rejected["status"] == "rejected"
        resubmitted = commercial.submit_commercial(
            db, project, doc["id"], actor=actor, actor_id=seed["owner_id"], role="owner"
        )
        assert resubmitted["status"] == "submitted"
        assert resubmitted["validation"]["approvals"] == []
        actions = [
            row.action
            for row in db.scalars(
                select(AuditLog).where(AuditLog.project_id == seed["project_id"])
            )
        ]
        assert "commercial.reject" in actions


def test_chain_approvers_and_duplicate_signature_guard():
    seed = _seed()
    owner = _actor(seed["owner_id"], "王经理")
    first = _actor(seed["approver_one"], "审批人甲")
    second = _actor(seed["approver_two"], "审批人乙")
    with session_scope() as db:
        project = db.get(Project, seed["project_id"])
        project.meta = {
            "approval_chain": [
                {"id": seed["approver_one"], "name": "审批人甲"},
                {"id": seed["approver_two"], "name": "审批人乙"},
            ]
        }
        db.flush()
        doc = commercial.save_commercial(
            db, project, quote_version_id=seed["version_id"], actor=owner
        )
        commercial.submit_commercial(
            db, project, doc["id"], actor=owner, actor_id=seed["owner_id"], role="owner"
        )
        with pytest.raises(commercial.CommercialForbiddenError):
            commercial.review_commercial(
                db,
                project,
                doc["id"],
                decision="approve",
                actor=second,
                actor_id=seed["approver_two"],
                role="editor",
            )
        stage_one = commercial.review_commercial(
            db,
            project,
            doc["id"],
            decision="approve",
            actor=first,
            actor_id=seed["approver_one"],
            role="editor",
        )
        assert stage_one["status"] == "submitted"
        with pytest.raises(commercial.CommercialForbiddenError):
            commercial.review_commercial(
                db,
                project,
                doc["id"],
                decision="approve",
                actor=first,
                actor_id=seed["approver_one"],
                role="editor",
            )
        final = commercial.review_commercial(
            db,
            project,
            doc["id"],
            decision="approve",
            actor=second,
            actor_id=seed["approver_two"],
            role="editor",
        )
        assert final["status"] == "approved"


def test_chain_amount_threshold_reuses_boq_applicable_stages():
    seed = _seed()
    owner = _actor(seed["owner_id"], "王经理")
    second = _actor(seed["approver_two"], "审批人乙")
    with session_scope() as db:
        project = db.get(Project, seed["project_id"])
        # 第一级只对 ≥1,000,000 的报价生效；本项目 200 元，仅第二级参与。
        project.meta = {
            "approval_chain": [
                {"id": seed["approver_one"], "name": "大额审批", "min_amount": 1_000_000},
                {"id": seed["approver_two"], "name": "常规审批"},
            ]
        }
        db.flush()
        doc = commercial.save_commercial(
            db, project, quote_version_id=seed["version_id"], actor=owner
        )
        commercial.submit_commercial(
            db, project, doc["id"], actor=owner, actor_id=seed["owner_id"], role="owner"
        )
        final = commercial.review_commercial(
            db,
            project,
            doc["id"],
            decision="approve",
            actor=second,
            actor_id=seed["approver_two"],
            role="editor",
        )
        assert final["status"] == "approved"


def test_submit_blocks_unapproved_or_inconsistent_quote():
    seed = _seed()
    actor = _actor(seed["owner_id"], "王经理")
    with session_scope() as db:
        project = db.get(Project, seed["project_id"])
        version = db.get(BoqQuoteVersion, seed["version_id"])
        version.status = "draft"
        db.flush()
        doc = commercial.save_commercial(
            db, project, quote_version_id=seed["version_id"], actor=actor
        )
        with pytest.raises(commercial.CommercialConflictError):
            commercial.submit_commercial(
                db,
                project,
                doc["id"],
                actor=actor,
                actor_id=seed["owner_id"],
                role="owner",
            )


# --------------------------------------------------------------- HTTP layer


def _login(username: str, password: str):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return client


def _make_user(admin, username: str) -> dict:
    response = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": PASSWORD,
            "role": "manager",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_project(client, name: str) -> dict:
    response = client.post("/projects", json={"name": name, "package": "weakcurrent-cabling"})
    assert response.status_code == 201, response.text
    return response.json()


def _seed_quote(project_id: str) -> str:
    with session_scope() as db:
        version = BoqQuoteVersion(
            project_id=project_id,
            package_id=None,
            name="HTTP定稿",
            status="approved",
            prices={"清单!2": 10.0},
            summary={"total_amount": 20.0, "tax_amount": 2.6, "total_with_tax": 22.6},
            basis={"qty": {"清单!2": 2.0}, "tax_rate": 0.13, "currency": "CNY"},
            reviewed_by="李总",
            reviewed_at=utcnow(),
        )
        db.add(version)
        db.flush()
        return version.id


@pytest.fixture()
def team(db):
    admin = _login(*ADMIN)
    _make_user(admin, "comm-http-owner")
    _make_user(admin, "comm-http-editor")
    _make_user(admin, "comm-http-viewer")
    return {
        "admin": admin,
        "owner": _login("comm-http-owner", PASSWORD),
        "editor": _login("comm-http-editor", PASSWORD),
        "viewer": _login("comm-http-viewer", PASSWORD),
    }


def test_http_permission_matrix_and_status_flow(team):
    project = _make_project(team["owner"], "商务标权限项目")
    project_id = project["id"]
    for username, role in (
        ("comm-http-editor", "editor"),
        ("comm-http-viewer", "viewer"),
    ):
        response = team["owner"].post(
            f"/projects/{project_id}/members",
            json={"username": username, "role": role},
        )
        assert response.status_code == 201, response.text
    version_id = _seed_quote(project_id)
    body = {"name": "商务标", "quote_version_id": version_id}

    assert team["viewer"].post(f"/projects/{project_id}/commercial", json=body).status_code == 403
    created = team["owner"].post(f"/projects/{project_id}/commercial", json=body)
    assert created.status_code == 201, created.text
    doc_id = created.json()["id"]
    assert created.json()["status"] == "draft"

    assert team["viewer"].get(f"/projects/{project_id}/commercial/{doc_id}").status_code == 200
    assert team["editor"].post(f"/projects/{project_id}/commercial/{doc_id}/submit").status_code == 403
    submitted = team["owner"].post(
        f"/projects/{project_id}/commercial/{doc_id}/submit", json={"note": "送审"}
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"

    assert team["viewer"].post(
        f"/projects/{project_id}/commercial/{doc_id}/review", json={"action": "approve"}
    ).status_code == 403
    approved = team["owner"].post(
        f"/projects/{project_id}/commercial/{doc_id}/review",
        json={"action": "approve", "note": "同意"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    assert team["editor"].post(
        f"/projects/{project_id}/commercial/{doc_id}/unapprove"
    ).status_code == 403
    withdrawn = team["owner"].post(
        f"/projects/{project_id}/commercial/{doc_id}/unapprove"
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "draft"

    assert team["editor"].delete(
        f"/projects/{project_id}/commercial/{doc_id}"
    ).status_code == 200


def test_chain_numeric_rule_uses_stored_quote_summary():
    """priced ≥ N 的规则必须读落库 validation.quote，而不是退回空摘要。"""

    seed = _seed()
    owner = _actor(seed["owner_id"], "王经理")
    rule_approver = _actor(seed["approver_one"], "审批人甲")
    fallback = _actor(seed["approver_two"], "审批人乙")
    chain = [
        {
            "approvers": [{"id": seed["approver_one"], "name": "规则审批"}],
            "mode": "all",
            "rules": [{"field": "priced", "op": "gte", "value": 8}],
        },
        {
            "approvers": [{"id": seed["approver_two"], "name": "兜底审批"}],
            "mode": "any",
        },
    ]

    def _submit_with_priced(priced: int) -> str:
        with session_scope() as db:
            project = db.get(Project, seed["project_id"])
            project.meta = {"approval_chain": chain}
            doc_payload = commercial.save_commercial(
                db, project, quote_version_id=seed["version_id"], actor=owner
            )
            commercial.submit_commercial(
                db,
                project,
                doc_payload["id"],
                actor=owner,
                actor_id=seed["owner_id"],
                role="owner",
            )
            row = db.get(CommercialDoc, doc_payload["id"])
            validation = dict(row.validation or {})
            quote = dict(validation.get("quote") or {})
            quote["priced"] = priced
            validation["quote"] = quote
            row.validation = validation
            db.flush()
            return doc_payload["id"]

    # priced=7 → 规则级未命中，兜底审批人直接定稿。
    low_id = _submit_with_priced(7)
    with session_scope() as db:
        project = db.get(Project, seed["project_id"])
        approved = commercial.review_commercial(
            db,
            project,
            low_id,
            decision="approve",
            actor=fallback,
            actor_id=seed["approver_two"],
            role="editor",
        )
    assert approved["status"] == "approved"
    assert [record["stage"] for record in approved["validation"]["approvals"]] == [1]

    # priced=10 → 规则级生效，兜底审批人不是当前级，被 403；规则审批人可批。
    high_id = _submit_with_priced(10)
    with session_scope() as db:
        project = db.get(Project, seed["project_id"])
        try:
            commercial.review_commercial(
                db,
                project,
                high_id,
                decision="approve",
                actor=fallback,
                actor_id=seed["approver_two"],
                role="editor",
            )
        except commercial.CommercialForbiddenError:
            pass
        else:
            raise AssertionError("规则命中时兜底审批人不得审批")
        result = commercial.review_commercial(
            db,
            project,
            high_id,
            decision="approve",
            actor=rule_approver,
            actor_id=seed["approver_one"],
            role="editor",
        )
    assert result["status"] == "submitted"  # 还有兜底级
    assert [record["stage"] for record in result["validation"]["approvals"]] == [0]
