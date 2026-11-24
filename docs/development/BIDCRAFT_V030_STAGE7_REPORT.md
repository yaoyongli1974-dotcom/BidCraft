# BidCraft v0.30.0 阶段 7 报告：前端契约收口与审批/知识库入口

- 分支：`quality/v0.30.0-reliability`
- 前置：阶段 0–6，最新提交 `8b037ab`（1598 pytest）
- 用户批准：跨项目工作流审批入口进入顶栏「待我审批」；相似度查重仅修前端枚举（不改后端业务规则）。
- 约束遵守：未推送 main、未发版、未改生产、未删测试、未改历史迁移、未降低门禁。

## 1. 问题与修复对照（11 项）

| # | 编号 | 问题 | 修复 | 证据 |
|---|------|------|------|------|
| 1 | V030-16 | 相似度 UI 发送 `scope=all/external/internal`、`mode=paragraph/sentence/section`，后端只接受 `project/history` 与 `text/structure/semantic/image/all`，启动查重恒 400 | 选项值与默认值改为后端枚举；报告列表同步中文标签 | `web/src/pages/tabs/SimilarityTab.tsx` |
| 2 | V030-16 | `history` 模式未携带来源项目 | 仅在 `scope=history` 时发送 `source_project_id`，缺失时禁用提交并加 `start()` 守卫 | `web/src/pages/tabs/SimilarityTab.tsx` |
| 3 | V030-16 | 统计磁贴读取后端从未返回的 `checked/reviewed/pending/total/skipped`，永远显示「—」 | 新增纯函数 `similarityTileValues(hits, stats, skippedText)`，按 `hits.length` 与 `review_status !== 'pending'` 推导 | `web/src/api/similarity.ts`、`SimilarityTab.tsx` |
| 4 | V030-16 | 状态元数据缺 `normal` / `suspicious`，后端可取但 UI 无标签/无法复核 | 补两项状态与 `isSimilarityReviewable()`，重置按钮复用该判定 | `web/src/lib/similarityMeta.ts`、`web/src/components/SimilarityResult.tsx` |
| 5 | V030-16 | 出站请求体无契约测试，前后端枚举漂移无法被 CI 捕获 | 新增 JS 枚举镜像与真实 `fetch` body 断言；新增本地 FastAPI + 生产 Pydantic schema 回放 | `web/src/api/outboundContract.test.ts`、`tests/test_v030_stage7_contract.py` |
| 6 | 阶段 7 | 跨项目工作流待审只有后端 `GET /approvals/workflows/pending`，顶栏收件箱看不到 | 新增 API 客户端、归一化器与类型；顶栏分「报价/商务标」「工作流待审」两节，徽标合并计数 | `web/src/api/orchestration.ts`、`web/src/api/shared.ts`、`web/src/lib/types.ts`、`web/src/components/PendingApprovals.tsx` |
| 7 | 阶段 7 | 工作流待审点击无跳转 | 工作流项跳 `/app/projects/{project_id}/agents`；原报价项仍跳 `/boq`，商务标项跳 `/commercial` | `web/src/components/PendingApprovals.tsx` |
| 8 | 阶段 7 | `PendingApprovals` 无组件测试，60s 轮询/关闭中止/重试/路由切换均无回归保护 | 新增 7 个 fake-timer 组件测试 + 工作流归一化单测 | `web/src/components/PendingApprovals.test.tsx`、`web/src/api/pendingApproval.test.ts` |
| 9 | 阶段 7 | 后端已提供 KB 文档清单与检索预览，前端无客户端/入口 | `knowledge.ts` 新增 `listKbDocuments` / `previewKbRetrieval` 与归一化；管理弹窗新增「文档与预览」分段 | `web/src/api/knowledge.ts`、`web/src/pages/admin/AdminKbDocsModal.tsx`、`AdminKnowledgeTab.tsx` |
| 10 | 阶段 7 | KB 预览/溯源 UI 无测试 | 新增 12 个测试，覆盖 URL 参数、渲染、溯源展示、空/错误/重试/关闭 | `web/src/api/knowledge.test.ts`、`web/src/pages/admin/AdminKbDocsModal.test.tsx` |
| 11 | 回归 | 前后端全量验证 | ruff/diff-check/typecheck/lint/test/build 全绿；后端路由数仍 250 | 见第 3 节 |

## 2. 关键数据

- 相似度枚举完全对齐后端 schema：`scope ∈ {project, history}`、`mode ∈ {text, structure, semantic, image, all}`；旧枚举在后端契约测试中被明确拒绝（400/422）。
- 出站契约覆盖 6 类请求：相似度检查、相似度命中复核、KB 文档字段、质量 finding 状态、商务标 `require_complete`；JS 镜像标注了后端 `file:line`。
- 管理端 KB 预览展示 `document_id / page / heading` 溯源；后端接口未改。
- 未新增 HTTP 路由（仍 250 条），未改数据库 schema，未改业务判定阈值。

## 3. 验证结果

| 项目 | 命令 | 结果 |
|------|------|------|
| Lint | `ruff check bidcraft tests scripts` | All checks passed |
| diff | `git diff --check` | 通过 |
| 定向后端 | `pytest tests/test_v030_stage7_contract.py tests/test_similarity*.py tests/test_v030_stage4_similarity.py tests/test_api_package.py` | **47 passed**, 1 warning（Starlette/TestClient 既有弃用） |
| 前端 typecheck | `cd web && npm run typecheck` | 通过 |
| 前端 lint | `cd web && npm run lint` | 通过 |
| 前端测试 | `cd web && npm test -- --run` | **39 files / 318 tests passed** |
| 前端构建 | `cd web && npm run build` | 通过（Vite 8，1710 modules） |
| 全量后端 | `pytest -o addopts="" -q` | **1606 passed, 16 skipped**, 899.40s，0 failed（`/tmp/opencode/v030_stage7_full.out`） |

## 4. 兼容性与风险

- 所有改动均为前端 + 新增测试文件；后端源码零改动，API 形状与路由数不变；旧后端缺工作流待审接口时返回空列表降级（404/HTML）。
- 顶栏轮询仍仅面板打开时启动，60s 间隔、关闭/卸载中止，新增一项请求不会累积定时器。
- 相似度历史查重若未选来源项目将无法提交，避免再次产生 400 请求。
- KB 文档预览失败时展示错误与重试；不改变原「素材元数据」视图。
- 真实浏览器端到端（Chrome 手动点击）未在本次执行；由前端组件测试与构建验证覆盖。

## 5. 回滚

按三个子任务分别回滚已跟踪文件，并删除新增测试文件；不要整目录 `git checkout -- web/`：

1. 相似度：`git checkout -- web/src/pages/tabs/SimilarityTab.tsx web/src/api/similarity.ts web/src/lib/similarityMeta.ts web/src/components/SimilarityResult.tsx web/src/pages/tabs/SimilarityTab.test.tsx web/src/api/backendShapes.test.ts`；删除 `web/src/api/outboundContract.test.ts`、`tests/test_v030_stage7_contract.py`。
2. 审批：`git checkout -- web/src/api/orchestration.ts web/src/api/shared.ts web/src/lib/types.ts web/src/api/pendingApproval.test.ts web/src/components/PendingApprovals.tsx`；删除 `web/src/components/PendingApprovals.test.tsx`。
3. 知识库：`git checkout -- web/src/api/knowledge.ts web/src/pages/admin/AdminKbDocsModal.tsx web/src/pages/admin/AdminKnowledgeTab.tsx`；删除 `web/src/api/knowledge.test.ts`、`web/src/pages/admin/AdminKbDocsModal.test.tsx`。
4. 无迁移/schema 回滚；报告文件可直接删除。
