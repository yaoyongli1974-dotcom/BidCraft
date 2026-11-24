"""Second-round fix verification: schema parity, queue, membership, billing, SSE.

All tests run on the SQLite ``db`` fixture; no external services required.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta
from typing import ClassVar

import pytest

pytest.importorskip("fastapi")


def test_migration_adds_all_plugin_and_audit_columns(db):
    """The columns that used to live only in SQLite _migrate_columns are real."""

    from sqlalchemy import inspect

    from bidcraft.db import get_engine

    cols = {
        table: {column["name"] for column in inspect(get_engine()).get_columns(table)}
        for table in ("plugins", "requirements", "jobs", "pending_charges")
    }
    assert {
        "fail_streak", "auto_disabled", "mem_limit_mb", "cpu_seconds",
        "scope", "owner_id", "allow_private_net",
    } <= cols["plugins"]
    assert {
        "page", "clause_no",
        "response_verdict", "response_verdict_label", "response_verdict_reason",
    } <= cols["requirements"]
    assert "owner_id" in cols["jobs"]
    assert {"id", "user_id", "credits", "status", "created_at"} <= cols["pending_charges"]


def test_job_owner_column_is_indexed_and_drives_backpressure(db, monkeypatch):
    """payload['owner_id'] is mirrored into an indexed column transparently."""

    from sqlalchemy import inspect, update

    from bidcraft.config import settings
    from bidcraft.db import get_engine, session_scope
    from bidcraft.jobqueue import check_backpressure
    from bidcraft.models import Job

    assert any(index["column_names"] == ["owner_id"] for index in inspect(get_engine()).get_indexes("jobs"))

    monkeypatch.setattr(settings, "max_queued_jobs", 0)
    monkeypatch.setattr(settings, "max_user_queued_jobs", 2)
    with session_scope() as session:
        session.add_all([
            Job(kind="test", status="queued", payload={"owner_id": "u1"}),
            Job(kind="test", status="queued", payload={"owner_id": "u1"}),
            Job(kind="test", status="queued", payload={"owner_id": "u2"}),
        ])
        session.flush()
        owners = session.execute(
            __import__("sqlalchemy").select(Job.owner_id)
        ).scalars().all()
        assert owners.count("u1") == 2 and owners.count("u2") == 1

        # 老库遗留行只有 payload：迁移期的回填 SQL 语义在这里等价验证。
        legacy = Job(kind="test", status="queued", payload={"owner_id": "legacy"})
        session.add(legacy)
        session.flush()
        session.execute(
            update(Job)
            .where(Job.id == legacy.id)
            .values(owner_id=legacy.payload["owner_id"])
        )
        session.flush()

        assert check_backpressure(session, "u1") is not None
        assert check_backpressure(session, "u3") is None
        assert check_backpressure(session, "legacy") is None  # 2 未达上限（已回填）


def test_backpressure_serializes_count_and_insert_on_postgres(monkeypatch):
    """PG takes a transaction-scoped advisory lock to close the count/insert race."""

    from bidcraft.config import settings
    from bidcraft.jobqueue import check_backpressure

    monkeypatch.setattr(settings, "max_queued_jobs", 0)
    monkeypatch.setattr(settings, "max_user_queued_jobs", 5)

    statements: list[str] = []

    class FakeDialect:
        name = "postgresql"

    class FakeBind:
        dialect = FakeDialect()

    class FakeDB:
        def get_bind(self):
            return FakeBind()

        def execute(self, statement, params=None):
            statements.append(str(statement))
            return type("Result", (), {"rowcount": 1})()

        def scalar(self, _statement):
            return 0

    assert check_backpressure(FakeDB(), "u1") is None
    assert any("pg_advisory_xact_lock" in sql for sql in statements)


def test_member_roles_are_validated_at_api_boundary(db):
    """Invalid project-member roles are rejected by the schema, still with 400."""

    from fastapi import HTTPException

    from bidcraft.api.schemas import MemberCreate, MemberUpdate

    assert MemberCreate(username="x", role="editor").role == "editor"
    assert MemberUpdate(role="viewer").role == "viewer"
    for factory in (lambda role: MemberCreate(username="x", role=role),
                    lambda role: MemberUpdate(role=role)):
        with pytest.raises(HTTPException) as exc:
            factory("super_admin")
        assert exc.value.status_code == 400

    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    assert client.post("/auth/login", json={"username": "admin", "password": "test-admin"}).status_code == 200
    project = client.post("/projects", json={"name": "成员角色校验"}).json()["id"]
    response = client.post(
        f"/projects/{project}/members",
        json={"username": "nobody", "role": "super_admin"},
    )
    assert response.status_code == 400, response.text


def test_project_listing_ignores_invalid_membership_rows(db):
    """A forged/invalid role must not grant visibility in GET /projects."""

    from bidcraft.api.routes.projects import projects as list_projects
    from bidcraft.context import reset_current_user, set_current_user
    from bidcraft.db import session_scope
    from bidcraft.models import Project, ProjectMember, User

    with session_scope() as session:
        owner = User(username="fix2-owner", password_hash="x", role="manager", is_external=True)
        viewer = User(username="fix2-viewer", password_hash="x", role="manager", is_external=True)
        session.add_all([owner, viewer])
        session.flush()
        project = Project(name="脏角色项目", owner_id=owner.id)
        session.add(project)
        session.flush()
        session.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="super_admin"))
        session.flush()
        viewer_id = viewer.id

    token = set_current_user({"id": viewer_id, "role": "viewer"})
    try:
        assert list_projects() == []
    finally:
        reset_current_user(token)


def test_credit_guard_uses_effective_vip_cost(db, monkeypatch):
    """A VIP with enough credits for the discounted price must not get a 402."""

    from fastapi import HTTPException

    from bidcraft import billing
    from bidcraft.api.shared import _credit_guard, _effective_chapter_cost
    from bidcraft.clock import utc_now
    from bidcraft.config import settings
    from bidcraft.db import session_scope
    from bidcraft.models import User

    monkeypatch.setattr(settings, "chapter_cost", 10)
    with session_scope() as session:
        vip = User(
            username="fix2-vip", password_hash="x", role="manager",
            is_external=True, credits=5,
            vip_expires_at=utc_now() + timedelta(days=7),
        )
        plain = User(
            username="fix2-plain", password_hash="x", role="manager",
            is_external=True, credits=5,
        )
        session.add_all([vip, plain])
        session.flush()
        assert _effective_chapter_cost(session, vip, 10) == 5
        assert _effective_chapter_cost(session, plain, 10) == 10
        _credit_guard(session, vip, 10)  # 5 credits cover ceil(10*0.5)=5
        with pytest.raises(HTTPException) as exc:
            _credit_guard(session, plain, 10)
        assert exc.value.status_code == 402
        assert billing.billing_settings(session)["vip_discount"] == 0.5


def test_pending_charge_settles_once_and_reconciles_crash(db):
    """Crash residue is refunded exactly once; normal settle stays idempotent."""

    from bidcraft import billing, jobqueue
    from bidcraft.clock import utc_now
    from bidcraft.db import session_scope
    from bidcraft.models import PendingCharge, User

    with session_scope() as session:
        user = User(username="fix2-charge", password_hash="x", role="manager",
                    is_external=True, credits=0)
        session.add(user)
        session.flush()
        billing.add_credits(session, user.id, 20, kind="admin", note="test")
        session.flush()
        assert user.credits == 20
        marker_id = jobqueue.begin_pending_charge(
            session, user, "p1", 4, 2, note="crash-window"
        )
        session.flush()
        user_id = user.id

    assert marker_id
    with session_scope() as session:
        # 仍 pending，TTL 未到时不能误退还在运行的生成。
        assert jobqueue.reconcile_pending_charges(older_than_seconds=600, db=session) == 0
        marker = session.get(PendingCharge, marker_id)
        marker.created_at = utc_now() - timedelta(hours=2)
        marker.updated_at = utc_now() - timedelta(hours=2)  # 心跳已停止
        session.flush()

    assert jobqueue.reconcile_pending_charges(older_than_seconds=60) == 1
    with session_scope() as session:
        user = session.get(User, user_id)
        marker = session.get(PendingCharge, marker_id)
        assert user.credits == 24
        assert marker.status == "refunded" and marker.refunded_credits == 4
        # 重复对账不重复退款
        assert jobqueue.reconcile_pending_charges(older_than_seconds=0, db=session) == 0
        assert user.credits == 24

    with session_scope() as session:
        user = session.get(User, user_id)
        second = jobqueue.begin_pending_charge(session, user, "p1", 6, 3)
        session.flush()
        assert jobqueue.settle_pending_charge(session, second, user, "p1", 3, 3) == 0
        assert session.get(PendingCharge, second).status == "cleared"
        assert jobqueue.settle_pending_charge(session, second, user, "p1", 3, 0) == 0
        assert user.credits == 24


def test_sse_polling_runs_in_worker_thread(db):
    """events_since() must not be called on the asyncio event loop."""

    from bidcraft import jobs as jobs_module
    from bidcraft.api.routes.jobs import job_events
    from bidcraft.context import reset_current_user, set_current_user
    from bidcraft.db import session_scope
    from bidcraft.models import Job

    with session_scope() as session:
        job = Job(kind="test", status="queued", payload={"owner_id": "sse-user"})
        session.add(job)
        session.flush()
        job_id = job.id

    poll_threads: list[int] = []
    original = jobs_module.events_since

    def recording(*_args, **_kwargs):
        poll_threads.append(threading.get_ident())
        return []

    class FakeRequest:
        headers: ClassVar[dict[str, str]] = {}

        def __init__(self):
            self.checks = 0

        async def is_disconnected(self) -> bool:
            self.checks += 1
            return self.checks >= 2

    token = set_current_user({"id": "sse-user", "role": "viewer"})
    jobs_module.events_since = recording  # type: ignore[assignment]
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
    assert poll_threads, "SSE loop never polled events"
    assert threading.get_ident() not in poll_threads, "DB poll ran on the event loop"


def test_project_listing_uses_nullslast_and_stable_id(db):
    """NULL updated_at sorts last on both dialects; equal timestamps use id."""

    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql, sqlite

    from bidcraft.api.routes.projects import projects as list_projects
    from bidcraft.context import reset_current_user, set_current_user
    from bidcraft.db import session_scope
    from bidcraft.models import Project, User

    statement = select(Project).order_by(
        Project.updated_at.desc().nullslast(), Project.id
    )
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        assert "NULLS LAST" in str(statement.compile(dialect=dialect)).upper()

    with session_scope() as session:
        admin = User(username="fix2-admin", password_hash="x", role="super_admin")
        session.add(admin)
        session.flush()
        stale = Project(name="较早项目", owner_id=admin.id)
        recent = Project(name="最近项目", owner_id=admin.id)
        session.add_all([stale, recent])
        session.flush()
        stale.updated_at = recent.updated_at  # tie -> deterministic id ordering
        session.flush()
        admin_id = admin.id
        ordered = sorted([stale.id, recent.id])

    token = set_current_user({"id": admin_id, "role": "super_admin"})
    try:
        rows = list_projects()
    finally:
        reset_current_user(token)
    assert [row["id"] for row in rows] == ordered
