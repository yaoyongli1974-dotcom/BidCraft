# v0.32 测试与验收报告（Phase 7）

> 分支：`feature/v0.32-ai-engine`，HEAD `a687020`（Phase 7 版本/CI/文档准备）
> 本报告汇总各阶段权威结果；Phase 7 收口后已重跑全量 pytest，并在版本 bump 后完成 Web typecheck/lint/test/build 与静态校验。

## 1. 后端全量（SQLite，权威结果）

| 项 | 结果 |
| --- | --- |
| 命令 | `.venv/bin/python -m pytest -o addopts="" -q` |
| 结果 | **2257 passed, 888 skipped, 1 warning in 971.16s**，零失败（Phase 7 收口后最终重跑） |
| warning | 仅 Starlette/httpx `TestClient` 弃用提示，不是失败 |
| 覆盖阶段 | Phase 0 基线 2125 → Phase 1 2145 → Phase 2 2174 → Phase 3 2199 → Phase 4 2219 → Phase 5 2241 → Phase 6/最终 2257 passed |

阶段增量来自各阶段新增测试（章节规划、知识资产、检索、工具循环、经验/事实治理、质量评估），没有删除测试或放宽既有断言；历史迁移 0001-0012 零改动。

## 2. 前端（Web）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| Typecheck | `npm run typecheck`（`tsc --noEmit`） | PASS |
| Lint | `npm run lint`（eslint） | PASS |
| 单测 | `npm test -- --run` | **57 files / 431 tests passed** |
| 构建 | `npm run build`（tsc + Vite 8） | PASS（约 1720 modules） |

测试覆盖新增规划面板、知识资产面板、检索溯源徽标、工具轨迹、经验/事实确认、质量导出与汇总；契约测试（`apiSurface.test.ts`、`outboundContract.test.ts` 的严格 body-key 断言）同步新 key 白名单，null 不被伪造为 ok/verified/trusted。`ErrorBoundary.test.tsx` 有意打印错误堆栈以验证边界组件，Vitest 汇总仍为全通过。

## 3. 静态与契约检查

| 检查 | 结果 |
| --- | --- |
| `ruff check bidcraft tests scripts` | All checks passed |
| `git diff --check` | clean（无尾随空格/冲突标记） |
| Alembic 单 head | `0015_fact_confirmation`（AST 扫描断言恰好一个 head） |
| Alembic preflight | 静态编译 + 临时 SQLite scratch 库 upgrade 通过 |
| 版本一致性 | 0.32.0：后端 `__init__.py` / `pyproject.toml` / `uv.lock`（项目自身）/ Web `package.json` + lock 根 / CHANGELOG 条目 / release-notes 表行一致 |
| API 路由 / 请求模型 | 274 / 93（`tests/test_api_package.py`） |
| `.github/workflows/ci.yml` YAML 解析 | PASS（PyYAML `safe_load`，job/step 结构可解析） |

## 4. 为什么本地 888 skipped，而 CI 有真实 PG 结果

本地 SQLite 全量中的 888 skipped 主要是 **opt-in 的 PostgreSQL 集成用例**：`tests/postgres/**` 与 `tests/test_postgres_integration.py` 等在 `BIDCRAFT_TEST_PG_URL` 未设置或缺少 `pg_dump` 等工具时由 fixture/skipif 主动跳过；另有少量既定环境跳过（审批样本未触发闸门、CJK 字体缺失）。这是刻意的「本地不连生产库」设计，不是失败，也不能当作 PG 通过证据。

CI 的 `postgres` job 单独启动临时 PostgreSQL 16 服务并注入 `BIDCRAFT_TEST_PG_URL`，显式执行 `tests/postgres`（含 v0.32.0 新增 `tests/postgres/test_v032_migrations.py`，由独立 agent 创建）、`tests/test_postgres_integration.py` 等文件，并用 JUnit 强制 `tests>=900` / `skipped=0` / `failures=0`：数量不足、任何跳过或失败都会让 CI 失败。因此「本地 888 skipped」与「CI 真实 PG 900+ 通过」是同一套用例在不同环境下的两种表现，而非相互矛盾。

## 5. 门禁同步（Phase 7）

- CI 临时镜像标签 `v0313-check` → `v0320-check`，7 处引用同步更新（build/inspect/run/smoke）。
- postgres job 命令显式追加 `tests/postgres/test_v032_migrations.py`；PG 数量/跳过/失败门槛保持 `>=900 / 0 / 0` 不变。
- Alembic 单 head 检查、Alembic preflight、版本/tag/CHANGELOG 一致性检查、安全门禁（pip-audit / npm audit / bandit / gitleaks）均保留未削弱。
- 部署清单 `deploy/docker-compose.yml`、`deploy/README.md` 的镜像标签与升级示例版本同步到 0.32.0。

## 6. 占位与未验证边界

- 最终全量 pytest 重跑（Phase 7 收口后）：`.venv/bin/python -m pytest -o addopts="" -q` → **2257 passed, 888 skipped, 1 warning in 971.16s**，零失败。
- 远端 CI：最终 run `45732364770`（SHA `0716edfbdd9cc3c804a1c8e3c41bde8ef2673635`）**9/9 jobs success**；`postgres (real integration)` 日志 `PG integration OK: 904 tests, 0 skipped`。
- 真实 PostgreSQL 0013/0014/0015 迁移往返：本地 pgserver 16.2 `tests/postgres/test_v032_migrations.py` 2 passed；CI postgres job 904 tests / 0 skipped 覆盖该文件。
- 未验证边界：真实模型端点的 function-calling 质量、外部 Dify/RAGFlow 的 RRF 延迟与限流、真实标注投标文件的字段召回、超大目录批量检索 P95；本地无这些环境时不作通过声明。
