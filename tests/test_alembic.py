"""Alembic 基线：老库收敛后记账、幂等、连正确的库。

create_all + _migrate_columns 仍是历史 schema 的收敛手段（baseline），
Alembic 从 0001_baseline 开始为**后续**变更记账。本文件守护这条边界。
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import inspect, text

from bidcraft.config import settings
from bidcraft.db import get_engine, init_db, reset_engine_for_tests

#: 当前 alembic head（新增 revision 时同步这里）。
HEAD = "0014_kb_asset_metadata"


def _version() -> list[str]:
    with get_engine().connect() as connection:
        rows = connection.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    return [row[0] for row in rows]


def test_fresh_db_is_stamped_to_baseline(db):
    assert _version() == [HEAD]


def test_repeated_init_is_idempotent(db):
    init_db()
    init_db()
    assert _version() == [HEAD]


def test_legacy_db_is_converged_then_stamped(tmp_path, monkeypatch):
    home = tmp_path / ".bidcraft"
    home.mkdir(parents=True)
    monkeypatch.setattr(settings, "home", home)
    reset_engine_for_tests(f"sqlite+pysqlite:///{home / 'bidcraft.db'}")
    try:
        # 老式 kb_chunks：只有 id/text，缺向量三列
        with sqlite3.connect(home / "bidcraft.db") as conn:
            conn.execute(
                "CREATE TABLE kb_chunks (id VARCHAR(36) PRIMARY KEY, text TEXT)"
            )
            conn.execute("INSERT INTO kb_chunks (id, text) VALUES ('old-1', 'legacy')")

        init_db()

        with get_engine().connect() as connection:
            cols = {column["name"] for column in inspect(connection).get_columns("kb_chunks")}
        assert {"embedding", "embed_key", "embed_dim"} <= cols
        assert _version() == [HEAD]
    finally:
        # 回到一个可用的文件库：内存库每连接独立，后续直接 import api/app 的
        # 测试会在 init_db() 里让 Alembic 连到空库而报 NoSuchTableError。
        scratch = tmp_path / ".bidcraft" / "after.sqlite"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        reset_engine_for_tests(f"sqlite+pysqlite:///{scratch}")
        init_db()


def test_batch2_downgrade_drops_indexes_before_columns(tmp_path, monkeypatch):
    """0005 的 downgrade 必须先删索引再删列（SQLite 不允许删被索引引用的列）。"""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from bidcraft.migrations import __file__ as migrations_init

    home = tmp_path / ".bidcraft"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "home", home)
    db_file = home / "dgrade.sqlite"
    reset_engine_for_tests(f"sqlite+pysqlite:///{db_file}")
    try:
        init_db()
        engine = get_engine()

        def table_set():
            return set(inspect(engine).get_table_names())

        added = {
            "enterprises", "enterprise_members", "kb_documents",
            "similarity_reports", "similarity_hits", "commercial_docs",
        }
        assert added <= table_set()

        cfg = Config()
        cfg.set_main_option("script_location", str(Path(migrations_init).parent))
        cfg.set_main_option(
            "sqlalchemy.url",
            engine.url.render_as_string(hide_password=False).replace("%", "%%"),
        )
        # downgrade 到上一版：索引与列都必须干净移除，且可重复升级回来
        command.downgrade(cfg, "0004_claim_token")
        assert not (added & table_set())
        cols = {c["name"] for c in inspect(engine).get_columns("kb_chunks")}
        assert not ({"document_id", "page_no", "heading", "chunk_index", "hash"} & cols)
        command.upgrade(cfg, "head")
        assert added <= table_set()
        assert _version() == [HEAD]
    finally:
        # 回到一个可用的文件库：内存库每连接独立，后续直接 import api/app 的
        # 测试会在 init_db() 里让 Alembic 连到空库而报 NoSuchTableError。
        scratch = tmp_path / ".bidcraft" / "after.sqlite"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        reset_engine_for_tests(f"sqlite+pysqlite:///{scratch}")
        init_db()


def test_migrations_follow_runtime_home(tmp_path, monkeypatch):
    """迁移必须连运行时注入的库，而不是任何配置文件里的固定路径。"""
    home = tmp_path / ".bidcraft"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "home", home)
    target = home / "custom.sqlite"
    reset_engine_for_tests(f"sqlite+pysqlite:///{target}")
    try:
        init_db()
        assert target.exists()
        with sqlite3.connect(target) as conn:
            assert conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchall() == [(HEAD,)]
    finally:
        # 回到一个可用的文件库：内存库每连接独立，后续直接 import api/app 的
        # 测试会在 init_db() 里让 Alembic 连到空库而报 NoSuchTableError。
        scratch = tmp_path / ".bidcraft" / "after.sqlite"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        reset_engine_for_tests(f"sqlite+pysqlite:///{scratch}")
        init_db()


def test_job_queue_indexes_created_and_reversible(db):
    """0008 建立队列热路径索引，降级后必须干净移除。"""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from bidcraft.migrations import __file__ as migrations_init

    engine = get_engine()
    expected = {"ix_jobs_status", "ix_jobs_status_priority_created"}

    def indexes():
        with engine.connect() as connection:
            return {idx["name"] for idx in inspect(connection).get_indexes("jobs")}

    assert expected <= indexes()
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(migrations_init).parent))
    cfg.set_main_option(
        "sqlalchemy.url",
        engine.url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    command.downgrade(cfg, "0007_yibiao_batch4")
    assert not (expected & indexes())
    command.upgrade(cfg, "head")
    assert expected <= indexes()
    assert _version() == [HEAD]


def test_v028_parity_columns_and_table_are_reversible(db):
    """0009 补齐的列/索引/表必须可降级再升级（PG 升级路径的核心保障）。"""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from bidcraft.migrations import __file__ as migrations_init

    engine = get_engine()
    plugin_cols = {
        "fail_streak", "auto_disabled", "mem_limit_mb", "cpu_seconds",
        "scope", "owner_id", "allow_private_net",
    }
    requirement_cols = {
        "page", "clause_no",
        "response_verdict", "response_verdict_label", "response_verdict_reason",
    }

    def columns(table: str) -> set[str]:
        return {column["name"] for column in inspect(engine).get_columns(table)}

    assert plugin_cols <= columns("plugins")
    assert requirement_cols <= columns("requirements")
    assert "owner_id" in columns("jobs")
    assert "pending_charges" in set(inspect(engine).get_table_names())

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(migrations_init).parent))
    cfg.set_main_option(
        "sqlalchemy.url",
        engine.url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    command.downgrade(cfg, "0008_job_queue_indexes")
    assert not (plugin_cols & columns("plugins"))
    assert not (requirement_cols & columns("requirements"))
    assert "owner_id" not in columns("jobs")
    assert "pending_charges" not in set(inspect(engine).get_table_names())

    command.upgrade(cfg, "head")
    assert plugin_cols <= columns("plugins")
    assert requirement_cols <= columns("requirements")
    assert "owner_id" in columns("jobs")
    assert "pending_charges" in set(inspect(engine).get_table_names())
    assert _version() == [HEAD]


def test_0010_backfills_owner_from_user_id_and_project(tmp_path, monkeypatch):
    """0009 只认 payload['owner_id']；0010 按运行时同款 COALESCE 链补全老行。"""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from bidcraft.migrations import __file__ as migrations_init
    from bidcraft.models import Project

    home = tmp_path / ".bidcraft"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "home", home)
    db_file = home / "owner-backfill.sqlite"
    reset_engine_for_tests(f"sqlite+pysqlite:///{db_file}")
    try:
        init_db()
        engine = get_engine()
        cfg = Config()
        cfg.set_main_option("script_location", str(Path(migrations_init).parent))
        cfg.set_main_option(
            "sqlalchemy.url",
            engine.url.render_as_string(hide_password=False).replace("%", "%%"),
        )
        command.downgrade(cfg, "0008_job_queue_indexes")

        # 造两条 0009 回填不到的老行：只有 user_id / 只有 project_id。
        with engine.begin() as connection:
            connection.execute(
                Project.__table__.insert().values(
                    id="p-legacy", name="老项目", owner_id="u-from-project"
                )
            )
            # 显式列名写入"老行"：此时库还停在 0008，模型里新增的
            # refund_claimed 列并不存在，不能用 Job.__table__.insert()。
            legacy_job_sql = (
                "INSERT INTO jobs (id, project_id, kind, status, progress, message, "
                "payload, result, priority, attempts, max_attempts, created_at, updated_at) "
                "VALUES (:id, :project_id, 'generate', 'queued', 0, '', :payload, '{}', "
                "1, 0, 3, '2020-01-01 00:00:00', '2020-01-01 00:00:00')"
            )
            connection.execute(
                text(legacy_job_sql),
                {
                    "id": "legacy-user-id",
                    "project_id": None,
                    "payload": '{"user_id": "u-from-payload"}',
                },
            )
            connection.execute(
                text(legacy_job_sql),
                {
                    "id": "legacy-project-owner",
                    "project_id": "p-legacy",
                    "payload": "{}",
                },
            )

        command.upgrade(cfg, "head")

        with sqlite3.connect(db_file) as conn:
            owners = dict(conn.execute("SELECT id, owner_id FROM jobs").fetchall())
            version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert owners["legacy-user-id"] == "u-from-payload"
        assert owners["legacy-project-owner"] == "u-from-project"
        assert version == HEAD
    finally:
        # 回到一个可用的文件库：内存库每连接独立，后续直接 import api/app 的
        # 测试会在 init_db() 里让 Alembic 连到空库而报 NoSuchTableError。
        scratch = tmp_path / ".bidcraft" / "after.sqlite"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        reset_engine_for_tests(f"sqlite+pysqlite:///{scratch}")
        init_db()


def test_0011_stage3_accounting_columns_are_reversible(tmp_path, monkeypatch):
    """0011 的两列必须可降级移除、再升级恢复，且重复执行依然幂等。"""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from bidcraft.migrations import __file__ as migrations_init
    from bidcraft.models import Job, PendingCharge

    home = tmp_path / ".bidcraft"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "home", home)
    db_file = home / "stage3.sqlite"
    reset_engine_for_tests(f"sqlite+pysqlite:///{db_file}")
    try:
        init_db()
        engine = get_engine()

        def columns(table: str) -> set[str]:
            with engine.connect() as connection:
                return {column["name"] for column in inspect(connection).get_columns(table)}

        # create_all 已按当前模型建出列；此时 revision 的 upgrade 必须是空操作。
        assert "sections_at_charge" in columns("pending_charges")
        assert "refund_claimed" in columns("jobs")

        cfg = Config()
        cfg.set_main_option("script_location", str(Path(migrations_init).parent))
        cfg.set_main_option(
            "sqlalchemy.url",
            engine.url.render_as_string(hide_password=False).replace("%", "%%"),
        )

        command.downgrade(cfg, "0010_batch1_billing_reliability")
        assert "sections_at_charge" not in columns("pending_charges")
        assert "refund_claimed" not in columns("jobs")

        command.upgrade(cfg, "head")
        assert "sections_at_charge" in columns("pending_charges")
        assert "refund_claimed" in columns("jobs")

        with engine.begin() as connection:
            connection.execute(
                Job.__table__.insert().values(
                    id="stage3-job", kind="generate", status="queued"
                )
            )
            connection.execute(
                PendingCharge.__table__.insert().values(
                    id="stage3-marker", user_id="u", project_id="p", credits=1, chapters=1
                )
            )

        with sqlite3.connect(db_file) as conn:
            assert conn.execute(
                "SELECT refund_claimed FROM jobs WHERE id = 'stage3-job'"
            ).fetchone() == (0,)
            assert conn.execute(
                "SELECT sections_at_charge FROM pending_charges WHERE id = 'stage3-marker'"
            ).fetchone() == (0,)

        # 再次 init_db + upgrade 都不得因列已存在而报错。
        init_db()
        command.upgrade(cfg, "head")
        assert "sections_at_charge" in columns("pending_charges")
        assert "refund_claimed" in columns("jobs")
        assert _version() == [HEAD]
    finally:
        # 回到一个可用的文件库：内存库每连接独立，后续直接 import api/app 的
        # 测试会在 init_db() 里让 Alembic 连到空库而报 NoSuchTableError。
        scratch = tmp_path / ".bidcraft" / "after.sqlite"
        scratch.parent.mkdir(parents=True, exist_ok=True)
        reset_engine_for_tests(f"sqlite+pysqlite:///{scratch}")
        init_db()


def test_0013_chapter_planning_table_is_reversible(db):
    """0013 的规划表/索引/唯一约束必须可降级再升级，且重复执行依然幂等。"""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from bidcraft.migrations import __file__ as migrations_init

    engine = get_engine()
    expected_columns = {
        "id", "project_id", "node_id", "package_id", "version", "status",
        "objective", "raw_response", "model_id", "prompt_version",
        "user_instruction", "created_by", "confirmed_by", "created_at",
        "updated_at", "confirmed_at", "used_at", "score_points",
        "requirements", "conditions", "challenges", "strategy", "measures",
        "knowledge_refs", "fact_refs", "planned_tables", "planned_diagrams",
        "forbidden_assumptions", "quality_criteria", "user_adjustments",
        "validation_errors",
    }
    expected_indexes = {
        "ix_chapter_plans_project_node_status",
        "ix_chapter_plans_project_package",
    }

    def columns() -> set[str]:
        with engine.connect() as connection:
            return {c["name"] for c in inspect(connection).get_columns("chapter_plans")}

    def indexes() -> set[str]:
        with engine.connect() as connection:
            return {i["name"] for i in inspect(connection).get_indexes("chapter_plans") if i.get("name")}

    assert "chapter_plans" in set(inspect(engine).get_table_names())
    assert expected_columns <= columns()
    assert expected_indexes <= indexes()
    with engine.connect() as connection:
        uniques = {
            item["name"]
            for item in inspect(connection).get_unique_constraints("chapter_plans")
            if item.get("name")
        }
    assert "uq_chapter_plan_project_node_version" in uniques

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(migrations_init).parent))
    cfg.set_main_option(
        "sqlalchemy.url",
        engine.url.render_as_string(hide_password=False).replace("%", "%%"),
    )

    command.downgrade(cfg, "0012_generation_lease")
    assert "chapter_plans" not in set(inspect(engine).get_table_names())

    command.upgrade(cfg, "head")
    assert "chapter_plans" in set(inspect(engine).get_table_names())
    assert expected_columns <= columns()
    assert expected_indexes <= indexes()

    # 幂等：create_all 已经建表后重复 upgrade 不得报错。
    init_db()
    command.upgrade(cfg, "head")
    assert expected_columns <= columns()
    assert _version() == [HEAD]


def test_0014_kb_asset_metadata_is_reversible_and_idempotent(db):
    """0014 的文档列/查重表必须可降级再升级，且历史行 review_status 保持 NULL。"""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from bidcraft.db import session_scope
    from bidcraft.migrations import __file__ as migrations_init
    from bidcraft.models import KbDocument, KnowledgeBase

    engine = get_engine()
    asset_columns = {
        "structured", "review_status", "reviewed_by", "reviewed_at",
        "published_at", "rejected_reason", "source_file_id",
    }
    group_columns = {
        "id", "kb_id", "fingerprint", "similarity", "status", "reason",
        "created_by", "created_at", "reviewed_by", "reviewed_at",
    }
    member_columns = {"id", "group_id", "document_id", "score", "role"}

    def columns(table: str) -> set[str]:
        with engine.connect() as connection:
            return {c["name"] for c in inspect(connection).get_columns(table)}

    def indexes(table: str) -> set[str]:
        with engine.connect() as connection:
            return {
                item["name"]
                for item in inspect(connection).get_indexes(table)
                if item.get("name")
            }

    def uniques(table: str) -> set[str]:
        with engine.connect() as connection:
            return {
                item["name"]
                for item in inspect(connection).get_unique_constraints(table)
                if item.get("name")
            }

    tables = set(inspect(engine).get_table_names())
    assert asset_columns <= columns("kb_documents")
    assert {"kb_asset_duplicate_groups", "kb_asset_duplicate_members"} <= tables
    assert group_columns <= columns("kb_asset_duplicate_groups")
    assert member_columns <= columns("kb_asset_duplicate_members")
    assert {"ix_kb_asset_duplicate_groups_kb_id", "ix_kb_asset_duplicate_groups_kb_status"} <= indexes(
        "kb_asset_duplicate_groups"
    )
    assert {"ix_kb_asset_duplicate_members_group_id", "ix_kb_asset_duplicate_members_document_id"} <= indexes(
        "kb_asset_duplicate_members"
    )
    assert "uq_kb_asset_duplicate_group" in uniques("kb_asset_duplicate_groups")
    assert "uq_kb_asset_duplicate_member" in uniques("kb_asset_duplicate_members")

    # 历史行（未经资产抽取）的 review_status 必须是 NULL，而不是 server_default。
    with session_scope() as session:
        kb = KnowledgeBase(name="migration-asset-kb")
        session.add(kb)
        session.flush()
        session.add(KbDocument(kb_id=kb.id, source="legacy.pdf", status="ready"))
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT review_status FROM kb_documents WHERE source = 'legacy.pdf'")
        ).fetchone()
    assert row == (None,)

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(migrations_init).parent))
    cfg.set_main_option(
        "sqlalchemy.url",
        engine.url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    command.downgrade(cfg, "0013_chapter_planning")
    tables = set(inspect(engine).get_table_names())
    assert "kb_asset_duplicate_groups" not in tables
    assert "kb_asset_duplicate_members" not in tables
    assert not (asset_columns & columns("kb_documents"))

    command.upgrade(cfg, "head")
    tables = set(inspect(engine).get_table_names())
    assert {"kb_asset_duplicate_groups", "kb_asset_duplicate_members"} <= tables
    assert asset_columns <= columns("kb_documents")
    assert group_columns <= columns("kb_asset_duplicate_groups")
    assert member_columns <= columns("kb_asset_duplicate_members")

    # 幂等：create_all 已建列/表后，重复 init_db + upgrade 不得报错。
    init_db()
    command.upgrade(cfg, "head")
    assert asset_columns <= columns("kb_documents")
    assert {"kb_asset_duplicate_groups", "kb_asset_duplicate_members"} <= set(
        inspect(engine).get_table_names()
    )
    assert _version() == [HEAD]
