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


def _seeded() -> tuple[str, list[str]]:
    """建一个项目并插入三个评分项（类别不同、分值不同），返回 id。"""

    from bidcraft.db import session_scope
    from bidcraft.models import Project, ScoringItem

    with session_scope() as db:
        project = Project(name="评分回填项目", package="weakcurrent-cabling")
        db.add(project)
        db.flush()
        rows = [
            ScoringItem(
                project_id=project.id,
                name="技术方案完整性",
                score=20.0,
                category="技术",
                section_hint="技术方案",
            ),
            ScoringItem(
                project_id=project.id,
                name="类似业绩",
                score=10.0,
                category="商务",
                section_hint="业绩",
            ),
            ScoringItem(project_id=project.id, name="报价得分", score=30.0, category="价格"),
        ]
        db.add_all(rows)
        db.flush()
        return project.id, [row.id for row in rows]


def _scores(project_id: str, rows: list[dict]) -> dict:
    from bidcraft import scoring
    from bidcraft.db import session_scope
    from bidcraft.models import Project

    with session_scope() as db:
        project = db.get(Project, project_id)
        assert project is not None
        scoring.record_scores(db, project, rows)
        return scoring.analysis(db, project)


def test_payload_rate_and_loss(db):
    from bidcraft import scoring
    from bidcraft.db import session_scope
    from bidcraft.models import ScoringItem

    _project_id, ids = _seeded()
    with session_scope() as session:
        item = session.get(ScoringItem, ids[0])
        item.actual_score = 16.0
        payload = scoring.score_payload(item)
    assert payload["rate"] == pytest.approx(0.8)
    assert payload["loss"] == pytest.approx(4.0)
    assert payload["actual_bidder"] == ""


def test_record_scores_validation(db):
    from bidcraft import scoring
    from bidcraft.db import session_scope
    from bidcraft.models import Project, ScoringItem

    project_id, ids = _seeded()
    with session_scope() as session:
        project = session.get(Project, project_id)
        scoring.record_scores(
            session,
            project,
            [
                {
                    "id": ids[0],
                    "actual_score": 18,
                    "actual_bidder": "本公司",
                    "score_note": "技术分",
                }
            ],
        )
        row = session.get(ScoringItem, ids[0])
        assert row.actual_score == pytest.approx(18.0)
        assert row.actual_bidder == "本公司"

        for bad in (99, -1, "abc"):
            with pytest.raises(scoring.ScoreError):
                scoring.record_scores(session, project, [{"id": ids[0], "actual_score": bad}])
        with pytest.raises(scoring.ScoreError):
            scoring.record_scores(session, project, [{"id": "missing", "actual_score": 1}])

        scoring.record_scores(session, project, [{"id": ids[0], "actual_score": None}])
        assert session.get(ScoringItem, ids[0]).actual_score is None


def test_analysis_dimensions_and_losses(db):
    project_id, ids = _seeded()
    report = _scores(
        project_id,
        [
            {"id": ids[0], "actual_score": 12},
            {"id": ids[1], "actual_score": 9},
            {"id": ids[2], "actual_score": 30},
        ],
    )
    assert report["total_max"] == pytest.approx(60.0)
    assert report["total_actual"] == pytest.approx(51.0)
    assert report["filled"] == 3
    assert report["rate"] == pytest.approx(0.85)
    dims = {d["category"]: d for d in report["dimensions"]}
    assert dims["技术"]["rate"] == pytest.approx(0.6)
    assert report["dimensions"][0]["category"] == "技术"
    assert report["losses"][0]["name"] == "技术方案完整性"
    assert report["losses"][0]["loss"] == pytest.approx(8.0)


def test_hints_text(db):
    from bidcraft import scoring
    from bidcraft.db import session_scope
    from bidcraft.models import Project

    project_id, ids = _seeded()
    with session_scope() as session:
        project = session.get(Project, project_id)
        assert scoring.hints(session, project) == ""
        scoring.record_scores(session, project, [{"id": ids[0], "actual_score": 10}])
        text = scoring.hints(session, project)
    assert "历史评分失分项" in text
    assert "技术方案完整性" in text
    assert "历史总得分率" in text


def test_scores_api_and_audit(db):
    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    project_id, ids = _seeded()
    client = _client()
    listed = client.get(f"/projects/{project_id}/scores")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total_items"] == 3

    updated = client.put(
        f"/projects/{project_id}/scores",
        json={
            "rows": [
                {"id": ids[0], "actual_score": 15, "actual_bidder": "本公司"},
                {"id": ids[2], "actual_score": 27},
            ]
        },
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["filled"] == 2
    assert body["total_actual"] == pytest.approx(42.0)
    assert len(body["losses"]) == 2

    bad = client.put(
        f"/projects/{project_id}/scores",
        json={"rows": [{"id": ids[0], "actual_score": 999}]},
    )
    assert bad.status_code == 400
    assert "不能超过分值" in bad.json()["detail"]

    with session_scope() as session:
        actions = [row.action for row in session.query(AuditLog).all()]
    assert "project.scores" in actions


def test_scores_api_rejects_viewer(db):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    project_id, ids = _seeded()
    admin = _client()
    admin.post(
        "/admin/users",
        json={
            "username": "scoreviewer",
            "display_name": "只读评分",
            "password": "viewer-pass-1",
            "role": "viewer",
        },
    )
    viewer = TestClient(create_app())
    assert (
        viewer.post(
            "/auth/login", json={"username": "scoreviewer", "password": "viewer-pass-1"}
        ).status_code
        == 200
    )
    member = admin.post(
        f"/projects/{project_id}/members", json={"username": "scoreviewer", "role": "viewer"}
    )
    assert member.status_code in (200, 201), member.text
    assert viewer.get(f"/projects/{project_id}/scores").status_code == 200
    blocked = viewer.put(
        f"/projects/{project_id}/scores",
        json={"rows": [{"id": ids[0], "actual_score": 1}]},
    )
    assert blocked.status_code in (403, 401)
