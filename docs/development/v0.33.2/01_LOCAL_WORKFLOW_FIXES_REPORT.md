# v0.33.2 本地工作流四项问题修复报告

基线：本地 main `fafd9d593ad14706b88046504cd9f68dd827e560`（v0.33.1 Release 之后新增的本地测试环境提交）。
分支：`fix/v0.33.2-local-workflow`。
约束：未推送 main / tag / Release，未部署生产；未删除测试、未弱化断言、未用 mock 代替核心证据。

## 1. 逐章规划过于繁琐

用户反馈：每一章都要点生成、确认。

根因：`ChapterPlanningPanel` 只支持单章；`useChapterPlans` 按 node 加载；后端规划 API 是单节点接口。批量生成/确认必须复用既有 API，不能自动确认绕过人工复核。

修复（frontend-only）：

- `web/src/components/ChapterPlanningPanel.tsx`：新增“批量生成缺失规划”“确认全部待确认”按钮；按真实 ID 顺序调用既有 generate/confirm API；失败逐章报告并继续；`outlineDirty`、只读、无写权限、无复核权限时禁用；批量生成绝不自动确认。
- `web/src/hooks/useChapterPlans.ts`、`web/src/lib/chapterPlan.ts`：新增 `missingPlanNodeIds`、`pendingConfirmNodeIds`、`batchGeneratePlans`、`batchConfirmPlans`、`isPendingConfirmPlan`。
- `web/src/api/generation.ts`：`review_strict` 不再被降级为 `auto`。
- `web/src/pages/tabs/OutlineTab.tsx`：新增“一键生成缺失章节规划”，批次完成后刷新 outline/project。

测试：`generation.planning.test.ts`、`outboundContract.test.ts`、`ChapterPlanningPanel.batch.test.tsx`、`OutlineTab.planning.test.tsx`，覆盖真实 ID、跳过规则、部分失败、只读/未保存/非复核人禁用、无自动确认、mode 映射。

## 2. 事实台账缺失项无法保存

用户反馈：招标文件本身缺失的信息，无法提供原文 ID / 摘录 / 页码，因此不应被强制要求。

根因：`ledger._verify_evidence` 对所有 `confirmed` / `manual` 都要求真实 source_*，没有“用户补充”这一来源口径。

修复：

- `bidcraft/models.py::Fact` 新增可空 `provenance_kind` / `supplier_note`，历史 NULL 按 `tender_extract` 解释；迁移 `0016_fact_provenance` 只加两列、无 server_default、可逆。
- `bidcraft/api/schemas.py::FactUpdate`、`bidcraft/ledger.py::FactItemV032`、`load_ledger`、`save_ledger`、`update_fact`：支持 `tender_extract` / `user_supplied`；user_supplied 必须填写 `supplier_note`，不写 source_*；切回原文口径时仍必须提供真实证据；审计 detail 记录来源类型与说明。
- `bidcraft/api/routes/projects.py::set_fact` 回显新字段。
- `bidcraft/ledger.py::render_ledger` 在提示词中标注“用户补充 / 招标文件原文”，用户说明不伪装为招标原文。
- 前端 `types.ts`、`api/ledger.ts`、`LedgerTab.tsx`：新增来源类型选择与说明输入；只有招标文件原文才要求源文件 ID / 摘录 / 页码；用户补充要求说明；列表展示用户补充标记。

测试：`tests/test_v032_fact_governance.py` 新增 5 个真实 DB / HTTP 用例；`tests/test_alembic.py` HEAD 更新为 `0016_fact_provenance` 并新增可逆/幂等测试；前端 `api/ledger.test.ts`、`outboundContract.test.ts`、`LedgerTab.factGovernance.test.tsx` 覆盖正常与异常路径。

### 2.1 来源口径在后续节点的留痕（用户补充要求）

按用户进一步要求，“按招标文件原文录入”仍强制真实来源文件 ID / 原文摘录 / 正整数页码；“用户补充说明”不伪造证据，但自动记录服务端确认人/时间，并把口径带到后续节点：

- `bidcraft/ledger.py`：来源口径、说明文字或状态降级变化都视为事实口径变化，清空旧确认盖章、把引用章节退回 `review`、清空锁定与复核戳，并写 `fact.invalidation`；`fact.update` 审计同时记录 old/new provenance、`provenance_changed`、`status_demoted`。
- `bidcraft/generation/chapter_plan.py`：规划上下文与持久化 `fact_refs` 均携带 `provenance_kind` / `supplier_note` / 服务端确认人时间；提示词版本 bump 到 `chapter_plan_v4`；`plan_context_text` 明确区分“用户补充”和“招标文件原文”；`plan_dependency_staleness` 比较来源口径、说明、确认人和确认时间，口径变化即判废已确认规划。
- `bidcraft/provenance.py` 与 `web/src/pages/tabs/DraftTab.tsx`：章节引用卡显示来源口径、用户依据、服务端确认人和确认时间，避免补充说明在正文节点被误读为招标原文。
- 新增 `tests/test_v0332_fact_provenance_trace.py`：4 个真实 DB 用例覆盖来源切换失效与审计、规划上下文/引用/提示词、规划依赖判废、引用卡载荷。`tests/test_v032_learning.py` 与 `tests/test_v032_chapter_planning.py` 的提示词版本断言同步更新为 v4（断言仍精确，未弱化）。

## 3. 含报价清单导入未提取单价

用户反馈：上传含报价清单后，系统没有提取报价。

根因：`boq.py::import_boq` 硬编码 `prices={}`；字段别名与解析流程都没有单价/合价。

修复（BOQ 代理完成）：

- `bidcraft/boq.py`：新增 `PRICE_ALIASES` / `AMOUNT_ALIASES`、`_price_field_of`、`_parse_price`；`ParsedBoq.prices` 以 `sheet!row` 记录真实单价；支持金额/数量反推、货币符号与千分位、无效值告警、等长 sheet 优先选有价格者；`import_boq` 保存价格，原有手工覆盖仍可用。
- `web/src/pages/tabs/BoqTab.tsx`：更新“价格必须手工录入”的旧文案，提示可识别单价/合价列，toast 显示识别到的单价数。

测试：`tests/test_boq.py` 3 个解析用例；`tests/test_api_boq.py` 上传/读取/覆盖用例；`web/src/pages/tabs/BoqTab.pricedImport.test.tsx`。先失败后修复。

## 4. 正文页 React #290 崩溃

用户反馈：点击“正文 / 开始写作”出现 `Minified React error #290`，args 为项目 UUID。

根因：`DraftTab.tsx` 把 `ref={ref}` 传给函数组件 `ChapterCollab`；`ref` 是 React 保留属性，字符串 ref 在函数组件上触发 #290，真正的 `projectRef` prop 变成 undefined。

修复：

- `web/src/components/ChapterCollab.tsx`：prop `ref` 改名为 `projectRef`。
- `web/src/pages/tabs/DraftTab.tsx`：`projectRef={ref}`。
- 新增 `ChapterCollab.test.tsx`、`DraftTab.refFix.test.tsx`；先复现“Function components cannot have string refs”，修复后通过。

## 5. 验证结果

- ruff：`ruff check bidcraft tests scripts` All checks passed。
- 后端定向：事实治理 + Alembic + api_package + BOQ → `70 passed`；规划/审批/Worker/Phase1/Phase4 回归 → `91 passed`；来源口径留痕新增/相关用例 → `49 passed`。
- 真实 PG 迁移：scratch DB `v0332_fact_provenance`（PostgreSQL 16.2 / 127.0.0.1:5433）运行 `tests/test_alembic.py tests/postgres/test_v032_migrations.py tests/postgres/test_v0313_migration_permissions.py` → `18 passed`。
- Web：typecheck / lint / `npm test -- --run` → 62 files / 475 tests 全通过；`npm run build` 通过（Vite 8，1720 modules）。ErrorBoundary 的 “渲染炸了” 是预期测试输出。
- 后端全量：`.venv/bin/python -m pytest -o addopts= -q` → `2393 passed, 1008 skipped, 1 warning in 957.49s`，零失败。

## 6. 已知边界

- 价格识别仅支持明确别名（精确/前缀匹配）；`含税单价` 等复合表头仍按未识别处理，避免误判。
- 金额反推单价按 4 位小数舍入；总价仍由数量×单价重算，不直接采用工作簿合价。
- 用户补充说明是人工留痕，不是招标文件证据；不会进入原文引文口径。
- 未替换用户自建的 127.0.0.1:8000 服务；浏览器端最终人工确认请在该服务更新代码/重启后进行。

## 7. 回滚

逐项 revert：React 两个组件两个测试；批量规划相关前端文件与测试；BOQ 五个文件；事实来源类型六个后端/迁移文件与四个前端文件。迁移 0016 只需 downgrade 到 0015，删除两列，不触碰历史事实值。
