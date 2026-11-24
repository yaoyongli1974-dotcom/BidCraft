"""v0.31.1 阶段 1：GenerationLease 独立会话心跳与失效传播。"""

from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

from bidcraft import jobqueue
from bidcraft.clock import utc_now
from bidcraft.config import Settings
from bidcraft.db import session_scope
from bidcraft.models import GenerationLease


def _lease() -> str:
    with session_scope() as db:
        token = jobqueue.acquire_generation_lease(db, "p-heartbeat", ttl_seconds=180)
    assert token
    return token


def test_renew_generation_lease_uses_independent_session(db) -> None:
    token = _lease()
    assert jobqueue.renew_generation_lease("p-heartbeat", token) == "active"
    with session_scope() as db2:
        row = db2.get(GenerationLease, "p-heartbeat")
        assert row is not None and row.token == token
    assert jobqueue.renew_generation_lease("", token) == "stopped"
    with session_scope() as db2:
        jobqueue.release_generation_lease(db2, "p-heartbeat", token)
    assert jobqueue.renew_generation_lease("p-heartbeat", token) == "lost"


def test_heartbeat_renews_lease_in_independent_session(db) -> None:
    token = _lease()
    near_expiry = utc_now() + timedelta(seconds=10)
    with session_scope() as db2:
        row = db2.get(GenerationLease, "p-heartbeat")
        assert row is not None
        row.expires_at = near_expiry

    with jobqueue.pending_charge_heartbeat(
        "", lease_project_id="p-heartbeat", lease_token=token, interval=0.05
    ):
        time.sleep(0.2)

    with session_scope() as db2:
        row = db2.get(GenerationLease, "p-heartbeat")
        assert row is not None and row.expires_at > near_expiry


def test_heartbeat_sets_abort_when_lease_is_lost(db) -> None:
    import threading

    token = _lease()
    abort = threading.Event()
    with jobqueue.pending_charge_heartbeat(
        "",
        lease_project_id="p-heartbeat",
        lease_token=token,
        abort=abort,
        interval=0.05,
    ):
        with session_scope() as db2:
            jobqueue.release_generation_lease(db2, "p-heartbeat", token)
        assert abort.wait(2.0), "租约失效后必须传播 abort"
    with session_scope() as db2:
        jobqueue.release_generation_lease(db2, "p-heartbeat", token)


def test_heartbeat_aborts_after_three_consecutive_db_errors(db, monkeypatch) -> None:
    import threading

    token = _lease()
    abort = threading.Event()
    monkeypatch.setattr(jobqueue, "renew_generation_lease", lambda *_: "retry")
    with jobqueue.pending_charge_heartbeat(
        "", lease_project_id="p-heartbeat", lease_token=token, abort=abort, interval=0.05
    ):
        assert abort.wait(2.0), "连续 3 次续期失败后必须传播 abort"


def test_heartbeat_interval_is_bounded_by_lease_ttl(monkeypatch) -> None:
    monkeypatch.setenv("BIDCRAFT_GENERATION_LEASE_TTL", "180")
    monkeypatch.setenv("BIDCRAFT_GENERATION_HEARTBEAT_INTERVAL", "10000")
    settings = Settings()
    assert settings.generation_lease_ttl_seconds == 180
    assert settings.generation_heartbeat_interval_seconds <= 180 // 3
    assert jobqueue._generation_heartbeat_seconds() <= 60


def test_worker_passes_abort_event_to_heartbeat() -> None:
    source = (
        Path(__file__).parents[1] / "bidcraft" / "workers" / "generation.py"
    ).read_text(encoding="utf-8")
    assert "abort=job_abort_event()" in source
