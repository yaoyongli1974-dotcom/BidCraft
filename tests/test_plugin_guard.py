from bidcraft import plugins
from bidcraft.db import session_scope
from bidcraft.models import Plugin


def _plugin(s):
    row = Plugin(name="guard", transport="stdio", command="python", args=[], env={}, url="",
                 enabled=True, tool_allowlist=[])
    s.add(row); s.flush(); return row


def test_auto_disable_and_reset(db):
    with session_scope() as s:
        row = _plugin(s)
        plugins.record_outcome(s, row, False)
        assert row.fail_streak == 1 and row.enabled is True
        plugins.record_outcome(s, row, False)
        plugins.record_outcome(s, row, False)
        assert row.fail_streak == 3 and row.enabled is False and row.auto_disabled is True
        assert "自动停用" in (row.last_test_message or "")


def test_success_and_reset_guard(db):
    with session_scope() as s:
        row = _plugin(s)
        plugins.record_outcome(s, row, False)
        plugins.record_outcome(s, row, False)
        plugins.record_outcome(s, row, True)
        assert row.fail_streak == 0 and row.auto_disabled is False
        plugins.record_outcome(s, row, False)
        plugins.record_outcome(s, row, False)
        plugins.record_outcome(s, row, False)
        row.enabled = True
        plugins.reset_guard(s, row)
        assert row.fail_streak == 0 and row.auto_disabled is False
