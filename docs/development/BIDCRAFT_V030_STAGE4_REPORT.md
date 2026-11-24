# BidCraft v0.30.0 阶段 4 报告：Worker 租约、Fencing 与幂等

- 分支：`quality/v0.30.0-reliability`
- 前置：阶段 0 `c9b9170`、阶段 1 `d6d0338`、阶段 2 `fc6b913`、阶段 3 `e409371`
- 风险报告：`docs/development/BIDCRAFT_V030_STAGE4_RISK_REPORT.md`
- 用户批准：**决策 A 幂等重算** + **决策 B 其余 worker 统一 abort 检查点**（含 agent 内部 callback，不改变 HTTP 接口）
- 约束遵守：未推送 main、未发版、未改生产、未删测试、未改历史迁移、未降低门禁。

## 1. 问题与修复对照（11 项）

| # | 编号 | 问题 | 修复 | 证据 |
|---|---|---|---|---|
| 1 | F09-1/F10-1 | `fenced_job()` 同一 Session 因 identity map 拿到旧 token，租约失效后仍能写 | 新增 `jobqueue.assert_claim_current()`（新 Session 读 token）；相似度终态写在新 Session | `bidcraft/jobqueue.py`、`bidcraft/workers/similarity.py`、`tests/test_v030_stage4_fencing.py` |
| 2 | F09-2 | similarity 在租约丢失后仍提交报告/命中/审计 | 提交前与终态前双重断言；失效抛 `JobAborted` 回滚，`except JobAborted` 先于通用异常 | `bidcraft/workers/similarity.py` |
| 3 | F09-3 | similarity 重试追加重复命中并覆盖 done 报告（业务规则） | 已批准幂等重算：done 报告直接返回；未完报告先清旧命中再重算 | `bidcraft/similarity/service.py`、`tests/test_v030_stage4_similarity.py` |
| 4 | F09-4/F10-2 | 6/8 worker 缺 abort 检查点 | agent/compliance/evaluation/export/images/knowledge 增加入口、循环边界、写前 `raise_if_aborted()`，终态前 `assert_claim_current()` | 6 个 worker 文件、`tests/test_v030_stage4_fencing.py` |
| 5 | F09-5 | similarity runner 未在 API 命名空间 re-export | `_API_RUNNER_NAMES['similarity']` 与 `api/shared.py` import/`__all__` | `bidcraft/jobqueue.py`、`bidcraft/api/shared.py` |
| 6 | F10-1 | stale worker 可能把 failed 覆盖成 done | 6 worker 的 `except JobAborted` 不改状态；终态写前 fence 复核 | 各 worker |
| 7 | F10-3 | agent 内部无 abort 通道 | `raise_if_aborted()` 在 agent 主循环边界生效，HTTP 接口不变 | `bidcraft/workers/agent.py` |
| 8 | F10-4 | 缺少 runner 恢复安全分类测试 | 新增表驱动/参数化测试覆盖 abort 后不落 failed | `tests/test_v030_stage4_fencing.py` |
| 9 | F10-5 | 缺少中途换 token 回归 | 6 worker + similarity 均有 token 变更/中止后 job 仍 running 的断言 | 两个新测试文件 |
| 10 | 语义保护 | abort 不能误判业务失败、不能触发退款 | `JobAborted` 分支在通用异常之前；不调 `_fail_job`、不发 failed 事件 | 同上 |
| 11 | 幂等保护 | 重试不能产生重复命中或新审计 | done 报告短路 + 旧命中删除；测试断言 audit/hit 数量不变 | `tests/test_v030_stage4_similarity.py` |

## 2. 变更清单

- `bidcraft/jobqueue.py`：`assert_claim_current()`、`_API_RUNNER_NAMES['similarity']`。
- `bidcraft/workers/{agent,compliance,evaluation,export,images,knowledge}.py`：`raise_if_aborted()` 检查点、`assert_claim_current()` 终态复核、`except JobAborted` 先于通用异常。
- `bidcraft/workers/similarity.py`：fresh-session 终态写 + 双重 fence 断言 + JobAborted 分支。
- `bidcraft/similarity/service.py`：done 报告短路 + 未完报告旧命中清理。
- `bidcraft/api/shared.py`：`_run_similarity_job` re-export。
- 新测试：`tests/test_v030_stage4_fencing.py`（16）、`tests/test_v030_stage4_similarity.py`（6）。

## 3. 验证结果

| 项目 | 命令 | 结果 |
|---|---|---|
| Lint | `ruff check bidcraft tests scripts` | All checks passed |
| diff | `git diff --check` | 通过 |
| 定向 | `pytest tests/test_v030_stage4_* tests/test_similarity*.py tests/test_jobqueue.py tests/test_reliability.py tests/test_worker_extraction.py tests/test_batch7_*` | **98 passed**, 1 warning |
| 全量后端 | `pytest -o addopts="" -q` | **1549 passed, 16 skipped**，874.17s，0 failed（`/tmp/opencode/v030_stage4_full.out`） |

## 4. 兼容性与风险

- HTTP 接口、payload 结构、事件 kind 均未变；`refund`/账务逻辑未触及。
- 已完成报告重试不再重算；这是用户批准的业务语义。
- 租约丢失后 job 保持 `running`，由 reaper 重新排队；可能延长单次完成时间但保证最终一致。
- 本地无真实 PG；相关并发语义仍由 CI PG job 验证。

## 5. 回滚

1. `git checkout -- bidcraft/jobqueue.py bidcraft/api/shared.py bidcraft/similarity/service.py bidcraft/workers/*.py`
2. 删除两个 `tests/test_v030_stage4_*.py` 与本报告。
3. 无需数据库回滚（本阶段无迁移/表结构变更）。
