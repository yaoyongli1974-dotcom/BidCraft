# BidCraft v0.34.0 阶段 1：风险清单与现状测试报告

## 1. 依据

本报告只记录当前真实代码（`fix/v0.34.0-commercial-reliability` @ `f333c27`）中由只读审计确认的问题。已实现能力不重复列为缺口。

## 2. P0 风险（必须修复）

| ID | 风险 | 真实位置 | 证据/影响 | 计划 |
|---|---|---|---|---|
| V0340-P0-01 | 微信支付 `mark_paid` 先读后写，无数据库级状态抢占 | `bidcraft/billing.py:394-437`, `bidcraft/api/routes/billing.py:195-215` | 并发 SUCCESS 通知可重复入账；现有测试仅顺序重复 | 条件 UPDATE `WHERE status='pending'` + rowcount；交易号唯一索引 |
| V0340-P0-02 | 兑换码先读后写，可并发重复兑换 | `bidcraft/billing.py:477-497` | 同一码并发兑换两次、双份点数 | 条件 UPDATE `WHERE status='unused'` + rowcount + 过期条件 |
| V0340-P0-03 | 回调缺少金额类型、币种、商户号、AppID、交易号、事件及新鲜度强校验 | `bidcraft/wechatpay.py:98-141`, `bidcraft/api/routes/billing.py:170-218` | 伪造/重放/错配回调可能错误入账或 500 | 强校验所有字段；拒绝 bool/非法浮点；缺字段拒绝 |
| V0340-P0-04 | mock-pay 未校验订单 provider | `bidcraft/api/routes/billing.py:118-130` | 临时开启 mock 可零成本支付真实微信订单 | 仅 `provider='mock'` 且条件 UPDATE 抢占 |
| V0340-P0-05 | 失败任务退款路径与状态抢占顺序不一致 | `bidcraft/services/job_support.py:41-71` vs `bidcraft/jobqueue.py:741-761` | 已完成任务可被错误退款；终态补退窗口 | 先 transition 抢 failed，再退款；终态补退仅确认未成功 |
| V0340-P0-06 | 恢复后 GenerationLease token 永久失效 | `bidcraft/jobqueue.py:777-821,854,891`, `bidcraft/workers/generation.py:220,232,358-366` | 新 Worker 心跳必 lost→abort→重试耗尽→失败退款 | requeue/claim 时重取租约并回写 payload，或清 token 由 worker 重取 |

## 3. P1 风险（完成或明确风险）

| ID | 风险 | 真实位置 | 计划 |
|---|---|---|---|
| V0340-P1-01 | 回调 body 无大小限制，未纳入敏感限流 | `bidcraft/api/routes/billing.py:170-174`, `bidcraft/api/app.py:454-464` | 1 MiB 限制；notify 加入 sensitive bucket |
| V0340-P1-02 | 平台证书配置缺失仍可下单，回调必失败 | `bidcraft/wechatpay.py:27-36` | configured 增加平台证书检查 |
| V0340-P1-03 | 已支付交易号无唯一约束 | `bidcraft/models.py:944-962` | 迁移新增部分唯一索引，先检测历史重复 |
| V0340-P1-04 | pending 订单无过期关闭，closed 回调 500 | `bidcraft/billing.py:394-398`, routes billing | 订单状态机与过期处理；异常状态回调返回 SUCCESS+告警 |
| V0340-P1-05 | 缺少 provider 主动对账 | `bidcraft/billing.py`, `bidcraft/cli.py` | 新增只读/确认式订单对账 |
| V0340-P1-06 | `admin_stats.revenue_cents` 混入 mock/admin 且未扣退款 | `bidcraft/billing.py:519-535` | 仅统计真实渠道净额 |
| V0340-P1-07 | 对账单 CSV 列语义错位 | `bidcraft/compliance.py:622-638` | 拆金额/点数/状态/交易号/渠道列 |
| V0340-P1-08 | 关键资金动作缺审计 | `bidcraft/billing.py:405-426,477-497`, admin 改点 | 统一 record_audit |
| V0340-P1-09 | marker TTL 对账误退排队任务 | `bidcraft/jobqueue.py:1490-1575` | 关联 Job 仍 queued/running 则 touch/skip |
| V0340-P1-10 | PG 两条退款路径加锁顺序相反 | `bidcraft/jobqueue.py:1433-1459,1344-1380` | 统一 job→marker→User 顺序 |
| V0340-P1-11 | 失败退款无自动补退 | `bidcraft/services/job_support.py:48-95` | reaper/reconcile 扫 failed unpaid job |
| V0340-P1-12 | 同步生成无逐章 abort fence | `bidcraft/api/routes/projects.py:2131-2154,2266-2285` | before_chapter 检查 lease/abort |
| V0340-P1-13 | `/admin/health` 的 `web_ready` 路径错误 | `bidcraft/api/routes/admin.py:1681` | 改 `parents[2]` 并加测试 |
| V0340-P1-14 | PG 备份缺 `--single-transaction`、manifest 无 DB/secret 哈希 | `bidcraft/cli.py:1364-1410`, `bidcraft/compliance.py:645-746` | 加一致性标志与哈希校验 |
| V0340-P1-15 | 恢复演练默认跳过，CI 未执行 | `tests/test_fix3_backup_drill.py`, `.github/workflows/ci.yml` | CI 独立步骤强制运行 |
| V0340-P1-16 | watchJob 不识别 cancelled/终态快照；BillingPage 终态轮询不停 | `web/src/hooks/watchJob.ts`, `web/src/pages/BillingPage.tsx` | 前端终态与防重复提交修复 |
| V0340-P1-17 | 备份手册版本/Alembic head 过期 | `docs/operations/BIDCRAFT_DATABASE_BACKUP_RESTORE.md` | 更新到 0.34.0 / 迁移 head |
| V0340-P1-18 | 审批记录 JSON 读改写，两个会签人并发可互相覆盖 | `bidcraft/boq.py:1504-1560`, `bidcraft/commercial.py:1285-1338` | 条件重读 + 追加审批；并发双审批测试 |
| V0340-P1-19 | 先落盘后入库/后校验，DB 回滚或 429 后遗留孤儿文件 | `bidcraft/user_docs.py:106-140`, `bidcraft/api/routes/my.py:45-87`, `bidcraft/qualifications.py:499-524`, `bidcraft/boq.py:665-679` | 临时文件 + 成功后 replace / 回滚清理；快照回滚保留文件 |
| V0340-P1-20 | 共享资质移交企业 owner 后证据文件路径失效，注销后证据目录残留 | `bidcraft/compliance.py:213-224,241-246`, `bidcraft/qualifications.py:83-89,512,538,575` | 移交时同步搬移文件并改写路径；注销时清理证据目录 |
| V0340-P1-21 | 个人文档/证据仅按后缀校验，无完整性 sha256 | `bidcraft/user_docs.py:113-125`, `bidcraft/qualifications.py:502-514` | magic byte 白名单 + checksum 列（新迁移）；读取时校验 |

## 4. 现状测试

- 前端基线：67 files / 487 tests、typecheck、lint、build 全通过。
- 后端基线：`.venv/bin/python -m pytest -o addopts= -q` → `2427 passed, 1008 skipped, 1 warning in 1042.38s`，0 failures。
- 远程 CI：基线 SHA `f333c27` Run `46092725093` 9/9 success。

## 5. 边界

- 不通过删除断言、skip、mock 关键支付/账务逻辑消除红灯。
- 不擅自改变扣费策略、VIP 权益规则或历史订单语义。
- 不修改历史迁移 `0001`–`0016`。
- 不在生产执行破坏性恢复测试。
