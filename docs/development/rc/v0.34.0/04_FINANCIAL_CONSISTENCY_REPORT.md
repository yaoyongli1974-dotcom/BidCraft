# BidCraft v0.34.0 阶段 4：资金一致性（P0 原子状态与消费者保护）报告

## 1. 范围与边界

本报告只覆盖本子任务拥有文件的改动；支付回调 / 签名与任务账务由并行 agent 负责，
未修改 `bidcraft/api/routes/billing.py`、`bidcraft/wechatpay.py` 及其测试，也未提交/推送。

| 文件 | 性质 |
|---|---|
| `bidcraft/billing.py` | 修改：`mark_paid` / `redeem` / `admin_stats` / `save_billing_settings` |
| `bidcraft/models.py` | 修改：`RechargeOrder` 增加非空交易号部分唯一索引 |
| `bidcraft/config.py` | 修改：新增纯校验 `validate_billing_config` 并在 `Settings.__post_init__` / `validate()` 调用 |
| `bidcraft/migrations/versions/0017_transaction_id_unique.py` | 新增：部分唯一索引迁移；未触碰 0001–0016 |
| `tests/test_alembic.py` | 修改：HEAD → 0017，新增 0017 往返/幂等/重复检测用例 |
| `tests/test_billing.py` | 修改：营收断言按「真实渠道口径」更新（mock 订单不计收入） |
| `tests/test_v0340_billing_atomicity.py` | 新增：真实线程 + 真实 Session 的原子性回归 |
| `tests/test_admin.py` | 拥有但未改动（原有断言全部保持） |

工作区里 `bidcraft/config.py` 事先存在另一 agent 的未提交字段
`wechat_pay_platform_serial_no`，本次改动在其之上叠加，未覆盖。

## 2. 修复内容

### 2.1 `billing.mark_paid`（V0340-P0-01 / P0-04 的 Service 侧）

- 单条条件 UPDATE：`WHERE id = :id AND status = 'pending'`，一次性写入
  `status='paid'`、`paid_at`、`transaction_id`、`provider`，按 `rowcount` 裁决赢家。
- 只有赢得抢占的一方才进入 VIP 续期或 `add_credits`；入账失败与订单状态变更处于
  同一事务，回滚时一起撤销，重试不会留下半支付状态。
- 抢占失败时刷新对象、以库内最终状态区分：
  - 已支付 + 同交易号（或未带交易号的重复调用）→ 幂等返回 `False`；
  - 已支付 + 不同交易号 → `BillingError("订单交易号与支付通知不一致")`；
  - 其它状态（closed/expired）→ `BillingError("订单状态不可支付")`。
- 显式校验 `provider` 参数：订单已有 provider 且传入不同值即 `BillingError`；不传则
  保留订单 provider。mock 订单传 `mock` 仍可用，真实订单不会被 `mock`/`admin` 覆盖。
- 返回值仍为 `bool`，不写任何审计（审计归路由）。

### 2.2 `billing.redeem`（V0340-P0-02）

- 条件 UPDATE：`code = ? AND status='unused' AND (expires_at IS NULL OR expires_at > now)`，
  写入 `status='used'`、`used_by`、`used_at`，`rowcount==0` 时绝不进入加钱路径。
- 失败原因按库内状态重新判定：不存在 / 已使用或停用 / 已过期，错误消息与旧行为一致。
- 成功后再读回被抢占行的 `id` 与 `credits` 调用 `add_credits`；不存在「先读状态再写余额」
  的窗口，并发双花在数据库层被截断。

### 2.3 `admin_stats`（V0340-P1-06）

- `revenue_cents` 只汇总 `status='paid' AND provider='wechat'` 的订单；mock/admin 订单
  仍然入账点数，但不计入营收，mock 支付流程本身未改动。
- 退款按 `CreditTransaction(kind='refund', ref_type in ('order','recharge_order'),
  ref_id=订单id)` 关联；流水 `amount` 是点数，按订单 `amount_cents/credits` 折回分；
  VIP 订单 `credits=0` 时把流水值直接当分处理。单笔订单扣减上限为其收入，营收不为负。

### 2.4 配置校验与索引

- `config.validate_billing_config(config=None, *, vouchers=None, raise_on_error=False)`：
  纯函数，支持 `Settings` 实例与 dict；拒绝 `signup_credits` / `chapter_cost` / 五个限流
  整数上的 bool、NaN/inf、负数；拒绝 `vouchers` / `packages` / `vip_packages` 中重复 ID。
  默认只返回错误列表；`raise_on_error=True` 抛 `ValueError`。默认值返回空列表。
- `Settings.__post_init__` 在任何 clamp 之前调用它，避免负数/NaN 被静默洗白；
  `Settings.validate()` 也汇总这些错误，`validate_startup()` 沿用原路径。
- `billing.save_billing_settings` 复用同一 helper 拒绝重复套餐 ID，避免管理端改配置后
  `find_package()` 命中歧义。
- `RechargeOrder` 增加 `uq_recharge_orders_transaction_id_nonempty`：
  `unique=True` + `sqlite_where/postgresql_where = transaction_id != ''`，
  中文注释说明空串表示未回写交易号、不参与唯一性。无其它 schema 变化。

## 3. 迁移说明（0017）

- `revision=0017_transaction_id_unique`，`down_revision=0016_fact_provenance`；
  `tests/test_alembic.py::HEAD` 同步为 `0017_transaction_id_unique`。
- `upgrade()`：先按名字判存（幂等）；若不存在，扫描历史非空重复交易号，**存在重复即
  抛 `RuntimeError` 并列出样例**，绝不静默改写历史数据或吞掉 DDL 错误；再创建部分
  唯一索引，SQLite / PostgreSQL 用各自方言条件。
- `downgrade()`：按名字判存后 `drop_index`，只删索引、不动订单数据。
- `create_all` 已按当前模型建出索引时，upgrade 为空操作；`init_db()` 后再
  `command.upgrade(cfg, 'head')` 也为空操作。
- 新测试 `test_0017_transaction_id_index_is_partial_reversible_and_idempotent` 验证：
  索引存在且 DDL 含 `UNIQUE` + `WHERE transaction_id != ''`；重复非空交易号被拒；
  多个空串交易号可共存；降级移除、升级恢复、重复执行幂等、版本号回到 HEAD。

## 4. 测试与结果

测试优先：先在 `tests/test_v0340_billing_atomicity.py` 与 `test_alembic.py` 写入预期，
改动生产代码前运行确认红灯（新文件因缺少 `validate_billing_config` 收集失败；
`test_alembic` 因 HEAD 未更新 15 失败；`test_billing` 因营收口径 1 失败）。

最终验证命令与结果：

```bash
.venv/bin/python -m ruff check bidcraft tests
# All checks passed!

.venv/bin/python -m pytest -o addopts= -q \
  tests/test_v0340_billing_atomicity.py tests/test_billing.py \
  tests/test_alembic.py tests/test_admin.py
# 54 passed, 1 warning in 18.39s
# 唯一 warning 为 Starlette/httpx TestClient 弃用提示（基线既有）
```

新文件覆盖的真实并发/事务用例：

- `test_concurrent_mark_paid_credits_exactly_once`：两线程两 Session 同一订单，结果
  恰为 `[False, True]`，余额只加一次、只有一条 recharge 流水。
- `test_concurrent_vip_mark_paid_extends_once`：VIP 订单只续期一次、只有一条 vip 流水、不加点数。
- `test_paid_order_with_different_transaction_id_is_rejected` / `test_non_pending_order_cannot_be_marked_paid`。
- `test_mark_paid_validates_provider_against_order[mock|admin|wechatpay]` /
  `test_mark_paid_accepts_matching_mock_provider`。
- `test_concurrent_redeem_credits_once`：一个赢家入账、一个输家被拒，只有一条 redeem 流水。
- `test_expired_and_disabled_codes_are_not_credited`。
- `test_settings_reject_invalid_billing_numbers`（14 组 bool/NaN/inf/负数参数化）、
  `test_validate_billing_config_is_pure_and_detects_duplicate_ids`、
  `test_save_billing_settings_rejects_duplicate_package_ids`。
- `test_admin_stats_counts_only_wechat_orders_net_of_order_refunds`：mock 订单 0 收入，
  wechat 订单 3000 分减 3 点退款（单价 300 分/点）→ 2100 分。
- `test_transaction_id_partial_unique_index_rejects_duplicates`。

并发用例使用 Barrier 同步的真实线程与独立 Session；SQLite 短锁冲突时由用例重试整笔
事务，未 mock 条件 UPDATE，也未用 Python 全局锁替代数据库 fencing。

## 5. 残留风险

1. 回调路由层对 `mock`/`admin` provider 的拒绝、金额/币种/商户号/AppID/新鲜度强校验由
   并行 agent 在 `billing.py` 路由与 `wechatpay.py` 完成；本次只保证 Service 层
   `mark_paid` 的 provider 一致性，不在路由层做二次改动。
2. 订单退款关联依赖流水约定 `ref_type='order'|'recharge_order'` + `ref_id=订单id`；
   若未来出现别的退款记账方式，需要扩展 `admin_stats` 的识别规则。金额换算按订单单价，
   单笔扣减封顶，属于保守口径；`credits=0` 的 VIP 订单按分值解释。
3. 历史库若已有重复非空交易号，0017 会明确拒绝升级；需要人工对账后处理，不能自动去重，
   这是有意的 fail-closed，不是可静默跳过的检查。
4. 本地验证环境为 SQLite；PostgreSQL 的部分唯一索引与并发行锁行为未在真实 PG 实例上
   演练（CI 的 postgres job 需另行验证）。
5. `Settings.__post_init__` 现在会对非法资金/限流值直接抛 `ValueError`；默认环境变量与
   现有测试不受影响，但生产部署若配置了负数/NaN 会在启动时失败（预期行为）。

## 6. 回滚

- 代码：还原上表所列 5 个已修改文件（`bidcraft/billing.py`、`models.py`、`config.py`、
  `tests/test_alembic.py`、`tests/test_billing.py`），删除新增迁移与新测试文件；
  不触碰其它 agent 的改动。
- 数据库：`alembic downgrade 0016_fact_provenance` 只删除部分唯一索引，不修改订单数据；
  若同时回滚代码，需保证没有并发支付在途，否则唯一性保护会暂时消失。
- 也可先只回滚 `admin_stats` 口径（恢复原汇总）以兼容旧前端断言，再单独处理索引；
  本分支未做数据回填，无不可逆数据变更。
