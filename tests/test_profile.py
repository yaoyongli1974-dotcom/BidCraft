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
            "username": "profileviewer",
            "display_name": "只读同事",
            "password": "viewer-pass-1",
            "role": "viewer",
        },
    )
    assert created.status_code == 201, created.text
    viewer = TestClient(create_app())
    login = viewer.post(
        "/auth/login", json={"username": "profileviewer", "password": "viewer-pass-1"}
    )
    assert login.status_code == 200, login.text
    return viewer


def test_normalize_defaults_and_limits():
    from bidcraft import profile

    empty = profile.normalize(None)
    assert empty["tone"] == "稳健"
    assert empty["forbidden_phrases"] == []

    normalized = profile.normalize(
        {
            "company_name": "  某某科技  ",
            "bid_amount": "1,286,000 元",
            "tone": "技术",
            "forbidden_phrases": ["绝对第一", "绝对第一", " "],
            "extra_facts": [
                {"key": "项目经理", "value": "张三"},
                {"key": "项目经理", "value": "李四"},
            ],
        }
    )
    assert normalized["company_name"] == "某某科技"
    assert normalized["tone"] == "技术"
    assert normalized["forbidden_phrases"] == ["绝对第一"]
    assert normalized["extra_facts"] == [{"key": "项目经理", "value": "张三"}]


def test_normalize_rejects_bad_tone():
    from bidcraft import profile

    with pytest.raises(profile.ProfileError):
        profile.normalize({"tone": "激昂"})


def test_load_falls_back_for_damaged_meta(db, monkeypatch):
    from bidcraft import profile
    from bidcraft.db import session_scope
    from bidcraft.models import Project

    with session_scope() as session:
        project = Project(name="坏设定项目", package="weakcurrent-cabling", meta={})
        session.add(project)
        session.flush()
        project.meta = {profile.META_KEY: {"tone": "不对"}}
        session.flush()
        assert profile.load(project)["tone"] == "稳健"


def test_profile_roundtrip_and_audit(db):
    client = _client()
    project = client.post("/projects", json={"name": "全局设定项目"}).json()
    ref = project["id"]

    assert client.get(f"/projects/{ref}/profile").json()["profile"]["tone"] == "稳健"

    saved = client.put(
        f"/projects/{ref}/profile",
        json={
            "profile": {
                "company_name": "某某科技有限公司",
                "project_name": "某园区智能化工程",
                "bid_amount": "1,286,000 元",
                "duration_days": "120 日历天",
                "tone": "技术",
                "forbidden_phrases": ["绝对第一"],
                "extra_facts": [{"key": "项目经理", "value": "张三"}],
            }
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["profile"]["company_name"] == "某某科技有限公司"
    assert body["anchors"]["项目名称"] == "某园区智能化工程"
    assert "公司名称：某某科技有限公司" in body["facts_block"]
    assert "- 项目经理：张三" in body["facts_block"]

    audit = client.get("/admin/audit").json()
    actions = [item["action"] for item in audit["items"]]
    assert "project.profile" in actions


def test_profile_rejects_bad_payload_and_viewer(db):
    client = _client()
    project = client.post("/projects", json={"name": "权限项目"}).json()
    ref = project["id"]
    bad = client.put(f"/projects/{ref}/profile", json={"profile": {"tone": "激昂"}})
    assert bad.status_code == 400
    assert "语气" in bad.json()["detail"]

    viewer = _viewer()
    denied = viewer.put(f"/projects/{ref}/profile", json={"profile": {"tone": "稳健"}})
    assert denied.status_code in (403, 404)


def test_chapter_messages_injects_facts():
    from bidcraft.generation.prompts import chapter_messages

    with_facts = chapter_messages(
        project_name="某项目",
        heading="总体方案",
        intent="",
        target_words=600,
        ledger_text="台账",
        outline_text="大纲",
        previous_summary="",
        package_title="弱电",
        facts_text="- 公司名称：某某科技",
    )
    text = with_facts[1]["content"]
    assert "【全局事实（必须严格遵守，前后一致）】" in text
    assert "公司名称：某某科技" in text
    assert text.index("全局事实") < text.index("【全书大纲】")

    without = chapter_messages(
        project_name="某项目",
        heading="总体方案",
        intent="",
        target_words=600,
        ledger_text="台账",
        outline_text="大纲",
        previous_summary="",
        package_title="弱电",
    )
    assert "全局事实" not in without[1]["content"]


def test_anchors_and_conflicts():
    from bidcraft import profile, textqa

    values = profile.anchors(
        {"project_name": "某园区智能化工程", "duration_days": "120 日历天", "bid_amount": ""}
    )
    assert values == {"项目名称": "某园区智能化工程", "工期": "120 日历天"}

    sections = [
        {"heading": "项目概述", "body": "项目名称：另一个工程\n计划工期：150 日历天"},
        {"heading": "服务承诺", "body": "项目名称：某园区智能化工程"},
    ]
    findings = textqa.profile_conflicts(sections, values)
    keys = {item["evidence"]["key"] for item in findings}
    assert keys == {"项目名称", "工期"}
    assert all(item["level"] == "major" for item in findings)
    assert findings[0]["id"].startswith("CON-")


def test_conflicts_ignore_matching_values():
    from bidcraft import textqa

    sections = [{"heading": "概述", "body": "项目名称：某园区智能化工程"}]
    assert textqa.profile_conflicts(sections, {"项目名称": "某园区智能化工程"}) == []


def test_check_project_reports_profile_conflict(db):
    client = _client()
    project = client.post("/projects", json={"name": "核对项目"}).json()
    ref = project["id"]
    node = client.put(
        f"/projects/{ref}/outline",
        json={"chapters": [{"heading": "项目概述", "intent": "概述", "target_words": 200}]},
    ).json()
    chapter_id = node["chapters"][0]["id"]
    assert (
        client.put(
            f"/projects/{ref}/chapters/{chapter_id}",
            json={"body": "项目名称：与设定不同的工程，工期满足要求。"},
        ).status_code
        == 200
    )
    client.put(
        f"/projects/{ref}/profile",
        json={"profile": {"project_name": "正确项目名称"}},
    )
    assert client.post(f"/projects/{ref}/check").status_code == 200
    issues = client.get(f"/projects/{ref}/issues").json()
    rows = issues["issues"] if isinstance(issues, dict) else issues
    assert any(item.get("category") == "一致性" and "全局设定" in item.get("finding", "") for item in rows)
