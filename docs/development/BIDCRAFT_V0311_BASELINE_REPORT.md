# BidCraft v0.31.1 阶段 0：并发可靠性热修复基线报告

## 1. 仓库与工作区

| 项目 | 实际值 |
|---|---|
| 指定基线 HEAD | `a1c9054dcd6b04a64b52e64808d07fce50fa1665` |
| 实际 HEAD | 与指定基线一致（`a1c9054`） |
| 基线分支 | `main`（干净，跟踪 `origin/main`） |
| 本轮工作分支 | `fix/v0.31.1-concurrency`（从基线新建，未推送） |
| 工作区 | 创建分支前 `git status --short` 为空；无用户未提交修改 |
| 已有分支 | `quality/v0.30.0-reliability`、`quality/v0.31.0-reliability`；本轮未删除或覆盖 |
| 标签 | `v0.31.0` 指向 `a1c9054`；无 `v0.31.1` |
| 远端 | `git@github.com:yaoyongli1974-dotcom/BidCraft.git` |

未执行 `reset --hard` / `clean -fd`，未覆盖已有工作。

## 2. 版本与环境

| 项目 | 实际值 |
|---|---|
| 后端版本 | `0.31.0`（`bidcraft/__init__.py:3`、`pyproject.toml:3`） |
| 前端版本 | `0.31.0`（`web/package.json:4`、`package-lock.json:3/9`） |
| Python | 3.13.5 |
| SQLite | 3.46.1 |
| SQLAlchemy | 2.1.8 |
| Node / npm | v26.13.1 / 11.20.0 |
| Alembic | 12 个 revision，单 head `0012_generation_lease` |
| PostgreSQL | 本机无 `psql`/Docker；CI 使用 `postgres:16-alpine` 做真实集成测试 |
| CI | main @ `a1c9054` 最近 3 次运行均 `completed/success`（例：45438578400） |

## 3. 基线测试

| 检查 | 命令 | 结果 |
|---|---|---|
| Web 依赖 | `cd web && npm ci --no-audit --no-fund` | 通过 |
| Web 类型 | `npm run typecheck` | 通过 |
| Web Lint | `npm run lint` | 通过 |
| Web 单测 | `npm test -- --run` | **39 files / 320 tests passed** |
| Web 构建 | `npm run build` | 通过（Vite 8） |
| Python 全量 | `.venv/bin/python -m pytest -o addopts="" -q` | **1669 passed / 18 skipped / 1 warning in 741.38s**，零失败（`/tmp/opencode/v0311_baseline_full.out`） |

## 4. 关键调用链现状

- **GenerationLease**：`jobqueue.acquire_generation_lease()` 用 SAVEPOINT + `IntegrityError` 实现 SQLite/PG 可移植互斥；`pending_charge_heartbeat()` 同时续 PendingCharge 与 GenerationLease；`assert_generation_lease()` 在 `before_chapter()` 校验；终态 `commit_fenced_result()` 保证 fencing UPDATE 与业务写入同事务。
- **Reaper**：`transition_job()` 用一条条件 UPDATE 实现 `VALID_TRANSITIONS`；`_requeue()` 返回 `requeued|failed|lost`；`reap_expired()`/`recover_on_startup()` 扫描过期 running Job。
- **章节写入**：writer 在长 Session 内逐章生成并 `flush`，`done_steps` 以 `chapter:<node_id>` 记账；当前**没有**每章独立的 fencing 事务，旧 Worker 在模型调用后仍可能写入章节。
- **账务**：同步与异步生成都在扣费同事务建 PendingCharge marker；异步 marker note 关联 Job id；退款按 `done_steps` 章节数；`refund_claimed` 条件 UPDATE 幂等。

## 5. 本轮已知问题（来自任务书与基线核对）

| 编号 | 级别 | 问题 |
|---|---|---|
| V0311-01 | P0 | `pending_charge_heartbeat()` 内部 `touch_generation_lease()` 调用参数不匹配，可能 TypeError 且当前只记日志 |
| V0311-02 | P1 | 达到最大重试次数时，Reaper 与 Worker 续租存在 TOCTOU，可能误杀活跃任务 |
| V0311-03 | P0 | 章节写入缺少独立事务级 fencing，旧 Worker 可写 Section/OutlineNode/done_steps |
| V0311-04 | P1 | Worker 动态可靠性测试不足，现有矩阵以静态源码检查为主 |
| V0311-05 | P2 | `verdict_agreement` 尚未真实计算，需实现或明确未测量 |
| V0311-06 | P2 | 版本与发布文档需统一；v0.31.0 实为已合并/已打 tag/已发 Release |
| V0311-07 | P1 | 真实 PostgreSQL 并发验证需 opt-in CI；本地环境不提供 PG/Docker |
| V0311-08 | P1 | 账务与租约竞争场景需补充：取消、旧 Worker、子账号删除、重复退款 |

## 6. 本轮修改范围

按阶段顺序：0 基线 → 1 心跳 → 2 Reaper 竞争 → 3 章节 fencing → 4 Worker 动态矩阵 → 5 账务一致性 → 6 判定一致性指标 → 7 发布文档 → 8 全量验收。

严格约束：不删测试、不降低断言、不改历史迁移 0001～0012、不把模型调用放进长事务、不推送 main、不创建 Release、不部署生产。涉及新增唯一约束或不可逆迁移先出风险报告。
