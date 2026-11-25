# G8 监控与日志验收报告（RC v0.31.3）

## 1. 结论

| 项目 | 状态 |
|---|---|
| `/health`、`/health/live`、`/health/ready` | A PASS |
| `/admin/health` 与 `global_metrics` | A PASS |
| Worker 结构化日志与 request-id | A PASS（代码/CI 覆盖） |
| 告警规则、通知通道、生产日志采集 | D NOT VERIFIED |

## 2. 实测证据

RC 环境（TCP `127.0.0.1:5432`）下，以 admin 登录后请求：

```
LOGIN 200
ADMIN_HEALTH 200
PUBLIC_HEALTH 200 LIVE 200 READY 200
```

`/admin/health` 返回的关键字段：`version`、`status`、`web_ready`、`model_configured`、`users`、`db_size_bytes`、`global_metrics` 等。

`global_metrics` 实测键：

```
jobs, jobs_by_status, oldest_queued_age_seconds, open_findings,
projects, projects_by_status, queued_jobs, stale_running_jobs,
users, workers_running
```

实测值：`projects_by_status={"intake":2}`、`jobs_by_status={}`、`workers_running=false`、`queued_jobs=0`、`open_findings=1`、`users=3`、`stale_running_jobs=0`。`workers_running=false` 是因为验收通过 TestClient 直接挂载应用，没有启动 `bidcraft serve` 的 worker 池，不代表生产部署异常。

## 3. 日志与 request-id

Stage 8 已在代码中加入 Job 上下文字段（job_id/kind/project_id/worker_id）与 JSON 日志格式；CI `python-lint`、`python-tests`、Docker 静态检查均通过。CI Docker job 还验证了容器 `/health/live` 真正可访问。

## 4. 未验证项

- 未验证外部告警系统（邮件/Webhook/短信）的触发、抑制与升级路径。
- 未验证生产日志采集、 retention、磁盘轮转与跨实例 request-id 聚合。
- 未在 `bidcraft serve` + embedded workers 启动形态下观察 `workers_running=true` 与队列积压告警阈值。

## 5. 结论

监控探针与本地指标接口可用；告警链路与生产日志平台不在 RC 隔离环境覆盖范围内，须在生产部署前完成配置与演练。
