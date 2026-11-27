# BidCraft 本地测试环境（v0.33.1）

本环境用于本地复现验收，所有状态隔离在 `~/.local/share/bidcraft-local-test/`，不修改生产目录、不使用真实客户数据、不连接生产数据库。该目录在重启后仍保留，避免 `/tmp` 清理导致测试数据与管理员密码丢失。

## 1. 一键准备

```bash
cd /home/yao/project
chmod +x scripts/local_test_env.sh
./scripts/local_test_env.sh init
```

`init` 会：

- 若 `.venv` 不存在，用 `uv sync --frozen --extra dev --extra api --extra postgres` 创建；
- 若 `web/node_modules` 不存在，执行 `npm ci`；
- 创建隔离 HOME：`~/.local/share/bidcraft-local-test/home`；
- 生成 `~/.local/share/bidcraft-local-test/env`（权限 `600`），包含随机管理员密码、离线模型占位配置；
- 若发现旧 `/tmp/opencode/bidcraft-local-test*.` 文件，会迁移到持久目录；
- 不输出密码内容。

## 2. 运行测试

```bash
# 后端全量（默认约 16 分钟）
./scripts/local_test_env.sh backend

# 后端快速核心回归
./scripts/local_test_env.sh backend-quick

# 前端 typecheck / lint / test / build
./scripts/local_test_env.sh web

# 一次跑完 init + 后端全量 + 前端全量
./scripts/local_test_env.sh all
```

也可直接传 pytest 参数：

```bash
./scripts/local_test_env.sh backend tests/test_api_package.py -q
```

## 3. 可选：真实 PostgreSQL

本机没有系统 `psql/docker`。`pg-start` 会在 `~/.local/share/bidcraft-local-test/pgvenv` 中自动创建 Python 3.12 虚拟环境并安装 `pgserver`，然后启动隔离 PostgreSQL 16.2：

```bash
./scripts/local_test_env.sh pg-start
./scripts/local_test_env.sh pg-test
./scripts/local_test_env.sh pg-stop
```

- PG 数据目录：`~/.local/share/bidcraft-local-test/pgdata`
- 测试库：`bidcraft_local_test`
- URI 文件：`~/.local/share/bidcraft-local-test/pg-uri.txt`（权限 `600`）
- `pg-test` 运行 CI 对应的 PostgreSQL 套件，并设置 `BIDCRAFT_TEST_PG_URL`

这是真实 PostgreSQL，但不是生产等价环境；生产 PgBouncer / 连接池 / 权限模型仍属于 NOT VERIFIED。

## 4. 本地服务冒烟

```bash
./scripts/local_test_env.sh serve
```

默认离线模型配置（env 文件含 `BIDCRAFT_OFFLINE=1` 和不可达的假模型地址），仅用于页面/接口连通性冒烟。后台即使配置了模型，`resolve_chat_endpoint()` 在 offline 下也会直接返回 None，因此不会调用。要改用后台已配置的真实模型：

```bash
./scripts/local_test_env.sh model-on
./scripts/local_test_env.sh serve
```

`model-on` 只移除 env 文件中的 offline/假模型变量，不改后台模型配置；`model-off` 可恢复测试离线口径。启动后可访问：

- `/health/live`
- `/health/ready`
- 管理登录：用户名 `admin`，密码读取自 `~/.local/share/bidcraft-local-test/env`，不要在报告或聊天中回显。

## 5. 预期结果与跳过项

- 后端基线：`2426 passed / 1008 skipped / 1 warning`（仅 Starlette/httpx deprecation）。
- Web：`67 files / 487 tests`，typecheck/lint/build 通过；`ErrorBoundary.test.tsx` 的堆栈输出是预期日志。
- PostgreSQL：`1022 passed / 1 skipped`（通过 `pg-test`）。
- 跳过项主要是：opt-in PostgreSQL、无 LibreOffice/pdfinfo 的 DOCX 渲染 golden、真实数据门禁。跳过不表示通过。

## 6. 真实数据门禁

以下目录当前只有示例与说明文件，没有真实数据：

- `tests/fixtures/tender_real/`
- `tests/fixtures/retrieval_real/`
- `tests/fixtures/expert_ratings_real/`

未提供真实招标文件 / qrels / 专家评分时，相关测试显式 DATA BLOCKED，不会使用合成件替代真实验收。

## 7. 清理

本环境只写入 `~/.local/share/bidcraft-local-test/` 下的隔离目录。需要清理时，请手动删除该目录；脚本不提供破坏性清理命令，避免误删业务数据。
