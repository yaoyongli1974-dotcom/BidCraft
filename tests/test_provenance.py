"""事实台账 → 正文的来源追溯：引用记录、需复核标记、边界判定。"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "prov-pass-1"


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
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


def _add_member(client, project_id: str, username: str, role: str):
    response = client.post(
        f"/projects/{project_id}/members", json={"username": username, "role": role}
    )
    assert response.status_code == 201, response.text


def _make_project(client, name: str = "来源追溯项目") -> str:
    response = client.post("/projects", json={"name": name, "package": "weakcurrent-cabling"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _seed(project_id: str, facts: dict[str, str], headings: list[str]) -> list[str]:
    """直接落 Fact 与 OutlineNode，避免依赖解析链路。"""

    from bidcraft.db import session_scope
    from bidcraft.models import Fact, OutlineNode

    node_ids: list[str] = []
    with session_scope() as db:
        for position, heading in enumerate(headings):
            node = OutlineNode(
                project_id=project_id,
                heading=heading,
                intent="",
                target_words=800,
                position=position,
            )
            db.add(node)
            db.flush()
            node_ids.append(node.id)
        for key, value in facts.items():
            db.add(
                Fact(
                    project_id=project_id,
                    key=key,
                    label=f"{key} 标签",
                    value=value,
                    status="confirmed",
                    source_quote=f"招标文件原文：{value}",
                    source_page=3,
                )
            )
    return node_ids


def _save_chapter(client, project_id: str, node_id: str, body: str) -> dict:
    response = client.put(
        f"/projects/{project_id}/chapters/{node_id}", json={"body": body}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_number_matching_requires_standalone_value():
    from bidcraft.provenance import count_occurrences

    assert count_occurrences("工期 90 日历天", "90") == 1
    assert count_occurrences("共 190 个信息点", "90") == 0
    assert count_occurrences("单价 1.90 元", "90") == 0
    assert count_occurrences("分项 90.5 万元", "90") == 0
    # 文本值按原样匹配，不做词边界
    assert count_occurrences("某某科技有限公司", "某某科技") == 1


def test_short_and_generic_values_are_ignored():
    from bidcraft.provenance import usable_value

    assert usable_value("1") is None
    assert usable_value("无") is None
    assert usable_value("") is None
    assert usable_value(" 90 ") == "90"


def test_chapter_save_records_citations_with_origin(db):
    client = _login(*ADMIN)
    project_id = _make_project(client)
    node_id = _seed(project_id, {"project.budget": "3500000", "project.duration": "90"}, ["技术方案"])[0]

    payload = _save_chapter(
        client, project_id, node_id, "本项目预算 3500000 元，工期 90 日历天。"
    )
    assert payload["citations"] == 2
    assert payload["citations_stale"] == 0

    response = client.get(f"/projects/{project_id}/chapters/{node_id}/citations")
    assert response.status_code == 200
    data = response.json()
    assert data["stale"] == 0
    keys = {item["fact_key"]: item for item in data["citations"]}
    assert set(keys) == {"project.budget", "project.duration"}
    assert keys["project.budget"]["label"] == "project.budget 标签"
    assert keys["project.budget"]["source_quote"] == "招标文件原文：3500000"
    assert keys["project.budget"]["source_page"] == 3
    assert keys["project.budget"]["occurrences"] == 1
    assert keys["project.budget"]["stale"] is False


def test_fact_change_marks_chapter_for_review(db):
    client = _login(*ADMIN)
    project_id = _make_project(client)
    node_id = _seed(project_id, {"project.budget": "3500000"}, ["商务响应"])[0]
    _save_chapter(client, project_id, node_id, "投标总报价 3500000 元。")

    response = client.patch(
        f"/projects/{project_id}/facts/project.budget",
        json={"value": "3600000", "status": "manual"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["node_id"] for item in body["stale_sections"]] == [node_id]
    assert body["stale_sections"][0]["stale_value"] == "3500000"

    chapter = client.get(f"/projects/{project_id}/chapters/{node_id}").json()
    assert chapter["citations_stale"] == 1

    outline = client.get(f"/projects/{project_id}/outline").json()["chapters"]
    assert outline[0]["citations"] == 1
    assert outline[0]["citations_stale"] == 1

    citations = client.get(f"/projects/{project_id}/chapters/{node_id}/citations").json()
    item = citations["citations"][0]
    assert item["value"] == "3500000"
    assert item["current_value"] == "3600000"
    assert item["stale"] is True

    overview = client.get(f"/projects/{project_id}/citations").json()
    assert overview["summary"]["stale"] == 1
    assert overview["summary"]["sections_needing_review"] == 1
    assert overview["chapters"][0]["heading"] == "商务响应"


def test_restoring_value_clears_review_flag(db):
    client = _login(*ADMIN)
    project_id = _make_project(client)
    node_id = _seed(project_id, {"project.budget": "3500000"}, ["商务响应"])[0]
    _save_chapter(client, project_id, node_id, "投标总报价 3500000 元。")

    client.patch(f"/projects/{project_id}/facts/project.budget", json={"value": "3600000"})
    client.patch(f"/projects/{project_id}/facts/project.budget", json={"value": "3500000"})

    chapter = client.get(f"/projects/{project_id}/chapters/{node_id}").json()
    assert chapter["citations_stale"] == 0


def test_editing_body_drops_stale_citation(db):
    client = _login(*ADMIN)
    project_id = _make_project(client)
    node_id = _seed(project_id, {"project.budget": "3500000"}, ["商务响应"])[0]
    _save_chapter(client, project_id, node_id, "投标总报价 3500000 元。")
    client.patch(f"/projects/{project_id}/facts/project.budget", json={"value": "3600000"})
    assert client.get(f"/projects/{project_id}/chapters/{node_id}").json()["citations_stale"] == 1

    # 正文改成新值后不再是「旧值引用」
    _save_chapter(client, project_id, node_id, "投标总报价 3600000 元。")
    chapter = client.get(f"/projects/{project_id}/chapters/{node_id}").json()
    assert chapter["citations"] == 1
    assert chapter["citations_stale"] == 0


def test_citations_are_readable_by_viewer_and_hidden_from_outsider(db):
    admin = _login(*ADMIN)
    for username in ("prov-viewer", "prov-outsider"):
        _make_user(admin, username)
    project_id = _make_project(admin)
    node_id = _seed(project_id, {"project.budget": "3500000"}, ["商务响应"])[0]
    _save_chapter(admin, project_id, node_id, "投标总报价 3500000 元。")
    _add_member(admin, project_id, "prov-viewer", "viewer")

    viewer = _login("prov-viewer")
    assert viewer.get(f"/projects/{project_id}/citations").status_code == 200
    assert viewer.get(f"/projects/{project_id}/chapters/{node_id}/citations").status_code == 200

    outsider = _login("prov-outsider")
    assert outsider.get(f"/projects/{project_id}/citations").status_code == 404


def test_check_project_rescans_bodies(db):
    """生成的正文（未经手动保存）在核对时也要被追溯。"""

    from bidcraft.db import session_scope
    from bidcraft.models import Section

    client = _login(*ADMIN)
    project_id = _make_project(client)
    node_id = _seed(project_id, {"project.duration": "90"}, ["工期承诺"])[0]
    with session_scope() as session:
        session.add(
            Section(
                node_id=node_id,
                body="我方承诺工期 90 日历天。",
                words=12,
                status="generated",
            )
        )

    chapter = client.get(f"/projects/{project_id}/chapters/{node_id}").json()
    assert chapter["citations"] == 0

    assert client.post(f"/projects/{project_id}/check").status_code == 200
    chapter = client.get(f"/projects/{project_id}/chapters/{node_id}").json()
    assert chapter["citations"] == 1
    assert chapter["citations_stale"] == 0


def test_refresh_project_is_idempotent(db):
    """全书重扫可反复执行，不会累积重复引用行。"""

    from bidcraft import provenance
    from bidcraft.db import session_scope

    client = _login(*ADMIN)
    project_id = _make_project(client)
    n1, n2 = _seed(project_id, {"project.budget": "3500000"}, ["章一", "章二"])
    _save_chapter(client, project_id, n1, "预算 3500000 元。")
    _save_chapter(client, project_id, n2, "总价 3500000 元整。")

    with session_scope() as session:
        first = provenance.refresh_project(session, project_id)
        second = provenance.refresh_project(session, project_id)
    assert first == 2 and second == 2

    overview = client.get(f"/projects/{project_id}/citations").json()
    assert overview["summary"]["citations"] == 2
    assert overview["summary"]["sections_citing"] == 2


def test_report_aggregates_multiple_chapters(db):
    """report() 全书视图：计数聚合、章节按位置排序、stale 章节数正确。"""

    from bidcraft import provenance
    from bidcraft.db import session_scope

    client = _login(*ADMIN)
    project_id = _make_project(client)
    n1, n2 = _seed(
        project_id,
        {"project.budget": "3500000", "project.duration": "90"},
        ["章一", "章二"],
    )
    _save_chapter(client, project_id, n1, "预算 3500000 元。")
    _save_chapter(client, project_id, n2, "总价 3500000 元，工期 90 天。")
    client.patch(
        f"/projects/{project_id}/facts/project.budget", json={"value": "3600000"}
    )

    with session_scope() as session:
        report = provenance.report(session, project_id)
    summary = report["summary"]
    assert summary["citations"] == 3  # 预算在两章各一次 + 工期一次
    assert summary["facts_cited"] == 2
    assert summary["stale"] == 2  # 预算被两章引用，改值后两条都 stale
    assert summary["sections_citing"] == 2
    assert summary["sections_needing_review"] == 2
    assert [chapter["heading"] for chapter in report["chapters"]] == ["章一", "章二"]
    assert report["chapters"][1]["stale"] == 1


def test_affected_by_change_direct(db):
    """改值前后的受影响章节：改成原值 / 无关字段都返回空。"""

    from bidcraft import provenance
    from bidcraft.db import session_scope

    client = _login(*ADMIN)
    project_id = _make_project(client)
    node_id = _seed(
        project_id, {"project.budget": "3500000", "project.duration": "90"}, ["章一"]
    )[0]
    _save_chapter(client, project_id, node_id, "预算 3500000 元，工期 90 天。")

    with session_scope() as session:
        affected = provenance.affected_by_change(
            session, project_id, "project.budget", "3600000"
        )
        assert [item["node_id"] for item in affected] == [node_id]
        assert affected[0]["stale_value"] == "3500000"
        # 改回原值 / 未改动的字段 / 无引用的字段都不需要复核
        assert provenance.affected_by_change(session, project_id, "project.budget", "3500000") == []
        assert provenance.affected_by_change(session, project_id, "project.duration", "90") == []
        assert provenance.affected_by_change(session, project_id, "no.such.fact", "x") == []


def test_deleted_fact_marks_citation_stale(db):
    """事实被删除后，旧引用标记 stale、标签回退到 key、当前值为空。"""

    from sqlalchemy import delete

    from bidcraft.db import session_scope
    from bidcraft.models import Fact

    client = _login(*ADMIN)
    project_id = _make_project(client)
    node_id = _seed(project_id, {"project.budget": "3500000"}, ["章一"])[0]
    _save_chapter(client, project_id, node_id, "预算 3500000 元。")

    with session_scope() as session:
        session.execute(
            delete(Fact).where(
                Fact.project_id == project_id, Fact.key == "project.budget"
            )
        )

    items = client.get(f"/projects/{project_id}/chapters/{node_id}/citations").json()
    assert len(items["citations"]) == 1
    item = items["citations"][0]
    assert item["stale"] is True
    assert item["current_value"] == ""
    assert item["label"] == "project.budget"


def test_citations_isolated_between_projects(db):
    """同名字段在不同项目取值不同，引用不得串项目。"""

    client = _login(*ADMIN)
    p1 = _make_project(client, "项目甲")
    p2 = _make_project(client, "项目乙")
    n1 = _seed(p1, {"project.budget": "3500000"}, ["章甲"])[0]
    n2 = _seed(p2, {"project.budget": "8800000"}, ["章乙"])[0]
    _save_chapter(client, p1, n1, "预算 3500000 元。")
    _save_chapter(client, p2, n2, "预算 8800000 元。")

    c1 = client.get(f"/projects/{p1}/chapters/{n1}/citations").json()["citations"]
    c2 = client.get(f"/projects/{p2}/chapters/{n2}/citations").json()["citations"]
    assert [item["value"] for item in c1] == ["3500000"]
    assert [item["value"] for item in c2] == ["8800000"]
    assert all(not item["stale"] for item in c1 + c2)
