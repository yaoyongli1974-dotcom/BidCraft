# 开发进度（对照 C 方案）

> 评估原文：`/media/yao/Data/workbuddy/zhibiao/bid-system-redev-assessment.html`
> 原则：只借规格、不复制代码；上游 AGPL 源码仅作行为参考。

| 模块 | 状态 | 落地位置 | 说明 |
|---|---|---|---|
| C1 领域规格 | ✅ | `bidcraft/resources/schema/`、`bidcraft/resources/packages/`、`docs/C1-spec.md` | 事实台账 Schema（pydantic 生成）、YAML 包格式、安全表达式 DSL |
| C2 解析与台账 | ✅ | `bidcraft/parsing/`、`bidcraft/ledger.py` | PDF/DOCX/TXT/XLSX；可选扫描 PDF OCR；字段、评分、材料、参数、要求、风险；人工修正与出处 |
| C3 规则引擎 | ✅ | `bidcraft/rules/` | 章节识别、评分对齐、18 条/包核对；blocking 门禁 |
| C4 生成链路 | ✅ | `bidcraft/generation/` | 大纲 → 逐章流式/续写 → 事实注入 → 去重 → 数字复核 → 确定性回落 |
| C5 复核 | ✅ | `bidcraft/rules/engine.py`、`bidcraft/export_gate.py`、`web/src/pages/tabs/RiskTab.tsx` | 四级问题、位置、证据、修订建议；问题锚定章节、处置状态跨核对保留；风险总览四档聚合 + 跳转定位 + 未处置阻断项拦截导出（owner/admin 可强制导出并留痕）；不自动改写正文 |
| C6 交付 | ✅ 最小 | `bidcraft/export/` | DOCX/PDF（TOC 页码）/Markdown/XLSX |
| 图片生成与插入 | ✅ | `images.py`、`/projects/{ref}/images`、`web/` | 按章节生成/重生成/上传 PNG·JPEG；提示词、风格、尺寸、章节正文上下文；失败资产保留可重试；图名精确匹配占位符或按光标插入；项目图库跨章节复用；全文配图计划统计各章占位/已匹配/失败/缺失并按字数计算章节预算；按计划后台批量生成缺失/失败配图；一键按预算插入已匹配配图；DOCX 嵌入 |
| 整篇配图策略与自动排版 | ✅ | `images.py`、`/projects/{ref}/image-policy`、`export/docx.py`、`web/` | 全文上限/默认风格尺寸/图注开关与前缀/版心宽度；计划返回全文预算与超限；按计划插入遵守全文预算；「生成并插入」一键编排；DOCX 跨章连续图注编号与统一宽度 |
| MCP 服务 | ✅ | `bidcraft/mcp.py`、`bidcraft mcp` | stdio JSON-RPC 2.0（MCP 2024-11-05）；6 工具：项目/详情/台账/核对/导出/知识检索；`--readonly` 只读；见 `docs/mcp.md` |
| 批量操作与标段 | ✅ | `api.py`、`/projects/batch-check`、`/projects/batch-export`、`web/` | 项目多选批量核对/批量导出（docx/md/xlsx 打包 zip 下载）；标段/包件字段创建/编辑/筛选；后台 Job 逐项目事件与部分失败容忍 |
| 多包件共享台账 | ✅ | `models.py`、`boq.py`、`/projects/{ref}/packages`、`web/` | 同项目多包件独立清单快照与报价、事实台账共享不复制；首包自动收养旧快照；分包导出文件名含包件名 |
| 项目复制 | ✅ | `duplicate.py`、`POST /projects/{ref}/duplicate`、`web/` | 复制为新项目：招标原件/台账/大纲/正文始终复制，清单报价与配图可选；报价版本与审批记录、问题/任务/成员/审计不复制；失败清理新目录；复制者=新负责人 |
| 项目模板库 | ✅ | `models.py`（is_template）、`/projects/templates`、`web/` | 负责人设为模板（含存量迁移）；全员浏览模板列表；任何登录用户从模板创建自己的项目（无需成员身份，复用复制引擎）；审计 `project.instantiate` |
| 企业定额库与调价 | ✅ | `models.py`、`boq.py`、`/admin/quotas`、`web/` | 定额 CRUD/模糊匹配；清单定额套用 dry-run 预览与按勾选写入；项目调价策略（系数/小数位）与按系数调价；报价汇总增调整后合计 |
| 回归校准 | ✅ | `calibrate.py`、`bidcraft calibrate`、`tests/fixtures/regression/` | 只读扫描目录统计字段命中/必填命中率/材料行/OCR 需求；真实招标别名增量；仿真 fixtures 与报告 JSON；`--save-baseline/--compare-baseline` 基线保存/比对（回归 exit 1、基线缺失/无效 exit 2）；不写库不建项目 |
| 插件市场（MCP 客户端） | ✅ | `plugins.py`、`plugin_pack.py`、`/admin/plugins`、`mcp.py`、`docs/plugins.md` | 超管注册 stdio/http MCP 插件、连通性测试与工具白名单；`bidcraft mcp` 聚合插件工具（`{插件}__{工具}` 前缀），单插件失败跳过，只读模式拒绝插件调用；`.bidplugin` 插件包导入/导出与同名覆盖升级 |
| 报价版本与汇率 | ✅ | `models.py`、`boq.py`、`/projects/{ref}/boq/versions`、`web/` | 报价版本快照/恢复/删除（上限 20、包件隔离）；版本与当前清单或另一版本 diff（新增/移除/变动 + 两侧汇总）；税率与币种、汇率折算人民币含税合计；导出增税率/税额/汇率/人民币合计行 |
| 报价审批流 | ✅ | `boq.py`、`/projects/{ref}/boq/versions/{id}/submit|review|unapprove`、`web/` | 版本状态机 draft→submitted→approved/rejected：可编辑成员提交（备注），负责人/超管定稿或驳回（意见），approved 可撤销回草稿；同包件至多一个定稿、定稿新版自动退旧版；rejected 重提清空上轮意见；审计 quote.submit/approve/reject/unapprove；前端状态徽标与双版本并排审阅；项目审批链（≤5 级、每级 1–5 人会签 all/或签 any、成员校验、名称快照）逐级审批，末级定稿、越级 403、超管代审，空链回退负责人单级；审批级可设金额门槛 `min_amount`（总额达到才生效，低价跳过，`approval_stage_count` 按生效级数）；`GET /approvals/pending` 跨项目待我审批（顶栏徽标+只读面板，点击直达项目清单页签）；审批级可按包件 `packages`/币种 `currencies` 白名单限定生效范围（与金额门槛组合，未命中跳过，全过滤回退全部） |
| 微信开放平台管理 | ✅ | `models.py`、`wechat.py`、`/admin/wechat`、`web/` | 管理端配置 AppID/AppSecret（加密存库不回显）、回调地址、启用与模拟模式；连通性测试；保存即热生效（`/health.wechat_login` 与登录页入口）；审计 `wechat.config` |
| 企业术语库 | ✅ | `models.py`、`terms.py`、`/admin/terms`、`web/` | 禁用表述与建议替换术语 CRUD/搜索/启停；项目核对逐章扫描产出 `TERM-###`（major/minor，含证据片段与改写建议），跨章去重、上限 200 |
| C7 可插拔 Agent / API | ✅ | `generation/agent.py`、`bidcraft/api.py`、`bidcraft/mcp.py` | OpenAI 兼容端点；FastAPI + 后台生成任务；MCP stdio 服务 |
| C8 行业包 | ✅ 8 个行业包 + 通用基包 | `bidcraft/resources/packages/*.yaml` | 弱电（综合布线/安防/BA）+ 消防、安装、土建、暖通、装修；管理员可在 `/admin` 维护 YAML 覆盖包 |
| 规则包管理 | ✅ | `rules/store.py`、`/admin/rule-packages`、`web/` | 超级管理员创建/修改/隐藏/恢复规则包；新建时可填写名称、唯一包名并选择工程类型；内置包只读，用户覆盖写入 `BIDCRAFT_HOME/packages`；保存前校验继承、字段、大纲与核对表达式 |
| C9 回归/部署 | ✅ 基本 | `tests/`、`Dockerfile`、`web/` | 1221 个测试；Docker 多阶段构建 Web（含 OCR 依赖）；本地/局域网两种部署 |
| SSE 流式进度 | ✅ | `bidcraft/jobs.py`、`/jobs/{id}/events`、`web/` | 章节/逐字事件持久化；Last-Event-ID 断线续传；SSE 失败回退轮询；刷新后重新挂接任务 |
| Excel 清单双向对齐 | ✅ | `boq.py`、`export/boq_xlsx.py`、`/projects/{ref}/boq`、`web/` | 清单导入与表头识别、材料模糊匹配、缺项/多项/数量·单位·规格差异、差异回写、响应表导出 |
| 清单报价 | ✅ | `boq.py`、`/projects/{ref}/boq/price`、`export/boq_xlsx.py`、`web/` | 逐行单价录入/清除、按清单数量计算合价、已报价/未报价/缺数量统计、报价汇总与响应表价格列；不自动生成价格 |
| 扫描件 OCR | ✅ 可选 | `parsing/ocr.py`、`parsing/loader.py`、`doctor` | `pdftoppm + tesseract`；只识别无文本页；语言包缺失回退/降级；限页与单页失败继续 |
| 登录与 RBAC | ✅ | `auth.py`、`security.py`、`web/` | Cookie 会话、scrypt、三角色、用户管理、最后管理员保护 |
| 项目级权限与审计 | ✅ | `models.py`、`/projects/{ref}/members`、`/projects/{ref}/audit`、`/admin/audit`、`web/` | 成员 editor/viewer 由 owner 管理；章节 base_revision 乐观锁 409；29 类操作审计与 before 分页 |
| 模型配置中心 | ✅ | `model_config.py`、`/admin/models`、`web/` | 对话/图片/向量模型；默认对话与图片模型接入生成；连通性测试；密钥加密 |
| 知识库对接 | ✅ | `knowledge.py`、`/admin/kbs`、`web/` | 本地目录索引；通用/Dify/RAGFlow API；章节检索注入；台账优先 |
| 向量语义检索 | ✅ 可选 | `knowledge.py`、`model_config.resolve_embedding_endpoint`、`/admin/kbs/{id}/embed`、`web/` | embedding 向量索引与混合重排；换模型自动失效；无模型/失败回退关键词召回 |
| 桌面客户端（C/S） | 📦 已归档 v0.6.3 | `desktop.py`、`client.py`、`bidcraft desktop`、`bidcraft client`、`docs/desktop.md` | Qt 原生外壳 + QWebChannel 桥直连 REST API（`--browser` 保留本地网关：仅 127.0.0.1 + 令牌门禁，作为回退）；Cookie/SSE 透传、静态与 SPA 回退；本地文件桥选择本机文件直传我的文档/项目原件；命令行客户端登录/项目/上传/下载与 Cookie 持久化。冻结于 v0.6.3，不再新增功能；依赖冻结配方（`desktop`/`build` extra + `uv.lock`），CI 仅手动触发且默认不附加 Release |
| 时区一致性 | ✅ | `clock.py`、`tests/test_clock.py` | 存储 naive UTC；日历日按北京时间；对外时间戳带偏移；源级护栏防回退 |
| 素材可信度分级 | ✅ | `material_policy.py`、`resources/packages/*.yaml`、`/admin/kbs/{id}/docs` | 规则包 `material_policy` 声明每类素材必填生命周期字段；检索注入按可信度分级，缺失/过期降级为题录；有效期按北京时间判定（`clock.py`），未知/缺失 kind 失败关闭，写入侧类型必填；「其他素材」必须带出处（`source_url`），不留免检类别；文档元数据存 `knowledge_bases.options.docs` |
| 解析深度（MinerU） | ✅ 可选 | `parsing/mineru.py`、`BIDCRAFT_MINERU_URL`、`bidcraft mineru-check` | 复杂版面/扫描件交给 MinerU，取 Markdown + 表格；失败/超时回退内置链路；页数为估算 |
| 全局事实设定 | ✅ | `profile.py`、`/projects/{ref}/profile`、`generation/prompts.py`、`textqa.profile_conflicts` | 项目级事实注入生成；核对按锚点比对，不一致报 CON；存 `project.meta["writing_profile"]` |
| 实质性条款响应绑定 | ✅ | `substantive.py`、`parsing/extract.py`、`/projects/{ref}/requirements`、`web/RiskTab` | ★/▲ 保留为一等标记与原文行；3-gram 覆盖判定逐条对照正文，未响应即 blocking/critical 并进导出硬门禁；人工确认「已响应/不适用」带原因与操作人留痕，可撤销；总览页可跳转覆盖章节 |
| 评分回填与失分复盘 | ✅ | `scoring.py`、`/projects/{ref}/scores`、`web/` | 评分项实际得分回填与得分率/分类聚合/失分 Top20；失分摘要注入生成提示 |
| 长文一致性续写 | ✅ | `generation/writer.continuity_digest` | 全书章节索引 + 近期摘要注入，跨章承接与去重 |
| 导出格式模板 | ✅ | `export/templates.py`、`/projects/{ref}/export-template` | 四套预设与项目级记忆；DOCX 字体/字号/行距/页边距/封面目录页码 |
| 合规与账号 | ✅ | `compliance.py`、`/legal/*`、`/account/export|delete|billing-profile`、`/billing/statement`、`bidcraft backup-verify` | 条款页、数据导出、对账开票、注销、备份校验 |
| 合规闭环（同意/开票/保留） | ✅ | `compliance.py`、`invoices.py`、`/billing/invoices`、`/admin/invoices` | 注册同意版本留痕；开票申请与开具/驳回；备份保留策略 `backup-prune` |
| 注销冷静期与删除审计 | ✅ | `compliance.py`、`/account/deletion`、`/admin/accounts/purge`、`bidcraft purge-accounts` | 7 天冷静期、可撤销、到期批量删除、`account.purge` 审计留痕 |
| 文本规则与查重 | ✅ | `textqa.py`、`dedup.py`、`/admin/textqa`、`bidcraft dedup` | 自定义错别字词库（存 `AppSetting["textqa"]`，上限 500）；跨文件段落查重报告，只读不联网 |
| 文本质量检查 | ✅ | `textqa.py`、`pipeline.check_project`、问题台账 | 错别字/重复字/重复标点、跨章节事实一致性、段落近似重复；确定性规则，不依赖模型 |
| 人员与素材元数据 | ✅ | `personnel.py`、`/my/personnel`、`/admin/kbs/{id}/docs`、`web/` | 人员台账（证书号/社保月数/在建锁定）；知识库逐文档元数据登记与可信度判定，缺字段降级题录 |
| 资质与投标前自查 | ✅ | `qualifications.py`、`/my/qualifications`、`/my/preflight`、`web/` | 资质/业绩台账与证据存证；按开标日六项自查（有效/临期/过期/缺失）与到期预警；不联网查询官方平台，只留人工截图证据 |
| 我的文档（个人知识库） | ✅ | `user_docs.py`、`models.UserDoc`、`/my/docs`、`web/MyDocsPage` | 普通用户上传文件到服务器个人目录并自动建索引；个人 local KB（options.personal）；生成检索只注入本人个人 KB 且默认搜索排除个人库防泄漏；列表/下载/删除与审计 |
| 注册与计费 | ✅ | `billing.py`、`wechat.py`、`wechatpay.py`、`web/` | 用户名/微信扫码注册、独立项目、按章扣点、兑换码、微信支付 Native/mock |
| VIP 会员 | ✅ | `billing.py`、`/admin/users/{id}/vip`、`/billing/vip/orders`、`web/` | 超管授予/续期/清除 VIP、自助购买 VIP 套餐（月/季/年，mock 续期不加点）；按章扣点享 VIP 折扣（vip_discount 0.1–1）；VIP 后台任务排队优先 |
| 子账号共享计费 | ✅ | `accounts.py`、`/account/sub-accounts`、`web/` | VIP 主账号自助创建子账号（sub_account_limit 上限），子账号共享主账号点数余额、不可单独充值/兑换/购买；删除子账号项目自动移交主账号 |
| 优先级任务队列 | ✅ | `jobqueue.py`、`jobs.py`、`web/` | 后台任务由 `jobs` 表驱动（worker 原子认领 + 租约心跳 + fencing 令牌），章节幂等账本 + 心跳中止 + 优雅退出，VIP=0 优先，并发 `BIDCRAFT_MAX_JOBS`（默认 2，1–8）；job payload 回显 priority/queue_position |

## 已覆盖的 P0

- 零遥测，数据默认本地（SQLite + 原件目录）。
- 解析 + 事实台账 + 人工确认；必填缺失拒绝生成。
- 废标/实质性条款专章、参数响应率、数量一致性、项目身份一致性。
- 规则全部外置；新增行业只加 YAML，管理员可在控制台直接维护覆盖包。
- 项目类型不再局限于弱电：消防、安装、土建、暖通、装修均有独立规则包与章节骨架。

## 明确未做（按优先级）

1. **插件远程市场自动安装与系统级隔离**：应用层的沙箱、HTTP 出站地址防护与作用域隔离已完成
   （见 `docs/plugins.md`：资源限制、SSRF 防护、`scope/owner_id/allow_private_net`、审计归属）；
   但插件仍在服务端操作系统用户下运行，未做容器或网络级隔离，也没有从远程市场自动安装的流程。
   `.bidplugin` 已支持可选 Ed25519 签名验签（默认兼容未签名包，可强制验签），见 `docs/plugins.md`。
2. **真实招标件持续回归基线扩充**：基线工具链已完善（`--save-baseline/--compare-baseline` 外，
   新增 `--label/--note/--manifest/--baseline-index`、`baselines` 目录命令、指纹核对与目录索引，
   见 `docs/calibration.md`），仿真夹具基线已入库并做门禁；真实件仍需用户自行提供并持续积累。

## 建议的下一迭代

1. 用真实招标件跑 `bidcraft calibrate --save-baseline --label ... --manifest`，把聚合基线（不含原文）
   与指纹清单纳入版本库，积累后以 `--compare-baseline` / `bidcraft baselines --verify` 做回归门禁，
   按字段命中率与别名缺口扩充 `FIELD_ALIAS_AUGMENT`。
2. 报价与文档进阶：审批链已支持多级流转、并行会签/或签、金额门槛与包件/币种条件分支，后续可做规则命中条件；
   商务完整性已与质量评估联动——报告读取最新 `CommercialDoc` 的真实必填项完成率计入 `commercial`
   维度并生成 major finding；同步 `evaluation/run` 保留，长报表可用 `evaluation/run-async` 后台生成。
   `submit_commercial` 已提供可选完整性硬门禁（`require_complete=True`，前端为「严格校验完整性」勾选框），
   默认关闭以保持旧行为；如需强制，可在部署侧约定送审一律传 `require_complete: true`。
3. 插件远程市场：受控的自动安装与升级流程（远程源校验、依赖声明与回滚），以及容器/网络级隔离；`.bidplugin` 签名验签已完成。
4. **实质性条款语义增强（已完成主体）**：条款类型（资质/业绩/参数/承诺）结构化规则、金融数字与同义词归一、
   证据可审计、评分办法分值→章节篇幅分配均已落地（`bidcraft/substantive.py`、`constraint_check.py`、
   `generation/outline.py`）。剩余方向：引入可选 LLM 语义匹配器替换默认规则匹配器，并用真实件基线验证召回。
