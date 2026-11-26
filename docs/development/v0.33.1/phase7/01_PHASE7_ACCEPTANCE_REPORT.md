# v0.33.1 Phase 7 验收报告：API / 前端产品闭环

## 1. 结论

本阶段无新增 API 路由、无新增前端功能。Phase 1/4 的接口与 UI 变更通过现有契约测试与完整 Web 验证；没有用 mock-only 正向路径替代真实后端行为。

## 2. 范围与真实文件

| 能力 | 实际文件 | 状态 |
|---|---|---|
| 审批待办解释 | `bidcraft/boq.py::pending_for_user`、`bidcraft/commercial.py::pending_for_user`、`web/src/api/shared.ts::normalizePendingApproval`、`web/src/components/PendingApprovals.tsx` | Phase 1 已改；`apiSurface/outboundContract/pendingApproval` 测试通过 |
| `vector_ready` 口径 | `bidcraft/knowledge.py::_vector_fields`、`web/src/lib/format.ts`、`AdminKnowledgeTab.tsx` | Phase 1 已改；`tests/test_v0331_vector_readiness.py` 通过 |
| 工具轨迹脱敏 | `bidcraft/security.py`、`generation/tool_loop.py` | Phase 4 已改；后端真实测试通过 |
| 经验策略审计 | `bidcraft/api/routes/projects.py::update_project` | Phase 4 已改；真实 TestClient + AuditLog 断言通过 |
| 接口面 | `tests/test_api_package.py`、`tests/test_batch8_contract.py`、`tests/test_v030_stage8_observability.py` | 275 routes / 94 Pydantic models / 20 targeted passed |

未新增路由、schema、迁移或前端依赖。

## 3. 验证命令与结果

```bash
cd web && npm run typecheck && npm run lint && npm test -- --run && npm run build
# 58 files / 454 tests passed；Vite 8 build 902ms
# ErrorBoundary.test.tsx 的堆栈是预期渲染异常测试输出，不是失败

.venv/bin/python -m pytest -o addopts= -q \
  tests/test_api_package.py tests/test_batch8_contract.py \
  tests/test_v030_stage8_observability.py
# 20 passed, 1 warning in 4.10s
```

## 4. 产品边界

- 无真实模型端点，生成/评测质量不宣称 PASS。
- 无真实招标件、qrels、专家评分，相关 UI 仅展示 `未测量`/DATA BLOCKED，不把合成样例当真实效果。
- 导出硬门禁未放宽；force 原因与 chapter confirmation 未改变。

## 5. 回滚

无本阶段业务代码变更；撤销本报告即可。Phase 1/4 的回滚按各阶段报告执行。
