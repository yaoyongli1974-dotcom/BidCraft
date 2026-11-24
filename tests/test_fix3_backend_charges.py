"""第三轮审计：崩溃窗口扣费的 TTL 配置、周期对账与 CLI 入口。"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("typer")


def test_pending_charge_ttl_config_from_env(monkeypatch):
    from bidcraft.config import Settings

    monkeypatch.delenv("BIDCRAFT_PENDING_CHARGE_TTL", raising=False)
    assert Settings().pending_charge_ttl_seconds == 1800

    monkeypatch.setenv("BIDCRAFT_PENDING_CHARGE_TTL", "42")
    assert Settings().pending_charge_ttl_seconds == 42

    # 负值不应让 cutoff 跑到未来（等价于立刻退款的 0）。
    monkeypatch.setenv("BIDCRAFT_PENDING_CHARGE_TTL", "-5")
    assert Settings().pending_charge_ttl_seconds == 0


def _seed_pending_charge(credits: int = 4, chapters: int = 2) -> tuple[str, str]:
    from bidcraft import billing, jobqueue
    from bidcraft.db import session_scope
    from bidcraft.models import User

    with session_scope() as session:
        user = User(
            username="fix3-charge", password_hash="x", role="manager",
            is_external=True, credits=0,
        )
        session.add(user)
        session.flush()
        billing.add_credits(session, user.id, 20, kind="admin", note="seed")
        marker_id = jobqueue.begin_pending_charge(session, user, "p1", credits, chapters)
        session.flush()
        return marker_id, user.id


def test_reconcile_uses_configured_ttl(db, monkeypatch):
    from bidcraft import jobqueue
    from bidcraft.clock import utc_now
    from bidcraft.config import settings
    from bidcraft.db import session_scope
    from bidcraft.models import PendingCharge, User

    marker_id, user_id = _seed_pending_charge()
    with session_scope() as session:
        session.get(PendingCharge, marker_id).created_at = utc_now().replace(
            year=2000
        )  # 很久以前的崩溃残留

    monkeypatch.setattr(settings, "pending_charge_ttl_seconds", 3600)
    assert jobqueue.reconcile_pending_charges() == 1
    with session_scope() as session:
        assert session.get(PendingCharge, marker_id).status == "refunded"
        assert session.get(User, user_id).credits == 24


def test_reconcile_charges_cli_settles_markers(db, monkeypatch):
    from typer.testing import CliRunner

    from bidcraft.cli import app
    from bidcraft.config import settings
    from bidcraft.db import session_scope
    from bidcraft.models import PendingCharge, User

    marker_id, user_id = _seed_pending_charge()
    monkeypatch.setattr(settings, "pending_charge_ttl_seconds", 0)

    result = CliRunner().invoke(app, ["reconcile-charges"])
    assert result.exit_code == 0, result.output
    assert "已结清 1 条未结扣费标记" in result.output

    with session_scope() as session:
        assert session.get(PendingCharge, marker_id).status == "refunded"
        assert session.get(User, user_id).credits == 24

    # 重复执行不重复退款。
    again = CliRunner().invoke(app, ["reconcile-charges"])
    assert again.exit_code == 0, again.output
    assert "已结清 0 条未结扣费标记" in again.output
    with session_scope() as session:
        assert session.get(User, user_id).credits == 24


def test_reaper_periodically_sweeps_pending_charges(db, monkeypatch):
    """未启动 worker 之外的长寿命进程也需要周期性对账（复用 reaper）。"""

    from bidcraft import jobqueue

    called = threading.Event()

    def fake_reconcile(*_args, **_kwargs) -> int:
        called.set()
        return 0

    monkeypatch.setattr(jobqueue, "reconcile_pending_charges", fake_reconcile)
    monkeypatch.setattr(jobqueue, "_RECONCILE_INTERVAL", 0.01)
    monkeypatch.setattr(jobqueue, "_REAP_INTERVAL", 0.01)
    try:
        jobqueue._start_reaper()
        assert called.wait(2), "reaper 没有触发未结扣费对账"
    finally:
        jobqueue._reset()
