"""工作流时间预算：只累计「Agent 真正执行」的时间。

``workflow_runs.created_at`` 不能当作执行时长：审批挂起与 Job 排队都可能很长，
而这段时间没有 Agent 在跑。这里把累计活跃秒数/当前活跃起点放在 ``run.stats``
（JSON 列，无需迁移），在 run 进入 ``running`` 时起表、挂起或终态时停表。

所有时间都取自 :func:`bidcraft.clock.utc_now`，便于测试统一推进。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..clock import utc_now

ACTIVE_SECONDS_KEY = "_active_seconds"
ACTIVE_SINCE_KEY = "_active_since"


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed


def start_active_clock(run: Any, *, when: datetime | None = None) -> None:
    """Start (or leave running) the active-execution clock for ``run``."""

    stats = dict(run.stats or {})
    if _parse(stats.get(ACTIVE_SINCE_KEY)) is not None or stats.get(ACTIVE_SINCE_KEY):
        return
    stats[ACTIVE_SINCE_KEY] = (when or utc_now()).isoformat()
    run.stats = stats


def pause_active_clock(run: Any, *, when: datetime | None = None) -> None:
    """Close the current active interval and add it to the accumulated total."""

    stats = dict(run.stats or {})
    since_raw = stats.pop(ACTIVE_SINCE_KEY, None)
    since = _parse(since_raw)
    if since is None:
        if since_raw is not None:
            run.stats = stats  # drop malformed marker
        return
    delta = max(0.0, ((when or utc_now()) - since).total_seconds())
    stats[ACTIVE_SECONDS_KEY] = float(stats.get(ACTIVE_SECONDS_KEY) or 0.0) + delta
    run.stats = stats


def active_seconds(run: Any, *, when: datetime | None = None) -> float:
    """Return active execution seconds, excluding queue/approval waiting."""

    stats = run.stats or {}
    total = 0.0
    try:
        total = float(stats.get(ACTIVE_SECONDS_KEY) or 0.0)
    except (TypeError, ValueError):
        total = 0.0
    since = _parse(stats.get(ACTIVE_SINCE_KEY))
    if since is not None:
        total += max(0.0, ((when or utc_now()) - since).total_seconds())
    return max(0.0, total)


__all__ = [
    "ACTIVE_SECONDS_KEY",
    "ACTIVE_SINCE_KEY",
    "active_seconds",
    "pause_active_clock",
    "start_active_clock",
]
