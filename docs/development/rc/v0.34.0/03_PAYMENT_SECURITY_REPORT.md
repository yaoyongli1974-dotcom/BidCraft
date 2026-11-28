# BidCraft v0.34.0 阶段 2：支付回调安全加固报告

## 1. 范围

本轮只处理 `02_RISK_AND_CURRENT_TEST_REPORT.md` 中支付回调相关的 P0 项：

- V0340-P0-03：回调签名新鲜度/序列号/签名类型、事件与资源字段、商户号/AppID、
  金额类型与币种、交易号、交易状态、解密异常的统一强校验。
- V0340-P0-04：`mock-pay` 对订单渠道的校验。
- V0340-P1-01（部分）：回调 body 的 1 MiB 上限。
- V0340-P1-02（部分）：`configured()` 要求平台证书与平台证书序列号可用。

严格限定改动文件：`bidcraft/wechatpay.py`、`bidcraft/api/routes/billing.py`、
`tests/test_wechatpay.py`、`tests/test_fix2_runtime_services.py`、新增
`tests/test_v0340_payment_security.py`、新增本文档。`bidcraft/config.py` 中已有的
`settings.wechat_pay_platform_serial_no`
（来自 `BIDCRAFT_WECHAT_PAY_PLATFORM_SERIAL_NO`）被本轮直接复用，未改动该文件。

## 2. 实际改动

### 2.1 `bidcraft/wechatpay.py`

| 位置 | 改动 |
|---|---|
| 模块常量 | 新增 `SIGNATURE_TIMESTAMP_TOLERANCE = 300`、`SIGNATURE_TYPE = "WECHATPAY2-SHA256-RSA2048"`、`RESOURCE_ALGORITHM = "AEAD_AES_256_GCM"`、`KNOWN_TRADE_STATES`（SUCCESS/REFUND/NOTPAY/CLOSED/REVOKED/USERPAYING/ACCEPTPAY/PAYERROR/FAIL）。 |
| `configured()` | 除原有商户参数与商户私钥文件外，新增 `wechat_pay_platform_cert`、`wechat_pay_platform_serial_no` 非空及平台证书文件存在性检查；避免「可下单但回调必失败」的半配置状态。 |
| `_verify_signature()` | 先统一小写 HTTP 头名；仍先检查平台证书文件与三个必备签名头；新增时间戳必须为十进制数字字符串且与本机时间偏差不超过 300 秒；配置了平台证书序列号时要求 `wechatpay-serial` 精确相等；`wechatpay-signature-type` 若出现必须等于 `WECHATPAY2-SHA256-RSA2048`；保留原始 body 字节的 RSA-SHA256 验签。 |
| `parse_notification()` | 验签后要求顶层 `event_type` 为以 `TRANSACTION.` 开头的字符串、`resource.algorithm == AEAD_AES_256_GCM`；AES-GCM 解密显式捕获 `InvalidTag`，与其它解密异常统一转 `PaymentError`；解密后要求 `mchid`/`appid` 与配置一致、`out_trade_no`/`transaction_id` 为非空字符串、`trade_state` 属于已知集合、`currency == CNY`、`amount.total` 为严格正整数（`bool` 因 `type(x) is int` 被拒绝，浮点/字符串/0/负数均拒绝）。返回归一化字典：`out_trade_no`、`transaction_id`、`trade_state`、`amount`（int）、`currency`、`mchid`、`appid`、`event_type`、`raw`。 |

### 2.2 `bidcraft/api/routes/billing.py`

| 位置 | 改动 |
|---|---|
| `MAX_NOTIFY_BODY_BYTES` / `_notify_fail()` | 新增 1 MiB 常量与统一的 `{"code": "FAIL", "message": ...}` JSON 响应构造。 |
| `wechat_notify()` | body 超过 1 MiB 直接 413 FAIL（解析前拦截）；解析失败仍返回 400 FAIL；二次校验归一化字段的类型/取值；订单不存在 404 FAIL；`order.provider != "wechat"` 返回 FAIL，不允许其他渠道订单被微信回调入账；金额不一致 400 FAIL；非 SUCCESS 状态 ACK（`SUCCESS`）但不调用 `mark_paid`、不写审计；SUCCESS 必须有非空 `transaction_id`；`mark_paid(db, order, transaction_id=..., provider="wechat")` 调用签名保持不变；重复回调依赖 `mark_paid` 幂等返回 `False`，仍只写一条 `billing.payment.notify` 审计。 |
| `mock_pay()` | 保留 `settings.wechat_pay_mock` 总闸（关闭时 404），并通过 `require_own_order` 后校验 `order.provider == "mock"`，其他渠道订单返回 404，避免临时开启 mock 时零成本支付真实微信订单。 |

## 3. 测试

### 3.1 `tests/test_wechatpay.py`（更新）

- `pay_env` 增加 `settings.wechat_pay_platform_serial_no` 设置；`_signed_headers()` 改用当前时间签名，默认带上正确的 `wechatpay-serial` 与 `wechatpay-signature-type`，并可按用例覆盖/省略；`_encrypted_notification()` 默认生成含 mchid/appid/currency 的合规密文。
- 新增/扩展用例：大小写混合头、缺签名类型仍验收、时间戳陈旧/未来/非数字、错误或缺失平台序列号、错误签名类型、成功解析的归一化字段、已知非 SUCCESS 状态、未知交易状态、事件类型/加密算法错误、mchid/appid 错配或缺失、订单号/交易号非法、金额布尔/浮点/字符串/0/负数、`InvalidTag` 坏密文、币种错误。
- 保留原有下单、验签失败、缺少平台证书、缺少 APIv3 密钥等断言，未删除或放宽。

### 3.2 `tests/test_fix2_runtime_services.py`（更新）

- `test_duplicate_wechat_notify_writes_single_audit` 中 monkeypatch 的解析结果补齐归一化字段（mchid/appid/currency/event_type/raw），金额错配场景由旧的 `{"total": 1}` 改为新的归一化 `amount: 1`；原有「金额不一致 400」「两次回调仅一条审计」「订单最终 paid」断言不变。

### 3.3 `tests/test_v0340_payment_security.py`（新增）

- 配置：`test_configured_requires_platform_cert_and_serial`。
- 回调：`test_notify_rejects_oversized_body`、`test_notify_accepts_body_at_size_limit`、`test_notify_rejects_non_wechat_provider`、`test_notify_acks_non_success_without_credit[CLOSED/FAIL/PAYERROR]`、`test_notify_rejects_invalid_normalized_fields`（15 组非法字段）、`test_notify_rejects_amount_mismatch`、`test_notify_marks_paid_with_valid_payload`。
- 模拟支付：`test_mock_pay_rejects_wechat_order_even_when_mock_enabled`、`test_mock_pay_still_requires_mock_setting`、`test_mock_pay_still_works_for_mock_provider`。

## 4. 验证结果

在本机当前工作树执行（未运行全量套件）：

- `.venv/bin/ruff check bidcraft tests` → `All checks passed!`
- `.venv/bin/python -m pytest -o addopts= -q tests/test_wechatpay.py tests/test_fix2_runtime_services.py tests/test_v0340_payment_security.py` → `82 passed, 1 warning in 16.55s`（唯一 warning 为 Starlette/httpx TestClient 弃用提示）。
- 分文件计数：`test_wechatpay.py` 52 passed、`test_fix2_runtime_services.py` 3 passed、`test_v0340_payment_security.py` 27 passed。
- 测试顺序遵循 TEST FIRST：先落测试确认 57 failed/25 passed（旧实现不满足新约束），再改生产代码，最终 82 passed。

> 注：验证期间同一工作树曾有其它并发任务对 `bidcraft/models.py` 的未提交改动，一度使 `create_app()` 相关测试收集失败（SQLAlchemy 不接受 `Index(comment=...)`）。该并发改动随后已自行修正；同时在 `HEAD` 的隔离副本中复核同样得到 82 passed。本轮六个文件未参与该修改。

## 5. 残留风险

1. **并发入账竞态（V0340-P0-01）**：本轮未改 `billing.mark_paid` 的条件 UPDATE / 唯一索引（不在文件所有权范围内）；顺序重复回调幂等成立，但真正并发回调仍依赖数据库层修复。
2. **时钟依赖**：300 秒新鲜度窗口依赖业务服务器与微信服务器时钟同步；时钟漂移超过窗口会拒绝合法回调（Fail-closed，需运维监控）。
3. **证书轮换**：`wechatpay-serial` 必须与 `BIDCRAFT_WECHAT_PAY_PLATFORM_SERIAL_NO` 精确一致；轮换平台证书时必须同步更新配置与证书文件，否则回调全部拒绝。
4. **传输层**：1 MiB 限制在应用层 body 读取后生效；反向代理层仍应配置请求体上限与敏感路径限流（P1-01 的限流部分不在本轮范围）。
5. **已知状态集合**：`KNOWN_TRADE_STATES` 包含微信支付 v3 常见状态及本轮要求的 `FAIL`；若微信新增状态，需显式加入集合，否则该回调被拒绝（Fail-closed）。
6. **密钥材料**：报告不包含任何私钥、APIv3 密钥、商户号真实值；测试中的密钥与商户号均为临时生成的假值。

## 6. 回滚

本轮无数据库迁移、无配置 schema 变更、无提交/推送。需要回滚时：

- 恢复生产代码：`git checkout -- bidcraft/wechatpay.py bidcraft/api/routes/billing.py`
- 恢复测试：`git checkout -- tests/test_wechatpay.py tests/test_fix2_runtime_services.py`
- 删除新增文件：`rm tests/test_v0340_payment_security.py docs/development/rc/v0.34.0/03_PAYMENT_SECURITY_REPORT.md`

注意：`bidcraft/config.py` 中 `wechat_pay_platform_serial_no` 字段及其环境变量由其它任务引入，本轮未修改，也不应随本轮回滚一并删除。回滚后回调将恢复旧行为（无新鲜度/序列号/字段强校验），仅在确认已修复上游风险后进行。
