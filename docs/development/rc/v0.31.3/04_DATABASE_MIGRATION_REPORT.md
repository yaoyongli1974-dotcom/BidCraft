# G3.1 数据库迁移报告（v0.31.3）

## 1. 目标与环境

- RC PostgreSQL：独立 pgserver 16.2 临时集群 `/tmp/opencode/v0313-rc-pgdata`
- 数据库 URL：本地临时 socket URI（脱敏后 `postgresql+psycopg://postgres:****@/postgres?host=...`）
- 隔离目录：`BIDCRAFT_HOME=/tmp/opencode/rc-v0313-home`
- 禁止连接生产库；未修改历史迁移；未删除业务数据。

## 2. 执行

准备 RC env（本地临时、chmod 600、不入库）：

```
BIDCRAFT_HOME=/tmp/opencode/rc-v0313-home
BIDCRAFT_DB_URL=<RC_PG_URI>
BIDCRAFT_ENV=rc
BIDCRAFT_MODEL_BASE_URL=http://127.0.0.1:9/v1
BIDCRAFT_MODEL_NAME=rc-smoke-model
BIDCRAFT_ADMIN_PASSWORD=<local random test only>
```

迁移与核验：

```
set -a; . /tmp/opencode/rc-v0313.env; set +a
.venv/bin/python - <<'PY'
from sqlalchemy import inspect, text
from bidcraft.config import settings
from bidcraft.db import get_engine, init_db
init_db()
engine = get_engine()
with engine.connect() as c:
    dialect = c.dialect.name
    version = c.execute(text('SELECT version_num FROM alembic_version')).scalar()
    cols = {col['name'] for col in inspect(c).get_columns('jobs')}
    tables = set(inspect(c).get_table_names())
print('DB_DIALECT', dialect)
print('ALEMBIC_VERSION', version)
print('JOBS_COLUMNS_OK', {'owner_id','refund_claimed','claim_token','lease_expires_at'} <= cols)
print('RC_TABLES_OK', {'pending_charges','generation_leases','quality_reports'} <= tables)
print('DB_URL_DISPLAY', settings.db_url_display)
PY
```

实测输出：

```
DB_DIALECT postgresql
ALEMBIC_VERSION 0012_generation_lease
JOBS_COLUMNS_OK True
RC_TABLES_OK True
DB_URL_DISPLAY postgresql+psycopg://postgres:****@/postgres?host=/tmp/opencode/v0313-rc-pgdata
```

## 3. 结论

- 迁移从空库执行到单 head `0012_generation_lease` 成功；关键列与表齐全。
- 本地执行的是 pgserver 真实 PostgreSQL 16.2，不是生产托管实例；结论为 **A PASS（RC 临时 PG）**，生产迁移仍需按备份手册在预发/生产窗口执行。
- CI `postgres (real integration)` 在最终 SHA `64ab219` 上 SUCCESS，作为额外证据。
