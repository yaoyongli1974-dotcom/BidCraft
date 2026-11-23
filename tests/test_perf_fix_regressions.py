"""性能整改项的回归测试：批量统计、SQL 排序、队列索引、SSE 断开与事务边界。"""

from __future__ import annotations

import asyncio
import inspect
import threading
from typing import ClassVar

import pytest

pytest.importorskip("fastapi")

ADMIN = ("admin", "test-admin")
PASSWORD = "perf-fix-pass-1"


def _login(username: str, password: str = PASSWORD):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return client


def _make_admin():
    return _login(*ADMIN)


def _make_user(admin, username: str):
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
    return _login(username)


def _count_selects(engine, lock, counter):
    from sqlalchemy import event

    def before(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            with lock:
                counter[0] += 1

    event.listen(engine, "before_cursor_execute", before)
    return before


def _seed_chapter(
    project_id: str,
    body: str = "本方案采用 Cat6A 布线。",
    review_status: str = "confirmed",
) -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import OutlineNode, Section

    with session_scope() as session:
        node = OutlineNode(project_id=project_id, heading="技术方案", position=1)
        session.add(node)
        session.flush()
        session.add(
            Section(
                node_id=node.id,
                body=body,
                words=len(body),
                status="written",
                review_status=review_status,
            )
        )
        session.flush()
        return node.id


def test_perf_fix_project_listing_is_batched(db):
    """GET /projects 的统计查询数量与项目数无关，响应结构保持不变。"""
    from sqlalchemy import event

    from bidcraft.api.shared import _project_stats
    from bidcraft.db import get_engine, session_scope
    from bidcraft.models import Project

    client = _make_admin()
    for index in range(12):
        response = client.post("/projects", json={"name": f"perf-listing-{index}"})
        assert response.status_code == 201, response.text

    engine = get_engine()
    lock = threading.Lock()
    counter = [0]
    listener = _count_selects(engine, lock, counter)
    event.listen(engine, "before_cursor_execute", listener)
    try:
        response = client.get("/projects?limit=500")
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 12
    # 12 个项目 × 7 个 COUNT = 84（旧实现）；批量聚合固定 5 条 + 会话/鉴权查询。
    assert counter[0] <= 25, counter[0]
    assert set(items[0]["stats"]) == {
        "sources",
        "facts_total",
        "facts_filled",
        "missing_required",
        "outline",
        "written",
        "issues_open",
    }
    with session_scope() as session:
        project = session.get(Project, items[0]["id"])
        assert items[0]["stats"] == _project_stats(session, project.id)

    # 非管理员分支同样返回正确角色与统计（分页下推到 SQL）。
    owner = _make_user(client, "perf-listing-owner")
    own = [owner.post("/projects", json={"name": f"own-{i}"}).json() for i in range(3)]
    listed = owner.get("/projects").json()
    by_id = {item["id"]: item for item in listed}
    for project in own:
        assert by_id[project["id"]]["my_role"] == "owner"
        assert by_id[project["id"]]["stats"]["facts_total"] == 0


def test_perf_fix_issues_ordered_and_paginated_in_sql(db):
    from bidcraft.db import session_scope
    from bidcraft.models import Issue

    client = _make_admin()
    project = client.post("/projects", json={"name": "perf-issues"}).json()
    with session_scope() as session:
        for level in ("minor", "blocking", "major", "critical"):
            session.add(
                Issue(
                    project_id=project["id"],
                    level=level,
                    code="TEST",
                    finding=f"finding-{level}",
                )
            )
        session.flush()

    first = client.get(f"/projects/{project['id']}/issues?limit=2&offset=0").json()
    second = client.get(f"/projects/{project['id']}/issues?limit=2&offset=2").json()
    assert [row["level"] for row in first] == ["blocking", "critical"]
    assert [row["level"] for row in second] == ["major", "minor"]
    # 所有行都在 SQL 排好序后再切片。
    all_rows = client.get(f"/projects/{project['id']}/issues").json()
    assert [row["level"] for row in all_rows] == [
        "blocking",
        "critical",
        "major",
        "minor",
    ]


def test_perf_fix_job_index_and_backpressure_count(db, monkeypatch):
    from sqlalchemy import event, inspect

    from bidcraft.config import settings
    from bidcraft.db import get_engine, session_scope
    from bidcraft.jobqueue import check_backpressure
    from bidcraft.models import Job

    engine = get_engine()
    indexes = inspect(engine).get_indexes("jobs")
    assert any(index["column_names"] == ["status"] for index in indexes)
    assert any(
        index["column_names"] == ["status", "priority", "created_at"]
        for index in indexes
    )

    monkeypatch.setattr(settings, "max_queued_jobs", 0)
    monkeypatch.setattr(settings, "max_user_queued_jobs", 2)
    with session_scope() as session:
        for owner in ("u1", "u1", "u2"):
            session.add(Job(kind="test", status="queued", payload={"owner_id": owner}))
        session.flush()

    lock = threading.Lock()
    counter = [0]
    listener = _count_selects(engine, lock, counter)
    event.listen(engine, "before_cursor_execute", listener)
    try:
        with session_scope() as session:
            full = check_backpressure(session, "u1")
            free = check_backpressure(session, "u3")
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert full is not None and "上限" in full
    assert free is None
    # 每次背压检查只发一条按 JSON 字段过滤的 COUNT，不再读取全部 payload。
    assert counter[0] == 2, counter[0]


def test_perf_fix_sse_stops_on_disconnect(db):
    from bidcraft import jobs as jobs_module
    from bidcraft.api.routes.jobs import job_events
    from bidcraft.context import reset_current_user, set_current_user
    from bidcraft.db import session_scope
    from bidcraft.models import Job

    assert inspect.iscoroutinefunction(job_events)

    with session_scope() as session:
        job = Job(kind="test", status="queued", payload={"owner_id": "sse-user"})
        session.add(job)
        session.flush()
        job_id = job.id

    class FakeRequest:
        headers: ClassVar[dict[str, str]] = {}

        async def is_disconnected(self) -> bool:
            return True

    def forbidden(*_args, **_kwargs):
        raise AssertionError("disconnect 后不应继续轮询事件")

    token = set_current_user({"id": "sse-user", "role": "viewer"})
    original = jobs_module.events_since
    jobs_module.events_since = forbidden  # type: ignore[assignment]
    try:
        async def collect():
            response = await job_events(job_id, FakeRequest(), 0)
            return [frame async for frame in response.body_iterator]

        frames = asyncio.run(collect())
    finally:
        jobs_module.events_since = original  # type: ignore[assignment]
        reset_current_user(token)

    assert frames[0] == "retry: 3000\n\n"
    assert "event: snapshot" in frames[1]
    assert len(frames) == 2


def test_perf_fix_reparse_heavy_work_outside_transaction(db, monkeypatch, tender_path):
    from bidcraft.db import get_engine

    client = _make_admin()
    project = client.post("/projects", json={"name": "perf-reparse"}).json()
    with tender_path.open("rb") as handle:
        uploaded = client.post(
            f"/projects/{project['id']}/sources",
            files={"file": ("tender.txt", handle, "text/plain")},
        )
    assert uploaded.status_code == 200, uploaded.text

    from bidcraft.api.routes import projects as routes_projects

    observed = []
    original = routes_projects.parse_documents

    def watched(package_name, specs):
        observed.append(get_engine().pool.checkedout())
        return original(package_name, specs)

    monkeypatch.setattr(routes_projects, "parse_documents", watched)
    response = client.post(f"/projects/{project['id']}/parse")
    assert response.status_code == 200, response.text
    assert observed and observed[0] == 0
    assert response.json()["project"]["stats"]["sources"] == 1


def test_perf_fix_export_render_outside_transaction(db, monkeypatch):
    from bidcraft.db import get_engine

    client = _make_admin()
    project = client.post("/projects", json={"name": "perf-export"}).json()
    _seed_chapter(project["id"])

    from bidcraft.api.routes import projects as routes_projects

    observed = []
    original = routes_projects.export_markdown

    def watched(*args, **kwargs):
        observed.append(get_engine().pool.checkedout())
        return original(*args, **kwargs)

    monkeypatch.setattr(routes_projects, "export_markdown", watched)
    response = client.post(f"/projects/{project['id']}/export", json={"format": "md"})
    assert response.status_code == 200, response.text
    assert ".md" in response.headers["content-disposition"]
    assert observed and observed[0] == 0
    audit = client.get(f"/projects/{project['id']}/audit").json()["items"]
    assert any(item["action"] == "export.run" for item in audit)


def test_perf_fix_commercial_export_render_outside_transaction(db, monkeypatch):
    from bidcraft.db import get_engine, session_scope
    from bidcraft.export import commercial_xlsx
    from bidcraft.models import BoqQuoteVersion, utcnow

    admin = _make_admin()
    owner = _make_user(admin, "perf-commercial-owner")
    project = owner.post("/projects", json={"name": "perf-commercial"}).json()
    with session_scope() as session:
        version = BoqQuoteVersion(
            project_id=project["id"],
            name="报价",
            status="approved",
            prices={"清单!2": 10.0},
            summary={"total_amount": 20.0, "tax_amount": 2.6, "total_with_tax": 22.6},
            basis={"qty": {"清单!2": 2.0}, "tax_rate": 0.13, "currency": "CNY"},
            reviewed_by="李总",
            reviewed_at=utcnow(),
        )
        session.add(version)
        session.flush()
        version_id = version.id
    created = owner.post(
        f"/projects/{project['id']}/commercial",
        json={"name": "商务标", "quote_version_id": version_id},
    )
    assert created.status_code == 201, created.text

    observed = []
    original = commercial_xlsx.export_commercial_xlsx

    def watched(*args, **kwargs):
        observed.append(get_engine().pool.checkedout())
        return original(*args, **kwargs)

    monkeypatch.setattr(commercial_xlsx, "export_commercial_xlsx", watched)
    response = owner.post(
        f"/projects/{project['id']}/commercial/{created.json()['id']}/export?format=xlsx"
    )
    assert response.status_code == 200, response.text
    assert ".xlsx" in response.headers["content-disposition"]
    assert observed and observed[0] == 0
    audit = owner.get(f"/projects/{project['id']}/audit").json()["items"]
    assert any(item["action"] == "commercial.export" for item in audit)


def test_perf_fix_generation_refunds_when_model_fails(db, monkeypatch):
    """模型调用失败时，提前提交的扣费必须显式退还（等价原整事务回滚）。"""
    from bidcraft.api.routes import projects as routes_projects
    from bidcraft.db import session_scope
    from bidcraft.models import Fact, User

    admin = _make_admin()
    owner = _make_user(admin, "perf-gen-owner")
    project = owner.post("/projects", json={"name": "perf-generation"}).json()
    node_id = _seed_chapter(project["id"], body="", review_status="draft")
    with session_scope() as session:
        session.add(
            Fact(
                project_id=project["id"],
                key="project.name",
                label="项目名称",
                value="示例项目",
            )
        )
        user = session.query(User).filter_by(username="perf-gen-owner").one()
        user.credits = 50
        session.flush()

    def boom(*_args, **_kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(routes_projects, "generate_document", boom)
    with pytest.raises(RuntimeError, match="model down"):
        owner.post(f"/projects/{project['id']}/chapters/{node_id}/generate")
    with session_scope() as session:
        user = session.query(User).filter_by(username="perf-gen-owner").one()
        assert user.credits == 50


def test_perf_fix_check_result_helper_preserves_fields():
    from bidcraft.pipeline import _finding_results

    finding = {
        "id": "T-1",
        "level": "major",
        "category": "术语",
        "message": "术语不一致",
        "location": "第一章",
        "recommendation": "统一术语",
        "evidence": {"page": 1},
    }
    results = _finding_results([finding])
    assert len(results) == 1
    result = results[0]
    assert result.code == "T-1"
    assert result.level == "major"
    assert result.category == "术语"
    assert result.message == "术语不一致"
    assert result.location == "第一章"
    assert result.finding == "术语不一致"
    assert result.recommendation == "统一术语"
    assert result.evidence == {"page": 1}
    assert result.passed is False
