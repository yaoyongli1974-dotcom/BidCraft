# BidCraft v0.31.0 阶段 0：仓库基线与现状审查报告

## 1. 基线结论

| 项目 | 实际值 |
|---|---|
| 仓库 | `yaoyongli1974-dotcom/BidCraft`（`git@github.com:yaoyongli1974-dotcom/BidCraft.git`） |
| 当前分支 | `quality/v0.31.0-reliability`（从干净的 `main` 新建，未推送） |
| 基线 HEAD | `510f56697e04b6e87434bf54376e13ebf8c80177`（v0.30.0 发版提交） |
| 工作区 | 审查前、审查后均为干净（`git status --porcelain` 为空） |
| 版本标签 | `v0.30.0` 指向同一提交；不存在 v0.31 标签或提交 |
| 版本一致性 | `bidcraft/__init__.py`、`pyproject.toml`、`uv.lock`、`web/package.json`、`web/package-lock.json` 全部为 `0.30.0` |
| Alembic | 单 head `0011_stage3_accounting`，共 12 个历史 revision（0001～0011）；本阶段未改动历史迁移 |
| CI | `main` @ `510f566` 最新 GitHub Actions 运行 `45392687693` 等 3 次均为 `success`（9/9 job） |
| 本地环境 | Python 3.13.5、SQLite 3.46.1、SQLAlchemy 2.1.8、Node v26.13.1、npm 11.20.0 |
| 数据库类型 | 默认 SQLite；生产可选 PostgreSQL（`deploy/docker-compose.yml` 中 `BIDCRAFT_DB_URL` 默认注释，CI 用 `postgres:16-alpine` 做真实集成测试） |
| 部署形态 | `deploy/docker-compose.yml`：非 root 镜像、资源限制、`/health/live` 原生探针、`/health/ready` 扩展探针、刻意不启用 `env_file` |

阶段 0 冒烟验证：`.venv/bin/python -m pytest -o addopts="" -q tests/test_jobqueue.py tests/test_v030_stage4_fencing.py tests/test_v030_stage4_similarity.py tests/test_v030_stage3_core.py tests/test_v030_stage7_contract.py` → **57 passed**（仅 1 条 Starlette/httpx 弃用警告）。v0.30.0 收官完整基线：后端 1620 passed / 16 skipped、Web 39 files / 319 tests、CI 9/9（见 `BIDCRAFT_V030_FINAL_ACCEPTANCE_REPORT.md`）。

## 2. 核心模型关系

| 模型 | 位置 | 与本轮相关的字段 |
|---|---|---|
| `Job` | `bidcraft/models.py:455` | `status`（裸字符串，`:466`）、`project_id`、`kind`、`payload`、`result`、`owner_id`（索引）、`attempts/max_attempts`、`claimed_at`、`lease_expires_at`、`heartbeat_at`、`worker_id`、`claim_token`（`:486`）、`refund_claimed`（`:489`）；`ix_jobs_status_priority_created`（`:459-461`） |
| `PendingCharge` | `bidcraft/models.py:542` | `user_id`、`billing_account_id`、`project_id`、`credits`、`chapters`、`sections_at_charge`（`:570`）、`status`、`refunded_credits`、`note`；无 FK，短周期账务标记 |
| `Section` | `bidcraft/models.py:396` | `body`、`status`、`revision`、`review_status`；章节幂等写入依据 |
| `SimilarityReport` / `SimilarityHit` | `bidcraft/models.py:971` / `:989` | `status/stats`、`report_id` 关联命中；查重幂等依据 |
| `QualityReport` / `QualityFinding` | `bidcraft/models.py:1138` 起 | 不可变报告快照 + 可编辑 finding 状态 |
| `SectionEditLock` | `bidcraft/models.py:1106` | 仅人工编辑互斥，生成路径从未获取 |
| 迁移 `0011_stage3_accounting` | `bidcraft/migrations/versions/0011_stage3_accounting.py:55-84` | 幂等新增 `pending_charges.sections_at_charge`、`jobs.refund_claimed`，无索引/唯一约束变更 |

## 3. 四条调用链

### 3.1 Worker 调用链（创建 → 入队 → 认领 → 续租 → 执行 → 写结果 → 完成）

1. 创建：API 在同事务内建 `Job(status="queued")` 与 `queued` 事件，事务外调用 `jobqueue.submit()`。入口包括 `generate_all` `api/routes/projects.py:1701-1764`、`evaluation_run_async` `api/routes/evaluation.py:63-111`、`similarity_check` `api/routes/similarity.py:38-141`、批量导出/检查 `projects.py:257-364`。
2. 唤醒：`submit()` `jobqueue.py:324-343`（`target/args` 为兼容占位，被忽略）；`embedded_workers` 时 `_ensure_workers()` `:1192-1205` 起 ≤8 worker 线程 + 单 reaper。
3. 认领：`claim_next()` `jobqueue.py:456-480` 使用条件 UPDATE `_claim_statement()` `:425-453`，每次生成新 `uuid4().hex` token，外层 `status='queued'` 防止 PG EvalPlanQual 双认领，`attempts += 1`。
4. 派发：`_execute()` `jobqueue.py:1047-1097` 通过 `execution_context(token, abort, *, job_id, kind, project_id, worker_id)` `:149-175` 绑定 `_claim_token` ContextVar、abort 信号和日志字段；`_runner_for()` `:271-291` 懒加载 `workers/__init__.py:19-26` 注册的 8 类 runner；`_rebuild_args()` `:1100-1125`；启动 `_heartbeat()` `:483-522`。
5. 心跳：条件 UPDATE `WHERE id=? AND status='running' AND worker_id=? AND claim_token=?` `:503-512`；`rowcount==0` 时置 abort 并退出；异常仅 debug 日志。
6. 执行与终态：runner 自行写终态，无通用 `complete()`。generation `workers/generation.py:40-234`（章节 ledger `step_status/mark_step_done`）；similarity `workers/similarity.py:21-92`；evaluation `workers/evaluation.py:62-119`。失败走 `_set_failed()` `jobqueue.py:525-542` 或 `_fail_job()` `services/job_support.py:29-78`（含退款 savepoint）。安全网 `_finalize_after_run()` `jobqueue.py:1026-1044`。

### 3.2 任务回收链（租约超时 → Reaper → 旧 Worker 失效 → 新认领）

- 参数：租约默认 180s（`_lease_seconds()` `:302-307`），心跳不超过租约一半；reaper 周期 30s（`:243`），对账周期 300s（`:246`）。
- 扫描：`reap_expired()` `jobqueue.py:565-588` 与 `recover_on_startup()` `:591-626` 均用 `SELECT ... WHERE status='running' AND (lease_expires_at IS NULL OR lease_expires_at < now)`，逐行 `_requeue()` `:545-562`。**Reaper 本身无条件 UPDATE/行锁/token 校验。**
- 重试上限：`cap = min(_max_attempts(), job.max_attempts or _max_attempts())`（`:550`），默认 3；到顶走 `_set_failed()`，否则清 `claim_token` 等字段并回 `queued`。
- 旧 Worker 失效：`fenced_job()` `:87-102` 返回 `None`；`assert_claim_current()` `:186-204` 用**新 Session**绕开 identity map 并抛 `JobAborted`；心跳 `rowcount==0` 置 abort；`raise_if_aborted()` `:178-183` 在检查点生效。`JobAborted` 不写 failed、不退款，留给 reaper 重新排队。
- 幂等恢复：generation 用 `payload['done_steps']` 的 `chapter:<node_id>`；similarity 用 `report_id` + done 短路；evaluation 用 `job.result/payload['report_id']`；批量用 `payload.results/done`。
- 已写业务结果：已 flush 的 Section/报告不回滚；相似任务若在提交前被回收则整笔回滚，若已提交则靠 report_id 幂等。

### 3.3 账务调用链（扣费 → marker → 执行 → 章节 → 结算/退款 → 对账）

1. 原语：`charge_chapters()` `billing.py:218`（VIP ceil、子账号父账号代付、写 `CreditTransaction`）；`refund_chapters(..., origin=)` `billing.py:245`；`add_credits()` 负额原子条件更新 `:167-205`。
2. 同步路径（无 Job）：`generate_chapter` `projects.py:1547` 与 `write` `projects.py:1633`：扣费 → `begin_pending_charge()` `jobqueue.py:617` → `pending_charge_heartbeat()` + `generate_document` → `settle_pending_charge()` `jobqueue.py:864`。
3. 异步路径：`generate_all` `projects.py:1701`：扣费 → 建 Job（`payload={charged,chapters,user_id,owner_id,...}`）→ `submit`；worker `_run_generation_job` 按 `done_steps` 计算退款 `_job_refund()` `services/job_support.py:219` / `refund_charged_job()` `jobqueue.py:792`（`refund_claimed` 条件 UPDATE 幂等）。**该路径不创建 PendingCharge marker。**
4. 对账：`reconcile_pending_charges()` `jobqueue.py:936` 按 `updated_at` TTL 扫描 pending marker，用当前章节数减 `sections_at_charge` 计算增量；调用点为启动 `recover_on_startup()`、reaper、CLI `bidcraft reconcile-charges`。
5. **同项目并发生成规则：当前允许**。同步 `generate_chapter`/`write` 无在途检查；`generate_all` 只有 `_cancel_stale_jobs()` `services/job_support.py:81-120` 的 check-then-insert 软检查（可被并发绕过，且拦不住同步路径）；`Job` 无 `(project_id, kind, status)` 唯一约束；`SectionEditLock` 不参与生成互斥。

### 3.4 查重业务链（请求 → Job → Worker → 报告/命中 → 完成 → 前端）

- 请求：`SimilarityRequest` `api/schemas.py:577`；`similarity_check()` `api/routes/similarity.py:38`：先建 `SimilarityReport(status="queued")` `:66-78`，背压 `:85`，建 `Job(kind="similarity", payload={report_id, scope, mode, threshold, ...})` `:88-107`，`submit()` `:112`；无 worker 池时内联 `:121-141`。
- 执行：`_run_similarity_job()` `workers/similarity.py:21`：claim → `run_similarity()` `similarity/service.py:184`（维度 runner `:176`，O(n²) 循环 `:259-276`，命中写入 `:290-306`，统计/done `:308-325`，审计 `:327`）→ `assert_claim_current` `:62` → 新 Session 二次 fence `:69-70` → done `:72-82`。
- 幂等：done 报告直接复用 `service.py:221-223`；未完成报告先删旧 `SimilarityHit` 再重算 `:224-229`。
- 前端：`web/src/api/similarity.ts:136-178`；`web/src/pages/tabs/SimilarityTab.tsx:88/194/233/253`（1.8s 轮询、终态集 `:33-41`）；`web/src/components/SimilarityResult.tsx:14`（确认/原创/重置 `:112-145`）。

## 4. 现有测试覆盖摘要

- 队列/可靠性：`test_jobqueue.py`（19）、`test_reliability.py`（7）、`test_reliability_writer.py`（2）、`test_v030_stage4_fencing.py`（6 个用例、参数化后 16）、`test_v030_stage4_similarity.py`（6）、`test_jobs_unit.py`（5）。
- 账务：`test_billing.py`、`test_fix3_refund.py`（2）、`test_fix3_backend_charges.py`（4）、`test_batch1_charge_refund.py`（9～10）、`test_batch1_pending_charge.py`（5）、`test_v030_stage3_core.py`（8）、`test_v030_stage3_routes.py`（5～7）。
- 查重：`test_similarity_api.py`（6）、`test_similarity_text.py`（6）、`test_similarity_image.py`（6）、`test_similarity_structure.py`（5）、`test_similarity_semantic.py`（4）。
- PostgreSQL（opt-in，需 `BIDCRAFT_TEST_PG_URL`）：`test_v030_pg_isolation.py`（20 轮×4 worker 并发认领 + 双池隔离）、`test_postgres_integration.py`（并发认领/fencing/迁移升级）。
- 回归：`tests/fixtures/regression/` 10 份 **合成** Markdown 夹具 + `baseline.json`（provenance `synthetic-regression-v030-stage6`）；`tests/fixtures/verdict_regression.json` 26 条**手写合成**条款；CI 门禁 `ci.yml:236-240`。
- 真实件现状：`baselines/` 只有 `README.md`，无 `index.json` 或真实基线；`real-tenders/` 被 `.gitignore` 忽略且不存在；`tests/fixtures/tender_cabling.txt` 是通用示例文本，不构成真实脱敏样本集。**阶段 5 不得把合成数据冒充真实样本。**

## 5. 已确认问题（供阶段 1～3 使用）

### Worker fencing / 队列（阶段 1、2）
1. Reaper `reap_expired()`/`recover_on_startup()` 为 SELECT→ORM 写，无条件 UPDATE/行锁；多 reaper 或「心跳续租 vs 回收」可重复 requeue 活任务。
2. `_finalize_after_run()` / `_set_failed()` 存在 TOCTOU：`fenced_job()` 校验通过后到写入前仍可被回收。
3. generation worker 无 `assert_claim_current()`，长 Session 依赖 identity-map `fenced_job()`；6-runner 测试矩阵排除 generation/similarity。
4. 心跳异常只记 debug 日志、不置 abort；续租 UPDATE 无 `lease_expires_at > now` 条件，可给已过期租约续命。
5. `Job.status` 无状态机约束；`_requeue()` 不清 `result/progress`，UI 可能见旧部分结果。
6. `_cancel_stale_jobs`（分钟级 stale）与 lease/reaper（秒级）并行，无交互测试；PG 背压 advisory lock 无并发提交测试。
7. similarity worker 工作会话用裸 `db.get(Job)`，缺少中途检查点；`similarity/service.py:261-266` 的宽泛 `except Exception` 会吞掉未来 `JobAborted`。

### 账务（阶段 3）
1. 异步 `generate_all` 无 PendingCharge marker；退款失败只发事件，无对账兜底，可能永久丢点。
2. `refund_charged_job()` payee 只取 `payload['user_id']`，子账号删除后无法退款（marker 路径已用 `billing_account_id` 解决）。
3. 同项目并发生成无 DB 互斥（详见 3.3），可能双扣费/章节互覆盖/退款归因错乱。
4. 对账按项目章节差值归因，不绑定具体任务；`write` 重生成已有正文时增量为 0，全额退款与 settle 口径不一致。
5. `reconcile_charges --older-than 0/负数` 会立即结清在途 marker；`billing_settings()` 读侧 `chapter_cost` 未 clamp 负值。

### 真实文件与质量（阶段 5、6）
1. 无真实脱敏招标文件、无标注集、无数据集版本管理；现有指标仅章节/问题/BOQ 三项，缺少召回率/可追溯率/人工复核等口径。
2. `quality_metrics.py` 缺数据时返回 `0.0`，无法区分“未测量”与“零质量”；合成夹具的 `min_tables/min_materials` 均为 0，表格门禁实际空转。

## 6. 本轮改造文件清单（预计，按阶段收敛）

- 阶段 1：`bidcraft/jobqueue.py`、`bidcraft/workers/similarity.py`、`bidcraft/similarity/service.py`、`tests/test_worker_fencing_atomicity.py`；新增报告 `docs/development/BIDCRAFT_V031_WORKER_FENCING_REPORT.md`。
- 阶段 2：`bidcraft/jobqueue.py`、`bidcraft/models.py`、`bidcraft/services/job_support.py`、`tests/test_jobqueue_race_conditions.py`；报告 `BIDCRAFT_V031_JOBQUEUE_REPORT.md`。
- 阶段 3：`bidcraft/jobqueue.py`、`bidcraft/models.py`、`bidcraft/services/job_support.py`、`bidcraft/workers/generation.py`、`bidcraft/api/routes/projects.py`、`bidcraft/billing.py`、可能新增 `0012_*` 迁移（**仅新增，不改历史**）、`tests/test_billing_concurrency_v031.py`；报告 `BIDCRAFT_V031_BILLING_CONSISTENCY_REPORT.md`。
- 阶段 4：全 worker 审计 + `tests/test_worker_reliability_matrix.py`；报告 `BIDCRAFT_V031_WORKER_RELIABILITY_REPORT.md`。
- 阶段 5：`tests/fixtures/tender_real/`（需用户提供真实脱敏样本；无样本时只交框架与模板）、`tests/fixtures/tender_real/annotations/`、dataset/loader；报告 `BIDCRAFT_V031_TENDER_DATASET_REPORT.md`。
- 阶段 6：`bidcraft/evaluation/` 扩展、`tests/`、报告 `BIDCRAFT_V031_TENDER_QUALITY_REPORT.md`（无人工标注的指标标记“未测量”）。
- 阶段 7：前端任务/账务/质量状态闭环、API 契约与权限测试。
- 阶段 8：`docs/operations/BIDCRAFT_DATABASE_BACKUP_RESTORE.md`、发布运维检查。
- 阶段 9：全量回归、`docs/development/BIDCRAFT_V031_FINAL_ACCEPTANCE_REPORT.md`、`docs/release-notes/v0.31.0.md`。

## 7. 约束与升级条件

本阶段严格遵守：不覆盖未提交代码、不用 `reset --hard`/`clean -fd`、不删测试、不跳测掩盖问题、不改历史迁移、不 `npm audit fix --force`、不改既有 API 业务语义、保持 SQLite/PG 兼容、不连生产库测试。

下列情形必须暂停并出风险报告：生产 schema/数据变更、账务历史修复、不可逆迁移、生产配置变更、删除业务数据、核心权限规则变化、推送 main、创建 Release、部署生产。**特别是阶段 3 的“同项目并发生成是否禁止”属于业务规则变化，须先确认后再实施。**
