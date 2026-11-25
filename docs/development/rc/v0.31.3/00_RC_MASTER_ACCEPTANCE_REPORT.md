# v0.31.3 RC 生产候选环境主验收报告

Branch: `fix/v0.31.3-production-readiness`  
Commit SHA: `978497247f033e79071a7b3e6692f712f58f04cc`（G0 冻结业务基线；后续 CI 修复与 RC 报告提交仅改 CI 冒烟脚本与报告，业务代码未变）  
Version: `0.31.3`  
RC Environment: `/tmp/opencode/rc-v0313-home`（隔离配置 `/tmp/opencode/rc-v0313.env`，chmod 600；未连接生产）  
PostgreSQL: 隔离 pgserver PostgreSQL 16.2（RC 库 TCP `127.0.0.1:5432`；CI 使用 `postgres:16-alpine`）  
Docker: RC 本机无 Docker CLI（本地 NOT VERIFIED）；CI docker job 真实构建镜像、启动容器并探测 `/health/live` SUCCESS  
CI Run: `45570895467` — https://github.com/yaoyongli1974-dotcom/BidCraft/actions/runs/45570895467 — 9/9 SUCCESS

## 1. 验收概览

| Gate | 状态 | 关键证据 |
|---|---|---|
| G0 基线 | A PASS | SHA/版本/Alembic head/工作区一致 |
| G1 CI | A PASS | Run 45570895467，9/9 SUCCESS |
| G2 RC 环境 | A PASS（PG）/ D（Docker 本机） | 隔离 PG 16.2 + RC env；无本地 Docker |
| G3 Docker 部署 | D NOT VERIFIED（本机）/ A（CI） | CI docker job 成功；本机无 CLI |
| G3.1 数据库迁移 | A PASS | RC PG `init_db()` → 0012；56 表/版本一致 |
| G4 核心 E2E | A PASS | health/login/project/requirements/BOQ/evaluation/similarity 全通过 |
| G5 Worker 故障恢复 | A PASS | 561 PG passed |
| G6 备份恢复 | A PASS（数据库）/ D（文件系统/生产） | backup-verify 56 表 9 行；SQL 还原到隔离库成功 |
| G7 性能稳定性 | A PASS（RC）/ D（生产） | 300 GET P50 14.16ms / P95 15.45ms；30 POST P50 73.33ms |
| G8 安全 | A PASS（RC）/ D（生产账号/密钥） | 15 安全测试 + 3 PG 权限测试 + CI 安全作业 |
| G8 监控告警 | A PASS（探针/指标）/ D（告警） | health/live/ready/admin health 200；无外部告警验证 |
| G8 AI 质量 | D NOT VERIFIED | 无真实模型/真实标注样本 |
| G9 最终决策 | CONDITIONAL GO | 见第 6 节 |

## 2. P0

P0 PASS。无未关闭 P0。

- 首个 CI run 的 docker 冒烟失败与第二个 CI run 的 shell 注释吞参数失败均为 CI harness 问题，已用最小修复关闭，最终 run 9/9 SUCCESS。
- 未发现数据丢失、账务不一致、权限越权或不可逆迁移问题。

## 3. P1

| 项 | 状态 | 说明 |
|---|---|---|
| Docker 本机运行时 | CONDITIONAL | CI 已验证；目标主机需部署窗口复验 |
| 生产 PostgreSQL 负载 | CONDITIONAL | RC/CI PG 通过，生产需观察 |
| 生产备份恢复 | CONDITIONAL | 隔离库还原通过，生产演练未做 |
| 告警链路 | CONDITIONAL | 探针与指标可用，通知/阈值未验证 |
| 真实 AI 质量 | CONDITIONAL | 流程可用，真实模型/样本缺失 |
| 安全配置 | CONDITIONAL | RC 通过，生产密钥/代理未验 |

## 4. NOT VERIFIED

- 生产环境 PostgreSQL 压力、慢查询、锁等待和长稳。
- 目标主机 Docker/compose 启动、健康检查、资源限制实际行为。
- 生产备份/恢复、文件系统数据目录、日志采集和告警通知。
- 真实模型生成质量与真实脱敏招标文件的召回/一致率。

## 5. 风险

见 `12_RISK_REGISTER.md`。当前无未关闭 P0；R01～R04 为 CONDITIONAL GO 的必要条件。

## 6. GO / CONDITIONAL GO / NO-GO

**CONDITIONAL GO**。允许进入生产上线准备，但上线前必须关闭 R01～R04：目标主机 Docker 健康验证、生产/预发 PG 观察、真实样本 AI 质量补测、告警通知演练。

## 7. 后续项

1. 在目标主机执行 compose config、容器启动与 `/health/live` 探测。
2. 在预发/生产只读窗口观察 PG 锁、慢查询、队列积压。
3. 用户提供脱敏真实招标文件与标注后补跑 AI 质量指标。
4. 配置并演练告警通知、日志采集和 request-id 聚合。
5. 增加真实数据目录的备份恢复演练。

## 8. 最终结论

CONDITIONAL PASS
