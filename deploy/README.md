# BidCraft 生产部署指南（1Panel + OpenResty）

架构：`公网 → OpenResty(80/443, 1Panel 管理) → 127.0.0.1:8000 → bidcraft 容器`
数据持久化在宿主机 `/opt/bidcraft/data`（容器内 `/data`，uid 10001）。

## 1. 镜像

```bash
docker build -t bidcraft:0.31.0 .          # 构建机
docker save bidcraft:0.31.0 | gzip -1 > bidcraft-0.31.0.tar.gz
# 传到服务器后：
docker load -i bidcraft-0.31.0.tar.gz
```

## 2. 数据目录与配置

```bash
sudo mkdir -p /opt/bidcraft/data /opt/bidcraft/compose
sudo chown -R 10001:10001 /opt/bidcraft/data
sudo cp deploy/docker-compose.yml /opt/bidcraft/compose/
# 参考 .env.example 创建 /opt/bidcraft/compose/.env（chmod 600）：
#   BIDCRAFT_ADMIN_PASSWORD=<至少 12 位强密码>  首次启动创建 admin，之后改它不会重置密码
#     prod 会拒绝 change-me / admin123 / password 等占位口令，留空也会阻止启动
#   BIDCRAFT_SECURE_COOKIE=1           仅 HTTP 访问时临时设 0（见下）
#   BIDCRAFT_DB_URL=（可选）postgresql://user:pass@host:5432/db   详见 docs/postgresql.md
#   BIDCRAFT_TRUST_PROXY=1             仅在确实位于 1Panel/OpenResty 反代之后开启
cd /opt/bidcraft/compose && sudo docker compose up -d
```

> [!IMPORTANT]
> `deploy/docker-compose.yml` **刻意未启用 `env_file:`**：`.env` 只用于 compose 文件里 `${VAR}` 的
> 宿主机插值，不会整份注入容器。只有 `environment:` 中列出的变量才会进入容器；若要让
> `BIDCRAFT_ENV`、`BIDCRAFT_DB_URL`、`BIDCRAFT_SECRET_KEY` 等变量生效，必须显式加到
> `environment:`（或先 `docker compose --env-file /path/to/.env up -d` 指定插值文件，再在
> `environment:` 引用）。这样升级时原本被忽略的变量不会突然注入，避免非预期的启动校验失败。

## 3. 1Panel 网站（反向代理）

1Panel → 网站 → 创建网站 → 反向代理：
- 域名：`bidcraft.cn`、`www.bidcraft.cn`（DNS A 记录指向服务器公网 IP）
- 代理地址：`http://127.0.0.1:8000`
- 如需 IP 直连访问，把该站点设为「默认站点」。

## 4. HTTPS（域名须已完成 ICP 备案）

国内云（如京东云）对未备案域名按 Host/SNI 拦截 80/443 乃至更多端口，
HTTP-01 验证无法通过。备案完成后：
1. 网站 → 证书 → Let's Encrypt 申请（ACME 账户邮箱随意，HTTP 验证，勾选自动续签）；
2. 网站设置 → HTTPS → 启用，选已签发证书，HTTP 自动跳转 HTTPS；
3. 将 `.env` 的 `BIDCRAFT_SECURE_COOKIE` 改回 `1` 并 `docker compose up -d`。

**备案过渡期（仅 HTTP/IP 访问）**：`BIDCRAFT_SECURE_COOKIE=0`，
否则会话 Cookie 带 Secure 标记、浏览器在 HTTP 下不回传，导致无法登录。
此阶段无 TLS，建议仅限临时使用。

## 5. 运维

- 健康检查（三类端点，用途不同）：
  - `curl http://127.0.0.1:8000/health/live`：**存活探针**，只确认进程在监听，不查数据库。
    Dockerfile 的 `HEALTHCHECK` 与 compose 的原生 `healthcheck` 都用它；DB 抖动不会导致容器被重启。
  - `curl http://127.0.0.1:8000/health/ready`：**就绪探针**，检查 DB 等启动依赖，仅用于判断
    是否接入流量，不用于触发重启。**未配置模型时该端点可能返回非 200**（例如全新部署尚未在
    `/admin` 配置模型），此时服务仍可通过 `/health/live`，不必强行让容器重启或把流量切走。
  - `curl http://127.0.0.1:8000/health`：**遗留兼容端点**，前端仍依赖其字段，保留公开最小字段
    返回；不再作为容器/编排探活路径。
  - compose 另有 `x-liveness-probe`（`/health/live`，start 20s / interval 30s / timeout 5s /
    failure 3）与 `x-readiness-probe`（`/health/ready`，start 20s / interval 10s / timeout 5s /
    failure 3）扩展块，供 K8s / 反代 / 监控按同样路径配置；Docker Compose 只执行原生 healthcheck。
- 版本与运行状态（超级管理员）：登录后带 Cookie 访问 `/admin/health`，可看到版本、启动时间与
  uptime、Python/平台、DB 大小与磁盘余量、模型/知识库/用户计数、`docs_enabled` 等开关：

  ```bash
  curl -s -c /tmp/bc.cookie -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"<密码>"}' http://127.0.0.1:8000/auth/login
  curl -s -b /tmp/bc.cookie http://127.0.0.1:8000/admin/health
  rm -f /tmp/bc.cookie
  ```
- 日志：`docker logs bidcraft`（应用日志为 `时间 级别 logger [req=请求ID] 消息`，
  可用 `BIDCRAFT_LOG_FORMAT=json` 切成单行 JSON 便于采集；级别由 `BIDCRAFT_LOG_LEVEL`
  控制）。后台任务日志的 logger 名为 `bidcraft.workers`，可用它过滤任务执行记录；
  uvicorn 访问日志也带 `req=<请求ID>`，响应回显 `X-Request-ID`，用它把前端报错、
  访问日志与应用日志对上。
- 备份：`/opt/bidcraft/data`（业务数据）+ `/opt/bidcraft/compose/.env`（管理员密码）。
  **迁移、大版本升级与重启前必须先 `bidcraft backup` 再 `bidcraft backup-verify`**（见 5.2 / 第 8 节），
  不要直接 `cp` 运行中的 SQLite 文件（WAL 下主库与 `-wal`/`-shm` 可能撕裂）。
- 资源与隔离：compose 限制 `mem_limit: 4g`、`cpus: "2.0"`、`pids_limit: 256`；
  `cap_drop: [ALL]` + `security_opt: ["no-new-privileges:true"]`，容器以固定 uid 10001 的非 root
  用户运行；`stop_grace_period: 45s` 大于应用 30s 优雅退出窗口，避免 SIGKILL 截断在途任务。
- 升级：先备份并校验 → 构建新镜像 → load → 改 compose 的 image 标签 → `docker compose up -d`
- 重启持久化：docker/1panel 服务均 enabled；容器 restart=unless-stopped/always

### 5.1 API 与 Worker 分离部署（可选，v0.12.0+）

默认镜像入口是单进程（`bidcraft serve`，API + worker 同进程），适合小规模使用。
需要让 API 可独立重启/扩容时，把两者拆成两个进程组，共用同一个 `BIDCRAFT_HOME` 数据卷。

> [!WARNING]
> **SQLite 绝不能配 `--workers 4` 或多实例。** 默认 `BIDCRAFT_DB_URL` 是 SQLite，
> 数据库层是**单写者**（WAL 只放宽读写并发，写事务仍串行且只有 30s busy timeout）。
> 多 worker/多容器共享 SQLite 文件会出现 `database is locked`、写丢失与迁移竞争。
> **只要 `--workers > 1` 或起了多个 API/Worker 容器，就必须在 `.env` 配置
> `BIDCRAFT_DB_URL` 指向同一个 PostgreSQL 实例**（详见 `docs/postgresql.md`）。
> 单机单进程部署继续用默认 SQLite 即可，无需改动。

```bash
# API 进程：只入队，不执行任务、不做启动恢复
# 注意：下面 --workers 4 只在 BIDCRAFT_DB_URL=postgresql://... 时允许！
BIDCRAFT_EMBEDDED_WORKERS=0 uvicorn bidcraft.api:app --host 0.0.0.0 --port 8000 --workers 4

# Worker 进程：认领并执行任务（可起多个，各自原子认领）
bidcraft worker
```

要点：

- API 与 Worker 必须指向**同一个数据目录**（同一 SQLite 文件），否则互相看不到 Job。
- 任务恢复只在 Worker 入口执行；多个 Worker 同时启动只会重排**已过期租约**，不会误伤
  其他 Worker 正在跑的任务。
- `bidcraft worker` 常驻运行，Ctrl-C / SIGTERM 退出；运行中任务靠租约超时由其他 Worker 回收。
- 仍不需要 Redis；Worker 发现新任务有最多 5 秒的轮询延迟。
- PostgreSQL 可选（v0.18.0+）：多机部署时在 `.env` 设置 `BIDCRAFT_DB_URL`，
  所有进程指向同一个 PG 库，不再要求共享文件卷；详见 `docs/postgresql.md`。
- **多实例必须在所有进程共用同一把 Fernet 主密钥**：要么共享挂载同一个
  `BIDCRAFT_HOME/secret.key`（0600），要么统一设置 `BIDCRAFT_SECRET_KEY`
  （32 字节密钥的 base64）。否则 A 实例加密的 API Key、微信配置或签名 state
  在 B 实例上无法解密，表现为"密钥丢失/微信回调校验失败"。
- 继续使用单进程镜像时无需任何改动（默认 `BIDCRAFT_EMBEDDED_WORKERS=1`）。

### 5.2 备份与恢复演练（必做）

除了整目录备份，系统内置一致性备份命令（不要直接 `cp` 运行中的 SQLite 文件）：

```bash
bidcraft backup                    # 生成 BIDCRAFT_HOME/backups/bidcraft-<时间>.zip
bidcraft backup --list             # 列出已有备份
bidcraft backup --include-dir mydata  # 临时追加一个数据目录（相对 BIDCRAFT_HOME，可重复）
bidcraft backup-verify <备份.zip>  # 校验完整性（DB + 清单里的全部数据文件）
```

PostgreSQL 备份走 `pg_dump`（需安装 `postgresql-client`），SQLite 走在线备份 API；
zip 内含 `secret.key`（存在时）、`manifest.json` 以及数据目录中的文件。默认打包
`projects/`、`packages/`、`output/`、`docs/`（相对 `BIDCRAFT_HOME`），可用
`BIDCRAFT_BACKUP_DIRS` 调整（逗号分隔，空串表示只备份数据库与密钥）。`manifest.json`
的 `data_files` 逐项记录路径、大小与 sha256；`backup-verify` 除 SQLite
integrity_check / PG 建表与数据行外，还会确认这些文件在包内且哈希一致，避免
"数据库完好但项目文件全丢"仍报通过。没有 `data_files` 的旧备份继续按原逻辑校验。
恢复步骤：**停服 → 解压 → 把 `bidcraft.db`（或 PG 的 `dump.sql`）、`secret.key`
与各数据目录放回 `BIDCRAFT_HOME` → 启动**。命令不会自动覆盖线上数据。

**恢复演练**：每季度（或每次大版本升级前）在独立的临时目录/临时 PG 库上做一次
完整 restore + `bidcraft backup-verify` + 登录与一条项目冒烟检查，记录耗时与结果。
没演练过的备份不算备份。详见 `docs/architecture.md`「备份与恢复」与 `docs/postgresql.md`。

## 6. 上线前的暴露面收紧（v0.6.3+ 内置，0.7.0 增补 `/admin/health`）

| 项 | 默认行为 | 说明 |
|---|---|---|
| `/docs`、`/redoc`、`/openapi.json` | **404** | 由 `BIDCRAFT_DOCS_ENABLED=0` 控制；仅排障时临时置 1 并重启 |
| `/health/live` | 存活探针 | 不查数据库，Docker HEALTHCHECK / compose `healthcheck` 使用；DB 抖动不会触发重启 |
| `/health/ready` | 就绪探针 | 检查启动依赖，仅决定是否接入流量；模型未配置时可能失败，不用于触发容器重启 |
| `/health` | 遗留端点（最小字段） | 只返回 `status` 与前端必需字段，不含版本、绝对路径、库内计数；前端/人工排障保留，不再作探活路径 |
| `/admin/health` | 超级管理员 | 完整运行状态（版本、uptime、Python/平台、DB 大小与磁盘余量、模型/知识库/用户计数、开关项）；`global_metrics` 给出全实例共享的库内聚合（各状态项目/任务数、未处置质量问题、用户数），多 worker/多实例看到同一份数据；匿名 401、普通成员 403，不会落到公开探活里 |
| 安全响应头 | 全站注入 | `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、CSP（`default-src 'self'`、`frame-ancestors 'none'`、`object-src 'none'`）、`Permissions-Policy`、`Cross-Origin-Opener-Policy: same-origin`、`Cross-Origin-Resource-Policy: same-origin`、HTTPS 下加 HSTS；每响应带回显的 `X-Request-ID` |
| CSRF 纵深防御 | 带 Cookie 的状态变更请求 | `POST/PUT/PATCH/DELETE` 且带 Cookie 时：显式 `Origin` 必须同源或命中 `BIDCRAFT_PUBLIC_ORIGIN`；`Sec-Fetch-Site` 为 `cross-site`/`same-site` 一律 403；不带这两个头的普通脚本/curl 保持兼容。仅在 `BIDCRAFT_TRUST_PROXY=1` 时采信 `X-Forwarded-Proto/Host`。不要依赖它替代反代层 CSRF/WAF |
| 敏感端点限流 | 进程内滑动窗口 | 登录（用户名维度 8 次/5min 之外）再按 IP + 全局限流；注册、改密/重置、兑换码、模型/知识库/插件测试同样限流。默认见下；多 worker 只在反代层补充限流 |

`.env` 追加：

```bash
BIDCRAFT_DOCS_ENABLED=0
# 私有化部署排查问题时可临时设 1，用完改回
BIDCRAFT_RATE_LIMIT=1
BIDCRAFT_RATE_WINDOW_SECONDS=300
BIDCRAFT_RATE_LOGIN_IP=60          # 登录：每 IP
BIDCRAFT_RATE_LOGIN_GLOBAL=1000    # 登录：每进程
BIDCRAFT_RATE_SENSITIVE_IP=30      # 注册/改密/兑换/模型测试：每 IP
BIDCRAFT_RATE_SENSITIVE_GLOBAL=300
BIDCRAFT_TRUST_PROXY=1             # 仅当确实位于反代之后；用 X-Forwarded-For 记真实 IP
BIDCRAFT_PUBLIC_ORIGIN=https://bid.example.com,https://admin.example.com  # 可选：CSRF 额外允许的 Origin
BIDCRAFT_BACKUP_DIRS=projects,packages,output,docs  # 内置 backup 打包的数据目录
BIDCRAFT_LOG_LEVEL=INFO            # text | json 由 BIDCRAFT_LOG_FORMAT 控制
```

限流是**进程内**计数器：重启清零、多 worker 各算各的，且只覆盖认证敏感端点；
bucket 按 TTL 过期并设 LRU 硬上限（最多 2048 个 key），不会被伪造 IP 无限撑大。
公网部署仍须在 OpenResty/1Panel 层配置全局速率限制与 WAF：可直接参考
[`deploy/nginx.conf.example`](nginx.conf.example) 中的 `limit_req_zone`（登录、注册、
认证端点兜底）与对应 `location` 片段，按实际域名和上游端口调整后再 `nginx -t`
并 reload。该示例同时说明为何进程内计数器无法替代反代层限流。`BIDCRAFT_TRUST_PROXY`
只在请求确实经过你控制的反代时开启，直连时开启会让客户端伪造 IP 绕过限流。
`bidcraft serve` 会把该开关透传给 uvicorn：关闭时不启用其 ProxyHeadersMiddleware；
若直接用 `uvicorn bidcraft.api:app` 启动，请显式加 `--no-proxy-headers`（否则
uvicorn 默认信任来自 127.0.0.1 的转发头，先于应用中间件改写来源 IP）。

### 6.1 刻意延期项（本版本未实施）

- **基础镜像 digest 固定（P3）**：`node:22-slim` 与 `python:3.13-slim` 仍是浮动标签
  （`ghcr.io/astral-sh/uv:0.12.17` 已按版本固定）。需先在 CI 固化具体 digest、验证跨架构
  构建与升级流程后再启用，避免本阶段引入非预期构建失败。
- **容器 `read_only: true`**：`/data` 卷与 LibreOffice 运行时目录需要写入，尚未梳理完可写
  白名单，因此暂不启用；已用非 root 用户、`cap_drop: [ALL]` 与
  `no-new-privileges:true` 收紧权限。
- **`env_file:` 注入**：刻意不启用（见第 2 节），`.env` 中的变量不会整份进入容器。

## 7. OpenResty：80 只做跳转，并补齐响应头

在 1Panel 网站 → 配置文件 中，HTTP(80) server 块内改为只跳转（替换原有 content 段落）：

```nginx
server {
    listen 80;
    server_name bid.xalcy.cn bidcraft.cn www.bidcraft.cn;

    # 备案完成前如需 HTTP 访问，保留下面一行注释；备案后打开
    return 301 https://$host$request_uri;
}
```

HTTPS(443) server 块建议追加（应用已注入主要头，反代侧再兜一层）：

```nginx
add_header X-Content-Type-Options nosniff always;
add_header X-Frame-Options DENY always;
add_header Referrer-Policy no-referrer always;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
client_max_body_size 60m;   # 个人文档/招标原件 50MB 上限 + 余量
```

验证：

```bash
curl -I https://bid.xalcy.cn/health        # 200，且无 version/home 字段
curl -s -o /dev/null -w '%{http_code}\n' https://bid.xalcy.cn/docs   # 404
curl -I http://bid.xalcy.cn/ | head -3     # 301 → https
```

## 8. 升级到最新版本（示例：→ 0.31.0）

线上 0.4.8 缺少审批条件分支、VIP/子账号/优先级队列、模板库等本轮能力。步骤：

```bash
cd /opt/bidcraft
docker compose -f compose/docker-compose.yml down          # 可选，改镜像后 up 即可
# 方式 A：服务器上构建
git clone https://github.com/yaoyongli1974-dotcom/BidCraft.git src && cd src
git fetch origin --tags && git checkout v0.31.0      # 或 origin/main
docker build -t bidcraft:0.31.0 .
# 方式 B：本地构建后导出/导入
# docker save bidcraft:0.31.0 | gzip > bidcraft-0.31.0.tar.gz   （本地）
# docker load < bidcraft-0.31.0.tar.gz                          （服务器）

sed -i 's/image: bidcraft:.*/image: bidcraft:0.31.0/' /opt/bidcraft/compose/docker-compose.yml

# 必做前置：升级会在启动时执行 Alembic 迁移，先做一致性备份并校验，失败则停止升级。
docker exec bidcraft bidcraft backup
docker exec bidcraft bidcraft backup-verify <上一步输出的 /data/backups/bidcraft-*.zip>
# PostgreSQL 后端镜像已内置 postgresql-client，可确认工具可用：
docker exec bidcraft pg_dump --version

cd /opt/bidcraft/compose && docker compose up -d

sleep 5
curl -s http://127.0.0.1:8000/health/live # 存活（容器健康检查用）
curl -s http://127.0.0.1:8000/health/ready # 就绪（模型未配置时可能非 200）
curl -s http://127.0.0.1:8000/health     # 遗留端点，最小字段（不显示版本）
# 超管确认版本：带 Cookie 访问 /admin/health，见「5. 运维」
docker exec bidcraft python -c "import bidcraft;print(bidcraft.__version__)"   # 0.31.0
```

升级后 `/opt/bidcraft/data` 不变，数据库增量列由启动时迁移自动补齐。**启动迁移前必须先
`bidcraft backup` + `bidcraft backup-verify`**；不要直接 `cp`/`tar` 运行中的数据目录
（SQLite WAL 下可能撕裂），也不要先 `down` 掉容器再临时拷贝数据库文件。


## 9 可选：启用 MinerU 解析

复杂版面或纯图片型招标文件，交给 MinerU 比页图 OCR 质量高一档；BidCraft 只做 HTTP 适配，不内置该模型。

```bash
# 任选一种方式部署 MinerU（示例，镜像与命令参数以其官方文档为准）
docker run -d --name mineru -p 8001:8000 opendatalab/mineru:latest \
  mineru-api --host 0.0.0.0 --port 8000
```

在 BidCraft 侧配置（compose 的 `environment` 或 shell）：

```yaml
BIDCRAFT_MINERU_URL: http://127.0.0.1:8001
BIDCRAFT_MINERU_TIMEOUT: "180"   # 秒，图片型大文件适当加大
BIDCRAFT_MINERU: "1"             # 置 0 可临时停用
```

验证：

```bash
bidcraft mineru-check
bidcraft mineru-check --pdf /path/to/样本.pdf   # 实测解析页数/表格/耗时
```

行为约定：配置了 MinerU 就优先用它；连接失败、超时、HTTP 非 2xx、返回空 Markdown 都会自动回退到内置 `pypdf/pdfplumber + OCR`，并在文档 `meta.mineru_error` 留下原因；页号由 Markdown 估算，引用定位精度略低于原生分页。
