# BidCraft 数据库备份与恢复手册（v0.34.0）

适用：自托管实例（SQLite 默认 / PostgreSQL 可选）。所有命令在开发机或受控运维终端执行；**不得对生产库做破坏性验证**。

## 1. 备份内容

`bidcraft backup` 生成 `BIDCRAFT_HOME/backups/bidcraft-<UTC时间戳>.zip`，包含：

| 条目 | SQLite | PostgreSQL |
|---|---|---|
| 数据库快照 | `bidcraft.db`（SQLite online backup API + `PRAGMA quick_check`） | `dump.sql`（`pg_dump --no-owner --no-privileges`，口令经 `PGPASSWORD` 传递） |
| `secret.key` | 存在则包含（Fernet 凭据、API Key 与 OAuth state 依赖它） | 同左 |
| 数据目录 | `projects/`、`packages/`、`output/`、`docs/`（由 `BIDCRAFT_BACKUP_DIRS` 覆盖，可 `--include-dir` 追加） | 同左 |
| `manifest.json` | 版本、时间、后端、文件字节数与 SHA-256、目录清单 | 同左 |

`--include-dir` 只允许相对 `BIDCRAFT_HOME` 的路径，拒绝越界/符号链接；`backups/` 自身永远排除，防止递归膨胀。

## 2. 执行备份

```bash
# 容器部署（先停写入窗口，或确认业务低峰）
docker exec bidcraft bidcraft backup
docker exec bidcraft bidcraft backup --include-dir custom-data
docker exec bidcraft bidcraft backup --list
```

前置条件：

- PostgreSQL 必须安装 `postgresql-client`（镜像已内置 `pg_dump`/`psql`）。
- 迁移、大版本升级、重启、磁盘维护前**必须**先备份，再执行 `bidcraft backup-verify`，记录路径与结果。

## 3. 验证备份

```bash
docker exec bidcraft bidcraft backup-verify /data/backups/bidcraft-YYYYmmdd-HHMMSS.zip
docker exec bidcraft bidcraft backup-prune 10 --dry-run
docker exec bidcraft bidcraft backup-prune 10
```

校验内容：zip 可解压、manifest 可解析、数据库快照可打开（SQLite `quick_check` / PG dump 非空）、`data_files` 中每一项路径/字节数/SHA-256 一致。退出码：0=通过，1=校验失败，2=参数/文件错误。

`backup-verify` 只做结构级校验；真正的恢复演练用 `BIDCRAFT_RESTORE_DRILL=1` 跑 `tests/test_fix3_backup_drill.py`（opt-in，临时 HOME 恢复并验证 `secret.key` 可解密、ORM 可读）。

## 4. 恢复步骤

### 4.1 SQLite

1. 停服：`docker compose down`（确保无进程写库）。
2. 保留当前目录的副本（不要直接覆盖原数据）：`cp -a $BIDCRAFT_HOME $BIDCRAFT_HOME.before-restore`。
3. 解压备份到临时目录，核对 `manifest.json` 与 `backup-verify` 输出。
4. 将 `bidcraft.db`、`secret.key`（若备份含）以及 `projects/`、`packages/`、`output/`、`docs/` 覆盖回 `BIDCRAFT_HOME`。
5. 启动：`docker compose up -d`，执行 `bidcraft doctor`、`bidcraft version`，登录并做一条项目冒烟（打开项目 → 运行检查 → 导出一份 DOCX）。
6. 记录恢复耗时、校验结果、操作人和快照路径。

**关键**：`secret.key` 必须与数据库快照同代；丢失会导致 API Key 无法解密、WeChat OAuth state 校验失败。

### 4.2 PostgreSQL

1. 停服并确认没有写入连接。
2. 解压 `dump.sql`；在临时库做试导入：`createdb bidcraft_restore_test && psql -d bidcraft_restore_test -f dump.sql`。
3. 核对表数量/行数与 `manifest.json`，在**同后端**的临时库验证迁移链（不碰生产库）：`BIDCRAFT_HOME=<临时目录> BIDCRAFT_DB_URL=<临时库 URL> bidcraft doctor`（`doctor` 会调用 `init_db()` 执行 Alembic 升级）。SQLite 临时库示例 `sqlite:///<临时目录>/verify.db`；PostgreSQL 则使用第 2 步导入的 `postgresql+psycopg://user:pass@host:5432/bidcraft_restore_test`，不要用 SQLite 冒充 PG 验证。
4. 正式恢复：`pg_restore`/`psql` 导入目标库；放回 `secret.key` 与数据目录。
5. 启动后按 4.1 第 5～6 步冒烟并记录。

## 5. 迁移与版本

- Alembic head **动态计算**，不按版本号硬编码：`bidcraft/migrations/versions/` 中所有 `revision` 减去所有 `down_revision` 必须恰好剩一个 head。CI 的 `python-lint` job 已用 AST 扫描执行该断言；本地可用下面的命令读取当前 head：
  ```bash
  uv run python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; cfg = Config(); cfg.set_main_option('script_location', 'bidcraft/migrations'); print(ScriptDirectory.from_config(cfg).get_heads())"
  ```
  历史迁移只增不改；每个新版本以仓库当前 head 为准，旧手册中写死的 head/revision 号不得当作依据。
- 升级前：`bidcraft backup` → `bidcraft backup-verify` → 在临时库跑升级 → 确认成功后再在生产库执行。
- 回滚：仓库迁移均提供 `downgrade`，但 SQLite 下部分历史迁移依赖 `inspect(bind)`，离线 SQL 模式不适用；回滚同样先备份。
- 容器镜像内置 `postgresql-client`，`/health/live` 用于 liveness，`/health/ready` 用于 readiness（依赖 DB/模型/web_dist）。

## 6. 留痕与审计

- 备份后保存输出路径、大小、SHA-256、操作人、目的（升级/演练/故障）。
- `bidcraft backup-prune` 默认保留 10 份；生产保留策略按合规要求调整，但**至少保留升级前一份**。
- 恢复演练建议每季度一次；演练报告与本手册一起归档。
