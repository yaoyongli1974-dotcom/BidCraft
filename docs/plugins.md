# 插件市场（MCP 客户端插件）

BidCraft 既是 MCP **服务端**（`bidcraft mcp`，见 `docs/mcp.md`），也可以作为 MCP
**客户端** 连接外部 MCP server（filesystem、fetch、memory、time 或自建服务），
把这些 server 的工具聚合进自己的 `tools/list`。这样 Claude Desktop / Cursor
只需要接入一个 BidCraft 端点，就能同时使用项目内核工具与第三方工具。

- 不常驻：每次 `tools/list` / `tools/call` 都重新启动（或握手）插件，调用结束立即
  结束子进程，插件崩溃不会留下僵尸进程。
- 不自动安装：仓库只提供 `PLUGIN_CATALOG` 预设模板，安装与启用必须由超管显式完成。
- 默认不启用任何插件：新库的 `plugins` 表为空。

## 架构

```
MCP 客户端 ──stdio──> bidcraft mcp（mcp.py）
                        │  tools/list：本征 6 工具 + Σ 启用插件工具
                        │  tools/call：前缀路由
                        ▼
                 bidcraft/plugins.py（MCP 客户端）
                   ├─ stdio：subprocess.Popen(command + args)，逐行 JSON-RPC
                   └─ http ：httpx.post(url, json=rpc)
                        ▼
                 外部 MCP server（npx / uvx / 自建 HTTP 服务）
```

握手（两种传输相同，`protocolVersion: 2024-11-05`）：

1. `initialize`（clientInfo: `bidcraft-plugin-client` + 版本）；
2. `notifications/initialized`；
3. `tools/list` 或 `tools/call`。

单请求超时读 `BIDCRAFT_PLUGIN_TIMEOUT`（默认 20 秒，配置层 clamp 到 5–120）。

stdio 插件子进程受资源限制：内存上限（`mem_limit_mb`，64–4096 MB，默认 512）映射
`RLIMIT_AS`、CPU 时间（`cpu_seconds`，1–120 秒，默认 10）映射 `RLIMIT_CPU`；子进程 umask
为 077，并在一次性临时目录（`bidcraft-plugin-*`，运行结束即删除）中运行。管理端保存时
越界或非法值回退默认值（512/10），HTTP 插件忽略这两个字段。

## 数据模型

表 `plugins`（`bidcraft/models.py::Plugin`，由 `create_all` 自动创建）：

| 字段 | 说明 |
|---|---|
| `name` | 唯一，≤60 字符；不能含 `__`（工具名前缀分隔符）；聚合工具名为 `{name}__{tool}` |
| `transport` | `stdio` 或 `http` |
| `command` / `args` / `env` | stdio 子进程；`env` 叠加在最小环境（PATH 等）之上 |
| `url` | http 端点（必须以 `http://` 或 `https://` 开头） |
| `enabled` | 关闭后不出现在 `tools/list`，调用返回“已禁用” |
| `tool_allowlist` | 空 = 该插件全部工具；非空 = 只暴露/允许列出的工具名 |
| `last_test_at/ok/message` | 最近一次“测试连接”的结果 |

## 管理 API（仅超级管理员）

| 方法与路径 | 说明 |
|---|---|
| `GET /admin/plugins` | 列表，含 `last_test_*` |
| `POST /admin/plugins` | 201；名称重复 409；stdio 缺 `command` / http 缺 `url` → 400 |
| `PATCH /admin/plugins/{id}` | 局部更新；改动后同样做 transport 校验 |
| `DELETE /admin/plugins/{id}` | `{"deleted": true, "id": ...}` |
| `POST /admin/plugins/{id}/test` | `{ok, message, tools: [工具名]}`，同时写 `last_test_*`；失败 `ok=false` 不返回 5xx |
| `GET /admin/plugins/catalog` | 预设模板（name/description/transport/command/args/url） |

```bash
curl -b cookie.txt -X POST http://127.0.0.1:8000/admin/plugins \
  -H 'content-type: application/json' \
  -d '{"name":"filesystem","transport":"stdio","command":"npx",
       "args":["-y","@modelcontextprotocol/server-filesystem","/srv/tenders"],
       "tool_allowlist":"read_file,list_directory"}'
```

`args` / `tool_allowlist` 接受数组、JSON 文本或逗号分隔字符串；`env` 接受 JSON 对象
或 JSON 文本（管理端表单可以直接提交文本）。所有变更写审计
`plugin.create/update/delete/test`（`project_id = NULL`）。

## 聚合行为

- `tools/list`：本征 6 个工具在前，其后是每个启用插件的工具，命名为
  `{plugin}__{tool}`，`description` 前缀 `[{plugin}] `，`inputSchema` 原样透传。
  `tool_allowlist` 非空时只列出允许的工具。
- 单个插件握手失败/超时/命令不存在：**跳过该插件**，往 stderr 打一行警告，
  其余工具照常返回。因此一个坏插件不会让整个 MCP 端点不可用。
- `tools/call`：名称前缀匹配插件 → 校验 `enabled` 与 allowlist → 调用插件，
  插件的 MCP `result`（`content` / `isError` / `structuredContent`）原样返回；
  插件错误转成 `isError: true` 的中文说明。
- 只读模式（`bidcraft mcp --readonly`）：**拒绝所有插件工具**，内置只读工具行为不变。
- 内置工具名不含 `__`，插件名禁止含 `__`，因此前缀路由不存在歧义。

## 预设

`PLUGIN_CATALOG` 只是模板，选中后仍需填写目录/命令并显式保存：

| 预设 | 命令 | 用途 |
|---|---|---|
| `filesystem` | `npx -y @modelcontextprotocol/server-filesystem <目录>` | 受控目录读写 |
| `fetch` | `uvx mcp-server-fetch` | 抓取网页正文 |
| `memory` | `npx -y @modelcontextprotocol/server-memory` | 跨会话记忆 |
| `time` | `uvx mcp-server-time` | 时区换算 |

`npx`/`uvx` 首次运行会联网下载包；离线部署请改用自建 stdio 可执行文件或 `http` 传输。

## 安全边界

- 插件以 **BidCraft 进程的操作系统用户** 身份运行，或向管理员填写的 URL 发请求：
  **只配置你信任的插件**。子进程只继承最小环境变量（`PATH` 等 + 插件自己的 `env`），
  不会拿到 `BIDCRAFT_MODEL_API_KEY`、数据库连接等密钥。
- 子进程 `stderr` 丢弃，`stdout` 只按 JSON-RPC 解析；混入的日志行会被忽略。
- 只有超级管理员能增删改插件；普通用户既看不到 `/admin/plugins`，也无法通过
  MCP 端点绕过 allowlist。
- `--readonly` 的 MCP 端点不暴露任何插件写工具，适合把 BidCraft 交给不可信客户端。
- 插件的 `env` 以明文存库（仅超管可见），不要在其中保存长期密钥。
- 不引入自动安装、远程市场拉取或遥测：配置即代码，全部留在本机 SQLite。

## 配置示例

```bash
# 单请求超时（秒），默认 20，范围 5–120
export BIDCRAFT_PLUGIN_TIMEOUT=45
```

自建 HTTP MCP server（把 JSON-RPC 作为 POST body，返回 JSON-RPC 响应）：

```json
{"name": "internal-search", "transport": "http",
 "url": "https://mcp.internal.example/rpc",
 "tool_allowlist": ["search_docs"]}
```

## 排错

| 现象 | 处理 |
|---|---|
| `插件 X 启动失败：...` | `command` 不在 PATH / 不可执行；stdio 插件用绝对路径更稳 |
| `插件 X 响应超时` | 首次 `npx`/`uvx` 下载慢；调大 `BIDCRAFT_PLUGIN_TIMEOUT` 或预装 |
| `插件 X 提前退出（退出码 N）` | 在终端手工执行 `command + args` 看 stderr |
| 工具列表里没有插件工具 | 检查 `enabled`、`tool_allowlist`，以及后端 stderr 的“已跳过”警告 |

## 验证

`tests/test_plugins.py` 覆盖：stdio 真实子进程假 server（list/call/超时/坏命令/
非零退出/进程回收）、http（`httpx.MockTransport` 验证握手顺序与错误）、
管理 API CRUD/409/400/403/test 写 `last_test_*`/catalog/审计、
MCP 聚合（前缀、allowlist、失败跳过、只读拒绝、调用路由）。
测试不依赖 `npx`/`uvx`。
