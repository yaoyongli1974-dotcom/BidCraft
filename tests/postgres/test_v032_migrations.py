"""v0.32 Phase 7：真实 PostgreSQL 上的迁移往返（0013 / 0014 / 0015）。

opt-in：未设置 ``BIDCRAFT_TEST_PG_URL`` 时，``tests/postgres/conftest.py`` 的
autouse fixture 会干净跳过本文件所有用例；设置后只连专用测试库，全程执行真实
PostgreSQL DDL 并用 SQLAlchemy ``inspect`` / PG catalog 校验，不用 mock，也不把
SQLite 的动态类型结果当作 PostgreSQL 结论。

历史边界：0001-0012 都是历史 revision，本文件与迁移代码都不编辑、不重写它们。
降级只走到 0012_generation_lease（0013 的父版本），让 0001-0012 建立/保留的
projects、knowledge_bases 等旧表与数据原样留下；本轮只验证 v0.32 的 0013/0014/
0015 可完整来回。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

pytest.importorskip("psycopg")

# 目标列表只允许列出本轮关心的 revision；head 必须动态推导，不能硬编码。
REV_0013 = "0013_chapter_planning"
REV_0014 = "0014_kb_asset_metadata"
REV_0015 = "0015_fact_confirmation"
TARGETS = (REV_0013, REV_0014, REV_0015)
PARENTS = {
    REV_0015: REV_0014,
    REV_0014: REV_0013,
    REV_0013: "0012_generation_lease",  # 0001-0012 是历史，只读、不编辑。
}

CHAPTER_PLAN_COLUMNS = {
    "id", "project_id", "node_id", "package_id", "version", "status",
    "objective", "raw_response", "model_id", "prompt_version",
    "user_instruction", "created_by", "confirmed_by", "created_at",
    "updated_at", "confirmed_at", "used_at", "score_points", "requirements",
    "conditions", "challenges", "strategy", "measures", "knowledge_refs",
    "fact_refs", "planned_tables", "planned_diagrams", "forbidden_assumptions",
    "quality_criteria", "user_adjustments", "validation_errors",
}
CHAPTER_PLAN_INDEXES = {
    "ix_chapter_plans_project_node_status",
    "ix_chapter_plans_project_package",
    "ix_chapter_plans_project_id",
    "ix_chapter_plans_node_id",
    "ix_chapter_plans_status",
}
CHAPTER_PLAN_UNIQUES = {"uq_chapter_plan_project_node_version"}
DOCUMENT_COLUMNS = {
    "structured", "review_status", "reviewed_by", "reviewed_at",
    "published_at", "rejected_reason", "source_file_id",
}
GROUP_COLUMNS = {
    "id", "kb_id", "fingerprint", "similarity", "status", "reason",
    "created_by", "created_at", "reviewed_by", "reviewed_at",
}
GROUP_INDEXES = {
    "ix_kb_asset_duplicate_groups_kb_id",
    "ix_kb_asset_duplicate_groups_kb_status",
}
GROUP_UNIQUES = {"uq_kb_asset_duplicate_group"}
MEMBER_COLUMNS = {"id", "group_id", "document_id", "score", "role"}
MEMBER_INDEXES = {
    "ix_kb_asset_duplicate_members_group_id",
    "ix_kb_asset_duplicate_members_document_id",
}
MEMBER_UNIQUES = {"uq_kb_asset_duplicate_member"}
CONFIRMATION_COLUMNS = {"confirmed_by", "confirmed_at"}
DUP_TABLES = {"kb_asset_duplicate_groups", "kb_asset_duplicate_members"}


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
    """从 Alembic 脚本动态解析唯一 head，不写死版本号。"""

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


def _assert_head_schema(pg_engine) -> None:
    """0013/0014/0015 的表、列、索引、唯一约束必须在 head 全部存在。"""

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
    """历史文档/事实不得被 DDL 盖章成「已审核 / 已人工确认」。"""

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
    """只写基线列：这些行在 0012（历史）与 head 两种 schema 下都必须有效。"""

    # id 列是 VARCHAR(36)，直接用 uuid4 的字符串形式，不要再加前缀。
    ids = {name: str(uuid.uuid4()) for name in ("project", "kb", "doc", "fact")}
    # create_all 的 Python 默认值不会落成 PG server_default，NOT NULL 列必须显式给值。
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, name, package, status, buyer, meta, is_template, "
                "created_at, updated_at) VALUES (:id, :name, 'weakcurrent-cabling', 'intake', "
                "'', '{}', false, '2020-01-01 00:00:00', '2020-01-01 00:00:00')"
            ),
            {"id": ids["project"], "name": "v032 迁移往返项目"},
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
            {"id": ids["doc"], "kb": ids["kb"], "source": "v032-legacy.pdf"},
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
                "key": "v032-legacy-fact",
                "label": "历史事实",
                "value": "老口径",
            },
        )
    return ids


def _legacy_row(pg_engine, table: str, row_id: str, column: str):
    with pg_engine.connect() as connection:
        return connection.execute(
            text(f"SELECT {column} FROM {table} WHERE id = :id"), {"id": row_id}
        ).scalar_one_or_none()


def test_v032_target_revisions_chain_to_dynamic_head_on_pg(pg_engine):
    """目标 revision 链必须与脚本一致，且 DB 当前版本就是动态推导的 head。"""

    cfg = _alembic_config(pg_engine)
    script = _script_directory(cfg)
    head = _head(cfg)
    revisions = {item.revision for item in script.walk_revisions()}
    assert set(TARGETS) <= revisions
    assert head in revisions
    for revision, parent in PARENTS.items():
        assert script.get_revision(revision).down_revision == parent
    # 目标链顺序固定：从动态 head 往回走必须依次遇到 0015/0014/0013，
    # 但 head 本身不写死（将来新增 revision 时这里仍然成立）。
    revisions_in_order = [item.revision for item in script.walk_revisions()]
    assert [rev for rev in revisions_in_order if rev in TARGETS] == list(reversed(TARGETS))
    assert _db_version(pg_engine) == head
    with pg_engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == head


def test_v032_migrations_roundtrip_on_pg(pg_engine):
    """head 校验 → 逐步降到 0012 → 再升回 head；旧数据保留、v0.32 产物可逆。"""

    from alembic import command

    cfg = _alembic_config(pg_engine)
    head = _head(cfg)
    # 起点必须在 head（conftest 的 init_db 已 stamp，这里显式保证）。
    command.upgrade(cfg, "head")
    assert _db_version(pg_engine) == head
    _assert_head_schema(pg_engine)
    _assert_no_server_defaults(pg_engine)

    ids = _seed_legacy_rows(pg_engine)
    # 历史行的新列必须是 NULL：没有 server_default，不能把批量数据伪装成已确认。
    assert _legacy_row(pg_engine, "kb_documents", ids["doc"], "review_status") is None
    assert _legacy_row(pg_engine, "facts", ids["fact"], "confirmed_by") is None
    assert _legacy_row(pg_engine, "facts", ids["fact"], "confirmed_at") is None
    assert _legacy_row(pg_engine, "facts", ids["fact"], "value") == "老口径"

    try:
        # 0015 down：只移除确认两列，事实本身与来源列不受影响。
        command.downgrade(cfg, REV_0014)
        assert _db_version(pg_engine) == REV_0014
        assert not (CONFIRMATION_COLUMNS & _schema(pg_engine, "facts")["columns"])
        assert DOCUMENT_COLUMNS <= _schema(pg_engine, "kb_documents")["columns"]
        assert _legacy_row(pg_engine, "facts", ids["fact"], "value") == "老口径"
        assert _legacy_row(pg_engine, "facts", ids["fact"], "key") == "v032-legacy-fact"

        # 0014 down：查重候选表 + 文档 7 列全部消失；历史文档行仍在。
        command.downgrade(cfg, REV_0013)
        assert _db_version(pg_engine) == REV_0013
        assert not (DUP_TABLES & _tables(pg_engine))
        assert not (DOCUMENT_COLUMNS & _schema(pg_engine, "kb_documents")["columns"])
        assert "kb_documents" in _tables(pg_engine)  # 0005 建的历史表，不碰。
        assert _legacy_row(pg_engine, "kb_documents", ids["doc"], "source") == (
            "v032-legacy.pdf"
        )
        assert _legacy_row(pg_engine, "kb_documents", ids["doc"], "status") == "ready"

        # 0013 down：规划表（含索引与唯一约束）整表移除；历史项目/事实完好。
        # 停在这里，0001-0012 的历史 revision 与数据一律不降级、不编辑。
        command.downgrade(cfg, PARENTS[REV_0013])
        assert _db_version(pg_engine) == PARENTS[REV_0013]
        assert "chapter_plans" not in _tables(pg_engine)
        assert _legacy_row(pg_engine, "projects", ids["project"], "name") == (
            "v032 迁移往返项目"
        )
        assert _legacy_row(pg_engine, "facts", ids["fact"], "value") == "老口径"

        # 重新升到动态 head：DDL 必须真实重建，历史行的新列重新回到 NULL。
        command.upgrade(cfg, "head")
        assert _db_version(pg_engine) == head
        with pg_engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == head
        _assert_head_schema(pg_engine)
        _assert_no_server_defaults(pg_engine)
        assert _legacy_row(pg_engine, "kb_documents", ids["doc"], "review_status") is None
        assert _legacy_row(pg_engine, "kb_documents", ids["doc"], "source") == (
            "v032-legacy.pdf"
        )
        assert _legacy_row(pg_engine, "facts", ids["fact"], "confirmed_by") is None
        assert _legacy_row(pg_engine, "facts", ids["fact"], "confirmed_at") is None
        assert _legacy_row(pg_engine, "facts", ids["fact"], "value") == "老口径"
        assert _legacy_row(pg_engine, "projects", ids["project"], "name") == (
            "v032 迁移往返项目"
        )
    finally:
        # 任何断言失败也要把专用测试库恢复到 head，避免污染后续用例。
        command.upgrade(cfg, "head")
