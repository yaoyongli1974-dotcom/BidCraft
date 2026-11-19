from __future__ import annotations

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
