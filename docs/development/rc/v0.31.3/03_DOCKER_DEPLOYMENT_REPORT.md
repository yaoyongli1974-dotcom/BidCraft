# G3 Docker 部署与运行报告（v0.31.3）

## 1. 本地执行结果

本地没有 `docker` / `docker-compose` CLI，也没有可用 sudo，因此**未在本机拉取镜像、启动容器或执行 `docker compose up`**。未执行任何危险 Docker 操作（包括 `compose down -v`）。

结论：本地 Docker 部署与运行时健康检查为 **D NOT VERIFIED**。不得把 CI 中的容器冒烟写成“本地 RC 已部署”。

## 2. CI 容器证据（真实执行，非本地）

G1 最终 CI：

- Run：`45570895467`
- SHA：`64ab219b5cb61110acece55f4d28718a4ffd7a03`
- Job：`docker (production image + compose hardening)` SUCCESS
- Job URL：https://github.com/yaoyongli1974-dotcom/BidCraft/actions/runs/45570895467/job/139056979532

该 job 实际执行：

1. `docker build -t bidcraft:v0313-check .`；
2. `docker inspect` 校验非 root 用户、HEALTHCHECK、pg_dump、CLI 可执行；
3. `docker compose -f deploy/docker-compose.yml config -q` 静态解析；
4. 启动 `bc-stage6-health` 容器，轮询容器健康状态并请求 `http://127.0.0.1:18000/health/live`，失败时打印日志；
5. 清理临时容器。

## 3. Compose 配置

`deploy/docker-compose.yml`（静态确认）：

- image：`bidcraft:0.31.3`
- 无 `env_file`
- `stop_grace_period >= 45s`
- `cpus >= 2.0`、`pids_limit >= 256`
- `cap_drop: ALL`、`no-new-privileges:true`
- liveness `/health/live`、readiness `/health/ready`
- 必需生产 env key 显式透传（不包含真实值）

## 4. 残留风险

- CI 使用 GitHub Actions runner 的 Docker，不是用户 RC 主机；网络、存储卷、代理与系统字体可能与目标环境不同。
- 本地无法验证镜像在 RC 主机上的实际启动延迟、卷挂载权限与备份目录可写性。
- 必须在具备 Docker 的 RC 主机上按部署手册执行一次真实 `docker compose up -d` 与 `/health/live`、`/health/ready` 探测后，才能把 G3 升级为 PASS。
