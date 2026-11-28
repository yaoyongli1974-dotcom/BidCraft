# BidCraft v0.34.0 阶段 10：数据库迁移报告

## 1. 范围

本版本只新增两个可逆迁移，未编辑历史迁移 `0001`–`0016`。

| 迁移 | down_revision | 内容 | 兼容 |
|---|---|---|---|
| `0017_transaction_id_unique` | `0016_fact_provenance` | 为 `recharge_orders.transaction_id` 增加非空部分唯一索引；升级前检测历史重复交易号，重复则 fail-closed 并给出清理指引；降级删除索引 | SQLite / PostgreSQL |
| `0018_file_checksums` | `0017_transaction_id_unique` | 为个人文档/资质证据相关表增加 sha256 校验和列与元数据；降级删除列 | SQLite / PostgreSQL |

当前 Alembic head：`0018_file_checksums`。`tests/test_alembic.py` 已同步并包含可逆性测试。

## 2. 设计约束

- 不修改任何历史迁移文件。
- 空交易号允许共存（历史订单可能为空），只约束非空交易号。
- 迁移使用 inspect 守卫与 idempotent DDL，可重复执行。
- 旧文件无 checksum 时读取端跳过校验，不把缺失哈希当作篡改。
- 不在生产直接执行降级；只在新库/临时库验证可逆性。

## 3. 真实验证

```bash
.venv/bin/python -m pytest -o addopts= -q tests/test_alembic.py
BIDCRAFT_TEST_PG_URL='postgresql+psycopg://postgres:@/bidcraft_local_test?host=/home/yao/.local/share/bidcraft-local-test/pgdata' \
  .venv/bin/python -m pytest -o addopts= -q tests/postgres tests/test_postgres_integration.py \
  tests/test_v030_pg_isolation.py tests/test_worker_fencing_atomicity.py \
  tests/test_jobqueue_race_conditions.py tests/test_alembic.py
```

结果：

- SQLite：迁移 head/reversibility 测试通过。
- 真实 PostgreSQL 16.2：`1078 passed, 1 skipped, 1 warning in 675.10s`。
- 历史迁移 `0001`–`0016` 与基线 `f333c27` diff 为空。
- 全量后端最终结果见 `10_TEST_REPORT.md` 与最终验收报告。

## 4. 失败与修复

首轮真实 PG 运行时 `tests/postgres/test_v0340_db_concurrency.py` 的 40 个用例失败，原因是测试种子创建用户后未先充值就调用 `charge_chapters`，触发 `InsufficientCredits`。这是测试 setup 错误而非生产迁移缺陷；修正为先注入 6 点再扣费，未降低任何断言。修正后 PG 全绿。

## 5. 回滚

- 逻辑回滚：分别 downgrade `0018_file_checksums`、`0017_transaction_id_unique`；迁移均自带可逆 downgrade。
- 数据注意：降级 `0017` 会移除唯一约束，不会删除订单；降级 `0018` 会移除 checksum 列，但已写入文件内容不受影响。
- 生产执行顺序：先备份并 `backup-verify`，再灰度迁移；禁止在生产直接 downgrade，除非经过单独审批和完整恢复演练。

## 6. 结论

迁移设计 **PASS**（SQLite + 本地真实 PG）；生产迁移与 PgBouncer 场景 **NOT VERIFIED**，需远程 CI / 用户环境确认。
