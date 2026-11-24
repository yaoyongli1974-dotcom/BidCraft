# v0.30.0 阶段 8 报告：可观测性、健康探针与容器加固

分支 `quality/v0.30.0-reliability`，基线 `9b3109e`（阶段 7 已提交）。
风险报告：`docs/development/BIDCRAFT_V030_STAGE8_RISK_REPORT.md`；用户批准范围 **A+B+C+D+E，但 D 排除 `env_file`**。

## 1. 改动范围

### A. 统一任务日志字段（P2）
- `bidcraft/config.py`：`JOB_LOG_FIELDS = (job_id, kind, project_id, worker_id)`，ContextVar 绑定/复位 `_JobContextFilter`，JSON 日志结构增加四个字段（未绑定为 `-`），调用方 `extra` 优先。
- `bidcraft/jobqueue.py`：`execution_context()` 在 worker 线程绑定并复位上述字段；日志模板与 `extra` 保持一致。
- `bidcraft/workers/{agent,compliance,evaluation,export,generation,images,knowledge,similarity}.py`：logger 统一为 `bidcraft.workers`，关键节点使用含任务字段的消息模板。

### B. 全局指标与 finding 词表（P2/P3）
- `bidcraft/health.py::global_metrics()` 新增 4 个键：`workers_running`（bool）、`queued_jobs`、`oldest_queued_age_seconds`、`stale_running_jobs`，总计 10 键；`open_findings` 改为使用 QualityFinding 的真实词表 `open`（+兼容 `pending`），不再误用 Issue 的 `new/reopened`。
- `bidcraft/api/routes/admin.py` 已在自身 session 内注入 `global_metrics`；`bidcraft/api/app.py` 删除失效的 monkey-patch 包装（health 路由侧已是唯一来源），并把 `/health`、`/health/live`、`/health/ready` 加入公开路径。
- `bidcraft/cli.py::serve` 注入带 request-id 过滤的 uvicorn 访问日志 `log_config`。

### C. 存活/就绪探针拆分（P1）
- `bidcraft/api/routes/health.py` 新增公开 `GET /health/live`（固定 200，不查库）与 `GET /health/ready`（DB 可读 + 模型可用/离线豁免 + Web 产物存在，否则 503），保留原 `/health` 响应格式不变。同时修正 `/health` 与就绪探针的 Web 产物路径：由 `api/web_dist` 改为实际构建目录 `bidcraft/web_dist`（`parents[2]`）；本地离线模式实测 `/health/ready` 返回 200。
- `tests/test_api_package.py` 路由总数 250 → 252；`tests/test_v030_stage6_verdict.py` 的 admin_health 指标断言由 6 键更新为 10 键（不是删除测试）。

### D. 容器加固（用户排除 `env_file`）
- `Dockerfile`：安装 `postgresql-client`（`pg_dump`/`psql`），HEALTHCHECK 指向 `/health/live`，保留非 root 用户。
- `deploy/docker-compose.yml`：`stop_grace_period: 45s`、`cpus: "2.0"`、`pids_limit: 256`、`cap_drop: [ALL]`、`no-new-privileges:true`；原生 healthcheck 为 `/health/live`，`x-liveness-probe`/`x-readiness-probe` 为 `/health/live` 与 `/health/ready` 扩展；**未添加 `env_file:`，`environment:` 未改**。

### E. CI 镜像构建与迁移预检 + 文档/UI
- `.github/workflows/ci.yml`：新增 `docker-image` job（构建镜像、`docker inspect` 非 root/HEALTHCHECK、`pg_dump --version`、`bidcraft version`、compose 静态解析）；`python-lint` 新增 Alembic 预检（离线 SQL 失败时退回临时 SQLite `create_all + stamp + upgrade`，不动运行时启动行为）。原有门禁与阈值未降低。
- `web/src/lib/types.ts` + `web/src/pages/admin/AdminHealthTab.tsx` + 测试：展示 4 个新指标，`workers_running=false` 或 `stale_running_jobs>0` 时高亮告警；布尔值显示“是/否”。
- 文档 `deploy/README.md`、`docs/postgresql.md`、`docs/admin-guide.md`：说明 live/ready 语义、`.env` 必须显式 `--env-file`/`environment` 传递、迁移前强制 `bidcraft backup` + `backup-verify`、容器资源限制、结构化任务日志与 request-id；digest 固定/`read_only` 仍列为延期项。

## 2. 11 项验收清单

| # | 验收项 | 证据 | 结果 |
|---|---|---|---|
| 1 | 不推送 main / 不发版 / 不改生产 | 仅本地分支提交，下一节给出 commit | ✅ |
| 2 | 不改历史迁移、不删测试 | `migrations/versions/` 无改动；仅为新增测试/更新断言 | ✅ |
| 3 | ruff 检查 | `ruff check bidcraft tests scripts` 全绿 | ✅ |
| 4 | git diff 检查 | `git diff --check` clean | ✅ |
| 5 | 任务日志字段统一 | `config.py` ContextVar/Filter + 8 个 worker logger 改名，定向测试覆盖 | ✅ |
| 6 | 全局指标 10 键 + 词表修正 | `test_global_metrics_ten_keys_include_queue_health`、`test_fix3_health.py` | ✅ |
| 7 | live/ready 探针与 503 分支 | `test_live_and_ready_probes` + DB/模型故障用例 | ✅ |
| 8 | 路由总数 252 | `tests/test_api_package.py` | ✅ |
| 9 | 容器加固且无 `env_file` | `tests/test_v030_stage8_deploy.py` 6 项静态断言、compose YAML 解析 | ✅ |
| 10 | CI 镜像 job + Alembic 预检 | YAML 解析 9 个 job；内联脚本本地实跑通过 | ✅ |
| 11 | 前端指标面板与全量回归 | typecheck/lint/build 通过；全量后端 1620 passed / 16 skipped | ✅ |

## 3. 验证命令与结果

- `ruff check bidcraft tests scripts`：All checks passed。
- `git diff --check`：clean。
- 定向后端：`tests/test_v030_stage8_observability.py tests/test_v030_stage8_deploy.py tests/test_api_package.py tests/test_fix3_health.py` → 23 passed（1 Starlette 弃用警告）。
- 前端：`npm run typecheck`、`npm run lint`、`npm test -- --run`、`npm run build` 全通过（39 个测试文件 / 319 个测试）。
- 全量后端：`.venv/bin/python -m pytest -o addopts="" -q` → **1620 passed, 16 skipped, 1 warning in 764.63s**（零失败，唯一警告为既存 Starlette/httpx 弃用提示）。

## 4. 残余风险与限制

1. 本地无 Docker，镜像构建/`docker inspect`/容器内 CLI 检查由 CI 的 `docker-image` job 验证。
2. `/health/ready` 要求 Web 产物存在；未构建前端的开发环境预期返回 503，属设计行为。
3. 按用户决定，compose 不注入 `env_file`，生产环境须显式传 `.env`；此项已在部署文档中显著标注。
4. digest 固定与 `read_only` 仍延期；容器以非 root + cap_drop + no-new-privileges 运行。
5. 阶段 9 最终验收尚未开始。

## 5. 回滚

仅回滚本阶段文件：`git checkout -- <上列改动文件>` 并删除两个新增测试与本报告；不要用 `git checkout .` 影响此前阶段提交。
