"""v0.30 PostgreSQL 并发隔离压力测试（opt-in，需 BIDCRAFT_TEST_PG_URL）。

与 test_postgres_integration.py 共享同一套真实 PG 环境，但把焦点放在**认领边界**：

* 每个用例前 autouse fixture 清空全部应用表（alembic_version 保留），保证零历史残留；
* claim_next() 认领全局 queued 任务，因此用例只能断言自己创建的 id；
* 两个独立 worker 池并发时，一个 job 只能被一个线程认领一次（条件 UPDATE 保证），
  不能出现重复认领或认领到别人的 id。

未设置 BIDCRAFT_TEST_PG_URL 时整文件跳过（环境缺失，不是掩盖失败）。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import inspect, text

PG_URL = os.getenv("BIDCRAFT_TEST_PG_URL", "").strip()
pytestmark = pytest.mark.skipif(not PG_URL, reason="需要 BIDCRAFT_TEST_PG_URL（专用测试库）")


@pytest.fixture(scope="module")
def pg_engine():
    """与集成测试一致的模块级 schema 准备（建表只在模块开始时做一次）。"""
    from bidcraft.config import settings
    from bidcraft.db import get_engine, init_db, reset_engine_for_tests
    from bidcraft.models import Base

    old_url = settings.db_url
    try:
        settings.db_url = PG_URL
        reset_engine_for_tests(PG_URL)
        engine = get_engine()
        Base.metadata.drop_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        init_db()
        yield engine
    finally:
        settings.db_url = old_url
        reset_engine_for_tests(old_url)


@pytest.fixture(autouse=True)
def _reset_pg_data(pg_engine):
    """隔离契约：每个用例前 TRUNCATE 全部应用表，alembic_version 原样保留。

    CASCADE 负责外键依赖顺序，RESTART IDENTITY 复位自增序列；动态读取表清单，
    迁移新增的表也会自动纳入。故任何用例（含迁移往返）的遗留 job 都不会越界。
    """
    tables = [name for name in inspect(pg_engine).get_table_names() if name != "alembic_version"]
    with pg_engine.begin() as connection:
        if tables:
            quoted = ", ".join(f'"{name}"' for name in tables)
            connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


def _make_jobs(count: int) -> list[str]:
    """只写本用例自己的 job；绝不替别的用例清理（那是 autouse 边界的职责）。"""
    from bidcraft.db import session_scope
    from bidcraft.models import Job

    ids = [str(uuid.uuid4()) for _ in range(count)]
    with session_scope() as db:
        for job_id in ids:
            db.add(Job(id=job_id, kind="kb_reindex", status="queued", payload={}, attempts=0))
    return ids


def test_claim_next_stress_20_rounds_no_duplicates(pg_engine):
    """20 轮并发认领：每轮新建 job，必须恰好认领本轮 id，不重复、不外泄。"""
    from bidcraft.db import session_scope
    from bidcraft.jobqueue import claim_next
    from bidcraft.models import Job

    workers = 4
    per_round = 4

    for round_no in range(20):
        ids = _make_jobs(per_round)
        claimed: list[str] = []
        lock = threading.Lock()

        def claim_one(_, _claimed=claimed, _lock=lock):
            # 真实 worker 会反复调 claim_next；并发碰撞时返回 None 属正常，重试即可。
            # 把轮次私有变量绑成默认参数，避免闭包晚绑定跨轮串味（B023）。
            for _ in range(100):
                job = claim_next()
                if job is None:
                    time.sleep(0.005)
                    continue
                with _lock:
                    _claimed.append(job.id)
                return

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(claim_one, range(workers)))

        # 恰好 per_round 次认领、无重复、且全部属于本轮——任何遗留 job 都会在这里暴露。
        assert len(claimed) == per_round, f"第 {round_no} 轮认领数量异常：{claimed}"
        assert len(set(claimed)) == per_round, f"第 {round_no} 轮出现重复认领：{claimed}"
        assert set(claimed) == set(ids), f"第 {round_no} 轮认领了非本轮任务：{claimed}"

        with session_scope() as db:
            rows = db.query(Job).filter(Job.id.in_(ids)).all()
            assert {row.id for row in rows} == set(ids)
            assert all(row.status == "running" for row in rows)


def test_parallel_worker_pools_cannot_cross_claim(pg_engine):
    """两个独立 worker 池（各有自己的 job 集合）并发认领，互不串扰。

    隔离含义：claim_next 是全局队列，无法按“分组”过滤；真正要保证的是
    **一个 job 只能被一个线程认领一次**。两个池同时开抢，断言：
    * 每个 id 只出现一次（没有两个池同时认领同一 job）；
    * 两池认领结果互不重叠、并集恰好等于双方创建的全部 id（无遗漏、无外来 id）。
    """
    from bidcraft.db import session_scope
    from bidcraft.jobqueue import claim_next
    from bidcraft.models import Job

    per_group = 4
    ids_a = _make_jobs(per_group)  # worker 池 A 的私有 job 集合
    ids_b = _make_jobs(per_group)  # worker 池 B 的私有 job 集合
    assert set(ids_a).isdisjoint(ids_b), "两个池的 job 集合必须互不相交"
    all_ids = set(ids_a) | set(ids_b)

    claims: list[tuple[str, str]] = []  # (job_id, group)
    lock = threading.Lock()
    start = threading.Event()

    def work(group: str, _index: int) -> None:
        start.wait(5)  # 两池同时起跑，放大并发碰撞窗口
        for _ in range(100):
            job = claim_next()
            if job is None:
                time.sleep(0.005)
                continue
            with lock:
                claims.append((job.id, group))
            return  # 与真实 worker 一致：拿到一个 job 就交给执行路径

    with ThreadPoolExecutor(max_workers=4) as pool_a, ThreadPoolExecutor(max_workers=4) as pool_b:
        futures_a = [pool_a.submit(work, "A", i) for i in range(4)]
        futures_b = [pool_b.submit(work, "B", i) for i in range(4)]
        start.set()
        for future in futures_a + futures_b:
            future.result()

    claimed_ids = [job_id for job_id, _group in claims]
    # 每个 id 恰好一次：A 与 B 不可能同时认领同一个 job。
    assert len(claimed_ids) == 2 * per_group, f"认领次数异常：{claims}"
    assert len(set(claimed_ids)) == len(claimed_ids), f"出现重复认领：{claims}"
    assert set(claimed_ids) == all_ids, f"认领了外来 id 或有遗漏：{claims}"

    claimed_by_a = {job_id for job_id, group in claims if group == "A"}
    claimed_by_b = {job_id for job_id, group in claims if group == "B"}
    # 两池的认领集合互斥且合并后完整——谁也不能把对方的 job 也算进自己的成果。
    assert claimed_by_a.isdisjoint(claimed_by_b)
    assert claimed_by_a | claimed_by_b == all_ids

    with session_scope() as db:
        rows = db.query(Job).filter(Job.id.in_(all_ids)).all()
        assert {row.id for row in rows} == all_ids
        assert all(row.status == "running" for row in rows)
