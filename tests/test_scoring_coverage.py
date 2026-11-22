"""评分覆盖矩阵（只读）：评分点→章节映射、保守覆盖判定、API 隔离。

属对标易标第一批。验证「每个评分点能否映射到带正文的章节」这一判定链路，
口径保守：匹配不到或正文为空一律未覆盖，不做语义脑补；不写库、不动并发。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


def _score(id, name, score=10.0, hint="", category=""):
    return {
        "id": id,
        "name": name,
        "score": score,
        "category": category,
        "section_hint": hint,
    }


def _node(id, heading, words=0, body="", intent="", status="draft"):
    return {
        "id": id,
        "heading": heading,
        "words": words,
        "body": body,
        "has_body": bool(body),
        "intent": intent,
        "review_status": status,
    }


def test_section_hint_exact_match_is_covered():
    from bidcraft.generation.coverage import (
        STATUS_COVERED,
        calculate_score_coverage,
    )

    scores = [_score("s1", "技术方案评分", 30, hint="技术方案")]
    nodes = [_node("n1", "技术方案", words=800, body="正文" * 400)]
    report = calculate_score_coverage(scores, nodes)
    row = report["rows"][0]
    assert row["status"] == STATUS_COVERED
    assert any(r["node_id"] == "n1" for r in row["nodes"])
    assert any("section_hint" in r for r in row["nodes"][0]["reasons"])
    assert report["covered_score"] == 30.0
    assert report["covered_ratio"] == 1.0


def test_empty_body_is_uncovered_not_covered():
    from bidcraft.generation.coverage import STATUS_UNCOVERED, calculate_score_coverage

    scores = [_score("s1", "技术方案", 30, hint="技术方案")]
    nodes = [_node("n1", "技术方案", words=0, body="")]
    report = calculate_score_coverage(scores, nodes)
    assert report["rows"][0]["status"] == STATUS_UNCOVERED
    assert report["covered_score"] == 0.0
    assert report["uncovered_count"] == 1


def test_short_body_is_weak():
    from bidcraft.generation.coverage import STATUS_WEAK, calculate_score_coverage

    scores = [_score("s1", "技术方案", 30, hint="技术方案")]
    nodes = [_node("n1", "技术方案", words=30, body="太短")]
    report = calculate_score_coverage(scores, nodes)
    assert report["rows"][0]["status"] == STATUS_WEAK
    assert report["weak_score"] == 30.0
    # weak 不计入 covered_score（保守）
    assert report["covered_score"] == 0.0


def test_unmatched_scoring_item_is_uncovered():
    from bidcraft.generation.coverage import STATUS_UNCOVERED, calculate_score_coverage

    scores = [_score("s1", "完全无关的评分项xyz", 15, hint="不存在的章节zzz")]
    nodes = [_node("n1", "技术方案", words=800, body="正文")]
    report = calculate_score_coverage(scores, nodes)
    assert report["rows"][0]["status"] == STATUS_UNCOVERED
    assert report["rows"][0]["nodes"] == []


def test_one_node_can_cover_multiple_scores_and_vice_versa():
    from bidcraft.generation.coverage import calculate_score_coverage

    scores = [
        _score("s1", "综合布线技术方案", 20, hint="技术方案"),
        _score("s2", "安防系统技术方案", 10, hint="技术方案"),
    ]
    nodes = [_node("n1", "技术方案", words=500, body="正文")]
    report = calculate_score_coverage(scores, nodes)
    assert len(report["rows"]) == 2
    for row in report["rows"]:
        assert any(r["node_id"] == "n1" for r in row["nodes"])
    assert report["covered_score"] == 30.0


def test_validate_reports_uncovered_and_missing_scores():
    from bidcraft.generation.coverage import (
        calculate_score_coverage,
        validate_scoring_coverage,
    )

    report = calculate_score_coverage([_score("s1", "X", 10, hint="X")], [_node("n1", "Y")])
    issues = validate_scoring_coverage(report)
    assert any("未找到" in issue for issue in issues)

    empty = calculate_score_coverage([], [])
    assert any("尚无带分值" in i for i in validate_scoring_coverage(empty))


def test_api_scoring_coverage_requires_project_and_is_readonly(db):
    """API 端点：viewer 可读、返回矩阵字段、不改变大纲/评分数据。"""
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app
    from bidcraft.db import session_scope
    from bidcraft.models import Project, ScoringItem

    with session_scope() as session:
        project = Project(name="覆盖项目", status="draft")
        session.add(project)
        session.flush()
        session.add(
            ScoringItem(
                project_id=project.id,
                name="技术方案",
                score=10.0,
                section_hint="技术方案",
            )
        )
        ref = project.id

    client = TestClient(create_app())
    assert client.post("/auth/login", json={"username": "admin", "password": "test-admin"}).status_code == 200
    response = client.get(f"/projects/{ref}/scoring-coverage")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == ref
    assert body["total_items"] == 1
    assert body["rows"][0]["name"] == "技术方案"
    assert body["rows"][0]["status"] == "uncovered"  # 无章节无正文

    # 只读：查询不应产生审计以外的副作用——再查一次结果一致
    again = client.get(f"/projects/{ref}/scoring-coverage")
    assert again.json()["rows"] == body["rows"]


def test_api_scoring_coverage_isolated_between_projects(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app
    from bidcraft.db import session_scope
    from bidcraft.models import Project, ScoringItem

    with session_scope() as session:
        p1 = Project(name="项目A", status="draft")
        p2 = Project(name="项目B", status="draft")
        session.add_all([p1, p2])
        session.flush()
        session.add(ScoringItem(project_id=p1.id, name="仅A的评分项", score=5.0))
        ref1, ref2 = p1.id, p2.id

    client = TestClient(create_app())
    client.post("/auth/login", json={"username": "admin", "password": "test-admin"})
    b1 = client.get(f"/projects/{ref1}/scoring-coverage").json()
    b2 = client.get(f"/projects/{ref2}/scoring-coverage").json()
    assert [r["name"] for r in b1["rows"]] == ["仅A的评分项"]
    assert b2["rows"] == []
