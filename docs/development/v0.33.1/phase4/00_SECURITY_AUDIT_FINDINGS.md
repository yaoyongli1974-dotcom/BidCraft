# v0.33.1 Phase 4 安全/租户/权限/审计审查（实施前）

本文件记录 Phase 4 实施前的只读审查结果；所有结论均基于当前分支真实代码，排除已实现的插件沙箱、SSRF、scope、Ed25519、限流、CSRF、基础审计、工具循环脱敏/去重/预算、审批解释、事实确认与四眼原则。

## 可立即闭环

| # | 级别 | 位置 | 缺口 | 最小修复 / 测试 |
|---|---|---|---|---|
| 1 | P2 | `bidcraft/generation/tool_loop.py:314-315`、`bidcraft/security.py:172-195` | 工具结果经 `json.dumps` 变成字符串后只走 `redact_text`，JSON 引号键的 `password`/`token` 不脱敏；可能通过 workflow 输出暴露给 viewer | `_trace` 先 `json.loads(result)`，用 `redact_mapping` 后再序列化；测试端到端断言 trace 不含明文 secret |
| 2 | P2 | `bidcraft/knowledge.py:1284-1288`、`bidcraft/generation/chapter_plan.py:369-377`、`bidcraft/enterprise/assets.py:156-170` | 企业 KB 边界只认 `options.personal`；直接设置 `options.enterprise_id` 而未设 `personal=True` 会绕过成员校验并进入检索/规划白名单 | `knowledge.search` 与 `_allowed_kb_ids` 对 `enterprise_id` 同样要求成员资格；非成员检索/规划测试 |
| 5 | P2 | `bidcraft/api/routes/admin.py:248,275,321,388` | 超级管理员 KB 增改删/embed 无审计；模型与规则包同类问题 | 提交前写 `kb.create/update/delete/embed` 审计，detail 只含 id/name；管理员审计测试 |
| 6 | P3 | `bidcraft/api/routes/projects.py:738-752` | `lesson_policy` PATCH 审计只记 key，不记撤销经验 id 与阈值变化 | 审计 detail 写归一化 diff（不含经验正文）；测试断言 lesson id 可见 |
| 8 | P3 | `bidcraft/generation/chapter_plan.py:238-256` | 旧签名 provider 工厂静默退回 `factory(db,user_id)`，丢项目/标段作用域 | 无法探测作用域时显式拒绝；测试旧签名工厂不返回全局结果 |
| 9 | P3 | `bidcraft/boq.py:1555`、`bidcraft/commercial.py:1537` | 待审收件箱未过滤非法 ProjectMember.role，与 `authorize_projects` 口径不一致 | 复用 `MEMBER_ROLES` 过滤；异常角色成员测试 |

## 部分可闭环（口径统一）

- `ChapterPlanGenerateRequest.package_id` 与 provider 的 `project.meta.package_id` 口径不一致：限制在本项目真实 `BidPackage`，并让门禁/规划/确认/读取/worker 使用同一个 package_id。
- 同步门禁 `load_active_plan()` 不带 package_id 而 worker 带 package_id：统一传入当前 package_id；不存在则按无规划处理。
- 真实 KB 跨库泄漏还缺 `kb.options.project_id/package_id` 的生产写入入口；本阶段只做口径收口，不宣称完成真实跨库验收。

## 数据阻塞

- 项目与企业租户绑定缺失：`Project` 无 `enterprise_id`，双企业成员在项目 A 导出/预览仍可能看到企业 B 资产。需要数据侧先给出项目↔企业关系，新增迁移与真实绑定数据后再闭环；本阶段不得用合成数据冒充已完成。

## 决策

Phase 4 先实现 #1、#2、#5、#6、#8、#9，再统一 package 口径；#7 标记 DATA BLOCKED。所有改动必须测试优先，禁止降低既有断言。
