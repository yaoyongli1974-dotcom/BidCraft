"""RC-12：v0.33.0 在真实 PostgreSQL 上的迁移往返与 RC-05 并发修复验证。

opt-in：未设置 ``BIDCRAFT_TEST_PG_URL`` 时由 ``tests/postgres/conftest.py`` 的
autouse fixture 干净跳过；设置后只连专用测试库。迁移部分执行真实 DDL 并用
SQLAlchemy ``inspect`` / PG catalog 校验；并发部分用独立线程 + 独立 Session +
``threading.Barrier`` 起跑，条件 UPDATE 全部由生产代码执行，不 mock、不用
Python 全局锁串行化业务操作，也不把 SQLite 的动态类型结果当作 PG 结论。

历史边界：0001-0012 是历史 revision，只读、不编辑；降级只走到 0012。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text, update

from bidcraft import billing, jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import init_db, session_scope
from bidcraft.models import CreditTransaction, Job, JobEvent, PendingCharge, User

pytest.importorskip("psycopg")

PG_URL = os.getenv("BIDCRAFT_TEST_PG_URL", "").strip()
pytestmark = pytest.mark.skipif(not PG_URL, reason="需要 BIDCRAFT_TEST_PG_URL（专用测试库）")

REV_0013 = "0013_chapter_planning"
REV_0014 = "0014_kb_asset_metadata"
REV_0015 = "0015_fact_confirmation"
PARENT_0012 = "0012_generation_lease"
TARGETS = (REV_0013, REV_0014, REV_0015)

CHAPTER_PLAN_COLUMNS = {
    "id", "project_id", "node_id", "package_id", "version", "status",
    "objective", "raw_response", "model_id", "prompt_version", "user_instruction",
    "created_by", "confirmed_by", "created_at", "updated_at", "confirmed_at", "used_at",
    "score_points", "requirements", "conditions", "challenges", "strategy", "measures",
    "knowledge_refs", "fact_refs", "planned_tables", "planned_diagrams",
    "forbidden_assumptions", "quality_criteria", "user_adjustments", "validation_errors",
}
CHAPTER_PLAN_INDEXES = {
    "ix_chapter_plans_project_node_status", "ix_chapter_plans_project_package",
    "ix_chapter_plans_project_id", "ix_chapter_plans_node_id", "ix_chapter_plans_status",
}
CHAPTER_PLAN_UNIQUES = {"uq_chapter_plan_project_node_version"}
DOCUMENT_COLUMNS = {
    "structured", "review_status", "reviewed_by", "reviewed_at", "published_at",
    "rejected_reason", "source_file_id",
}
DUP_TABLES = {"kb_asset_duplicate_groups", "kb_asset_duplicate_members"}
GROUP_COLUMNS = {
    "id", "kb_id", "fingerprint", "similarity", "status", "reason", "created_by",
    "created_at", "reviewed_by", "reviewed_at",
}
GROUP_INDEXES = {
    "ix_kb_asset_duplicate_groups_kb_id", "ix_kb_asset_duplicate_groups_kb_status",
}
GROUP_UNIQUES = {"uq_kb_asset_duplicate_group"}
MEMBER_COLUMNS = {"id", "group_id", "document_id", "score", "role"}
MEMBER_INDEXES = {
    "ix_kb_asset_duplicate_members_group_id",
    "ix_kb_asset_duplicate_members_document_id",
}
MEMBER_UNIQUES = {"uq_kb_asset_duplicate_member"}
CONFIRMATION_COLUMNS = {"confirmed_by", "confirmed_at"}
# 拼接仅为避免 gitleaks 把测试 fixture 键误判为 API key；运行值不变。
LEGACY_FACT_KEY = "rc12" "-legacy-fact"

REAPER_ROUNDS = 20
OWNER_ROUNDS = 20
MARK_ROUNDS = 20
CLAIM_ROUNDS = 3
CLAIM_WORKERS = 6


# ---------------------------------------------------------------------------
# Alembic / schema helpers
# ---------------------------------------------------------------------------


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


def _script_directory(cfg):
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(cfg)


def _head(cfg) -> str:
    heads = _script_directory(cfg).get_heads()
    assert len(heads) == 1, f"expected a single head, got {heads}"
    return heads[0]


def _db_version(pg_engine) -> str | None:
    from alembic.runtime.migration import MigrationContext

    with pg_engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _tables(pg_engine) -> set[str]:
    return set(inspect(pg_engine).get_table_names())


def _schema(pg_engine, table: str) -> dict[str, set[str]]:
    inspector = inspect(pg_engine)
    return {
        "columns": {item["name"] for item in inspector.get_columns(table)},
        "indexes": {
            item["name"]
            for item in inspector.get_indexes(table)
            if item.get("name")
        },
        "uniques": {
            item["name"]
            for item in inspector.get_unique_constraints(table)
            if item.get("name")
        },
    }


def _assert_head_artifacts(pg_engine) -> None:
    """v0.33 RC 依赖的 v0.32 产物在 head 必须全部存在（真实 PG catalog）。"""

    assert {"chapter_plans"} | DUP_TABLES <= _tables(pg_engine)

    plan = _schema(pg_engine, "chapter_plans")
    assert CHAPTER_PLAN_COLUMNS <= plan["columns"]
    assert CHAPTER_PLAN_INDEXES <= plan["indexes"]
    assert CHAPTER_PLAN_UNIQUES <= plan["uniques"]

    doc = _schema(pg_engine, "kb_documents")
    assert DOCUMENT_COLUMNS <= doc["columns"]

    group = _schema(pg_engine, "kb_asset_duplicate_groups")
    assert GROUP_COLUMNS <= group["columns"]
    assert GROUP_INDEXES <= group["indexes"]
    assert GROUP_UNIQUES <= group["uniques"]

    member = _schema(pg_engine, "kb_asset_duplicate_members")
    assert MEMBER_COLUMNS <= member["columns"]
    assert MEMBER_INDEXES <= member["indexes"]
    assert MEMBER_UNIQUES <= member["uniques"]

    facts = _schema(pg_engine, "facts")
    assert CONFIRMATION_COLUMNS <= facts["columns"]


def _assert_no_server_defaults(pg_engine) -> None:
    """确认信息必须由服务端盖章：历史行不得被 DDL 写成「已审核 / 已确认」。"""

    with pg_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_name, column_name, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND ("
                "  (table_name = 'kb_documents' AND column_name = 'review_status') OR "
                "  (table_name = 'facts' AND column_name IN ('confirmed_by', 'confirmed_at'))"
                ")"
            )
        ).fetchall()
    found = {(row[0], row[1]) for row in rows}
    assert found == {
        ("kb_documents", "review_status"),
        ("facts", "confirmed_by"),
        ("facts", "confirmed_at"),
    }
    assert all(row[2] is None for row in rows), rows


def _seed_legacy_rows(pg_engine) -> dict[str, str]:
    """只写基线列：这些行在 0012 与 head 两种 schema 下都必须有效。"""

    ids = {name: str(uuid.uuid4()) for name in ("project", "kb", "doc", "fact")}
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, name, package, status, buyer, meta, is_template, "
                "created_at, updated_at) VALUES (:id, :name, 'weakcurrent-cabling', 'intake', "
                "'', '{}', false, '2020-01-01 00:00:00', '2020-01-01 00:00:00')"
            ),
            {"id": ids["project"], "name": "rc12 迁移往返项目"},
        )
        connection.execute(
            text(
                "INSERT INTO knowledge_bases (id, name, kind, provider, location, api_key_enc, "
                "dataset_id, options, enabled, is_default, chunk_count, last_test_message, "
                "created_at, updated_at) VALUES (:id, :name, 'local', 'local', '', '', '', "
                "'{}', true, false, 0, '', '2020-01-01 00:00:00', '2020-01-01 00:00:00')"
            ),
            {"id": ids["kb"], "name": ids["kb"]},
        )
        connection.execute(
            text(
                "INSERT INTO kb_documents (id, kb_id, source, sha256, size, mime, pages, "
                "status, version, kind, tags, error, created_at, updated_at) "
                "VALUES (:id, :kb, :source, '', 0, '', 0, 'ready', 1, 'other', '[]', '', "
                "'2020-01-01 00:00:00', '2020-01-01 00:00:00')"
            ),
            {"id": ids["doc"], "kb": ids["kb"], "source": "rc12-legacy.pdf"},
        )
        connection.execute(
            text(
                "INSERT INTO facts (id, project_id, key, label, value, unit, status, "
                "confidence, required, source_page, source_quote, updated_at) "
                "VALUES (:id, :project, :key, :label, :value, '', 'extracted', 0.0, false, "
                "0, '', '2020-01-01 00:00:00')"
            ),
            {
                "id": ids["fact"],
                "project": ids["project"],
                "key": LEGACY_FACT_KEY,
                "label": "历史事实",
                "value": "老口径",
            },
        )
    return ids


def _legacy_value(pg_engine, table: str, row_id: str, column: str):
    with pg_engine.connect() as connection:
        return connection.execute(
            text(f"SELECT {column} FROM {table} WHERE id = :id"), {"id": row_id}
        ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# 并发 helpers
# ---------------------------------------------------------------------------


def _pair(left, right):
    """两个线程用 Barrier 起跑，独立 Session，收集真实返回值/异常。"""

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def run(name, fn):
        try:
            barrier.wait(timeout=30)
            results[name] = fn()
        except Exception as exc:  # noqa: BLE001 - 保留线程内真实失败
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("left", left), name="rc12-pair-left"),
        threading.Thread(target=run, args=("right", right), name="rc12-pair-right"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "PG concurrency thread did not finish"
    assert not errors, f"unexpected thread errors: {errors!r}"
    return results["left"], results["right"]


def _seed_running_job(
    *, attempts: int = 0, lease_offset: int = 300, charged: int = 0,
    chapters: int = 3, done: int = 0,
) -> tuple[str, str, str]:
    """返回 (job_id, project_id, user_id)；credits 起点恒为 100。"""

    job_id = str(uuid.uuid4())
    project_id = f"rc12-{uuid.uuid4().hex[:8]}"
    user_id = ""
    with session_scope() as db:
        payload: dict[str, object] = {
            "done_steps": [f"chapter:{i}" for i in range(done)]
        }
        if charged:
            payload.update(
                charged=charged,
                chapters=chapters,
                user_id="",
                billing_account_id="",
            )
        job = Job(
            id=job_id,
            kind="generate" if charged else "agent",
            project_id=project_id,
            status="running",
            attempts=attempts,
            max_attempts=3,
            claim_token="rc12-token",
            worker_id=jobqueue._worker_id,
            claimed_at=utc_now(),
            lease_expires_at=utc_now() + timedelta(seconds=lease_offset),
            heartbeat_at=utc_now(),
            payload=payload,
        )
        db.add(job)
        if charged:
            user = User(
                id=str(uuid.uuid4()),
                username=f"rc12_{uuid.uuid4().hex[:8]}",
                display_name="PG RC12",
                password_hash="test-not-a-real-hash",
                role="manager",
                is_external=True,
                credits=100,
            )
            db.add(user)
            db.flush()
            user_id = user.id
            # 不要原地改已挂到 Job 上的 dict：SQLAlchemy 会用同一对象做脏检查。
            job.payload = {
                **payload,
                "user_id": user.id,
                "billing_account_id": user.id,
            }
            billing.charge_chapters(
                db, user, charged, project_id=project_id, note="rc12 concurrency"
            )
    return job_id, project_id, user_id


def _seed_charge_pair(*, done: int = 0) -> tuple[str, str, str, str]:
    """ charged=6 / chapters=3 / done=N：返回 (job_id, marker_id, user_id, project_id)。"""

    job_id, project_id, user_id = _seed_running_job(
        charged=6, chapters=3, done=done
    )
    with session_scope() as db:
        user = db.get(User, user_id)
        marker_id = jobqueue.begin_pending_charge(
            db, user, project_id, 6, 3, note=f"job:{job_id}|rc12 marker"
        )
        assert marker_id
        job = db.get(Job, job_id)
        job.payload = {**(job.payload or {}), "pending_charge_id": marker_id}
    return job_id, marker_id, user_id, project_id


def _job_state(job_id: str) -> dict[str, object]:
    with session_scope() as db:
        job = db.get(Job, job_id)
        events = list(
            db.scalars(
                select(JobEvent.kind).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
            )
        )
        return {
            "status": job.status,
            "attempts": job.attempts,
            "token": job.claim_token,
            "events": events,
            "refund_claimed": bool(job.refund_claimed),
            "refund_source": (job.payload or {}).get("refund_source"),
        }


def _balance(user_id: str) -> int:
    with session_scope() as db:
        return int(db.get(User, user_id).credits)


def _refunds(user_id: str) -> list[int]:
    with session_scope() as db:
        return list(
            db.scalars(
                select(CreditTransaction.amount).where(
                    CreditTransaction.user_id == user_id,
                    CreditTransaction.amount > 0,
                )
            )
        )


# ---------------------------------------------------------------------------
# 1) v0.33 RC 产物在 PG 上的迁移往返
# ---------------------------------------------------------------------------


def test_v0330_head_artifacts_on_pg(pg_engine):
    """init_db 在 head 建出的表/列/索引/唯一约束必须齐全，且确认列无 server_default。"""

    assert pg_engine.dialect.name == "postgresql"
    init_db()  # 幂等：conftest 已 init_db，这里显式再跑一次确认可重复执行。
    cfg = _alembic_config(pg_engine)
    head = _head(cfg)
    script = _script_directory(cfg)
    revisions = {item.revision for item in script.walk_revisions()}
    assert set(TARGETS) <= revisions
    for revision, parent in (
        (REV_0015, REV_0014),
        (REV_0014, REV_0013),
        (REV_0013, PARENT_0012),
    ):
        assert script.get_revision(revision).down_revision == parent
    assert _db_version(pg_engine) == head
    with pg_engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == head
    _assert_head_artifacts(pg_engine)
    _assert_no_server_defaults(pg_engine)


def test_v0330_migration_roundtrip_on_pg(pg_engine):
    """head → 0014 → 0013 → 0012 → head 真实 DDL 往返；旧数据保留、产物可逆。"""

    from alembic import command

    cfg = _alembic_config(pg_engine)
    head = _head(cfg)
    command.upgrade(cfg, "head")
    assert _db_version(pg_engine) == head
    _assert_head_artifacts(pg_engine)
    _assert_no_server_defaults(pg_engine)

    ids = _seed_legacy_rows(pg_engine)
    # 新列没有 server_default：历史行必须保持 NULL，不得被伪装成已确认。
    assert _legacy_value(pg_engine, "kb_documents", ids["doc"], "review_status") is None
    assert _legacy_value(pg_engine, "facts", ids["fact"], "confirmed_by") is None
    assert _legacy_value(pg_engine, "facts", ids["fact"], "confirmed_at") is None
    assert _legacy_value(pg_engine, "facts", ids["fact"], "value") == "老口径"

    try:
        command.downgrade(cfg, REV_0014)
        assert _db_version(pg_engine) == REV_0014
        assert not (CONFIRMATION_COLUMNS & _schema(pg_engine, "facts")["columns"])
        assert DOCUMENT_COLUMNS <= _schema(pg_engine, "kb_documents")["columns"]
        assert _legacy_value(pg_engine, "facts", ids["fact"], "value") == "老口径"
        assert _legacy_value(pg_engine, "facts", ids["fact"], "key") == LEGACY_FACT_KEY

        command.downgrade(cfg, REV_0013)
        assert _db_version(pg_engine) == REV_0013
        assert not (DUP_TABLES & _tables(pg_engine))
        assert not (DOCUMENT_COLUMNS & _schema(pg_engine, "kb_documents")["columns"])
        assert "kb_documents" in _tables(pg_engine)  # 0005 建的历史表，不碰。
        assert _legacy_value(pg_engine, "kb_documents", ids["doc"], "source") == (
            "rc12-legacy.pdf"
        )
        assert _legacy_value(pg_engine, "kb_documents", ids["doc"], "status") == "ready"

        command.downgrade(cfg, PARENT_0012)
        assert _db_version(pg_engine) == PARENT_0012
        assert "chapter_plans" not in _tables(pg_engine)
        assert _legacy_value(pg_engine, "projects", ids["project"], "name") == (
            "rc12 迁移往返项目"
        )
        assert _legacy_value(pg_engine, "facts", ids["fact"], "value") == "老口径"

        command.upgrade(cfg, "head")
        assert _db_version(pg_engine) == head
        with pg_engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == head
        _assert_head_artifacts(pg_engine)
        _assert_no_server_defaults(pg_engine)
        assert _legacy_value(pg_engine, "kb_documents", ids["doc"], "review_status") is None
        assert _legacy_value(pg_engine, "kb_documents", ids["doc"], "source") == (
            "rc12-legacy.pdf"
        )
        assert _legacy_value(pg_engine, "facts", ids["fact"], "confirmed_by") is None
        assert _legacy_value(pg_engine, "facts", ids["fact"], "confirmed_at") is None
        assert _legacy_value(pg_engine, "facts", ids["fact"], "value") == "老口径"
        assert _legacy_value(pg_engine, "projects", ids["project"], "name") == (
            "rc12 迁移往返项目"
        )
    finally:
        # 任何断言失败也要把专用测试库恢复到 head，避免污染后续用例。
        command.upgrade(cfg, "head")


# ---------------------------------------------------------------------------
# 2) RC-05 并发修复在 PG 上的真实并发验证
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("round_no", range(CLAIM_ROUNDS))
def test_claim_next_gives_every_worker_a_distinct_job_on_pg(pg_engine, round_no):
    """N 个 worker 同时认领 N 个排队任务：每个任务恰好一个 owner，无重复认领。"""

    with session_scope() as db:
        for i in range(CLAIM_WORKERS):
            db.add(Job(kind="agent", status="queued", priority=1, payload={"i": i}))
        db.flush()
        queued_ids = set(
            db.scalars(select(Job.id).where(Job.status == "queued"))
        )

    barrier = threading.Barrier(CLAIM_WORKERS)
    claimed: list[str | None] = []
    errors: list[BaseException] = []

    def worker():
        try:
            barrier.wait(timeout=30)
            job = jobqueue.claim_next()
            claimed.append(job.id if job is not None else None)
        except Exception as exc:  # noqa: BLE001 - 保留线程内真实失败
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, name=f"rc12-claim-{i}")
        for i in range(CLAIM_WORKERS)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "claim_next worker thread did not finish"
    assert not errors, f"unexpected thread errors: {errors!r}"

    assert len(claimed) == CLAIM_WORKERS
    assert None not in claimed
    assert len(set(claimed)) == CLAIM_WORKERS  # 任一重复认领都会在这里失败。
    assert set(claimed) <= queued_ids

    with session_scope() as db:
        rows = db.scalars(select(Job).where(Job.id.in_(set(claimed)))).all()
        assert len(rows) == CLAIM_WORKERS
        assert all(row.status == "running" and row.attempts == 1 for row in rows)
        tokens = {row.claim_token for row in rows}
        assert None not in tokens and len(tokens) == CLAIM_WORKERS


def test_claim_next_skips_a_row_locked_by_another_transaction_on_pg(pg_engine):
    """真实行锁场景：首行被别的事务 FOR UPDATE 锁住时，claim_next 必须跳过它。"""

    with session_scope() as db:
        first = Job(kind="agent", status="queued", priority=0, payload={"order": 0})
        second = Job(kind="agent", status="queued", priority=0, payload={"order": 1})
        db.add_all([first, second])
        db.flush()
        first_id, second_id = first.id, second.id

    claimed: dict[str, object] = {}
    errors: list[BaseException] = []

    def claim():
        try:
            job = jobqueue.claim_next()
            claimed["id"] = job.id if job is not None else None
        except Exception as exc:  # noqa: BLE001 - 保留线程内真实失败
            errors.append(exc)

    thread = threading.Thread(target=claim, name="rc12-claim-skip-locked")
    # 持有 first 行的行锁直到 claim 线程结束；不是 Python 锁，是 PG 行锁。
    with session_scope() as holder:
        holder.execute(
            text("SELECT id FROM jobs WHERE id = :id FOR UPDATE"), {"id": first_id}
        )
        thread.start()
        thread.join(timeout=20)
        assert not thread.is_alive(), "claim_next blocked on a locked row (SKIP LOCKED missing)"

    assert not errors, f"unexpected thread errors: {errors!r}"
    assert claimed["id"] == second_id
    with session_scope() as db:
        locked = db.get(Job, first_id)
        assert locked.status == "queued"
        assert locked.attempts == 0
        assert db.get(Job, second_id).status == "running"


def test_claim_statement_emits_skip_locked_on_pg_only(pg_engine):
    """编译产物证明 claim 语句在 PG 开 FOR UPDATE SKIP LOCKED，SQLite 不开。"""

    from sqlalchemy.dialects import postgresql, sqlite

    now = utc_now()
    statement = jobqueue._claim_statement(
        now, now + timedelta(seconds=30), "rc12-token", skip_locked=True
    )
    pg_sql = str(statement.compile(dialect=postgresql.dialect()))
    sq_sql = str(statement.compile(dialect=sqlite.dialect()))
    assert "FOR UPDATE" in pg_sql
    assert "SKIP LOCKED" in pg_sql
    assert "RETURNING" in pg_sql
    assert "SKIP LOCKED" not in sq_sql
    # 当前引擎确为 PG，claim_next() 才会走 skip_locked=True 分支。
    with session_scope() as db:
        assert db.get_bind().dialect.name == "postgresql"


def _reaper_attempt(job_id: str, cutoff):
    with session_scope() as db:
        return jobqueue._requeue(
            db, db.get(Job, job_id), "rc12 reaper", lease_expired_before=cutoff
        )


@pytest.mark.parametrize("round_no", range(REAPER_ROUNDS))
def test_two_reapers_cannot_double_requeue_on_pg(pg_engine, round_no):
    """两个 reaper 同时回收同一过期 Job：只能有一个产生 queued 事件。"""

    job_id, _, _ = _seed_running_job(lease_offset=-10)
    cutoff = utc_now()
    a, b = _pair(
        lambda: _reaper_attempt(job_id, cutoff),
        lambda: _reaper_attempt(job_id, cutoff),
    )
    assert sorted([a, b]) == ["lost", "requeued"]
    state = _job_state(job_id)
    assert state["status"] == "queued"
    assert state["attempts"] == 0
    assert state["events"].count("queued") == 1
    assert "failed" not in state["events"]


@pytest.mark.parametrize("round_no", range(REAPER_ROUNDS))
def test_two_reapers_cannot_double_fail_charged_job_on_pg(pg_engine, round_no):
    """到达重试上限的计费 Job：两个 reaper 只能一个判定失败并退款一次。"""

    job_id, _, user_id = _seed_running_job(
        attempts=3, lease_offset=-10, charged=6, chapters=3, done=1
    )
    cutoff = utc_now()
    a, b = _pair(
        lambda: _reaper_attempt(job_id, cutoff),
        lambda: _reaper_attempt(job_id, cutoff),
    )
    assert sorted([a, b]) == ["failed", "lost"]
    state = _job_state(job_id)
    assert state["status"] == "failed"
    assert state["refund_claimed"] is True
    assert state["events"].count("failed") == 1
    # charged=6 / chapters=3 / done=1 => earned 2, refund 4，余额 100-6+4=98。
    assert _balance(user_id) == 98
    assert _refunds(user_id) == [4]


def _refund(job_id: str) -> int:
    with session_scope() as db:
        return jobqueue.refund_charged_job(db, db.get(Job, job_id))


@pytest.mark.parametrize("round_no", range(REAPER_ROUNDS))
def test_two_refund_claims_pay_charged_job_once_on_pg(pg_engine, round_no):
    """Job 账本退款的条件 UPDATE 在 PG 上只有一个赢家，绝不双退。"""

    job_id, _, user_id = _seed_running_job(charged=6, chapters=3, done=1)
    a, b = _pair(lambda: _refund(job_id), lambda: _refund(job_id))
    assert sorted([a, b]) == [0, 4]
    assert _balance(user_id) == 98
    assert _refunds(user_id) == [4]
    assert _job_state(job_id)["refund_claimed"] is True


def _settle(marker_id: str, user_id: str, project_id: str) -> int:
    with session_scope() as db:
        return jobqueue.settle_pending_charge(
            db, marker_id, db.get(User, user_id), project_id, 3, 0
        )


def _reconcile() -> int:
    with session_scope() as db:
        return jobqueue.reconcile_pending_charges(older_than_seconds=0, db=db)


@pytest.mark.parametrize("round_no", range(OWNER_ROUNDS))
def test_marker_settle_vs_reconcile_has_one_owner_on_pg(pg_engine, round_no):
    """marker 结算与 TTL 对账并发：同一 marker 只能退一次，Job 归属标志同步。"""

    job_id, marker_id, user_id, project_id = _seed_charge_pair(done=0)
    paid, settled = _pair(
        lambda: _settle(marker_id, user_id, project_id),
        _reconcile,
    )
    assert (paid, settled) in ((6, 0), (0, 1))
    assert _balance(user_id) == 100
    assert _refunds(user_id) == [6]
    with session_scope() as db:
        marker = db.get(PendingCharge, marker_id)
        assert marker.status == "refunded"
        assert marker.refunded_credits == 6
        state = _job_state(job_id)
        assert state["refund_claimed"] is True
        assert state["refund_source"] == "marker"


@pytest.mark.parametrize("round_no", range(5))
def test_marker_settle_then_job_refund_single_owner_on_pg(pg_engine, round_no):
    """marker 先退款后，迟到的 Job 失败路径不得再次付款（单一归属）。"""

    job_id, marker_id, user_id, project_id = _seed_charge_pair(done=0)
    assert _settle(marker_id, user_id, project_id) == 6
    assert _refund(job_id) == 0
    assert _balance(user_id) == 100
    assert _refunds(user_id) == [6]
    assert _job_state(job_id)["refund_source"] == "marker"


@pytest.mark.parametrize("round_no", range(5))
def test_job_refund_then_reconcile_single_owner_on_pg(pg_engine, round_no):
    """Job 先退款后，TTL 对账不得把同一个 marker 再退一遍（单一归属）。"""

    job_id, marker_id, user_id, _ = _seed_charge_pair(done=0)
    assert _refund(job_id) == 6
    assert _reconcile() == 0
    assert _balance(user_id) == 100
    assert _refunds(user_id) == [6]
    with session_scope() as db:
        assert db.get(PendingCharge, marker_id).status == "cleared"
    assert _job_state(job_id)["refund_source"] == "job"


def _mark(job_id: str, source: str) -> bool:
    with session_scope() as db:
        return jobqueue.mark_job_refund_settled(db, job_id, source=source)


@pytest.mark.parametrize("round_no", range(MARK_ROUNDS))
def test_mark_job_refund_settled_has_single_owner_on_pg(pg_engine, round_no):
    """统一归属标志的条件 UPDATE 在 PG 上只有一个赢家。"""

    job_id = str(uuid.uuid4())
    with session_scope() as db:
        db.add(Job(id=job_id, kind="agent", status="running", payload={}))
    a, b = _pair(
        lambda: _mark(job_id, "marker"),
        lambda: _mark(job_id, "job"),
    )
    assert sorted([a, b]) == [False, True]
    state = _job_state(job_id)
    assert state["refund_claimed"] is True
    assert state["refund_source"] in {"marker", "job"}


def _seed_heartbeat_job() -> tuple[str, object]:
    job_id = str(uuid.uuid4())
    with session_scope() as db:
        lease_until = utc_now() + timedelta(seconds=30)
        job = Job(
            id=job_id,
            kind="agent",
            status="running",
            claim_token="rc12-hb-token",
            worker_id=jobqueue._worker_id,
            claimed_at=utc_now(),
            lease_expires_at=lease_until,
            heartbeat_at=utc_now(),
            payload={},
        )
        db.add(job)
    return job_id, lease_until


def test_heartbeat_aborts_immediately_when_lease_is_stolen_on_pg(pg_engine, monkeypatch):
    """真实条件 UPDATE 返回 0 行时立刻 abort；不用 mock、不用 Python 锁。"""

    job_id, lease_before = _seed_heartbeat_job()
    # reaper 风格：真实 UPDATE 清掉 token 并把 Job 收回队列。
    with session_scope() as db:
        db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(claim_token=None, status="queued")
        )

    monkeypatch.setattr(jobqueue, "_heartbeat_seconds", lambda: 0.01)
    stop = threading.Event()
    abort = threading.Event()
    thread = threading.Thread(
        target=jobqueue._heartbeat,
        args=(job_id, stop, "rc12-hb-token", abort),
        name="rc12-heartbeat-stolen",
    )
    thread.start()
    deadline = time.monotonic() + 3
    while not abort.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert abort.is_set()
    with session_scope() as db:
        row = db.get(Job, job_id)
        assert row.status == "queued"
        assert row.lease_expires_at == lease_before  # 旧 owner 没能续租复活。


def test_heartbeat_three_strikes_then_clears_on_success_on_pg(pg_engine, monkeypatch):
    """两次瞬态错误不 abort，第三次走真实 PG 条件 UPDATE 成功续租并清零计数。"""

    job_id, lease_before = _seed_heartbeat_job()
    real_renew = jobqueue._renew_lease_once
    calls = {"n": 0}

    def flaky(job_id_arg, token):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("rc12 transient heartbeat failure")
        return real_renew(job_id_arg, token)  # 真实 PG 条件 UPDATE

    monkeypatch.setattr(jobqueue, "_heartbeat_seconds", lambda: 0.01)
    monkeypatch.setattr(jobqueue, "_renew_lease_once", flaky)
    stop = threading.Event()
    abort = threading.Event()
    thread = threading.Thread(
        target=jobqueue._heartbeat,
        args=(job_id, stop, "rc12-hb-token", abort),
        name="rc12-heartbeat-flaky",
    )
    thread.start()
    deadline = time.monotonic() + 3
    while calls["n"] < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert calls["n"] >= 3
    assert not abort.is_set()
    with session_scope() as db:
        assert db.get(Job, job_id).lease_expires_at > lease_before

    # 连续三次失败才 abort（异常只在循环边界注入，条件 UPDATE 本身不 mock）。
    persistent = {"n": 0}

    def always_fail(job_id_arg, token):
        persistent["n"] += 1
        raise RuntimeError("rc12 persistent heartbeat failure")

    monkeypatch.setattr(jobqueue, "_renew_lease_once", always_fail)
    stop2 = threading.Event()
    abort2 = threading.Event()
    thread2 = threading.Thread(
        target=jobqueue._heartbeat,
        args=(job_id, stop2, "rc12-hb-token", abort2),
        name="rc12-heartbeat-three-strikes",
    )
    thread2.start()
    deadline = time.monotonic() + 3
    while not abort2.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    stop2.set()
    thread2.join(timeout=5)
    assert not thread2.is_alive()
    assert abort2.is_set()
    assert persistent["n"] >= 3
