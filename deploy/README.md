# BidCraft 生产部署指南（1Panel + OpenResty）

架构：`公网 → OpenResty(80/443, 1Panel 管理) → 127.0.0.1:8000 → bidcraft 容器`
数据持久化在宿主机 `/opt/bidcraft/data`（容器内 `/data`，uid 10001）。

## 1. 镜像

```bash
docker build -t bidcraft:0.6.5 .          # 构建机
docker save bidcraft:0.6.5 | gzip -1 > bidcraft-0.6.5.tar.gz
# 传到服务器后：
docker load -i bidcraft-0.6.5.tar.gz
```

## 2. 数据目录与配置

```bash
sudo mkdir -p /opt/bidcraft/data /opt/bidcraft/compose
sudo chown -R 10001:10001 /opt/bidcraft/data
sudo cp deploy/docker-compose.yml /opt/bidcraft/compose/
# 参考 .env.example 创建 /opt/bidcraft/compose/.env（chmod 600）：
#   BIDCRAFT_ADMIN_PASSWORD=<强密码>   首次启动创建 admin，之后改它不会重置密码
#   BIDCRAFT_SECURE_COOKIE=1           仅 HTTP 访问时临时设 0（见下）
cd /opt/bidcraft/compose && sudo docker compose up -d
```

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

- 健康检查：`curl http://127.0.0.1:8000/health`（公开，只返回最小字段）
- 版本与运行状态（超级管理员）：登录后带 Cookie 访问 `/admin/health`，可看到版本、启动时间与
  uptime、Python/平台、DB 大小与磁盘余量、模型/知识库/用户计数、`docs_enabled` 等开关：

  ```bash
  curl -s -c /tmp/bc.cookie -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"<密码>"}' http://127.0.0.1:8000/auth/login
  curl -s -b /tmp/bc.cookie http://127.0.0.1:8000/admin/health
  rm -f /tmp/bc.cookie
  ```
- 日志：`docker logs bidcraft`；站点访问日志在 1Panel 网站目录 `log/`
- 备份：`/opt/bidcraft/data`（业务数据）+ `/opt/bidcraft/compose/.env`（管理员密码）
- 升级：构建新镜像 → load → 改 compose 的 image 标签 → `docker compose up -d`
- 重启持久化：docker/1panel 服务均 enabled；容器 restart=unless-stopped/always
## 6. 上线前的暴露面收紧（v0.6.3+ 内置，0.6.5 增补 `/admin/health`）

| 项 | 默认行为 | 说明 |
|---|---|---|
| `/docs`、`/redoc`、`/openapi.json` | **404** | 由 `BIDCRAFT_DOCS_ENABLED=0` 控制；仅排障时临时置 1 并重启 |
| `/health` | 最小字段 | 只返回 `status` 与前端必需字段，不含版本、绝对路径、库内计数；Docker HEALTHCHECK 仍可用 |
| `/admin/health` | 超级管理员 | 完整运行状态（版本、uptime、Python/平台、DB 大小与磁盘余量、模型/知识库/用户计数、开关项）；匿名 401、普通成员 403，不会落到公开探活里 |
| 安全响应头 | 全站注入 | `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、CSP（`default-src 'self'`、`frame-ancestors 'none'`、`object-src 'none'`）、HTTPS 下加 HSTS |

`.env` 追加：

```bash
BIDCRAFT_DOCS_ENABLED=0
# 私有化部署排查问题时可临时设 1，用完改回
```

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

## 8. 升级到最新版本（示例：→ 0.6.5）

线上 0.4.8 缺少审批条件分支、VIP/子账号/优先级队列、模板库等本轮能力。步骤：

```bash
cd /opt/bidcraft
docker compose -f compose/docker-compose.yml down          # 可选，改镜像后 up 即可
# 方式 A：服务器上构建
git clone https://github.com/yaoyongli1974-dotcom/BidCraft.git src && cd src
git fetch origin main && git checkout origin/main   # 0.6.5 未打 tag，用 main
docker build -t bidcraft:0.6.5 .
# 方式 B：本地构建后导出/导入
# docker save bidcraft:0.6.5 | gzip > bidcraft-0.6.5.tar.gz   （本地）
# docker load < bidcraft-0.6.5.tar.gz                          （服务器）

sed -i 's/image: bidcraft:.*/image: bidcraft:0.6.5/' /opt/bidcraft/compose/docker-compose.yml
cd /opt/bidcraft/compose && docker compose up -d

sleep 5
curl -s http://127.0.0.1:8000/health     # 最小字段（不显示版本）
# 超管确认版本：带 Cookie 访问 /admin/health，见「5. 运维」
docker exec bidcraft python -c "import bidcraft;print(bidcraft.__version__)"   # 0.6.5
```

升级后 `/opt/bidcraft/data` 不变，数据库增量列由启动时迁移自动补齐；建议先备份该目录。


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
