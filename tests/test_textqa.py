from __future__ import annotations

import pytest

from bidcraft import textqa


def _sections() -> list[dict]:
    return [
        {
            "heading": "项目概况",
            "body": "项目名称：智慧园区弱电工程\n工期：60日历天\n投标人名称：甲公司\n质保期：2年\n",
        },
        {
            "heading": "施工组织",
            "body": (
                "本项目按装调试均由自有队伍完成，帐号与帐户信息另行提交。"
                "我们承诺的的工期为90天，项目名称：智慧园区弱电工程（二期）。"
                "质保期：3年。投标人名称：乙公司。\n"
            ),
        },
    ]


def test_find_typos_rules_and_duplicates():
    body = "请按装设备，帐号与帐户另行提供。的的工期，报价，，请确认。"
    findings = textqa.find_typos(body, heading="正文")
    messages = " ".join(item["message"] for item in findings)
    assert "按装" in messages and "帐号" in messages and "帐户" in messages
    assert "重复字「的的」" in messages
    assert "重复标点「，，」" in messages
    assert all(item["id"].startswith("TXT-") for item in findings)
    assert all(item["level"] == "minor" and item["category"] == "文本质量" for item in findings)
    assert findings[0]["evidence"]["quote"]


def test_deadline_context_rule():
    wrong = textqa.find_typos("请于截止2026年6月前提交。")
    assert any("截至" in item["recommendation"] for item in wrong)
    right = textqa.find_typos("投标截止日期为2026年6月1日。")
    assert not any("截至" in item["recommendation"] for item in right)


def test_consistency_detects_mismatched_values():
    findings = textqa.check_consistency(_sections())
    keys = {item["evidence"]["key"] for item in findings}
    assert {"工期", "质保期", "投标人名称", "项目名称"} <= keys
    assert all(item["level"] == "major" and item["category"] == "一致性" for item in findings)
    assert all(item["recommendation"] for item in findings)


def test_consistency_ignores_single_value():
    sections = [{"heading": "A", "body": "工期：60日历天"}, {"heading": "B", "body": "工期：60天"}]
    assert textqa.check_consistency(sections) == []


def test_duplication_flags_copy_paste():
    paragraph = (
        "我方将严格按照招标文件要求组织施工，配备专职项目经理与技术负责人，"
        "建立质量管理体系并落实三级检查制度，明确各工序交接与隐蔽验收流程，"
        "确保工程按期保质完成并通过竣工验收，交付完整的技术资料与培训服务。"
    )
    sections = [
        {"heading": "第一章", "body": paragraph},
        {"heading": "第二章", "body": paragraph + "（补充说明）"},
    ]
    findings = textqa.check_duplication(sections)
    assert findings and findings[0]["category"] == "重复"
    assert findings[0]["evidence"]["similarity"] >= textqa.DUPLICATE_THRESHOLD


def test_duplication_ignores_short_and_distinct_text():
    sections = [
        {"heading": "A", "body": "短句。"},
        {"heading": "B", "body": "完全不同的一段内容，讲的是设备清单与报价构成，与上一段无关。"},
    ]
    assert textqa.check_duplication(sections) == []


def test_scan_sections_numbers_continuously_and_caps():
    findings = textqa.scan_sections(_sections(), limit=5)
    assert len(findings) == 5
    ids = [item["id"] for item in findings]
    assert len(set(ids)) == len(ids)
    assert findings[0]["id"].startswith("TXT-")


def test_check_project_includes_textqa_issues(db):
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section
    from bidcraft.pipeline import check_project, create_project

    with session_scope() as session:
        project = create_project(session, "文本质量项目", "weakcurrent-cabling")
        node = OutlineNode(project_id=project.id, heading="技术方案", position=1)
        session.add(node)
        session.flush()
        session.add(Section(node_id=node.id, body="请按装设备，帐号信息另附。"))
        session.flush()
        results, _summary = check_project(session, project.id)
        codes = [item.code for item in results]
        assert any(code.startswith("TXT-") for code in codes), codes


# ----------------------------------------------------- 企业自定义错别字词库


def _admin_client():
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    assert response.status_code == 200, response.text
    return client


def test_save_and_load_custom_roundtrip(db):
    from bidcraft.db import session_scope

    rules = [
        {"wrong": "竣工验受", "right": "竣工验收", "note": "验收用语"},
        {"wrong": "竣工验受", "right": "别的写法", "note": "重复项应被丢弃"},
        {"wrong": "弱项", "right": "", "note": ""},
    ]
    with session_scope() as session:
        saved = textqa.save_custom(session, rules)
    assert [r["wrong"] for r in saved] == ["竣工验受", "弱项"]
    assert saved[0] == {"wrong": "竣工验受", "right": "竣工验收", "note": "验收用语"}
    with session_scope() as session:
        assert textqa.load_custom(session) == saved


def test_load_custom_drops_invalid_and_caps(db):
    from bidcraft.db import session_scope
    from bidcraft.models import AppSetting

    raw = [
        {"wrong": "", "right": "x"},
        {"wrong": "超" * 21},
        {"wrong": "合法", "right": "y", "note": "z"},
        "not-a-dict",
        {"wrong": "合法", "right": "重复"},
        {"wrong": "ok", "right": "超" * 21},
        {"wrong": "ok2", "note": "超" * 61},
    ] + [{"wrong": f"词{i}", "right": ""} for i in range(600)]
    with session_scope() as session:
        session.add(AppSetting(key="textqa", value={"typos": raw}))
    with session_scope() as session:
        loaded = textqa.load_custom(session)
    assert loaded[0] == {"wrong": "合法", "right": "y", "note": "z"}
    assert len(loaded) == textqa.CUSTOM_MAX == 500
    assert len({r["wrong"] for r in loaded}) == len(loaded)


def test_save_custom_validation_errors(db):
    from bidcraft.db import session_scope

    cases = [
        ([{"wrong": "", "right": "x"}], "错词不能为空"),
        ([{"wrong": "   "}], "错词不能为空"),
        ([{"wrong": "超" * 21}], "错词不能超过"),
        ([{"wrong": "ok", "right": "超" * 21}], "「改为」不能超过"),
        ([{"wrong": "ok", "note": "超" * 61}], "备注不能超过"),
        ([{"wrong": f"词{i}"} for i in range(501)], "不能超过 500"),
        ("不是列表", "必须是列表"),
        ([123], "格式不正确"),
    ]
    with session_scope() as session:
        for payload, fragment in cases:
            with pytest.raises(ValueError) as exc:
                textqa.save_custom(session, payload)
            assert fragment in str(exc.value)
        assert textqa.load_custom(session) == []


def test_custom_hit_in_scan_sections_and_check_project(db):
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section
    from bidcraft.pipeline import check_project, create_project

    with session_scope() as session:
        textqa.save_custom(
            session, [{"wrong": "竣工验受", "right": "竣工验收", "note": "验收用语"}]
        )
    sections = [{"heading": "验收", "body": "工程竣工验受合格后交付使用。"}]
    with session_scope() as session:
        findings = textqa.scan_sections(session, sections)
    hits = [f for f in findings if "竣工验受" in f["message"]]
    assert hits, findings
    assert hits[0]["evidence"]["custom"] is True
    assert "竣工验收" in hits[0]["message"]
    assert hits[0]["recommendation"] == "验收用语"

    with session_scope() as session:
        project = create_project(session, "自定义词库项目", "weakcurrent-cabling")
        node = OutlineNode(project_id=project.id, heading="验收", position=1)
        session.add(node)
        session.flush()
        session.add(Section(node_id=node.id, body="工程竣工验受合格后交付使用。"))
        session.flush()
        results, _summary = check_project(session, project.id)
    assert any(
        item.code.startswith("TXT-") and item.evidence.get("custom") for item in results
    )


def test_custom_overrides_builtin_and_legacy_signature(db):
    from bidcraft.db import session_scope

    with session_scope() as session:
        textqa.save_custom(
            session, [{"wrong": "帐号", "right": "账户", "note": "企业自定义备注"}]
        )
    sections = [{"heading": "财务", "body": "请提供帐号信息。"}]
    with session_scope() as session:
        findings = textqa.scan_sections(session, sections)
    hits = [f for f in findings if "帐号" in f["message"]]
    assert len(hits) == 1  # 自定义覆盖内置，不重复报告
    assert "账户" in hits[0]["message"]
    assert hits[0]["recommendation"] == "企业自定义备注"
    assert hits[0]["evidence"]["custom"] is True
    # 旧签名 scan_sections(sections) 仍可用，且不带自定义标记
    legacy = textqa.scan_sections(sections)
    assert any("帐号" in f["message"] and not f["evidence"].get("custom") for f in legacy)


def test_admin_textqa_get_put_roundtrip_and_audit(db):
    pytest.importorskip("fastapi")
    from sqlalchemy import select

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    client = _admin_client()
    initial = client.get("/admin/textqa")
    assert initial.status_code == 200
    assert initial.json() == {"typos": [], "count": 0, "builtin_count": len(textqa.TYPO_RULES)}

    rules = [{"wrong": "竣工验受", "right": "竣工验收", "note": "自定义"}]
    put = client.put("/admin/textqa", json={"typos": rules})
    assert put.status_code == 200, put.text
    assert put.json()["count"] == 1
    assert put.json()["typos"] == rules
    assert put.json()["builtin_count"] == len(textqa.TYPO_RULES)
    assert client.get("/admin/textqa").json()["typos"] == rules

    with session_scope() as session:
        rows = list(
            session.scalars(select(AuditLog).where(AuditLog.action == "textqa.config"))
        )
    assert rows and rows[-1].detail.get("count") == 1


def test_admin_textqa_invalid_400_chinese(db):
    pytest.importorskip("fastapi")
    client = _admin_client()
    cases = [
        {"typos": [{"wrong": "", "right": "x"}]},
        {"typos": [{"wrong": "超" * 21}]},
        {"typos": [{"wrong": "ok", "right": "超" * 21}]},
        {"typos": [{"wrong": "ok", "note": "超" * 61}]},
        {"typos": [{"wrong": f"词{i}"} for i in range(501)]},
        {"typos": "不是列表"},
    ]
    for payload in cases:
        response = client.put("/admin/textqa", json=payload)
        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert any("一" <= ch <= "鿿" for ch in detail), detail
    assert client.get("/admin/textqa").json()["count"] == 0


def test_admin_textqa_viewer_forbidden(db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    admin = _admin_client()
    created = admin.post(
        "/admin/users",
        json={
            "username": "viewer1",
            "display_name": "只读同事",
            "password": "viewer-pass-1",
            "role": "viewer",
        },
    )
    assert created.status_code == 201
    viewer = TestClient(create_app())
    login = viewer.post(
        "/auth/login", json={"username": "viewer1", "password": "viewer-pass-1"}
    )
    assert login.status_code == 200
    assert viewer.get("/admin/textqa").status_code == 403
    assert viewer.put("/admin/textqa", json={"typos": []}).status_code == 403
