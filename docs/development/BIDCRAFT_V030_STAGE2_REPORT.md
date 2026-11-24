# BidCraft v0.30.0 阶段 2 报告：安全扫描与依赖治理

- 日期：2026-11-24
- 分支：`quality/v0.30.0-reliability`
- 基线：`d6d0338`（阶段 1 报告见 `BIDCRAFT_V030_STAGE1_REPORT.md`）
- 约束遵守：未推送 main、未发版、未改生产、未改历史迁移、未删测试、未降低扫描阈值、未引入未经评估的大版本升级。

## 1. 阶段目标与结果（11 项检查表）

| # | 检查项 | 结论 |
|---|---|---|
| 1 | 分支/基线 | `quality/v0.30.0-reliability`，基线 `d6d0338`，工作区起点干净 |
| 2 | 范围 | 仅 CI Bandit 门禁、前端依赖与构建配置、Bandit 精确豁免脚本、后端表达式安全测试 |
| 3 | V030-05 Bandit 目录级排除 | 已移除 `--exclude bidcraft/migrations`，改为全量扫描 + 2 项精确 (文件,行,规则) 豁免 |
| 4 | V030-05 B307 依据 | 新增 10 条恶意表达式用例，证明 AST 白名单 + 空 builtins 下无副作用 |
| 5 | V030-06 npm 审计 | 14 → 0（critical 2 / high 1 / moderate 11 全部清零） |
| 6 | 大版本升级评估 | 已核对 engines/peer：Vite 8.3.8、Vitest 5.1.1、plugin-react 6.3.0、react-router-dom 7.18.6、typescript-eslint 8.74.0；CI Node 22 满足 |
| 7 | 破坏性变更 | Vite 8 用 Rolldown，`manualChunks` 对象形式迁移为 `rolldownOptions.output.codeSplitting.groups`；前端业务源码与测试无需修改 |
| 8 | Ruff 覆盖 | CI lint 扩展为 `ruff check bidcraft tests scripts` |
| 9 | 测试 | 后端 28 passed / 1 skipped（环境性 DOCX golden）；前端 35 文件 / 288 测试全通过 |
| 10 | 风险 | 见第 4 节 |
| 11 | 回滚 | 见第 5 节 |

## 2. 变更清单

### `.github/workflows/ci.yml`
- `security-sast`：Bandit 全量扫描 `bidcraft`（不再排除 migrations），生成 JSON 后由 `scripts/bandit_gate.py` 判定；扫描退出码 1（有发现）允许进入门禁，>1 视为扫描失败；`-ll` 中/高危阈值与 `continue-on-error: true` 不变。
- `python-lint`：`ruff check bidcraft tests scripts`，把新增门禁脚本纳入静态检查。

### `scripts/bandit_gate.py`（新增）
- 解析 Bandit JSON（容忍 uvx 进度前缀），按 `(相对文件名, 行号, 规则 ID, 理由)` 精确豁免 2 项迁移静态 DDL：`0009_v028_schema_parity.py:89 B608`、`0010_batch1_billing_reliability.py:59 B608`；未命中/行号漂移会提示，新增发现一律失败。
- 缺报告、坏 JSON、扫描 errors、违规发现分别以不同退出码失败；MEDIUM/HIGH 过滤等价于 `-ll`。

### `tests/test_expr_security.py`（新增）
- 10 条参数化恶意表达式（`__import__`、`open`、属性穿越、lambda、推导式、dunder 名、超长表达式等）使用 tripwire 对象/副本文件证明 `evaluate()` 拒绝执行且无副作用；另有合法表达式回归用例。为 `bidcraft/rules/expr.py` 的 `# nosec B307` 提供行为证据，而非仅靠注释豁免。

### `web/package.json` / `web/package-lock.json`
| 包 | 前 | 后 |
|---|---|---|
| vite | 5.4.21 | 8.3.8 |
| vitest | 3.2.7 | 5.1.1 |
| @vitejs/plugin-react | 4.7.0 | 6.3.0 |
| react-router-dom | 6.30.6 | 7.18.6 |
| typescript-eslint | 8.72.2 | 8.74.0 |

锁文件由 `npm install` 重新生成；传递依赖在原 semver 范围内上移（如 TypeScript 5.7.2 → 5.9.3）。

### `web/vite.config.ts`
- Vite 8 改用 Rolldown，构建配置由 `build.rollupOptions.output.manualChunks` 迁移为 `build.rolldownOptions.output.codeSplitting.groups`，保留 `react-vendor` 与 `icons` 分包。

## 3. 验证结果

| 检查 | 命令 | 结果 |
|---|---|---|
| Ruff | `ruff check bidcraft tests scripts` | All checks passed |
| diff | `git diff --check` | 通过 |
| Bandit 全量 | `bandit -q -ll -f json -r bidcraft -o /tmp/bandit.json` | 中危 2 项（均为迁移静态 DDL），0 高危 |
| Bandit 门禁 | `python scripts/bandit_gate.py /tmp/bandit.json` | 2 项精确豁免，GATE OK，退出 0；反向用例（新增/漂移/坏 JSON）均按预期失败 |
| 表达式安全 | `pytest tests/test_expr.py tests/test_expr_security.py` | 17 passed |
| DOCX 定向 | `pytest tests/test_batch4_export_engine.py tests/test_batch4_golden.py` | 11 passed / 1 skipped（本机无渲染工具） |
| npm 审计 | `npm audit --audit-level=high` | found 0 vulnerabilities |
| 前端类型/规范/测试/构建 | `npm run typecheck && npm run lint && npm test -- --run && npm run build` | 全通过；35 文件 / 288 测试；构建 1710 模块，分包正常 |
| CI YAML | `yaml.safe_load` | 通过 |

## 4. 遗留风险与交接

1. **精确豁免对行号敏感**：迁移文件行号漂移会让门禁失败（fail-closed），属有意设计；后续若必须改迁移，需同步更新 `ALLOWED` 并说明。
2. **Node 引擎下限**：`jsdom@31` 要求 Node `^22.22.2 || ^24.15 || >=26`，CI `setup-node` 使用最新 22.x，满足要求；若自建 CI 固定旧 22 小版本需升级。
3. **锁文件整体重生成**：传递依赖在原 semver 范围内上移；已用全量前端测试与构建验证，但真实浏览器端回归仍需部署环境观察。
4. **CI 未实跑**：本阶段在本地完成门禁与全量前端验证；需推送分支后由 GitHub Actions 确认安全作业全部绿灯。

## 5. 回滚说明

逐文件回滚：恢复 `.github/workflows/ci.yml`、`web/package.json`、`web/package-lock.json`、`web/vite.config.ts`，删除 `scripts/bandit_gate.py`、`tests/test_expr_security.py`、本报告，再执行 `cd web && npm ci`。原锁文件备份在 `/tmp/package-lock.before.json`（仅参考，不纳入仓库）。未触碰业务后端代码、历史迁移或既有测试。
