# BidCraft 生产部署指南（1Panel + OpenResty）

架构：`公网 → OpenResty(80/443, 1Panel 管理) → 127.0.0.1:8000 → bidcraft 容器`
数据持久化在宿主机 `/opt/bidcraft/data`（容器内 `/data`，uid 10001）。

## 1. 镜像

```bash
docker build -t bidcraft:0.5.0 .          # 构建机
docker save bidcraft:0.5.0 | gzip -1 > bidcraft-0.5.0.tar.gz
# 传到服务器后：
docker load -i bidcraft-0.5.0.tar.gz
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

- 健康检查：`curl http://127.0.0.1:8000/health`
- 日志：`docker logs bidcraft`；站点访问日志在 1Panel 网站目录 `log/`
- 备份：`/opt/bidcraft/data`（业务数据）+ `/opt/bidcraft/compose/.env`（管理员密码）
- 升级：构建新镜像 → load → 改 compose 的 image 标签 → `docker compose up -d`
- 重启持久化：docker/1panel 服务均 enabled；容器 restart=unless-stopped/always