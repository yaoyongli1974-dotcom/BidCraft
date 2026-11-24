# v0.30.0 阶段 3 风险报告：任务队列与计费可靠性

- 基线分支：`quality/v0.30.0-reliability` @ `fc6b913`
- 审计范围：V030-07（同步生成无 Job/心跳、PendingCharge 仅 TTL）、V030-08（扣费/退款幂等、账务事务边界、事件一致性）
- 本报告只做风险评估；**未修改任何代码、测试或数据库迁移**。

## 1. 核心事实

| 项 | 现状 |
|---|---|
| PendingCharge TTL | 默认 1800 秒，配置 `BIDCRAFT_PENDING_CHARGE_TTL`（`config.py:288`），`reconcile_pending_charges` 按 `created_at` 判断（`jobqueue.py:802-818`） |
| 活跃性判断 | `_project_has_live_job()`（`jobqueue.py:753-772`）仅按 `project_id + status IN (queued,running)`，**项目级**、不区分哪笔扣费 |
| 对账触发 | `recover_on_startup()`、reaper 每 300 秒、`bidcraft reconcile-charges` CLI |
| 已具备保障 | 扣费与 marker 同事务；settle/reconcile 用条件 UPDATE 抢占；VIP 除不尽余数精确退款；`refund_charged_job` 以 `done_steps` 为准且幂等；子账号退款定位主账号 |

## 2. 差距与风险分级

| 编号 | 差距 | 风险 | 是否需批准 |
|---|---|---|---|
| G7-1 | 同步生成无 Job/心跳，>TTL 时 reaper 会把 marker 全额退款，章节最终免费交付 | 业务规则（退款时机） | 是 |
| G7-2 | marker 活跃性按项目判断：同项目无关的 images/export Job 会永久阻塞退款 | 账务语义 | 是 |
| G7-3 | 同步路径只捕获 `Exception`；`BaseException` 会遗留 marker | 低（可随主修复一起做） | 否 |
| G8-1 | 写章节后、结算前崩溃 → 对账按 0 章全额退款，用户保留全部章节且免费 | 账务语义 + 数据一致性 | 是 |
| G8-2 | `_cancel_stale_jobs/_fail_job` 不退款； worker 停摆时重试会二次扣费 | 账务语义 | 是 |
| G8-3 | `_fail_job` 不清 `claim_token`，旧 worker 可把 failed 覆盖成 done 并重复发事件 | worker 状态语义 | 是 |
| G8-4 | Job 路径退款靠 JSON `payload['refunded']` 读改写，reaper 与 worker 并发可双退 | 账务语义 + 可能需要新列 | 是 |
| G8-5 | 退款失败仅记日志，无 JobEvent/用户可见痕迹 | 低（可随主修复一起做） | 否 |
| G8-6 | 退款 ledger 的 `ref_id` 仅 project_id，无法与 Job/marker 对账 | 数据结构/API 形状 | 是 |
| G8-7 | 测试矩阵缺少长任务、写后崩溃、陈旧任务退款、并发双退、PG 并发退款等场景 | 低（随实现补） | 否 |

## 3. 候选方案

### 方案 A：无新增迁移的最小闭环（推荐先做）

1. 在 `jobqueue` 新增 `touch_pending_charge(marker_id)`，心跳线程只更新既有 `PendingCharge.updated_at`。
2. `reconcile_pending_charges` 改用 `updated_at` 而非 `created_at` 作为 TTL 基准。
3. **移除 marker 的项目级活跃性检查**：marker 只代表同步生成，心跳本身就足以证明存活；崩溃后心跳停止，TTL 到期自动退款。
4. 同步生成两处入口在模型调用期间启动心跳线程，`finally` 停止；同时把 `except Exception` 改为覆盖 `BaseException` 的结算保护。
5. 补 G8-5 的 `refund_failed` 事件，以及长任务/无关 Job/BaseException 场景测试。

- 优点：无 schema 变更、无 API 形状变更、无历史迁移编辑。
- 代价：改变退款时机语义（从“项目有活 Job 就不退”变为“marker 心跳活着就不退”）；这是修正误判，但需要你确认属于已授权的 V030-07/08 修复范围。
- 仍不解决：G8-1（写后崩溃按 0 章退款）、G8-4（Job 并发双退）。

### 方案 B：完整账务加固（推荐与 A 合并，但需明确批准）

在方案 A 基础上增加**新迁移**（不编辑 0001-0010）：

1. `pending_charges.sections_at_charge`：扣费时记录项目已有非空章节数；对账时 `written = 当前非空章节数 − baseline`，解决 G8-1；退款为 0 时 marker 置 `cleared` 而非 `refunded`。
2. `jobs.refund_claimed` 布尔列：条件 UPDATE 抢占，统一 `refund_charged_job` / `_fail_job` / reaper 的退款入口，解决 G8-4；`CreditTransaction` 增加可选幂等键思路一并评估。
3. `_fail_job` 调用 `refund_charged_job` 并清 `claim_token`/租约字段，解决 G8-2/G8-3。
4. `refund_chapters(..., ref_id)` 携带 job/marker 标识并补 `refund` JobEvent，解决 G8-6。
5. 补 PG 并发退款测试（沿用 opt-in `BIDCRAFT_TEST_PG_URL`，本地无法执行）。

- 优点：覆盖全部 P0 账务风险。
- 代价：新增 Alembic 迁移（新 revision，可逆）；改变退款语义与 ledger 数据形状；需要 CI 的 PG 作业验证，本地无 PostgreSQL，需接受“本地跳过、CI 验证”。

### 方案 C：仅低风险 + 测试

只做 G7-3、G8-5 和补充测试，所有 P0 保持现状。不推荐：V030-07/08 的核心问题仍未解决。

## 4. 我的建议

批准 **方案 A + 方案 B 的完整包**，因为审计中的 P0 都是“用户免费拿到章节”或“重复扣费”的真实资损路径，且任务书本阶段就是要求修复 V030-07/08。若你希望控制变更面，可先批准 **方案 A**，将 G8-1/G8-4/G8-6 延后到后续小版本并在发布说明中标注。

## 5. 需要你确认

1. 是否批准改变退款时机语义（心跳替代项目级 Job 活跃性）？
2. 是否批准新增可逆 Alembic 迁移（sections_at_charge、refund_claimed）以及 ledger ref_id 形状变化？
3. 是否接受 PG 相关并发测试仅能在 CI 的 postgres:16 作业中执行（本地无 psql/docker）？
