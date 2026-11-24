# BidCraft v0.30.0 阶段 8 风险报告：可观测性与容器部署

- 分支：`quality/v0.30.0-reliability`，HEAD `9b3109e`（阶段 7 已提交）。
- 触发原因：阶段 8 中部分修复会改变**生产部署配置 / 启动校验行为 / 日志格式**，且新增 HTTP 路由；按任务书“定义风险需暂停并出报告”执行。
- 约束：本阶段仍未推送 main、未发版、未改生产环境；以下均为仓库内代码/配置改动提案。

## 1. 审计来源

- 可观测性审计：`docs/development/BIDCRAFT_V030_STAGE8_REPORT.md`（待阶段结束时编写实施结论）。
- 核心发现（V030-17）：
  - P1 `/health` 单端点同时承担存活与就绪，DB 抖动会触发容器重启；缺 `/health/live`、`/health/ready`。
  - P2 任务日志缺 `job_id/kind/project_id/worker_id`，后台任务无法与请求关联。
  - P2 核心指标缺 `workers_running` / 队列积压 / 陈旧 running 任务。
  - P3 `open_findings` 使用 Issue 状态词（`new`/`reopened`），与 QualityFinding 实际 `open/handled/ignored` 不一致；`app.py` 中 `/admin/health` monkey-patch 已失效；worker logger 名统一为 `bidcraft.api`；uvicorn access log 无 request id。
- 核心发现（V030-18）：
  - P1 `deploy/docker-compose.yml` 未使用 `env_file: .env`，`BIDCRAFT_ENV=prod`、`BIDCRAFT_DB_URL`、`BIDCRAFT_SECRET_KEY` 等全部未进入容器，生产加固静默失效。
  - P2 镜像缺 `pg_dump` / `psql`，PG 部署无法执行 `bidcraft backup` 与恢复演练。
  - P2 `stop_grace_period` 10s 小于应用 30s 优雅退出窗口，重启可能 SIGKILL 在途任务。
  - P2 仅限制内存，缺 `cpus` / `pids_limit`。
  - P2 CI 不构建生产镜像、不校验非 root/能力丢弃/工具可用性。
  - P2 启动自动迁移无强制备份前置；文档矛盾（直接 cp 运行中 SQLite vs 使用 `bidcraft backup`）。
  - P3 缺 `no-new-privileges` / `cap_drop: ALL`；基础镜像为浮动标签。

## 2. 风险分类与影响

| 项 | 风险类别 | 影响 | 是否可回滚 |
|---|---|---|---|
| A 包：任务日志字段、核心指标、状态词、删除无效 wrapper、worker logger 名 | 无（仅日志/指标语义） | `open_findings` 历史计数可能变化；日志字段名新增，旧字段保留 | 是（代码回滚） |
| B 包：访问日志 request-id 关联 | 生产日志格式变化 | 运维日志解析需适配；现有 `X-Request-ID` 响应头不变 | 是（uvicorn 配置回滚） |
| C 包：`/health/live`、`/health/ready` + Dockerfile/compose 探针 | 新增 API（2 条路由，旧 `/health` 保留）+ 生产部署配置变化 | LB/编排需改用新探针；旧探针继续可用；错误配置可能导致就绪失败 | 是（配置/路由回滚） |
| D 包：`env_file` 注入、`stop_grace_period`、CPU/PID/能力限制、`postgresql-client` | 生产部署配置/启动校验行为变化 | 现有 `.env` 未启用的变量会开始生效；需确认 `.env` 中无敏感误配；CPU 限流可能影响峰值吞吐 | 是（compose/Dockerfile 回滚） |
| E 包：CI 镜像构建、迁移前备份文档与离线 SQL 预检 | CI/文档，不改生产 | CI 时间增加；无运行时行为变化 | 是（CI/文档回滚） |

## 3. 推荐方案

- **推荐：A + C + D + E 全做，B 一并做（若接受日志格式变化）**。
- 保守替代：只做 A + E；探针、env 注入、容器加固留待单独运维窗口。
- 无论哪种方案：不修改历史迁移；不删除测试；不降低任何扫描门禁；不推送 main、不发版。

## 4. 用户决策（已确认）

用户选择：**A + B + C + D + E，但 D 中排除 `env_file` 注入**。

- 实施范围：代码级日志字段与核心指标、访问日志 request-id、新增 `/health/live` 与 `/health/ready`（保留旧 `/health`）、Dockerfile HEALTHCHECK 指向存活探针、compose 停止宽限期/CPU/PID/能力限制、`postgresql-client`、CI 镜像构建与离线迁移预检、健康面板同步。
- 明确不实施：`deploy/docker-compose.yml` 增加 `env_file: .env`；容器内环境变量注入方式维持原样，避免原本被忽略的 `.env` 变量突然生效。
- 所有改动仅在 `quality/v0.30.0-reliability` 分支，不推送 main、不发版、不改生产环境。
