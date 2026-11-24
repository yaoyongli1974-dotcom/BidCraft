"""v0.31.1 阶段 3：章节级 fencing 写入。"""

from __future__ import annotations

from sqlalchemy import select

from bidcraft import jobqueue
from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.generation.agent import ModelClient
from bidcraft.generation.outline import build_outline, load_outline, save_outline
from bidcraft.generation.writer import generate_document
from bidcraft.jobqueue import JobAborted, claim_context
from bidcraft.models import Job, JobEvent, Section
from bidcraft.pipeline import add_source, create_project, parse_project
from bidcraft.rules.loader import load_package

TOKEN = "chapter-fence-token"


def _prepare(tender_path):
    with session_scope() as session:
        project = create_project(session, "章节围栏项目", "weakcurrent-cabling")
        add_source(session, project, tender_path)
        ledger, _ = parse_project(session, project.id)
        package = load_package(project.package)
        spec = build_outline(ledger, package)
        save_outline(session, project.id, spec)
        session.commit()
        return project.id, ledger, package


def _seed_job() -> str:
    with session_scope() as db:
        job = Job(kind="generate", status="running", claim_token=TOKEN)
        db.add(job)
        db.flush()
        return job.id


def _write_via_fence(job_id, node_id, section_id, body, *, token=TOKEN):
    with claim_context(token), jobqueue.fenced_chapter_write(job_id, node_id) as (
        db,
        _row,
    ):
        section = db.get(Section, section_id)
        assert section is not None
        section.body = body
        section.words = 7
        section.status = "drafted"
        jobqueue.mark_step_done(db, job_id, f"chapter:{node_id}", token)


def test_chapter_fence_commits_body_and_done_step(db, tender_path) -> None:
    project_id, _ledger, _package = _prepare(tender_path)
    with session_scope() as reader_db:
        node = load_outline(reader_db, project_id)[0]
    job_id = _seed_job()
    _write_via_fence(job_id, node["id"], node["section_id"], "已授权章节正文")
    with session_scope() as reader_db:
        assert reader_db.get(Section, node["section_id"]).body == "已授权章节正文"
    with claim_context(TOKEN), session_scope() as reader_db:
        assert jobqueue.step_status(reader_db, job_id, f"chapter:{node['id']}") is True
        events = list(
            reader_db.scalars(select(JobEvent.kind).where(JobEvent.job_id == job_id))
        )
        assert events == []


def test_stale_token_cannot_write_chapter(db, tender_path) -> None:
    project_id, _ledger, _package = _prepare(tender_path)
    with session_scope() as reader_db:
        node = load_outline(reader_db, project_id)[0]
    job_id = _seed_job()
    try:
        _write_via_fence(
            job_id, node["id"], node["section_id"], "不应落盘", token="stale"
        )
    except JobAborted:
        pass
    else:
        raise AssertionError("失效 token 未触发 JobAborted")
    with session_scope() as reader_db:
        section = reader_db.get(Section, node["section_id"])
        assert not (section.body or "")
        assert jobqueue.step_status(db, job_id, f"chapter:{node['id']}") is None


def test_writer_delegates_body_write_to_commit_chapter(db, tender_path) -> None:
    project_id, ledger, package = _prepare(tender_path)
    seen: list[tuple[str, str]] = []

    def commit_chapter(info, body, source) -> None:
        seen.append((str(info["node_id"]), str(source)))
        with session_scope() as persist_db:
            section = persist_db.scalar(
                select(Section).where(Section.id == info["section_id"])
            )
            if section is not None:
                section.body = body
                section.words = int(info.get("words") or 0)
                section.status = "drafted"

    with session_scope() as session:
        report = generate_document(
            session,
            project_id,
            ModelClient(settings),
            ledger,
            package,
            commit_chapter=commit_chapter,
        )
    assert len(seen) == len(report.written)
    assert seen
    with session_scope() as reader_db:
        node = load_outline(reader_db, project_id)[0]
        assert (reader_db.get(Section, node["section_id"]).body or "")
