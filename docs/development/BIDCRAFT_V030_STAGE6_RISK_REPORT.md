# BidCraft v0.30.0 阶段 6 风险报告：回归集与质量指标

- 状态：相关语义变更已获用户批准（2026-11-24）
- 审计来源：只读审计 ses_df3313af8ffeEjO2625YY10GMg（HEAD 55adb2e + stage5 工作区）
- 约束：不改历史迁移、不删测试、不降低门禁、不推送 main、不发版。

## 1. 批准事项

1. **合成脱敏回归集 + 基线门禁扩展**：新增 3–6 份全合成样本（表格、分页、OCR 噪声、中文大写金额、跨章引用），加 `SYNTHETIC` 哨兵；基线 schema 增加 `expected_values`、`min_tables`、`min_materials`；CI 增加 `--compare-baseline` 门禁。用户选择“批准（推荐）”。
   - 合规边界：样本全部合成，不来自任何真实文档；无真实企业名/电话/身份证/邮箱。
2. **三项质量指标写入报告**：`manual_edit_rate = revision>1 章节 / 有正文章节`；`issue_density = 未处置 blocking+major 问题 / 千正文字`；`boq_match_rate = 已匹配清单项 / 清单总项`。写入 `QualityReport.summary.metrics`，不新增 HTTP 字段、不改 /admin/health。用户选择“批准三项并写入报告（推荐）”。

## 2. 无语义争议的实施项

- `judge_for_project` N+1：在 `build_requirement_context` 一次计算 project_meta/deadline/matcher 并复用；不改变四态判定、evidence 或 deadline 口径，新增“GET /requirements 与 /check 一致”的集成测试。
- 脱敏合规扫描：测试扫描手机号/身份证/邮箱/真实企业后缀并断言哨兵存在。
- `global_metrics` 挂载健壮性：可选改为 admin_health 内显式合并（内部实现，不改变响应键）。

## 3. 明确不做

- 不把 verdict FAR/FRR 快照挂到 `/admin/health`（避免新增健康字段）。
- 不引入任何真实招标文件；不改动既有合成样本的判定基线语义。
