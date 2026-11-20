"""时区回归：北京日历日、带偏移的时间戳序列化、对账单按日归档。"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from bidcraft import clock, compliance
from bidcraft.db import session_scope
from bidcraft.models import CreditTransaction, User

#: payload 中直接 ``.isoformat()`` 的字段：naive UTC 不带 +00:00，前端 new Date()
#: 会按本地时区解析，显示早 8 小时。新增字段请用 clock.iso_utc() / 各模块 _iso()。
NAIVE_ISO = re.compile(
    r"\.(?:created_at|updated_at|submitted_at|reviewed_at|used_at|expires_at|due_at"
    r"|accepted_at|requested_at|issued_at|uploaded_at|last_login_at)\.isoformat\(\)"
)


def test_iso_utc_attaches_offset():
    assert clock.iso_utc(None) is None
    assert clock.iso_utc(dt.datetime(2026, 11, 20, 2, 0)) == "2026-11-20T02:00:00+00:00"
    assert (
        clock.iso_utc(dt.datetime(2026, 11, 20, 10, 0, tzinfo=clock.BEIJING_TZ))
        == "2026-11-20T10:00:00+08:00"
    )
    assert clock.iso_utc(dt.date(2026, 11, 20)) == "2026-11-20"


def test_local_date_and_format_use_beijing_time():
    late = dt.datetime(2026, 11, 19, 16, 30)  # UTC → 北京 20 日 00:30
    assert clock.local_date(late) == dt.date(2026, 11, 20)
    assert clock.format_local(late) == "2026-11-20 00:30:00"
    assert clock.local_date(None) is None
    assert clock.format_local(None) == ""


def test_statement_csv_filters_and_prints_beijing_days(db):
    with session_scope() as session:
        user = User(username="tz-statement", password_hash="x", role="manager")
        session.add(user)
        session.flush()
        uid = user.id
        session.add_all(
            [
                CreditTransaction(
                    user_id=uid,
                    kind="recharge",
                    amount=1,
                    balance_after=1,
                    note="深夜",
                    created_at=dt.datetime(2026, 11, 19, 16, 30),
                ),
                CreditTransaction(
                    user_id=uid,
                    kind="recharge",
                    amount=2,
                    balance_after=3,
                    note="上午",
                    created_at=dt.datetime(2026, 11, 20, 2, 0),
                ),
            ]
        )

    # 北京时间 11-20 00:30（UTC 还是 11-19 16:30）必须算在 11-20
    with session_scope() as session:
        body = compliance.statement_csv(session, uid, start="2026-11-20")
    assert "时间（北京时间）" in body
    assert "深夜" in body and "2026-11-20 00:30:00" in body
    assert "上午" in body and "2026-11-20 10:00:00" in body

    # 按 UTC 日期过滤时这条会被错算进 11-19；按北京日期必须排除
    with session_scope() as session:
        yesterday = compliance.statement_csv(session, uid, end="2026-11-19")
    assert "深夜" not in yesterday and "上午" not in yesterday


def test_no_naive_timestamp_serialization_left():
    """源级护栏：payload 里不许再出现 ``xxx_at.isoformat()``。"""

    offenders = [
        f"{path}:{lineno}: {line.strip()}"
        for path in sorted(Path("bidcraft").rglob("*.py"))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if NAIVE_ISO.search(line)
    ]
    assert not offenders, offenders
