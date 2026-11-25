# G0/G1 基线与远端 CI 报告（RC v0.31.3）

## 1. 基线冻结结果

| 项目 | 实测值 | 结论 |
|---|---|---|
| Branch | `fix/v0.31.3-production-readiness` | 与目标一致 |
| Full SHA | `978497247f033e79071a7b3e6692f712f58f04cc`（short `9784972`） | 与目标一致 |
| 工作区 | `git status --short` 为空；无未跟踪文件 | 通过 |
| 后端版本 | `0.31.3` | 通过 |
| Web 版本 | `0.31.3`（package.json / lock） | 通过 |
| 依赖锁 | `uv.lock` 与 pyproject 版本一致；CI 使用 `--frozen` | 通过 |
| Alembic | 单 head `0012_generation_lease`，历史迁移 0001～0012 未改 | 通过 |
| Docker 配置 | `deploy/docker-compose.yml` image `bidcraft:0.31.3`，无 `env_file`，live/ready 探针与资源限制齐全 | 静态通过 |
| PostgreSQL（本地） | 系统无 `psql` / `pg_dump` / Docker；阶段 1～6 使用 pgserver PostgreSQL 16.2 完成真实 PG 验证 | 环境待 G2 确认 |
| Release 文档 | `docs/release-notes/v0.31.3.md`、CHANGELOG、README 表均存在且一致 | 通过 |
| Python | 3.13.5（项目 venv），SQLAlchemy 2.1.8，psycopg 3.3.9 | 通过 |
| Node / npm | v26.13.1 / 11.20.0 | 通过 |

**结论：G0 基线通过。** 实际 HEAD 与目标短 SHA `9784972` 一致，未执行任何切换、回退或重置。

## 2. 远端 CI 取证

远端起初不存在 `fix/v0.31.3-production-readiness` 分支，GitHub API `actions/runs?head_sha=9784972...` 返回 `total_count=0`。为取得**目标 SHA 本身**的 CI 证据（不允许用旧 SHA 代替），已将当前 RC 分支推送到 origin（非 main、非 force、非 tag）：

```
git push -u origin fix/v0.31.3-production-readiness
```

触发的 CI：

- Run ID：`45558628252`
- Run URL：https://github.com/yaoyongli1974-dotcom/BidCraft/actions/runs/45558628252
- Head SHA：`978497247f033e79071a7b3e6692f712f58f04cc`
- Head branch：`fix/v0.31.3-production-readiness`
- 运行结果：**overall failure**（详见第 3 节）

CI 完成后已在本报告第 3 节记录每个 job 的结论与关键步骤证据。

## 3. CI 完整运行结果（目标 SHA 9784972）

| Job | 结论 |
|---|---|
| python (lint + static checks) | SUCCESS |
| python (pytest + regression) | SUCCESS |
| web (lint + test + build) | SUCCESS |
| postgres (real integration) | SUCCESS |
| docx (CJK render + fidelity) | SUCCESS |
| docker (production image + compose hardening) — image build / invariants / static hardening | SUCCESS |
| docker — `Compose real parse and live probe smoke` | **FAILED** |
| security (dependency audit / SAST / secret scan) | SUCCESS |

P0 失败证据：容器以 `BIDCRAFT_ENV=prod` 启动，但未配置模型，生产启动校验拒绝启动：

```
RuntimeError: 启动配置校验失败：生产环境未配置模型（BIDCRAFT_MODEL_BASE_URL / BIDCRAFT_MODEL_NAME）
```

该失败是 CI 冒烟脚本配置缺口，不是应用代码缺陷：`/health/live` 不访问模型，仅要求进程可启动。按任务书 P0 规则，正式验收在此暂停并报批；用户已批准最小 CI 修复。

批准的修复：

- `.github/workflows/ci.yml`：冒烟容器注入不可达的假模型配置 `BIDCRAFT_MODEL_BASE_URL=http://127.0.0.1:9/v1`、`BIDCRAFT_MODEL_NAME=rc-smoke-model`，不触发任何真实模型调用。
- `tests/test_v030_stage8_deploy.py`：静态断言这两个 env 存在（增强，未降低断言）。

修复后需要以**新 SHA** 重跑 CI；旧 RUN `45558628252` 只作为 P0 失败证据，不能作为 G1 PASS 依据。修复后 CI 结果将在第 4 节记录。

## 4. 修复后 CI 复验

首个修复 SHA `0b3e59ce0699ca4fe9ff03ce089570c676667386` 触发 CI run `45568988988`：8/9 job SUCCESS，`docker (production image + compose hardening)` 仍 FAILED。根因是 YAML `run: |` 中把说明注释放在 `docker run \` 的续行参数之间，shell 把后续行当作注释吞掉，导致 `docker run` 缺少镜像参数（`docker: 'docker run' requires at least 1 argument`）。已将注释移到 `docker run` 之前，env 注入本身保持不变；这是同一获批 CI 冒烟修复的脚本位置修正。再次复验的 SHA 与 CI run 待回填。
