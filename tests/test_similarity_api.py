"""API：查重报告生命周期、四维编排、导出与跨项目权限隔离。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "similarity-pass-1"
TEXT_A = (
    "本项目综合布线系统采用六类非屏蔽双绞线，所有永久链路按照设计文件完成测试，"
    "测试结果全部合格并形成完整记录，确保满足招标文件的技术要求。"
)
TEXT_B = TEXT_A.replace("六类", "超六类").replace("全部合格", "均合格")


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _admin():
    return _login(*ADMIN)


def _make_user(admin, username: str, role: str = "manager") -> dict:
    response = admin.post(
        "/admin/users",
        json={
            "username": username,
            "display_name": username,
            "password": PASSWORD,
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _make_project(client, name: str) -> dict:
    response = client.post(
        "/projects", json={"name": name, "package": "weakcurrent-cabling"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _seed(project_id: str, headings: list[tuple[str, str]]) -> list[str]:
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    node_ids: list[str] = []
    with session_scope() as db:
        for position, (heading, body) in enumerate(headings):
            node = OutlineNode(project_id=project_id, heading=heading, position=position)
            db.add(node)
            db.flush()
            db.add(Section(node_id=node.id, body=body, status="written"))
            node_ids.append(node.id)
    return node_ids


def test_report_lifecycle_review_and_export(db):
    admin = _admin()
    project = _make_project(admin, "API 查重项目")
    project_id = project["id"]
    _seed(
        project_id,
        [
            ("技术方案", TEXT_A),
            ("实施方案", TEXT_B),
        ],
    )

    response = admin.post(
        f"/projects/{project_id}/similarity/check",
        json={"mode": "text", "threshold": 0.8},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "done"
    assert body["hits"] >= 1
    report_id = body["report_id"]

    reports = admin.get(f"/projects/{project_id}/similarity/reports")
    assert reports.status_code == 200
    assert any(item["id"] == report_id for item in reports.json())

    report = admin.get(f"/projects/{project_id}/similarity/reports/{report_id}")
    assert report.status_code == 200
    payload = report.json()
    assert payload["status"] == "done"
    assert payload["mode"] == "text"
    assert payload["stats"]["hits"] >= 1
    assert payload["completed_at"]

    hits = admin.get(f"/projects/{project_id}/similarity/reports/{report_id}/hits")
    assert hits.status_code == 200
    items = hits.json()
    assert items
    hit = items[0]
    assert hit["kind"] == "text"
    assert hit["loc_a"] and hit["loc_b"]
    assert hit["excerpt_a"] and hit["excerpt_b"]
    assert hit["review_status"] == "pending"

    reviewed = admin.patch(
        f"/projects/{project_id}/similarity/hits/{hit['id']}",
        json={"status": "normal", "note": "人工确认属于合理引用"},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["review_status"] == "normal"
    assert reviewed.json()["review_note"] == "人工确认属于合理引用"

    filtered = admin.get(
        f"/projects/{project_id}/similarity/reports/{report_id}/hits",
        params={"review_status": "normal"},
    )
    assert filtered.status_code == 200
    assert len(filtered.json()) == 1

    export = admin.get(
        f"/projects/{project_id}/similarity/reports/{report_id}/export"
    )
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/markdown")
    assert "# 多维查重报告" in export.text
    assert "相似度" in export.text


def test_all_modes_are_independent_and_report_persisted(db):
    admin = _admin()
    project = _make_project(admin, "四维查重项目")
    project_id = project["id"]
    _seed(
        project_id,
        [
            ("技术方案", TEXT_A),
            ("技术方案（二）", TEXT_A),
        ],
    )

    response = admin.post(
        f"/projects/{project_id}/similarity/check",
        json={"mode": "all", "threshold": 0.8},
    )
    assert response.status_code == 202, response.text
    report_id = response.json()["report_id"]

    report = admin.get(f"/projects/{project_id}/similarity/reports/{report_id}").json()
    assert set(report["stats"]["modes"]) == {"text", "structure", "semantic", "image"}
    assert report["stats"]["by_kind"]["text"] >= 1
    assert report["stats"]["by_kind"]["structure"] >= 1
    # 语义模型未配置：该维度降级为文本查重但仍产出结果。
    assert report["stats"]["modes"]["semantic"]["degraded"] is True
    assert report["stats"]["modes"]["image"]["assets"] == 0
    assert any(item["mode"] == "image" for item in report["skipped"])

    hits = admin.get(f"/projects/{project_id}/similarity/reports/{report_id}/hits").json()
    kinds = {item["kind"] for item in hits}
    assert {"text", "semantic"} <= kinds
    assert all(item["loc_a"] and item["loc_b"] for item in hits)


def test_empty_project_returns_completed_report(db):
    admin = _admin()
    project = _make_project(admin, "空查重项目")
    response = admin.post(
        f"/projects/{project['id']}/similarity/check",
        json={"mode": "text", "threshold": 0.8},
    )
    assert response.status_code == 202, response.text
    report_id = response.json()["report_id"]
    report = admin.get(
        f"/projects/{project['id']}/similarity/reports/{report_id}"
    ).json()
    assert report["status"] == "done"
    assert report["stats"]["hits"] == 0
    assert report["skipped"]


def test_check_does_not_change_section_body(db):
    from bidcraft.db import session_scope
    from bidcraft.models import Section

    admin = _admin()
    project = _make_project(admin, "只读 API 项目")
    project_id = project["id"]
    _seed(project_id, [("技术方案", TEXT_A), ("实施方案", TEXT_B)])
    with session_scope() as session:
        before = [section.body for section in session.query(Section).all()]

    response = admin.post(
        f"/projects/{project_id}/similarity/check",
        json={"mode": "all", "threshold": 0.7},
    )
    assert response.status_code == 202, response.text

    with session_scope() as session:
        after = [section.body for section in session.query(Section).all()]
    assert after == before


def test_cross_project_permission_isolation(db):
    admin = _admin()
    _make_user(admin, "sim-owner-a")
    _make_user(admin, "sim-owner-b")
    _make_user(admin, "sim-viewer")
    owner_a = _login("sim-owner-a")
    owner_b = _login("sim-owner-b")
    viewer = _login("sim-viewer")

    project_a = _make_project(owner_a, "A 的查重项目")
    project_b = _make_project(owner_b, "B 的查重项目")
    _seed(project_a["id"], [("技术方案", TEXT_A)])
    _seed(project_b["id"], [("技术方案", TEXT_A)])
    shared = owner_b.post(
        f"/projects/{project_b['id']}/members",
        json={"username": "sim-owner-a", "role": "viewer"},
    )
    assert shared.status_code == 201, shared.text

    # B 不能把 A 的项目作为来源项目。
    denied = owner_b.post(
        f"/projects/{project_b['id']}/similarity/check",
        json={"mode": "text", "threshold": 0.8, "source_project_id": project_a["id"]},
    )
    assert denied.status_code == 404

    # 非成员不能读取 A 的报告列表/报告详情。
    assert owner_b.get(f"/projects/{project_a['id']}/similarity/reports").status_code == 404
    assert viewer.get(f"/projects/{project_a['id']}").status_code == 404

    # 来源项目对 A 可见时允许跨项目比对。
    allowed = owner_a.post(
        f"/projects/{project_a['id']}/similarity/check",
        json={"mode": "text", "threshold": 0.8, "source_project_id": project_b["id"]},
    )
    assert allowed.status_code == 202, allowed.text
    report_id = allowed.json()["report_id"]
    report = owner_a.get(
        f"/projects/{project_a['id']}/similarity/reports/{report_id}"
    ).json()
    assert report["stats"]["source_project_id"] == project_b["id"]
    assert report["stats"]["hits"] >= 1

    # 只读成员不能发起查重，但可以读取报告。
    added = owner_a.post(
        f"/projects/{project_a['id']}/members",
        json={"username": "sim-viewer", "role": "viewer"},
    )
    assert added.status_code == 201, added.text
    blocked = viewer.post(
        f"/projects/{project_a['id']}/similarity/check",
        json={"mode": "text", "threshold": 0.8},
    )
    assert blocked.status_code == 403
    assert viewer.get(
        f"/projects/{project_a['id']}/similarity/reports"
    ).status_code == 200


def test_invalid_payload_returns_400(db):
    admin = _admin()
    project = _make_project(admin, "非法查重参数")
    response = admin.post(
        f"/projects/{project['id']}/similarity/check",
        json={"mode": "unknown", "threshold": 0.8},
    )
    assert response.status_code == 400
    invalid_threshold = admin.post(
        f"/projects/{project['id']}/similarity/check",
        json={"mode": "text", "threshold": 2},
    )
    assert invalid_threshold.status_code == 400
