# BidCraft B/S 架构

> 目标：浏览器即客户端，服务端集中保存项目与事实台账；既能单机运行，也能部署在
> 局域网供多名投标经理使用。除显式配置的模型端点（以及图片端点返回的临时下载 URL）
> 外，系统不发起外部网络请求。

## 1. 部署拓扑

### 单机模式

```
┌──────────────────────────── 一台电脑 ────────────────────────────┐
│ 浏览器 ──HTTP──▶ bidcraft serve (FastAPI)                        │
│                       ├─ 登录会话 + RBAC                          │
│                       ├─ 静态客户端 bidcraft/web_dist            │
│                       ├─ 业务 API /projects /jobs ...            │
│                       ├─ 管理 API /admin (超级管理员)            │
│                       ├─ SQLite  .bidcraft/bidcraft.db           │
│                       ├─ 原件/导出件  .bidcraft/projects/<id>/   │
│                       ├─ 可选：LibreOffice（PDF 目录页码刷新）   │
│                       ├─ 可选：pdftoppm + tesseract（扫描件 OCR） │
│                       └─ 可选：OpenAI 兼容模型端点               │
└──────────────────────────────────────────────────────────────────┘
```

`bidcraft serve --open` 启动后自动打开 `http://127.0.0.1:8000`。默认只监听
回环地址，数据不出本机。

### 局域网模式

```
  浏览器 A ─┐
  浏览器 B ─┼──HTTP/HTTPS──▶ Nginx/Caddy（TLS、访问控制）
  浏览器 C ─┘                │
                             ▼
                    bidcraft serve --host 0.0.0.0
                             ├─ 登录 + RBAC（内置）
                             ├─ SQLite（WAL）
                             └─ BIDCRAFT_HOME（集中备份目录）
```

客户端零安装。服务端内置 Cookie 会话与 RBAC：超级管理员管理用户、模型与知识库；
投标经理读写项目；只读成员只能查看。跨网段/公网部署仍应启用 TLS
（`BIDCRAFT_SECURE_COOKIE=1`）并限制来源。

### 备份与恢复

项目数据集中在 `BIDCRAFT_HOME`：`bidcraft.db`（SQLite，WAL 模式）、
`projects/<id>/`（招标原件、清单原件、`images/` 配图）、`packages/`（管理员规则包覆盖）、
`output/`（导出件）以及 `secret.key`（模型/知识库密钥的 Fernet 主密钥）。

**不要直接 `cp`/`tar` 运行中的整个目录**：WAL 模式下主库文件与 `-wal`/`-shm`
需整体一致，拷贝跨越写事务会得到撕裂快照，恢复后可能丢数据或直接打不开。
推荐：

1. **内置备份命令**（无需停服）：
   `bidcraft backup` 使用 SQLite 在线备份 API 把 `bidcraft.db` 复制为临时一致性
   快照，连同 `secret.key`（存在时）与 `manifest.json`（版本、创建时间、库大小、
   是否含密钥、`BIDCRAFT_HOME`）打包为
   `BIDCRAFT_HOME/backups/bidcraft-<YYYYmmdd-HHMMSS>.zip`。`-o/--output` 可指定
   目录（自动命名）或 `.zip` 文件路径，`--list` 列出已有备份。临时快照在结束后
   自动清理，不写原库。
2. **停服冷备**（最稳妥）：停止服务（或容器）后整目录打包，重启即恢复一致性；
3. **手工在线 `.backup`**：`sqlite3 $BIDCRAFT_HOME/bidcraft.db ".backup '/backup/bidcraft.db'"`
   取一致库快照，再用 `rsync` 同步 `projects/ packages/ output/ secret.key`。
   文件按增量写入，业务上可接受与库快照存在几秒偏差（库记录是权威索引）。

`secret.key` 必须随库一起备份，否则已加密的模型/知识库 API Key 无法解密，只能重新录入。
恢复时保持 `BIDCRAFT_HOME` 路径不变，并确保文件属主为运行用户（容器内 uid 10001）。
恢复步骤：**停服 → 解压备份 zip → 把 `bidcraft.db` 与 `secret.key` 放回
`BIDCRAFT_HOME` → 启动服务**。命令不自动还原，避免误覆盖线上数据。

## 2. 组件

| 层 | 实现 | 职责 |
|---|---|---|
| 客户端 | React 18 + TypeScript + Vite | 项目列表与类型分组/标段筛选/批量核对导出、上传、台账校订、清单对齐与报价、大纲、章节编辑、章节配图、项目图库与配图计划、整篇配图策略、规则包管理、协作成员与审计、核对、导出 |
| HTTP 层 | FastAPI + uvicorn | REST、静态资源托管、SPA 深链接回退、后台任务；另有 `bidcraft mcp` stdio JSON-RPC 服务供外部 Agent 接入 |
| 领域层 | `pipeline` / `ledger` / `parsing` / `rules` / `generation` / `boq` / `images` / `plugins` / `calibrate` / `terms` / `plugin_pack` / `duplicate` | 解析（含可选 OCR）、事实台账、规则包加载/覆盖、规则核对、清单对齐与报价（包件、定额、调价）、正文生成、配图与整篇排版策略、MCP 客户端插件（stdio/http 会话与工具调用，stdio 子进程受 RLIMIT_AS/RLIMIT_CPU/umask 0o077 与一次性临时工作目录约束）、只读回归校准、企业术语库与禁用表述核对、`.bidplugin` 插件包导出/导入校验与受控安装、项目复制（深拷贝与外键重映射） |
| 认证 | scrypt + Cookie 会话 + RBAC | 超级管理员/投标经理/只读成员；项目成员 editor/viewer；用户管理与会话吊销 |
| 持久化 | SQLAlchemy + SQLite（WAL） | 项目、来源、事实、材料、参数、评分、要求、风险、清单快照（含用户单价与 `package_id`）、包件（`bid_packages`）、企业定额（`quota_items`）、插件配置（`plugins`，含资源限制 `mem_limit_mb`/`cpu_seconds`）、术语库（`term_items`）、微信开放平台配置（`wechat_config`，密钥加密存 `app_secret_enc`）、图片资产（含生成参数）、大纲、章节（含 revision）、问题、任务与事件、用户、模型、知识库、项目成员、操作审计；报价版本（`boq_quote_versions`，含审批状态、痕迹与逐级审批记录 `approvals`）；新表由 `create_all` 建表，`boq_snapshots.package_id`、`boq_quote_versions` 七个审批列与 `approvals` 由 `db._migrate_columns` 追加并回填；调价策略存 `project.meta["price_policy"]`，审批链存 `project.meta["approval_chain"]`（≤5 级阶段快照 `{approvers:[{id,name}],mode:all|any,min_amount?}`，每级 1–5 人，可选金额门槛（总额达到才生效），负责人 PATCH 配置，兼容旧扁平格式） |
| 文件存储 | `BIDCRAFT_HOME/projects/<id>/`、`BIDCRAFT_HOME/packages/`、`BIDCRAFT_HOME/output/` | 招标原件、清单原件、配图（`images/`）、导出件、批量导出 zip；管理员规则包覆盖与 `hidden.json` |
| 文档转换 | python-docx + LibreOffice/UNO | DOCX（含配图嵌入）、PDF（刷新 TOC 页码）、XLSX |
| 模型 | 管理端配置 + OpenAI 兼容 HTTP 客户端 | 默认对话模型生成；默认图片模型按章节生成配图；向量模型可配置测试；不可用时确定性兜底 |
| 知识库 | 本地索引 + 向量混合检索 + 第三方检索 | 本地目录二元组召回；可选 embedding 向量余弦重排（`kb_chunks.embedding/embed_key/embed_dim`）；通用/Dify/RAGFlow API；片段注入提示词 |

## 3. 主流程

```
上传招标文件
   │  POST /projects/{ref}/sources
   ▼
解析（PDF/DOCX/TXT/XLSX；扫描 PDF 可选 OCR）→ 字段/评分/材料/参数/要求/风险
   │
   ▼
事实台账（C1）── 人工校订 PATCH /facts/{key}
   │  必填缺失 = 生成门禁（409 missing_required）
   ├── 清单对齐：上传工程量清单 → 表头识别 → 材料匹配
   │     差异（缺项/多项/数量·单位·规格）→ 逐行回写台账或加入台账
   │     → 逐行录入单价（合价 = 清单数量 × 单价）→ 报价汇总
   │     → 导出含报价的清单响应表（XLSX）
   ▼
大纲（规则包/评分导向/招标格式/自动）── 可人工增删改
   │
   ▼
逐章生成（后台 Job）── 事实注入 → 去重 → 数字复核 → 确定性兜底
   │  POST /generate → 202 job_id
   │  GET /jobs/{id}/events（SSE，Last-Event-ID 可恢复）→ 逐字预览
   │  GET /jobs/{id} / GET /projects/{ref}/jobs → 轮询回退与刷新后重新挂接
   ├── 章节配图：POST /images（提示词/风格/尺寸/章节上下文）
   │     失败资产 201 保留，可重生成；支持上传 PNG/JPEG
   │     项目图库跨章节复用；GET /images/plan 汇总各章占位匹配与章节/全文预算
   │     GET/PUT /image-policy 整篇策略（全文上限/默认风格尺寸/图注/版心宽度）
   │     POST /images/generate-missing → images Job 批量补齐缺失/失败图
   │     POST /images/apply-plan 按章节与全文预算替换已匹配占位
   │     图名精确匹配占位符或按光标插入 Markdown → DOCX 嵌入（跨章图注编号）
   ▼
核对（blocking/critical/major/minor）→ 问题清单
   │
   ▼
导出 DOCX / PDF / XLSX / Markdown
```

PDF 读取优先用 pypdf 提取文本层，失败时回退 pdfplumber；仍无文本的页面才进入可选 OCR：
`pdftoppm` 按页渲染 PNG，`tesseract` 识别后回填 `pages`，原页码与页数保持不变。OCR 只处理
空文本页，混合型 PDF 的文本页不会重复识别。缺少命令、语言包或单页识别失败时只记录
`ocr_unavailable` / `ocr_error` / `ocr_warnings`，不阻断解析；全部页面仍无文本时由
`parse_project` 给出安装提示。`bidcraft doctor` 展示 OCR 可用性与实际语言，`BIDCRAFT_OCR=0`
可完全关闭。

`bidcraft calibrate <dir>` 是解析链路的旁路诊断：复用 `parsing` 与 `ledger` 的纯函数遍历目录，
统计字段命中、必填命中率、材料行与 OCR 需求，**不写数据库、不创建项目、不调用模型**；
单文件失败只记 `error` 后继续，报告可写 JSON。`save_baseline`/`compare_baseline` 把目录级聚合
存为基线文件（schema=1，仅 aggregate），比对字段命中下降、必填命中率降幅（容差 2%）、
解析失败增多与材料行均值下降（相对降幅 >10%），CLI 以退出码 0/1/2 表达无回归/有回归/基线缺失或无效。
详见 `docs/calibration.md`。

后台生成任务用数据库 `jobs` 表保存状态，并用 `job_events` 追加事件流。生成线程按章提交事务，
再更新任务进度，避免 SQLite 单写者锁竞争。事件包括 `queued`/`status`/`chapter_start`/`delta`/
`chapter`/`done`/`failed`；模型流式输出按约 320 字或 250ms 合并成 `delta` 事件，避免逐 token
写库。浏览器优先用 `EventSource` 消费 `/jobs/{id}/events`，断线时携带 `Last-Event-ID` 自动续传；
SSE 不可用时回退到约 1.6 秒一次的任务轮询。刷新页面后通过 `GET /projects/{ref}/jobs` 找回
正在运行的任务并重新挂接。

配图由默认启用的 `image` 模型生成，服务端把图名、专业包、章节标题、章节正文摘要、用户
画面描述、风格与安全约束拼成提示词。供应商失败不会丢失记录：先落一条 `failed` 资产再返回
201，前端显示错误并可直接重生成；成功时按 PNG/JPEG magic bytes 写入真实扩展名，`options`
记录尺寸、风格、用户描述、质量和 `revised_prompt`。上传图片同样校验 10MB 上限与真实类型。
正文插入只使用图名完全匹配的 `ready` 资产；没有精确匹配时禁用占位符插入，避免用无关图片
顶替。项目图库把本项目全部图片按范围展示，可跨章节插入同一资产；配图计划按当前大纲顺序
扫描各章正文中的 `{{图片占位:图名}}`，统计已匹配/失败/缺失，并给出可直接跳转的章节清单。
计划每次请求实时计算，不缓存匹配结果。章节预算按正文字数与 `BIDCRAFT_IMAGE_WORDS_PER_IMAGE`
计算，并受 `BIDCRAFT_IMAGE_MAX_PER_CHAPTER` 限制；整篇策略存于 `project.meta.image_policy`
（全文上限、批量生成默认风格/尺寸、图注开关与前缀、版心宽度），计划同时返回全文已用/剩余/
超限统计。`POST /images/apply-plan` 先按章节预算、再按全文上限只替换 ready 且未超预算的占位
（超出全文上限计入 `skipped_over_total`），未匹配/失败/超预算项保持原样，成功后把章节标记为
`edited`。「生成并插入」把批量生成与按计划插入串成一次编排：批量 Job 完成后自动 apply。
按计划批量生成时，服务端把同一图名收敛为一个目标，
创建 `kind="images"` 的后台 Job，逐张调用默认图片模型并更新 `progress`/`payload`；单张失败
只计入结果并继续，配置错误则终止整个任务。浏览器轮询 `GET /jobs/{id}` 显示进度，刷新后通过
`GET /projects/{ref}/jobs?kind=images` 重新挂接。导出 DOCX 时通过安全 URL resolver 把
`/projects/<id>/images/<file>` 嵌入文档，并按整篇策略设置图宽与跨章累计的图注编号
（`图N　图名`，可关闭）；PDF 经 DOCX 继承同一排版。

## 4. API 分组

| 分组 | 端点 |
|---|---|
| 认证 | `POST /auth/login`、`POST /auth/logout`、`GET /auth/me`、`POST /auth/password` |
| 管理 | `/admin/models`、`/admin/kbs`、`/admin/users`、`/admin/rule-packages`（含校验/隐藏/恢复）、`/admin/quotas`（CRUD/搜索/匹配测试）、`/admin/plugins`（CRUD/测试/预设目录/`.bidplugin` 包导入导出）、`/admin/terms`（CRUD/搜索/启停）、`/admin/wechat`（读取/保存/连通性测试），仅超级管理员 |
| 元信息 | `GET /health`、`GET /packages`、`GET /docs` |
| 项目 | `GET/POST /projects`、`GET/PATCH/DELETE /projects/{ref}`（PATCH 改名称/标段）、`POST /projects/{ref}/duplicate`（复制为新项目：原件/台账/大纲/正文必选，清单报价与配图可选，版本与审批记录不复制） |
| 来源 | `GET /projects/{ref}/sources`、`POST /projects/{ref}/sources`、`POST /projects/{ref}/parse` |
| 台账 | `GET /projects/{ref}/ledger`、`PATCH /projects/{ref}/facts/{key}` |
| 清单 | `GET/POST/DELETE /projects/{ref}/boq`、`POST /projects/{ref}/boq/apply`、`POST /projects/{ref}/boq/price`、`POST /projects/{ref}/boq/export`（均接受 `package_id`） |
| 包件与报价 | `GET/POST /projects/{ref}/packages`、`PATCH/DELETE /projects/{ref}/packages/{package_id}`、`GET/PUT /projects/{ref}/price-policy`、`POST /projects/{ref}/boq/quota-fill`（dry-run 可选）、`POST /projects/{ref}/boq/adjust` |
| 报价版本与审批 | `GET/POST /projects/{ref}/boq/versions`、`GET /projects/{ref}/boq/versions/{id}`（含 prices）、`GET .../diff`（against=版本 id 或 current）、`POST .../restore`、`DELETE .../versions/{id}`、`POST .../submit`、`POST .../review`（approve/reject，按审批链逐级，金额门槛条件级自动跳过）、`POST .../unapprove`；`GET /approvals/pending` 跨项目待我审批（登录即可，平铺数组，submitted_at 降序上限 100） |
| 大纲 | `GET/POST/PUT /projects/{ref}/outline` |
| 章节 | `GET/PUT /projects/{ref}/chapters/{id}`、`POST .../generate` |
| 生成 | `POST /projects/{ref}/write`（同步兼容）、`POST /projects/{ref}/generate`（后台）、`GET /projects/{ref}/jobs`、`GET /jobs/{id}`、`GET /jobs/{id}/events`（SSE） |
| 配图 | `GET/PUT /projects/{ref}/image-policy`、`GET/POST /projects/{ref}/images`、`GET /projects/{ref}/images/plan`、`POST /projects/{ref}/images/apply-plan`、`POST /projects/{ref}/images/generate-missing`（后台 Job）、`POST /projects/{ref}/images/{id}/regenerate`、`POST /projects/{ref}/images/upload`、`GET/DELETE /projects/{ref}/images/{id}`；`GET /projects/{ref}/jobs?kind=images` 用于刷新后重新挂接 |
| 核对 | `POST /projects/{ref}/check`、`GET /projects/{ref}/issues` |
| 导出 | `POST /projects/{ref}/export`（docx/pdf/xlsx/md，返回二进制） |
| 批量 | `POST /projects/batch-check`、`POST /projects/batch-export`（后台 Job，202）、`GET /batch-jobs/{id}/download`（zip，仅任务所有者/超管） |
| MCP | `bidcraft mcp [--readonly]` stdio JSON-RPC：list_projects/get_project/get_ledger/run_check/export_document/search_knowledge，见 `docs/mcp.md`；`tools/list` = 本征 6 工具 + 启用插件工具（`{插件}__{工具}` 前缀，allowlist 过滤），`tools/call` 按前缀路由，只读模式拒绝插件调用，见 `docs/plugins.md` |
| 协作 | `GET/POST /projects/{ref}/members`、`PATCH/DELETE /projects/{ref}/members/{user_id}`、`GET /projects/{ref}/audit`、`GET /admin/audit`（仅超级管理员） |

除 `/health`、`/auth/login` 与静态资源外，API 均要求有效会话；未登录返回 `401`，
权限不足返回 `403`。错误统一为 `{"detail": ...}`；生成门禁返回 `409`，
`detail.missing_required` 为缺失必填字段 key 列表，客户端据此跳转到事实台账并高亮。

## 5. 并发与一致性

- SQLite 开启 WAL 与 30s busy timeout，读请求不被生成事务阻塞。
- 同一项目同时只允许一个后台生成任务（API 返回 409）。
- 章节编辑直接写 `sections`，字数按中文字符 + 英文词元重新计算；`sections.revision`
  乐观锁：PUT 带 `base_revision` 且与服务端不一致时返回 409（附当前 revision/正文），
  前端可载入最新或强制覆盖；不带 `base_revision` 保持旧行为。
- 项目访问按 owner/成员(editor/viewer)/super_admin 判定；写操作要求 editor 及以上，
  成员管理与删除项目仅 owner/super_admin。操作审计写 `audit_logs`（项目级与全局），
  删除项目级联清理成员与审计行。
- 生成期间的事实台账以任务启动时快照为准；修改台账后建议重新生成受影响章节。
- 知识库检索按章节标题/意图查询，失败时静默跳过，不阻断生成。
- 向量检索为可选增强：`resolve_embedding_endpoint` 取启用的 embedding 模型（offline 时关闭）；
  重建索引后按批写入 chunk 向量，`embed_key` 记录模型指纹，换模型后旧向量自动失效；
  检索时混合二元组与余弦相似度打分，查询嵌入失败静默回退关键词召回。
- 清单对齐不缓存匹配结果：每次读取都用当前材料表重算，避免台账修改后显示陈旧差异。
  重新解析招标文件会重建材料表（id 随之变化），已回写的数量/规格以新台账为准，需重新对齐。
- 规则包以文件为源：用户目录覆盖内置，保存使用临时文件 + `os.replace` 原子替换；
  `hidden.json` 只影响新建项目列表，`load_package` 仍加载隐藏包，避免已有项目失效。
  规则包名称限制为小写字母/数字/连字符，保存前完成完整继承链与表达式校验。
- 单价保存在 `boq_snapshots.prices`（按 `sheet!row` 键），合价每次按当前清单数量重算；
  重新上传清单会替换快照并清空报价，重新解析招标文件不影响清单与报价。报价只统计
  未忽略的清单行，已忽略行即使录入单价也不计入合计。
- 多包件以 `boq_snapshots.package_id` 隔离，`NULL` 保留旧单快照语义；首个包件创建时把
  现有 `NULL` 快照收养为该包。`resolve_package` 统一判定：项目未启用包件时只允许 `NULL`
  快照（显式传 `package_id` 返回 400），启用后必须指定且属于本项目（缺失 400、未知 404）。
  对齐/报价/忽略/导出均按包件隔离，事实台账材料仍项目级共享；删除包件同时删除该包快照与
  清单原件。分包导出文件名 `{项目名}-{包件名}-清单响应表.xlsx`（未启用包件时保持旧名）。
- 定额套用只读匹配 `quota_items`（name 为主、spec 加分，阈值 0.75），`dry_run=true` 只返回
  proposals 不写库，确认后才经 `set_unit_price` 写入；程序不生成价格。调价策略存
  `project.meta["price_policy"]`，`factor` 夹取 0.1–10、`round_digits` 夹取 0–4；
  `boq/adjust` 按包件把已录入单价乘系数并四舍五入写回，无单价返回 409，审计记
  `boq.adjust`。`summary.adjusted_total` 由策略实时计算，不另存。
- 插件调用不常驻：每次 `tools/list`/`tools/call` 重新握手，stdio 子进程按
  `BIDCRAFT_PLUGIN_TIMEOUT`（默认 20 秒，配置层夹取 5–120）设截止时间，超时先
  `terminate`、5 秒内未退出再 `kill`，调用结束同样回收。单个插件握手失败/超时/命令不存在
  只往 stderr 打一行警告并跳过，其余工具照常返回；`tool_allowlist` 在聚合层过滤，
  只读模式拒绝所有插件工具。
- 图片资产独立于章节正文保存；生成参数与失败原因在 `image_assets.options` / `last_error`。
  重新生成创建新资产而不覆盖旧图，删除资产只删对应文件；正文中的 Markdown 引用不会级联清理。
  配图计划按当前大纲/正文/资产实时计算，不落库；章节预算同样实时计算，不持久化。
  项目图库复用只写 Markdown 引用，不复制文件；按计划插入会直接改正文并保存，因此前端要求
  当前章节无未保存修改。同一项目同时只允许一个 `kind="images"` 批量任务，超过 2 小时无进度
  才判定为中断；批量任务逐张使用短事务，避免供应商请求期间长期占用 SQLite 写锁。
  整篇配图策略存 `project.meta.image_policy`，PUT 只接受已知键并夹取范围，保留其他 meta；
  全文已用张数按当前正文 Markdown 图片行实时统计，不另落库。
- 批量核对/导出为 `kind="batch_check"/"batch_export"` 的后台 Job（`project_id=None`，
  所有权记 `payload.owner_id`）：逐项目短事务执行，单项失败只记结果不中断；导出 zip 写
  `BIDCRAFT_HOME/output/batch-<job8>.zip`，下载端点校验任务所有权。`bid_section` 存
  `project.meta`，无 schema 迁移；PATCH 名称/标段走写门禁并记 `project.update` 审计。
- SSE 事件按任务持久化在 `job_events`，`GET /jobs/{id}/events` 先回放
  `Last-Event-ID` 之后的事件再尾随；终态事件到达后服务端主动结束响应。
  事件写入使用独立短事务，生成线程在每章结束前提交正文事务，避免与 SSE 读请求争锁。
- 任务事件没有外键级联，删除项目时按 `job_id` 显式清理；任务本身仍保留 30 分钟
  无进度超时判定。
- 建议 uvicorn 单 worker 运行（`bidcraft serve` 默认即单 worker）。多 worker 时任务
  状态仍可查询，但需额外的任务锁/队列才能严格避免重复生成；SSE 按数据库事件回放，
  不依赖进程内广播，因此跨 worker 重新连接仍可恢复。

## 6. 安全与合规

- 无遥测、无统计上报、无远程错误收集；模型调用（含图片端点返回的临时图片 URL 下载）
  是唯一可选外联。图片下载仅允许 http/https，并校验 12MB 上限与 PNG/JPEG magic bytes。
- 上传文件名只取 basename，存储路径由服务端生成；图片上传另校验 10MB 上限与真实类型。
- 密码使用 scrypt 加盐哈希；会话为随机 256 位令牌，数据库只存 SHA-256 摘要；
  Cookie 为 HttpOnly + SameSite=Lax，HTTPS 下应设 `BIDCRAFT_SECURE_COOKIE=1`。
- 登录失败按用户名限速（8 次/5 分钟）；修改密码或停用用户会吊销其会话。
- 模型/知识库 API Key 使用 `BIDCRAFT_HOME/secret.key`（0600）做 Fernet 加密，
  管理 API 只返回 `has_api_key`。请把该文件纳入备份并限制目录权限。
- 公网部署仍建议在反向代理层增加 TLS、来源限制、请求体大小与速率限制。
- 事实台账、原件、导出件均在本地文件系统；备份见「局域网模式 › 备份与恢复」，
  删除由运维控制。
- 上游 AGPL 项目仅作行为/规格参考，本仓库不包含其源代码。

## 7. 扩展路径

1. 任务队列：把 `jobs` 线程替换为 Celery/RQ/Arq，支持取消、重试、多 worker；
   现有 `job_events` 可继续作为事件出口。
2. 存储：SQLite → PostgreSQL；原件 → 对象存储；导出件生命周期管理。
3. 知识库：企业资质/业绩按白名单字段回填事实台账（仍需人工确认）。
