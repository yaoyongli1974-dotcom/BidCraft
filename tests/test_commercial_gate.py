"""商务标提交时的可选完整性硬门槛。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bidcraft import commercial
from bidcraft.api import create_app
from bidcraft.commercial import CommercialConflictError
from bidcraft.db import session_scope
from bidcraft.models import BoqQuoteVersion, Project, User, utcnow

pytest.importorskip("fastapi")


@pytest.fixture(autouse=True)
def _isolated_db(db):
    yield db


def _seed() -> dict[str, str]:
    with session_scope() as db:
        owner = User(
            username="gate-owner", display_name="王经理", password_hash="x", role="manager"
        )
        db.add(owner)
        db.flush()
        project = Project(name="完整性门槛项目", owner_id=owner.id)
        db.add(project)
        db.flush()
        version = BoqQuoteVersion(
            project_id=project.id,
            package_id=None,
            name="定稿报价",
            status="approved",
            prices={"清单!2": 100.0},
            summary={"total_amount": 200.0, "tax_amount": 26.0, "total_with_tax": 226.0},
            basis={"qty": {"清单!2": 2.0}, "tax_rate": 0.13},
            reviewed_by="李总",
            reviewed_at=utcnow(),
        )
        db.add(version)
        db.flush()
        return {
            "project_id": project.id,
            "version_id": version.id,
            "owner_id": owner.id,
        }


def _payload(*, complete: bool) -> dict:
    return {
        "ready": True,
        "sections": {},
        "quote": {"version_id": "v1"},
        "validation": {"ok": True, "errors": []},
        "completeness": {
            "ok": complete,
            "missing": ["投标函.投标人", "投标函.报价"] if not complete else [],
            "checked": 16,
        },
    }


def _submit(monkeypatch, seed, *, complete: bool, require_complete: bool):
    monkeypatch.setattr(
        commercial, "build_commercial_payload", lambda *a, **k: _payload(complete=complete)
    )
    actor = {"id": seed["owner_id"], "username": "王经理", "display_name": "王经理"}
    with session_scope() as db:
        project = db.get(Project, seed["project_id"])
        doc = commercial.save_commercial(
            db, project, quote_version_id=seed["version_id"], actor=actor
        )
        return commercial.submit_commercial(
            db,
            project,
            doc["id"],
            actor=actor,
            actor_id=seed["owner_id"],
            role="owner",
            require_complete=require_complete,
        )


def test_default_submit_allows_incomplete(monkeypatch):
    seed = _seed()
    assert _submit(monkeypatch, seed, complete=False, require_complete=False)["status"] == "submitted"


def test_require_complete_rejects_missing_fields(monkeypatch):
    seed = _seed()
    with pytest.raises(CommercialConflictError) as exc:
        _submit(monkeypatch, seed, complete=False, require_complete=True)
    message = str(exc.value)
    assert "完整性校验未通过" in message
    assert "投标函.投标人" in message
    assert "投标函.报价" in message


def test_require_complete_allows_complete(monkeypatch):
    seed = _seed()
    assert _submit(monkeypatch, seed, complete=True, require_complete=True)["status"] == "submitted"


def test_api_forwards_require_complete(monkeypatch):
    seed = _seed()
    monkeypatch.setattr(
        commercial, "build_commercial_payload", lambda *a, **k: _payload(complete=False)
    )
    client = TestClient(create_app())
    assert client.post(
        "/auth/login", json={"username": "admin", "password": "test-admin"}
    ).status_code == 200
    with session_scope() as db:
        project = db.get(Project, seed["project_id"])
        doc = commercial.save_commercial(
            db,
            project,
            quote_version_id=seed["version_id"],
            actor={"username": "admin", "display_name": "admin"},
        )
        doc_id = doc["id"]
    response = client.post(
        f"/projects/{seed['project_id']}/commercial/{doc_id}/submit",
        json={"note": "送审", "require_complete": True},
    )
    assert response.status_code == 409, response.text
    assert "完整性校验未通过" in response.text
