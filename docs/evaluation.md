# 质量评估与商务完整性联动

## 运行方式

| 入口 | 行为 | 返回 |
| --- | --- | --- |
| `POST /projects/{ref}/evaluation/run` | 在请求线程内同步生成报告（兼容旧调用） | `201` + 报告快照 |
| `POST /projects/{ref}/evaluation/run-async` | 提交后台任务（`kind=evaluation`） | `202` + `{job_id}` |

后台任务用 `GET /jobs/{job_id}` 或 `GET /jobs/{job_id}/events`（SSE）跟踪；`Job.result`
含 `report_id`、`report_version`、`overall_score`、`grade`、`verdict`。终态后重新拉取
`GET /projects/{ref}/evaluation` 即为最新快照。队列背压/重复提交沿用既有 Job 机制：
单项目已有排队或运行的评估任务返回 `409`，全局/单用户排队超限返回 `429`。

## 商务完整性如何进入评分

`bidcraft/evaluation/commercial.py` 读取项目最新 `CommercialDoc`：

- 优先使用保存时落盘的 `validation.completeness`；旧文档缺该字段时按当前必填规则重算；
- 完成率 = `(checked - len(missing)) / checked`，写入 `summary.commercial` 并驱动
  `commercial` 维度得分（权重 10%），不再使用 major issue 数的代理分；
- 必填项未完成时额外生成一条 `商务完整性` major finding，并计入 `risk_summary.by_level.major`，
  因此结论不会在商务标明显缺失时被判为 `pass`；
- 项目没有商务标文档时保留旧代理逻辑，`summary.commercial` 不出现，避免误导。

### 可选送审硬门禁

`submit_commercial` 增加可选参数 `require_complete`（默认 `False`，保持既有行为）。打开后，
若最新商务标必填项未填齐，则拒绝送审并列出缺失字段（最多显示 8 项）。启用方式：

- API：`POST /projects/{ref}/commercial/{doc_id}/submit` 请求体加 `{"require_complete": true}`；
- 前端：送审弹窗勾选「严格校验完整性」。

该门禁与质量评估相互独立：即使关闭门禁，不完整的商务标仍会在评估报告中计入
`commercial` 维度得分与 major finding。

## 前端

质量评估页保留「运行评估」（同步），新增「后台评估」按钮：提交后通过 `watchJob`
轮询任务进度，完成后自动刷新最新报告。报告六维中的「商务完整性」得分直接反映真实
必填项完成率，问题清单中的商务完整性 finding 可正常标记处置。
