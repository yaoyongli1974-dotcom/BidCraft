# BidCraft v0.30.0 阶段 4 风险报告：Worker 租约与幂等

- 状态：**等待用户批准**（仅涉及下列语义变更；其余修复不阻塞）
- 审计来源：只读审计 ses_df36d1fbaffeJ4cqfHFPeZZoXm（HEAD a8d364f + 阶段 3 未提交代码）
- 约束：不改历史迁移、不删测试、不降低门禁、不推送 main、不发版。

## 1. 必须修复且无业务语义争议的 P0（将直接实施）

- F09-1/F10-1：`fenced_job()` 同一 Session 中因 identity map 不重新 SELECT，租约换 token 后旧 worker 仍能通过 fence。改为绕过 identity map 读取 token，并在读后取新 Session 写最终结果。
- F09-2：similarity 租约失效后 `session_scope()` 仍提交报告/命中/审计。改为重新校验 token，失效即 `raise JobAborted` 回滚；`except JobAborted` 先于通用异常，避免被 `_fail_job` 处理。
- F09-5：新增 similarity runner 的 API 命名空间 re-export，使崩溃注入测试可用（纯增量）。

## 2. 需批准的业务/行为语义变更

### 决策 A — F09-3 similarity 重试幂等（业务规则）

现状：`run_similarity` 复用 `payload.report_id` 重试时，无条件把报告改回 running 并**追加**新一轮命中，不检查已完成状态；重排后命中翻倍、统计只反映最后一批。

修复选项：

1. **幂等重算（推荐）**：已完成（`done`）的报告直接返回原结果不重算；未完成的重跑前先删除该报告旧命中再写入，保证一份报告一批命中。
2. **仅去重保留重算**：每次重试仍重算，但先清空旧命中再写入；不在 done 时短路。报告耗时不变，命中数恢复正确。
3. **暂不修复**：保留现状，仅在报告中登记风险。

影响：`SimilarityHit` 行数与 `stats.hits` 在重试后不再翻倍；调用方若依赖“重试=重算”需选 2 或 3。

### 决策 B — F10-2/F09-4 其余 worker 的 abort 检查点（行为变更）

现状：仅 generation 在长循环里调用 `raise_if_aborted()`；agent/evaluation/export/images/knowledge/compliance/similarity 在关闭或租约丢失后仍会跑完当前批次再写结果。

修复：在各自长循环/重操作前调用 `raise_if_aborted()`（可选 callback，向后兼容），租约丢失或服务关闭时中止并交还队列重排。

影响：停机/租约丢失时正在进行的工作更早中断并重排，而不是继续完成；最终一致性更强，但单次任务的完成时间可能变长（需等重跑）。generation 已是此语义，本项是统一。

### 决策 C — F10-3 agent 内部签名

向 `run_workflow` 传入可选 abort callback（仅内部 API，不改变 HTTP 接口）。若决策 B 批准则同步实施。

## 3. 其他待办（无需批准）

- F10-4：补一张表驱动测试，声明各 runner 的恢复安全类别（ledger / report-id / 确定性覆盖 / 只读）。
- F10-5：为 8 类 runner 补“中途换 token”参数化回归测试。
- 全局 `fenced_job` 语义收紧后跑完整 worker/队列回归。
