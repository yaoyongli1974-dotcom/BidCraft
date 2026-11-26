# v0.32.2 质量评估概览前端闭环 — 实施报告

> 分支：`feature/v0.32-ai-engine`（本地分段，按 CI 循环推送）
> 基线：`27bc869`（v0.32.1 文档提交，上一 SHA CI 9/9 成功）
> 范围：把已有的只读 `GET /projects/{ref}/evaluation/summary` 接到质量评估中心前端，不新增 API、不改后端业务逻辑、不改版本号。

## 1. 背景

Phase 6 已新增跨项目/跨版本评估概览 API，但前端尚未消费：`getEvaluationSummary()` 客户端与类型存在，QualityTab 没有展示入口。本次仅补齐真实只读展示，避免 API 成为死代码。

## 2. 实现

- `web/src/pages/tabs/QualityTab.tsx`：
  - 引入 `getEvaluationSummary`，新增 `quality-summary:${ref}` 查询；
  - 在评估说明后展示「跨版本评估概览」面板，最多展示 6 行服务端返回的真实项目/版本分数；
  - `measured=false` 或分数/结论缺失时显示「未测量 / —」，不伪造 0 分、等级或结论；
  - 同步运行评估和后台评估完成后的 `summaryQuery.refresh()`，避免概览滞后；
  - 面板失败显示 `ErrorState`，不静默降级为空数据。
- `web/src/styles/workspace.css`：新增 `.quality-summary-value` 行内徽标布局。
- `web/src/lib/hooks.ts`：`useQuery` 用 `Promise.resolve(...)` 包裹 fetcher 返回值，使同步 mock 与真实异步请求同口径；不改变请求语义、缓存键或取消逻辑。

## 3. 测试

- `web/src/pages/tabs/QualityTab.test.tsx`：
  - 默认 mock `getEvaluationSummary`，避免既有 QualityTab 用例因新查询返回 undefined；
  - 新增用例断言真实分数、真实结论徽标、未测量项均正确展示，且未测量项目不出现伪造的 `0.0`。
- 验证命令与结果：
  - `npm run typecheck` → 通过；
  - `npm run lint` → 通过；
  - `npm test -- --run` → **57 files / 434 tests passed**（ErrorBoundary 用例打印预期 stack trace）；
  - `npm run build` → 通过（Vite 8，1720 modules）；
  - `ruff check bidcraft tests scripts` → All checks passed；
  - `git diff --check` → clean。

## 4. 兼容与风险

- 无后端、数据库、路由数、schema 数或版本号变化；只读接口保持 viewer 可访问、跨项目 404 语义不变。
- 展示严格使用服务端 `measured` 标记：没有报告就不给分，不给结论，不把本地旧报告冒充服务端概览。
- `useQuery` 的 `Promise.resolve` 只增强对同步返回值的容错，线上 API 客户端仍返回 Promise。

## 5. 回滚

回退 `web/src/pages/tabs/QualityTab.tsx`、`web/src/pages/tabs/QualityTab.test.tsx`、`web/src/styles/workspace.css`、`web/src/lib/hooks.ts` 的本次 hunk，删除本报告即可；无数据库状态需要回滚。
