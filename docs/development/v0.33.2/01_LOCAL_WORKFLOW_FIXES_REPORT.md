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

## 5. 截图补充：运行期三项问题

用户在本地环境截图反馈三项运行期问题，全部按“真实复现 → 测试先行 → 最小修复”处理，未放宽任何门禁：

1. **编辑锁请求返回 HTML**：`GET /projects/{ref}/chapters/{node_id}/lock` 只有 POST/PUT/DELETE，请求落到 SPA catch-all 返回 `index.html`，前端 `response.json()` 报 `Unexpected token '<'`。
   - `bidcraft/api/routes/collaboration.py` 新增 GET lock，复用 `check_section_lock`，无锁返回 404；`web/src/api/collaboration.ts` 已把 404 转为 `null`，无需改前端。
   - 路由总数 275→276，`tests/test_api_package.py`、`tests/test_v030_stage8_observability.py` 同步；`tests/test_section_locks.py` 新增 JSON/HTML 回归。
2. **全书生成误判规划依赖失效**：台账重解析会删除并重建评分项/要求，UUID 变化但名称/正文稳定；旧规划被误报“评分项已删除”。
   - `bidcraft/generation/chapter_plan.py` 新增 `_refresh_stable_refs`：仅在同项目内按精确稳定值（评分项名称、要求正文、事实 key）找到唯一真实行时刷新 ID；值真的变化或匹配歧义仍判废。
   - `validate_chapter_plan` 与 `plan_dependency_staleness` 均先刷新；`_fact_refs_with_provenance` 按 key 命中时覆盖旧 ID。
   - `tests/test_v0330_chapter_plan_consistency.py` 新增重建 UUID 场景与负向“值真变仍判废”。
3. **批量确认后仍有章节未确认**：`REJECTED` 规划在 `load_active_plan` 中表现为无有效规划，且旧流程确认时不判废，导致到生成阶段才 409。
   - `bidcraft/api/routes/projects.py::approve_chapter_plan` 确认前调用 `invalidate_stale_plan` 并在 409 前提交判废状态；新增 API 用例。
   - `web/src/hooks/useChapterPlans.ts`：`loadChapterPlans` 改为 allSettled，单章失败不再清空全部；批量确认校验返回规划必须 `CONFIRMED`，否则计入失败；`ChapterPlanningPanel.tsx` 等待刷新后再提示，并明确“已失效章节请先批量生成”。

## 6. 评分台账重复/噪声项导致 0 覆盖

用户反馈：招标文件提取的评分台账存在重复项与条款碎片（截图含 `24J`、`30日`、`小写：` 等），覆盖矩阵显示 0%。

根因：表格抽取直接使用单元格原文，`24J`/金额/日期等非分值列也被当成评分项；去重只按原始字符串；覆盖矩阵虽然会按归一化名称去重，但不会过滤已经入库的噪声项。

修复（测试先行，先红后绿）：

- `bidcraft/generation/outline.py`：统一 `is_scoring_noise()` 与稳定名称归一；历史 NULL/重复项共用同一口径。
- `bidcraft/parsing/extract.py`：评分表只接受明确分值单元格（`^\d+(?:\.\d+)?\s*分?$`），单元格先清洗，过滤 `小写/大写/报价金额/24J/30日/合计/总分/满分` 等碎片；表项与文本回退都做归一化去重；自动推导 `category`（技术/商务/价格），让覆盖匹配有依据。
- `bidcraft/generation/coverage.py`：即使噪声已入库，覆盖矩阵读取时也会过滤并在 `validation_issues` 明确报告过滤数量；匹配增加“评分项名称命中/意图相似”；锚点优先选中文技术短语，不再误选金额碎片；节点同时返回 `id` 与 `node_id`。
- `bidcraft/ledger.py` / `bidcraft/scoring.py`：台账入库与得分汇总复用 `normalize_scoring()`，避免列表页/覆盖矩阵/得分分析各算各的。
- 前端 `ScoreCoverage.tsx`：总分 0 时不再显示“0% 覆盖”的伪结论，改为提示重新提取；`issues` 可缺省；`types.ts` 补齐 `anchors`/`evidence`。

测试：`tests/test_v0332_scoring_noise.py`（噪声过滤、稳定去重、名称/意图匹配、已入库噪声过滤、台账落库去重）；同步更新 `tests/test_scoring_coverage.py` 里过短的合成名称；Web 新增总分为 0 的展示用例。

## 7. 材料台账重复/提示行导致清单无法对齐

用户截图显示材料台账出现“（按清单序号4至24逐项填列）”“—”等提示行，以及同规格重复行，造成大量“台账有·清单无”。

根因：`extract_materials` 未过滤提示行/占位符，去重只按原始字符串；已入库噪声在 `boq.align` 中仍作为真实台账行参与对齐。

修复：

- `bidcraft/parsing/clean.py`：新增 `is_material_noise()` / `normalize_material_key()`，识别“按清单序号…逐项填列”等提示语、纯破折号占位符和过短碎片。
- `bidcraft/parsing/extract.py::extract_materials`：过滤提示行，按全角/空白/标点折叠后的名称+规格稳定去重；重复行合并数量，不臆造新字段。
- `bidcraft/ledger.py`：台账构建、入库、读取三处统一调用材料去重/过滤；已有业务行不直接删除，只在读取和对齐时排除噪声。
- `bidcraft/boq.py::align`：读取时过滤已入库提示行；保留同名同规格的真实多行，避免破坏一对一对齐与数量核对。
- 新增 `tests/test_v0332_material_noise.py`：覆盖抽取过滤/去重、稳定变体保留、对齐时忽略提示行且保留真实重复行。

## 8. 本地模型调用与品牌口径

- 用户反馈已配置模型但系统未调用。根因：本地测试 env 初始化时强制写入 `BIDCRAFT_OFFLINE=1` 与不可达假模型地址；`model_config.resolve_chat_endpoint()` 在 offline 时直接返回 None。
- `scripts/local_test_env.sh` 新增 `model-on` / `model-off`：前者移除 offline/假模型变量，让服务读取后台已配置的 chat 模型；后者恢复测试离线口径。已在本地执行 `model-on`，验证 `resolve_chat_endpoint()` 返回 `source=database, model=deepseek-flash`。
- 用户反馈顶部仍显示旧定位“弱电投标文件工作台”。已将 `AppShell`、`HelpContext`、`Masthead`、`LoginPage`、`RegisterPage` 统一改为“AI 技术标编制工作台”；保留“标书工坊”品牌名。新增 `Masthead.branding.test.tsx` 断言新文案且旧文案不存在。

## 9. auto 模式全书生成遇到失效规划

用户反馈全书生成失败，报“章节规划依赖已变化”。原因：评分噪声清理后旧规划引用的评分项被删除，后端正确判废；auto 模式不要求人工确认，但前端没有在启动生成前重建失效规划。

修复（frontend-only）：

- `web/src/hooks/useChapterPlans.ts`：新增 `batchRegeneratePlans()`，复用单章 `regenerateChapterPlan` 顺序重建。
- `web/src/pages/tabs/DraftTab.tsx`：auto 模式下全书生成前用 `missingPlanNodeIds()` 找出失效规划，先批量重新生成；失败则终止启动，成功后再提交全书任务。review / review_strict 模式仍由用户在「目录大纲」批量生成并确认，不自动确认。
- 新增 `web/src/pages/tabs/DraftTab.autoRegenerate.test.tsx`，验证 auto 模式重建与生成调用顺序。

## 10. 清单对齐页缺少直接的提交入口

用户反馈：商务标提示“先在清单对齐中提交并定稿报价版本”，但清单对齐页找不到提交/定稿按钮。

根因：提交/审批按钮只在已经保存的草稿版本行上出现；刚导入清单、尚未保存版本时只看到“保存当前为版本”，缺少一步到位的入口。

修复（frontend-only）：

- `web/src/pages/tabs/BoqVersions.tsx`：`save()` 支持 `submitAfter`，新增“保存并提交审批”主按钮；先保存当前单价快照，再直接打开提交审批确认弹窗；不自动批准、不绕过审批链或导出门禁。
- 新增 `web/src/pages/tabs/BoqVersions.submit.test.tsx`：验证按钮调用 `createBoqVersion` 并进入提交确认。

## 11. 验证结果

- ruff：`ruff check bidcraft tests scripts` All checks passed。
- 后端定向：事实治理 + Alembic + api_package + BOQ → `70 passed`；规划/审批/Worker/Phase1/Phase4 回归 → `91 passed`；来源口径留痕新增/相关用例 → `49 passed`；截图三项回归（编辑锁/稳定值刷新/确认前判废）→ `30 passed`。
- 真实 PG 迁移：scratch DB `v0332_fact_provenance`（PostgreSQL 16.2 / 127.0.0.1:5433）运行 `tests/test_alembic.py tests/postgres/test_v032_migrations.py tests/postgres/test_v0313_migration_permissions.py` → `18 passed`。
- 评分噪声回归：`tests/test_v0332_scoring_noise.py tests/test_scoring_coverage.py tests/test_extract.py tests/test_xlsx.py tests/test_batch3_generation_fixes.py` → `32 passed`；相关 11 文件回归 `94 passed`。
- 材料/评分台账定向：`.venv/bin/python -m pytest -o addopts= -q tests/test_v0332_material_noise.py tests/test_v0332_scoring_noise.py tests/test_boq.py tests/test_extract.py tests/test_calibrate.py tests/test_calibrate_baseline.py` → `75 passed`；重复台账行补充修复后 `tests/test_v0332_material_noise.py tests/test_boq.py tests/test_api_boq.py` → `39 passed`。
- Web：typecheck / lint / `npm test -- --run` → 66 files / 482 tests 全通过；`npm run build` 通过（Vite 8，1720 modules）。ErrorBoundary 的 “渲染炸了” 是预期测试输出。
- 后端全量：`.venv/bin/python -m pytest -o addopts= -q` → `2404 passed, 1008 skipped, 1 warning in 997.07s`，零失败。

## 11. 已知边界

- 价格识别仅支持明确别名（精确/前缀匹配）；`含税单价` 等复合表头仍按未识别处理，避免误判。
- 金额反推单价按 4 位小数舍入；总价仍由数量×单价重算，不直接采用工作簿合价。
- 用户补充说明是人工留痕，不是招标文件证据；不会进入原文引文口径。
- 未替换用户自建的 127.0.0.1:8000 服务；浏览器端最终人工确认请在该服务更新代码/重启后进行。

## 12. 回滚

逐项 revert：React 两个组件两个测试；批量规划相关前端文件与测试；BOQ 五个文件；事实来源类型六个后端/迁移文件与四个前端文件。迁移 0016 只需 downgrade 到 0015，删除两列，不触碰历史事实值。
