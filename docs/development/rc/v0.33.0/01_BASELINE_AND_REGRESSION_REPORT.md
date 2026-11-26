# RC-00/01 基线与全量回归报告

## 1. 基线信息

- 仓库：`/home/yao/project`
- 原始基线：`main` @ `7fe741dcc7c066e9b77e1269fae3260f7894e389`（v0.32.2）
- RC 分支：`fix/v0.33.0-rc-reliability`，从上述干净基线创建；未回滚、未重置。
- 后端/前端版本：基线均为 `0.32.2`；Alembic 单 head `0015_fact_confirmation`。
- Python 3.13.5；SQLAlchemy 2.1.8；Node 26.13.1；npm 11.20.0。
- 约束：仅开发分支本地提交；未推送 main、未打 tag、未创建 Release、未部署、未改生产数据。

## 2. 基线验证（改动前）

```bash
.venv/bin/python -m pytest -o addopts="" -q
# 2270 passed, 888 skipped, 1 warning in 989.67s

cd web && npm run typecheck && npm run lint && npm test -- --run && npm run build
# typecheck/lint/build 通过；57 files / 434 tests passed
```

`888 skipped` 为仓库既定的 PostgreSQL/可选环境跳过；无失败。

## 3. 审计方式

四个只读审计覆盖：RC-01/02 工具循环与 Agent 安全；RC-03/04 规划一致性与知识资产生命周期；RC-05/12 生成并发、账务与迁移；RC-06..11/13 检索基准、专家校准、经验治理、审批解释、真实件 E2E、过程指标与最终验收。审计只读，不修改代码。

## 4. P0 阶段提交与验证

| 模块 | 本地提交 | 核心验证 |
|---|---|---|
| RC-01 工具结果验证 | `77e1f56` | 26 passed；严格证据/失败语义 |
| RC-02 Agent 安全审计 | `8dd27f5` | 46 passed；扩展 127 passed |
| RC-03 规划依赖一致性 | `241a9bf` | 31 passed；扩展 102 passed |
| RC-04 知识资产生命周期 | `f5c9159` | 11 passed；扩展 95 passed |
| RC-05 生成并发/单一退款 | `591891c` | 9 passed；扩展 99 passed；全量 2324 passed |

## 5. RC-05 后全量回归（最终 P0 基线）

```bash
.venv/bin/python -m pytest -o addopts="" -q
# 2324 passed, 888 skipped, 1 warning in 999.36s
```

零失败；唯一 warning 为 Starlette/httpx TestClient 弃用提示，与业务无关。

## 6. 兼容性与保留项

- 无历史迁移改写；Alembic 仍为单 head `0015_fact_confirmation`。
- 未删除任何测试、未放宽任何断言；为适配新语义只更新了过期断言（保留原验证目标）。
- 无 API 业务语义变更或前端破坏性变更；RC-01..RC-05 均为后端/P0 可靠性修复。
- 真实招标文件、专家评分、真实模型端点、生产数据库/部署仍为数据或环境阻塞项，将在最终报告按 PARTIAL/NOT VERIFIED 口径列出。
