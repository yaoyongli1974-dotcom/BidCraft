# 变更记录

按版本倒序。对外口径以 `docs/release-notes/` 下对应文件为准；本文件是仓库内的简明索引。

## v0.31.3 (2026-11-25)

生产上线前最后验收与风险收口，对外说明见 [docs/release-notes/v0.31.3.md](docs/release-notes/v0.31.3.md)，逐阶段证据与四类结论见 `docs/development/BIDCRAFT_V0313_*.md`。

- 真实 PostgreSQL 16.2 复现并修复 `refund_claimed == 0` 在 BOOLEAN 列上的退款失败；100 轮 Reaper/心跳与终态竞争、50 轮退款/对账竞争全部通过。
- 8 类 Worker 真实 PG 运行时矩阵、导出归档失败清理、图片/知识副作用边界与迁移/权限/IDOR 验证完成。
- CI 纳入全部 `tests/postgres/**`（tests≥900、skipped=0），安全门禁改为强制，容器健康检查与 compose 环境变量透传补齐。
- 本地 SQLite 2125 passed / 18 skipped；真实 PG 901 passed / 1 skipped；历史迁移零改动，无测试删除。

## v0.31.2 (2026-11-25)

并发可靠性验证与残余风险专项收官，对外说明见 [docs/release-notes/v0.31.2.md](docs/release-notes/v0.31.2.md)，逐阶段证据与最终验收矩阵见 `docs/development/BIDCRAFT_V0312_*.md`。

- `commit_fenced_result` 禁止 NULL / 过期租约提交终态；Reaper 与章节写入升级为真实多线程 / 独立 Session / 条件 UPDATE 证据（100 轮 × 多场景）。
- GenerationLease + Job claim 双重 fencing 在单一短事务内提交章节，重复写入幂等；8 类 Worker 补状态守卫，取消任务不再被复活。
- 账务 6 场景 × 50 轮真实并发验证金额守恒；并发失败唯一终态、退款领取失败可重试。
- 质量指标新增 coverage / samples / mismatches 明细，旧比率口径不变；真实件样本仍保持「未测量」。
- 后端全量 2125 passed / 18 skipped，前端 39 files / 320 tests；历史迁移零改动，无测试删除。

## v0.31.1 (2026-11-24)

并发可靠性热修复，对外说明见 [docs/release-notes/v0.31.1.md](docs/release-notes/v0.31.1.md)，逐阶段证据见 `docs/development/BIDCRAFT_V0311_*.md`。

- 修复生成租约心跳调用缺陷，续租走独立事务并在失效/连续失败时中止 worker 与同步生成。
- Reaper 重试上限处使用扫描时点租约判定，100 轮竞争不误杀活跃任务；过期生成租约统一清理。
- 章节级 fencing 短事务写入：旧 worker 不能写正文或记完成账；动态矩阵覆盖全部 8 类 runner。
- 账务终态幂等修复与四类竞争场景回归；判定一致率支持真实应答文本，未标注时明示未测量。

## v0.31.0 (2026-11-24)

并发可靠性、账务一致性与真实投标质量治理专项收官，对外说明见 [docs/release-notes/v0.31.0.md](docs/release-notes/v0.31.0.md)，逐阶段证据与最终验收矩阵见 `docs/development/BIDCRAFT_V031_*.md`。

- Worker 终态写入全部改为事务级 fencing（`commit_fenced_result` / `finalize_job_result`），Reaper 条件迁移与中止检查点覆盖 8 类任务。
- 账务新增异步生成 marker、done_steps 退款归因、付款账号兜底与同项目生成互斥租约（Alembic `0012_generation_lease`）；冲突返回 409，历史迁移未改。
- 真实招标文件数据集契约框架与未测量口径落地；质量报告摘要与前端统一展示过程/真实件六项指标。
- 备份恢复手册与演练检查补齐；本地后端 1669 passed / 18 skipped，前端 39 files / 320 tests。

## v0.30.0 (2026-11-24)

十阶段质量治理收官，对外说明见 [docs/release-notes/v0.30.0.md](docs/release-notes/v0.30.0.md)，逐阶段证据与最终验收矩阵见 `docs/development/BIDCRAFT_V030_*.md`。

- CI 拆分为 lint/tests/web/PG/DOCX/Docker/安全共 9 个 job，真实 PG 数据边界与并发压测、CJK 严格渲染门禁、JUnit 报告与失败摘要；字体与 gitleaks 误报按精确方式修复，不放宽门禁。
- 依赖安全：Vite 8 / Vitest 5 / Router 7 等已评估升级，npm audit 14→0；Bandit 全树扫描 + 按文件行号的精确豁免。
- 账务与任务队列：新增 Alembic `0011_stage3_accounting`；同步生成心跳、按已写章节差额退款、DB 条件认领、fence 清除与 worker 幂等/中止检查点。
- DOCX 表格分类与资源上限；4 个合成回归 fixture + 值/表格/材料基线门禁；质量指标入库、`judge_for_project` N+1 消除。
- 前端查重契约与统计口径修复、跨项目工作流审批入口、KB 文档/检索预览；统一任务日志字段与 10 项全局指标，新增 `/health/live`、`/health/ready`。
- 容器内置 pg_dump、健康检查与 CPU/PID/cap 加固（不加 env_file），CI 增加镜像构建与 Alembic preflight。
- 后端 1620 passed / 16 skipped；前端 39 files / 319 tests；远端 CI 9/9 成功。

## v0.29.0 (2026-11-24)

对标易标专项改造（八批）收官，对外说明见 [docs/release-notes/v0.29.0.md](docs/release-notes/v0.29.0.md)，逐批证据见 [docs/development/BIDCRAFT_YIBIAO_REFACTOR_REPORT.md](docs/development/BIDCRAFT_YIBIAO_REFACTOR_REPORT.md)。

- 新增 Alembic `0010_batch1_billing_reliability`：失败终态幂等退款、条件占claim、TTL 活跃性、付款账号与历史 owner 回填；历史迁移未改。
- 判定引擎修复业绩时间窗继承、`和/与/及` 拆分与参数数值比较；API verdict 与 /check 同口径，证据节点化与可追溯字段补齐。
- 生成链路：台账比例截断、prompt token 预算、评分点段级绑定、失败/短章恢复、大纲 upsert 与评分去重。
- DOCX 专业渲染器接入导出；包完整性校验、确定性字节、字体/超链接、商务标模板与强制门禁；CLI/MCP 渠道对齐。
- 商务标多包件与共享资产、报价删除保护/冻结币种、多包件评估；知识库生命周期过滤与页码来源追踪、异步相似度；Agent 活跃时钟预算与评估幂等；前端代理与契约测试。
- 后端 1499 passed / 14 skipped；前端 35 files / 288 tests；ruff 全绿。

## v0.28.1 (2026-11-23)

v0.28.0 之后的第二轮质量加固与运维补齐，对外说明见 [docs/release-notes/v0.28.1.md](docs/release-notes/v0.28.1.md)。

- 新增 Alembic `0009_v028_schema_parity`，补齐插件/需求列与 `jobs.owner_id` 索引回填、pending_charges 表；PG 旧 schema 升级路径有 opt-in 测试。
- 扣费对账闭环：TTL 配置、周期扫描与 `bidcraft reconcile-charges` CLI；VIP 非整除退款精度修正。
- 审批通过真正续跑工作流、同步评估返回完整 findings；限流 TTL+LRU、CSRF 代理配置、备份哈希清单与恢复演练、nginx 限流示例。
- 前端 6 个真实全局指标键与状态中文映射、插件 scope 往返、导出模板 UI；新 CLI/环境变量与文档同步。
- 后端 1361 passed / 13 skipped；前端 33 files / 282 tests；ruff 全绿。

## v0.28.0 (2026-11-23)

v0.27.0 之后的能力收口与质量加固，对外说明见 [docs/release-notes/v0.28.0.md](docs/release-notes/v0.28.0.md)。

- 真实件基线工具（指纹核对/基线目录/CLI）与实质性条款同义/财务大写数字容错。
- 插件出站地址防护、scope 归属隔离、审计归属与可选 Ed25519 签名验签。
- 评估异步 `run-async`、商务完整性联动与可选送审硬门禁；历史报告 findings 按需加载。
- 可选 LLM 语义匹配器（`BIDCRAFT_VERDICT_LLM=1`），带缓存、预算与自动回退。
- 三方分析的全量遗留问题修复（性能/安全/产品文档）；新增 Alembic `0008_job_queue_indexes`。
- 后端 1323 passed / 11 skipped；前端 30 files / 262 tests；ruff 全绿。

## v0.27.0 (2026-11-23)

对标易标第四批（收官）：投标质量评估中心（evaluation 模块：六维评分/硬性风险一票否决/四类覆盖聚合/风险摘要/不可变报告快照 + finding 人工处置）。新增 Alembic `0007_yibiao_batch4`（2 表，downgrade 可执行）。前端新增质量评估 tab。

本版同时收口三批已落地能力的文档口径与前端可用性：企业知识库 2.0（企业/成员、增量索引、共享资质与业绩）、商务标自动编制（approved 报价汇编 + 可选完整性硬门禁）、多维查重（文本/结构/语义/图片，含可选 LLM 语义匹配器）、Agent 编排（标准/全流程/投标三种类型、审批四眼原则：提交人不得自审、超管可代审）、章节协作（锁/修订/分工）与质量评估中心（同步与 `run-async` 双入口、历史报告按需加载 findings、处置意见与处置人留痕、可重新打开）。`.bidplugin` 支持 Ed25519 签名验签；管理端新增「运行状态」页读取完整 `/admin/health` 并展示运营指标口径，计费页可在界面开具/驳回发票。API 路由模块现为 18 个，`uv.lock` 锁定 80 个包。

验证：后端 1300+ passed / 前端 262 tests（typecheck 通过）；新增 `tests/test_ux_fix_approval.py` 锁定审批四眼原则。

## v0.26.0 (2026-11-22)

对标易标第三批：AI Agent 编排（注册表/DAG 工作流/编排器/人工审批闸门，复用任务队列与 fencing，审批不占 Job 租约；ModelClient.complete_with_tools）+ 项目协作与版本管理（章节编辑锁、修订历史与 diff/恢复、章节分工）。新增 Alembic `0006_yibiao_batch3`（6 表，downgrade 可执行）。前端新增 Agent 编排 tab 与章节协作入口。后端 1209 passed / 前端 232 tests。

## v0.25.0 (2026-11-22)

对标易标第二批：企业知识库 2.0（企业/成员、KbDocument 增量索引、企业共享资质/人员/业绩）、商务标自动编制（确定性汇编、approved 报价口径、复用审批链、XLSX/DOCX 导出）、多维查重（文本/结构/语义/图片四维、报告命中落库与复核）。新增 Alembic `0005_yibiao_batch2`（6 表 + 可空列，downgrade 可执行）。前端新增企业/商务/查重三 tab。后端 1178 passed / 前端 224 tests。

## v0.24.0 (2026-11-22)

对标易标专项改造第一批：修复 PDF 导出漏传模板、needs 死字段、全局知识库被排除三个确定性缺陷；新增只读评分覆盖矩阵（后端端点 + 前端区块）；新增 DOCX 专业文档引擎（Document IR / 模板解析 / 渲染 / 导出后校验）。无数据库结构变更。验证 1110 pytest / 195 web。

## v0.23.0 (2026-11-22)

判定引擎边界加固（R16–R21，任务书驱动）。

- R16 复合条款拆分后携带完整原始要求做项目类型过滤，无关项目不计有效业绩。
- R17 年份精度起始/截止边界转人工复核，月精度不臆造日期，日精度按真实日历比较。
- R18 正文修改使确认失效时，用 append-only 审计保留完整判定与人工确认快照（含正文 revision/哈希/操作人），同事务回滚。
- R19 业绩证据增加项目身份（合同编号/归一化名/客户）与来源状态，冲突转人工、缺失不伪造。
- R20 判定回归集 16→26 条（含时间窗/类型样本），新增 FAR/FRR/四态准确率/复核率/关联错误数指标。
- R21 单位与主体关联边界测试（元/万元、主备链路、多主体无标点、source_span 定位）。
- 无 DB 变更、无 API 结构变化。后端 1076 passed / 8 skipped，web 191 tests，PG 8/8，Docker 0。

## v0.22.0 (2026-11-22)

判定引擎可靠性、日期精度、证据关联与质量回归（R08–R15，任务书驱动）。

- R08 复合条款不再绕过业绩截止日期校验：统一判定逻辑下沉到每个原子。
- R09 拆分歧义识别：连接词出现≠歧义，转折词才转人工；共享主体不机械拆分。
- R10 业绩证据归属：PerformanceEvidence 结构、项目名/类型匹配、同项目去重、日期类型区分。
- R11 约束主体局部定位：标点+连接词双边界，主备链路归因。
- R12 日期精度与日历边界：year/month/day 不臆造、日历月回退、闰年/月末/边界转人工。
- R13 单位规则回归：补单位登记、中文「两」、浮点容差、注册表自检。
- R14 判定质量回归体系：16 条样本 + 混淆矩阵 + FAR=0.0，纳入 CI。
- R15 导出审计闭环：正文修改使已确认条款失效，全导出路径统一门禁。
- 验证：1028 passed / 8 skipped；前端 191 tests；PG 集成 8/8；Docker build 通过。

## v0.21.0 (2026-11-22)

判定引擎深度改造（R01–R07，任务书驱动）。

- R01 复合条款原子拆分闭环：连接词拆分 + 原子独立判定 + 歧义进入 judge 最终判定，杜绝「一个原子满足即整条满足」。
- R02 多约束主体关联：每条约束按局部子句独立推断 family，禁止不同指标相互替代。
- R03 业绩时间窗按招标截止日期（事实台账 + meta）实际校验，逐项计算、过期不计、缺日期转人工复核。
- R04 单位提取完整性：方向正则复用完整单位表 + 二进制容量单位，禁止跨量纲比较。
- R05 requirements API 消除 N+1：ProjectRequirementContext 单请求共享大纲与 node_map。
- R06 CI 严格 npm ci + 真实 PostgreSQL service container 集成 job（禁止静默跳过）。
- R07 export.force 审计移到门禁之后（失败不留成功审计）；★ 最终确认权限收紧到 owner/admin。

验证：979 passed / 8 skipped，web lint/test/build 全绿，PG 集成 8/8，docker build exit 0。

## v0.20.1 (2026-11-22)

T01–T08 任务书改造（可靠性 / 可重复性收尾）。

- T01 导出门禁验收：★ 未确认阻止导出、强制导出独立权限（editor 403）+ 原因必填 + 完整审计，人工确认后 SUB 阻断消失。
- T02 单位判断改三态 `unit_relation()`（compatible/incompatible/unknown），新增存储容量量纲，禁止跨量纲比较，单位缺失转人工复核。
- T03 `split_atoms()` 保留原文/编号/归属映射，连接词歧义标记，支持转人工复核。
- T04 `_evaluate_one()` 多候选按主体关联归因，冲突转 `unclear`，不再取第一个/最大值。
- T05 时间窗与业绩对象/完成日期关联，缺日期转人工复核，一处短窗不否定全部业绩。
- T06 `/requirements` 单请求内大纲预加载共享，消除 N+1，请求间隔离、更新后失效。
- T07 PG 迁移咨询锁获取失败改为阻止迁移（fail-closed），不再降级文件锁。
- T08 Dockerfile 改严格 `npm ci`；对齐 package-lock 版本；Docker 构建通过。

验证：946 passed / 8 skipped，web lint/test/build 全绿，docker build exit 0。

## v0.20.0 (2026-11-22)

两个 P0：复核按重要性分级（★ 未确认不得交付、独立授权 + 审计）；单位量纲比较（禁止跨量纲）。931 passed。

## v0.19.0 (2026-11-22)

实质性条款五层判定引擎：四态结论 + 逐层证据 + 可插拔语义层。924 passed。

## v0.18.0

实质性条款约束比较：数值/等级/否定逐条判定，约束否决优先于语义相似。908 passed。

## v0.17.0

PostgreSQL 双后端支持：方言分派、咨询锁、pg_dump 备份、外键删除收口。879 passed。

更早版本见 `docs/release-notes/`（v0.4.8 – v0.16.0）。
