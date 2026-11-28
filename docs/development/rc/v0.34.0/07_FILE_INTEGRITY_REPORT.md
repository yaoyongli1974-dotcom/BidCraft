# BidCraft v0.34.0 阶段 5：文件完整性与上传原子性报告

## 1. 范围与边界

处理任务书 P1-1（上传原子性）与 P1-2（magic bytes + sha256 校验）。严格按文件所有权：

| 类型 | 文件 |
|---|---|
| 生产修改 | `bidcraft/pipeline.py`、`bidcraft/user_docs.py`、`bidcraft/qualifications.py`、`bidcraft/api/routes/my.py` |
| 新增迁移 | `bidcraft/migrations/versions/0018_file_checksums.py`（down 为 `0017_transaction_id_unique`） |
| 测试修改/新增 | `tests/test_alembic.py`、`tests/test_v0340_file_integrity.py` |
| 报告 | 本文件 |

未编辑 `0001`–`0017` 迁移、`models.py`、`jobqueue.py`、`boq.py`、`commercial.py`、`billing.py`、`config.py`、web、CI；无 commit/push、无 `ruff format`、无测试删除或断言降低。工作区其它并行任务的改动不在本阶段范围。

## 2. 生产改动（按函数）

### 2.1 `bidcraft/pipeline.py`

- 新增 `sniff_content` / `validate_declared_type`：扩展名对应的 magic bytes 白名单（PDF `%PDF-`、Office `PK\x03\x04`、PNG/JPEG/WEBP、纯文本），显式拒绝 MZ/ELF/shebang/`<?`/`<script`/gzip/rar/7z/xz/wasm/Java class 等危险文件头。无法识别且不是合法 UTF-8 文本的一律 `unsafe`。
- 新增 `stage_file` / `defer_unlink`：写盘先落到同目录、同扩展名的点号暂存文件；通过 Session 的 `after_commit` 原子 `os.replace` 到正式路径，`after_rollback` 清理暂存文件。每个 Session 只注册一组事件钩子（`db.info`），不会随上传次数累积；事务内解析用 `pending["paths"]` 读暂存路径（保留原扩展名，loader 分派不受影响），提交后再读正式路径。
- `add_source`：去掉「先写正式路径再 try/except 删文件」的旧写法，改为 `stage_file` + DB 行；HTTPException / DB 失败 / commit 失败都走 rollback 清理，不再留下孤儿文件。`_discard_source` 的预提交删除只能删到尚不存在的正式路径，真正的快照清理由 rollback 钩子完成（即 post-commit/rollback 语义）。

### 2.2 `bidcraft/user_docs.py`

- `save_upload`：先做 magic 白名单校验（纯文本仅对 `.txt/.md` 有效），再 `stage_file` 写暂存；sha256 通过显式 SQL 写入 0018 新增的 `user_docs.checksum` 列（模型未声明该列，故不触碰 `models.py`），与业务行同事务提交。
- 新增 `stored_checksum` / `verify_stored_file`：下载时重算 sha256 与库中记录比对，不符或路径越界一律 `LookupError`（HTTP 404）；历史行空串表示「未记录哈希」，不伪造已验证状态。
- 新增 `delete_user_files`：账号注销/管理端删除时清理该用户目录下全部文件。

### 2.3 `bidcraft/qualifications.py`

- `add_evidence`：magic 白名单校验（图片/PDF；历史纯文本占位证据通过 `allow_text_fallback` 兼容既有测试夹具），暂存文件 + `sha256` 写入 evidence JSON 条目。
- `evidence_path`：下载前按条目 `sha256` 重算比对，篡改即 404。
- `delete_evidence`：删文件改为 `defer_unlink`，提交后才真正删除，回滚时文件保持。
- 新增 `transfer_evidence_to_owner`：企业共享资质易主时把证据文件搬到新 owner 目录，`evidence[*].filename/path` 同步改写；只处理旧 owner 目录下的文件，缺失文件保留元数据由校验暴露。
- 新增 `purge_evidence`：删除私有资质的全部证据文件并清空元数据。

### 2.4 `bidcraft/api/routes/my.py`

- `my_doc_file` 改用 `user_docs.verify_stored_file`，校验文件边界与 sha256。

### 2.5 迁移 0018

- `user_docs.checksum VARCHAR(64) NOT NULL DEFAULT ''`，`upgrade` 只在列不存在时添加，`downgrade` 只删该列；SQLite/PG 通用，重复执行幂等。历史行默认空串，读取端跳过校验，不伪造历史哈希。

## 3. 测试（先写测试，后写生产修复）

新增 `tests/test_v0340_file_integrity.py`（8 个用例）：

| 用例 | 覆盖点 |
|---|---|
| `test_user_doc_upload_backpressure_leaves_no_orphan` | 注入队列背压 HTTPException：暂存文件/正式文件/DB 行都不留 |
| `test_evidence_upload_failure_leaves_no_orphan` | 注入审计 HTTP 层异常：证据目录空、evidence 列表空 |
| `test_magic_whitelist_rejects_disguised_uploads` | `.pdf` 内 MZ、`.txt` 内 shebang 都 400；正常文本成功 |
| `test_user_doc_checksum_is_stored_and_verified_on_download` | 库中 sha256 正确、下载成功；篡改后 404 |
| `test_qualification_evidence_checksum_is_stored_and_verified_on_download` | evidence sha256 正确、下载成功；篡改后 404 |
| `test_evidence_with_executable_magic_is_rejected` | 伪装成 PNG 的 PE 文件 400 |
| `test_shared_evidence_transfer_and_user_deletion_move_or_delete_files` | 共享资质易主后证据搬家、路径/哈希正确；注销后私有证据文件清空 |
| `test_add_source_rollback_removes_staged_snapshot` | 招标源文件同事务解析可读；注入 DB 失败回滚后项目目录无孤儿快照 |

`tests/test_alembic.py`：HEAD 改为 `0018_file_checksums`；新增 `test_0018_file_checksums_is_reversible_and_idempotent`（列存在且默认 `''`、降级移除、升级恢复、历史行保持空串、重复 init/upgrade 幂等）。

**测试先行证据**：修复前运行
`.venv/bin/python -m pytest -o addopts= -q tests/test_v0340_file_integrity.py tests/test_v0340_audit_ops.py tests/test_alembic.py`
→ `30 failed, 1 skipped, 2 warnings in 15.99s`（孤儿文件、伪装上传放行、无 checksum 列、审计缺失等）。

## 4. 验证结果（实际命令）

```bash
.venv/bin/ruff check bidcraft/pipeline.py bidcraft/user_docs.py bidcraft/qualifications.py \
    bidcraft/api/routes/my.py bidcraft/api/routes/admin.py bidcraft/cli.py \
    bidcraft/compliance.py bidcraft/migrations/versions/0018_file_checksums.py \
    tests/test_alembic.py tests/test_v0340_file_integrity.py tests/test_v0340_audit_ops.py
# All checks passed!

.venv/bin/python -m pytest -o addopts= -q \
    tests/test_v0340_file_integrity.py tests/test_v0340_audit_ops.py tests/test_alembic.py
# 31 passed, 1 skipped, 2 warnings in 16.38s

.venv/bin/python -m pytest -o addopts= -q \
    tests/test_alembic.py tests/test_admin.py tests/test_user_docs.py \
    tests/test_qualifications.py tests/test_compliance.py tests/test_backup.py \
    tests/test_fix2_runtime_backup.py tests/test_sec_fix_audit.py tests/test_clock.py
# 87 passed, 1 skipped, 2 warnings in 48.42s

.venv/bin/python -m pytest -o addopts= -q \
    tests/test_api_boq.py tests/test_v030_stage5_limits.py tests/test_project_duplicate.py
# 40 passed, 1 warning in 51.71s
```

修复过程中曾出现 7 个相邻回归失败（同事务解析读不到暂存文件）；已通过「暂存文件保留原扩展名 + `read_parse_inputs` 读暂存路径」修复，重跑 40 passed，未删除或降低任何断言。

## 5. 残留风险与回滚

- **历史文件无哈希**：0018 升级前上传的文档/证据 checksum 为空，读取端跳过校验；如需强制校验，需另行离线回填（本阶段不伪造哈希）。
- **纯文本兜底**：资质证据允许短的可解码 UTF-8 文本（既有夹具为 `b"x"`/`b"cert"` 等占位内容），因此「把脚本文本改名成 .png」仍可能被接受；可执行二进制 magic 与已知脚本头已被拒绝。若要完全消除，应统一改为真实图片/PDF 并去掉兜底。
- **事件清理依赖 Session 生命周期**：暂存文件清理绑定在 `session_scope` 的 commit/rollback 事件；绕过 `session_scope`、既不 commit 也不 rollback 的长期 Session 可能保留暂存文件（需调用方自行关闭）。
- **PG 迁移未在真实 PG 实例演练**：0018 为纯 `ADD/DROP COLUMN` + `inspect` 判存，SQLite 可逆/幂等已实测；PG 兼容性按同类 0016 口径保证，未做真实 PG DDL 演练（NOT VERIFIED）。
- **回滚**：`alembic downgrade 0017_transaction_id_unique`（或 `bidcraft` 自带迁移入口）删除 `checksum` 列；代码回退后读取端不再校验，已落盘文件与 evidence JSON 中多出的 `sha256` 键不影响旧代码（被忽略）。
