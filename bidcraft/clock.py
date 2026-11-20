"""统一的中国时区时钟。

用户全部在中国境内，「到期日」「有效期至」这类**日历日**判断必须按 UTC+8：
服务器跑在 UTC 时，北京时间 00:00–08:00 用 UTC 取日期会晚一天，昨天到期的
证书会被判成未过期。这里集中提供时区，避免各模块各写一份 `date.today()`。
"""

from __future__ import annotations

import datetime as dt

#: 中国标准时间（无夏令时，固定 UTC+8）。
BEIJING_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")


def now() -> dt.datetime:
    """当前北京时间（带时区）。"""
    return dt.datetime.now(BEIJING_TZ)


def today() -> dt.date:
    """当前北京日期。"""
    return now().date()
