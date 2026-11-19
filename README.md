# BidCraft · 弱电投标文件编制系统

按「C 方案：只借规格重制」实现的自研薄内核 + B/S 客户端。不复制任何上游代码，
只复用 `fb-labs/ZhiBiao` 与 `FB208/OpenBidKit_Yibiao` 的领域规格（字段口径、
核对规则、章节骨架）并做成配置包。

## 路线边界

- **不是** fork：仓库内没有上游源码，只有重新实现的逻辑与 YAML 规格。
- **零遥测**：除显式配置的 OpenAI 兼容模型端点（以及图片端点返回的临时下载 URL）外，
  程序不发起任何外部网络请求。
- **规则外置**：换业主、换专业只改 `bidcraft/resources/packages/*.yaml`，不改代码。
- **事实优先**：没有事实台账就拒绝生成；缺失值写 `{{待填:字段}}`，不编造证书/业绩/参数。

## 快速开始

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -e '.[dev]'

# 1. 检查环境（零遥测、规则包校验）
bidcraft doctor

# 2. 一条命令跑通：解析 → 台账 → 大纲 → 正文 → 核对 → DOCX
bidcraft run 招标文件.pdf --name "某园区智能化工程" --package weakcurrent-cabling

# 3. 分步执行
bidcraft ingest 招标文件.pdf -n "某园区智能化工程" -p weakcurrent-cctv
bidcraft show <项目ID>
bidcraft fact <项目ID> project.name -v "某园区智能化工程"
bidcraft outline <项目ID> --mode scoring
bidcraft write <项目ID>
bidcraft check <项目ID>
bidcraft boq <项目ID> 招标工程量清单.xlsx   # 清单逐行对齐台账材料
bidcraft boq-price <项目ID> 2 12.50         # 为第 2 行清单录入单价（--clear 清除）
bidcraft boq-export <项目ID>                # 导出含报价的清单响应表
bidcraft export <项目ID> -f docx
bidcraft export <项目ID> -f pdf     # 本机 LibreOffice 转换，自动刷新目录页码
bidcraft schema                     # 输出 C1 事实台账 JSON Schema
bidcraft export <项目ID> -f xlsx     # 导出事实台账/材料/参数/评分/风险工作簿
bidcraft backup                     # 在线一致性备份 db + secret.key 为 zip

# B/S 客户端（推荐；需要 Node.js 20+）
uv pip install -e '.[api]'
cd web && npm install && npm run build && cd ..
bidcraft serve --open               # http://127.0.0.1:8000
```

浏览器客户端覆盖：项目列表 → 招标文件上传/解析 → 事实台账校订 → 大纲编排 →
逐章生成/编辑 → 合规核对 → 清单对齐 → DOCX/PDF/XLSX 导出。模型不可用时所有按钮仍可走
确定性草稿，界面会明确提示。

依赖已锁定在根目录 `uv.lock`（49 个包）。复现同一环境用：

```bash
uv sync --frozen --extra api --extra dev   # 或在已激活的 venv 内 uv sync --frozen
```

## B/S 部署

**单机模式**（默认，数据不出本机）：

```bash
bidcraft serve --open                 # 127.0.0.1:8000，浏览器自动打开
```

**局域网模式**（一台服务器、多人浏览器访问）：

```bash
BIDCRAFT_HOME=/srv/bidcraft-data bidcraft serve --host 0.0.0.0 --port 8000
```

客户端只需访问 `http://<服务器IP>:8000`，无需安装任何软件。服务端内置登录与
RBAC（超级管理员 / 投标经理 / 只读成员），首次启动会创建超级管理员：

- 设置 `BIDCRAFT_ADMIN_PASSWORD` 时使用该密码；未设置则生成随机密码写入
  `BIDCRAFT_HOME/initial-admin.txt`（0600，仅首次）。
- 超级管理员登录后进入「管理控制台」，配置对话/图片/向量模型、知识库与用户。
- 公网或跨网段访问请启用 HTTPS 并设置 `BIDCRAFT_SECURE_COOKIE=1`，建议置于 TLS
  反向代理之后。项目数据集中存储在 `BIDCRAFT_HOME`（SQLite + 原件 + 导出件），
  `secret.key` 用于解密模型/知识库密钥，需一并备份。详细组件、数据流、并发与安全边
  界见 `docs/architecture.md`。

### 备份与恢复

数据库运行在 SQLite WAL 模式，**直接 `cp` 数据目录有撕裂风险**（`-wal`/`-shm`
与主库不同步，拷贝中若有写入会得到不一致的快照）。使用内置命令即可在线生成一致性
快照（SQLite 在线备份 API，无需停服）：

```bash
bidcraft backup                       # 默认写入 BIDCRAFT_HOME/backups/bidcraft-<YYYYmmdd-HHMMSS>.zip
bidcraft backup -o /backup/           # 指定目录，自动按时间命名
bidcraft backup -o /backup/manual.zip # 指定完整文件路径
bidcraft backup --list                # 查看已有备份
```

备份包内含 `bidcraft.db`（一致性快照）、`secret.key`（存在时）与 `manifest.json`
（版本、创建时间、库大小、是否含密钥、`BIDCRAFT_HOME`）。`secret.key` 必须一并保存，
否则已加密的模型/知识库 API Key 无法解密，只能重新录入。

**恢复步骤**：

1. 停止服务：`systemctl stop bidcraft`（或 `docker stop bidcraft`）。
2. 备份现有数据目录后解压：`unzip /backup/bidcraft-<时间>.zip -d /tmp/restore`。
3. 把 `bidcraft.db` 与 `secret.key` 放回 `BIDCRAFT_HOME`（默认 `./.bidcraft/`），
   保持路径不变并确保文件属主为运行用户（容器内 uid 10001）。
4. 启动服务（`bidcraft serve` 或容器），确认项目与模型配置可正常读取。

没有安装 CLI 或需要保留原件/导出件时，仍可手工冷备（停服后整目录打包）或用
`sqlite3 $BIDCRAFT_HOME/bidcraft.db ".backup '/backup/bidcraft.db'"` 取库快照后
`rsync` 同步 `projects/ packages/ output/ secret.key`；不要直接 `cp` 运行中的目录。

Docker（含 LibreOffice、中文字体与 OCR，构建时自动编译 Web 客户端）：

```bash
docker build -t bidcraft .
docker run -d -p 8000:8000 -v bidcraft-data:/data --name bidcraft bidcraft
```

镜像内以非 root 用户 `bidcraft`（uid 10001）运行，`/data` 为唯一需要写权限的目录；
用宿主目录挂载时请先 `chown -R 10001:10001 <目录>`。`HEALTHCHECK` 轮询 `/health`，
`docker ps` 可直接看到健康状态。

不配置模型也能出稿：`write` 会回落到确定性草稿（结构与台账表格完整，文字质量有限）。
模型优先使用管理控制台里「设为默认」的对话模型；也保留环境变量作为兜底：

```bash
export BIDCRAFT_MODEL_BASE_URL=http://127.0.0.1:11434/v1   # Ollama / vLLM / 自建网关
export BIDCRAFT_MODEL_NAME=qwen3:32b
export BIDCRAFT_MODEL_API_KEY=...          # 可空
export BIDCRAFT_OFFLINE=1                  # 强制离线，任何情况下不连模型
```

### 扫描件 OCR（可选）

纯图片 PDF 会自动尝试 `pdftoppm` 渲染 + `tesseract` 识别；缺少命令或中文语言包时
不会阻断解析，而是保留 `needs_ocr` 提示，可继续上传可复制文本的 PDF。OCR 不增加
Python 依赖，只需系统命令：

```bash
sudo apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-chi-sim
bidcraft doctor                            # 查看「扫描件 OCR」是否可用
export BIDCRAFT_OCR=0                      # 如需完全关闭
export BIDCRAFT_OCR_LANGS=chi_sim+eng      # 默认语言
export BIDCRAFT_OCR_MAX_PAGES=100          # 单份文件最多识别页数，0 为不限制
```

也支持 `BIDCRAFT_OCR_DPI`（默认 300）、`BIDCRAFT_OCR_TIMEOUT`（每页秒数，默认 120）、
`BIDCRAFT_TESSERACT` / `BIDCRAFT_PDFTOPPM` 自定义命令路径。只对没有文本层的页面执行
OCR，已有文本的 PDF 不受影响。

### 模型与知识库

超级管理员在 `/admin` 配置：

- **对话模型**：OpenAI 兼容或 Ollama，可设默认、温度、max tokens，支持连通性测试；
  默认对话模型直接用于逐章生成。
- **图片模型**：配置默认图片模型后，可按章节生成工程示意配图；支持画面描述、风格、
  尺寸、失败重试、本地 PNG/JPEG 上传，并按图名精确匹配占位符或按光标插入正文。
  可在模型 `options` 中设置 `image_size`、`image_quality`、`response_format`。
  配图计划会按章节字数计算预算，可用 `BIDCRAFT_IMAGE_WORDS_PER_IMAGE`（默认 3000 字/张）
  与 `BIDCRAFT_IMAGE_MAX_PER_CHAPTER`（默认 4 张/章）调整；「按计划插入」会遵守该预算。
- **向量模型**：可配置并测试 `/embeddings`，为后续语义检索预留。
- **知识库**：本地目录（PDF/DOCX/TXT/MD/XLSX）或第三方 API（通用 JSON / Dify / RAGFlow）。
  本地索引默认字符二元组召回；配置 embedding 模型后可构建向量索引，按
  「二元组 + 余弦相似度」混合打分重排，未配置或向量失败时自动回退关键词召回。
  启用后按章节检索参考片段注入提示词，台账事实优先级最高。
- **规则包**：内置弱电、消防、安装、土建、暖通、装修等行业包；管理员可直接编辑完整 YAML，
  保存前校验继承链、字段、章节大纲与核对表达式。内置包不会被改写，修改落在
  `BIDCRAFT_HOME/packages/`；删除只从新建项目列表隐藏，已有项目继续可用。
- **用户与权限**：创建投标经理/只读成员，停用、重置密码；系统保护最后一个超级管理员。

密钥使用 `BIDCRAFT_HOME/secret.key` 加密后入库，接口只返回 `has_api_key`，不回传明文。

完整的角色矩阵、首次登录、模型/知识库字段与第三方接口契约见
[`docs/admin-guide.md`](docs/admin-guide.md)。

### 注册、计费与微信

- 登录页支持用户名密码与微信扫码注册/登录（开放平台网站应用）。
- 外部用户拥有独立项目空间，按章扣点；查看、编辑、核对、导出免费。
- 充值支持管理后台配置套餐、批量兑换码，以及微信支付 Native（可 mock 演示）。
- 详细配置、回调、计费规则见 [`docs/billing.md`](docs/billing.md)。

```bash
export BIDCRAFT_SIGNUP_CREDITS=5          # 注册赠送点数
export BIDCRAFT_CHAPTER_COST=1            # 每章生成消耗
export BIDCRAFT_WECHAT_MOCK=1             # 本地演示扫码登录
export BIDCRAFT_WECHAT_PAY_MOCK=1         # 本地演示支付
```

数据默认落在 `./.bidcraft/`（SQLite + 原件 + 导出件），可用 `BIDCRAFT_HOME` 改路径。

## 文档索引

- [`docs/architecture.md`](docs/architecture.md)：组件分层、数据流、并发模型、
  安全边界、部署拓扑与备份/恢复。
- [`docs/admin-guide.md`](docs/admin-guide.md)：管理控制台、角色矩阵、模型/知识库/
  规则包配置与首次登录。
- [`docs/billing.md`](docs/billing.md)：注册、按章扣点、兑换码与微信支付。
- [`docs/roadmap.md`](docs/roadmap.md)：已落地能力与后续版本计划。
- [`docs/mcp.md`](docs/mcp.md)：MCP（JSON-RPC 2.0）服务与 6 个工具接入。
- [`docs/desktop.md`](docs/desktop.md)：桌面客户端（C/S 模式）、本地文件桥与命令行客户端。
- [`docs/plugins.md`](docs/plugins.md)：MCP 客户端插件市场、聚合与安全边界。
- [`docs/calibration.md`](docs/calibration.md)：真实招标文件回归校准命令与报告字段。
- [`docs/C1-spec.md`](docs/C1-spec.md)：领域规格（事实台账 Schema 与规则口径）。
- [`LICENSE`](LICENSE) / [`NOTICE`](NOTICE)：专有许可条款与第三方组件许可。

## MVP 范围（C1–C4 + 最小交付）

| 模块 | 状态 | 说明 |
|---|---|---|
| C1 领域规格 | ✅ | 事实台账 JSON Schema + 9 个 YAML 规则包（通用基包 + 弱电三包 + 消防/安装/土建/暖通/装修） |
| C2 解析与台账 | ✅ | PDF/DOCX/TXT/XLSX；扫描 PDF 可选 `pdftoppm + tesseract` OCR；29 类可扩展字段、评分表、材料清单、参数、要求、废标风险；支持人工校订 |
| C3 规则引擎 | ✅ | YAML 驱动章节识别、评分对齐、核对；安全表达式 DSL，换招标文件不改代码 |
| C4 生成链路 | ✅ | 大纲 → 单章流式/续写 → 事实注入 → 去重 → 数字复核 → 确定性回落；后台任务支持 SSE 逐字预览 |
| C5 核对 | ✅ 最小 | 阻断/严重/主要/提示四级；问题带位置与修订建议；不自动改写 |
| C6 文档交付 | ✅ 最小 | DOCX 封面、TOC 域、页码、宋体/黑体、Markdown 表格、占位标红；Markdown / PDF（LibreOffice，刷新目录页码）/ Excel 工作簿 |
| Excel 清单对齐 | ✅ | 导入招标工程量清单，按行匹配台账材料，标注缺项/多项/数量·单位·规格差异（整数数量精确比对，非整数计量按 0.5% 容差），差异双向回写；支持逐行录入单价、自动计算合价与报价汇总并导出响应表 |
| 图片配图 | ✅ | 按章节生成工程示意配图：画面描述/风格/尺寸、章节正文上下文、失败资产保留与重试、本地 PNG/JPEG 上传、重新生成、按图名精确匹配占位符或按光标插入；项目图库支持跨章节复用，全文配图计划统计各章占位/已匹配/失败/缺失并按章节字数计算预算，可批量生成缺失/失败配图，也可一键按计划插入已匹配配图；整篇配图策略（全文上限/默认风格尺寸/图注/版心宽度）与「生成并插入」一键编排；DOCX 导出嵌入图片并自动图注编号排版 |
| MCP 服务 | ✅ | `bidcraft mcp` 以 stdio 提供 MCP（JSON-RPC 2.0）服务：项目/台账/核对/导出/知识检索 6 个工具，`--readonly` 只读模式；接入配置见 `docs/mcp.md` |
| 批量操作与标段 | ✅ | 项目列表多选后批量核对/批量导出（docx/md/xlsx，后台 Job，zip 打包下载）；项目可填标段/包件并按标段筛选；`PATCH /projects/{ref}` 改名称/标段 |
| 项目复制 | ✅ | 一键复制项目为新模板：招标原件、事实台账、大纲与正文始终复制；清单报价与配图可选；报价版本/审批记录、问题/任务/成员/审计不复制；复制者成为新项目负责人 |
| 项目模板库 | ✅ | 负责人可把项目「设为模板」；全员可浏览模板库并「从模板创建」自己的项目（无需成员身份，复用复制引擎，清单/配图可选）；模板行徽标与创建弹窗 |
| 多包件共享台账 | ✅ | 项目内多包件/标段独立清单快照与分包导出（`{项目名}-{包件名}-清单响应表.xlsx`），台账材料项目级共享不复制；首包自动收养既有清单；包件增删改与权限审计 |
| 企业定额库与调价 | ✅ | 超管维护企业定额（名称/规格/单位/单价/分类），清单按相似度匹配定额并 dry-run 预览后套用单价；项目调价策略（系数 0.1–10、小数位 0–4）与按系数调价；报价汇总含调整后合计 |
| 插件市场（MCP 客户端） | ✅ | 超管注册 stdio/http MCP 插件（预设 filesystem/fetch/memory/time），连通性测试与工具白名单；`bidcraft mcp` 聚合插件工具（`{插件}__{工具}` 前缀），只读模式拒绝插件写调用；stdio 子进程受资源限制（内存 64–4096 MB 默认 512、CPU 1–120 秒默认 10、umask 077、一次性临时工作目录）；`.bidplugin` 插件包导入/导出（同名默认覆盖升级）；见 `docs/plugins.md` |
| 回归校准 | ✅ | `bidcraft calibrate <dir>` 只读扫描真实/仿真招标文件，统计字段命中、必填命中率、材料行数与 OCR 需求，输出 JSON 报告；`--save-baseline/--compare-baseline` 保存/比对基线（字段命中下降、命中率降幅>容差、解析失败增多即回归，exit 0/1/2）；不写库不建项目；见 `docs/calibration.md` |
| 规则包管理 | ✅ | 超级管理员在 `/admin` 创建/修改/隐藏/恢复 YAML 规则包；新建时可填写规则包名称、唯一包名并选择工程类型，YAML 自动同步；内置包只读，用户覆盖写入 `BIDCRAFT_HOME/packages`；保存前校验继承、字段、大纲与核对表达式；新建项目按弱电/消防/安装/土建/暖通/装修分组 |
| SSE 流式进度 | ✅ | 后台生成任务持久化章节/逐字事件，`Last-Event-ID` 断线续传；刷新后自动重新挂接，SSE 不可用时回退轮询 |
| B/S 客户端 | ✅ | React + Vite 单页应用，项目/台账/大纲/正文/配图/核对/清单对齐/导出全流程；本地或局域网部署 |
| 登录与权限 | ✅ | Cookie 会话、scrypt 密码、超级管理员/投标经理/只读成员 RBAC、用户管理 |
| 项目协作与审计 | ✅ | 项目成员（可编辑/只读）由负责人管理；章节 revision 乐观锁，冲突可载入最新或强制覆盖；项目与全局操作审计日志 |
| 模型配置 | ✅ | 管理控制台维护对话/图片/向量模型；默认对话与图片模型接入生成；连通性测试；密钥加密 |
| 知识库 | ✅ | 本地目录索引或通用/Dify/RAGFlow API；可选 embedding 向量索引与混合重排（无模型自动回退关键词）；章节检索注入；台账事实优先 |
| 注册与计费 | ✅ | 用户名/微信扫码注册、独立项目空间、按章扣点、兑换码、微信支付 Native（可 mock） |
| 桌面客户端（C/S） | ✅ | `bidcraft desktop --server URL` 启动 **Qt 原生外壳**（PySide6 + QtWebEngine）：菜单栏/工具栏/状态栏、原生导航与快捷键、应用图标与窗口标题、系统托盘与最小化到托盘、单实例激活、窗口几何与服务器记忆、原生文件对话框上传与下载保存、托盘通知、在线状态轮询与检查更新；界面内请求经 **JS 桥直连服务端 REST API**（Cookie 由客户端持有），断网时回放**只读离线缓存**并显示离线横幅；Qt 不可用时逐级回退 pywebview 内嵌窗口 → 浏览器网关（仅绑定 127.0.0.1）；另有 PyInstaller 打包脚本与 GitHub Actions 三平台构建（Windows `.exe`、macOS `.app`、Linux `.deb`/`.rpm`）；`bidcraft client` 命令行完成登录/项目/上传/下载；见 `docs/desktop.md` |
| 我的文档（个人知识库） | ✅ | 普通用户自助上传文件（拖拽选择、≤50MB、格式校验、进度）到服务器个人目录并自动建索引；「我的文档」页管理（列表/状态/下载/删除）；上传即建个人知识库，生成检索只注入本人文档且默认搜索排除个人库防跨用户泄漏；审计 `user_doc.upload/delete` |
| VIP 会员 | ✅ | 超管授予/续期/清除 VIP（天数），或自助购买 VIP 套餐（月/季/年卡，mock 支付续期不加点）；VIP 按章扣点享折扣（可配 0.1–1）；VIP 后台任务排队优先（priority=0） |
| 子账号 | ✅ | VIP 主账号自助创建子账号（上限可配），子账号共享主账号点数余额、不可单独充值/兑换/购买 VIP；删除子账号其项目自动移交主账号 |
| 优先级任务队列 | ✅ | 后台任务（批量核对/导出、配图、生成）经优先级堆队列调度，VIP 用户任务优先出队；并发数 `BIDCRAFT_MAX_JOBS`（默认 2，1–8）；job payload 回显 priority/queue_position |
| 报价版本与汇率 | ✅ | 报价快照版本保存/恢复/删除（每包件上限 20）、版本对比（新增/移除/改价 + 两侧合计）、税率与币种策略、汇率换算与人民币含税合计、导出「报价汇总」含税/汇率行 |
| 报价审批流 | ✅ | 版本状态机（草稿→待审→已定稿/已驳回），成员提交审批、负责人定稿/驳回、可撤销定稿并重新提交；同包件至多一个定稿，定稿新版自动退回旧版；双版本（含当前清单）并排审阅视图；**项目审批链**（负责人配置 ≤5 级有序审批人，每级 1–5 人、会签 all/或签 any，逐级通过、末级定稿，越级 403，超管可代审；空链回退负责人单级）；审批级可设**金额门槛**（报价总额达到才需该级审批，低价自动跳过）；审计 `quote.submit/approve/reject/unapprove`；顶栏「待我审批」跨项目待办（数量徽标 + 只读面板，点击直达项目清单页签，60s 轮询）；审批级可按**包件 / 币种**白名单限定生效范围（与金额门槛组合，未命中自动跳过） |
| 微信开放平台管理 | ✅ | 管理控制台配置 AppID/AppSecret（加密存库不回显）、回调地址、启用与模拟模式、连通性测试；保存即热生效，登录页自动出现扫码入口 |
| 企业术语库 | ✅ | 管理端维护「禁用表述 / 建议替换」术语；项目核对自动产出 `TERM-###` 问题（禁用词 major、替换建议 minor，含原文证据与建议） |

未做（按评估建议留到完整版）：插件远程市场自动安装与系统级隔离（受控本地插件包导入/导出已支持；无容器/网络隔离）、真实招标件的持续回归基线扩充。

## 目录

```
bidcraft/
  parsing/      PDF/DOCX/TXT 加载、可选 OCR、去噪、字段/表格抽取
  rules/        YAML 包加载与合并、用户覆盖存储、安全表达式核对引擎
  generation/   大纲、提示词、模型客户端、正文生成与去重
  auth.py       登录、会话、RBAC 与初始管理员
  security.py   scrypt 密码、会话令牌、Fernet 密钥加密
  model_config.py 模型端点解析与连通性测试
  knowledge.py  本地知识库索引、向量混合检索与第三方检索适配
  images.py     图片生成、上传、重生成、项目图库、配图计划、整篇策略与 DOCX 嵌入
  mcp.py        MCP stdio 服务（JSON-RPC 2.0，6 个工具，可只读）
  plugins.py    MCP 客户端插件（stdio/http 握手与工具调用）与 mcp 聚合
  boq.py        工程量清单解析、材料匹配、差异回写、单价与报价汇总、包件隔离、定额套用、调价策略
  calibrate.py  只读回归校准：扫描招标文件目录统计字段命中与 OCR 需求，基线保存/比对，不写库
  export/       DOCX / Markdown / PDF（LibreOffice + UNO）/ XLSX / 清单响应表
  resources/
    packages/   common-bid-facts + 弱电三包 + 消防/安装/土建/暖通/装修
    schema/     fact_ledger.schema.json
  tools/        Schema 再生成
  api.py        FastAPI：B/S 接口 + 静态客户端托管 + 后台生成任务
  web_dist/     Web 客户端构建产物（npm run build 生成）
  cli.py        Typer 命令行
web/            React + TypeScript + Vite 客户端源码
docs/
  research/     两套上游源码的规格提取（只记录规则，不复制代码）
  architecture.md 组件、数据流、并发模型、安全边界与备份/恢复
  admin-guide.md  管理控制台：角色矩阵、模型/知识库/规则包/用户配置
  billing.md      注册、计费、兑换码与微信支付（Native）配置
  C1-spec.md      领域规格说明
  mcp.md          MCP 服务接入与工具说明
  plugins.md      插件市场：MCP 客户端插件、聚合、安全边界与排错
  calibration.md  真实招标文件回归校准：命令、报告字段与别名维护
  roadmap.md      版本路线与后续计划
tests/          单元 + 端到端回归
```

## 规则包格式

```yaml
package: weakcurrent-cabling
project_type: weakcurrent
project_type_label: 弱电智能化
extends: [common-bid-facts]
fields:
  - {key: cabling.point_count, label: 信息点位数量, aliases: [信息点数量], required: true, type: number}
outline:
  items:
    - {heading: 综合布线系统总体设计, target_words: 5000, needs: [cabling.point_count]}
checks:
  - id: CBL-001
    level: blocking
    assert: "number(facts['cabling.point_count']) == materials_total(materials, '信息点')"
    message: 信息点位数与工程量清单不一致
```

`project_type` 可选 `weakcurrent/fire/installation/civil/hvac/decoration/other`，
用于新建项目分组；管理员覆盖包写入 `BIDCRAFT_HOME/packages/`，删除只隐藏、不影响已有项目。

核对表达式只允许比较、布尔运算、索引与白名单函数（`present/contains/number/
param_response_ratio/storage_tb/...`），由 AST 白名单执行，不是 `eval` 裸跑。

## 验证

```bash
uv sync --frozen --extra api --extra dev   # 复现锁定环境
uv run ruff check bidcraft tests
uv run pytest
bidcraft doctor
```

CI（`.github/workflows/ci.yml`）在 push / PR 时执行同样的两道门禁：Python 侧
`ruff check bidcraft tests` + `pytest -q`（Python 3.13），Web 侧 `npm run lint` /
`npm run test` / `npm run build`（Node 22）。测试全部使用 mock 端点与本地临时目录，
不需要数据库、模型或网络服务。

回归链路：`tests/test_e2e.py` 用合成招标文件跑「解析 → 台账 → 大纲 → 草稿 → 核对 → DOCX」，
并断言抽取字段、规则命中、Markdown 表格转 Word 表格与文件产出；`tests/test_pdf.py` 在装有
LibreOffice 的机器上验证 PDF 与目录页码；`tests/test_xlsx.py` 覆盖 Excel 招标文件与台账导出；
`tests/test_api.py` 覆盖 HTTP 项目全流程、台账/大纲/章节编辑、后台生成任务与删除；
`tests/test_boq.py` 与 `tests/test_api_boq.py` 覆盖清单表头识别、空白单元格保列、材料模糊匹配、
数量容差、单向缺项、差异回写、忽略/恢复、单价录入/清除、合价与报价汇总、响应表导出与重传清空报价；
`tests/test_admin.py` 覆盖登录、RBAC、模型配置脱敏、本地知识库索引/检索与用户保护规则；
`tests/test_knowledge_vector.py` 覆盖向量模型解析、向量构建与换模型失效、混合排序、
无模型/查询失败回退、阈值过滤、限 chunk 截断与 embed/reindex 接口；
`tests/test_collab.py` 覆盖项目成员权限矩阵、my_role、审计分页、章节 revision 冲突与强制覆盖、删项目级联清理；
`tests/test_api_jobs.py` 覆盖任务事件持久化、SSE 回放与 `Last-Event-ID` 续传、跨用户任务隔离、
后台生成事件落库与刷新后的任务列表；`tests/test_ocr.py` 用假命令覆盖 OCR 探测、语言包回退、
选页/限页、单页失败继续、扫描 PDF 接入与 OCR 不可用降级，不依赖本机 tesseract；
`tests/test_images.py` 用假图片端点覆盖提示词/风格/尺寸、章节上下文、失败资产保留与重生成、
PNG/JPEG 校验、本地图片上传、跨项目章节校验、只读权限、DOCX 图片嵌入，以及全文配图计划
的已匹配/失败/缺失统计、章节配图预算、按计划插入、批量生成缺失配图与失败重试任务；
`tests/test_rule_packages.py` 覆盖规则包列表类型、管理员创建/覆盖内置包、隐藏与恢复、
被引用删除 409、YAML/表达式校验、包名不一致与非超管权限；
`tests/test_image_policy.py` 覆盖整篇配图策略 clamp/权限/审计、全文预算跳过、
DOCX 图注编号与版心宽度、批量生成默认风格尺寸；`tests/test_mcp.py` 覆盖 MCP 协议
（initialize/ping/错误码）、6 个工具与只读模式、stdio 子进程冒烟；
`tests/test_batch.py` 覆盖标段字段与 PATCH、批量核对/导出的权限过滤、zip 打包下载与部分失败；
`tests/test_packages.py`、`tests/test_quota.py`、`tests/test_plugins.py`、`tests/test_calibrate.py`
分别覆盖多包件隔离与收养、定额套用与调价、插件 CRUD/聚合/只读拒绝、校准报告与 CLI；
`tests/test_quote_versions.py`、`tests/test_price_tax.py`、`tests/test_version_diff.py`
覆盖报价版本、税率与汇率、版本对比；`tests/test_quote_approval.py` 覆盖审批状态机与自动退定稿；
`tests/test_quote_approval_chain.py`、`tests/test_quote_approval_parallel.py`、`tests/test_quote_approval_condition.py`、`tests/test_quote_approval_conditions.py`、`tests/test_approvals_pending.py` 覆盖多级审批链配置、逐级流转、会签/或签、金额门槛与包件/币种条件级及跨项目待我审批；
`tests/test_plugin_guard.py`、`tests/test_plugin_sandbox.py`、`tests/test_plugin_pack.py`
覆盖插件连续失败自动停用、资源限制沙箱、插件包导出/导入/升级/冲突与非法包校验；
`tests/test_wechat_admin.py`、`tests/test_terms.py` 覆盖微信开放平台配置与术语库核对；
`tests/test_hardening.py`、`tests/test_backup.py`、`tests/test_kb_reindex_job.py`、
`tests/test_security.py`、`tests/test_wechatpay.py`、`tests/test_jobs_unit.py`、
`tests/test_store_unit.py` 覆盖上传限额/SSRF/分页等加固、在线备份、异步重建索引、
加密与支付签名、任务工具与规则包存储；`tests/test_calibrate_baseline.py` 覆盖基线
保存/比对规则、容差边界与 CLI 退出码，并以提交基线做夹具门禁；`tests/test_project_duplicate.py`
覆盖项目复制的全量深拷贝、外键重映射、可选清单/配图、排除项与失败清理；`tests/test_project_templates.py`
覆盖模板标记权限、模板列表与从模板创建（含非成员实例化与失败清理）；
`tests/test_vip.py`、`tests/test_subaccounts.py`、`tests/test_jobqueue.py` 覆盖 VIP 授权/续期/折扣、
子账号共享计费与权限隔离、优先级队列调度、审批条件分支；`tests/test_user_docs.py` 覆盖个人文档上传/索引、列表/下载/删除、跨用户隔离与检索防泄漏、审计；`tests/test_client.py`、`tests/test_desktop.py` 覆盖客户端登录/Cookie 复用、文档与原件上传、下载、网关反代与 Cookie 透传、静态/SPA 回退、令牌门禁与本地文件桥；`tests/test_offline_cache.py`、`tests/test_qtshell.py` 覆盖只读离线缓存回放与淘汰、Qt 外壳版本比较/桥接注入脚本/导航映射/设置键与 Qt 缺失回退（当前全量 **564 passed**）。

前端验证：`cd web && npm run build`（TypeScript 编译 + Vite 产物输出到
`bidcraft/web_dist`），随后 `bidcraft serve --open` 做浏览器全流程验收。

## 许可与合规

本项目以**专有许可**发布：版权归 yaoyongli1974-dotcom 所有，允许自用与修改，
**禁止未经授权的分发或对外托管服务**。完整条款见 [`LICENSE`](LICENSE)，
第三方依赖及其许可见 [`NOTICE`](NOTICE)。

上游 fb-labs/ZhiBiao 与 FB208/OpenBidKit_Yibiao 均为 AGPL-3.0：本项目只参考其
**领域规格与行为**，不包含其源代码；如未来需要复制上游代码，必须重新评估
AGPL §13 与 NOTICE 归属义务。
