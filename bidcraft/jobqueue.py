"""A small in-process priority job queue.

User-facing background jobs (batch check/export, image batch, generation) are
submitted here instead of spawning an unbounded thread per request. A bounded
pool of daemon workers drains a min-heap ordered by ``(priority, sequence)`` so
that VIP jobs (priority 0) are picked ahead of regular jobs (priority 1) whenever
workers are contended. Lower priority value runs first; ties keep FIFO order.
"""

from __future__ import annotations

import heapq
import itertools
import threading
from collections.abc import Callable
from typing import Any

from .config import settings

_lock = threading.Condition()
_heap: list[tuple[int, int, Callable[..., Any], tuple, str]] = []
_seq = itertools.count()
_workers_started = False


def _max_workers() -> int:
    try:
        value = int(settings.max_concurrent_jobs)
    except (TypeError, ValueError):
        value = 2
    return max(1, min(8, value))


def submit(
    target: Callable[..., Any],
    args: tuple = (),
    *,
    priority: int = 1,
    job_id: str = "",
) -> None:
    """Enqueue ``target(*args)``; start the worker pool lazily on first use."""
    global _workers_started
    with _lock:
        heapq.heappush(_heap, (int(priority), next(_seq), target, args, job_id))
        if not _workers_started:
            _workers_started = True
            for _ in range(_max_workers()):
                threading.Thread(
                    target=_worker, daemon=True, name="bidcraft-job"
                ).start()
        _lock.notify_all()


def position(job_id: str) -> int:
    """1-based queue position among still-pending jobs; 0 if running/unknown."""
    if not job_id:
        return 0
    with _lock:
        target_entry = None
        for entry in _heap:
            if entry[4] == job_id:
                target_entry = entry
                break
        if target_entry is None:
            return 0
        ahead = sum(
            1
            for entry in _heap
            if (entry[0], entry[1]) < (target_entry[0], target_entry[1])
        )
        return ahead + 1


def _worker() -> None:
    while True:
        with _lock:
            while not _heap:
                _lock.wait()
            _priority, _seqno, target, args, _job_id = heapq.heappop(_heap)
        try:
            target(*args)
        except Exception:  # noqa: BLE001, S110 - job functions own their error status
            pass


def _reset() -> None:
    """Test helper: drop pending entries (workers, if any, keep waiting)."""
    with _lock:
        _heap.clear()
