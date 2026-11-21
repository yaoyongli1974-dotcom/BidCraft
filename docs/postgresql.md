# PostgreSQL 支持

BidCraft 默认使用 SQLite（WAL 模式），适合单进程/单机部署。设置 `BIDCRAFT_DB_URL`
后即切换到 PostgreSQL，应用行为保持一致；API 与 Worker 分离部署
（见 `deploy/README.md` 5.1 节）时推荐用 PostgreSQL，多个进程可共享同一数据库。

## 启用步骤

1. 安装驱动（SQLite 用户无需安装）：

   ```bash
   uv sync --extra postgres
   # 或 pip install -e '.[postgres]'
   ```

2. 在 PostgreSQL 中创建专用库和用户：

   ```sql
   CREATE USER bidcraft WITH PASSWORD '请替换为强口令';
   CREATE DATABASE bidcraft OWNER bidcraft;
   ```

3. 设置连接串（三种写法等价，都会归一到 psycopg 3 驱动）：

   ```bash
   export BIDCRAFT_DB_URL='postgresql://bidcraft:口令@127.0.0.1:5432/bidcraft'
   # 或 postgresql+psycopg://... / postgres://...
   ```

4. 正常启动即可（`bidcraft serve` / `bidcraft worker` / `uvicorn bidcraft.api:app`）。
   首次启动会自动建表并 stamp Alembic 基线，无需手工初始化。

口令含 `@`、`:`、`/`、`?` 等特殊字符时请做百分号编码，或改用 libpq 的
`postgresql+psycopg://user@host/db?password=...` 形式。

## 与 SQLite 的行为差异（重要）

- **外键会被真正强制执行**。SQLite 部署从未开启 `foreign_keys=ON`，28 处
  `ForeignKey` 只是 DDL 纸面约束；PostgreSQL 默认强制。因此所有删除路径已收敛到
  `services/purge.py` 的 `delete_project_rows` / `delete_user_rows`：账户注销、超管删用户、
  删子账号都会显式清理关联行（不删 `AuditLog.user_id` 追责记录）。不要再用裸
  `db.delete(user)` 绕过这些入口。
- **迁移由 `create_all` + Alembic 负责**。历史补列 `_migrate_columns` 只适用于
  SQLite（含 `BOOLEAN DEFAULT 0` 等 SQLite 友好写法），在 PG 上会跳过。新增 schema
  变更仍走幂等 Alembic revision。
- **迁移锁跨主机**：SQLite 用本地文件锁，PostgreSQL 用会话级咨询锁
  `pg_advisory_lock`，多台机器同时启动也不会并发执行 DDL。
- **时间列是 `TIMESTAMP WITHOUT TIME ZONE`**，库里存 naive UTC，出站由 `clock`
  转北京时间 ISO；与 SQLite 完全一致。
- **JSON 列用 SQLAlchemy `JSON` 类型**（PostgreSQL 上落 `json` 而非 `jsonb`），
  应用层过滤仍在 Python 中做，保持跨方言一致。
- 任务认领的条件 `UPDATE ... RETURNING` 与 fencing token 在两种方言上行为一致；
  并发碰撞时单次认领可能返回 `None`，worker 循环会重试。

## 备份与恢复

`bidcraft backup` 按后端自动选择方式：

- SQLite：`sqlite3` 在线备份 API，zip 内为 `bidcraft.db`；
- PostgreSQL：调用 `pg_dump`（需安装 `postgresql-client`），zip 内为 `dump.sql`。
  口令通过 `PGPASSWORD` 传递，不出现在命令行参数里。

`bidcraft backup-verify <zip>` 两种格式都能校验：SQLite 跑完整性检查，PG 解析
SQL 中的建表与数据行。恢复 PG 备份时用 `psql` 导入 `dump.sql`，再放回 `secret.key`。

## 已知限制

- 没有提供 SQLite → PostgreSQL 的自动数据迁移工具。已有数据请用
  `sqlite3 .dump` 或 `pgloader` 等工具迁移；新部署建议直接从空 PG 库开始。
- PostgreSQL 需要对应驱动；未安装 `psycopg` 时设置 PG 连接串会在建引擎阶段报
  “Can't load plugin”，这是预期行为，请安装 `postgres` extra。
- 默认连接池未做上限约束；高并发下发部署请按机器资源调整 SQLAlchemy pool 参数。

## 验证记录

- 配置/方言单测：`tests/test_postgres_support.py`（默认运行，无需 PG 服务）。
- 真实 PG 集成：`BIDCRAFT_TEST_PG_URL=... .venv/bin/python -m pytest tests/test_postgres_integration.py`，
  覆盖建表/migration head、中文与 JSON CRUD、并发原子认领、fencing、导出门禁、
  外键删除、咨询锁与 pg_dump 备份。**会清空目标库所有表，务必指向专用测试库。**
