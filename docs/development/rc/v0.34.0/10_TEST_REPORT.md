# BidCraft v0.34.0 阶段 8/9：前端终态与轮询闭环测试报告

## 1. 范围与文件所有权

分支 `fix/v0.34.0-commercial-reliability`，基线 HEAD `b0aceb9`。本阶段只做前端生命周期与测试，严格按所有权修改：

| 类型 | 文件 |
|---|---|
| 生产修改 | `web/src/hooks/watchJob.ts`、`web/src/pages/BillingPage.tsx`、`web/src/pages/admin/AdminHealthTab.tsx`、`web/src/lib/types.ts`（仅 `AdminGlobalMetrics.workers_running` 注释） |
| 新增测试 | `web/src/pages/BillingPage.test.tsx` |
| 修改测试 | `web/src/hooks/watchJob.test.ts`、`web/src/pages/admin/AdminHealthTab.test.tsx`、`web/src/pages/tabs/QualityTab.evidence.test.tsx`、`web/src/pages/tabs/QualityTab.uxFix.test.tsx`（后两处仅为 `WatchJobHandle` 类型断言） |
| 本报告 | `docs/development/rc/v0.34.0/10_TEST_REPORT.md` |

未编辑任何后端 Python 文件、迁移、`tests/**`、其它 RC 报告；未 commit/push、未运行 `ruff format`、未跑后端全量套件。

## 2. 生产改动

### 2.1 `watchJob`：终态统一闭环

- 终态集合与后端 `bidcraft/jobs.py::TERMINAL_STATUSES` 对齐为 `done`/`failed`/`cancelled`（此前只识别 `done`/`failed`，`cancelled` 会无限轮询）。
- 任一终态都立即关闭 SSE、`clearInterval` 并把 `timer` 置空；`handled` 守卫保证 SSE 与轮询同时到达也只结算一次。
- 新增 `onTerminal(job)`：对三种终态都回调一次，携带完整终态快照（含 `result`/`error`/`message`）；返回的 stop 函数还提供 `terminalSnapshot()`，便于尚未注册回调的调用方读取。
- `cancelled` 明确不是成功：只走 `onFailed`，绝不误触发 `onDone`；避免页面显示「已完成」后继续等待。现有调用方无需改动即可停止 loading。
- SSE_EVENTS 增加 `cancelled`；终态帧若缺 `status`，用事件名兜底。

### 2.2 `BillingPage`：终态停表与同步提交守卫

- 终态集合 `paid`/`closed`/`expired`/`failed`（来自 `/billing/orders` 状态契约）；effect 入口与 `tick` 收到最新订单后双重判断，命中终态就地 `clearInterval`，不等下一次 React effect cleanup。
- 新增同步 ref 守卫 `submittingRef` / `payingRef` / `redeemingRef`：同一事件循环内双击时 React state 尚未更新，仅靠 `busyPackage`/`paying` 的 disabled 无法拦截；ref 立即置位，保证 `createBillingOrder`、`createVipOrder`、`mockPayOrder`、`redeemCreditCode` 各只提交一次，`finally` 里释放以便失败后重试。

### 2.3 `AdminHealthTab`：`workers_running` 正确口径

- 标签由误导性的「工作进程是否运行」改为「内置工作进程已启动」，值由 `是/否` 改为 `已启动/未启动`。
- 后端 `bidcraft/jobqueue.py::workers_running()` 的语义是「**本进程**是否启动内置 worker 池（vs API-only 部署）」。因此 `false` 不再渲染 `工作进程未运行` 的 warn 告警，改为 info 说明「未启动内置工作进程（可为 API-only 部署）」；队列风险由 `queued_jobs` / `oldest_queued_age_seconds` / `stale_running_jobs > 0` 体现，仍保留 warn。
- 注释/脚注与 `AdminGlobalMetrics` 类型注释同步为当前 10 键契约，未编造后端没有的指标。

## 3. 测试先行证据

测试先写、后改生产代码。在改动前的失败输出（真实执行）：

```bash
cd web && npx vitest run src/hooks/watchJob.test.ts --reporter=verbose
# Test Files 1 failed | Tests 4 failed | 6 passed (10)
# → onTerminal 未被调用（3 处），cancelled 事件未结算（1 处）

cd web && npx vitest run src/pages/admin/AdminHealthTab.test.tsx --reporter=verbose
# Test Files 1 failed | Tests 3 failed | 4 passed (7)
# → 找不到「内置工作进程已启动」指标行、info 说明不存在

cd web && npx vitest run src/pages/BillingPage.test.tsx --reporter=verbose
# 首轮：4 个终态用例因 fake timers 在 findByText 阶段超时（测试写法问题，已改为数据加载后再启用 fake timers）
# 修正写法后、生产改动前：Tests 3 failed | 4 passed (7)
# → 连续三次 dispatchEvent 得到 createBillingOrder/createVipOrder/mockPayOrder 各 2 次调用
```

测试只在 API 边界 mock：`watchJob.test.ts` 只 mock `getJob`（状态契约来自后端 `TERMINAL_STATUSES`，不把 mock 返回值当作「后端已正确产生该状态」的证据）；`BillingPage.test.tsx` 只 mock billing API 与 auth/toast context。没有 mock-only 的成功宣称。

## 4. 验证结果（实际命令）

```bash
cd web && npm run typecheck          # tsc --noEmit → 无输出，退出码 0
cd web && npm run lint               # eslint . → 无输出，退出码 0
cd web && npm test -- --run          # Test Files 68 passed (68); Tests 499 passed (499)
cd web && npm run build              # tsc + vite build → ✓ built in 662ms
cd web && npx vitest run src/hooks/watchJob.test.ts \
    src/pages/BillingPage.test.tsx \
    src/pages/admin/AdminHealthTab.test.tsx
# Test Files 3 passed (3); Tests 24 passed (24)
# 上述定向文件连续 5 次运行均 24 passed
```

`npm test -- --run` 共执行 13 次：首次 499 passed；随后有一次出现 `1 failed | 498 passed`（失败用例名未保留）；其后连续 11 次均为 499 passed。定向文件 5 连跑无失败，判定为非本阶段引入的偶发时序抖动，但尚未定位到具体用例，列为残余风险。全量输出中的 `Error: 渲染炸了` 属 `ErrorBoundary.test.tsx` 故意抛错的预期日志，不是失败。

最终全量后端（所有 P0/P1 修复、版本 bump 后）：

```bash
.venv/bin/python -m pytest -o addopts= -q
# 2582 passed, 1050 skipped, 2 warnings in 1069.30s, 0 failures
```

输出：`/tmp/opencode/v0340_p1_fix_full.out`。两个 warning 分别为 Starlette/httpx TestClient 弃用提示和测试中故意制造重复 `manifest.json` 的 zip 写入提示；均未掩盖失败。

## 5. 残余风险

1. 一次全量 web 测试出现未复现的单用例失败（见上）；未定位到具体测试名，不能宣称已修复。
2. 前端只按后端既有状态契约消费终态；`watchJob` 的 `cancelled` 闭环证明的是「收到该状态时前端停止」，不证明后端在所有取消路径都写了该状态。
3. `onTerminal`/`terminalSnapshot()` 是新增的可选能力；现有页面尚未显式消费 `onTerminal`，取消路径依赖 `onFailed` 收尾，因此页面文案仍显示「失败」而非「已取消」。
4. 本阶段报告写入时版本仍为 `0.33.2`；随后版本负责人已完成 0.34.0 版本号 bump 与发版说明，最终版本一致性由阶段 10 统一复核。

## 6. 回滚

仅回滚本阶段前端改动（保留其它并行任务文件）：

```bash
git restore web/src/hooks/watchJob.ts web/src/pages/BillingPage.tsx \
  web/src/pages/admin/AdminHealthTab.tsx web/src/lib/types.ts \
  web/src/hooks/watchJob.test.ts web/src/pages/admin/AdminHealthTab.test.tsx \
  web/src/pages/tabs/QualityTab.evidence.test.tsx \
  web/src/pages/tabs/QualityTab.uxFix.test.tsx
rm web/src/pages/BillingPage.test.tsx docs/development/rc/v0.34.0/10_TEST_REPORT.md
```

回滚后恢复：cancelled 任务无限轮询、订单终态后仍可能在 in-flight tick 上继续轮询、双击创建重复订单、`workers_running=false` 被误标为生产故障。无需数据库迁移或服务端重启；重新打包前端镜像即可。

## 7. 结论

本地前端生命周期验证 **PASS**；远程 CI 最终 run `46123816243` 对 SHA `0698dfd` **9/9 success**；生产环境 **NOT VERIFIED**。建议 **CONDITIONAL GO**：远程 CI 与 PG 真实并发、版本号 bump 已完成，生产等价环境仍需单独验收。
