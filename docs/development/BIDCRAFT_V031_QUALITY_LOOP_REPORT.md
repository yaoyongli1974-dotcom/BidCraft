# BidCraft v0.31.0 阶段 7：前后端质量闭环报告

基线：阶段 6（`tender_quality` 写入质量报告摘要）。本阶段只打通既有数据到界面的显示链路，不新增后端路由、不改业务语义、不降低门禁。

## 1. 结论

质量报告摘要中的三类过程指标（人工修改率、问题密度、清单匹配率）与三类真实件指标（条款召回率、关键值解析率、判定一致率）现在在「质量评估」页统一展示。**真实件数据集未就绪时显示「未测量」，不会把 0 误读为质量为零。**

## 2. 交付内容（11 项核对）

| # | 项目 | 状态 | 证据 |
|---|---|---|---|
| 1 | 前端类型 | 完成 | `web/src/lib/types.ts::QualityReport.summary` 新增 `metrics` 与 `tender_quality` |
| 2 | 摘要归一化 | 完成 | `web/src/api/evaluation.ts::normalizeQualityReport` 保留两类指标、`notes`、`reason`、`files` |
| 3 | 未测量语义 | 完成 | `asMetric()` 将 `null/undefined/''` 归一为 `null`，界面显示「未测量」 |
| 4 | 指标面板 | 完成 | `QualityTab.tsx` 新增「过程与真实件指标」面板，6 个 StatTile |
| 5 | 状态提示 | 完成 | 面板标题 Badge 显示 `tender_quality.status`，并显示 `reason`/`notes` |
| 6 | 比率格式 | 完成 | 0～1 比率展示为百分比；问题密度展示为「/千词」 |
| 7 | 数值健壮性 | 完成 | 非有限数字回退原字符串，不抛异常 |
| 8 | 契约测试 | 完成 | `web/src/api/evaluation.test.ts` 新增真实摘要形状用例，断言未测量为 `null` |
| 9 | 既有测试兼容 | 完成 | 旧摘要形状仍可归一化为 `undefined`，无破坏性变更 |
| 10 | 路由数量 | 不变 | 前端-only；后端路由仍为 252 |
| 11 | 权限与门禁 | 不变 | 只读成员仍不能运行评估；无新写操作 |

## 3. 验证结果

| 检查 | 结果 |
|---|---|
| `npm run typecheck` | 通过 |
| `npm run lint` | 通过 |
| `npm test -- --run` | **39 files / 320 tests passed**（新增 1 项） |
| `npm run build` | Vite 8 构建通过 |

## 4. 边界与风险

- 真实指标仍取决于用户提供的脱敏真实招标文件与人工标注；数据未就绪时本阶段只保证「明确未测量」的展示口径。
- `tender_quality.files` 明细已归一化但当前面板只展示汇总；如需逐文件下钻，待真实样本就绪后再评估。
- 回滚：恢复 4 个 web 文件与本报告即可；无迁移、无数据库/API 变更。
