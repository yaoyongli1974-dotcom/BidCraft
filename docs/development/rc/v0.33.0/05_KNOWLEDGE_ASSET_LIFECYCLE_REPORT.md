# RC-04 知识资产生命周期报告

## 1. 范围与基线

- 分支：`fix/v0.33.0-rc-reliability`
- 基线：HEAD `241a9bf`（RC-03 章节规划一致性）
- 审计来源：只读审计 B6–B9（P1：B6/B7，P2：B8/B9）
- 约束：仅本任务授权文件；不改历史迁移、不新增表/字段、不删测试或放宽断言、
  不动生产数据、不提交/推送、不跑 `ruff format`。

## 2. 已修复问题与改动面

### B6（P1）重新入库保留已发布审核痕迹

`bidcraft/knowledge.py::ingest_document()`

- 在命中已有行且摘要发生变化的 `else` 分支中，若原 `review_status` 非 NULL，
  则重置为 `draft`，并清空 `reviewed_by` / `reviewed_at` / `published_at` /
  `rejected_reason`。历史 `review_status IS NULL` 的文档保持 legacy 语义，
  不写入 `draft`。
- 摘要未变（含 `row.status != "ready"` 但摘要相同的边界）时不触碰审核痕迹；
  原 early-return 分支行为完全保留。
- `index_local()` 重索引本就调用 `ingest_document()`，因此同一修复自动覆盖
  reindex/reingest 路径（新测试 `test_index_local_reingest_resets_lifecycle`）。
- 新文本回到 draft 后即被 `_ready_chunk_filter()` 拦截，直到重新走完
  `draft → review → approved → published` 才恢复检索可见。

### B7（P1）企业资产匹配引用未发布文档块

`bidcraft/enterprise/matching.py`

- `_document_rows()`：查询 `KbDocument` 时追加统一审核门禁；`match_performance()`、
  `match_equipment()`、`build_project_asset_package()` 均走此入口。
- `_document_text()`：块级查询 join `KbDocument`（outer join 保留无溯源旧块）
  并追加同一门禁，避免调用方传入未发布文档时仍取其正文作为证据。
- 公共函数签名不变；`status` / `valid_to` 的既有有效期判定仍由 `_document_valid()`
  负责，本次只增加审核状态维度。

统一谓词 `bidcraft/knowledge.py::releasable_review_filter()`：
`review_status IS NULL OR = '' OR = 'published'` 放行；
`draft` / `review` / `approved` / `rejected` / `archived` 拦截。检索
`_ready_chunk_filter()` 也复用该谓词，避免三条链路判定不一致。

### B8（P2）删除文档/知识库留下查重孤儿行

`bidcraft/knowledge.py::cleanup_duplicate_references()`（新增）、`delete_document()`

- 先删除引用目标文档的 `KbAssetDuplicateMember`，再只删除本 KB 中已无成员的
  `KbAssetDuplicateGroup`；非空组（含已裁决组）与其他 KB 不受影响。
- `bidcraft/api/routes/admin.py`：`update_kb()` 两处清理旧文档的分支与
  `delete_kb()` 在批量删除 `KbDocument` / `KbChunk` 前调用同一清理函数。
- 企业文档删除路由 `enterprise.py::enterprise_document_delete()` 已委托
  `knowledge.delete_document()`，故无需改动该企业路由。

### B9（P2）向量化嵌入不可发布块

`bidcraft/knowledge.py::build_vectors()`

- 待向量化块与其 `pending` 计数均 join `KbDocument` 并应用
  `releasable_review_filter()`；legacy（NULL/空串）与 published 仍可嵌入。
- 草稿/驳回/归档正文不再被送往向量接口，也不写入 `embed_key`。

## 3. 测试

新增 `tests/test_v0330_kb_lifecycle.py`，11 项真库（SQLite `db` fixture）回归：

1. `test_reingest_changed_content_resets_lifecycle_and_search_gate`：发布文档内容变化后
   审核痕迹清空、新文本不可检索，重新发布后恢复可见。
2. `test_reingest_same_digest_keeps_published_lifecycle`：摘要不变时版本、审核痕迹与块 ID
   全部保持（兼容性）。
3. `test_index_local_reingest_resets_lifecycle`：磁盘文件变更后的 `index_local()` 走同一逻辑。
4–8. `test_matching_ignores_non_published_document_chunks[draft/review/approved/rejected/archived]`：
   真实 `matching.match_equipment()`，发布/legacy 文档命中，五种未发布状态的文档不出现在结果中。
9. `test_delete_document_cleans_duplicate_members_and_empty_groups`：同摘要组逐个删成员，
   空组才删除，另一个非空组保留。
10. `test_delete_kb_route_cleans_duplicate_rows`：真实 `DELETE /admin/kbs/{kb_id}` 后
    组与成员行均清空。
11. `test_build_vectors_skips_non_releasable_chunks`：仅 stub `embed_texts` 记录送入的文本，
    DB 生命周期过滤真实生效；断言嵌入文本集合恰为 published + legacy。

实际结果（均在 `/home/yao/project`，使用仓库 `.venv`）：

```text
.venv/bin/ruff check bidcraft/knowledge.py bidcraft/knowledge_assets.py \
  bidcraft/enterprise/matching.py bidcraft/api/routes/admin.py \
  bidcraft/api/routes/enterprise.py tests/test_v0330_kb_lifecycle.py
All checks passed!

.venv/bin/python -m pytest tests/test_v0330_kb_lifecycle.py --disable-warnings --tb=short
11 passed, 1 warning in 11.29s

.venv/bin/python -m pytest \
  tests/test_v0330_kb_lifecycle.py tests/test_kb_documents.py \
  tests/test_kb_assets_v032.py tests/test_kb_assets_api_v032.py \
  tests/test_enterprise_matching.py tests/test_knowledge_vector.py \
  tests/test_kb_reindex_job.py tests/test_admin.py tests/test_batch6_gaps.py \
  tests/test_personnel.py tests/test_user_docs.py \
  --disable-warnings --tb=short
95 passed, 1 warning in 62.08s (0:01:02)
```

唯一 warning 是 Starlette/httpx `TestClient` 弃用提示，与本次改动无关。
全仓 `ruff check .` 仍有 1 条既有问题：`packaging/entry.py:37 SIM114`（未在本任务
授权文件内，未改动）。

## 4. 失败历史

1. 测试文件首轮收集失败：`from bidcraft import ... matching` 导入路径错误，
   ruff 同时报 1 条未使用导入（`knowledge_assets`）。改为
   `from bidcraft.enterprise import assets, matching` 并移除未用导入；ruff 随后通过。
2. 生产改动前基线运行：`10 failed, 1 passed`。具体分布：
   - B6 两例（`review_status` 仍为 `published`，断言 `published in ('draft', None)` 失败）；
   - B7 五个参数化状态全部失败（未发布文档仍出现在 `match_equipment().results`）；
   - B8 两例（删除后成员行仍为 1；KB 删除后组仍存在）；
   - B9 一例（`embedded == 4`，期望 2）；
   - 唯一通过的是“摘要不变不改变审核痕迹”的兼容性用例。
3. 生产改动后首轮 `10 passed, 1 failed`：用例 9 自身预判错误——同摘要组有 3 个成员，
   删除 2 个后组仍应存在。测试补删第 3 个文档并增加“非空组保留”的真实断言，
   未删断言、未降低强度；随后 11/11 通过。
4. 最终复跑均通过，数字见第 3 节。

## 5. 兼容性

- 无迁移、无 schema 变化、无生产数据改写；只使用现有模型字段与表。
- 未新增/删除 API 路由：`admin.py` 路由装饰器由 HEAD 的 66 条保持为 66 条，
  `enterprise.py` 由 15 条保持为 15 条（`git show HEAD:<file> | grep -c '^@router\.'`
  与工作树同命令对比）。
- 所有公共函数签名不变；新增 `knowledge.releasable_review_filter()` 与
  `knowledge.cleanup_duplicate_references()` 为追加式，不替换现有接口。
- 检索门禁从“仅 NULL/published”扩展为“NULL/空串/published”，空串按历史未审核状态
  放行；这是审计要求的统一 legacy 口径，不影响 draft/rejected 等拦截。
- 未修改/删除任何既有测试或断言。

## 6. 残余风险

- `kb_payload().vector_ready` 仍按 KB 全部块数计算 `vector_total`；存在草稿块时
  向量化有意跳过，可能显示 `vector_stale=True`。这是预期安全行为，不是嵌入失败。
- 本次清理覆盖 `knowledge.delete_document()` 与 admin 的 KB 删除/替换文档分支；
  其他未来新增的批量删文档路径需继续复用 `cleanup_duplicate_references()`。
- 草稿/驳回正文仍存于 `kb_chunks`（供重新审核与重抽取），只是从检索、企业匹配和
  向量化三条链路统一剔除；“物理删除非发布块”不在本次范围。
- 验证使用 SQLite 真库 + TestClient；PG 并发场景未在本次单独覆盖，留待 RC 后期回归。

## 7. 回滚

纯代码回滚，无数据迁移：

- 回退 `bidcraft/knowledge.py`、`bidcraft/enterprise/matching.py`、
  `bidcraft/api/routes/admin.py` 三处 diff（无 `enterprise.py` 改动）。
- 删除 `tests/test_v0330_kb_lifecycle.py` 与本报告。
- 回滚后已重置为 draft 的文档不会自动恢复 published，需按现有审核状态机重新发布；
  被清理的查重空组如需恢复，可重新调用 `find_duplicate_candidates()` 生成。
