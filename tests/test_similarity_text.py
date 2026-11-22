"""文本查重：段落命中、阈值边界、固定条款白名单、空文档与只读性。"""

from __future__ import annotations

from bidcraft.db import session_scope
from bidcraft.dedup import similarity
from bidcraft.models import OutlineNode, Project, Section
from bidcraft.similarity import text as text_engine

BASE = (
    "本项目综合布线系统采用六类非屏蔽双绞线，所有永久链路按照设计文件完成测试，"
    "测试结果全部合格并形成完整记录，确保满足招标文件的技术要求。"
)
MODIFIED = BASE.replace("六类", "超六类").replace("全部合格", "均合格")


def _seed_project(name: str, headings: list[tuple[str, str]]) -> str:
    with session_scope() as db:
        project = Project(name=name)
        db.add(project)
        db.flush()
        for position, (heading, body) in enumerate(headings):
            node = OutlineNode(project_id=project.id, heading=heading, position=position)
            db.add(node)
            db.flush()
            db.add(Section(node_id=node.id, body=body, status="written"))
        return project.id


def test_identical_and_modified_paragraphs_hit(db):
    project_id = _seed_project(
        "文本查重项目",
        [
            ("技术方案", f"{BASE}\n\n第二段用于区分的独立内容，包含设备选型与施工组织安排等说明文字。"),
            ("实施方案", f"{MODIFIED}\n\n另一段完全不同的售后服务承诺，包括响应时间与备件库管理要求。"),
        ],
    )
    with session_scope() as db_session:
        result = text_engine.compare_project_documents(
            db_session, project_id, threshold=0.8
        )
    assert result["stats"]["documents"] == 2
    assert result["stats"]["cross_project"] is False
    assert result["hits"], result
    top = result["hits"][0]
    assert top["similarity"] >= 0.8
    assert top["loc_a"] and top["loc_b"]
    assert top["excerpt_a"] and top["excerpt_b"]
    assert top["evidence"]["same_section"] is False


def test_threshold_boundary_is_inclusive(db):
    left = [{"text": BASE, "heading": "A", "node_id": "a", "project_id": "p", "index": 0}]
    right = [{"text": MODIFIED, "heading": "B", "node_id": "b", "project_id": "p", "index": 0}]
    exact = similarity(BASE, MODIFIED)
    hit = text_engine.compare_sections(left, right, threshold=exact)
    assert hit["stats"]["hits"] == 1
    assert hit["hits"][0]["similarity"] == round(exact, 4)
    missed = text_engine.compare_sections(left, right, threshold=min(1.0, exact + 0.01))
    assert missed["stats"]["hits"] == 0


def test_common_clause_marked_reasonable_but_kept(db):
    clause = (
        "依据《中华人民共和国招标投标法》及国家标准 GB 50311-2016 的相关规定，"
        "投标人应当严格按照招标文件要求编制技术方案并承担相应违约责任。"
    )
    project_id = _seed_project("白名单项目", [("法规响应", clause), ("合同条款", clause)])
    with session_scope() as db_session:
        result = text_engine.compare_project_documents(db_session, project_id, threshold=0.8)
    assert len(result["hits"]) == 1
    evidence = result["hits"][0]["evidence"]
    assert evidence["common"] is True
    assert evidence["reasonable_duplicate"] is True
    assert evidence["common_category"]
    assert result["stats"]["common_candidates"] == 1


def test_classify_common_text_categories():
    assert text_engine.classify_common_text("我公司郑重承诺所提供材料真实有效")["category"] == (
        "enterprise_declaration"
    )
    assert text_engine.classify_common_text("合同价款支付方式与履约保证金按约定执行")["category"] == (
        "contract_clause"
    )
    assert text_engine.classify_common_text("详见招标文件投标人须知与评标办法")["category"] == (
        "tender_original"
    )
    assert text_engine.classify_common_text("测试依据 GB/T 50312-2016 执行")["common"] is True
    assert text_engine.classify_common_text("今天天气晴朗，现场勘查顺利完成")["common"] is False


def test_empty_document_returns_no_hits(db):
    project_id = _seed_project("空文档项目", [("空章节", "")])
    with session_scope() as db_session:
        result = text_engine.compare_project_documents(db_session, project_id)
    assert result["hits"] == []
    assert result["stats"]["paragraphs"] == 0
    assert result["skipped"]


def test_compare_does_not_modify_section_body(db):
    body = f"{BASE}\n\n{BASE}"
    project_id = _seed_project("只读项目", [("技术方案", body), ("复制章节", body)])
    with session_scope() as db_session:
        before = db_session.query(Section).count()
        text_engine.compare_project_documents(db_session, project_id, threshold=0.8)
        after = db_session.query(Section).count()
        stored = db_session.query(Section).all()
    assert before == after == 2
    assert all(section.body in {body, BASE} for section in stored)
    assert any(section.body == body for section in stored)
