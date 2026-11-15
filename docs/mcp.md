# MCP 服务（bidcraft mcp）

BidCraft 内置一个 **MCP stdio 服务**，可把项目、事实台账、核对与导出能力接入
Claude Desktop、Cursor 等支持 MCP 的客户端。协议为 JSON-RPC 2.0，
`protocolVersion: 2024-11-05`；不新增第三方依赖。

## 启动

```bash
bidcraft mcp              # 读写模式
bidcraft mcp --readonly   # 只读模式：禁用 run_check 与 export_document
```

- 服务从 **stdin 逐行读取 JSON-RPC 消息**，向 **stdout 只写协议 JSON**；
  启动提示与日志全部走 stderr，不会污染协议流。
- 数据目录沿用 `BIDCRAFT_HOME`（默认 `.bidcraft`），与 Web/CLI 共用同一 SQLite 库。
- stdio 通道信任本机启动者；如需限制能力，使用 `--readonly`。

## 客户端配置

Claude Desktop（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "bidcraft": {
      "command": "/绝对路径/BidCraft/.venv/bin/bidcraft",
      "args": ["mcp"],
      "env": { "BIDCRAFT_HOME": "/绝对路径/BidCraft/.bidcraft" }
    }
  }
}
```

Cursor（`.cursor/mcp.json`）结构相同。只读接入把 `args` 改为 `["mcp", "--readonly"]`。
`command` 必须是可执行文件的绝对路径（或保证在客户端 PATH 中）。

## 工具清单

| 工具 | 参数 | 说明 | 只读模式 |
|---|---|---|---|
| `list_projects` | 无 | 项目列表（id/name/package/status/bid_section/updated_at，最多 100） | 可用 |
| `get_project` | `ref` | 项目详情与统计（id/前缀/名称均可解析） | 可用 |
| `get_ledger` | `ref` | 事实台账文本（截断至 16000 字符） | 可用 |
| `run_check` | `ref` | 执行规则核对，返回 issues 与 summary | 拒绝 |
| `export_document` | `ref`, `format` | 导出 `docx`/`md`/`xlsx` 到项目 `output/`，返回路径；`pdf` 不支持 | 拒绝 |
| `search_knowledge` | `query`, `limit?` | 知识库检索（limit 1–10，片段截断 800 字符） | 可用 |

工具结果统一为 MCP `content: [{type: "text", text: <JSON 字符串>}]`。
业务失败（项目不存在、无正文、只读拒绝等）返回 `isError: true` 与中文说明，
不会中断服务进程。

## 协议行为

- `initialize` → 返回 serverInfo（`bidcraft-mcp` + 版本）与 capabilities（tools）。
- `notifications/initialized` 等通知：无响应。
- `ping` → 空结果。
- `tools/list` → 6 个工具及 inputSchema。
- `tools/call` → 执行工具；未知工具返回 `isError`。
- 未知 method → `-32601`；坏 JSON → `-32700`；非对象消息 → `-32600`；
  内部异常兜底 `-32603`。

## 验证

`tests/test_mcp.py` 覆盖协议握手、工具清单、各工具成功/失败路径、只读门禁、
以及真实子进程 stdio 冒烟（initialize + tools/list）。
