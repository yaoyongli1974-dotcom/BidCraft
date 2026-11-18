from __future__ import annotations

import threading
import time

from bidcraft import jobqueue
from bidcraft.config import settings


def test_max_workers_clamp(monkeypatch):
    for value, expected in [(0, 1), (1, 1), (2, 2), (8, 8), (99, 8), (-5, 1)]:
        monkeypatch.setattr(settings, "max_concurrent_jobs", value)
        assert jobqueue._max_workers() == expected
    monkeypatch.setattr(settings, "max_concurrent_jobs", "not-a-number")
    assert jobqueue._max_workers() == 2


def test_position_unknown_and_empty():
    assert jobqueue.position("") == 0
    assert jobqueue.position("does-not-exist") == 0


def test_submit_executes_target():
    done = threading.Event()
    seen: list = []

    def work(a, b):
        seen.append((a, b))
        done.set()

    jobqueue.submit(work, (3, 4), job_id="exec-test")
    assert done.wait(5)
    assert seen == [(3, 4)]
    # once consumed it is no longer pending
    assert jobqueue.position("exec-test") == 0


def test_vip_priority_runs_first():
    release = threading.Event()
    running: list = []
    order: list = []

    def blocker():
        running.append(1)
        release.wait(10)

    # Saturate every existing worker with a blocker so the ordered jobs must queue.
    for i in range(8):
        jobqueue.submit(blocker, job_id=f"blk{i}")
    time.sleep(0.3)
    # Drop any blockers that are still queued (more blockers than workers).
    with jobqueue._lock:
        jobqueue._heap[:] = [e for e in jobqueue._heap if not e[4].startswith("blk")]

    jobqueue.submit(lambda: order.append("A"), priority=1, job_id="A")
    jobqueue.submit(lambda: order.append("B"), priority=0, job_id="B")
    jobqueue.submit(lambda: order.append("C"), priority=1, job_id="C")

    assert jobqueue.position("B") == 1
    assert jobqueue.position("A") == 2
    assert jobqueue.position("C") == 3

    release.set()
    deadline = time.time() + 5
    while len(order) < 3 and time.time() < deadline:
        time.sleep(0.02)
    assert set(order) == {"A", "B", "C"}
    assert order[0] == "B"  # VIP priority 0 picked ahead of regular priority 1
