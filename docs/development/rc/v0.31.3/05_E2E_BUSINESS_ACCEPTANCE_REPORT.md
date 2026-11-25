# G4 核心业务 E2E 验收报告（RC v0.31.3）

## 1. 环境与方法

- RC 环境：`BIDCRAFT_ENV=rc`，隔离目录 `/tmp/opencode/rc-v0313-home`
- 数据库：隔离 PostgreSQL 16.2（pgserver），Alembic head `0012_generation_lease`
- 入口：`fastapi.testclient.TestClient(create_app())`，与 RC 进程同一配置加载路径
- 账号：RC 本地临时管理员，凭据来自 `/tmp/opencode/rc-v0313.env`（chmod 600，未写入报告）
- 数据：仅 RC 测试项目，未触碰生产库/生产账号/真实客户数据

## 2. 实测结果

| 步骤 | 请求 | 实测 |
|---|---|---|
| 存活探针 | `GET /health/live` | 200 |
| 就绪探针 | `GET /health/ready` | 200 |
| 登录 | `POST /auth/login` | 200 |
| 创建项目 | `POST /projects` | 201；`PROJECT_ID=428490e0-6fcb-4d4c-98e1-5e54110a1808` |
| 项目列表 | `GET /projects` | 200 |
| 项目详情 | `GET /projects/{id}` | 200 |
| 条款要求 | `GET /projects/{id}/requirements` | 200 |
| 空报价版本 | `GET /projects/{id}/boq/versions` | 200 |
| 最新质量报告 | `GET /projects/{id}/evaluation/latest` | 200 |
| 发起查重 | `POST /projects/{id}/similarity/check` | 202 |
| 查重报告轮询 | `GET /projects/{id}/similarity/reports/{report_id}` | 200；最终 `status=done` |
| 任务 UI 路由 | `GET /jobs` | 200，`text/html`（SPA 页面，非 JSON API） |

首次 E2E 脚本的最后一步把 `GET /jobs` 当作 JSON 接口调用 `.json()`，触发 JSONDecodeError。复核确认 `/jobs` 是前端 SPA 路由，返回 200 HTML；已改为按 HTML 路由记录，不作为 API 失败，未删除步骤或降低断言。

## 3. 结论

核心业务链路（探针 → 登录 → 项目 → 要求/报价/评估/查重 → 异步报告完成）在隔离 RC + 真实 PostgreSQL 上全部通过。

**状态：A PASS**

残余说明：本轮使用假模型地址，未验证真实模型生成质量；AI 业务样本在 G8 单列。查重完成基于空项目数据，真实文档语义效果需在 G8 用授权脱敏样本验证。
