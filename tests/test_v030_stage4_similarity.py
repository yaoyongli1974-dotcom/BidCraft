"""v0.30 stage 4：查重重试幂等 + worker 租约失效回滚。

全部落在本地 SQLite/内存风格的 db fixture 上；相似度维度用假 runner 替代，
不触发任何外部模型/图片服务。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

pytest.importorskip("fastapi")

HIT_A = {
    "kind": "text",
    "source_a": "a.txt",
    "source_b": "b.txt",
    "loc_a": "1",
    "loc_b": "2",
    "excerpt_a": "技术方案甲",
    "excerpt_b": "技术方案乙",
    "similarity": 0.91,
    "evidence": {},
}
HIT_B = {**HIT_A, "loc_a": "3", "loc_b": "4", "similarity": 0.87}


def _project() -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import Project

    with session_scope() as db:
        project = Project(name="stage4-查重项目", package="weakcurrent-cabling")
        db.add(project)
        db.flush()
        return project.id


def _report(project_id: str, *, status: str = "queued") -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import SimilarityReport

    with session_scope() as db:
        report = SimilarityReport(
            project_id=project_id,
            scope="project",
            mode="text",
            threshold=0.85,
            status=status,
            stats={},
            skipped=[],
            created_by="",
        )
        db.add(report)
        db.flush()
        return report.id


def _job(project_id: str, report_id: str, token: str) -> str:
    from bidcraft.db import session_scope
    from bidcraft.models import Job

    with session_scope() as db:
        job = Job(
            project_id=project_id,
            kind="similarity",
            status="running",
            claim_token=token,
            payload={
                "project_id": project_id,
                "report_id": report_id,
                "scope": "project",
                "mode": "text",
                "threshold": 0.85,
                "created_by": "",
            },
        )
        db.add(job)
        db.flush()
        return job.id


def _counts(report_id: str) -> tuple[int, int]:
    """(该报告的命中行数, similarity.check 审计行数)。"""
    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog, SimilarityHit

    with session_scope() as db:
        hits = db.scalar(
            select(func.count())
            .select_from(SimilarityHit)
            .where(SimilarityHit.report_id == report_id)
        )
        audits = db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "similarity.check")
        )
    return int(hits or 0), int(audits or 0)


def _events(job_id: str, kind: str) -> int:
    from bidcraft.db import session_scope
    from bidcraft.models import JobEvent

    with session_scope() as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(JobEvent)
                .where(JobEvent.job_id == job_id, JobEvent.kind == kind)
            )
            or 0
        )


def _fake_text_runner(monkeypatch, hits=None):
    """把 text 维度换成确定性的假结果，返回调用次数列表。"""
    from bidcraft.similarity import service

    calls: list[tuple] = []
    result_hits = [dict(HIT_A), dict(HIT_B)] if hits is None else [dict(item) for item in hits]

    def _run(db, project_id, source_project_id, threshold):
        calls.append((project_id, source_project_id, threshold))
        return {
            "hits": [dict(item) for item in result_hits],
            "skipped": [],
            "stats": {"hits": len(result_hits)},
        }

    monkeypatch.setitem(service._RUNNERS, "text", _run)
    return calls


def test_done_report_rerun_returns_same_result_without_duplicates(db, monkeypatch):
    """已 done 的报告复用 report_id 重跑：原样返回，不重算/不追加命中/不记审计。"""
    from bidcraft.db import session_scope
    from bidcraft.similarity import service

    project_id = _project()
    report_id = _report(project_id)
    calls = _fake_text_runner(monkeypatch)

    with session_scope() as session:
        first = service.run_similarity(session, project_id, mode="text", report_id=report_id)
        assert first.status == "done"
        first_stats = dict(first.stats or {})
        first_completed = first.completed_at
    first_hits, first_audits = _counts(report_id)
    assert (first_hits, first_audits) == (2, 1)
    assert len(calls) == 1

    with session_scope() as session:
        second = service.run_similarity(session, project_id, mode="text", report_id=report_id)
        assert second.id == report_id
        assert second.status == "done"
        assert dict(second.stats or {}) == first_stats
        assert second.completed_at == first_completed

    assert _counts(report_id) == (first_hits, first_audits)
    assert len(calls) == 1  # 维度 runner 没有被再次执行


def test_unfinished_report_rerun_drops_stale_hits_before_recompute(db, monkeypatch):
    """未完成的报告重试：先删旧命中并重置 stats/skipped，只剩一批新命中。"""
    from bidcraft.db import session_scope
    from bidcraft.models import SimilarityHit, SimilarityReport
    from bidcraft.similarity import service

    project_id = _project()
    report_id = _report(project_id)
    other_report_id = _report(project_id)
    with session_scope() as session:
        for report_id_for_hit in (report_id, report_id, other_report_id):
            session.add(
                SimilarityHit(
                    report_id=report_id_for_hit,
                    kind="text",
                    loc_a="旧",
                    loc_b="旧",
                    similarity=0.5,
                )
            )
        session.get(SimilarityReport, report_id).stats = {"hits": 2, "stale": True}
        session.flush()
    assert _counts(report_id)[0] == 2

    _fake_text_runner(monkeypatch, hits=[HIT_A])
    with session_scope() as session:
        report = service.run_similarity(session, project_id, mode="text", report_id=report_id)
        assert report.status == "done"
        assert report.stats["hits"] == 1
        assert "stale" not in report.stats
        assert report.skipped == []

    hits, audits = _counts(report_id)
    assert (hits, audits) == (1, 1)
    # 另一份报告的命中不能被波及。
    assert _counts(other_report_id)[0] == 1


def test_reused_report_project_mismatch_still_rejected(db, monkeypatch):
    """保留 report_id 的归属校验：跨项目复用直接 LookupError。"""
    from bidcraft.db import session_scope
    from bidcraft.similarity import service

    project_id = _project()
    other_project_id = _project()
    report_id = _report(project_id)
    _fake_text_runner(monkeypatch)

    with session_scope() as session, pytest.raises(LookupError):
        service.run_similarity(session, other_project_id, mode="text", report_id=report_id)
    assert _counts(report_id) == (0, 0)


def test_worker_lost_lease_midrun_aborts_without_writing_report(db, monkeypatch):
    """中途换 token：fencing 事务拒绝提交，报告/命中/审计一律不落库。"""
    from bidcraft import jobqueue
    from bidcraft.db import session_scope
    from bidcraft.models import Job
    from bidcraft.similarity import service
    from bidcraft.workers import similarity as worker

    project_id = _project()
    report_id = _report(project_id)
    job_id = _job(project_id, report_id, "token-A")
    calls = _fake_text_runner(monkeypatch)

    real_prepare = service.prepare_similarity_result

    def _prepare_then_lose_lease(db, *args, **kwargs):
        prepared = real_prepare(db, *args, **kwargs)
        # 模拟 reaper 在比对运行中回收租约并提交新 token。
        with session_scope() as fresh:
            fresh.get(Job, job_id).claim_token = "token-B"
        return prepared

    monkeypatch.setattr(service, "prepare_similarity_result", _prepare_then_lose_lease)
    failed = []
    monkeypatch.setattr(worker, "_fail_job", lambda *a, **kw: failed.append((a, kw)))

    with jobqueue.claim_context("token-A"):
        worker._run_similarity_job(job_id)

    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job.status == "running"
        assert job.claim_token == "token-B"
        assert job.result in (None, {})
    assert calls and len(calls) == 1  # 计算确实发生过，但最终提交被拒绝
    assert _counts(report_id) == (0, 0)  # 报告/命中/审计都未提交
    assert _events(job_id, "failed") == 0
    assert _events(job_id, "done") == 0
    assert failed == []


def test_final_done_write_is_fenced_by_conditional_claim(db, monkeypatch):
    """最终提交用条件 UPDATE 原子校验 token；旧 worker 不能覆盖新 worker。"""
    from bidcraft import jobqueue
    from bidcraft.db import session_scope
    from bidcraft.models import Job
    from bidcraft.similarity import service
    from bidcraft.workers import similarity as worker

    project_id = _project()
    report_id = _report(project_id)
    job_id = _job(project_id, report_id, "token-A")
    _fake_text_runner(monkeypatch)

    commit_calls: list[str] = []
    real_commit = jobqueue.commit_fenced_result

    def _commit_with_lost_lease(jid):
        commit_calls.append(jid)
        # 在工作会话提交后、最终 fencing 条件 UPDATE 之前换 token。
        with session_scope() as fresh:
            fresh.get(Job, jid).claim_token = "token-B"
        return real_commit(jid)

    monkeypatch.setattr(jobqueue, "commit_fenced_result", _commit_with_lost_lease)

    work_sessions: list = []
    real_prepare = service.prepare_similarity_result

    def _track_prepare(session, *args, **kwargs):
        work_sessions.append(session)
        return real_prepare(session, *args, **kwargs)

    monkeypatch.setattr(service, "prepare_similarity_result", _track_prepare)

    with jobqueue.claim_context("token-A"):
        worker._run_similarity_job(job_id)

    assert commit_calls == [job_id]
    with session_scope() as check:
        stale = check.get(Job, job_id)
        assert stale.status == "running"
        assert stale.claim_token == "token-B"
        assert stale.result in (None, {})
    assert _events(job_id, "done") == 0
    assert _events(job_id, "failed") == 0
    # 事务级 fencing：报告/命中/审计与 Job 终态同事务，旧 worker 全都不写。
    assert _counts(report_id) == (0, 0)
    assert work_sessions  # 计算阶段确实在独立会话中执行


def test_api_shared_exposes_similarity_runner():
    """API 名字空间导出 runner，供 monkeypatch/崩溃注入测试使用。"""
    import importlib

    from bidcraft.workers import _run_similarity_job

    shared = importlib.import_module("bidcraft.api.shared")

    assert shared._run_similarity_job is _run_similarity_job
    assert "_run_similarity_job" in shared.__all__
