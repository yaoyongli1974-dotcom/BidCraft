# BidCraft v0.34.0 RC 发版说明（专项报告）

正式用户发版说明见 `docs/release-notes/v0.34.0.md`；本文件记录 RC 专项验收口径。

## 1. 主题

商业化安全、账务一致性与生产可靠性专项收口。

## 2. RC 范围结论

| 模块 | 结论 |
|---|---|
| 支付回调与 mock 支付 | PASS：签名新鲜度/序列号/事件/字段强校验，mock-pay 渠道隔离 |
| 资金原子性 | PASS：`mark_paid` / `redeem` 条件抢占，交易号唯一索引 |
| 任务退款/租约恢复 | PASS：先抢状态再退款，requeue 清理并重取 GenerationLease |
| DB 并发/审批并发 | PASS（SQLite + 本地真实 PG）：统一锁顺序、条件审批快照、有界退款补偿 |
| 文件完整性/上传原子性 | PASS：临时文件 + 成功后替换、magic byte + sha256、证据移交/注销清理 |
| 权限/审计 | PASS：关键配置、计费、兑换、点数调整补齐审计；web_ready 路径修正 |
| 备份/运维 | PASS（本地）：pg_dump 一致性、manifest 哈希、web 文档更新 |
| 前端终态 | PASS：watchJob 与 BillingPage 终态闭环、防重复提交 |
| CI | 本地 PASS：9 job、强制门禁、PG 并发文件显式收集；远程 CI 待最终 SHA 取证 |
| 生产等价环境 | NOT VERIFIED：生产 PG/PgBouncer、Docker 运行时、真实恢复演练需用户环境确认 |

## 3. 迁移

- `0017_transaction_id_unique`
- `0018_file_checksums`

均幂等可逆，未改历史迁移。

## 4. 验证数字

- 阶段 0 基线：`2427 passed / 1008 skipped / 1 warning`
- P0 合并全量：`2535 passed / 1008 skipped / 2 warnings`
- 本地真实 PostgreSQL：`1078 passed / 1 skipped / 1 warning`
- P1 定向后端合并：`218 passed / 44 skipped / 2 warnings`
- Web：`68 files / 499 tests`，typecheck/lint/build 通过
- 最终全量后端：`2580 passed / 1050 skipped / 2 warnings in 1064.39s`，0 failures
- 远程 CI：最终 SHA 取证后进行，合并 main 前必须 9/9 success

## 5. 已知边界

- 不包含真实招标文件样本、专家评分、真实模型质量验收。
- 不开放远程插件市场、容器级隔离。
- 历史文件缺 checksum 时跳过校验；旧证据占位符保留纯文本回退。
- 最终发版必须先取得最终 SHA 的远程 CI 9/9，并单独授权 main/tag/Release。
