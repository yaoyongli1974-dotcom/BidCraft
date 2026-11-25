# G8 AI 业务质量 RC 样本验收报告（RC v0.31.3）

## 1. 结论

| 项目 | 状态 |
|---|---|
| 真实模型生成质量 | D NOT VERIFIED |
| 真实脱敏招标文件与人工标注 | D NOT VERIFIED |
| 指标计算与未测量口径（隔离 RC/合成标注） | A PASS |
| E2E 异步查重流程 | A PASS |

## 2. 实测证据

G4 E2E 在 RC 环境中完成：`similarity_check` 返回 202，`similarity_report_poll` 返回 200，`FINAL_REPORT_STATUS=done`，说明评估/查重任务链可以正常入队、执行、落库。

G8 指标口径在真实 PG 上已有测试证据（阶段 4）：`tests/postgres/test_v0313_quality_metrics.py` 4 passed，验证：

- 项目指标：2 份正文 / 1 份人工修改 → `manual_edit_rate=0.5`；4 个中文词 + 1 个未处置 major → `issue_density=250/千词`；2 条 BOQ / 1 条匹配 → `boq_match_rate=0.5`。
- 缺 manifest → `status=未测量`、比率为 `null`、原因明确。
- 文件头含 `SYNTHETIC` → 数据集不就绪，不冒充真实件。
- 人为构造的带标注数据集可测得 `clause_recall=1.0`、`value_parse_rate=1.0`、`verdict_agreement=1.0`。

## 3. 限制

RC 环境使用不可达的假模型地址 `http://127.0.0.1:9/v1`，仅满足启动配置校验，没有任何真实模型调用；用户也未提供真实脱敏招标文件、应答样本与人工标注。因此本轮**不得**把合成数据上的 1.0 结果表述为真实投标质量达标。

## 4. 结论与后续

AI 业务质量的流程与指标口径已具备验收条件，但真实质量水平仍为 NOT VERIFIED。后续需由用户提供脱敏真实样本、人工标注与可用模型端点后，按同一数据集契约补跑并固化阈值。
