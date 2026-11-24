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

## 报告与问题处置接口

| 入口 | 返回 | 说明 |
| --- | --- | --- |
| `GET /projects/{ref}/evaluation` | 最新报告（含 findings） | 页面初始真相源 |
| `GET /projects/{ref}/evaluation/reports?limit=20` | 历史报告列表 | 快照不可变；为控制体积，列表不含 findings |
| `GET /projects/{ref}/evaluation/reports/{report_id}` | 单份报告（含 findings） | 按需读取详情 |
| `GET /projects/{ref}/evaluation/reports/{report_id}/findings` | 问题清单 | 前端选中历史报告时调用，避免空问题列表 |
| `PATCH /projects/{ref}/evaluation/findings/{finding_id}` | 更新后的问题 | 请求体 `{status, note?}`；`note` 与操作人写入 `evidence.resolution_note` / `evidence.resolved_by`，不改写报告快照 |

处置状态支持 `handled` / `ignored`，也支持 `open`（重新打开）；每次处置都写审计并
保留意见与处置人，历史报告的 findings 同样可处置，处置结果回写在同一份快照的 finding 上。

## 前端

质量评估页保留「运行评估」（同步），「后台评估」按钮：提交后通过 `watchJob`
轮询任务进度（含进度百分比、完成自动刷新、失败提示）。报告六维中的「商务完整性」得分
直接反映真实必填项完成率，问题清单中的商务完整性 finding 可正常标记处置。

历史报告区点击任意版本会按需调用 findings 详情接口（列表接口不返回 findings，
直接切换会显示空问题列表）。问题卡片可填写处置意见，标记已处置/忽略后展示处置人与
意见，并可「重新打开」。

## 尚未提供：报告导出与批量看板

质量报告目前**仅支持在线查看与处置**：没有报告导出端点（DOCX/PDF/XLSX），也没有跨项目
批量质量看板 UI，属于未实现/暂缓能力（见 `docs/BIDCRAFT_RECON_INVENTORY.md` 模块 8）。
报告快照含逐条 finding 与证据，导出格式与脱敏口径尚未定稿；现有
`POST /projects/{ref}/export` 针对投标文件正文，不复用为质量报告导出。
