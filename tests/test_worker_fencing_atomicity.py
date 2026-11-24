"""v0.31 阶段 1：Worker fencing 事务级原子提交测试。

覆盖 SQLite（本地）与 PostgreSQL（opt-in，需 ``BIDCRAFT_TEST_PG_URL``）：
旧 token、租约过期、重领后旧 worker、并发提交、取消/完成态、回滚、
commit 失败、崩溃回收，以及 100 轮固定种子的并发提交。
"""

from __future__ import annotations

import os
import random
import threading
from contextlib import contextmanager
from datetime import timedelta

import pytest

from bidcraft import jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.models import Job, Project, SimilarityReport

PG_URL = os.getenv("BIDCRAFT_TEST_PG_URL") or ""


def _project(name: str = "v031-fencing") -> str:
    with session_scope() as db:
        row = Project(name=name, package="weakcurrent-cabling")
        db.add(row)
        db.flush()
        return row.id


def _job(
    project_id: str,
    token: str,
    *,
    status: str = "running",
    lease_seconds: int = 300,
) -> str:
    now = utc_now()
    with session_scope() as db:
        row = Job(
            project_id=project_id,
            kind="similarity",
            status=status,
            claim_token=token,
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            worker_id="test-worker",
            attempts=1,
            payload={"project_id": project_id, "report_id": ""},
        )
        db.add(row)
        db.flush()
        return row.id


def _job_state(job_id: str) -> tuple[str, str | None, dict | None]:
    with session_scope() as db:
        row = db.get(Job, job_id)
        assert row is not None
        return str(row.status), row.claim_token, (row.result or None)


def _commit_done(job_id: str, marker: str) -> None:
    """Inside a fenced transaction write a result and mark the job done."""

    with jobqueue.commit_fenced_result(job_id) as (db, row):
        row.result = {"winner": marker}
        row.status = "done"
        row.progress = 100
        db.flush()


def test_normal_owner_commit_succeeds(db):
    job_id = _job(_project(), "token-ok")
    with jobqueue.claim_context("token-ok"):
        _commit_done(job_id, "current")
    assert _job_state(job_id) == ("done", "token-ok", {"winner": "current"})


def test_old_token_commit_rejected(db):
    job_id = _job(_project(), "token-new")
    with jobqueue.claim_context("token-old"), pytest.raises(jobqueue.JobAborted):
        _commit_done(job_id, "stale")
    assert _job_state(job_id) == ("running", "token-new", None)


def test_expired_lease_same_token_commit_rejected(db):
    job_id = _job(_project(), "token-t", lease_seconds=-1)
    with jobqueue.claim_context("token-t"), pytest.raises(jobqueue.JobAborted):
        _commit_done(job_id, "expired")
    assert _job_state(job_id) == ("running", "token-t", None)


def test_reclaim_then_old_worker_cannot_commit(db):
    project_id = _project()
    with session_scope() as setup:
        row = Job(
            project_id=project_id,
            kind="similarity",
            status="queued",
            payload={"project_id": project_id},
        )
        setup.add(row)
        setup.flush()
        job_id = row.id

    first = jobqueue.claim_next()
    assert first is not None and first.id == job_id
    old_token = str(first.claim_token)

    with session_scope() as expire:
        current = expire.get(Job, job_id)
        assert current is not None
        current.lease_expires_at = utc_now() - timedelta(seconds=1)

    requeued, failed = jobqueue.reap_expired()
    assert (requeued, failed) == (1, 0)
    second = jobqueue.claim_next()
    assert second is not None and second.id == job_id
    new_token = str(second.claim_token)
    assert new_token != old_token

    with jobqueue.claim_context(old_token), pytest.raises(jobqueue.JobAborted):
        _commit_done(job_id, "old")
    assert _job_state(job_id)[0] == "running"

    with jobqueue.claim_context(new_token):
        _commit_done(job_id, "new")
    assert _job_state(job_id) == ("done", new_token, {"winner": "new"})


def test_old_and_new_worker_concurrent_only_owner_wins(db):
    job_id = _job(_project(), "token-current")
    barrier = threading.Barrier(3)
    outcomes: dict[str, bool] = {}
    errors: list[str] = []

    def attempt(name: str, token: str) -> None:
        barrier.wait()
        try:
            with jobqueue.claim_context(token):
                _commit_done(job_id, name)
            outcomes[name] = True
        except jobqueue.JobAborted:
            outcomes[name] = False
        except Exception as exc:  # noqa: BLE001 - surfaced by assertion below
            errors.append(f"{name}: {exc!r}")

    threads = [
        threading.Thread(target=attempt, args=("old", "token-stale")),
        threading.Thread(target=attempt, args=("new", "token-current")),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert errors == []
    assert outcomes == {"old": False, "new": True}
    assert _job_state(job_id) == ("done", "token-current", {"winner": "new"})


@pytest.mark.parametrize("status", ["failed", "cancelled", "done"])
def test_terminal_states_reject_commit(db, status):
    job_id = _job(_project(), "token-t", status=status)
    with jobqueue.claim_context("token-t"), pytest.raises(jobqueue.JobAborted):
        _commit_done(job_id, "late")
    assert _job_state(job_id)[0] == status
    assert _job_state(job_id)[2] is None


def test_business_exception_rolls_back_whole_unit(db):
    project_id = _project()
    job_id = _job(project_id, "token-t")

    def fail_inside() -> None:
        with jobqueue.commit_fenced_result(job_id) as (db_session, row):
            db_session.add(
                SimilarityReport(
                    project_id=project_id,
                    scope="project",
                    mode="text",
                    threshold=0.85,
                    status="done",
                    stats={},
                    skipped=[],
                    created_by="stage1-rollback",
                )
            )
            db_session.flush()
            row.result = {"partial": True}
            raise RuntimeError("模拟业务写入中途失败")

    with jobqueue.claim_context("token-t"), pytest.raises(RuntimeError):
        fail_inside()

    with session_scope() as check:
        written = (
            check.query(SimilarityReport)
            .filter(SimilarityReport.created_by == "stage1-rollback")
            .count()
        )
        assert written == 0
    assert _job_state(job_id) == ("running", "token-t", None)


def test_commit_failure_leaves_no_partial_result(db, monkeypatch):
    project_id = _project()
    job_id = _job(project_id, "token-t")
    real_scope = jobqueue.session_scope

    @contextmanager
    def failing_scope():
        with real_scope() as session:
            def _boom() -> None:
                raise RuntimeError("模拟数据库提交失败")

            session.commit = _boom  # type: ignore[method-assign]
            yield session

    monkeypatch.setattr(jobqueue, "session_scope", failing_scope)
    with (
        jobqueue.claim_context("token-t"),
        pytest.raises(RuntimeError),
        jobqueue.commit_fenced_result(job_id) as (db_session, row),
    ):
        db_session.add(
            SimilarityReport(
                project_id=project_id,
                scope="project",
                mode="text",
                threshold=0.85,
                status="done",
                stats={},
                skipped=[],
                created_by="stage1-commit-failure",
            )
        )
        db_session.flush()
        row.status = "done"

    with session_scope() as check:
        written = (
            check.query(SimilarityReport)
            .filter(SimilarityReport.created_by == "stage1-commit-failure")
            .count()
        )
        assert written == 0
    assert _job_state(job_id)[0] == "running"


def test_crashed_worker_job_is_safely_reclaimed_and_committed(db):
    job_id = _job(_project(), "token-crashed", lease_seconds=-1)
    with session_scope() as expire:
        row = expire.get(Job, job_id)
        assert row is not None
        row.lease_expires_at = utc_now() - timedelta(seconds=1)

    requeued, failed = jobqueue.reap_expired()
    assert (requeued, failed) == (1, 0)
    claimed = jobqueue.claim_next()
    assert claimed is not None and claimed.id == job_id
    token = str(claimed.claim_token)
    with jobqueue.claim_context(token):
        _commit_done(job_id, "recovered")
    assert _job_state(job_id) == ("done", token, {"winner": "recovered"})


def test_100_rounds_old_vs_new_concurrent_commit(db):
    random.seed(20261124)  # 固定随机种子，失败可复现
    for round_no in range(100):
        job_id = _job(_project(f"round-{round_no}"), f"current-{round_no}")
        barrier = threading.Barrier(3)
        outcomes: dict[str, bool] = {}
        errors: list[str] = []
        order = ["old", "new"]
        random.shuffle(order)

        def attempt(
            name: str,
            token: str,
            barrier=barrier,
            job_id=job_id,
            outcomes=outcomes,
            errors=errors,
        ) -> None:
            barrier.wait()
            try:
                with jobqueue.claim_context(token):
                    _commit_done(job_id, name)
                outcomes[name] = True
            except jobqueue.JobAborted:
                outcomes[name] = False
            except Exception as exc:  # noqa: BLE001 - surfaced by assertion below
                errors.append(f"{name}: {exc!r}")

        threads = [
            threading.Thread(
                target=attempt,
                args=(name, f"stale-{round_no}" if name == "old" else f"current-{round_no}"),
            )
            for name in order
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), f"round {round_no} thread hung"

        assert errors == [], f"round {round_no}: {errors}"
        assert outcomes == {"old": False, "new": True}, f"round {round_no}: {outcomes}"
        assert _job_state(job_id) == (
            "done",
            f"current-{round_no}",
            {"winner": "new"},
        )


@pytest.mark.skipif(not PG_URL, reason="需要 BIDCRAFT_TEST_PG_URL 才会运行真实 PostgreSQL 校验")
def test_postgresql_atomic_fencing_optin(tmp_path, monkeypatch):
    """真实 PG 上重复核心场景：条件 UPDATE 在 WAL 事务里同样只让合法 owner 写入。"""

    from bidcraft.config import settings
    from bidcraft.db import init_db, reset_engine_for_tests

    home = tmp_path / ".bidcraft-pg"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "home", home)
    reset_engine_for_tests(PG_URL)
    init_db()
    try:
        job_id = _job(_project("pg-fencing"), "pg-current")
        with jobqueue.claim_context("pg-stale"), pytest.raises(jobqueue.JobAborted):
            _commit_done(job_id, "stale")
        assert _job_state(job_id) == ("running", "pg-current", None)
        with jobqueue.claim_context("pg-current"):
            _commit_done(job_id, "current")
        assert _job_state(job_id) == ("done", "pg-current", {"winner": "current"})
    finally:
        reset_engine_for_tests(f"sqlite+pysqlite:///{home / 'local.db'}")
        init_db()
