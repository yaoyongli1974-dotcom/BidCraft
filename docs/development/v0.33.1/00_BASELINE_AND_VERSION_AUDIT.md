# v0.33.1 Phase 0：基线与版本审计

## 1. 审计范围

本文件是《BidCraft v0.33.1 完整技术改造任务书》Phase 0 的第一份只读审计产物。仅核对当前 main 的代码、版本、发布记录、CI 与 v0.33.0 RC 遗留；本阶段不修改业务代码、不做发版、不做生产部署。

## 2. 当前基线事实（实测）

| 项目 | 实测结果 |
|---|---|
| 仓库 | `/home/yao/project` |
| 分支 | `main` |
| HEAD | `373499729a651c4d7bb69527379b0201b7ec7d1a` |
| 工作区 | 干净（`git status --porcelain=v1` 无输出） |
| 最近提交 | `3734997` CI 契约修复；`03047ca` v0.33.0 release prep；`896919b` RC13 最终报告 |
| 后端版本 | `0.33.0`（`bidcraft/__init__.py:3`） |
| pyproject 版本 | `0.33.0`（`pyproject.toml:3`） |
| Web 版本 | `0.33.0`（`web/package.json:4`） |
| 最新 Tag | `v0.33.0` |
| 最新 GitHub Release | tag/name `v0.33.0`，id `459311341`，assets=0，URL `https://github.com/yaoyongli1974-dotcom/BidCraft/releases/tag/v0.33.0` |
| CHANGELOG | 最高版本节为 `v0.33.0` |
| 发版说明索引 | `docs/release-notes/README.md:6` 为 `v0.33.0` 行 |
| Alembic | 16 个版本文件；单 head `0015_fact_confirmation`；链为 `0013_chapter_planning → 0014_kb_asset_metadata → 0015_fact_confirmation` |
| Python 测试文件 | `tests/` 下 234 个 `test_*.py` |
| Web 测试文件 | `web/src/` 下 58 个 `*.test.ts` / `*.test.tsx` |
| API 规模 | `tests/test_api_package.py:54` 断言 275 条路由；`:128` 断言 94 个 Pydantic 请求模型 |

## 3. CI 与发布证据

- 目标 SHA `373499729a651c4d7bb69527379b0201b7ec7d1a` 的 GitHub Actions API 返回 7 条运行记录；抽样 5 条均为 `completed / success`。
- 最终发版 CI 记录此前已记录为 run `45807554195`，9/9 jobs success；本次 API 查询到的该 SHA 多次运行也全部为 success。
- CI 共 9 个 job：python-lint、python-tests、web、postgres real integration、docker、docx render、dependency audit、bandit SAST、secret scanning。
- `.github/workflows/ci.yml:493-518` 的真实 PG job 使用 `BIDCRAFT_TEST_PG_URL`，执行 `tests/postgres`、`tests/postgres/test_v032_migrations.py` 等，并断言 `tests >= 900`、`skipped == 0`、`failures == 0`。
- `.github/workflows/ci.yml:301/388` 的 docker 冒烟使用 `bidcraft:v0330-check`；v0.33.1 若发版需同步 image tag 断言。
- `docs/release-notes/v0.33.0.md` 与 CHANGELOG 已存在；v0.33.1 尚无 tag、branch、release notes。

## 4. 基线回归

- 后端全量回归在 Phase 0 重新执行，命令：`.venv/bin/python -m pytest -o addopts= -q`，输出 `/tmp/opencode/v0331_phase0_full.out`。实测结果：**2364 passed / 1008 skipped / 1 warning in 1009.09s**，零失败；唯一 warning 为 Starlette/httpx TestClient 弃用提示。该结果与 v0.33.0 最终记录一致。
- 前端基线实测：`npm run typecheck`、`npm run lint`、`npm test -- --run`、`npm run build` 全部成功；测试为 58 files / 451 tests；`ErrorBoundary.test.tsx` 输出预期异常堆栈，不是失败；Vite 8 构建 1720 modules，耗时约 981ms。

## 5. v0.33.0 RC 结论继承

v0.33.0 最终结论为 **CONDITIONAL PASS / PARTIAL ACCEPTANCE**，P0 全部通过，但明确存在以下未完成/未验证项：

1. RC-09 普通条件链（只有 `min_amount` / `packages` / `currencies`，不含 `rules`）在 pending items 中没有 `approval_explain`。证据：`docs/development/rc/v0.33.0/10_APPROVAL_RULE_EXPLAINABILITY_REPORT.md:19`、`bidcraft/boq.py:1586-1599`。
2. RC-04 `vector_ready` 按 KB 全部块数计算，草稿/不可发布块可能造成统计偏大。证据：`docs/development/rc/v0.33.0/05_KNOWLEDGE_ASSET_LIFECYCLE_REPORT.md:136`、`bidcraft/knowledge.py:121-165`。
3. 真实招标文件、真实检索 qrels、专家评分、真实模型端点、生产等价 PostgreSQL/PgBouncer、生产备份/恢复/部署均 DATA BLOCKED 或 NOT VERIFIED。
4. v0.33.0 的最终报告在发版前曾标注远程 CI 未取证；发版后最终 run 45807554195 已 9/9 成功（本 Phase 0 再次确认目标 SHA 的 CI 运行均 success）。

## 6. Phase 0 结论

当前 main 可作为 v0.33.1 的合法基线：版本、tag、release、CHANGELOG 一致，Alembic 单 head，CI 成功，前端基线通过。v0.33.1 不得回退到旧 SHA；后续改动应基于 `3734997` 创建独立开发分支，并按阶段提交报告与测试，不自动合并 main、不打 tag、不建 Release、不部署生产。
