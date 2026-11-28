# BidCraft v0.34.0 阶段 7：备份、恢复演练与运维硬化报告

## 1. 范围与边界

处理任务书 P1-6（pg_dump 一致性、备份指纹、verify fail-closed、opt-in 恢复演练）与 P1-7（合规 CSV 列）。文件所有权：

| 类型 | 文件 |
|---|---|
| 生产修改 | `bidcraft/cli.py`、`bidcraft/compliance.py` |
| 新增测试 | `tests/test_v0340_audit_ops.py` |
| 报告 | 本文件 |

未编辑 `config.py`、`models.py`、`billing.py`、web、CI、既有备份测试。

## 2. 生产改动

### 2.1 `bidcraft/cli.py::backup`

- PostgreSQL 路径的 `pg_dump` 命令加入 `--single-transaction`，保证 dump 是单事务一致快照，避免边导边写导致的表间不一致。
- `manifest.json` 新增 `db_sha256`（数据库快照 `bidcraft.db` / `dump.sql`）与 `secret_key_sha256`（包含密钥时为其真实 sha256，否则空串）。指纹与打包内容在同一次命令中计算，不读取正文进日志。

### 2.2 `bidcraft/compliance.py`

- 新增 `_verify_backup_identity`：只要清单记录过 `db_sha256` / `secret_key_sha256`，`backup-verify` 就重算包内对应文件哈希；缺失或不符一律 `ComplianceError`（CLI 非 0 退出，fail-closed）。旧备份没有这两个键时跳过，保持向后兼容。
- `statement_csv`：流水区块保持原首行表头（兼容既有脚本与断言）；订单改为独立区块，表头明确 `订单号 / 金额（分） / 点数 / 状态 / 交易号 / 支付渠道`，值直接取 `amount_cents`（不再 `/100` 错位到「数量」列）、`credits`、`status`、`transaction_id`、`provider`。

### 2.3 恢复演练

新增 opt-in 用例 `tests/test_v0340_audit_ops.py::test_restore_drill_verifies_manifest_hashes_after_extract`，默认跳过；设置 `BIDCRAFT_RESTORE_DRILL=1` 才运行，只使用 pytest 临时目录，不碰线上数据。解压后逐项核对 DB/secret.key 真实哈希与清单一致。既有 `tests/test_fix3_backup_drill.py` 继续验证「同一把 secret.key 能解密 + ORM 能读回数据」。

## 3. 测试

| 用例 | 覆盖点 |
|---|---|
| `test_backup_manifest_contains_db_and_secret_key_hashes` | zip 内 db/secret.key 哈希与清单一致 |
| `test_backup_verify_fails_closed_on_recorded_hash_mismatch` | 篡改清单 db_sha256 后 `verify_backup` 抛 `ComplianceError` |
| `test_pg_dump_backup_uses_single_transaction` | monkeypatch `pg_dump` 捕获真实命令行，断言 `--single-transaction` |
| `test_statement_csv_has_explicit_order_columns` | 订单区块列名与值（cents/credits/status/txid/provider）正确 |
| `test_restore_drill_verifies_manifest_hashes_after_extract` | opt-in：解压后 DB/密钥哈希与清单一致 |

**测试先行证据**：修复前 `30 failed, 1 skipped, 2 warnings in 15.99s`，其中备份指纹、CSV 列、PG 参数相关用例均失败。

## 4. 验证结果（实际命令）

```bash
.venv/bin/ruff check bidcraft/cli.py bidcraft/compliance.py tests/test_v0340_audit_ops.py
# All checks passed!

.venv/bin/python -m pytest -o addopts= -q \
    tests/test_v0340_file_integrity.py tests/test_v0340_audit_ops.py tests/test_alembic.py \
    tests/test_admin.py tests/test_user_docs.py tests/test_qualifications.py \
    tests/test_compliance.py tests/test_backup.py tests/test_fix2_runtime_backup.py \
    tests/test_sec_fix_audit.py tests/test_clock.py
# 87 passed, 1 skipped, 2 warnings in 48.42s

BIDCRAFT_RESTORE_DRILL=1 .venv/bin/python -m pytest -o addopts= -q \
    tests/test_fix3_backup_drill.py \
    tests/test_v0340_audit_ops.py::test_restore_drill_verifies_manifest_hashes_after_extract
# 2 passed in 2.70s
```

PG 路径仅用假 `pg_dump` 捕获命令行参数，未在真实 PostgreSQL 实例上跑端到端备份（NOT VERIFIED）；SQLite 真实 zip 备份/校验/恢复演练均已通过。

## 5. 残留风险与回滚

- **旧备份无指纹**：无 `db_sha256` / `secret_key_sha256` 的历史 zip 仍按原有结构校验通过，无法提供防替换保证；需要时用新版本重新备份。
- **单事务 dump 的前提**：`--single-transaction` 只对支持事务一致快照的 PostgreSQL 表有效；非事务表（极少见）仍需业务侧停机窗口。
- **CSV 双表头**：见报告 08。
- **回滚**：去掉 `--single-transaction` 与两个 manifest 键即可回到旧格式；旧 `backup-verify` 会忽略新增键，旧备份也能继续校验。已带新键的备份在旧代码上仍可手工解压恢复。
