from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


def _client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return client


def _viewer():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _client()
    created = admin.post(
        "/admin/users",
        json={
            "username": "termviewer",
            "display_name": "只读同事",
            "password": "viewer-pass-1",
            "role": "viewer",
        },
    )
    assert created.status_code == 201, created.text
    viewer = TestClient(create_app())
    login = viewer.post(
        "/auth/login", json={"username": "termviewer", "password": "viewer-pass-1"}
    )
    assert login.status_code == 200, login.text
    return viewer


def test_term_crud_and_flat_listing(db):
    client = _client()
    assert client.get("/admin/terms").json() == []

    created = client.post(
        "/admin/terms",
        json={
            "kind": "replace",
            "source": "业主",
            "target": "采购人",
            "note": "统一称谓",
        },
    )
    assert created.status_code == 201, created.text
    term = created.json()
    assert set(term) == {
        "id",
        "kind",
        "source",
        "target",
        "note",
        "enabled",
        "created_at",
        "updated_at",
    }
    assert term["kind"] == "replace"
    assert term["source"] == "业主"
    assert term["target"] == "采购人"
    assert term["enabled"] is True
    assert term["created_at"]
    assert term["updated_at"]
    assert term["note"] == "统一称谓"

    forbidden = client.post(
        "/admin/terms", json={"kind": "forbidden", "source": "绝对领先"}
    )
    assert forbidden.status_code == 201, forbidden.text
    assert forbidden.json()["target"] == ""

    listing = client.get("/admin/terms").json()
    assert isinstance(listing, list)
    assert len(listing) == 2
    assert client.get("/admin/terms", params={"kind": "forbidden"}).json() == [
        forbidden.json()
    ]
    assert [row["source"] for row in client.get("/admin/terms", params={"search": "业主"}).json()] == [
        "业主"
    ]
    assert client.get("/admin/terms", params={"search": "统一称谓"}).json()
    assert client.get("/admin/terms", params={"limit": 1, "offset": 1}).json() == [term]

    term_id = term["id"]
    patched = client.patch(
        f"/admin/terms/{term_id}", json={"target": "招标人", "enabled": False}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["target"] == "招标人"
    assert patched.json()["enabled"] is False
    assert patched.json()["source"] == "业主"
    assert client.patch("/admin/terms/missing", json={"target": "x"}).status_code == 404

    assert client.delete(f"/admin/terms/{term_id}").json() == {"deleted": True, "id": term_id}
    assert client.delete(f"/admin/terms/{term_id}").status_code == 404
    assert len(client.get("/admin/terms").json()) == 1


def test_term_conflicts_and_validation(db):
    client = _client()
    assert (
        client.post(
            "/admin/terms", json={"kind": "replace", "source": "业主", "target": "采购人"}
        ).status_code
        == 201
    )
    # Same (kind, source) conflicts; the same word under another kind is fine.
    assert (
        client.post(
            "/admin/terms", json={"kind": "replace", "source": "业主", "target": "甲方"}
        ).status_code
        == 409
    )
    assert (
        client.post("/admin/terms", json={"kind": "forbidden", "source": "业主"}).status_code
        == 201
    )

    bad_payloads = [
        {"kind": "banned", "source": "x"},
        {"kind": "", "source": "x"},
        {"kind": "forbidden", "source": "   "},
        {"kind": "forbidden", "source": "术" * 121},
        {"kind": "replace", "source": "新术语"},  # replace requires a target
        {"kind": "replace", "source": "新术语", "target": "  "},
        {"kind": "forbidden", "source": "长备注", "note": "备" * 201},
        {"kind": "forbidden", "source": "长替换", "target": "替" * 121},
    ]
    for payload in bad_payloads:
        response = client.post("/admin/terms", json=payload)
        assert response.status_code == 400, (payload, response.text)

    assert client.get("/admin/terms", params={"kind": "nonsense"}).status_code == 400

    row = client.get("/admin/terms", params={"kind": "replace"}).json()[0]
    assert client.patch(f"/admin/terms/{row['id']}", json={"kind": "bad"}).status_code == 400
    assert client.patch(f"/admin/terms/{row['id']}", json={"target": ""}).status_code == 400
    assert client.patch(f"/admin/terms/{row['id']}", json={"source": " "}).status_code == 400
    assert client.patch(f"/admin/terms/{row['id']}", json={"note": "备" * 201}).status_code == 400
    assert (
        client.patch(
            f"/admin/terms/{row['id']}", json={"kind": "forbidden", "source": "业主"}
        ).status_code
        == 409
    )
    # Switching a forbidden row to replace without a target is rejected too.
    extra = client.post("/admin/terms", json={"kind": "forbidden", "source": "史无前例"}).json()
    assert (
        client.patch(f"/admin/terms/{extra['id']}", json={"kind": "replace"}).status_code == 400
    )
    assert (
        client.patch(
            f"/admin/terms/{extra['id']}", json={"kind": "replace", "target": "采购人"}
        ).status_code
        == 200
    )


def test_viewer_cannot_manage_terms(db):
    viewer = _viewer()
    assert viewer.get("/admin/terms").status_code == 403
    assert (
        viewer.post("/admin/terms", json={"kind": "forbidden", "source": "禁止"}).status_code
        == 403
    )
    assert viewer.patch("/admin/terms/whatever", json={"note": "x"}).status_code == 403
    assert viewer.delete("/admin/terms/whatever").status_code == 403


def test_scan_body_forbidden_and_replace(db):
    from bidcraft import terms
    from bidcraft.db import session_scope

    with session_scope() as session:
        terms.create_term(session, kind="forbidden", source="必须满足")
        terms.create_term(session, kind="replace", source="业主", target="采购人")
        terms.create_term(session, kind="forbidden", source="停用的词", enabled=False)

    with session_scope() as session:
        assert terms.scan_body(session, "") == []
        assert terms.scan_body(session, "停用的词出现在正文里。") == []

        findings = terms.scan_body(session, "本方案必须满足规范，业主负责验收。", heading="技术方案")
        assert [item["id"] for item in findings] == ["TERM-001", "TERM-002"]
        by_source = {item["evidence"]["source"]: item for item in findings}
        forbidden = by_source["必须满足"]
        assert forbidden["level"] == "major"
        assert forbidden["category"] == "术语规范"
        assert forbidden["location"] == "技术方案"
        assert "必须满足" in forbidden["message"]
        assert "必须满足" in forbidden["recommendation"]
        assert len(forbidden["evidence"]["quote"]) <= terms.EVIDENCE_LIMIT
        assert forbidden["evidence"]["quote"].startswith("必须满足")
        replace = by_source["业主"]
        assert replace["level"] == "minor"
        assert replace["evidence"]["target"] == "采购人"
        assert "采购人" in replace["recommendation"]

        # Long bodies keep the evidence quote bounded.
        long_body = "垫" * 200 + "必须满足" + "垫" * 200
        quoted = terms.scan_body(session, long_body)[0]["evidence"]["quote"]
        assert len(quoted) == terms.EVIDENCE_LIMIT

        # Numbering can continue across chapters.
        continued = terms.scan_body(session, "业主代表列席。", heading="服务承诺", start=3)
        assert continued[0]["id"] == "TERM-003"
        assert continued[0]["location"] == "服务承诺"

        assert terms.scan_body(session, "采购人负责验收。") == []

    # Without an explicit session the scanner opens its own short-lived one.
    assert len(terms.scan_body(None, "本方案必须满足规范。")) == 1


def test_check_project_reports_term_findings(db):
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import Issue, OutlineNode, Section
    from bidcraft.pipeline import check_project, create_project

    admin = _client()
    assert (
        admin.post("/admin/terms", json={"kind": "forbidden", "source": "必须满足"}).status_code
        == 201
    )
    assert (
        admin.post(
            "/admin/terms",
            json={"kind": "replace", "source": "业主", "target": "采购人"},
        ).status_code
        == 201
    )
    disabled = admin.post(
        "/admin/terms",
        json={"kind": "forbidden", "source": "禁用但不启用", "enabled": False},
    )
    assert disabled.status_code == 201

    with session_scope() as session:
        project = create_project(session, "术语核对项目", "weakcurrent-cabling")
        first = OutlineNode(project_id=project.id, heading="技术方案", position=1)
        second = OutlineNode(project_id=project.id, heading="服务承诺", position=2)
        session.add_all([first, second])
        session.flush()
        session.add(
            Section(node_id=first.id, body="本方案必须满足规范，业主负责验收。禁用但不启用。")
        )
        session.add(Section(node_id=second.id, body="必须满足响应时限。"))
        session.commit()

        results, summary = check_project(session, project.id)
        term_results = [result for result in results if result.code.startswith("TERM")]
        # Appended chapter by chapter: two hits in 技术方案, one in 服务承诺.
        assert [result.code for result in term_results] == ["TERM-001", "TERM-002", "TERM-003"]
        assert [result.level for result in term_results] == ["major", "minor", "major"]
        assert all(result.category == "术语规范" for result in term_results)
        assert [result.location for result in term_results] == ["技术方案", "技术方案", "服务承诺"]
        assert all(result.recommendation for result in term_results)
        assert all(not result.passed for result in term_results)
        assert summary["failed"] >= len(term_results)
        assert summary["by_level"]["major"] >= 2
        stored = list(session.scalars(select(Issue).where(Issue.project_id == project.id)))
        stored_terms = [row for row in stored if row.code.startswith("TERM")]
        assert len(stored_terms) == 3
        assert all(row.status == "open" for row in stored_terms)
        assert all(row.evidence["quote"] for row in stored_terms)
        session.commit()

    checked = admin.post(f"/projects/{project.id}/check")
    assert checked.status_code == 200, checked.text
    payload = checked.json()
    assert any(issue["code"].startswith("TERM") for issue in payload["issues"])
    issues = admin.get(f"/projects/{project.id}/issues").json()
    assert any(row["code"].startswith("TERM") for row in issues)


def test_term_mutations_are_audited(db):
    client = _client()
    created = client.post(
        "/admin/terms", json={"kind": "forbidden", "source": "绝对禁止"}
    ).json()
    client.patch(f"/admin/terms/{created['id']}", json={"note": "备注"})
    client.delete(f"/admin/terms/{created['id']}")

    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    with session_scope() as session:
        rows = list(
            session.scalars(select(AuditLog).where(AuditLog.action.like("term.%")))
        )
    actions = {row.action for row in rows}
    assert {"term.create", "term.update", "term.delete"} <= actions
    assert all(row.project_id is None for row in rows)
    assert all(row.username == "admin" for row in rows)
    assert all(row.detail.get("term_id") == created["id"] for row in rows)
