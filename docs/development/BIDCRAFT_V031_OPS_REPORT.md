# BidCraft v0.31.0 阶段 8：备份、发布与运维报告

基线：阶段 7（`2470340`）。本阶段不改动运行时代码，只补齐可执行的备份/恢复手册与发布前置检查。

## 1. 结论

新增 `docs/operations/BIDCRAFT_DATABASE_BACKUP_RESTORE.md`，把仓库已有的 `bidcraft backup`、`backup-verify`、`backup-prune`、恢复演练测试和容器探针串成一套可交接的运维流程。所有命令均不针对生产库做破坏性验证。

## 2. 交付内容（11 项核对）

| # | 项目 | 状态 | 证据 |
|---|---|---|---|
| 1 | 备份内容清单 | 完成 | 手册第 1 节：SQLite online backup + `quick_check`、PG `pg_dump`、`secret.key`、4 个数据目录、SHA-256 manifest |
| 2 | 容器命令 | 完成 | `docker exec bidcraft bidcraft backup / --list / --include-dir` |
| 3 | 完整性校验 | 完成 | `bidcraft backup-verify`，退出码 0/1/2 与校验范围明确 |
| 4 | 保留策略 | 完成 | `backup-prune 10 --dry-run` / 正式清理；升级前至少保留一份 |
| 5 | SQLite 恢复 | 完成 | 停服 → 保留副本 → 解压 → 覆盖 → 启动 → `doctor`/冒烟 |
| 6 | PostgreSQL 恢复 | 完成 | 临时库试导入 → 迁移验证 → 正式导入 → 冒烟 |
| 7 | secret.key 同代 | 完成 | 手册明确丢失/错配会导致 API Key 与 OAuth state 失效 |
| 8 | 迁移前置 | 完成 | 升级前必须备份+验证+临时库演练；Alembic 单 head 以仓库为准，不改历史 0001～0011 |
| 9 | 恢复演练 | 完成 | `BIDCRAFT_RESTORE_DRILL=1 pytest tests/test_fix3_backup_drill.py`（opt-in，临时 HOME） |
| 10 | 容器探针 | 完成 | `/health/live` liveness、`/health/ready` readiness；镜像内置 `postgresql-client` |
| 11 | 审计留痕 | 完成 | 备份路径/大小/SHA-256/操作人/目的与季度演练记录要求 |

## 3. 验证结果

| 检查 | 结果 |
|---|---|
| `pytest tests/test_fix3_backup_drill.py tests/test_fix2_runtime_backup.py tests/test_v030_stage8_deploy.py` | 10 passed / 1 skipped（无 PG URL，opt-in） |
| `BIDCRAFT_RESTORE_DRILL=1 pytest tests/test_fix3_backup_drill.py` | 1 passed |
| `ruff check bidcraft tests scripts` | 通过（本阶段无 Python 代码变更） |
| `git diff --check` | 通过 |

## 4. 边界与风险

- 本地环境无 PostgreSQL/Docker/LibreOffice，真实容器恢复与 PG 试导入在 CI/运维窗口验证。
- 本阶段未改运行时配置（遵循用户「D 排除 env_file」决定）。
- 回滚：删除/回退本报告即可；无迁移、无代码变更。
