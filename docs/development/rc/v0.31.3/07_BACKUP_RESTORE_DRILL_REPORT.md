# G6 PostgreSQL + 文件系统备份恢复演练报告（RC v0.31.3）

## 1. 范围与环境

- 隔离 RC PostgreSQL 16.2（pgserver，仅本地 RC 实例），非生产库。
- 隔离 RC HOME：`/tmp/opencode/rc-v0313-home`；环境文件 `/tmp/opencode/rc-v0313.env`（chmod 600，密码不报告）。
- 为让 `bidcraft backup` 的 libpq URI 正常解析，RC 专用 pgserver 增加 TCP 监听 `127.0.0.1:5432`；备份时覆盖 `BIDCRAFT_DB_URL=postgresql+psycopg://postgres:@127.0.0.1:5432/postgres`。
- 未连接生产数据库、未删除生产数据、未使用真实客户文件。

## 2. 备份执行

命令要点（env 来自 RC 文件，口令不回显）：

```
. /tmp/opencode/rc-v0313.env
export BIDCRAFT_DB_URL='postgresql+psycopg://postgres:@127.0.0.1:5432/postgres'
export PATH="/tmp/opencode/pgvenv312/lib/python3.12/site-packages/pgserver/pginstall/bin:$PATH"
bidcraft backup
```

结果：

- 备份文件：`/tmp/opencode/rc-v0313-backup.zip`，10.2 KB。
- 包内：`dump.sql`（PostgreSQL 逻辑备份）+ `manifest.json`。
- `secret.key` 不存在时跳过，不伪造条目。
- 备份数据目录配置为 0，未包含无关文件。

## 3. 备份结构校验

```
bidcraft backup-verify /tmp/opencode/rc-v0313-backup.zip
```

输出：`备份完整：56 张表，9 行数据`。

该校验解析 pg_dump SQL 并核对清单；它不是恢复演练本身。

## 4. 真实恢复演练

在**同一个隔离 RC pgserver** 上创建全新数据库 `bidcraft_rc_restore`，仅从此 RC 备份导入：

```
psql -h 127.0.0.1 -U postgres -d postgres -c 'DROP DATABASE IF EXISTS bidcraft_rc_restore;'
psql -h 127.0.0.1 -U postgres -d postgres -c 'CREATE DATABASE bidcraft_rc_restore;'
unzip -p /tmp/opencode/rc-v0313-backup.zip dump.sql > /tmp/opencode/rc_restore_dump.sql
psql -h 127.0.0.1 -U postgres -d bidcraft_rc_restore -v ON_ERROR_STOP=1 -q -f /tmp/opencode/rc_restore_dump.sql
```

导入成功（`RESTORE_OK`），无 SQL 错误。

恢复后验证（将 `BIDCRAFT_DB_URL` 指向新库）：

- `DB_DIALECT postgresql`
- `ALEMBIC_VERSION 0012_generation_lease`
- 应用表 55 张 + `alembic_version` 1 张 = 备份校验一致；应用数据 8 行 + 版本表 1 行 = 9 行。

## 5. 结论与残留

| 项 | 结论 |
|---|---|
| RC 隔离 PostgreSQL 备份生成 | A PASS |
| 备份结构校验 | A PASS |
| 全新数据库中的真实 SQL 恢复 | A PASS |
| Alembic 版本与行数一致 | A PASS |
| 生产 PostgreSQL 恢复演练 | NOT VERIFIED（需生产/预发窗口） |
| 文件系统数据目录恢复 | NOT VERIFIED（本次配置 0 个目录，无真实文件可恢复） |

注意：仓库 CLI 提供 `backup` / `backup-verify`，没有 `restore` 子命令；恢复按 `docs/operations/BIDCRAFT_DATABASE_BACKUP_RESTORE.md` 的手册使用 `psql`/`pg_restore` 执行。该事实在最终报告中不得表述为「一键恢复」。

## 6. 回滚与恢复

本演练仅操作 RC 专用数据库 `bidcraft_rc_restore`；如需清理，由授权用户在 RC 实例中删除该数据库。未触碰 RC 主库 `postgres` 中的业务数据之外的内容，也未触碰生产环境。
