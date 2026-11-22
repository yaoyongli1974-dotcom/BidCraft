"""真实 PostgreSQL 端到端验证（opt-in）。

运行方式::

    BIDCRAFT_TEST_PG_URL='postgresql+psycopg://u:p@127.0.0.1:5432/bidcraft_test' \
        .venv/bin/python -m pytest tests/test_postgres_integration.py

会**清空目标库的所有表**，请务必指向专用测试库。默认未设置环境变量时整文件跳过，
不影响常规 SQLite 套件。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

PG_URL = os.getenv("BIDCRAFT_TEST_PG_URL", "").strip()
pytestmark = pytest.mark.skipif(not PG_URL, reason="需要 BIDCRAFT_TEST_PG_URL（专用测试库）")


@pytest.fixture(scope="module")
def pg_engine():
    from bidcraft.config import settings
    from bidcraft.db import get_engine, init_db, reset_engine_for_tests
    from bidcraft.models import Base

    old_url = settings.db_url
    try:
        settings.db_url = PG_URL
        reset_engine_for_tests(PG_URL)
        engine = get_engine()
        Base.metadata.drop_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        init_db()
        yield engine
    finally:
        settings.db_url = old_url
        reset_engine_for_tests(old_url)


def _make_jobs(count: int) -> list[str]:
    from bidcraft.db import session_scope
    from bidcraft.models import Job

    ids = [str(uuid.uuid4()) for _ in range(count)]
    with session_scope() as db:
        for job_id in ids:
            db.add(Job(id=job_id, kind="kb_reindex", status="queued", payload={}, attempts=0))
    return ids


def test_schema_created_and_alembic_stamped(pg_engine):
    from sqlalchemy import inspect

    tables = set(inspect(pg_engine).get_table_names())
    assert {"projects", "jobs", "sections", "facts", "alembic_version"} <= tables
    with pg_engine.connect() as connection:
        head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert head == "0007_yibiao_batch4"


def test_crud_roundtrip_with_json_and_chinese(pg_engine):
    from bidcraft.db import session_scope
    from bidcraft.models import Fact, OutlineNode, Project, Section

    with session_scope() as db:
        project = Project(name="PostgreSQL 中文项目", meta={"source": "pg", "score": 1.5})
        db.add(project)
        db.flush()
        db.add(
            Fact(
                project_id=project.id,
                key="budget",
                label="预算金额",
                value="人民币 280 万元",
            )
        )
        db.flush()
        node = OutlineNode(project_id=project.id, heading="总体方案", position=1)
        db.add(node)
        db.flush()
        db.add(
            Section(
                node_id=node.id,
                body="本方案采用 Cat6A 综合布线系统。",
                status="edited",
                review_status="confirmed",
            )
        )
        project_id = project.id

    with session_scope() as db:
        restored = db.get(Project, project_id)
        assert restored is not None
        assert restored.name == "PostgreSQL 中文项目"
        assert restored.meta["source"] == "pg"
        assert restored.meta["score"] == 1.5
        assert any(fact.value == "人民币 280 万元" for fact in restored.facts)


def test_claim_next_is_atomic_under_concurrency(pg_engine):
    from bidcraft.db import session_scope
    from bidcraft.jobqueue import claim_next
    from bidcraft.models import Job

    ids = _make_jobs(12)
    claimed: dict[str, object] = {}
    lock = threading.Lock()

    def claim_until_done(_):
        # 真实 worker 循环会反复调用 claim_next；并发碰撞时单次调用返回 None 属正常。
        for _ in range(40):
            job = claim_next()
            if job is None:
                time.sleep(0.01)
                continue
            with lock:
                claimed[job.id] = job
            return

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(claim_until_done, range(12)))

    assert len(claimed) == 12, "仍有任务没被认领"
    assert set(claimed) == set(ids)
    assert len({job.claim_token for job in claimed.values()}) == 12, "认领令牌没有每次刷新"
    with session_scope() as db:
        rows = db.query(Job).filter(Job.id.in_(ids)).all()
        assert {row.id for row in rows} == set(ids)
        assert all(row.status == "running" for row in rows)


def test_fencing_rejects_stale_token_on_postgres(pg_engine):
    from bidcraft.db import session_scope
    from bidcraft.jobqueue import claim_context, claim_next, fenced_job
    from bidcraft.models import Job

    job_id = _make_jobs(1)[0]
    first = claim_next()
    assert first is not None and first.id == job_id
    old_token = first.claim_token

    with session_scope() as db:
        job = db.get(Job, job_id)
        job.claim_token = "new-token-from-reaper"
        job.status = "queued"

    with claim_context(old_token), session_scope() as db:
        assert fenced_job(db, job_id) is None
    with claim_context("new-token-from-reaper"), session_scope() as db:
        assert fenced_job(db, job_id) is not None


def test_export_gate_reads_confirmed_chapters_on_postgres(pg_engine):
    from bidcraft.db import session_scope
    from bidcraft.export_gate import ExportGateError, enforce_export_gate
    from bidcraft.models import OutlineNode, Project, Section

    with session_scope() as db:
        project = Project(name="PG 导出门禁")
        db.add(project)
        db.flush()
        db.add(OutlineNode(project_id=project.id, heading="总体方案", position=1))
        db.flush()
        db.add(
            Section(
                node_id=db.query(OutlineNode).filter_by(project_id=project.id).one().id,
                body="已经有正文但还没确认。",
                review_status="draft",
            )
        )
        project_id = project.id

    with session_scope() as db, pytest.raises(ExportGateError, match="未确认"):
        enforce_export_gate(db, project_id, "docx")

    with session_scope() as db:
        for section in db.query(Section).all():
            if section.body:
                section.review_status = "confirmed"

    with session_scope() as db:
        assert enforce_export_gate(db, project_id, "docx") == 0


def test_delete_user_enforces_all_foreign_keys_on_postgres(pg_engine):
    """SQLite 从不启用外键，PostgreSQL 强制——删除路径必须清干净所有子表。"""

    from bidcraft.compliance import delete_account
    from bidcraft.db import session_scope
    from bidcraft.models import (
        AuthSession,
        CreditTransaction,
        InvoiceRequest,
        OutlineNode,
        Project,
        ProjectMember,
        RechargeOrder,
        Section,
        User,
    )

    with session_scope() as db:
        owner = User(username="pg-owner", password_hash="x", role="manager")
        other = User(username="pg-other", password_hash="x", role="manager")
        db.add_all([owner, other])
        db.flush()
        project = Project(name="待删项目", owner_id=owner.id)
        db.add(project)
        db.flush()
        node = OutlineNode(project_id=project.id, heading="总体方案", position=1)
        db.add(node)
        db.flush()
        db.add(Section(node_id=node.id, body="正文", review_status="draft"))
        db.add(RechargeOrder(user_id=owner.id, out_trade_no="PG-1", amount_cents=100, credits=1))
        db.add(InvoiceRequest(user_id=owner.id, title="某公司", amount_cents=100))
        db.add(CreditTransaction(user_id=owner.id, amount=1, kind="admin", note="test"))
        db.add(AuthSession(user_id=owner.id, token_hash="t", expires_at=__import__("datetime").datetime(2099, 1, 1)))
        db.add(ProjectMember(project_id=project.id, user_id=other.id, role="editor"))
        owner_id = owner.id

    with session_scope() as db:
        user = db.get(User, owner_id)
        removed = delete_account(db, user)
        assert removed["projects"] == 1

    with session_scope() as db:
        assert db.get(User, owner_id) is None


@pytest.mark.skipif(
    not __import__("shutil").which("pg_dump"), reason="需要 pg_dump（postgresql-client）"
)
def test_backup_command_uses_pg_dump(pg_engine, tmp_path):
    import zipfile

    from typer.testing import CliRunner

    from bidcraft.cli import app

    target = tmp_path / "pg-backup.zip"
    result = CliRunner().invoke(app, ["backup", "--output", str(target)])
    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(target) as bundle:
        names = set(bundle.namelist())
        assert "dump.sql" in names and "manifest.json" in names
        assert "bidcraft.db" not in names
        manifest = __import__("json").loads(bundle.read("manifest.json"))
        assert manifest["db_backend"] == "postgresql"
        assert manifest["db_file"] == "dump.sql"
        assert bundle.read("dump.sql").startswith((b"--", b"SET", b"CREATE"))


def test_migration_guard_uses_advisory_lock(pg_engine):
    """两进程式并发启动时，后到者必须等前者释放咨询锁。"""

    from bidcraft.db import _migration_guard

    entered_first = threading.Event()
    release_first = threading.Event()
    entered_second = threading.Event()
    observed: list[str] = []

    def first():
        with _migration_guard():
            entered_first.set()
            assert release_first.wait(5), "第一个持锁者超时"

    def second():
        assert entered_first.wait(5)
        with _migration_guard():
            observed.append("second")
            entered_second.set()

    t1 = threading.Thread(target=first)
    t2 = threading.Thread(target=second)
    t1.start()
    assert entered_first.wait(5)
    t2.start()
    time.sleep(0.5)
    assert not entered_second.is_set(), "第二个实例没有等待 PG 咨询锁"
    release_first.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert observed == ["second"]
