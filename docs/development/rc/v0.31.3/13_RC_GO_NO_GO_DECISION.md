# RC Go / No-Go 决策（v0.31.3）

## 决策头

| 字段 | 值 |
|---|---|
| Branch | `fix/v0.31.3-production-readiness` |
| Commit SHA | `978497247f033e79071a7b3e6692f712f58f04cc`（G0 冻结业务基线；后续 CI 修复与 RC 报告提交仅改 CI 冒烟脚本与报告，业务代码未变） |
| Version | 0.31.3 |
| RC Environment | `/tmp/opencode/rc-v0313-home`，隔离 PostgreSQL 16.2（TCP `127.0.0.1:5432`），假模型地址，无生产数据/账号/充值 |
| PostgreSQL | 隔离 pgserver 16.2；迁移 head `0012_generation_lease`；备份 56 表/9 行，恢复到 `bidcraft_rc_restore` 成功 |
| Docker | 本地无 Docker CLI → D NOT VERIFIED；CI `docker (production image + compose hardening)` SUCCESS（含容器启动与 `/health/live` 探针） |
| CI Run | `45570895467`，9/9 jobs SUCCESS，URL https://github.com/yaoyongli1974-dotcom/BidCraft/actions/runs/45570895467 |

## 决策结论

**CONDITIONAL GO（有条件进入生产发布流程）**；最终结论：**CONDITIONAL PASS**。

依据：P0 全部通过；未发现数据丢失、账务错误、权限越权或不可逆迁移问题。P1 中的本地 Docker 运行时、真实 PostgreSQL 生产负载、真实模型质量与真实投标人标注样本未在 RC 环境完整验证，必须在生产发布前/发布首日作为条件项闭环。

## P0 状态

| 项 | 结论 |
|---|---|
| G0 基线冻结与 SHA 核验 | PASS |
| G1 远程 CI（目标业务基线与修复后 SHA） | PASS（修复后 9/9 SUCCESS） |
| G2 隔离 RC 环境 | PASS（PG 隔离；Docker 本地能力缺失但 CI 覆盖） |
| G3 / G3.1 部署与迁移 | RC PG PASS；本地 Docker NOT VERIFIED；CI 部署静态/运行时冒烟 PASS |
| G4 核心业务 E2E | PASS |
| G5 Worker 失败/恢复 | PASS（561 PG 并发用例） |
| G6 备份恢复演练 | RC PG PASS；生产恢复与文件系统数据 NOT VERIFIED |
| G7 性能/稳定性 | RC 隔离环境 PASS；生产负载/长稳 NOT VERIFIED |
| G8 安全/监控/AI 质量 | 测试口径 PASS；生产配置、真实模型与真实件质量 NOT VERIFIED |

## P1 状态

| 项 | 结论 |
|---|---|
| CI Docker 冒烟脚本配置（模型配置缺失） | PASS（已修复，CI 全绿） |
| 安全门禁强制化与依赖/SAST/密钥扫描 | PASS（CI 9/9） |
| RC PG 权限/IDOR 与 15 项安全回归 | PASS |
| 生产代理、密钥、账号与租户数据 | CONDITIONAL（生产环境核验） |
| 生产日志采集、外部告警、长稳 | CONDITIONAL / NOT VERIFIED |
| 真实模型生成质量 | NOT VERIFIED（RC 使用不可达假模型地址） |
| 真实招标文件与人工标注 | NOT VERIFIED（未提供，指标保持未测量） |

## NOT VERIFIED

- 本地 Docker 容器运行时健康探针（本地无 CLI；CI 已 SUCCESS）。
- 生产 PostgreSQL 负载、并发峰值和长稳 soak。
- 生产备份恢复到独立生产同构环境；备份中配置文件系统目录为 0。
- 生产代理配置、真实密钥、账号权限与租户隔离数据。
- 外部告警通道与生产日志留存。
- 真实模型质量与真实件判定一致率。

## 主要风险

见 `12_RISK_REGISTER.md`：R01 PG 生产负载、R02 本地 Docker、R03 真实模型、R04 真实件样本为 CONDITIONAL GO 的主要驱动；无未关闭 P0。

## 后续项

1. 生产发布前：在目标生产同构 PG 上运行只读预检与迁移 dry-run，确认备份/恢复演练。
2. 生产发布时：执行容器 `/health/live`、`/health/ready` 与 `/admin/health` 核验，确认 embedded workers 状态。
3. 生产首日：观察 PG 慢查询、队列积压、Worker 失败率、日志与告警通道。
4. 用户提供脱敏真实招标文件与应答标注后，补测 AI 质量指标，替换「未测量」口径。
5. 任何进入 main / 打 tag / 创建 Release / 生产部署动作需单独授权。

## 决策签名口径

本报告是 RC 验收决策，不授权合并、推送 main、创建 tag/Release、生产部署或生产数据变更。
