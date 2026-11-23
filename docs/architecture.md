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
│                       ├─ SQLite / PostgreSQL (BIDCRAFT_DB_URL)     │
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

项目数据集中在 `BIDCRAFT_HOME`：`bidcraft.db`（SQLite，WAL 模式）或 PostgreSQL 库（由 `BIDCRAFT_DB_URL` 指定）、
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
| 前端分层 | `web/src/api/`（14 域）+ `lib/http.ts` + `hooks/watchJob.ts` + `lib/permissions.ts` | API 请求按业务域拆分、传输与错误口径统一；任务进度统一走 SSE 事件流 + 断线轮询兜底（终态只结算一次）；权限能力（负责人/编辑/只读/旧后端兼容）集中判断；章节 409 乐观锁冲突解析与弹窗提示统一在 `lib/http.ts`；无 Redux、页面结构不变 |
| API 分层 | `bidcraft/api/app.py`（门面/组装）+ `schemas.py`（71 请求模型）+ `shared.py`（helper/常量/重导出）+ `routes/`（12 域） | 依赖单向：routes → schemas/shared；app 仅兼容 `from bidcraft.api import ...` 与 monkeypatch，runner/helper 与 workers/services 对象同一；`jobqueue` 经 sys.modules 运行期查找保持不变 |

| HTTP 层 | FastAPI + uvicorn | REST、静态资源托管、SPA 深链接回退、后台任务；另有 `bidcraft mcp` stdio JSON-RPC 服务供外部 Agent 接入 |
| 统一授权入口 | `bidcraft/authorization.py` | 项目/任务/节点/批量项目的可见性与动作判定收口为单一模块，HTTP/CLI/MCP 共用；HTTP 层薄封装委托并映射领域异常 404/403，不 import FastAPI |
| 身份上下文 | bidcraft/context.py | 请求身份存 ContextVar；set 返回 token，中间件 try/finally reset；后台任务显式传参、不依赖上下文 |
| API 架构 | `bidcraft/api/app.py` + `bidcraft/api/routes/`（12 域） | 应用装配与共享 helper 在 `app.py`；194 个路由按 auth/account/billing/admin/projects/my/jobs/batch_jobs/legal/packages/health/approvals 拆到 `routes/`；耗时任务在 `bidcraft/workers/`，跨任务共享逻辑在 `bidcraft/services/job_support.py`；`bidcraft.api` 保持既有导入路径与 monkeypatch 语义 |
| 数据库迁移 | `bidcraft/migrations/` | Alembic 记账后续 schema 变更；`create_all`+`_migrate_columns` 作历史基线（后者仅 SQLite）；`0002_quote_basis` 为首个真实 revision；SQLite 用 fcntl 文件锁、PostgreSQL 用 `pg_advisory_lock` 串行化并发迁移 |
| 双数据库后端 | `bidcraft/db.py` / `config.py` | 默认 SQLite；`BIDCRAFT_DB_URL` 切 PostgreSQL（psycopg 3）。删除路径统一走 `services/purge.py` 以应对 PG 强制外键；备份 SQLite 走在线备份 API、PG 走 `pg_dump` |
| 金额服务 | `bidcraft/services/money.py` | Decimal + ROUND_HALF_UP 统一报价/税额/汇总/调价计算，边界转 float；报价版本 `basis` 列冻结不可变计算依据 |
| 报价 Service | `bidcraft/services/quote_service.py` | 报价改价编排从 HTTP 路由抽离，HTTP/CLI 共用，显式 actor 承载审计身份，可脱离 FastAPI 测试 |
| 关联清理 | `bidcraft/services/purge.py` | 项目删除集中清理关联行；`find_orphans`/`clean_orphans` + `bidcraft db-orphans` 检测清理孤儿引用；审计 user_id 悬挂留存 |
| 领域层 | `pipeline` / `ledger` / `parsing` / `rules` / `generation` / `boq` / `images` / `plugins` / `calibrate` / `terms` / `qualifications` / `personnel` / `material_policy` / `textqa` / `dedup` / `profile` / `mineru` / `material_policy` / `clock` / `plugin_pack` / `duplicate` / `accounts` / `jobqueue` / `export_gate` / `audit` / `provenance` | 解析（含可选 OCR）、事实台账、规则包加载/覆盖、规则核对、清单对齐与报价（包件、定额、调价）、正文生成、配图与整篇排版策略、MCP 客户端插件（stdio/http 会话与工具调用，stdio 子进程受 RLIMIT_AS/RLIMIT_CPU/umask 0o077 与一次性临时工作目录约束）、只读回归校准、企业术语库与禁用表述核对、资质台账与投标前自查（证据存证与到期预警）、人员台账与素材可信度分级（material_policy，检索注入降级）、文本质量检查（错别字/跨章一致性/重复）、跨文件查重与自定义词库、开票申请与开具、注册同意留痕（accept_terms 缺省/False 一律 400 拒绝，必须显式提交）与备份保留、导出格式模板（DOCX 样式预设）、上线合规（条款/导出/对账/注销/备份校验）、评分回填与失分复盘、项目级全局事实注入与一致性锚点、可选 MinerU 解析适配（失败回退内置链）、素材可信度分级（缺字段/过期降级为题录）、`.bidplugin` 插件包导出/导入校验与受控安装、项目复制（深拷贝与外键重映射）、子账号 CRUD 与共享计费、VIP 会员与消费折扣、DB 驱动任务队列（jobs 行即队列，worker 原子认领 + 租约心跳 + fencing 令牌（旧 worker 写入被拦、启动恢复只收过期租约）+ 重排队恢复 + 背压，reaper 独立常驻线程（不被忙队列饿死）、API/Worker 可分离（`BIDCRAFT_EMBEDDED_WORKERS=0` + `bidcraft worker`）、章节级幂等账本（done_steps 跨重启续跑）+ 心跳丢失 abort 中止 + SIGTERM 优雅退出（停止认领、检查点收尾），VIP 优先）、个人文档上传与私人知识库（自动建索引、检索隔离）、HTTP 客户端库与命令行客户端（cookie 持久化、文档/原件上传、下载）、桌面网关（本机反代 + 本地文件桥，仅绑定 127.0.0.1 且校验随机令牌） |
| 认证 | scrypt + Cookie 会话 + RBAC | 超级管理员/投标经理/只读成员；项目成员 editor/viewer；用户管理与会话吊销 |
| 持久化 | SQLAlchemy + SQLite（WAL） | 项目、来源、事实、材料、参数、评分、要求、风险、清单快照（含用户单价与 `package_id`）、包件（`bid_packages`）、企业定额（`quota_items`）、插件配置（`plugins`，含资源限制 `mem_limit_mb`/`cpu_seconds`）、术语库（`term_items`）、资质台账（`qualifications`，含评分项 `scoring_items.actual_score` 回填）、人员台账（`personnel`）、素材元数据（存 `knowledge_bases.options.docs`）、资质证据文件存 `BIDCRAFT_HOME/qualifications/{user_id}/`、微信开放平台配置（`wechat_config`，密钥加密存 `app_secret_enc`）、图片资产（含生成参数）、大纲、章节（含 revision）、问题、任务与事件（含优先级 `priority`）、用户（含 VIP 到期 `vip_expires_at`、子账号归属 `parent_id`、注销冷静期 `deletion_requested_at`/`deletion_due_at`）、充值流水（`credit_transactions`，kind 含 `vip`）、开票申请（`invoice_requests`，发票文件存 `BIDCRAFT_HOME/invoices/{user_id}/`）、模型、知识库、项目成员、操作审计；报价版本（`boq_quote_versions`，含审批状态、痕迹与逐级审批记录 `approvals`）；新表由 `create_all` 建表，`boq_snapshots.package_id`、`boq_quote_versions` 七个审批列与 `approvals`、`users.vip_expires_at`/`parent_id`、`jobs.priority` 由 `db._migrate_columns` 追加并回填；全局事实存 `project.meta["writing_profile"]`，调价策略存 `project.meta["price_policy"]`，审批链存 `project.meta["approval_chain"]`（≤5 级阶段快照 `{approvers:[{id,name}],mode:all|any,min_amount?,packages?,currencies?}`，每级 1–5 人，可选金额门槛（总额达到才生效）与包件/币种白名单（版本包件、项目币种命中才生效），负责人 PATCH 配置，兼容旧扁平格式） |
| 文件存储 | `BIDCRAFT_HOME/projects/<id>/`、`BIDCRAFT_HOME/packages/`、`BIDCRAFT_HOME/output/` | 招标原件、清单原件、配图（`images/`）、导出件、批量导出 zip；管理员规则包覆盖与 `hidden.json` |
| 文档转换 | python-docx + LibreOffice/UNO | DOCX（含配图嵌入）、PDF（刷新 TOC 页码）、XLSX |
| 模型 | 管理端配置 + OpenAI 兼容 HTTP 客户端 | 默认对话模型生成；默认图片模型按章节生成配图；向量模型可配置测试；不可用时确定性兜底 |
| 知识库 | 本地索引 + 向量混合检索 + 第三方检索 | 本地目录二元组召回；可选 embedding 向量余弦重排（`kb_chunks.embedding/embed_key/embed_dim`）；通用/Dify/RAGFlow API；片段注入提示词；个人知识库（`user_docs` + `options.personal`）随上传自动索引，`knowledge.search(include_personal=False)` 默认排除、`_knowledge_provider(user_id)` 仅注入本人个人 KB 防跨用户泄漏 |

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
核对（blocking/critical/major/minor）→ 问题清单（锚定大纲章节 + 人工处置状态）
   │
   ▼
实质性条款（★/▲）逐条对照正文 → 未响应即阻断
   │  约束比较：数值方向（≥/≤/等级序/质保时长）、否定语义、复合条款逐条拆分；
   │  判定写入核对问题 evidence（satisfied/violated/absent）供人工举证
   │
   ▼
风险总览（四档聚合）→ 存在未处置阻断项时拦截导出（owner/admin 可强制，写审计）
   │
   ▼
导出 DOCX / PDF / XLSX / Markdown（xlsx 台账不受门禁限制）
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
| 管理 | `/admin/models`、`/admin/kbs`、`/admin/users`、`/admin/rule-packages`（含校验/隐藏/恢复）、`/admin/quotas`（CRUD/搜索/匹配测试）、`/admin/plugins`（CRUD/测试/预设目录/`.bidplugin` 包导入导出）、`/admin/terms`（CRUD/搜索/启停）、`/admin/textqa`（错别字词库）、自助 `/my/qualifications` 与 `/my/preflight`、`/admin/kbs/{id}/docs`（文档元数据与可信度判定）、`/admin/wechat`（读取/保存/连通性测试）、`/admin/users/{id}/vip`（VIP 授予/续期/清除，days=0 清除），仅超级管理员；自助计费路由 `/billing/vip/orders`（VIP 套餐购买，续期不加点）、`/account/sub-accounts`（子账号 CRUD，登录主账号自助）；个人文档路由 `/my/docs`（上传→个人 KB→索引 job、列表/下载/删除，登录用户自助） |
| 元信息 | `GET /health`（**始终公开可达**，默认只返回探活/前端必需字段）、`GET /admin/health`（超管完整运行状态）、`GET /packages`、`GET /docs`。`BIDCRAFT_HEALTH_PUBLIC=minimal/public`（默认）时 `/health` 不返回版本、绝对路径、模型名与库内计数；设为其它值（内网/私有模式）才附带这些敏感字段，完整状态仍应走 `/admin/health` |
| 项目 | `GET/POST /projects`、`GET/PATCH/DELETE /projects/{ref}`（PATCH 改名称/标段/审批链/模板标记）、`POST /projects/{ref}/duplicate`（复制为新项目：原件/台账/大纲/正文必选，清单报价与配图可选，版本与审批记录不复制）、`GET /projects/templates`（模板列表，登录即可）、`POST /projects/templates/{id}/instantiate`（从模板创建，无需成员身份） |
| 来源 | `GET /projects/{ref}/sources`、`POST /projects/{ref}/sources`、`POST /projects/{ref}/parse` |
| 台账 | `GET /projects/{ref}/ledger`、`PATCH /projects/{ref}/facts/{key}` |
| 清单 | `GET/POST/DELETE /projects/{ref}/boq`、`POST /projects/{ref}/boq/apply`、`POST /projects/{ref}/boq/price`、`POST /projects/{ref}/boq/export`（均接受 `package_id`） |
| 包件与报价 | `GET/POST /projects/{ref}/packages`、`PATCH/DELETE /projects/{ref}/packages/{package_id}`、`GET/PUT /projects/{ref}/price-policy`、`POST /projects/{ref}/boq/quota-fill`（dry-run 可选）、`POST /projects/{ref}/boq/adjust` |
| 报价版本与审批 | `GET/POST /projects/{ref}/boq/versions`、`GET /projects/{ref}/boq/versions/{id}`（含 prices）、`GET .../diff`（against=版本 id 或 current）、`POST .../restore`、`DELETE .../versions/{id}`、`POST .../submit`、`POST .../review`（approve/reject，按审批链逐级，金额门槛与包件/币种条件级自动跳过）、`POST .../unapprove`；`GET /approvals/pending` 跨项目待我审批（登录即可，平铺数组，submitted_at 降序上限 100） |
| 大纲 | `GET/POST/PUT /projects/{ref}/outline` |
| 章节 | `GET/PUT /projects/{ref}/chapters/{id}`、`POST .../generate` |
| 生成 | `POST /projects/{ref}/write`（同步兼容）、`POST /projects/{ref}/generate`（后台）、`GET /projects/{ref}/jobs`、`GET /jobs/{id}`、`GET /jobs/{id}/events`（SSE） |
| 配图 | `GET/PUT /projects/{ref}/image-policy`、`GET/POST /projects/{ref}/images`、`GET /projects/{ref}/images/plan`、`POST /projects/{ref}/images/apply-plan`、`POST /projects/{ref}/images/generate-missing`（后台 Job）、`POST /projects/{ref}/images/{id}/regenerate`、`POST /projects/{ref}/images/upload`、`GET/DELETE /projects/{ref}/images/{id}`；`GET /projects/{ref}/jobs?kind=images` 用于刷新后重新挂接 |
| 核对 | `POST /projects/{ref}/check`、`GET /projects/{ref}/issues`、`GET /projects/{ref}/risk-overview`（四档聚合 + 门禁状态）、`GET /projects/{ref}/citations`、`GET /projects/{ref}/chapters/{node_id}/citations`（正文→台账引用与需复核）、`PATCH /projects/{ref}/issues/{id}`、`POST /projects/{ref}/issues/bulk-status`、`GET /projects/{ref}/requirements`（实质性条款 + 响应状态 + 类型结构化判定：资质/业绩/参数/承诺锚点降低改写误判）、`PATCH /projects/{ref}/requirements/{id}` |
| 五层判定引擎 | `bidcraft/verdict.py`：条款提取→原子拆分→结构化约束→多维验证→四态判定；语义层 `SemanticMatcher` 可插拔（默认规则实现；`BIDCRAFT_VERDICT_LLM=1` 可启用可选 LLM 匹配器 `verdict_llm.py`，按规范化 JSON 缓存、单次核对最多 200 次调用，无模型/调用失败/预算耗尽自动回退规则匹配，数值/等级/时间/否定仍由确定性规则负责；LLM 不能单独判定 SATISFIED），数值/等级/时间/否定由确定性规则负责；四态结论与逐层证据写入 `GET /requirements` 与 issue.evidence。人工复核按重要性分级（★ blocking / ▲ critical / 普通 info），★ 未确认不得交付、强制导出须负责人独立授权并在 `export.force` 审计记 `overridden_substantives`；`constraint_check` 单位缺失不认定量纲一致、禁止跨量纲比较（Mbps≠MB/s）、单位不明确转人工复核，确认响应时快照原始判定入审计 |
| 判定回归与质量度量 | `bidcraft/calibration.py` + `tests/fixtures/verdict_regression.json`（26 条样本，覆盖资质/业绩/数量/金额/带宽/存储/质保/响应/交付/否定/复合/单位换算/时间窗/项目类型），逐样本支持 deadline 由统一执行器驱动；输出混淆矩阵 TP/FP/TN/FN、假接受率 FAR=FP/(FP+TN)、错误拒绝率 FRR、四态准确率、人工复核率与项目类型/身份关联错误数，★ 条款误放行监测；纳入 CI，已知严重误放行令构建失败。判定引擎支持业绩证据归属（PerformanceEvidence：项目名/类型/签订·开工·竣工·验收日期/精度/来源）、按招标截止日期（事实台账+meta）逐项校验、日历月与闰年边界、year/month/day 日期精度不臆造；正文修改自动使本章已确认条款响应失效 |
| 质量评估 | `bidcraft/evaluation/`（coverage/risk/scorer/report）：聚合规则核对/★判定/来源追溯/评分覆盖/章节确认/导出门禁为不可变质量报告，六维评分（权重配置化）+ 硬性风险一票否决；`POST/GET /projects/{ref}/evaluation`、`GET /evaluation/reports`、`PATCH /evaluation/findings/{id}`。新增 `0007_yibiao_batch4`（quality_reports/quality_findings 2 表） |
| 章节复核 | `POST /projects/{ref}/chapters/{node_id}/review`（草稿→待复核→已确认→已锁定，确认/锁定/撤回/解锁写审计；已确认章节不可编辑、导出只读已确认） |
| 导出 | `POST /projects/{ref}/export`（docx/pdf/xlsx/md，返回二进制；未处置阻断项使 docx/pdf/md 返回 409，`force`+`force_reason` 仅 owner/admin 可用并写 `export.force` 审计） |
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
  成员管理与删除项目仅 owner/super_admin。操作审计写 `audit_logs`（项目级与全局）；
  删除项目只清理成员与业务子表，**审计行不删除**——只把 `audit_logs.project_id` 置空，
  行仍可从 `/admin/audit` 全局检索，也不会被孤儿清理误删。
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
- 登录失败按用户名限速（8 次/5 分钟）；在此之外，登录/注册/改密/重置/兑换码/
  模型·知识库·插件测试还有**进程内 IP + 全局滑动窗口限流**（默认 300 秒窗口，
  阈值见 `BIDCRAFT_RATE_*`），多 worker 需在反代层再限一次。反代后可开启
  `BIDCRAFT_TRUST_PROXY=1`，用 `X-Forwarded-For` 记录真实来源 IP（直连时保持关闭）。
- 带 Cookie 的状态变更（`POST/PUT/PATCH/DELETE`）有 CSRF 纵深防御：显式 `Origin`
  必须同源，`Sec-Fetch-Site` 仅接受 `same-origin`/`none`；不带这两个头的普通
  脚本/curl 不受影响。响应统一带 `X-Request-ID`（回显客户端值或生成 UUID）与
  `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、CSP、
  `Permissions-Policy`、`Cross-Origin-Opener-Policy`、`Cross-Origin-Resource-Policy`。
- 基础日志由 `BIDCRAFT_LOG_LEVEL` / `BIDCRAFT_LOG_FORMAT`（text/json）控制，
  每条日志带 `request_id`，可用响应头里的 `X-Request-ID` 串起一次请求的全链路。
- 身份相关操作（登录成功/失败/锁死、注册、退出、改密，以及管理端用户的
  创建/更新/改角色/改口令/停用/删除）均写 `audit_logs`，detail 中**只记字段名、
  角色变化与来源 IP，绝不写口令或会话令牌**。审计写入失败只记 warning，不阻断主流程。
- 模型/知识库 API Key 使用 `BIDCRAFT_HOME/secret.key`（0600）做 Fernet 加密，
  管理 API 只返回 `has_api_key`。请把该文件纳入备份并限制目录权限。
  **PostgreSQL 多实例无共享卷时必须共享同一个 `secret.key`，或所有实例统一设置
  `BIDCRAFT_SECRET_KEY`**（32 字节 Fernet 密钥的 base64），否则跨实例无法解密；
  详见 `docs/postgresql.md`。
- 公网部署仍建议在反向代理层增加 TLS、来源限制、请求体大小与速率限制。
- 事实台账、原件、导出件均在本地文件系统；备份见「局域网模式 › 备份与恢复」，
  删除由运维控制。
- 上游 AGPL 项目仅作行为/规格参考，本仓库不包含其源代码。

## 7. 扩展路径

1. 任务队列：把 `jobs` 线程替换为 Celery/RQ/Arq，支持取消、重试、多 worker；
   现有 `job_events` 可继续作为事件出口。
2. 存储：SQLite → PostgreSQL；原件 → 对象存储；导出件生命周期管理。
3. 知识库：企业资质/业绩按白名单字段回填事实台账（仍需人工确认）。
## 8. 时间与时区

- **存储**：数据库时间戳一律 naive UTC（`models.utcnow()`），跨时区比较不受进程时区影响。
- **日历日**：有效期、到期预警、按日归档等「哪一天」的判断统一走 `bidcraft/clock.py`
  （`today()` / `local_date()`），按北京时间（UTC+8），不用 UTC 日期，也不依赖容器 `TZ`。
- **对外序列化**：时间戳必须带偏移（`clock.iso_utc()` 或各模块 `_iso()`），否则浏览器
  `new Date()` 按本地时区解析，显示早 8 小时；`tests/test_clock.py` 有源级护栏防回退。
- **展示文本/文件名/CSV**：用 `clock.format_local()` 输出北京时间。
