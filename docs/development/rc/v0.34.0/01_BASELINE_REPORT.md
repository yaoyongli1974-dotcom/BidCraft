# BidCraft v0.34.0 阶段 0：代码基线与架构审查报告

## 1. 基线信息

| 项 | 实际值 |
|---|---|
| 仓库 | `yaoyongli1974-dotcom/BidCraft` |
| 工作目录 | `/home/yao/project` |
| 基线分支 | `main` |
| 基线 Commit | `f333c27b86ba6e0ce6b72b0b6a957531dfeaf5d1` |
| 专项分支 | `fix/v0.34.0-commercial-reliability`（从基线创建，未推送） |
| 版本 | `0.33.2`（`bidcraft/__init__.py`、`pyproject.toml`、`web/package.json`） |
| Alembic Head | `0016_fact_provenance`（17 个版本文件，`tests/test_alembic.py` 同步） |
| Python | 3.13.5 |
| SQLAlchemy | 2.1.8 |
| psycopg | 3.3.9 |
| Node / npm | v26.13.1 / 11.20.0 |
| CI | 基线 SHA 最新 CI Run `46092725093`，9/9 jobs success |

## 2. 工作区与分支保护

- 执行前 `git status --short` 为空。
- 未覆盖任何用户未提交修改。
- 未修改历史迁移 `0001`–`0015`。
- 未推送 main、未创建 tag、未创建 Release、未部署生产。

## 3. 当前代码规模

| 范围 | 实际数量 |
|---|---:|
| `bidcraft/**/*.py` | 145 |
| `tests/test_*.py` | 241 |
| `web/src/**/*.test.*` | 67 |
| Alembic migration 文件 | 17 |

## 4. 基线测试

### 4.1 前端

执行：

```bash
cd web && npm run typecheck && npm run lint && npm test -- --run && npm run build
```

结果：

- typecheck 通过
- lint 通过
- 67 files / 487 tests passed
- Vite 8 build 通过，1720 modules
- `ErrorBoundary.test.tsx` 输出 “渲染炸了” 是预期错误边界测试日志

### 4.2 后端

执行：

```bash
.venv/bin/python -m pytest -o addopts= -q
```

结果：

- `2427 passed, 1008 skipped, 1 warning in 1042.38s`
- 0 failures；唯一 warning 为 Starlette/httpx TestClient 弃用提示
- 输出：`/tmp/opencode/v0340_baseline_full.out`

## 5. 已确认的真实模块结构

本轮严格按当前仓库真实代码改造，不存在任务书中假设的 `backend/` 目录；后端代码位于 `bidcraft/`。

### 5.1 支付与账务

| 模块 | 文件 |
|---|---|
| 支付回调 Handler | `bidcraft/api/routes/billing.py::wechat_notify` |
| 微信签名/解密 | `bidcraft/wechatpay.py::_verify_signature`, `parse_notification` |
| 订单/账务 Service | `bidcraft/billing.py::create_order`, `create_vip_order`, `mark_paid`, `add_credits`, `charge_chapters`, `refund_chapters` |
| 数据模型 | `bidcraft/models.py::RechargeOrder`, `CreditTransaction`, `User` |
| 前端账务 | `web/src/pages/BillingPage.tsx`, `web/src/api/billing.ts` |

### 5.2 AI 任务与账务

| 模块 | 文件 |
|---|---|
| 任务队列 | `bidcraft/jobqueue.py` |
| Worker | `bidcraft/workers/*.py` |
| 失败/退款辅助 | `bidcraft/services/job_support.py` |
| 章节生成 | `bidcraft/generation/writer.py`, `bidcraft/workers/generation.py` |
| 任务路由 | `bidcraft/api/routes/projects.py` |

### 5.3 数据库、文件、权限、运维

| 模块 | 文件 |
|---|---|
| 数据库初始化/迁移 | `bidcraft/db.py`, `bidcraft/migrations/versions/` |
| 上传/文件 | `bidcraft/pipeline.py`, `bidcraft/images.py`, `bidcraft/api/routes/projects.py` |
| 权限 | `bidcraft/authorization.py`, `bidcraft/api/shared.py` |
| 审计 | `bidcraft/audit.py`, 各业务路由 |
| 备份 | `bidcraft/cli.py`, `bidcraft/compliance.py`, `docs/operations/BIDCRAFT_DATABASE_BACKUP_RESTORE.md` |
| 健康检查 | `bidcraft/api/routes/health.py`, `bidcraft/health.py` |

## 6. 当前 CI 状态

基线 SHA `f333c27` 最新 CI Run `46092725093`：

- python (lint + static checks)：success
- security (bandit SAST)：success
- web (lint + test + build)：success
- security (dependency audit)：success
- python (pytest + regression)：success
- docx (CJK render + fidelity)：success
- postgres (real integration)：success
- docker (production image + compose hardening)：success
- security (secret scanning)：success

## 7. 本轮风险盘点依据

后续阶段报告将基于真实代码逐项记录：

1. 支付回调字段、签名、金额、币种、商户号、AppID、状态交易号校验。
2. `mark_paid` 原子状态转换与并发幂等。
3. `add_credits` / `charge_chapters` / `refund_chapters` 事务与流水一致性。
4. AI 任务扣费、退款、租约、Fencing Token、步骤幂等与恢复。
5. SQLite / PostgreSQL 迁移、并发与事务兼容。
6. 上传原子落盘、文件哈希、孤儿文件检测、删除安全。
7. 权限矩阵、IDOR、审计。
8. 备份恢复演练、健康检查与监控。

## 8. 已知限制

- 本地无生产 PostgreSQL、Docker、LibreOffice，真实生产等价验证需远程 CI 或用户提供环境。
- 本报告仅确认基线，不代表 P0/P1 已修复。
- 后续所有改造必须有真实代码依据、测试证据和阶段报告。
