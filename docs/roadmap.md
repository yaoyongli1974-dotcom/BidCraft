# 开发进度（对照 C 方案）

> 评估原文：`/media/yao/Data/workbuddy/zhibiao/bid-system-redev-assessment.html`
> 原则：只借规格、不复制代码；上游 AGPL 源码仅作行为参考。

| 模块 | 状态 | 落地位置 | 说明 |
|---|---|---|---|
| C1 领域规格 | ✅ | `bidcraft/resources/schema/`、`bidcraft/resources/packages/`、`docs/C1-spec.md` | 事实台账 Schema（pydantic 生成）、YAML 包格式、安全表达式 DSL |
| C2 解析与台账 | ✅ | `bidcraft/parsing/`、`bidcraft/ledger.py` | PDF/DOCX/TXT/XLSX；可选扫描 PDF OCR；字段、评分、材料、参数、要求、风险；人工修正与出处 |
| C3 规则引擎 | ✅ | `bidcraft/rules/` | 章节识别、评分对齐、18 条/包核对；blocking 门禁 |
| C4 生成链路 | ✅ | `bidcraft/generation/` | 大纲 → 逐章流式/续写 → 事实注入 → 去重 → 数字复核 → 确定性回落 |
| C5 复核 | ✅ 最小 | `bidcraft/rules/engine.py`、`check` 命令 | 四级问题、位置、证据、修订建议；不自动改写正文 |
| C6 交付 | ✅ 最小 | `bidcraft/export/` | DOCX/PDF（TOC 页码）/Markdown/XLSX |
| 图片生成与插入 | ✅ | `images.py`、`/projects/{ref}/images`、`web/` | 按章节生成/重生成/上传 PNG·JPEG；提示词、风格、尺寸、章节正文上下文；失败资产保留可重试；图名精确匹配占位符或按光标插入；项目图库跨章节复用；全文配图计划统计各章占位/已匹配/失败/缺失并按字数计算章节预算；按计划后台批量生成缺失/失败配图；一键按预算插入已匹配配图；DOCX 嵌入 |
| 整篇配图策略与自动排版 | ✅ | `images.py`、`/projects/{ref}/image-policy`、`export/docx.py`、`web/` | 全文上限/默认风格尺寸/图注开关与前缀/版心宽度；计划返回全文预算与超限；按计划插入遵守全文预算；「生成并插入」一键编排；DOCX 跨章连续图注编号与统一宽度 |
| MCP 服务 | ✅ | `bidcraft/mcp.py`、`bidcraft mcp` | stdio JSON-RPC 2.0（MCP 2024-11-05）；6 工具：项目/详情/台账/核对/导出/知识检索；`--readonly` 只读；见 `docs/mcp.md` |
| 批量操作与标段 | ✅ | `api.py`、`/projects/batch-check`、`/projects/batch-export`、`web/` | 项目多选批量核对/批量导出（docx/md/xlsx 打包 zip 下载）；标段/包件字段创建/编辑/筛选；后台 Job 逐项目事件与部分失败容忍 |
| 多包件共享台账 | ✅ | `models.py`、`boq.py`、`/projects/{ref}/packages`、`web/` | 同项目多包件独立清单快照与报价、事实台账共享不复制；首包自动收养旧快照；分包导出文件名含包件名 |
| 企业定额库与调价 | ✅ | `models.py`、`boq.py`、`/admin/quotas`、`web/` | 定额 CRUD/模糊匹配；清单定额套用 dry-run 预览与按勾选写入；项目调价策略（系数/小数位）与按系数调价；报价汇总增调整后合计 |
| 回归校准 | ✅ | `calibrate.py`、`bidcraft calibrate`、`tests/fixtures/regression/` | 只读扫描目录统计字段命中/必填命中率/材料行/OCR 需求；真实招标别名增量；仿真 fixtures 与报告 JSON；不写库不建项目 |
| 插件市场（MCP 客户端） | ✅ | `plugins.py`、`/admin/plugins`、`mcp.py`、`docs/plugins.md` | 超管注册 stdio/http MCP 插件、连通性测试与工具白名单；`bidcraft mcp` 聚合插件工具（`{插件}__{工具}` 前缀），单插件失败跳过，只读模式拒绝插件调用 |
| 报价版本与汇率 | ✅ | `models.py`、`boq.py`、`/projects/{ref}/boq/versions`、`web/` | 报价版本快照/恢复/删除（上限 20、包件隔离）；版本与当前清单或另一版本 diff（新增/移除/变动 + 两侧汇总）；税率与币种、汇率折算人民币含税合计；导出增税率/税额/汇率/人民币合计行 |
| 微信开放平台管理 | ✅ | `models.py`、`wechat.py`、`/admin/wechat`、`web/` | 管理端配置 AppID/AppSecret（加密存库不回显）、回调地址、启用与模拟模式；连通性测试；保存即热生效（`/health.wechat_login` 与登录页入口）；审计 `wechat.config` |
| 企业术语库 | ✅ | `models.py`、`terms.py`、`/admin/terms`、`web/` | 禁用表述与建议替换术语 CRUD/搜索/启停；项目核对逐章扫描产出 `TERM-###`（major/minor，含证据片段与改写建议），跨章去重、上限 200 |
| C7 可插拔 Agent / API | ✅ | `generation/agent.py`、`bidcraft/api.py`、`bidcraft/mcp.py` | OpenAI 兼容端点；FastAPI + 后台生成任务；MCP stdio 服务 |
| C8 行业包 | ✅ 8 个行业包 + 通用基包 | `bidcraft/resources/packages/*.yaml` | 弱电（综合布线/安防/BA）+ 消防、安装、土建、暖通、装修；管理员可在 `/admin` 维护 YAML 覆盖包 |
| 规则包管理 | ✅ | `rules/store.py`、`/admin/rule-packages`、`web/` | 超级管理员创建/修改/隐藏/恢复规则包；新建时可填写名称、唯一包名并选择工程类型；内置包只读，用户覆盖写入 `BIDCRAFT_HOME/packages`；保存前校验继承、字段、大纲与核对表达式 |
| C9 回归/部署 | ✅ 基本 | `tests/`、`Dockerfile`、`web/` | 337 个测试；Docker 多阶段构建 Web（含 OCR 依赖）；本地/局域网两种部署 |
| SSE 流式进度 | ✅ | `bidcraft/jobs.py`、`/jobs/{id}/events`、`web/` | 章节/逐字事件持久化；Last-Event-ID 断线续传；SSE 失败回退轮询；刷新后重新挂接任务 |
| Excel 清单双向对齐 | ✅ | `boq.py`、`export/boq_xlsx.py`、`/projects/{ref}/boq`、`web/` | 清单导入与表头识别、材料模糊匹配、缺项/多项/数量·单位·规格差异、差异回写、响应表导出 |
| 清单报价 | ✅ | `boq.py`、`/projects/{ref}/boq/price`、`export/boq_xlsx.py`、`web/` | 逐行单价录入/清除、按清单数量计算合价、已报价/未报价/缺数量统计、报价汇总与响应表价格列；不自动生成价格 |
| 扫描件 OCR | ✅ 可选 | `parsing/ocr.py`、`parsing/loader.py`、`doctor` | `pdftoppm + tesseract`；只识别无文本页；语言包缺失回退/降级；限页与单页失败继续 |
| 登录与 RBAC | ✅ | `auth.py`、`security.py`、`web/` | Cookie 会话、scrypt、三角色、用户管理、最后管理员保护 |
| 项目级权限与审计 | ✅ | `models.py`、`/projects/{ref}/members`、`/projects/{ref}/audit`、`/admin/audit`、`web/` | 成员 editor/viewer 由 owner 管理；章节 base_revision 乐观锁 409；29 类操作审计与 before 分页 |
| 模型配置中心 | ✅ | `model_config.py`、`/admin/models`、`web/` | 对话/图片/向量模型；默认对话与图片模型接入生成；连通性测试；密钥加密 |
| 知识库对接 | ✅ | `knowledge.py`、`/admin/kbs`、`web/` | 本地目录索引；通用/Dify/RAGFlow API；章节检索注入；台账优先 |
| 向量语义检索 | ✅ 可选 | `knowledge.py`、`model_config.resolve_embedding_endpoint`、`/admin/kbs/{id}/embed`、`web/` | embedding 向量索引与混合重排；换模型自动失效；无模型/失败回退关键词召回 |
| 注册与计费 | ✅ | `billing.py`、`wechat.py`、`wechatpay.py`、`web/` | 用户名/微信扫码注册、独立项目、按章扣点、兑换码、微信支付 Native/mock |

## 已覆盖的 P0

- 零遥测，数据默认本地（SQLite + 原件目录）。
- 解析 + 事实台账 + 人工确认；必填缺失拒绝生成。
- 废标/实质性条款专章、参数响应率、数量一致性、项目身份一致性。
- 规则全部外置；新增行业只加 YAML，管理员可在控制台直接维护覆盖包。
- 项目类型不再局限于弱电：消防、安装、土建、暖通、装修均有独立规则包与章节骨架。

## 明确未做（按优先级）

1. **插件沙箱与自动安装**：插件已可按本机子进程/HTTP 运行并由超管配置，但仍在服务端进程的
   操作系统用户下执行，没有隔离沙箱，也不会从远程市场自动安装。
2. **报价审批流与多版本并排审阅**：报价已可保存版本、恢复与逐项对比，但缺少审批流转
   （提交/驳回/定稿）与多版本并排审阅视图。
3. **真实招标件持续回归基线扩充**：`bidcraft calibrate` 已能跑真实目录，但仓库内只有仿真
   fixtures；真实件需用户自行提供并持续积累成基线。

## 建议的下一迭代

1. 用真实招标件跑 `bidcraft calibrate`，按字段命中率与别名缺口扩充回归基线与 `FIELD_ALIAS_AUGMENT`。
2. 报价进阶：审批流转与定稿、多版本并排审阅，在现有版本/汇率链路上扩展。
3. 插件沙箱与自动安装：限制子进程文件/网络访问，提供受控的插件安装与升级流程。
