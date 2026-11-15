# OpenBidKit 领域逻辑抽取规格（供 Python 干净重写）

来源仓库：`/media/yao/Data/workbuddy/zhibiao/OpenBidKit_src`（Electron + React + C# OpenXmlHelper，AGPL）。
本文只记录**规则、字段、阈值、流程与行为**，不复制实现代码。所有引用为 `文件:行号`。

> 重要前提：`sql/workspace_schema.sql` 是"设计说明快照"（`PRAGMA user_version = 44`，见 1-17 行），
> 运行时真正建表以 Electron migration 为准（同文件 6 行注释）。字段名可视为权威，迁移顺序不必照搬。

---

## 0. 结论摘要

1. 产品是"招标文件 → 全局事实 → 目录 → 逐节 HTML 正文 → DOCX/PDF"的单工作区流水线，所有中间态落 SQLite + 工作区 Markdown/HTML/JSON 文件。
2. 关键设计是**把"事实"和"正文"分离**：先生成一组 `{id,title,content}` 的全局事实，正文阶段强制以事实为准，缺失时按三种模式（编造/概括/占位）统一处理。
3. 目录不是"AI 随便生成"：完整标走招标文件"格式章节"锚点抽取；技术标走技术评分项映射；两者都有确定性的结构校验与人工确认节点。
4. 检查能力是"程序确定性 + AI 语义"分层：暗标有完整 C# 规则引擎（70+ rule id），废标/查重以 AI + 阈值算法为主。
5. 导出保真度靠 C# 直接操作 OOXML（受限 HTML → DOCX、原生 TOC 域、PAGEREF 域、页码测量回填），不是 HTML→PDF 路线。
6. 仓库**没有**弱电/智能化行业模板、参数库或检查清单（第 6 节给证据），这部分必须自建。

---

## 1. 数据模型

### 1.1 技术方案 `technical_plan_*`

| 表 | 位置 | 关键列 / 语义 |
|---|---|---|
| `technical_plan_meta` | `sql/workspace_schema.sql:28-74` | 单行 `id=1`。`step`（见下）、`tender_file_name`、`tender_markdown_path/hash/chars`、`tender_files_json`（多份招标文件元数据）、`tender_original_markdown_*`（多标段原始件）、`original_plan_*`、`bid_sections_json`、`selected_section_id/title`、`outline_word_control_snapshot_json`、`outline_project_name/overview`、`content_generation_runtime_json` |
| `technical_plan_generation_config` | `:77-107` | 单行 `id=1`。`bid_analysis_mode`（key/full/custom）、`bid_section_mode`、`outline_mode`（response-file / standalone-technical / standalone-business）、`outline_expansion_mode`（如 ai-complement；原方案 only 见第 3 节）、`minimum_words`、`maximum_words`、`section_words`、`global_facts_mode`（fabricate/omit/placeholder）、`export_template_id`、`export_template_scope`（ai-only/document）、`use_ai_images`、`use_mermaid_images`、`use_html_images`、`html_image_types`、`table_requirement`（none/light/moderate/heavy）、`image_quantity`（默认 30）、`html_image_optimization`、`word_count_repair`、`layout_check`、四种配图布局开关 |
| `technical_plan_generation_bid_tasks` | `:110-116` | 勾选的招标解析项 `task_id` + `sort_order` |
| `technical_plan_generation_reference_docs` | `:119-125` | 勾选的参考知识库文档 |
| `technical_plan_tasks` | `:128-138` | 每任务类型一行：`type`(PK)、`task_id`、`status`、`progress`、`stats_json`、`error`、`pause_requested` |
| `task_logs` | `:141-151` | 三个任务域共用的有限日志，触发器随任务删除/换代清理（`:507-550`） |
| `technical_plan_bid_items` | `:156-167` | 招标解析结果：`item_id`、`label`、`status`、`content`（Markdown 或 JSON 字符串）、`error`、`sort_order` |
| `technical_plan_outline_nodes` | `:171-189` | **目录与正文唯一权威**。`node_id`(UUID)、`parent_node_id`、`sort_order`、`level`、`title`、`description`、`content_mode`、`content_mode_note`、`rule_basis`（technical/business/both，仅 rule-page）、`source_requirement_id/title`、`knowledge_item_ids_json`、`content`（HTML 正文） |
| `technical_plan_content_sections` | `:200-209` | 每节生成状态 `idle/running/success/error`，不存正文 |
| `technical_plan_content_plans` | `:212-217` | 每节 `plan_json`（字数、写作重点、表格、知识引用） |
| `technical_plan_global_fact_groups` | `:221-231` | `group_id`、`title`、`content`（Markdown bullets）、`sort_order` |

`step` 合法值：`document-analysis / generation-settings / bid-analysis / outline-generation / global-facts / content-edit / expand`（`client/electron/services/technicalPlanStore.cjs:207`）。

### 1.2 标书查重 `duplicate_check_*`（`:238-456`）

- `duplicate_check_files`：`role` = `tender | bid`，投标文件至少一份（`:248-261`）。
- `duplicate_check_analysis_sections`：四类子分析 `metadata / outline / content / image`（`:281-290`）。
- `duplicate_check_metadata_items`：`key`、`label`、`value`、`normalized`、`date_day`、`comparable`、`date_comparable`，唯一 `(file_id,key)`（`:309-322`）。
- `duplicate_check_outline_items`：`item_id` 落库为 `file_id::item_id`；`source`（catalog/heading/semantic）、`confidence`、`normalized_title`、`normalized_path`、`path_titles_json`、`from_tender`、`matched_tender_sentence`（`:332-349`）。
- 结果表：`outline_groups`（type duplicate/similar、score）、`outline_pairwise`（`title_overlap`、`path_overlap`、`order_similarity`、`risk`）、`content_duplicates`+`content_occurrences`、`duplicate_images`(hash)+`image_occurrences`(locations_json)。

### 1.3 废标项 `rejection_check_*`（`:463-630`）

- `rejection_check_documents`：`role`、`source`、`markdown_path`、`content_hash`；招标可多份、合并正文另存（`:475-489`）。
- `rejection_check_extraction`：单行，招标"无效投标/废标项"解析 Markdown + `tender_signature`（`:553-561`）。
- `rejection_check_results`：`result_type` = `rejection / typo / logic`，各自 `status`、`input_signature`（`:565-573`）。
- `rejection_check_risk_findings`：`bid_document_id`、`type`、`severity`、`title`、`summary`、`requirement`、`bid_evidence`、`risk_reason`、`suggestion`（`:576-590`）。
- `rejection_check_typo_findings`：`wrong_text`、`correct_text`、`original_excerpt`、`reason`、`location_hint`（`:599-610`）。
- `rejection_check_logic_findings`：`title`、`original_text`、`location_hint`、`fallacy_reason`、`suggestion`（`:616-627`）。

### 1.4 知识库 `knowledge_*`（`:637-834`）

- `knowledge_documents`：状态机 + 计数（`item_count`、`block_count`、`filtered_block_count`、`candidate_item_count`、`discarded_block_count`、`system_discarded_after_retry_count`、`last_batch_size`）（`:650-676`）。
- `knowledge_blocks`：`block_id`（P000001…）、`type`、`heading_path_json`、`content`、`is_filtered`、`filter_reason`（`:686-699`）。
- `knowledge_candidate_items`（`source` first/supplement/recovery）、`knowledge_items`（`resume` 简介 + `content` 正文）、`knowledge_item_blocks`、`knowledge_discarded_groups`（source ai/system）（`:709-777`）。
- `knowledge_reports`：`batch_size` 默认 20、`coverage_rate`、`matched_rate`、`recovery_attempt_count`（`:783-799`）。
- `knowledge_document_steps` / `knowledge_match_batches`：断点重试用（`:802-834`）。

### 1.5 资信库 / 模板 / 可研 / 暗标

- `credential_library_profile`（单行）：企业工商、财务、资质日期、开户行、水印开关（`:841-877`）。
- `credential_library_certificates / employees / projects / other_materials / images`：图片统一 `owner_type + owner_id + field_key + relative_path`（`:880-951`）。
- `export_templates`：`config_json` 为完整 `ExportFormatConfig`；`is_system=1` 由 `electron/services/systemExportTemplates.cjs` 幂等同步，用户只能复制（`:962-972`）。
- `feasibility_report_*`：`outline_template` 默认 `government`、`target_words` 默认 30000、`analysis_markdown`、`key_parameters_markdown`（`:981-1027`）。
- `blind_check_*`：`presets.profile_json` + `source_json`（AI 从招标提取预设的出处）、`results.summary/stats/rule_errors/groups`、`finding_groups.candidate/items_json`、`reviews.review_json`、`fix_results`（前后汇总、逐条对账）（`:1040-1167`）。

---

## 2. 招标文件解析 / 全局事实

### 2.1 招标解析任务清单（`client/electron/services/bidAnalysisTask.cjs:67-178`）

每条任务：`id / label / required / requiredIn / output(markdown|json) / prompt`。必选 4 项（`projectOverview`、`projectInfo`、`partAInfo`、`deliveryAndServiceRequirements`），技术/商务评分项按 `outlineMode` 条件必选。

| id | 输出 | JSON 字段（json 项） |
|---|---|---|
| `projectOverview` | md | 名称/背景/规模预算/时间/实施内容/技术特点，只要实施相关，不提取商务 |
| `techRequirements` | md | 分「技术评分项」「技术评分要求」：`【评分项名称】【权重/分值】【评分标准】【数据来源】`；判据=是否要写具体方案 |
| `projectInfo` | json | `project_name/project_number/project_type/project_budget/project_address` |
| `partAInfo` | json | `company_name/address/contact_person/contact_phone` |
| `deliveryAndServiceRequirements` | json | `implementation_period/delivery_scope/delivery_location/acceptance_requirements/warranty_period/after_sales_service/response_time/training_requirements/documentation_requirements` |
| `procurementList` | md | 采购清单：名称/规格型号/技术参数/单位/数量/预算/交付/验收/质保，不编字段 |
| `responseFileRequirements` | md | 投标文件组成、格式模板、签章、装订/密封、份数、递交，保持原顺序，区分"必须/如适用" |
| `agentInfo` | json | 代理机构 9 字段（含银行账户） |
| `keyInfo` | json | `bid_announcement_time/bid_file_get_way/bid_file_price/get_bid_file_time/bid_document_submission_location/bid_submission_deadline/bid_opening_time/bid_opening_address/other_notes` |
| `marginInfo` | json | `bidding_deposit/payment_method/due_date/refund_conditions/non_refundable_conditions/other_notes` |
| `qualificationReview` / `complianceCheck` | md | 资格性审查 / 符合性检查（完整性/有效性/规范/偏差） |
| `openBid` | json | `time_place/part_req/invalid_bid/objection/bid_process` |
| `evaluationBid` | json | `committee/duties/scoring/method/principles/others` |
| `businessScoring` | md | 商务评分项/要求，结构同技术评分 |
| `discardedBids` | md | 「无效投标」「废标项」，原文明确项 + 最多 3-5 条"重点补充："经验项（`client/src/shared/prompts/analysisPrompts.ts:1-36`） |
| `signingProcess` | json | `bid_notice/contract_sign/performance_bond/contract_text` |
| `terminationCondition` | json | `breach_termination/force_majeure/contract_termination/dispute_resolution` |

Markdown 项有"整项无结果标记"：**仅当完全无内容**时只返回该标记；局部缺失写"没有提及"（`bidAnalysisTask.cjs:217-228`）。
解析按上下文窗口分段，逐段提取后合并（`:257-300`），空结果会报"解析结果为空，请重新解析"（`:427`）。

### 2.2 全局事实的提取入口与材料

Agent 版：`client/electron/services/globalFactsTaskV2.cjs`；非 Agent 分段版：`globalFactsTask.cjs`；AI 调整：`globalFactsAdjustmentTask.cjs`。

输入材料（`globalFactsTaskV2.cjs:396-444`）：
`招标文件/招标文件-*.md`（或多标段时 `招标文件-当前投标范围.md`）、`项目概述.md`、`招标解析结果.md`、`技术方案目录.md`、可选 `标段说明.md`、`参考知识库/条目-N.md`、`原方案.md`、`资信库.json`、`材料说明.md`。

> **关键细节**：`招标解析结果.md` 只注入 3 项：`projectInfo`、`partAInfo`、`deliveryAndServiceRequirements`（`globalFactsTask.cjs:345-359`）。评分、采购清单等不进入事实生成上下文，需 Agent 自己读招标原文。

### 2.3 输出结构

```json
{"groups":[{"id":"project_team","title":"项目角色变量","content":"- 项目经理：张伟……"}]}
```
- JSON Schema：根只有 `groups`，`minItems:1`，每项必填 `id/title/content`，`additionalProperties:false`（`globalFactsTaskV2.cjs:18-38`）。
- `id` 规范化：小写、非 `[a-z0-9_-]`→`_`、去首尾 `_`，空则 `fact_001`；重复自动加 `_2`（`globalFactsTask.cjs:129-152`）。
- `content` 归一化：字符串/数组/对象 → Markdown bullets（`valueToMarkdown`，`:154-...`）。
- 程序校验：非法 JSON、schema 错误、缺字段均为 `blocking` 问题，退回同一 Agent 会话修复（`globalFactsTaskV2.cjs:49-72`）。

### 2.4 事实生成规则（必须写进重实现）

来源：`globalFactsTaskV2.cjs:219-292`、`globalFactsTask.cjs:518-526, 650-673`。

1. **先定大项（id/title），再填 content**。凡"后续正文需要统一口径"的事项都要建项；多份招标文件综合判断，不能因某份没写而漏项。
2. 全局事实**不是**招标要求摘录、评分规则或待办清单；"要求句"要转写为"本方案统一采用的响应事实/承诺口径/执行安排"。
3. 每条 content 只写简体中文短 bullet，不写分析过程/来源/风险/正文草稿/商务报价/资格材料。
4. **必须包含"工期、运维期、交货时间"中至少一个变量**；缺值时按模式处理（见 2.5）。
5. 知识库和原方案**只能补充已有大项的 content，不得新增大项**；原方案与招标明确事实冲突时，原方案已落地的安排优先替换该 bullet。
6. 资信库只允许固定字段进入事实（`globalFactsTaskV2.cjs:171-176`）：
   - 基本信息：公司名称、法定代表人、地址、所在行业、公司性质、注册资本、经营范围、公司介绍
   - 资质：名称、编号、有效期
   - 员工：姓名、职务、职称、学历、专业、人员简介
   - 业绩：项目名称、客户名称、项目类型、项目负责人、合同金额、开始/结束日期、项目介绍
   - **证件号、联系方式、财务信息、图片不进入全局事实**（`:170` 注释）。
7. 项目角色（项目经理/技术负责人）按招标资格要求从资信库员工中选真实姓名+职称；无符合人员时按缺值规则。
8. Agent 工作原则要求先分段读、可用 grep/node/jq 辅助，但**事实含义与冲突由模型判断**（`:244`）。

### 2.5 缺失值三模式（`globalFactsTaskV2.cjs:89-97`；正文同义，`contentGenerationAgent.cjs:76-80`）

| 模式 | 规则 | 示例 |
|---|---|---|
| `fabricate`（默认） | 资料无明确值时，按项目语境补一套合理、稳定、不冲突的具体值；不得与已有事实冲突，不得覆盖事实 | 项目经理：张伟 |
| `omit` | 该项仍保留，写成不涉及具体时间/地点/人员/业绩/证书/型号/数量指标的**笼统但正确的承诺**；严禁省略、严禁编造 | 项目经理：按招标文件对该岗位的要求配备 |
| `placeholder` | 值必须逐字写 `【待填写】`，不得写"待定/TBD"；不得编造；已有占位符保持原样 | 项目经理：【待填写】 |

三模式都要求 content 中出现工期/运维期/交货时间变量；`omit`/`placeholder` 的笼统承诺或占位符在最终整理时**不得以"不够具体"删除**。

### 2.6 非 Agent 版的补充合并（了解行为即可）

流程：招标分段提取 → 知识库 patch → 原方案 patch → 最终整理（`globalFactsTask.cjs:975-1015`）。
Patch 结构：`{target_group_id, new_group_id, title, content, mode: append|prepend|replace}`；按 `target_group_id` 或标题匹配，未匹配则新建（`:253-291`）。
最终整理规则：合并同义/重复大项、删除仍停留在要求摘录/评分规则/资料清单/待办层面的内容、要求句改写为方案口径（`:99-115, 650-673`）。

生成完成后若用户用 AI 调整事实，会**清空正文生成结果**（`invalidateContentGeneration: true`，`globalFactsAdjustmentTask.cjs:110-116`），说明事实是正文的强上游。

### 2.7 「投标文件组成 / 格式章」的识别与使用

实现：`client/electron/services/outlineFormatExtraction.cjs`。

- 判据是**内容功能**不是标题词：招标文件中"规定投标人应提交哪些文件，并给出这些文件的格式、样式或待填写文本"的部分即格式章节；可能是独立章节，也可能是文件末尾的一组附件；须知/评审办法只提材料名而无格式 → 不算（`:115-124`）。
- 两种 basis（`:7-9`）：
  - `format-template`：找到格式章节，`format_scope` 为数组，每段 `{source?, start, end}`，按投标文件组成顺序覆盖整个格式章节。
  - `response-requirements`：找不到格式章节时退回"响应文件要求"，`format_scope=null`，只生成**平铺一级目录**，不生成 children/source/post_award（`:136` 第三步）。
- 抽取规则（`:126-135`）：格式章节自身每个文件/格式条目 = 一个节点；**不合并、不拆分、不新增、不删除**；只在原文本身有上级归组时才生成层级；标题只去掉序号/附件编号/章节编号与纯格式标注，其余原样；只给标题、要求投标人自写的条目也要建节点；仅中标后使用的条目要列出并标 `post_award=true`。
- 位置锚点：`start` 含、`end` 不含；节点 `source.start` 为标题位置，`source.end` 为同段下一同级条目起点；一级目录必须完整落在某个 scope 段内且按段顺序，子节点同文件且范围在父节点内，同级递增不重叠（`:132`）。
- 校验是确定性的：scope 重叠、标题在范围内找不到、节点越界/越父/同级重叠、basis 与 scope 不匹配都会 `blocking`（`:147-240`）。标题核对取起始位置后前 3 个非空块（`TITLE_CHECK_UNIT_COUNT=3`，`:14`）。
- 结构来源：Word 原件用 OpenXmlHelper 列块（块号），无原件的 PDF/MD 合并后加行号（`L000123 |`）；混合导入只给非 docx 文件加行号（`:41-83`）。

---

## 3. 技术方案大纲与正文

### 3.1 节点结构

`technicalPlanOutline.cjs` + `outlineGenerationTaskV2.cjs:72-156`。

```
{ id: uuid|null, number: "1.1.2", title, description,
  attr?: 通用|商务/资信|技术|其他|目录|报价|业绩,      // 仅一级目录
  content_mode?: ai-generate|template-fill|rule-page|directory-generate|manual-fill|other, // 仅叶子
  content_mode_note?, rule_basis?: technical|business|both,
  children?: [...] }
```

- **最多 6 级**；父节点只能有 `children`，不得有 `content_mode/content_mode_note`（`:107-124, 818`）。
- `id` 是 UUID v4 稳定身份，**永不因改名/排序/层级变化而变**；Agent 输出新节点必须 `id:null`，由程序 `randomUUID()` 分配；未知/重复 id 直接报错（`technicalPlanOutline.cjs:38-61`）。
- `number` 由程序按当前位置计算：`1` / `1.1` / `1.1.1`（`numberOutline`，`:14-19`）；不得把编号塞进 id。
- 一级 `attr` 与 `content_mode` 有硬约束（`outlineGenerationTaskV2.cjs:52-57`）：商务/资信→template-fill|rule-page；目录→directory-generate；报价→template-fill|rule-page|manual-fill；业绩→template-fill。
- `rule-page` 叶子**必须**填 `rule_basis`，其他叶子不得填（`technicalPlanOutline.cjs:30-36`）。

### 3.2 三种目录来源（outline_mode）

1. `response-file`（完整标）：走第 2.7 节"格式章节"抽取，目录=投标文件组成，技术章节只是其中 attr=技术 的 ai-generate 叶子。
2. `standalone-technical`（技术标独立成册）：一级目录**直接对应技术评分大项**；禁止创建"技术方案/项目管理方案/监理大纲/施工组织设计/技术标"等外层总目录，也不得混入商务/资信/投标函（`outlineGenerationTaskV2.cjs:768-797, 840-863`）。
3. `standalone-business`（商务标独立成册）：同样用格式章节提示词，金额类多为 template-fill/rule-page/manual-fill。

`outline_expansion_mode` 另控制是否用原方案：`original-only` 时目录**只能来自原方案.md 实际存在的章节**，不得新增/拆分/为凑字数补章节（`ORIGINAL_ONLY_DIRECTORY_RULE`，`:58`）。

### 3.3 目标叶子数与字数分配

- 默认单节约 3000 字（`DEFAULT_ESTIMATED_SECTION_WORDS=3000`，`:31`）。
- `deriveTargetLeafCount`（`:341-353`）：
  - min 与 max 都有：`ceil((min+max)/2 / sectionWords)`
  - 只有 max：`floor(max/sectionWords) - 2`
  - 只有 min：`ceil(min/sectionWords) + 2`
  - 都没有：`null` → 由 Agent 按项目复杂度决定。
- 独立成册时每个技术分支至少保留 1 个 ai 叶子（`enforceMinimumLeafTarget`，`:355-362`）。
- 允许偏差：最终审核用目标 ±2 作为可接受范围（`:935`）。
- **正文全文基准**（`contentGenerationTask.cjs:256-260`）：
  - min&max：`round((min+max)/2)`
  - 只有 min：`round(min*1.2)`
  - 只有 max：`round(max*0.8)`
  - 0 = 不设全文目标。
- **每节 target_words**（`:262-289`）：
  - 增量新增小节固定 `ADDED_SECTION_TARGET_WORDS`；
  - 无全文目标：`sectionWords`（0=不限）；
  - 有全文目标：本轮预算 `budget = round(totalTarget * 本轮节数 / 总AI节数)`，先按 Agent 提议比例分配，再用"最大余数法"校正到整数且合计=budget；任一节 ≤0 直接报错。
- 表格配额（`:328-333`）：`none=0`、`light=floor(叶子数*0.2)`、`moderate=floor(叶子数*0.4)`、`heavy=不限`。

### 3.4 目录生成流水线（V2 Agent，`outlineGenerationTaskV2.cjs`）

阶段与产物文件（`:32-46`）：`outline.json`、`technical-score-groups.json`、`score-directory-plan.json`、`leaf-allocation.json`、`outline-review.json`、`leaf-allocation-context.json`、`outline-review-context.json`。

1. **initial-outline**：只生成一级目录。
2. **score-planning**（有技术评分时）：
   - 先把"技术评分项"结构化为 `{groups:[{requirement_id:"R1", title, description, detail_points:[...]}]}`（`:158-183`）；"技术评分要求"只能作为评分标准/扣分口径，不能变成评分项。
   - 再写挂载规划 `{allow_root_changes, branches:[{node_id,node_title,score_item_level,mappings:[{requirement_id,target_title,additional_titles?,adjustment_note?}]}], extra_titles:[...]}`（`:185-238`）。
   - 默认一一对应；合并=多个 mapping 用同一 target_title；拆分才用 `additional_titles`。
   - **必须调用一次 ask-user** 确认挂载目录与评分项层级；只有合并/拆分/遗漏/新增同级大项/改层级/改已确认目录才需要批准（`:857-858`）。
   - 锁定已确认目录：本阶段只允许插入经批准的新一级目录（id=null，attr=技术，ai-generate，无 children）（`:860-862`）。
3. **leaf_allocation**：按评分项数量/细项/复杂度为每个技术分支分配 ai 叶子数；`sum(leaf_count)=allocatable_ai_leaf_count`，每分支 ≥1，优先 ≥2（`:823-835`）。
4. **children_generation**：按评分规划生成完整目录；已确认节点的数量/顺序/层级/id/title/description/一级 attr 不得变；新节点 id=null；最多 6 级；避免单子节点冗余层级（`:865-905`）。
5. **leaf_adjustment**（数量偏离目标时）：固定 3 个 ask-user 选项——"接受当前结果" / "允许 Agent 自行调整" / "自定义需求"；只能调整 ai-generate 叶子，不得把其他模式改成 ai-generate（`:908-928`）。
6. **outline_review**：程序先算确定性结果（叶子数、模式计数、最大层级、父节点数、评分映射覆盖），Agent 只审语义（评分覆盖、近义重复、专业合理性）。输出 `{status, issues[], user_feedback, summary}`，`status ∈ passed|simple_fix|user_feedback|user_refuse`，`issues[].category ∈ leaf-count|score-coverage|duplicate-directory|professional-structure`（`:961-990`）。只有标题专业化/明显重复子目录合并可静默修；一级目录调整、增删拆分、结构重排、数量超标必须 ask-user（`:950`）。

### 3.5 逐节正文流程

编排（`contentGenerationTask.cjs:38-60, 580-620`）：
- `content_plan` = `{writing_focus(1-2 句，不编参数), target_words, knowledge:{item_ids}, table:{needed,purpose}, image_suitability_score(0-10)}`。
- `image_needed` **不由模型输出**，程序按 `image_suitability_score` 选本轮入选小节并计算（`:383, 2503`）。
- 知识库引用 id 必须属于允许集合，否则过滤（`:345-360`）。

写作（`contentGenerationAgent.cjs`）：
- 输入文件：`项目概述.md`、`正文编排决策.json`、`正文完整目录.json`、`受限HTML生成规范.md`、`配图类型对照表.md`、`正文模板.html`、`所选模板配置.json`、`全局事实设定.md`（`:28-37`）。
- 输出：每节一个 `正文/<encodeURIComponent(node_id)>.html`，结果清单 `正文生成结果.json = {sections:[{section_id,file,words}]}`（`:38-49, 550`）。
- 阶段：`content-planning → planning → restoring（原方案还原） → generating → auditing → table-cleaning → layout-checking → word-converting → done`（`contentGenerationTask.cjs:1159-1196, 604`）。
- 续跑：同一持久 Session；已发过的阶段只发 `继续之前的任务`（`CONTINUE_PROMPT`，`:25`）；进入读写量大的阶段前压缩历史，摘要只保留流程结论、失败小节与事实口径（`:684-686`）。
- 并发：主 Agent 发编排与配图名额，逐节写作模型只执行本节；所有写作/配图完成后主 Agent 统一 `check-word-count`，**不能边生成边按部分结果调字数**（`:549`）。
- 分批：骨架按约 2000 字一批（`DEFAULT_BATCH_CHARS=2000`，`client/src/shared/layoutBudget/generationBatches.ts:15`），达到 0.6 阈值时在图片块处切分（`:17,60-79`）。

### 3.6 全局事实注入规则

- 程序**自动把 `全局事实设定.md` 全文**注入每个并发小节写作请求，无需主 Agent 转述（`contentGenerationAgent.cjs:543`）。
- 事实是"统一口径约束"，**不是逐项覆盖清单**：只在与本节相关时使用，不为覆盖事实加无关段落，不在各节重复罗列项目概况/人员/设备/制度（`writingInstructions`，`:86-92`）。
- 冲突处理：材料与全局事实冲突 → **以全局事实为准**；允许补充设定只适用于缺失信息，不得覆盖/改变全局事实；禁止无依据引用（`globalFactsInstructions`，`:76-80`）。
- 原方案还原时同样：保留底稿实质信息/参数/措施/承诺，与事实冲突时以事实为准；原表格与原图必须原样保留（含顺序与归属）（`restorationInstructions`，`:122-129`）。
- 占位符约定：正文用 `【待填写】`；可研用 `【待补充】/【待确认】`（`feasibilityReportPrompts.cjs:194-203`）；程序校验"受保护 token"（数量词 + 占位符）在改写后不得减少，否则回退原文（`feasibilityReportTasks.cjs:15-60`）。

### 3.7 去重 / 一致性 / 质量闸门

- **一致性审计是必做阶段**（`contentGenerationTask.cjs:1724`）。逐节核对两类问题：
  - `与全局事实冲突`；`小节内部矛盾`（`contentGenerationConsistencyTools.cjs:18`）。
  - 事实分类固定 8+1：`人数与数量、日期与期限、频次与时限、地点与范围、金额、技术参数、编号与名称、责任归属、其他`（`:19`）。
  - 产出事实台账 `正文一致性事实台账.json/.md`（v2），记录每节 hash、facts（category/subject/value/quote）、issues（type/problem/evidence/suggestion）；台账由程序保护，Agent 改动会在提交前还原（`:14-17, 116-129`）。
  - 判断口径明确排除：用词/称谓/详略差异、事实未提及的补充内容、承诺语气强弱、文风润色（`:169-177`）。
- **表格清理**：`table_requirement=none` 时，审计后把所有数据表格（含原方案表格）转为段落/列表，保留数据、表头关系、单位、条件、备注；`imageText/threeImages/fourImages` 图片表格整块保留（`受限HTML生成规范.md` 表格段）。
- **字数校正**：`word_count_repair` 开关；阶段完成后由程序统一处理，日志记录 `初值 → 终值（要求 min～max），改写节数`（`contentGenerationTask.cjs:2823-2827`）。
- **格式自检**：`layout_check` 时用"导出 → 检测页栏留白 → 并发补写 → 再导出复查"闭环（`:1311-1317`）。
- HTML 结构：受限标签、顶层块 `<!-- yibiao:block -->`、块 id 规则、禁止 div/style/SVG/宽高/颜色（`受限HTML生成规范.md` 基础块段）；导出前逐节 `findHtmlStructureIssues` 预检，问题交回原正文任务修复，不在导出服务内改（`technicalPlanExport.cjs:66-84`）。

---

## 4. 检查能力

### 4.1 废标项检查（rejection-check）——纯 AI，三轮

提示词：`client/src/shared/prompts/rejectionPrompts.ts`；任务：`rejectionCheckTask.cjs`。

- 检查项来源：招标解析的"无效投标/废标项" Markdown + 可选 `custom_check_items`（用户补充关注点）（`rejectionPrompts.ts:16-36`）。
- 严格边界：**只检查电子投标文件正文可判断事项**；签字、盖章、密封、纸质正副本、现场递交、原件、开标现场行为一律排除（`:48-62, 92-98`）。
- "材料缺失"判定：必须在目录、章节标题、附件标题、材料清单、正文、表格等**结构性文本线索**中均找不到痕迹才可报；图片/扫描件正文不可见不算缺失（`:58, 94-96`）。
- 三轮：① 分析（哪些能判、哪些不纳入、重点章节）→ ② 初步检查（结构化 Markdown，有证据才输出）→ ③ 定稿只输出 JSON（`:47-137`）。
- 定稿形状：`findings[] = {bidDocumentId, type: invalidBid|rejectionItem, severity: high|medium|low, title(≤28 中文字符), summary, requirement, bidEvidence, riskReason, suggestion}`（`:1117-1131`）。
- 去重键：pending 用 `bidDocumentId+type+title+requirement`；confirmed 用 `+bidEvidence`（`rejectionCheckTask.cjs:643-679`）。
- 另有"事实登记簿"（factRegister）用于跨片段比对，字段 `category/name/value/evidence/locationHint`（`:695-710, 1204`）。

**错别字**（`:139-175`）：只报高度确信的错别字/同音/形近/录入错误；`{wrongText, correctText, originalExcerpt, reason}`；程序按 `wrongText` 在原文定位并截取 ±8 字上下文校验（`typoExcerptRadius=8`，`rejectionCheckTask.cjs:8, 298-300`）。
**逻辑谬误**（`:177-217`）：句内逻辑漏洞 + 全文前后不一致（人员/设备/工期/金额/数量/服务期/项目名/参数）；`{title(≤28), originalText, locationHint, fallacyReason, suggestion}`。
检查开关：`rejectionCheck / typoCheck / logicCheck` 默认全开（`client/src/features/rejection-check/pages/RejectionCheckPage.tsx:49-51`）。

结果可导出 Excel（`checkResultExportService.cjs:7-9`）：废标 4 sheet「检查概览/废标项/错别字/逻辑问题」；查重 6 sheet「查重概览/元数据/目录重复/文件相似度/重复句子/重复图片」。

### 4.2 暗标合规检查（blind-check）——确定性 C# 引擎 + AI 复核

- 规则真源在 C#：`openxmlhelper/src/OpenXmlHelper/BlindCheck/Rules/*.cs`；桌面端只做预设、调度、结果映射与导出。
- 分类 9 个：`privacy, structure, page, paragraph, font, text, table, consistency, image`（`blindCheckReview.cjs:8`）；严重级 `high|medium|low`（`:9`）。
- rule id 命名空间与清单（`blindCheckPresets.cjs:186-199`），代表性：
  `privacy.keyword`、`struct.doc_props/revision/comment/hidden/header_footer/empty_paragraph/page_number_text/toc/heading_number/textbox`、`page.paper_size/orientation/margins/columns/sections`、`para.alignment/line_spacing/first_line_indent/space_before_after/page_break_before/snap_to_grid`、`font.chinese/western/size/bold/italic/underline/color/highlight/effects/char_spacing/scaling/script`、`text.english_punct/spaces/linebreak/tabs/irregular/halfwidth_punct/fullwidth_digit/fullwidth_latin`、`table.font/size/indent/style/shading/nested/alignment/merge`、`consistency.heading_format/heading_numbering/heading_level_jump/heading_content/body_format/mixed_fonts`、`image.color/graphic_color/vector/meta`、`page.background`。
- `candidate:true` 的规则只做初筛，必须交 AI 复核判定（`blindCheckTask.cjs:43`，schema 见 `blindCheckReview.cjs:32-70`）。
- 预设 profile 结构（`blindCheckPresets.cjs:6-140`）：`page`（纸张/四边距/方向/分栏/节）、`body`（中英文字体/字号/行距 exact|atLeast|multiple/段前段后/首行缩进/对齐/是否允许加粗斜体下划线颜色底纹/字符间距/缩放/容差 0.5pt、0.1 字符、0.02cm）、`headings`（1-4 级）、`table`（字体/字号/首行缩进/样式/底纹/嵌套/合并）、`structure`（空段落/修订/批注/隐藏文字/页眉页脚/页码 forbid|footer_only/标题编号 arabic|不检查/目录 ignore|hint|forbid|require/文本框/超链接/域/书签/封面跳过）、`text`（英文标点/空格规则/制表符/手动换行/不规范字符/半角括号/全角数字字母）、`privacy`（内置词/正则/白名单/忽略机构/公司后缀/手机/身份证/邮箱/URL/统一信用代码/银行账号/占位符/目录）、`props`（作者/最后修改者等）、`image`（颜色/图形颜色/矢量/元数据）。
- AI 复核输出（`blindCheckReview.cjs:23-83`）：
  `{summary, clauses:[{id:c1.., requirement, basis(逐字摘录招标原文), status: pass|fail|unknown, explanation, issues[]}], candidates:[{id:"g1"|"g1#0", verdict: problem|not_problem|uncertain, reason}], issues:[{id:a1.., title(≤80), severity, category, basis(≤300), detail, suggestion, locations:[{target,text,image_part?:alt|content}]}]}`
  校验：编号唯一、basis 必须在招标原文中逐字可匹配、fail 必须挂问题编号、候选必须全覆盖、人工条款必须被条款对照覆盖（`:253-320`）。
- 自动修复：只修白名单 `FIX_RULE_IDS`（`blindCheckPresets.cjs:202-211`）；默认不勾选"段前分页、对齐网格、英文标点、多余空格、分栏"（`:210`）。修复**只改副本**，原文件不动；修复后重新检查并给 `validation {checked, original_errors, fixed_errors}` 与前后汇总（`blindCheckFixTask.cjs:17-34, 44-50`）。

### 4.3 查重（duplicate-check）——确定性算法 + AI 辅助提取

- **元数据**：可比较 key 白名单（`duplicateCheckService.cjs:93-111`），覆盖 OOXML 属性、RSID、OLE、PDF 头/对象/增量更新/签名/附件等；日期类另判 `date_comparable`（`:112`）。
- **目录提取**：`source` 与置信度——目录行 `catalog=0.92`、标题 `heading=0.82`、语义行 `semantic=0.68/0.55`（`:1253-1281`）。
- **目录重复**：同 `normalized_path` → group score `1`；同 `normalized_title` → `0.95`；跨文件标题 bigram 相似度 `≥0.86` 且层级差 `≤1` → `similar`（`:1403-1416`）。
- **两两相似度**：`score = path_overlap*0.45 + title_overlap*0.35 + order_similarity*0.2`（LCS）；风险 `high≥0.75 / medium≥0.55 / low≥0.35 / none`（`:1361-1364, 1436-1445`）。
- **正文重复句**：
  - 分句：`。！？!?` 必切；`；;` 仅在当前累计 ≥20 字时切（`:1708-1728`）。
  - 有效句：去编号/控制字符后，有效字母数字汉字 ≥4；≥12 字即有效；6-11 字需含冒号+中文/字母，或含单位（日历天/个月/万元/GHz/MB/kg/mm/天/年/元/% 等）（`:1730-1740`）。
  - 超过 600 字截断存证（`:1750`）。
- **剔除招标原文自身句子**（避免把抄招标当重复）：匹配器逐级放宽——exact → strict（去空白标点+NFKC）→ field（可识别格式字段+安全尾部）→ skeleton（日期/编号/金额/百分比/分数/页码/数字占位化）→ near（字符 bigram containment/dice/长度比：正文 ≥30 字用 0.90/0.82/0.5，短句用 0.95/0.88/0.55）（`:1836-1990`）。
- **图片**：按 hash 归组，记录每文件出现次数与位置。
- 结果 id 形式：句子 `S000001`，目录组 `G0001`（`:2000-2005, 1387`）。

### 4.4 可研报告检查

- 目录模板 7 套：政府投资、企业投资、工业与高端制造、高新技术与数字化/信息化、基础设施与公用事业、农业与生态环保、商业/园区与地产（`feasibilityReportPrompts.cjs:2-160`）。
- 流程：资料分段分析（9 个固定 JSON 维度）→ 关键参数提取 → 按章写作；默认目标 30000 字。
- 改写保护：数量词（亿元/万元/%/年/月/天/平方米/吨/千瓦时/人/户/项/套…）与 `【待补充】/【待确认】` 计数不得减少，否则整段回退原文（`feasibilityReportTasks.cjs:15-60`）。

### 4.5 一致性 / 错别字 / 编号 覆盖情况

| 能力 | 归属 | 说明 |
|---|---|---|
| 全文事实一致性 | 正文生成 `auditing` 阶段 | 必做，两类问题 + 9 类事实台账 |
| 错别字 | 废标项检查 tab | AI，高置信度，程序定位校验 |
| 逻辑/前后矛盾 | 废标项检查 tab | AI，含跨章节参数不一致 |
| 标题编号连续性/层级跳跃 | 暗标 `consistency.heading_numbering/heading_level_jump` | C# 确定性 |
| 目录编号 | 技术方案大纲 | 程序按树位置重算，不信任模型编号 |
| 页码引用一致性 | 导出 | 锚点书签 + PAGEREF，缺失写"【待填写：页码】" |

---

## 5. 文档导出

### 5.1 导出格式配置（`ExportFormatConfig`）

文件：`client/src/shared/types/exportFormat.ts:180-233`，默认值 `client/electron/services/exportFormatDefaults.cjs:10-110`。

顶层 8 块：`template_name`、`page`、`heading_level1_page_break_before`、`heading_border`、`headings[6]`、`body_text`、`table`、`image`。

- `page`：纸张 a4/a3/a5/b4/b5/letter/legal/16k、方向、双栏、首页不同、四边距 cm、页眉/页脚（8 种装饰样式 `plain|band|rules|top-bar|footer-badge|slant|letterhead|frame`、文字/字体/字号/对齐/颜色/装饰带高度/文字偏移/强调色）、页码格式 `第{page}页`、起始页、补零 0|2|3。
- `headings[i]`：字体、中文字号名、对齐、粗体、颜色、段前段后 pt、首行缩进字符、行距倍数、编号格式 `outline-decimal|custom`、编号模板（支持 `{zh}{num}{tail}{tail1..6}{full}{circled}`）。默认 1 级"第{zh}章"、2 级"第{zh}节"、其余 `{tail}`。
- `heading_border`：上下/左右结构、边框色、6 级单元格底色（默认 `#eef5ff #f3f7ff #f8fbff #fbfdff #ffffff #ffffff`）、标题上下留白。
- `body_text`：字体/字号/对齐/段前段后（lines|pt）/首行缩进/行距 6 模式（single、one-and-half、double、at-least、exact、multiple）/9 种无序符号/9 种有序编号。
- `table`：边框宽/色、单元格内边距、满宽、表题样式；表头行/首列/正文单元格三套（字体/字号/对齐/文字色/底色）。
- `image`：最大宽度百分比（默认 90）、对齐、图注样式。
- `export_template_scope`：`ai-only` 仅 AI 生成目录套模板；`document` 整个文件套模板；页面基础布局始终全文件（schema 注释 `:89`）。

系统预设 3 套：`tpl-system-standard-bid`（标准投标简版）、`tpl-system-a4-visual`（A4 图文版）、`tpl-system-a3-landscape-visual`（A3 横版图文）；`template_id 一经发布不可改`（`systemExportTemplates.cjs:9,63,134,220`）。

### 5.2 DOCX 生成（OpenXmlHelper / C#）

- 渲染入口：`Jobs/RenderRestrictedHtmlDocxAction.cs`；受限 HTML 渲染器 `RestrictedHtmlDocumentRenderer.cs`（2424 行）、插入器 `RestrictedHtmlWordInserter.cs`（925 行）。
- 支持标签：顶层 `p/ol/ul/figure/table`，行内 `strong/em/u/sup/sub/br`；表格 `data-yb-preset` 四预设，`caption/thead/tbody/th/td`，`rowspan/colspan` 正整数；图片 `figure > img[data-yb-asset-ref] + figcaption + template[data-yb-role=prompt]`（`受限HTML生成规范.md`）。
- 样式由配置注入：标题样式、正文、表格三套单元格、图注、章节边框/页框、页眉页脚装饰（`ApplyFormatting` / `ApplyTables` / `ApplyChapterFrames`，`RestrictedHtmlDocumentRenderer.cs:1141-1830`）。
- 商务模板：非 AI 叶子按节点 ID 并入模板章节，按模板原样导出；无对应章节保留占位提示（`technicalPlanExport.cjs:44-49`）。
- 导出编号：目录生成小节及其之前的一级节点为"前置页"不编号，后续编号首段减去前置页数；目录标题不编号（`:120-126`）。

### 5.3 目录（TOC）与页码

- 目录只在**第一个** `directory-generate` 叶子生成；多余目录叶子保留"待填写"提示（`technicalPlanExport.cjs:49,117-119,179-181`）。
- 正文写 `<p>YIBIAOTOC</p>` 标记 → C# 换成 SDT 内容控件 + 原生 TOC 域：` TOC \o "1-9" \h \z \u `（`Jobs/TableOfContentsBuilder.cs:20-35, 78-80`）。
- 目录条目按 Word 大纲级别 0-8 收集（含表格内，不含文本框和目录自身）；目录之前的封面等前置页不进目录（`:245-277`）。
- 生成 TOC1-TOC9 与 TOC Heading 样式；目录标题继承同级标题样式但取消大纲级别（`:173-210`）。
- 页码先写等宽占位（4 位）→ Main 用 Word 测得书签页码 → `fill-toc-page-numbers` 回填；同时导出"只含目录页"的小文档复测目录占几页，多轮迭代（`:32-34, 131-160`；`technicalPlanToc.cjs:22-60`）。
- 正文页码引用：`{{YBPAGE:节点ID}}` 或合并相邻小节的 `{{YBPAGE:起点ID:终点ID}}`，可选 `|text` 文字写法；C# 换成 IF+PAGEREF 域，起止同页显示一个数，否则"起-止"；`|text` 显示"第3页/第3页至第7页"；缺书签写 `【待填写：页码】`（`Jobs/PageReferenceBuilder.cs:16-21, 33-35, 96-160`）。
- 书签区间约定：`_YbPS…`(start)/`_YbPE…`(end)，避开格式自检书签(0 起)、商务模板(+1,000,000)、目录(2,000,000 起)。

### 5.4 双层 PDF

`client/electron/services/dualLayerPdf/*`：
- 源文件 `.docx/.doc/.wps`；后端按平台探测 Microsoft Word / WPS / LibreOffice，`.wps` 优先 WPS；逐个文件导出**单层 PDF**（`officePdfExport.mjs:61-99, 177-198`）。
- 隐藏 BrowserWindow 逐页渲染页面图并提取文字框（含 OCR 依赖 `ocr-v1`，可选安装/更新）（`dualLayerPdfService.cjs:9-23, 33-40`；`workerWindow.cjs:7-66`）。
- `pdfWriter.cjs`：每页一张整页图 + 不可见文字层；文字用 `3 Tr`（不可见渲染）、无字形字体 + `ToUnicode` CMap，按 UTF-16 编码，横向缩放对齐原文字宽度；流式逐页写，内存与页数无关；带书签与 Info（`pdfWriter.cjs:1-15, 97-118`）。
- 输出可选：`pdf`（可搜索可复制带书签）、`image`（每页一张 JPG，放在同名文件夹）；输出目录默认源文件目录；状态只在内存、重启不恢复（`dualLayerPdfService.cjs:19-23`；页面 `client/src/features/dual-layer-pdf/pages/*.tsx:7-11`）。

### 5.5 约束

- 导出前必须通过 HTML 结构预检（缺正文、坏图片引用、结构不完整会阻止/回退修复）。
- 无法导出的单张图片：原位改文字提示并记 warning，不中断整本导出（`technicalPlanExport.cjs:88-90`）。
- 规则页码小节未编制时导出会给 warning"请先编制后再导出"（`:282`）。

---

## 6. 弱电 / 行业知识：**基本不存在**

证据（全仓 grep，排除 node_modules/.git）：
- `弱电`、`综合布线`、`楼宇自控`、`门禁`：**0 命中**。
- `智能化`：仅 1 处，出现在可研大纲的一个二级标题「设备自动化与智能化」（`feasibilityReportPrompts.cjs:72`）。
- `机房`：仅 1 处，是受限 HTML 规范的图片示例文案（`client/electron/resources/content-generation/受限HTML生成规范.md:97-99`）。
- `安防/监控`：无行业模板命中。

因此仓库**没有**弱电/智能化行业的章节骨架、设备参数库、品牌库、检查清单或评分模板。存在的只有通用资产：

| 资产 | 路径 | 性质 |
|---|---|---|
| 7 套可研通用大纲 | `client/electron/services/feasibilityReportPrompts.cjs:2-160` | 硬编码常量（政府/企业/工业/高科技/基础设施/农业/园区） |
| 配图类型对照表（45 类） | `client/electron/resources/content-generation/配图类型对照表.md` | 配置数据：mermaid 3 类、html 32 类（甘特/时间轴/泳道/拓扑/平面布置/各类图表/组织架构…）、ai 12 类（实物/场景/效果/剖视/爆炸/原理/工艺/操作…） |
| 受限 HTML 规范 / 正文模板 | `.../受限HTML生成规范.md`、`.../正文模板.html` | 样式与结构约束，非行业内容 |
| 3 套系统导出模板 | `client/electron/services/systemExportTemplates.cjs` | 排版模板 |

结论：重实现时行业知识必须自建（建议做成可插拔 YAML/JSON：章节骨架、参数表、评分对齐规则、检查清单），不要期待从本项目迁移。

---

## 7. 值得借鉴的产品细节（CLI-first MVP）

1. **事实先行的强人工确认**
   - 全局事实独立成页、可编辑、可用自然语言让 AI 调整；调整后自动作废正文（`globalFactsAdjustmentTask.cjs:110-116`）。
   - 目录阶段最多 3 处 ask-user（评分挂载、叶子数偏离、最终审核），且选项固定、第一项为推荐项。
   - MVP 建议：`bidcraft facts review` 输出 diff + 逐项 accept/edit/reject；事实哈希写入后续生成元数据，事实变更即使正文失效。

2. **缺失值三模式是产品级抽象**
   - `fabricate / omit / placeholder` 一套枚举贯穿事实、正文、导出；`【待填写】` 作为可 grep 的交付前检查项。
   - MVP 建议：默认 `placeholder`，`bidcraft lint` 统计 `【待填写】` 数量并阻断导出。

3. **Issue → Fix 闭环**
   - 废标：finding 自带 `requirement / bidEvidence / riskReason / suggestion`，天然可生成修复建议。
   - 暗标：候选(candidate) → AI 判定(verdict) → 问题(a#) → 修复项白名单 → 只改副本 → 修复后复查 + 逐条对账。
   - MVP 建议：统一 `Finding{id, rule, severity, evidence, suggestion, status}` + `fix --dry-run` 输出补丁清单。

4. **证据/证明材料库**
   - 资信库按 `owner_type/owner_id/field_key` 挂图片，支持水印；人员/业绩/证书结构化；全局事实只取白名单字段，避免敏感信息进正文。
   - MVP 建议：`credentials.yaml` + `assets/`，生成时以 `credential_ref` 引用，导出前校验文件存在。

5. **知识库复用（无向量检索）**
   - 文档 → block（语义合并目标 500 字，超大块 8000 字再切）→ 过滤（空/页码/<100 字/目录/重复页眉页脚≥3 次/封面/签章页）→ 首轮条目提取 → 补漏 → 合并候选 → 条目→block 匹配 → 遗漏段落补漏（最多 2 次）→ 报告 coverage/matched（`knowledgeBaseService.cjs:14-18, 336-370, 1016-1037`）。
   - 搜索是 SQLite `instr(lower(...))` 子串匹配 + 字段优先级，**没有 embedding/向量**（`knowledgeBaseStore.cjs:256-300`）；匹配按上下文自动分段，忽略 batchSize（`:2250-2259`）。
   - MVP 建议：先做 FTS5/BM25，条目级引用（`documentId::itemId`）即可；向量检索作为二期。

6. **导出可追溯**
   - 无"导出历史"表；只有 `task_logs` 与检查结果 Excel。导出警告（缺页码、缺规则页码、多余目录、图片降级）随结果返回。
   - MVP 建议：`exports` 表记录 template_id/config_hash/outline_hash/facts_hash/warnings，支持复现。

7. **其它可直接借用的约束**
   - 目录编号永不由模型决定；节点身份 UUID + `number` 派生。
   - 评审/事实/正文都强调"不得编造具体参数/业绩/承诺"，编造边界写进 prompt 与检查规则。
   - 所有长任务：`status/progress/stats/logs/pause_requested` 四件套 + 断点续跑（`knowledge_document_steps`、`*_match_batches`、Agent `prompted_stage`）。
   - 商务/规则页码章节用"模板填写 + 规则表"而不是 AI 正文，避免法律性内容被改写。

---

## 8. 重实现时的最小领域接口（建议）

```
TenderDoc{files[], markdown, sections[]}
BidAnalysis{items: map<itemId,{label,output,content}>}
GlobalFact{id,title,content}                 // groups[]
OutlineNode{id,parentId,sort,level,title,description,contentMode,ruleBasis,content}
ContentPlan{nodeId,writingFocus,targetWords,table{needed,purpose},knowledgeIds[],imageScore}
CheckFinding{id,source:'rejection'|'blind'|'duplicate'|'consistency'|'typo'|'logic',
             severity:'high'|'medium'|'low', category, title, evidence, suggestion, status}
ExportConfig{page,headings[6],headingBorder,body,table,image,scope}
```
所有 prompt 常量应外置为可版本化模板；`【待填写】`、`【待补充】`、`【待确认】` 作为全局保留标记。
