# BidCraft v0.34.0 阶段 9：CI 门禁与本地校验报告

## 1. 范围

只修改 `.github/workflows/ci.yml`，并新增本报告。未改任何测试/后端代码，未降低任何既有门禁，未新增 job，未添加宽泛 allowlist，未 commit/push、未跑远程 CI。

## 2. 保留的 9 个 job 与强制门禁

YAML 解析后仍为 9 个 job（实际执行 `python -c` 校验，输出见第 4 节）：

1. `python-lint`：ruff、Alembic 单 head（AST 动态扫描）、Alembic preflight、版本/tag/CHANGELOG/发版说明一致性。
2. `python-tests`：全量 pytest、三项外部数据状态（非门禁）、calibration 基线、verdict 严格回归、**新增 opt-in restore drill**。
3. `docker-image`：镜像不变量（非 root / pg_dump / CLI / live 探针）、compose 静态加固、compose 真解析 + live 冒烟、**新增 /health/ready 冒烟 + 清理步骤**。
4. `web`：lint / test / build。
5. `postgres`：真实 PG 集成 + **显式包含 v0.34.0 并发文件**，JUnit 断言 `tests >= 900`、`skipped == 0`、`failures == 0`，**新增该文件必须出现在 JUnit 报告中的断言**。
6. `docx-render`：CJK 渲染严格门禁（0 skip / 0 failure）。
7. `security-dependency-audit`：pip-audit + npm audit（含 full tree）。
8. `security-sast`：bandit `-ll` + 可审查的门禁脚本。
9. `security-secret-scan`：gitleaks。

没有 `continue-on-error`、没有 `|| true` 绕过既有门禁、没有按目录放宽扫描。新增的 opt-in restore drill 是独立步骤，缺配置时只打印 `NOT VERIFIED` 并退出 0，不改变任何既有门禁结果。

## 3. 具体改动

### 3.1 postgres job

- 测试命令显式增加 `tests/postgres/test_v0340_db_concurrency.py`（该文件由并行任务提供，当前工作区已存在；`tests/postgres` 目录本身也在命令中，显式列出防止收集规则遗漏）。
- 保留并原样使用原有 `tests >= 900 / skipped == 0 / failures == 0` 断言；追加从 JUnit `testcase` 的 `file`（回退 `classname`）断言 `test_v0340_db_concurrency.py` 确实产生用例，避免「命令里有文件、报告里没跑」。

### 3.2 docker-image job

新增 `Readiness probe smoke (/health/ready)`：用 `BIDCRAFT_OFFLINE=1` 启动临时容器（离线模式豁免模型依赖，只验证 DB + web_dist 依赖），30 次轮询 `curl -fsS /health/ready`，用 `python3` 解析 JSON 断言 `status=ready`、`db=ok`、`web_ready=true`；失败时打印日志并让 job 失败。随后 `if: always()` 的清理步骤删除容器。原有 `/health/live` 冒烟不动。

### 3.3 python-tests job

新增 `Restore drill (opt-in, NOT VERIFIED without credentials)`：读取 `vars.BIDCRAFT_RESTORE_DRILL` 与 `secrets.BIDCRAFT_RESTORE_DRILL_CREDENTIAL`；任一未提供时输出 `RESTORE DRILL: NOT VERIFIED (opt-in var/credential not configured; no drill executed)` 并退出 0。两者都配置时才执行 `BIDCRAFT_RESTORE_DRILL=1 uv run --frozen pytest -q -o addopts="" tests/test_fix3_backup_drill.py`（临时 HOME 的真实恢复演练，不碰生产数据），成功后打印 `VERIFIED`；真实演练失败仍会让步骤失败。

## 4. 本地校验（实际命令与结果）

```bash
.venv/bin/python - <<'PY'
import yaml
from pathlib import Path
doc = yaml.safe_load(Path('.github/workflows/ci.yml').read_text())
print(len(doc['jobs']), list(doc['jobs']))
PY
# 9 ['python-lint', 'python-tests', 'docker-image', 'web', 'postgres',
#    'docx-render', 'security-dependency-audit', 'security-sast', 'security-secret-scan']
# YAML 解析成功；各 job steps 数：9/14/8/7/9/10/6/3/2
```

进一步从解析后的 YAML 取出真实脚本片段在本地重放（不是重新实现门禁）：

```text
PG command includes v0.34.0 concurrency file: OK
jobs: 9
PG gate exit: 0
PG integration OK: 900 tests, 0 skipped
restore drill exit: 0
RESTORE DRILL: NOT VERIFIED (opt-in var/credential not configured; no drill executed)
readiness bash -n exit: 0
```

负向用例（同一段 CI Python 代码，换用合成 JUnit）：

```text
skipped=1    -> rejected: AssertionError: PG 集成测试被跳过 1 项（环境/URL 缺失）
899 tests    -> rejected: AssertionError: PG 集成测试数量不足: 899（PG 套件未全部执行）
no new file  -> rejected: AssertionError: v0.34.0 DB 并发用例未进入 JUnit 报告
```

即：`>=900 / skipped==0 / failures==0 / 新文件实际收集` 四项均保持强制，且新断言确实会拒绝缺失。

另在本地读取 Alembic head（备份手册里的动态命令）：

```bash
.venv/bin/python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; cfg = Config(); cfg.set_main_option('script_location', 'bidcraft/migrations'); print(ScriptDirectory.from_config(cfg).get_heads())"
# ['0018_file_checksums']   （该迁移由并行任务提供，仍未提交）
```

## 5. 未验证项与残余风险

1. **远程 CI：NOT VERIFIED。** 未触发、未等待、未读取任何 GitHub Actions run；本报告中的 PASS 只覆盖本地 YAML 解析与脚本重放，不宣称远程 9/9 通过。
2. **PG 真实集成：本地 CONDITIONAL PASS / 远程 CI NOT VERIFIED。** 报告写入时未启动 PG；随后使用持久化 pgserver PostgreSQL 16.2 执行完整 PG 套件（`tests/postgres tests/test_postgres_integration.py tests/test_v030_pg_isolation.py tests/test_worker_fencing_atomicity.py tests/test_jobqueue_race_conditions.py tests/test_alembic.py`）→ `1078 passed, 1 skipped, 1 warning in 675.10s`。其中新 `tests/postgres/test_v0340_db_concurrency.py` 首轮因测试种子账户余额为 0 失败，修正种子（先充值 6 点）后全部通过。远程 CI 仍需对最终 SHA 单独取证。
3. **Docker 就绪冒烟：NOT VERIFIED。** 只做了 `bash -n` 语法检查，未构建镜像、未启动容器；`/health/ready` 在 `BIDCRAFT_OFFLINE=1` 下应通过，需 CI 实跑证明。
4. **Restore drill：NOT VERIFIED。** 本地重放的是「无配置 → NOT VERIFIED」分支；真实演练需要显式配置开关/凭据，本次没有执行。
5. **版本一致性：本地 PASS / 远程 CI NOT VERIFIED。** 报告写入时仍为 `0.33.2`；随后版本负责人完成 0.34.0 bump（`bidcraft/__init__.py`、`pyproject.toml`、`uv.lock`、`deploy/*`、`web/package.json`、`web/package-lock.json`）、`docs/release-notes/v0.34.0.md`、`CHANGELOG.md`、release-notes README 行，本地一致性脚本通过。远程 CI 仍需确认。
6. 并行任务的迁移 `0018_file_checksums.py` 与 PG/v0.34.0 测试文件在本报告写入时仍未提交；最终交付前必须随分支提交，否则 CI 的显式文件路径会导致 postgres job 失败。

## 6. 回滚

```bash
git restore .github/workflows/ci.yml
rm docs/development/rc/v0.34.0/11_CI_REPORT.md
```

回滚后 CI 恢复为 9 个原 job / 原门禁，但失去新 PG 并发文件的显式收集保证、`/health/ready` 冒烟与 opt-in 恢复演练状态报告。

## 7. 结论

本地 CI 配置校验 **PASS**（YAML、9 job、门禁负向重放）；远程 CI、PG 真实集成、Docker 就绪冒烟与恢复演练 **NOT VERIFIED**。建议 **CONDITIONAL GO**：待版本号 bump、并行任务文件提交、远程 CI 9/9 通过后再发版。
