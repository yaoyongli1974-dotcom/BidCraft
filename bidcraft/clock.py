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


def iso_utc(value: dt.datetime | dt.date | None) -> str | None:
    """序列化数据库里的 naive UTC 时间戳为**带时区**的 ISO 字符串。

    不带 ``+00:00`` 的字符串会让前端 ``new Date()`` 按本地时区解析，显示早 8 小时。
    ``date`` 原样输出（只表示日历日，不含时刻）。
    """
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.UTC)
        return value.isoformat()
    return value.isoformat()


def local_date(value: dt.datetime | None) -> dt.date | None:
    """UTC 时间戳对应的北京日历日（按日过滤/归档时用）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone(BEIJING_TZ).date()


def format_local(value: dt.datetime | None, pattern: str = "%Y-%m-%d %H:%M:%S") -> str:
    """按北京时间格式化时间戳（CSV / 文件名 / 展示文本）。"""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone(BEIJING_TZ).strftime(pattern)
