# v0.33.1 Phase 4 验收报告：安全 / 租户 / 权限 / 审计边界

## 1. 结论

已完成本阶段可闭环项；项目↔企业租户绑定仍 DATA BLOCKED，不能用合成数据宣称已解决。

修复并验证：工具结果嵌套 JSON 脱敏、企业知识库显式授权边界、规划检索作用域 fail-closed、KB 管理状态变更审计、经验治理策略细粒度审计。待办收件箱异常成员角色的审计提示经真实 DB 测试未复现，未做无意义代码改动。

## 2. 代码与测试

| 缺口 | 实际文件 | 修复 / 测试 |
|---|---|---|
| 工具结果 JSON 字符串脱敏失败 | `bidcraft/security.py::redact_mapping`、`bidcraft/generation/tool_loop.py::_redact_result_text/_trace` | 递归解析嵌入 JSON 字符串并遮蔽敏感键；`tests/test_v0331_phase4_security.py::test_nested_json_tool_result_is_redacted_in_trace`、`tests/test_v0330_agent_security.py::test_nested_json_result_is_redacted` 先失败后通过 |
| 企业 KB 未显式授权即可全局检索 | `bidcraft/knowledge.py::search`、`bidcraft/generation/chapter_plan.py::_allowed_kb_ids` | 带 `options.enterprise_id` 的库必须经 `extra_ids` 显式授权；规划白名单同样过滤；两个真实 DB 测试先失败后通过 |
| 旧检索工厂丢弃项目/标段作用域 | `bidcraft/generation/chapter_plan.py::scoped_knowledge_provider` | 不支持作用域参数的工厂直接 `ValueError`，不再静默降级；真实测试先失败后通过 |
| KB 管理变更无审计 | `bidcraft/api/routes/admin.py::create_kb/update_kb/delete_kb/embed_kb` | 增加 `kb.create/update/delete/embed` 审计，detail 只含 id/name/字段/计数；`test_admin.py`、`test_knowledge_vector.py` 断言真实 AuditLog 行，先失败后通过 |
| `lesson_policy` 审计不可追责 | `bidcraft/api/routes/projects.py::update_project` | 审计 detail 增加 `revoked_added/revoked_removed/changed`；`tests/test_v0330_lesson_governance.py` 断言撤销 id 出现在 AuditLog，先失败后通过 |
| 异常 ProjectMember 角色进入待办 | `tests/test_v0331_phase4_security.py::test_pending_inbox_ignores_invalid_member_role` | 真实 DB 构造 `role='pending'` 成员与审批链，当前实现未返回条目；审计提示未复现，不改代码 |

无 migration、无新增路由/schema、无前端改动。

## 3. 验证命令与真实结果

```bash
.venv/bin/python -m pytest -o addopts= -q tests/test_v0331_phase4_security.py
# 5 passed in ~1s（含先失败后通过用例）

.venv/bin/python -m pytest -o addopts= -q \
  tests/test_v0331_phase4_security.py tests/test_v0330_agent_security.py \
  tests/test_v0330_tool_validation.py tests/test_security.py \
  tests/test_v0330_lesson_governance.py tests/test_admin.py \
  tests/test_knowledge_vector.py tests/test_kb_assets_v032.py \
  tests/test_kb_assets_api_v032.py tests/test_v032_chapter_planning.py \
  tests/test_v0330_chapter_plan_consistency.py tests/test_approvals_pending.py \
  tests/test_quote_approval_condition.py
# 154 passed, 1 warning in 98.29s

.venv/bin/python -m ruff check bidcraft tests scripts
# All checks passed!

git diff --check
# clean
```

```bash
.venv/bin/python -m pytest -o addopts= -q > /tmp/opencode/v0331_phase4_full.out 2>&1; tail -12 /tmp/opencode/v0331_phase4_full.out
# 2379 passed, 1008 skipped, 1 warning in 982.08s (0:16:22)
```

## 4. 数据阻塞与残余风险

- DATA BLOCKED：`Project` 无 `enterprise_id`，双企业成员在项目 A 检索/导出项目 B 企业资产的租户隔离无法闭环；需要先定义项目↔企业绑定关系、新增迁移与真实数据。
- 企业知识库显式授权依赖调用方先做成员校验；`job_support.knowledge_context()` 与 `chapter_plan` 已走该路径。若未来新增检索入口，必须同样传 `extra_kb_ids`。
- KB audit detail 只放 id/name/字段/计数，不记录 api_key；符合安全边界。
- 工具轨迹脱敏会解析嵌入 JSON 字符串；极深嵌套受 `redact_mapping(depth<=6)` 限制，超出部分返回 `***REDACTED***`。

## 5. 回滚

回退本阶段修改的 7 个业务文件与 5 个测试文件；删除 `docs/development/v0.33.1/phase4/`。无数据库迁移和前端状态需要回滚。
