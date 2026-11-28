# BidCraft v0.34.0 RC 专项主任务书

## 1. 范围与基线

- 专项分支：`fix/v0.34.0-commercial-reliability`
- 基线 Commit：`f333c27b86ba6e0ce6b72b0b6a957531dfeaf5d1`
- 基线版本：`0.33.2`
- 基线 Alembic Head：`0016_fact_provenance`
- 基线 CI：Run `46092725093`，9/9 success
- 基线全量回归：`2427 passed, 1008 skipped, 1 warning in 1042.38s`

## 2. 强约束

- 使用当前真实代码，不回滚、不 reset --hard / clean -fd。
- 不推送 main、不建 tag、不创建 Release、不部署生产、不操作生产库/数据。
- 不修改历史迁移 `0001`–`0016`；新增迁移必须幂等、可逆、SQLite/PG 兼容。
- 不删除测试、不降低断言、不用 skip/xfail/mock 掩盖真实失败。
- 不改变扣费规则、VIP 权益、订单语义、导出门禁等业务逻辑。
- 每阶段必须有代码、测试、报告、真实命令与结果。

## 3. 阶段与报告

| 阶段 | 内容 | 报告 |
|---|---|---|
| 0 | 基线保护、架构与风险盘点 | `01_BASELINE_REPORT.md`, `02_RISK_AND_CURRENT_TEST_REPORT.md` |
| 1 | 支付回调安全 | `03_PAYMENT_SECURITY_REPORT.md` |
| 2 | 资金一致性与原子抢占 | `04_FINANCIAL_CONSISTENCY_REPORT.md` |
| 3 | 任务失败退款与租约恢复 | `05_JOB_BILLING_REPORT.md` |
| 4 | 数据库并发与迁移 | `06_DB_CONCURRENCY_REPORT.md` |
| 5 | 文件完整性与上传原子性 | `07_FILE_INTEGRITY_REPORT.md` |
| 6 | 权限、IDOR、审计 | `08_PERMISSION_AUDIT_REPORT.md` |
| 7 | 备份恢复与运维 | `09_BACKUP_RESTORE_REPORT.md` |
| 8 | 前端终态与轮询闭环 | `10_TEST_REPORT.md`（前端部分） |
| 9 | CI 门禁与全量验证 | `11_CI_REPORT.md`, `10_TEST_REPORT.md` |
| 10 | 迁移、部署、回滚与最终验收 | `12_MIGRATION_REPORT.md`, `13_DEPLOYMENT_GUIDE.md`, `14_ROLLBACK_GUIDE.md`, `15_RELEASE_NOTES.md` |

## 4. 当前进展

- 阶段 0：已提交 `69eb538`。
- P0 支付安全、资金原子性、任务退款/租约恢复：实现完成，定向验证通过；合并全量回归 `2535 passed, 1008 skipped, 2 warnings in 988.14s`，0 failures。
- 全量回归第 2 个 warning 为 `tests/test_kb_reindex_job.py` 退出后遗留 worker 线程读取已被重置的测试库（no such table: jobs），属于测试环境线程生命周期噪音，已记录为后续 P1 运维/稳定性清理项，未掩盖任何失败。
- P1：代码与测试已实现，正在做最终全量回归与真实 PostgreSQL 复验。
- P1 本地真实 PostgreSQL（pgserver 16.2）：`1078 passed, 1 skipped, 1 warning in 675.10s`。
- P1 三项首轮红灯已修复：`boq.review_version` 在重试前 flush 未提交状态；`reconcile_pending_charges` 结算计数不再把「0 退款但已 cleared」漏计；PG 并发测试种子账户先充值再扣费；旧 CI 镜像标签断言按 v0340-check 更新并禁止旧标签回退。
- 最终全量后端回归：`2582 passed, 1050 skipped, 2 warnings in 1069.30s`，0 failures。
- 本地真实 PostgreSQL：`1078 passed, 1 skipped, 1 warning`。
- 远程 CI：最终代码 SHA `0698dfd279d5b5a0b4961d57d57d55f160293c82`，run `46123816243`，9/9 jobs success（python lint/pytest、web、postgres real integration、docx render、docker image/compose、dependency audit、bandit、secret scan）。
- 当前 RC 结论：CONDITIONAL PASS / CONDITIONAL GO。P0 全部 PASS；P1 本地 PASS；远程 CI 9/9 PASS；生产等价 PG、Docker 运行时、真实模型/真实招标文件样本 NOT VERIFIED。

## 5. 结论口径

最终验收只允许使用：

- `PASS`
- `FAIL`
- `CONDITIONAL`
- `NOT VERIFIED`

最终建议只允许：

- `GO`
- `CONDITIONAL GO`
- `NO-GO`

任何未运行、无真实环境或数据阻塞的项目必须明确标记，不得写为 PASS。

## 6. 最终验收清单

| 项 | 交付/结果 | 状态 |
|---|---|---|
| 代码基线 | 分支 `fix/v0.34.0-commercial-reliability`，基线 `f333c27`，未回滚/未强推/未 clean | PASS |
| 支付回调安全 | 新鲜度、序列号、签名类型、事件/算法、mchid/appid、金额/币种/交易号强校验；1 MiB 限制 | PASS |
| 资金原子性 | `mark_paid` / `redeem` 条件抢占，交易号唯一索引迁移 0017 | PASS |
| 任务退款/租约 | 先 transition 后退款；requeue 清 token；worker 重取租约 | PASS |
| DB 并发 | 统一锁顺序、条件审批快照、有界退款补偿、PG 并发测试 | PASS（SQLite + 本地 PG） |
| 文件完整性 | 临时文件 + 提交后替换；magic byte + sha256 迁移 0018；孤儿文件清理 | PASS |
| 权限/审计 | 关键配置、计费、兑换、管理员点数与密码重置补齐审计；web_ready 修正 | PASS |
| 备份/运维 | pg_dump 一致性、manifest 哈希、恢复演练 opt-in、文档更新 | PASS（本地） |
| 前端终态 | watchJob / BillingPage 终态闭环与防重复提交 | PASS |
| CI | 9 job、强门禁、PG 并发文件显式收集、/health/ready 冒烟 | PASS（本地 + 远程 run 46123816243 9/9） |
| 迁移 | 0017、0018 幂等可逆；0001–0016 零 diff | PASS（本地） |
| API/前端兼容 | 路由数与业务语义不变；前端契约测试通过 | PASS |
| 后端全量 | 2582 passed / 1050 skipped / 2 warnings / 0 failures | PASS |
| Web | 68 files / 499 tests，typecheck / lint / build | PASS |
| PG 真实验证 | 本地 pgserver 16.2 全套件 1078 passed | PASS（本地） |
| 生产等价环境 | 生产 PG/PgBouncer、Docker 运行时、生产恢复演练 | NOT VERIFIED |
| 数据阻塞项 | 真实招标文件、专家评分、真实模型质量 | NOT VERIFIED |
| 最终发布决策 | 合并 main / tag / Release 需单独授权 | CONDITIONAL GO |

远程 CI 首轮 run `46118559049` 在 2 个 job 失败：python pytest 缺少 web_dist 构建产物；runner 自带 pg_dump 18 不支持 `--single-transaction`。已分别通过 `python-tests` job 增加 Node 22 + npm ci/build、以及 `bidcraft/cli.py::_pg_dump_supports()` 版本探测修复，并新增 2 个静态/单元测试防止回退。第二轮 run `46122775578` 剩余 PG JUnit classname 断言误报与 DraftTab 异步加载抖动，也已修复。最终 run `46123816243` 对 SHA `0698dfd` 9/9 jobs success。

未解决问题：生产等价 PG/PgBouncer、Docker 运行时与生产恢复演练仍 NOT VERIFIED；真实模型/真实招标文件样本仍 NOT VERIFIED；kb_reindex worker 线程退出后读取已重置测试库的警告（测试生命周期噪音）。

回滚：按阶段反向 revert；迁移 0017、0018 自带 reversible downgrade；生产禁止未经批准直接 downgrade。
