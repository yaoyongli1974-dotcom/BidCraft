# BidCraft v0.31.3 生产上线前最终验收报告

## 1. 范围与基线

- 分支：`fix/v0.31.3-production-readiness`（仅分支提交，未推送 main、未创建 Release、未部署生产）
- 基线：`f06feb7`（v0.31.2）
- 任务书：9 阶段生产上线前最后验收与风险收口（0 基线保护 → 8 最终验收）
- 版本：0.31.3（后端、Web、lock、部署清单一致）
- Alembic 单 head：`0012_generation_lease`；历史迁移 0001～0012 零改动
- 约束遵守：无新功能、无 API 业务语义变更、无测试删除/断言放宽、无 skip/xfail/mock 掩盖真实问题、无源码检查冒充运行时证据、无合成件冒充真实件、无“DB 回滚自动回滚文件系统”的错误表述、无强制推送/Release/生产部署、`reset --hard`/`clean -fd` 未执行

## 2. 阶段提交与结论

| 阶段 | 提交 | 主题 | 结论 |
|---|---|---|---|
| 0 | `20266e5` | 基线保护与真实 PostgreSQL 环境确认 | A PASS（2125 pytest 基线） |
| 1 | `fcf66dd` | 真实 PG 并发验证与退款布尔比较修复 | A PASS（834 PG passed；发现并修复生产 bug） |
| 2 | `3f1f325` | 八类 Worker 真实 PG 运行时矩阵 | A PASS（57 PG passed） |
| 3 | `f8f9469` | 文件副作用清理与真实 PG 验证 | A PASS（894 PG passed） |
| 4 | `41697e6` | 真实 PG 指标分母与真实件未测量口径 | A PASS（4 PG passed） |
| 5 | `bdf4573` | 真实 PG 迁移往返与权限/IDOR 验证 | A PASS（901 PG passed） |
| 6 | `3fea0ce` | CI 真实 PG 纳入、安全门禁强制化、容器健康检查 | A PASS（PG 901）+ D（本地 Docker） |
| 7 | `4d475df` | 发版说明与版本号 bump 0.31.3 | A PASS |
| 8 | 本提交 | 最终生产验收与四类状态报告 | 见下方 |

## 3. 验证结果

| 检查 | 结果 |
|---|---|
| SQLite 全量 pytest | **2125 passed / 886 skipped / 1 warning in 788.52s**，零失败（skip = 868 项 PG opt-in + 18 项既有环境 skip） |
| PostgreSQL 全量（真实 16.2 pgserver） | **901 passed / 1 skipped / 1 warning in 76.83s**，零失败（本地 skip 为 pg_dump 缺工具，CI 已安装） |
| Web typecheck / lint / test / build | 通过：39 files / 320 tests，Vite 8 build |
| `ruff check bidcraft tests scripts` | 通过 |
| `git diff --check` | 通过 |
| 历史迁移改动 / 测试删除 | 0 / 0 |
| 版本一致性 | 0.31.3（后端 / Web / lock / 部署） |
| Alembic 单 head | `0012_generation_lease` |

Stage 8 发现并修复两个测试侧残留：
1. 新增 `tests/postgres/conftest.py` 在 pytest 默认 import mode 下遮蔽了根目录 `tests/conftest.py`，导致 `tests/test_extract.py` 的 `from conftest import make_docx_tender` 在完整收集时 ImportError。修复方式是新增空的 `tests/postgres/__init__.py`，令子目录 conftest 以包模块导入；完整收集从失败恢复为 **3011 tests collected**，未修改测试断言，也未删除任何测试。
2. 阶段 6 将 CI 临时镜像标签从 `bidcraft:v030-check` 改为 `bidcraft:v0313-check`，但 `tests/test_v030_stage8_deploy.py` 仍断言旧标签，全量运行出现 1 failed / 2124 passed / 886 skipped。已把断言更新为当前标签并额外断言旧标签不回退（断言更强，未删除测试），单独复跑 6 passed；随后第三次全量运行 2125 passed / 886 skipped，零失败。

## 4. 四类状态结论

### A PASS

- **阶段 0 基线保护**：HEAD/版本/Alembic/tag/CI 与 Web 基线全部确认，主分支 CI 成功。
- **阶段 1 PG 并发与退款 bug 修复**：先复现 100 failed / 401 passed，定位 `refund_charged == 0` 在 PG BOOLEAN 上的 `boolean = integer` 错误；改为 `.is_(False)` 后 501 passed。随后全部 PG 并发场景 834 passed。
- **阶段 2 Worker 运行时矩阵**：8 类 runner 的真实 PG Job/JobEvent、真实 claim/lease、旧 token/取消/abort/过期租约/正常终态/重复终态/写入异常回滚，57 passed。
- **阶段 3 文件副作用**：导出 zip 在 fencing 失败时删除，成功时保留；图片生成文件保留且重试幂等；KB 仅 DB 写入；894 PG passed。
- **阶段 4 质量指标与真实件口径**：PG 上三项指标分子分母精确验证；缺 manifest / SYNTHETIC 文件均判未测量；带标注数据集测得 1.0/1.0/1.0；4 passed。
- **阶段 5 迁移与权限**：Alembic head→0001→head 往返保留数据与版本；非法成员角色不授权；跨项目 finding 返回 404 且无越权审计；3 passed。
- **阶段 6 CI/测试门禁**：新 PG 测试全部纳入 CI，断言 ≥900 tests / 0 skip / 0 failure；安全作业不再 `continue-on-error`；conftest 遮蔽缺陷修复；YAML 与 compose env 静态校验通过。
- **阶段 7 文档版本**：发版说明、CHANGELOG、版本串一致，collect-only 通过。

### B CONDITIONAL PASS

- **真实 PostgreSQL 环境**：本地使用 pgserver 启动的真实 PostgreSQL 16.2（unix socket）而非生产托管实例；CI 使用 `postgres:16-alpine`。两者均非生产库，结论为条件通过，生产首日仍需观察真实负载与权限。
- **CI 强制安全门禁**：本地已验证无 continue-on-error，但实际远端 CI 在新分支尚未执行（按约束不推送分支到远端触发），需用户授权后验证。
- **真实件质量指标**：计算与未测量口径在 PG 上通过；真实脱敏招标文件及人工标注仍需用户提供，真实召回率/一致率尚未测量。

### C BLOCKED

- 无。本轮没有发现需要暂停生产上线的未修复数据丢失、权限越权、账务错误或不可逆迁移问题。

### D NOT VERIFIED

- **Docker 运行时健康检查**：本地无 Docker，无法执行 compose config、容器启动、`/health/live` 轮询；CI 已加入该步骤但尚未运行。
- **生产环境的 PostgreSQL 行锁压力、备份恢复演练与远端 CI 结果**：需要生产/预发环境或用户授权后执行。
- **真实招标文件与人工标注**：样本未提供，无法把“未测量”升级为实测结论。

## 5. 结论与回滚

本轮在生产上线前完成了真实 PostgreSQL 并发复现与修复、8 类 Worker 运行时验证、文件副作用边界、迁移/权限、CI/容器门禁与文档版本收口。唯一生产代码修复是 `refund_charged_job` 的 PostgreSQL BOOLEAN 条件；其余为测试、CI、文档与显式文件清理。

结论：**具备条件上线评审条件**；上线前需完成 CI 远端运行、Docker 容器健康检查与真实 PG 环境观察。真实脱敏样本仍在用户提供后补测。

回滚：按阶段 revert；无新增迁移、无 schema 变化、无生产配置部署变更；阶段 3 的导出 zip 清理是删除临时未交付文件，无需数据回滚。
