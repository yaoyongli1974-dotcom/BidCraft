# BidCraft v0.34.0 阶段 6：权限、证据生命周期与审计覆盖报告

## 1. 范围与边界

处理任务书 P1-3（共享证据移交/删除）与 P1-4（审计盲点、`/admin/health` 的 web_ready 路径）。文件所有权：

| 类型 | 文件 |
|---|---|
| 生产修改 | `bidcraft/api/routes/admin.py`、`bidcraft/api/routes/my.py`、`bidcraft/compliance.py`、`bidcraft/qualifications.py`、`bidcraft/cli.py` |
| 新增测试 | `tests/test_v0340_file_integrity.py`、`tests/test_v0340_audit_ops.py` |
| 报告 | 本文件 + `07_FILE_INTEGRITY_REPORT.md` |

未编辑 `models.py`、`billing.py`、`web files`、CI 与其它并行任务文件。

## 2. 生产改动

### 2.1 审计覆盖（`bidcraft/api/routes/admin.py`）

新增 `_admin_audit(db, action, detail)`：显式 `record_audit(..., actor=current_user())`，身份来自请求上下文（CLI 无上下文时记 `system`）。覆盖此前无审计的写操作：

- `model.create` / `model.update` / `model.delete`：记录 model_id、name、kind/provider、变更字段名及 `api_key_updated` / `api_key_cleared` 布尔标记；**不写 api_key / api_key_enc 值**。
- `billing.settings`：对比保存前后配置，逐字段记 `{"from":…,"to":…}`，覆盖 `vip_discount` / `chapter_cost` / `signup_credits` / `sub_account_limit` 与套餐数量；不落套餐正文。
- `billing.redeem`：记 count / credits / amount_cents / days；**不写生成的兑换码**（可消费凭证不入日志）。
- `billing.credit_adjust`：记 user_id / username / amount；不含口令。
- 已有 `user.vip`、`admin.user.*`、`auth.*` 行为保持不变，口令/哈希仍只记字段名与布尔变化。

### 2.2 CLI 改密（`bidcraft/cli.py`）

`reset-password` 成功后写 `admin.user.password_reset` 审计，detail 只有 user_id/username；`actor` 取 `current_user()`（离线命令为空 → system）。口令与哈希均不进入日志。

### 2.3 用户删除的资产/证据生命周期（`bidcraft/compliance.py`）

- 新增 `prepare_user_deletion(db, user)`，统一处理：个人文档文件 → 企业归属移交（含共享证据搬家）→ 私有资质证据删除 → 发票文件清理。
- `_detach_enterprise_memberships` 在把共享资质 owner 改为企业新 owner **之前**调用 `qualifications.transfer_evidence_to_owner`，证据文件从旧 owner 目录搬到新 owner 目录，`evidence[*].filename/path` 同步改写；`removed` 新增 `evidence_files_moved` 计数。
- `delete_account` 与管理端 `DELETE /admin/users/{id}` 共用该 helper，避免两条删除路径一边搬行、一边把证据留在被删用户目录。
- `my_doc_file` 改用 `user_docs.verify_stored_file`，下载时对路径边界与 sha256 双重校验（`my.py`）。

### 2.4 运维可见性

`/admin/health` 的 `web_ready` 由 `Path(__file__).resolve().parent.parent / ...`（指向 `api/`）改为 `parents[2] / "web_dist" / "index.html"`（指向 `bidcraft/`），与 `/health`、`/health/ready` 同口径；构建产物存在时三项探针一致返回 true。

## 3. 测试（真实 API / 真实文件 / 真实审计表）

`tests/test_v0340_audit_ops.py`：

| 用例 | 覆盖点 |
|---|---|
| `test_model_config_changes_are_audited_without_secrets` | 模型增删改 3 类审计；`sk-v0340-top-secret` 不出现在任何 detail；actor=admin |
| `test_billing_settings_redeem_and_credit_adjustments_are_audited` | billing.settings/redeem/credit_adjust 三类；chapter_cost/vip_discount 前后值；兑换码不入审计；actor=admin |
| `test_cli_reset_password_is_audited_without_secret` | CLI 离线改密审计 1 条；新口令与 password_hash 不入 detail |
| `test_admin_health_web_ready_and_health_ready_smoke` | `/health/ready` 200 且 web_ready=true；`/admin/health` web_ready=true |
| `test_statement_csv_has_explicit_order_columns` | CSV 订单区块列名含金额（分）/点数/状态/交易号/支付渠道，值与订单一致 |

`tests/test_v0340_file_integrity.py::test_shared_evidence_transfer_and_user_deletion_move_or_delete_files`：企业 owner + member 真实建群、共享资质证据上传、注销 member 后证据文件搬到 enterprise owner 目录且路径/哈希不变；再注销 owner 后私有证据文件删除。

**测试先行证据**：修复前 `30 failed, 1 skipped, 2 warnings in 15.99s`，包含审计缺失、web_ready=false、CSV 列错位等真实失败。

## 4. 验证结果（实际命令）

```bash
.venv/bin/ruff check bidcraft/pipeline.py bidcraft/user_docs.py bidcraft/qualifications.py \
    bidcraft/api/routes/my.py bidcraft/api/routes/admin.py bidcraft/cli.py \
    bidcraft/compliance.py bidcraft/migrations/versions/0018_file_checksums.py \
    tests/test_alembic.py tests/test_v0340_file_integrity.py tests/test_v0340_audit_ops.py
# All checks passed!

.venv/bin/python -m pytest -o addopts= -q \
    tests/test_v0340_file_integrity.py tests/test_v0340_audit_ops.py tests/test_alembic.py \
    tests/test_admin.py tests/test_user_docs.py tests/test_qualifications.py \
    tests/test_compliance.py tests/test_backup.py tests/test_fix2_runtime_backup.py \
    tests/test_sec_fix_audit.py tests/test_clock.py
# 87 passed, 1 skipped, 2 warnings in 48.42s

.venv/bin/python -m pytest -o addopts= -q \
    tests/test_billing.py tests/test_vip.py tests/test_enterprise_assets.py \
    tests/test_enterprise_permissions.py tests/test_enterprise_evidence.py \
    tests/test_purge.py tests/test_fix3_health.py tests/test_v030_stage8_observability.py
# 35 passed, 1 warning in 29.63s
```

## 5. 残留风险与回滚

- **CSV 双表头**：为兼容既有断言（首行必须是历史流水表头），订单单独一段带新表头；人读/Excel 正常，严格 `DictReader` 单表解析需按区块处理。若后续允许更新 `tests/test_compliance.py`，可合并为单一表头并删除空行。
- **纯文本证据兜底**：见报告 07，脚本文本改名成图片后缀仍可能被接受；二进制/已知脚本头已拒绝。
- **审计为追加日志**：管理端删除用户只清理业务行，`AuditLog` 保留（与既有 `delete_user_rows` 口径一致）。
- **PG 真实删除路径未演练**：证据转移是文件操作 + JSON 路径改写，跨 PG 无额外 SQL；但端到端 PG 注销未在本阶段实测（NOT VERIFIED）。
- **回滚**：代码回退即可去掉新增审计与 web_ready 修正；审计行本身保留（`admin.user.password_reset` / `model.*` / `billing.*` 等不会被旧代码消费，但也不影响旧代码）；已搬家的证据文件不会自动搬回，需按 `evidence[*].path` 手工核对。
