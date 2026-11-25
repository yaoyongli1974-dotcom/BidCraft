# v0.31.3 阶段 5：数据库迁移与权限边界（真实 PostgreSQL）

- 分支：`fix/v0.31.3-production-readiness`（阶段 0 `20266e5`、1 `fcf66dd`、2 `3f1f325`、3 `f8f9469`、4 `41697e6` 后）
- 数据库：真实 PostgreSQL 16.2（pgserver，unix socket `/tmp/opencode/v0313-pgdata`，psycopg 3.3.9）
- 结论：**A PASS**；未修改生产代码，未修改历史迁移，未删测试。

## 1. 复现先行

按任务书要求先写测试、再判断是否改生产代码。新增 `tests/postgres/test_v0313_migration_permissions.py`，在真实 PG 上覆盖：

1. Alembic 全链路 `head → 0001_baseline → head` 往返：检查中途没有 `jobs.owner_id`、`jobs.refund_claimed`、`ix_jobs_status_priority_created`，没有 `pending_charges` / `generation_leases`；升回 head 后列、索引、表与业务数据（Project / Job）完整恢复，版本号等于动态推导出的单一 head。
2. 成员权限：有效 `editor` / `viewer` 可见项目；非法 `ProjectMember.role='owner'` 不授予可见性；`authorize_projects()` 批量过滤只返回可见项目；`viewer` 读可见但 `_require_write()` 映射为 HTTP 403。
3. finding 越权：`finding_for_project()` / `update_finding()` 对跨项目 finding 返回 `None`；HTTP `PATCH /projects/{A}/evaluation/findings/{B的finding}` 返回 404，finding 状态不变，无越权审计；`GET /projects/{B}` 返回 404。

首次运行 1 failed / 2 passed：测试把 HTTP 薄封装误写为领域 `Forbidden`，实际 `_require_write()` 正确映射为 `HTTPException(403)`。修正测试期望（检查 `status_code == 403`）后 3 passed。该失败不是生产缺陷，未降低断言、未改生产代码。

## 2. 验证命令与结果

```bash
.venv/bin/python -m ruff check bidcraft tests
BIDCRAFT_TEST_PG_URL="$(cat /tmp/opencode/v0313_pg_uri.txt)" \
  .venv/bin/python -m pytest -o addopts="" -q \
  tests/postgres/test_v0313_migration_permissions.py
# 3 passed, 1 warning in 4.11s

BIDCRAFT_TEST_PG_URL="$(cat /tmp/opencode/v0313_pg_uri.txt)" \
  .venv/bin/python -m pytest -o addopts="" -q \
  tests/postgres tests/test_postgres_integration.py \
  tests/test_v030_pg_isolation.py \
  tests/test_worker_fencing_atomicity.py \
  tests/test_jobqueue_race_conditions.py
# 901 passed, 1 skipped, 1 warning in 75.71s
```

`ruff check bidcraft tests`：All checks passed。全量 PG 套件相较阶段 3 的 894 passed 增至 901 passed（新增本阶段 3 用例 + 阶段 4 的 4 用例已在上一阶段计入，本次增量按实际命令结果为准）。

## 3. 失败与残余风险

- 无新发现的生产缺陷；阶段 1 已修复的 `refund_claimed = 0` PostgreSQL BOOLEAN 比较是本任务前序真实缺陷，已由 100 轮 PG 竞争回归锁定。
- 本地 pgserver 是真实 PostgreSQL 16.2 进程，但不是生产实例；CI 的真实 PostgreSQL 集成任务仍需在阶段 6 复核。
- 权限测试覆盖项目可见性、成员角色过滤、finding IDOR 与 HTTP 映射；插件作用域、工作流四 eyes、管理端用户 CRUD 的既有覆盖在 SQLite/CI 中，未在本阶段重复搬移到 PG。
- Alembic 全链路往返会临时改变测试库 schema；用例通过 `try/finally command.upgrade(cfg, 'head')` 恢复，且每用例前 autouse fixture 建立数据边界。

## 4. 回滚

仅新增测试文件与本报告；无生产代码、无迁移、无 schema 变化。回滚直接删除 `tests/postgres/test_v0313_migration_permissions.py` 与本报告即可。
