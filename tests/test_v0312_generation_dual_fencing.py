"""v0.31.2 阶段 3：GenerationLease + Job claim 双重 fencing（真实生产路径）。

所有章节写入都调用 `bidcraft.workers.generation.commit_generation_chapter`，
即生产 Worker 实际使用的函数；不使用测试自造回调。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from bidcraft import jobqueue
from bidcraft.clock import utc_now
from bidcraft.db import session_scope
from bidcraft.generation.outline import build_outline, load_outline, save_outline
from bidcraft.jobqueue import JobAborted, claim_context
from bidcraft.models import GenerationLease, Job, JobEvent, OutlineNode, Section
from bidcraft.pipeline import add_source, create_project, parse_project
from bidcraft.rules.loader import load_package
from bidcraft.workers.generation import commit_generation_chapter

TOKEN = "v0312-dual-token"


def _prepare(tender_path):
    with session_scope() as db:
        project = create_project(db, "双重围栏项目", "weakcurrent-cabling")
        add_source(db, project, tender_path)
        ledger, _ = parse_project(db, project.id)
        package = load_package(project.package)
        spec = build_outline(ledger, package)
        save_outline(db, project.id, spec)
        db.flush()
        nodes = load_outline(db, project.id)
        return project.id, nodes[0]["id"], nodes[0]["section_id"]


def _seed(project_id: str, *, lease_valid: bool = True) -> str:
    with session_scope() as db:
        row = Job(
            kind="generate",
            status="running",
            claim_token=TOKEN,
            lease_expires_at=utc_now() + timedelta(seconds=300),
            attempts=1,
        )
        db.add(row)
        db.flush()
        job_id = row.id
    with session_scope() as lease_db:
        jobqueue.acquire_generation_lease(
            lease_db,
            project_id,
            job_id=job_id,
            owner_id="",
            ttl_seconds=300,
        )
    if not lease_valid:
        with session_scope() as db:
            db.execute(jobqueue.delete(jobqueue.GenerationLease).where(jobqueue.GenerationLease.project_id == project_id))
    return job_id


def _info(node_id: str, section_id: str) -> dict[str, Any]:
    return {"node_id": node_id, "section_id": section_id, "words": 6}


def _state(job_id: str, node_id: str, section_id: str):
    with session_scope() as db:
        section = db.get(Section, section_id)
        outline = db.get(OutlineNode, node_id)
        events = list(
            db.scalars(
                select(JobEvent.kind)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.id)
            )
        )
        job = db.get(Job, job_id)
        done = (job.payload or {}).get("done_steps") or [] if job else []
        step = f"chapter:{node_id}" in done
        return (
            section.body if section else None,
            outline.status if outline else None,
            step,
            events,
        )


def _lease_token(project_id: str) -> str:
    with session_scope() as lease_db:
        return str(
            lease_db.scalar(
                select(GenerationLease.token).where(
                    GenerationLease.project_id == project_id
                )
            )
            or ""
        )


def _write(
    job_id,
    project_id,
    node_id,
    section_id,
    body="授权正文",
    *,
    token=TOKEN,
    lease_token=None,
):
    with claim_context(token):
        commit_generation_chapter(
            job_id,
            project_id,
            lease_token if lease_token is not None else _lease_token(project_id),
            _info(node_id, section_id),
            body,
            "model",
        )


def _pair(a: Callable[[], Any], b: Callable[[], Any]):
    barrier = threading.Barrier(2)
    results: list[Any] = [None, None]
    errors: list[BaseException] = []

    def run(index: int, fn: Callable[[], Any]) -> None:
        try:
            barrier.wait(30)
            results[index] = fn()
        except BaseException as exc:  # noqa: BLE001 - test captures all
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(0, a), daemon=True),
        threading.Thread(target=run, args=(1, b), daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads)
    return results, errors


def _assert_clean(state) -> None:
    body, outline_status, step, events = state
    assert body in (None, "")
    assert outline_status in (None, "draft", "pending")
    assert step in (None, False)
    assert "chapter_committed" not in events


def test_normal_dual_fence_commits_everything(db, tender_path) -> None:
    project_id, node_id, section_id = _prepare(tender_path)
    job_id = _seed(project_id)
    _write(job_id, project_id, node_id, section_id)
    body, status, step, events = _state(job_id, node_id, section_id)
    assert body == "授权正文"
    assert status == "drafted"
    assert step is True
    assert events == ["chapter_committed"]


def test_job_token_invalid_rejects(db, tender_path) -> None:
    project_id, node_id, section_id = _prepare(tender_path)
    job_id = _seed(project_id)
    with session_scope() as writer_db:
        writer_db.get(Job, job_id).claim_token = "new-token"
    with pytest.raises(JobAborted):
        _write(job_id, project_id, node_id, section_id, token=TOKEN)
    _assert_clean(_state(job_id, node_id, section_id))


def test_generation_lease_invalid_rejects(db, tender_path) -> None:
    project_id, node_id, section_id = _prepare(tender_path)
    job_id = _seed(project_id, lease_valid=False)
    with pytest.raises(JobAborted):
        _write(job_id, project_id, node_id, section_id, lease_token="invalid")
    _assert_clean(_state(job_id, node_id, section_id))


def test_dual_fence_single_side_failures_reject(db, tender_path) -> None:
    for lease_valid in (True, False):
        project_id, node_id, section_id = _prepare(tender_path)
        job_id = _seed(project_id, lease_valid=lease_valid)
        with session_scope() as writer_db:
            writer_db.get(Job, job_id).claim_token = "new-token"
        with pytest.raises(JobAborted):
            _write(job_id, project_id, node_id, section_id)


def test_mid_transaction_error_rolls_back_all(db, tender_path, monkeypatch) -> None:
    project_id, node_id, section_id = _prepare(tender_path)
    job_id = _seed(project_id)
    import bidcraft.workers.generation as generation_worker

    original = generation_worker.mark_step_done

    def fail(*args, **kwargs):
        raise RuntimeError("模拟 done_steps 写入失败")

    monkeypatch.setattr(generation_worker, "mark_step_done", fail)
    with pytest.raises(RuntimeError, match="done_steps"):
        _write(job_id, project_id, node_id, section_id)
    monkeypatch.setattr(generation_worker, "mark_step_done", original)
    _assert_clean(_state(job_id, node_id, section_id))


def test_stale_identity_map_cannot_bypass(db, tender_path) -> None:
    project_id, node_id, section_id = _prepare(tender_path)
    job_id = _seed(project_id)
    with session_scope() as stale_db:
        stale = stale_db.get(Job, job_id)
        assert stale.claim_token == TOKEN
        with session_scope() as reaper_db:
            reaper_db.get(Job, job_id).claim_token = "new-token"
        assert stale.claim_token == TOKEN  # identity map still stale
        with pytest.raises(JobAborted):
            _write(job_id, project_id, node_id, section_id)


def test_duplicate_chapter_write_is_idempotent(db, tender_path) -> None:
    project_id, node_id, section_id = _prepare(tender_path)
    job_id = _seed(project_id)
    _write(job_id, project_id, node_id, section_id, body="唯一正文")
    # 重复投递是幂等 no-op：生产函数提前 return，不覆盖正文也不再发成功事件。
    _write(job_id, project_id, node_id, section_id, body="重复正文")
    body, _status, step, events = _state(job_id, node_id, section_id)
    assert body == "唯一正文"
    assert step is True
    assert events.count("chapter_committed") == 1


def test_100_rounds_job_claim_race(db, tender_path) -> None:
    for _round in range(100):
        project_id, node_id, section_id = _prepare(tender_path)
        job_id = _seed(project_id)

        def revoke(job_id=job_id):
            with session_scope() as reaper_db:
                reaper_db.get(Job, job_id).claim_token = "new-token"
            return "revoked"

        def writer(job_id=job_id, project_id=project_id, node_id=node_id, section_id=section_id):
            try:
                _write(job_id, project_id, node_id, section_id)
                return "committed"
            except JobAborted:
                return "aborted"

        results, errors = _pair(writer, revoke)
        assert not errors
        assert results[1] == "revoked"
        state = _state(job_id, node_id, section_id)
        if results[0] == "aborted":
            _assert_clean(state)
        else:
            assert results[0] == "committed"
            body, _status, step, events = state
            assert body == "授权正文"
            assert step is True
            assert events.count("chapter_committed") == 1


def test_100_rounds_generation_lease_race(db, tender_path) -> None:
    for _round in range(100):
        project_id, node_id, section_id = _prepare(tender_path)
        job_id = _seed(project_id)

        def release(project_id=project_id):
            with session_scope() as release_db:
                release_db.execute(
                    jobqueue.delete(jobqueue.GenerationLease).where(
                        jobqueue.GenerationLease.project_id == project_id
                    )
                )
            return "released"

        def writer(job_id=job_id, project_id=project_id, node_id=node_id, section_id=section_id):
            try:
                _write(job_id, project_id, node_id, section_id)
                return "committed"
            except JobAborted:
                return "aborted"

        results, errors = _pair(writer, release)
        assert not errors
        assert results[1] == "released"
        state = _state(job_id, node_id, section_id)
        body, _status, step, events = state
        # Writer 可能在租约删除前完成合法提交；只要求最终状态自洽。
        if "chapter_committed" in events:
            assert body == "授权正文"
            assert step is True
        else:
            _assert_clean(state)


def test_100_rounds_chapter_vs_reaper(db, tender_path) -> None:
    for _round in range(100):
        project_id, node_id, section_id = _prepare(tender_path)
        job_id = _seed(project_id)

        def reclaim(job_id=job_id):
            with session_scope() as reclaim_db:
                row = reclaim_db.get(Job, job_id)
                return jobqueue._requeue(
                    reclaim_db, row, "租约竞争", lease_expired_before=utc_now()
                )

        _results, errors = _pair(
            lambda job_id=job_id, project_id=project_id, node_id=node_id, section_id=section_id: _write(
                job_id, project_id, node_id, section_id
            ),
            reclaim,
        )
        assert not errors
        body, _status, step, events = _state(job_id, node_id, section_id)
        committed = events.count("chapter_committed") == 1
        assert committed == (body == "授权正文" and step is True)
        assert events.count("queued") <= 1
