"""章节级可靠性钩子：abort 检查点与幂等账本在真实 writer 路径上生效。"""

from __future__ import annotations

import pytest

from bidcraft.config import settings
from bidcraft.db import session_scope
from bidcraft.generation.agent import ModelClient
from bidcraft.generation.outline import build_outline, save_outline
from bidcraft.generation.writer import generate_document
from bidcraft.jobqueue import JobAborted
from bidcraft.pipeline import add_source, create_project, parse_project
from bidcraft.rules.loader import load_package


def _prepare(tender_path):
    with session_scope() as session:
        project = create_project(session, "可靠性 writer 项目", "weakcurrent-cabling")
        add_source(session, project, tender_path)
        ledger, _ = parse_project(session, project.id)
        package = load_package(project.package)
        spec = build_outline(ledger, package)
        save_outline(session, project.id, spec)
        session.commit()
        return project.id, ledger, package, [item.heading for item in spec.items]


def test_before_chapter_can_skip_and_abort(db, tender_path):
    project_id, ledger, package, headings = _prepare(tender_path)
    skip = headings[1]

    with session_scope() as session:
        before_calls: list[str] = []
        after_calls: list[str] = []

        def before(info, chapter_db):
            before_calls.append(info["heading"])
            return info["heading"] != skip  # 跳过第二章（模拟已完成单元）

        def after(info, chapter_db):
            after_calls.append(info["heading"])

        client = ModelClient(settings)  # 无模型 → 走确定性 fallback 正文
        report = generate_document(
            session,
            project_id,
            client,
            ledger,
            package,
            before_chapter=before,
            after_chapter=after,
        )
    assert len(headings) >= 2
    assert skip in before_calls
    assert skip not in report.written
    assert skip not in after_calls
    assert set(after_calls) == set(report.written)


def test_before_chapter_abort_propagates(db, tender_path):
    project_id, ledger, package, _headings = _prepare(tender_path)

    with session_scope() as session:

        def before(info, chapter_db):
            raise JobAborted("租约失效")

        with pytest.raises(JobAborted):
            generate_document(
                session,
                project_id,
                ModelClient(settings),
                ledger,
                package,
                before_chapter=before,
            )
