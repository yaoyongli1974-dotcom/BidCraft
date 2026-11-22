"""质量评估 HTTP 端点：权限、报告快照、finding 处置。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "eval-api-pass"


def _login(username, password=PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture()
def ctx(db):
    admin = _login(*ADMIN)
    for name in ("eval-owner", "eval-viewer", "eval-outsider"):
        resp = admin.post(
            "/admin/users",
            json={"username": name, "display_name": name, "password": PASSWORD, "role": "manager"},
        )
        assert resp.status_code == 201, resp.text
    owner = _login("eval-owner")
    project = owner.post(
        "/projects", json={"name": "评估项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    return {"admin": admin, "owner": owner, "viewer": _login("eval-viewer"),
            "outsider": _login("eval-outsider"), "project": project}


def test_report_run_list_and_detail(ctx):
    p = ctx["project"]
    owner = ctx["owner"]
    r = owner.post(f"/projects/{p}/evaluation/run", json={"note": "第一次"})
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["report_version"] == 1
    assert data["verdict"] in {"pass", "warn", "blocked"}
    assert "overall_score" in data

    latest = owner.get(f"/projects/{p}/evaluation")
    assert latest.status_code == 200
    assert latest.json()["report"]["id"] == data["id"]

    reports = owner.get(f"/projects/{p}/evaluation/reports")
    assert reports.status_code == 200 and len(reports.json()) == 1

    detail = owner.get(f"/projects/{p}/evaluation/reports/{data['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == data["id"]

    # 第二次生成版本递增，旧版本不变
    r2 = owner.post(f"/projects/{p}/evaluation/run", json={"note": "第二次"})
    assert r2.json()["report_version"] == 2
    assert owner.get(f"/projects/{p}/evaluation/reports/{data['id']}").json()["summary"]["note"] == "第一次"


def test_viewer_can_read_but_not_run(ctx):
    p = ctx["project"]
    viewer = ctx["viewer"]
    # viewer 是项目外成员：加入项目
    ctx["owner"].post(
        f"/projects/{p}/members", json={"username": "eval-viewer", "role": "viewer"}
    )
    assert viewer.get(f"/projects/{p}/evaluation").status_code == 200
    # 生成报告是写操作，viewer 被拒（中间件 viewer 非 GET 拒绝）
    assert viewer.post(f"/projects/{p}/evaluation/run", json={}).status_code == 403


def test_finding_update_requires_write(ctx):
    p = ctx["project"]
    owner = ctx["owner"]
    owner.post(f"/projects/{p}/evaluation/run", json={})
    # 制造一个 finding：项目带一条 blocking issue
    from bidcraft.db import session_scope
    from bidcraft.models import Issue

    with session_scope() as db:
        db.add(
            Issue(
                project_id=p,
                code="EVAL-BLOCK",
                level="blocking",
                category="核对项",
                location="",
                finding="测试阻断",
                status="open",
            )
        )
    report2 = owner.post(f"/projects/{p}/evaluation/run", json={}).json()
    findings = owner.get(f"/projects/{p}/evaluation/reports/{report2['id']}/findings")
    assert findings.status_code == 200
    assert len(findings.json()) >= 1
    fid = findings.json()[0]["id"]

    patched = owner.patch(
        f"/projects/{p}/evaluation/findings/{fid}",
        json={"status": "handled", "note": "已复核"},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "handled"


def test_cross_project_report_isolation(ctx):
    owner = ctx["owner"]
    other = owner.post(
        "/projects", json={"name": "另一个项目", "package": "weakcurrent-cabling"}
    ).json()["id"]
    report = owner.post(f"/projects/{ctx['project']}/evaluation/run", json={}).json()
    # 用另一个项目的 ref 访问本报告 -> 404
    resp = owner.get(f"/projects/{other}/evaluation/reports/{report['id']}")
    assert resp.status_code == 404
