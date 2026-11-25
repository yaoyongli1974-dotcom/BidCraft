# G5 Worker 失败与恢复验收报告（RC v0.31.3）

## 1. 环境

- RC 配置：`BIDCRAFT_ENV=rc`，隔离 home `/tmp/opencode/rc-v0313-home`
- 数据库：RC 专用 PostgreSQL 16.2；`BIDCRAFT_TEST_PG_URL` 指向 RC 测试库（非生产）
- 证据：使用生产 fencing / refund / reaper / finalize 代码，真实线程与独立 Session，无 mock 核心 SQL

## 2. 执行命令

```bash
set -a; . /tmp/opencode/rc-v0313.env; set +a
export BIDCRAFT_TEST_PG_URL="$(cat /tmp/opencode/rc_pg_uri.txt)"
.venv/bin/python -m pytest -o addopts="" -q \
  tests/postgres/test_v0313_worker_runtime.py \
  tests/postgres/test_v0313_postgres_reaper.py \
  tests/postgres/test_v0313_file_side_effects.py
```

结果：**561 passed in 40.95s**，零失败（输出 `/tmp/opencode/rc_g5_tests.out`）。

## 3. 覆盖场景

- 8 类 Worker 运行时：旧 token、已取消任务不复活、预置 abort、过期 lease 禁止终态、正常终态恰好一次、重复终态幂等、终态写入异常回滚。
- Reaper/退款：双 Reaper 竞争、心跳先胜/Reaper 先胜、重试上限唯一失败、真实扣费退款金额守恒、NULL/过期 lease 拒绝终态、旧 token 抢占后不能提交。
- 文件副作用：导出 zip 在 fencing 失败时清理、成功时保留且 zip 有效；图片生成文件在 fence 丢失后保留，重试幂等并只发一次 done；KB 仅 DB 写入。

## 4. 结论

**状态：A PASS**

残余：假模型地址未验证真实模型长任务中断；外部对象存储/供应商计费副作用不在本轮范围。所有数据均在 RC 测试库。

