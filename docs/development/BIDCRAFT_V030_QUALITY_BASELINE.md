# BidCraft v0.30.0 质量治理基线报告（阶段 0）

- 生成时间：2026-11-24
- 审查基准：任务书 `bbf07d07ce3e4923f1542021bc327d44c877ae52` (v0.29.0)
- **实际基线 HEAD**：`4d7b4dc7aaff85ae7f24f0fa85891256eb3ca0af`
  - 说明：基准之后已有 hotfix `4d7b4dc`（修复 CI 的 postgres extra / 字体缺字体跳过 / PG 测试任务串扰 / 安全审计门禁）。按任务书要求，以最新 HEAD 重新审查，已解决项不重复修改。
- 工作分支：`quality/v0.30.0-reliability`（从最新 main 新建，工作区原为干净）
- 任务约束：本分支不推送 main、不发 Release、不改生产、不改历史迁移、不删测试、不降低安全阈值。

## 1. 环境与版本

| 项目 | 值 |
|---|---|
| Python | 3.13.5（`.venv/bin/python --version`） |
| Node | v26.13.1 |
| npm | 11.20.0 |
| SQLite | 3.46.1 |
| SQLAlchemy | 2.1.8 |
| PostgreSQL | 本地无 psql/服务；CI `postgres:16-alpine`（`BIDCRAFT_TEST_PG_URL`） |
| Alembic | 单 head `0010_batch1_billing_reliability`；10 个 revision，10 个 down_revision |
| 版本一致性 | backend 0.29.0 / web/package.json 0.29.0 / package-lock 顶层+root 0.29.0 |
| DOCX 渲染工具 | 本地无 LibreOffice/soffice/pdfinfo/pdftotext；仅有 `fc-list`（无 CJK 字体） |

## 2. 基线验证结果（原始输出路径可追溯）

| 项目 | 命令 | 结果 | 输出文件 |
|---|---|---|---|
| 后端全量 | `.venv/bin/python -m pytest -o addopts="" -q` | **1499 passed, 14 skipped**，845.50s（0:14:05），0 failed | `/tmp/opencode/full_pytest_ci_fix.out` |
| Ruff | `.venv/bin/python -m ruff check bidcraft tests` | All checks passed | `/tmp/opencode/v030_ruff.out` |
| diff 检查 | `git diff --check` | 通过 | 同上 |
| Web typecheck/lint/test/build | `npm run typecheck && npm run lint && npm test -- --run && npm run build` | 35 files / **288 tests passed**；build 5.13s | `/tmp/opencode/v030_web_checks.out` |
| Bandit（CI 命令） | `uvx --from bandit bandit -q -ll -r bidcraft --exclude bidcraft/migrations` | Exit 0；Medium/High 0；Low 19（阈值外）；2 处精确抑制 | `/tmp/opencode/v030_bandit.out` |
| pip-audit | `uvx --python 3.13.18 --from pip-audit pip-audit -r /tmp/opencode/v030_requirements.txt` | No known vulnerabilities found | `/tmp/opencode/v030_pip_audit.out` |
| npm audit（全量） | `npm audit --json` | **14 漏洞**：critical 2、high 1、moderate 11 | `/tmp/opencode/v030_npm_audit.json` |
| DOCX/导出测试 | `pytest tests/test_docx_renderer.py tests/test_docx_validator.py tests/test_batch4_export_engine.py tests/test_batch4_golden.py` | **21 passed, 1 skipped**（真实渲染 golden 因缺 LibreOffice 跳过） | `/tmp/opencode/v030_docx_baseline.out` |
| CI（hotfix 后） | run `45345071614` | 6/6 jobs success（python/postgres/web/3 个安全作业） | GitHub Actions |

### npm audit 明细（基线）

- critical：`tinypool <=2.1.1`、`vitest 0.0.95–4.1.10` → 修复需 vitest 5.1.1（major）
- high：`vite <=6.4.3` → 修复需 vite 8.3.8（major）
- moderate：`@vitest/mocker`、`esbuild`、`minimatch`、5 个 `@typescript-eslint/*` 与 `typescript-eslint`、`react-router`/`react-router-dom` 6.x
- 当前 CI 门禁仅审计生产依赖（`npm audit --omit=dev --audit-level=high`），全量审计为 advisory。本轮按任务书要求必须真正清零 critical/high。

### Bandit 明细（基线）

- B104（cli.py 通配监听）：已用 `ipaddress.ip_address(value).is_unspecified` 替代字面量比较，消除误报。
- B608（compliance.py sqlite_master 表名）：白名单来源 + 精确 `# nosec B608` 并注释理由。
- B608（迁移 0009/0010）：当前 CI 用 `--exclude bidcraft/migrations` 目录级排除，**不满足任务书“不得全文件忽略、精确抑制”的要求**，阶段 2 改为按 (file,line,rule) 白名单的精确豁免。
- B307（rules/expr.py 受控 eval）：当前 `# nosec B307` 不满足“不允许仅通过 nosec 消除”，阶段 2 需补充 AST 白名单恶意表达式测试或改用 AST 解释器。

## 3. 待修复问题清单（按阶段与优先级）

| 编号 | 阶段 | 优先级 | 问题 | 基准状态 |
|---|---|---|---|---|
| V030-01 | 1 | P0 | 中文字体测试与宿主字体环境耦合；缺 CJK 字体时 skip 即算全通过 | 部分解决（显式 skip），需拆映射测试 + 专用渲染 job |
| V030-02 | 1 | P0 | Python 主 job 缺 psycopg | 已解决（CI 增加 `--extra postgres`） |
| V030-03 | 1 | P0 | PG 并发测试任务串扰，仅靠 `db.query(Job).delete()` 清理 | 部分解决，需独立数据边界 + 20 轮并发 + 并行隔离测试 |
| V030-04 | 1 | P0 | CI job 职责未拆分；缺失败摘要/报告上传/环境信息 | 未完成 |
| V030-05 | 2 | P0 | Bandit 迁移目录级排除、B307 仅 nosec | 未完成 |
| V030-06 | 2 | P0 | npm critical 2 / high 1 未清零，需评估 major 升级 | 未完成 |
| V030-07 | 3 | P0 | 同步生成无 Job/心跳，PendingCharge 仅靠 TTL | 未完成 |
| V030-08 | 3 | P0 | 扣费/退款幂等、账务事务边界、事件一致性场景测试不足 | 部分完成 |
| V030-09 | 4 | P0 | similarity worker 租约失效后仍可能写最终报告 | 未完成 |
| V030-10 | 4 | P0 | 其他 worker fencing/幂等审计未系统化 | 部分完成 |
| V030-11 | 5 | P0 | DOCX 表格校验不区分 layout/form/data/content table | 未完成 |
| V030-12 | 5 | P1 | 真实 DOCX/PDF 渲染环境本地不可用，golden 跳过 | 未完成（需 CI 专用 job） |
| V030-13 | 5 | P1 | 大文档资源限制需复核上传/解析/IR/导出各阶段 | 部分完成 |
| V030-14 | 6 | P1 | 匿名化真实招标文件回归集缺失 | 未完成（需合成脱敏样本） |
| V030-15 | 6 | P1 | `judge_for_project` 潜在 N+1；质量指标未系统化 | 未完成 |
| V030-16 | 7 | P1 | KB/审批/查重前端闭环与契约测试需复核 | 部分完成 |
| V030-17 | 8 | P1 | 统一任务日志字段、核心指标、liveness/readiness 拆分 | 部分完成 |
| V030-18 | 8 | P1 | 容器非 root/资源限制/日志轮转/迁移安全复核 | 未完成 |
| V030-19 | 9 | P0 | 最终全量验收矩阵与报告 | 未完成 |

## 4. 阶段 0 结论

- 工作区保护完成：未执行 reset/clean，未覆盖任何用户文件；已创建独立分支。
- 基线报告已生成；所有问题已编号并映射到阶段。
- 下一步：阶段 1（CI 失败专项治理），必须先完成代码/配置修改，再执行对应测试；未通过不得进入阶段 2。
