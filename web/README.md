# BidCraft Web · 标书工坊

弱电 / 智能化投标经理使用的 B/S 工作台。把招标文件导入、解析成事实台账，再经过校订、大纲、正文、合规核对，最后导出 DOCX / PDF / XLSX。所有数据留在部署本机，界面不发送遥测，也不依赖 CDN 字体或外部静态资源。

## 技术栈

- React 18 + TypeScript + Vite
- React Router（Browser history 路由）
- 原生 CSS（设计变量 + 少量组件样式），无 UI 框架、无 Tailwind 构建
- `lucide-react` 图标
- HttpOnly Cookie 会话 + 前端路由守卫；无 mock 数据、无演示账号

## 本地开发

要求 Node.js 20+，后端运行在 `http://127.0.0.1:8000`。

```bash
cd web
npm install
npm run dev
```

Vite 开发服务器运行在 `http://127.0.0.1:5173`，并把以下根级命名空间代理到后端（默认 `http://127.0.0.1:8000`）：`/health`、`/packages`、`/my`、`/projects`、`/enterprise`、`/commercial`、`/similarity`、`/evaluation`、`/orchestration`、`/agents`、`/collaboration`、`/jobs`、`/batch-jobs`、`/api`、`/auth`、`/account`、`/approvals`、`/legal`、`/billing`、`/admin`。`/admin`、`/billing`、`/account`、`/legal` 同时也是 SPA 路由：带 `Accept: text/html` 的浏览器导航会交给 Vite 的历史回退（支持深链刷新），带 `Accept: application/json` 的 API 请求才走代理。目标地址可覆盖：

```bash
BIDCRAFT_API_TARGET=http://127.0.0.1:8765 npm run dev
```

## 构建与部署

```bash
npm run build
```

路由页面使用 `React.lazy` 按需加载：首屏只下载应用外壳与当前页面，工作台各页签在打开时才加载。`vite.config.ts` 将 React / React Router 与 `lucide-react` 拆成独立 chunk。构建产物输出到 **`../bidcraft/web_dist`**，`base: '/'`。后端将静态文件与 API 同源托管；非 API 路径回退到 `index.html`，因此 `/app/...` 深链接可以刷新。

路由约定：

- `/login`：公开登录页
- `/register`：公开注册页
- `/billing`：账户与充值（需登录；只读成员可查看，不能购买/兑换）
- `/`：项目列表（需登录）
- `/app/projects/:ref`：项目工作台（概览）
- `/app/projects/:ref/sources`：招标文件
- `/app/projects/:ref/ledger`：事实台账
- `/app/projects/:ref/boq`：清单对齐
- `/app/projects/:ref/outline`：目录大纲
- `/app/projects/:ref/draft`：正文
- `/app/projects/:ref/check`：核对
- `/app/projects/:ref/export`：导出
- `/app/projects/:ref/collab`：协作与审计
- `/admin`：管理控制台（仅超级管理员；非管理员显示 403 状态）
- API 始终在根路径 `/health`、`/packages`、`/my`、`/projects`、`/jobs`、`/batch-jobs`、`/auth`、`/account`、`/approvals`、`/legal`、`/billing/*`、`/admin/*`、`/agents`、`/enterprise`

## 认证与权限

- 会话 Cookie `bidcraft_session` 由后端设置，前端不读取、不持久化密码。
- 启动时调用 `GET /auth/me`；加载期间显示品牌骨架屏，避免先闪现项目页再跳登录。
- 所有受保护路由未登录时跳转 `/login?next=…`，登录后返回原目标。
- 任意受保护 API 返回 401 时，统一清空认证状态并跳转登录；登录与 `/auth/me` 请求不会触发循环重定向。
- 登录页不提供任何演示凭据或默认密码。健康检查 `registration` 为真时开放 `/register` 自助注册；注册成功自动登录并赠送体验点数。
- 微信登录启用时，登录页与注册页展示“微信扫码登录”；`wechat_mock` 模式走本地模拟回调，真实模式跳转微信授权页。均为整页跳转，不经过 fetch。
- 角色：`super_admin`（全部权限 + 管理控制台）、`manager`（项目读写，无管理控制台）、`viewer`（只读）。
- 注册用户默认获得 `manager` 角色与体验点数；用户菜单显示当前点数。
- 点数不足时生成接口返回 402，正文页展示“点数不足 / 去充值”横幅与 Toast，不吞掉原有错误。
- 只读成员在项目列表、招标文件、事实台账、大纲、正文、核对与导出页均看到明确的“只读成员”提示；写按钮禁用/隐藏，录入控件只读，后端 403 会以可读 Toast 呈现。
- 用户菜单提供“修改密码”；修改成功后强制退出并跳转登录页，提示使用新密码登录。

## 管理控制台

仅超级管理员可进入 `/admin`，非管理员显示 403 页面。八个分区：

- **模型配置**：按对话 / 图片 / 向量分段与计数；默认对话模型醒目标记；密钥只显示“已配置/未配置”；连接测试显示成功/失败、延迟与后端原始消息；图片模型用于正文配图；测试按钮只验证端点连通性，实际生成在正文页图片占位触发。
- **知识库**：本地目录与第三方接口分组；显示路径/端点、片段数、索引时间、密钥状态与测试结果；本地库支持确认后重建索引并显示索引片段数。
- **规则包**：行业规则包、YAML 校验与项目类型；内置包只读、覆盖包可恢复。
- **定额库**：企业综合单价维护、分类筛选与匹配测试。
- **用户与权限**：创建、编辑、启用/停用、删除成员；显示点数并可增减（正数增加、负数扣除）；角色说明与密码重置；后端保护性 400 错误原样展示。
- **审计日志**：全站写操作与成员变更的倒序记录，支持按项目过滤。
- **计费与充值**：注册赠送与每章消耗设置、充值套餐编辑、兑换码生成与复制、外部用户 / 营收 / 流通点数指标、最近订单与兑换码。
- **插件市场**：MCP 客户端插件的本机命令 / HTTP 工具接入、启用状态与连通性测试。

## 功能

### 项目列表

- 新建项目：项目名称 + 规则包选择（行业包优先，通用包置底）；只读成员不可新建或删除
- 项目表：业务项目编号、规则包、状态、招标人、章节进度、事实覆盖、缺失必填、未关闭问题、更新时间
- 搜索、排序、删除确认、空状态、加载骨架
- 项目编号使用后端 `number` 字段；缺失时显示“未提取”，不把内部 `id` 当业务编号

### 项目工作台

- 持久上下文头部：项目名称、规则包、招标人、项目编号、状态、保存/模型状态
- 九个一级页签：概览、招标文件、事实台账、清单对齐、目录大纲、正文、核对、导出、协作
- 概览：七步推进清单、下一步 CTA、事实/章节/门禁/问题统计、最近文件与问题
- 招标文件：拖拽/选择上传、上传进度、解析状态、页数/大小/备注、重新解析确认、原件下载、OCR 提示
- 事实台账：
  - 按 `project / commercial / qualification / technical / bidder` 分组（兼容行业包键前缀）
  - 搜索、仅缺失、仅必填；行内编辑、状态选择、置信度、来源引用展开
  - 缺失必填高亮，生成门禁横幅，409 `missing_required` 自动跳转并高亮
  - 材料清单 / 技术参数 / 评分办法 / 实质性要求 / 废标风险只读表
  - 招标文件格式骨架
- 目录大纲：自动 / 规则包 / 评分导向 / 招标格式；目标字数；重新生成前确认清空正文；章节增删、上下移动、字数分配；逐章生成；未保存离开守卫
- 正文：双栏章节列表 + Markdown 编辑器/预览；已有正文默认进入预览，编辑需显式切换；字数目标；Ctrl+S 保存；逐章生成/重新生成确认；全书生成任务轮询与顶部进度条；待填占位提示/预览标红；未保存守卫
- 配图：图片占位可生成图片并插入正文；本章图库支持缩略图、插入、删除与失败原因；Markdown 预览渲染本机相对图片；图片生成暂不计费
- 核对：通过/关注/阻断结论、级别与类别筛选、问题卡（编号/级别/位置/发现/建议/证据）、检查规则总数（summary.total）、重新核对
- 导出：DOCX / PDF / XLSX / Markdown；二进制 blob 下载与 `Content-Disposition` 文件名；后端错误 `detail` 展示；正文缺失禁用状态；模型/离线提示；本机导出记录
- 设置与帮助抽屉：版本、服务/模型/离线状态、数据目录、局域网说明、工作流、快捷键
- 当前账号菜单：显示名称、角色与点数、账户与充值、管理控制台（仅超级管理员）、修改密码、退出登录

### 交互与可访问性

- 全站键盘焦点态、跳过导航、模态焦点陷阱、Esc 关闭
- 页签（台账分类、管理控制台）支持左右方向键 / Home / End 切换与 roving tabindex，页签与面板通过 `aria-controls` / `aria-labelledby` 关联
- 路由切换后焦点移到页面标题（懒加载期间落到主内容区），应用级错误边界提供重新加载与错误摘要
- `Ctrl/Cmd + S` 保存正文/大纲
- 事实台账内按 `/` 聚焦搜索
- 确认弹窗、Toast、骨架屏、空状态、错误重试
- `prefers-reduced-motion` 降级
- 390 / 768 / 1024 / 1440 宽度均无页面级横向滚动；表格在容器内横向滚动

## 目录

```text
web/
  index.html
  package.json
  vite.config.ts          # Vite + Vitest 配置（含 dev 代理与 chunk 拆分）
  eslint.config.js
  tsconfig.json
  README.md
  src/
    main.tsx
    App.tsx                # Browser router + 登录守卫
    components/            # 外壳、弹窗、用户菜单、密码、AuthGate、反馈
    context/               # auth / toast / confirm / health / help
    lib/                   # API client、类型、格式化、查询缓存、hooks、Markdown 渲染
    test/                  # Vitest setup（Testing Library + jsdom）
    pages/
      LoginPage.tsx
      RegisterPage.tsx
      ProjectsPage.tsx
      WorkspacePage.tsx
      BillingPage.tsx
      admin/               # 控制台 + 模型 / 知识库 / 规则包 / 定额 / 用户 / 计费 / 审计 / 插件
      tabs/                # 九个工作台页面
    styles/                # global / workspace / responsive / auth-admin / billing
    lib/markdown.css
```

## API 与兼容处理

- 同源请求，不配置 API base URL；所有请求使用相对根路径并携带会话 Cookie
- JSON 与 401 处理集中在 `apiFetch`；登录、`/auth/me`、登出不会触发 401 跳转循环
- 所有 multipart 上传统一走 `uploadForm`（进度回调、401 重新登录、403 / 网络错误均转成 `ApiError`）
- `useQuery` 带 30 秒模块级缓存、同 key 并发去重与过期响应丢弃；登录、登出、改密会清空缓存
- `/admin/models`、`/admin/kbs`、`/admin/users`、`/admin/billing`、`/admin/redeem-codes` 均通过统一客户端返回并刷新
- `/projects/{ref}/images` 提供章节配图的列出、生成与删除；`url` 是受 SPA 路径守卫允许的相对路径
- `/billing/summary` 返回余额、套餐、流水、订单与微信支付开关；模拟支付与真实二维码支付共用同一订单面板
- 真实微信支付通过 `/billing/orders/{id}/qr` 图片与 2 秒轮询 `GET /billing/orders/{id}` 确认到账
- 测试结果不假定持久字段以外的延迟值
- `GET /projects` 与 `GET /projects/{ref}` 支持顶层 `stats`；缺失时从 `ledger_summary`、`outline`、`sources` 等字段降级计算
- `GET /outline` 支持 `{chapters}` 或裸数组；项目详情的 `outline` 支持数量或章节数组
- `POST /generate` 202 → 轮询 `GET /jobs/{jobId}`，处理 queued / running / done / failed
- 导出读取 `Content-Disposition`，兼容 `filename*=UTF-8''…` 和普通 `filename`
- 409 `{detail:{missing_required}}` 统一跳转事实台账并高亮对应 key
- 后端错误 `detail` 为字符串、数组或对象时均转成可读中文提示

## 设计与约束

- 纸张/墨色/朱砂印的视觉基调，避免通用紫色渐变；正文预览使用衬线正文，界面使用无衬线
- 8pt 间距体系，数据密度优先，弱化装饰但保留明确层级
- 无 CDN 字体；中文优先使用系统字体栈（思源黑体/宋体、苹方、微软雅黑等）
- 前端不生成业务数据：空就是空，缺失就是缺失，所有统计来自后端或本机记录

## 已知边界

- 扫描 PDF 的 OCR 不在前端范围；界面会明确提示需先 OCR
- 全书生成任务在页面刷新/离开后不会自动恢复轮询；重新进入正文页可手动刷新章节（生成仍由后端继续）
- `GET /issues` 目前没有“标记已处理”的变更接口，因此只展示状态，不提供状态修改
- 章节排序使用上/下移动按钮，未实现拖拽排序
- PDF 导出依赖服务端安装 LibreOffice，失败时展示后端错误
- 生产环境必须由后端同源托管，不支持把 `web_dist` 直接双击 `file://` 打开
- `/admin` 与后端 API 前缀同名；正常 SPA 导航可用，直接刷新 `/admin` 需要有效的超级管理员 Cookie，否则后端会优先返回 401/403 JSON

## 验证

```bash
npm run lint      # ESLint（typescript-eslint + react-hooks + react-refresh）
npm run test      # Vitest + Testing Library（jsdom）
npm run typecheck
npm run build
```

构建体积（2026-11，生产压缩，以构建输出为准）：

- 入口 `index`：约 77 kB（gzip 26 kB）
- React / React Router chunk：约 208 kB（gzip 68 kB）
- `lucide-react` chunk：约 47 kB（gzip 9 kB）
- 其余路由与工作台页签各自按需加载（约 2–113 kB，最大为管理控制台）
- 样式：约 215 kB（gzip 37 kB），正文页专属样式另按需加载

路由级代码分割前，单一 JS 入口约 658 kB（gzip 205 kB）。现在首屏只加载外壳、当前路由与上述公共 chunk。具体数值以每次构建输出为准。

视觉检查覆盖：登录、注册、微信模拟登录、账户与充值、管理控制台、项目工作台、正文写作、图片占位生成/图库/插入/删除、Markdown 图片预览、DOCX 图片导出、402 点数不足，以及 1440 / 1024 / 768 / 390 宽度。
