# v0.32 数据库迁移报告（Phase 7）

> 分支：`feature/v0.32-ai-engine`；基线 Alembic head `0012_generation_lease`
> 现状：新增 3 个 revision，**单 head = `0015_fact_confirmation`**；0001-0012 零改动（无编辑、无重排、无回填）。
> 设计纪律：中文注释、可空、无 server_default、inspect 判存幂等、可逆；历史行 `NULL` 一律保留 legacy 语义，禁止用迁移把旧数据伪装成「已审核 / 已人工确认」。

## 1. 迁移清单

| Revision | Down | 新增对象 | 兼容语义 |
| --- | --- | --- | --- |
| `0013_chapter_planning` | `0012_generation_lease` | 表 `chapter_plans`：31 列（全部带中文 `comment=`）、唯一约束 `uq_chapter_plan_project_node_version(project_id, node_id, version)`、复合索引 `(project_id, node_id, status)` 与 `(project_id, package_id)`，以及 `project_id`/`node_id`/`status` 单列索引 | 只追加，不改既有大纲/章节表；旧章节无规划记录时按模式兼容放行 |
| `0014_kb_asset_metadata` | `0013_chapter_planning` | `kb_documents` 增加 7 个可空列：`structured`、`review_status`、`reviewed_by`、`reviewed_at`、`published_at`、`rejected_reason`、`source_file_id`；新增 `kb_asset_duplicate_groups`、`kb_asset_duplicate_members` 两张表及唯一约束/索引 | `review_status IS NULL` = legacy 文档，继续按原检索规则进入检索；未审核新状态不进入检索 |
| `0015_fact_confirmation` | `0014_kb_asset_metadata` | `facts` 增加 2 列：`confirmed_by VARCHAR(80)`、`confirmed_at DATETIME`，均可空且**无 server_default** | `NULL` = 未人工确认，不因迁移把批量抽取事实标成已确认 |

三者的 `upgrade()` / `downgrade()` 均为中文注释、先 `inspect(bind)` 判存再操作；PostgreSQL 上表/索引带 `IF (NOT) EXISTS`，SQLite 仅靠判存守卫；重复执行为空操作。`create_all` 与「老库先 stamp `0001_baseline` 再 upgrade head」两条路径都被覆盖。

## 2. 逐项说明

### 2.1 0013_chapter_planning

- 记录规划版本、状态机与引用：`score_points`/`requirements`/`knowledge_refs`/`fact_refs` 等 JSON 字段只接受真实 ID（服务层 `generation/chapter_plan.py::validate_chapter_plan` 校验，伪造/跨项目/跨标段写入 `validation_errors`）。
- `raw_response` 保存模型原始返回或确定性回退说明，不伪造模型结论；`prompt_version` 记录 `chapter_plan_v1/v2/v3` 演进。
- `update_chapter_plan`/`regenerate_chapter_plan` 追加新版本并使旧确认失效；唯一约束保证同章节同版本只有一行。
- downgrade：先按逆序删 5 个索引（SQLite 不允许删除仍被索引引用的对象），再删表；唯一约束随表消失，不触碰其它表。

### 2.2 0014_kb_asset_metadata

- 结构化资产挂在既有 `KbDocument`/`KbChunk`/`KnowledgeBase` 上，不新建第二套文库；`source_file_id` 必须是真实来源文件 ID。
- 审核状态机 draft → review → approved → published（以及 rejected/archived）；`knowledge._ready_chunk_filter()` 只放行 legacy(NULL) 与 published 块，其余状态不进入检索。
- `structured` 为确定性正则提炼结果（非模型生成）；重复候选按同库内 sha256 与规范化题录/证号聚合，跨企业去重未启用（避免越权暴露）。
- downgrade：先删两张新表（逆序删索引），再仅删除 `kb_documents` 上本次新增的 7 列；原始 KB 文档/块与其内容不受影响。

### 2.3 0015_fact_confirmation

- 两列由服务端在人工确认时盖章（`ledger.update_fact` 写 `confirmed_by`/`confirmed_at`），客户端无法自报；`confirmed` / `manual` 状态必须有真实 `SourceFile` + 文本内 `source_quote` + 合法页码。
- 不设默认值是关键：`NULL` 明确表达「未经人工确认」，迁移本身不得赋予确认语义。
- `save_ledger(..., force=False)` 保护已确认行；force 覆盖时清空确认戳并在同事务写 `fact.update` 审计。事实值/单位变化后引用章节回到 `review`、确认失效并写 `fact.invalidation`。
- downgrade：只移除这两列；既有事实值与来源列不动，无历史数据回填需求。

## 3. Legacy NULL 与 no-default 语义（汇总）

| 场景 | 旧值/迁移后 | 运行期解释 |
| --- | --- | --- |
| `kb_documents.review_status IS NULL` | 历史行不写默认值 | 视为 legacy，保持 v0.31.3 的检索行为，不被新状态机误挡 |
| `facts.confirmed_by/at IS NULL` | 两列可空且无默认值 | 视为未人工确认；前端不补 confirmed=true、后端不伪造确认人 |
| `chapter_plans` 无记录的历史章节 | 旧章节从未规划 | `review` 默认放行并记 warning；`review_strict` 才强制阻断 |
| 检索可信/降级信号缺失 | `trust_tier=None` / `degraded=None` | 保留 null（未知），不映射为 0/trusted/未降级 |

## 4. Downgrade 顺序与回滚边界

严格逆序：`0015_fact_confirmation` → `0014_kb_asset_metadata` → `0013_chapter_planning` → `0012_generation_lease`。每步只删除该 revision 自己新增的对象，不触碰历史迁移数据；业务侧确认信息、规划数据与结构化资产会随对应列/表删除，均无需历史回填。数据库回滚前仍应先执行 `bidcraft backup` + `bidcraft backup-verify`（见 `deploy/README.md`）。

## 5. 历史迁移零改动

- `git diff --name-only` 中不含 `bidcraft/migrations/versions/0001_*.py` … `0012_*.py`；三份新迁移只把 `down_revision` 指向既有链尾，未改任何历史文件、未改写旧 revision 内容或顺序。
- CI 门禁保持：AST 扫描断言恰好一个 head（当前 `0015_fact_confirmation`）、Alembic preflight 静态编译 + 临时 SQLite scratch 库执行、版本一致性脚本校验后端/Web/lock/CHANGELOG。
- 单 head 同步证据：`tests/test_alembic.py`（0013/0014/0015 可逆性断言）、`tests/test_api_package.py`（路由 274 / 请求模型 93）与 `tests/test_v030_stage8_observability.py` 同步更新。

## 6. 验证状态与占位

- **SQLite 可逆性已验证**：`tests/test_alembic.py` 覆盖 0013/0014/0015 的 upgrade/downgrade 往返、legacy NULL 语义与 no-default PRAGMA 检查；全量 SQLite 回归 `2257 passed, 886 skipped, 1 warning in 1779.18s`，零失败。
- **真实 PostgreSQL 往返测试**：`tests/postgres/test_v032_migrations.py` 由独立 agent 创建，已显式加入 CI postgres job 的 pytest 命令；本地无 PG 常驻服务，结果占位：【待回填：真实 PG 迁移往返测试结果（含 upgrade/downgrade 往返、NULL 语义、幂等与权限）】。
- **CI PG 门禁**：远端 job 强制 `tests>=900`、`skipped=0`、`failures=0`；【待回填：远端 CI postgres job 结果】。
- 未验证边界：真实生产库体量下的迁移耗时/锁竞争、跨版本跳级升级（仅验证从 `0012_generation_lease` 起逐级 upgrade），不作通过声明。
