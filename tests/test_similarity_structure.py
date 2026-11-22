"""结构查重：标题相似、章节顺序变化、大纲相似度与重复标题。"""

from __future__ import annotations

from bidcraft.db import session_scope
from bidcraft.models import OutlineNode, Project
from bidcraft.similarity import structure as structure_engine


def _nodes() -> list[dict]:
    return [
        {"id": "1", "parent_id": "", "position": 1, "heading": "第一章 技术方案"},
        {"id": "2", "parent_id": "1", "position": 2, "heading": "总体架构"},
        {"id": "3", "parent_id": "", "position": 3, "heading": "第二章 实施计划"},
        {"id": "4", "parent_id": "", "position": 4, "heading": "第三章 服务承诺"},
    ]


def test_extract_document_structure_levels_and_paths():
    ordered = structure_engine.extract_document_structure(_nodes())
    assert [item["node_id"] for item in ordered] == ["1", "2", "3", "4"]
    child = ordered[1]
    assert child["level"] == 1
    assert child["path"] == "第一章 技术方案 / 总体架构"
    assert [item["order"] for item in ordered] == [0, 1, 2, 3]


def test_heading_similarity_ignores_numbering():
    result = structure_engine.compare_heading_structure(
        [{"node_id": "a", "heading": "一、技术方案", "order": 0, "path": "技术方案"}],
        [{"node_id": "b", "heading": "技术方案", "order": 0, "path": "技术方案"}],
        threshold=0.7,
    )
    assert result["matched"] == 1
    assert result["pairs"][0]["similarity"] == 1.0


def test_section_order_change_detected():
    left = structure_engine.extract_document_structure(
        [
            {"id": "a", "parent_id": "", "position": 1, "heading": "技术方案"},
            {"id": "b", "parent_id": "", "position": 2, "heading": "实施计划"},
            {"id": "c", "parent_id": "", "position": 3, "heading": "服务承诺"},
        ]
    )
    right = structure_engine.extract_document_structure(
        [
            {"id": "x", "parent_id": "", "position": 1, "heading": "技术方案"},
            {"id": "y", "parent_id": "", "position": 2, "heading": "服务承诺"},
            {"id": "z", "parent_id": "", "position": 3, "heading": "实施计划"},
        ]
    )
    order = structure_engine.compare_section_order(left, right, threshold=0.7)
    assert order["matched"] == 3
    assert order["order_score"] < 1.0
    assert order["inversions"] == 1

    identical = structure_engine.compare_outline_similarity(left, left, threshold=0.7)
    changed = structure_engine.compare_outline_similarity(left, right, threshold=0.7)
    assert identical["score"] == 1.0
    assert changed["score"] < identical["score"]
    assert changed["coverage"] == 1.0


def test_duplicate_headings_inside_project(db):
    with session_scope() as session:
        project = Project(name="结构重复项目")
        session.add(project)
        session.flush()
        session.add(OutlineNode(project_id=project.id, heading="技术方案", position=1))
        session.add(OutlineNode(project_id=project.id, heading="技术方案（二）", position=2))
        session.add(OutlineNode(project_id=project.id, heading="服务承诺", position=3))
        project_id = project.id

    with session_scope() as session:
        result = structure_engine.compare_project_structure(session, project_id, threshold=0.7)
    assert result["stats"]["cross_project"] is False
    assert result["stats"]["nodes"] == 3
    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["kind"] == "structure"
    assert hit["evidence"]["duplicate_heading"] is True
    assert hit["loc_a"] and hit["loc_b"] and hit["excerpt_a"] and hit["excerpt_b"]
    assert hit["similarity"] == 1.0


def test_cross_project_outline_similarity(db):
    ids = []
    with session_scope() as session:
        for name in ("源项目", "目标项目"):
            project = Project(name=name)
            session.add(project)
            session.flush()
            for position, heading in enumerate(("技术方案", "实施计划", "服务承诺"), start=1):
                session.add(
                    OutlineNode(project_id=project.id, heading=heading, position=position)
                )
            ids.append(project.id)

    with session_scope() as session:
        result = structure_engine.compare_project_structure(
            session, ids[1], source_project_id=ids[0], threshold=0.7
        )
    assert result["stats"]["cross_project"] is True
    assert result["stats"]["matched"] == 3
    assert result["stats"]["outline_score"] == 1.0
    assert len(result["hits"]) == 3
