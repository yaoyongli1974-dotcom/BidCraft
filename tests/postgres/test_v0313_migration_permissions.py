"""v0.31.3 阶段 5：真实 PostgreSQL 上的全量迁移往返与权限边界。

先复现、后修生产代码；本文件只依赖真实 PG（``BIDCRAFT_TEST_PG_URL``），
不把 SQLite 动态类型结果当作 PostgreSQL 结论。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text

pytest.importorskip("fastapi")


def _head() -> str:
    from bidcraft.migrations import __file__ as migrations_init

    versions = Path(migrations_init).parent / "versions"
    revisions: dict[str, str] = {}
    parents: set[str] = set()
    for path in versions.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        rev = re.search(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", source, re.MULTILINE)
        if rev is None:
            continue
        revisions[rev.group(1)] = path.name
        parent = re.search(r"^down_revision\s*=\s*['\"]([^'\"]+)['\"]", source, re.MULTILINE)
        if parent is not None:
            parents.add(parent.group(1))
    heads = set(revisions) - parents
    assert len(heads) == 1
    return heads.pop()


def _alembic_config(pg_engine):
    from alembic.config import Config

    from bidcraft.migrations import __file__ as migrations_init

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(migrations_init).parent))
    cfg.set_main_option(
        "sqlalchemy.url",
        pg_engine.url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    return cfg


def _columns(pg_engine, table: str) -> set[str]:
    return {item["name"] for item in inspect(pg_engine).get_columns(table)}


def _indexes(pg_engine, table: str) -> set[str]:
    return {item["name"] for item in inspect(pg_engine).get_indexes(table)}


def test_full_chain_downgrade_to_baseline_and_reupgrade_on_pg(pg_engine):
    """head → 0001_baseline → head，schema 与业务数据都必须还原。"""
    from alembic import command

    from bidcraft.db import session_scope
    from bidcraft.models import Job, Project

    cfg = _alembic_config(pg_engine)
    owner_id = "migration-owner"
    with session_scope() as db:
        project = Project(name="全链路往返项目", owner_id=owner_id)
        db.add(project)
        db.flush()
        db.add(
            Job(
                id=str(uuid.uuid4()),
                project_id=project.id,
                kind="generate",
                status="queued",
                owner_id=owner_id,
                payload={"owner_id": owner_id},
            )
        )
        project_id = project.id

    try:
        command.downgrade(cfg, "0001_baseline")
        with pg_engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == "0001_baseline"
        assert "owner_id" not in _columns(pg_engine, "jobs")
        assert "refund_claimed" not in _columns(pg_engine, "jobs")
        assert "pending_charges" not in inspect(pg_engine).get_table_names()
        assert "generation_leases" not in inspect(pg_engine).get_table_names()
        assert "ix_jobs_status_priority_created" not in _indexes(pg_engine, "jobs")

        command.upgrade(cfg, "head")
        with pg_engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == _head()
        assert "owner_id" in _columns(pg_engine, "jobs")
        assert "refund_claimed" in _columns(pg_engine, "jobs")
        assert "pending_charges" in inspect(pg_engine).get_table_names()
        assert "sections_at_charge" in _columns(pg_engine, "pending_charges")
        assert "generation_leases" in inspect(pg_engine).get_table_names()
        assert "ix_jobs_status_priority_created" in _indexes(pg_engine, "jobs")
        with session_scope() as db:
            restored = db.get(Project, project_id)
            assert restored is not None and restored.name == "全链路往返项目"
            jobs = db.scalars(select(Job).where(Job.project_id == project_id)).all()
            assert len(jobs) == 1 and jobs[0].owner_id == owner_id
    finally:
        command.upgrade(cfg, "head")


def _seed_user(db, username: str, role: str, password: str = "Test-Pass-123!"):
    from bidcraft.models import User
    from bidcraft.security import hash_password

    user = User(
        id=str(uuid.uuid4()),
        username=username,
        display_name=username,
        role=role,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.flush()
    return user, password


def test_membership_and_cross_project_finding_isolation_on_pg(pg_engine):
    """成员角色过滤、批量授权与 finding→report→project 归属在 PG 上生效。"""
    from bidcraft.api.shared import _require_write
    from bidcraft.authorization import authorize_project, authorize_projects
    from bidcraft.context import reset_current_user, set_current_user
    from bidcraft.db import session_scope
    from bidcraft.evaluation.report import finding_for_project, update_finding
    from bidcraft.models import (
        Project,
        ProjectMember,
        QualityFinding,
        QualityReport,
    )

    with session_scope() as db:
        owner_a, _ = _seed_user(db, "pg-owner-a", "manager")
        owner_b, _ = _seed_user(db, "pg-owner-b", "manager")
        editor, _ = _seed_user(db, "pg-editor", "manager")
        viewer, _ = _seed_user(db, "pg-viewer", "viewer")
        intruder, _ = _seed_user(db, "pg-intruder", "manager")
        project_a = Project(name="A项目", owner_id=owner_a.id)
        project_b = Project(name="B项目", owner_id=owner_b.id)
        db.add_all([project_a, project_b])
        db.flush()
        db.add_all(
            [
                ProjectMember(id=str(uuid.uuid4()), project_id=project_a.id, user_id=editor.id, role="editor"),
                ProjectMember(id=str(uuid.uuid4()), project_id=project_a.id, user_id=viewer.id, role="viewer"),
                # 非法角色不应授予任何可见性（历史上曾漏过滤）。
                ProjectMember(id=str(uuid.uuid4()), project_id=project_a.id, user_id=intruder.id, role="owner"),
            ]
        )
        report_b = QualityReport(id=str(uuid.uuid4()), project_id=project_b.id)
        db.add(report_b)
        db.flush()
        finding_b = QualityFinding(
            id=str(uuid.uuid4()),
            report_id=report_b.id,
            title="B项目质量问题",
            level="major",
            status="open",
        )
        db.add(finding_b)
        db.flush()
        ids = {
            "a": project_a.id,
            "b": project_b.id,
            "editor": editor.id,
            "viewer": viewer.id,
            "intruder": intruder.id,
            "finding_b": finding_b.id,
        }

    tokens: list = []

    def as_user(user_id: str, role: str = "manager"):
        token = set_current_user({"id": user_id, "username": user_id, "role": role})
        tokens.append(token)
        return token

    try:
        as_user(ids["editor"])
        with session_scope() as db:
            project, effective = authorize_project(db, ids["a"])
            assert project.id == ids["a"] and effective == "editor"
            assert [p.id for p in authorize_projects(db, [ids["a"], ids["b"]])] == [ids["a"]]
            assert finding_for_project(db, ids["finding_b"], ids["a"]) is None
            assert update_finding(db, ids["finding_b"], "handled", "越权", actor=ids["editor"], project_id=ids["a"]) is None

        as_user(ids["intruder"])
        with session_scope() as db:
            with pytest.raises(LookupError):
                authorize_project(db, ids["a"])
            assert authorize_projects(db, [ids["a"]]) == []

        as_user(ids["viewer"])
        with session_scope() as db:
            project, effective = authorize_project(db, ids["a"])
            assert project.id == ids["a"] and effective == "viewer"
            # HTTP 薄封装把领域 Forbidden 映射成 403，不直接泄漏领域异常。
            with pytest.raises(Exception) as exc:
                _require_write(effective)
            assert getattr(exc.value, "status_code", None) == 403
    finally:
        for token in reversed(tokens):
            reset_current_user(token)


def test_finding_disposition_http_is_scoped_on_pg(pg_engine):
    """HTTP PATCH 跨项目 finding 必须 404，且不产生越权审计。"""
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app
    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog, Project, QualityFinding, QualityReport

    with session_scope() as db:
        owner_a, password_a = _seed_user(db, "http-owner-a", "manager")
        owner_b, _ = _seed_user(db, "http-owner-b", "manager")
        project_a = Project(name="HTTP A", owner_id=owner_a.id)
        project_b = Project(name="HTTP B", owner_id=owner_b.id)
        db.add_all([project_a, project_b])
        db.flush()
        report_b = QualityReport(id=str(uuid.uuid4()), project_id=project_b.id)
        db.add(report_b)
        db.flush()
        finding_b = QualityFinding(
            id=str(uuid.uuid4()), report_id=report_b.id, title="B finding", status="open"
        )
        db.add(finding_b)
        db.flush()
        refs = {
            "a": project_a.id,
            "b": project_b.id,
            "finding_b": finding_b.id,
            "username": owner_a.username,
            "password": password_a,
        }

    client = TestClient(create_app())
    response = client.post(
        "/auth/login",
        json={"username": refs["username"], "password": refs["password"]},
    )
    assert response.status_code == 200, response.text
    response = client.patch(
        f"/projects/{refs['a']}/evaluation/findings/{refs['finding_b']}",
        json={"status": "handled", "note": "cross-project"},
    )
    assert response.status_code == 404, response.text
    response = client.get(f"/projects/{refs['b']}")
    assert response.status_code == 404, response.text
    with session_scope() as db:
        finding = db.get(QualityFinding, refs["finding_b"])
        assert finding is not None and finding.status == "open"
        audits = db.scalars(
            select(AuditLog).where(AuditLog.action == "quality.finding_update")
        ).all()
        assert audits == []
