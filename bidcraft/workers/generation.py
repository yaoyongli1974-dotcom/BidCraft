"""正文生成后台任务。

业务生成逻辑在 :mod:`bidcraft.generation.writer`；这里只负责 job 行/SSE 编排。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .. import jobs
from ..db import session_scope
from ..generation.outline import build_outline, load_outline, save_outline
from ..generation.writer import generate_document
from ..jobqueue import (
    JobAborted,
    current_claim_token,
    fenced_job,
    mark_step_done,
    raise_if_aborted,
    step_status,
)
from ..ledger import load_ledger
from ..models import Project
from ..rules.engine import ledger_gate
from ..rules.loader import load_package
from ..services.job_support import (
    _fail_job,
    _generation_client,
    _job_refund,
    _knowledge_provider,
    _report_payload,
)

logger = logging.getLogger("bidcraft.api")


def _run_generation_job(job_id: str, project_id: str, force: bool) -> None:
    """Background worker: writes chapters and streams progress into the Job row.

    Progress is persisted twice: the job row remains the fallback/polling view,
    while ``job_events`` is the resumable SSE feed used by the browser.
    """
    try:
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is None:
                return
            job.status = "running"
            job.message = "正在准备事实台账…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)
            project = db.get(Project, project_id)
            if project is None:
                raise ValueError("项目不存在")
            owner_id = str((job.payload or {}).get("user_id") or project.owner_id or "")
            ledger = load_ledger(db, project_id)
            problems = ledger_gate(ledger)
            if problems:
                raise ValueError("必填事实缺失：" + "、".join(problems))
            package = load_package(project.package)
            if not load_outline(db, project_id):
                save_outline(db, project_id, build_outline(ledger, package))
                db.flush()

        with session_scope() as work_db:
            project = work_db.get(Project, project_id)
            ledger = load_ledger(work_db, project_id)
            package = load_package(project.package)
            current = {"heading": ""}
            delta = {"text": "", "last": time.monotonic()}

            def flush_delta() -> None:
                text = delta["text"]
                if not text:
                    return
                delta["text"] = ""
                delta["last"] = time.monotonic()
                # Release any pending chapter writes before opening the short
                # delta session: SQLite allows a single writer at a time.
                work_db.commit()
                with session_scope() as delta_db:
                    jobs.publish_event(
                        job_id,
                        "delta",
                        {"heading": current["heading"], "text": text},
                        db=delta_db,
                    )

            def on_chapter_start(info: dict[str, Any]) -> None:
                # SQLite has a single writer: close the previous chapter's
                # transaction before writing the job/event rows.
                work_db.commit()
                flush_delta()
                current["heading"] = str(info.get("heading") or "")
                with session_scope() as start_db:
                    row = fenced_job(start_db, job_id)
                    if row is None:
                        return
                    row.message = f"正在生成：{current['heading']}"
                    row.payload = {
                        **(row.payload or {}),
                        "heading": current["heading"],
                        "done": max(0, int(info.get("index") or 1) - 1),
                        "total": int(info.get("total") or 0),
                    }
                    jobs.publish_event(
                        job_id,
                        "chapter_start",
                        {
                            "heading": current["heading"],
                            "index": int(info.get("index") or 0),
                            "total": int(info.get("total") or 0),
                            "target_words": int(info.get("target_words") or 0),
                        },
                        db=start_db,
                    )

            def on_progress(info: dict[str, Any]) -> None:
                work_db.commit()
                flush_delta()
                with session_scope() as progress_db:
                    row = fenced_job(progress_db, job_id)
                    if row is None:
                        return
                    row.progress = int(info.get("progress") or 0)
                    row.payload = {
                        **(row.payload or {}),
                        "heading": info.get("heading", ""),
                        "done": info.get("done", 0),
                        "total": info.get("total", 0),
                    }
                    prefix = "本章失败" if info.get("failed") else "已完成"
                    row.message = (
                        f"{prefix} {info.get('done', 0)}/{info.get('total', 0)} 章"
                    )
                    jobs.publish_event(
                        job_id,
                        "chapter",
                        {
                            "heading": info.get("heading", ""),
                            "progress": int(info.get("progress") or 0),
                            "done": int(info.get("done") or 0),
                            "total": int(info.get("total") or 0),
                            "words": int(info.get("words") or 0),
                            "source": info.get("source", ""),
                            "failed": bool(info.get("failed")),
                        },
                        db=progress_db,
                    )

            def on_delta(chunk: str) -> None:
                if not chunk:
                    return
                # Stop an in-flight model stream promptly once the lease is
                # lost or shutdown begins (the callback exception propagates
                # out of the stream generator and aborts the run).
                raise_if_aborted()
                delta["text"] += chunk
                if len(delta["text"]) >= 320 or time.monotonic() - delta["last"] >= 0.25:
                    flush_delta()

            def before_chapter(info: dict[str, Any], chapter_db) -> bool:
                # Abort first (lease/shutdown), then consult the idempotency
                # ledger: a unit finished by an earlier attempt is skipped.
                raise_if_aborted()
                status = step_status(chapter_db, job_id, f"chapter:{info['node_id']}")
                if status is None:  # lost the claim between guard and lookup
                    raise JobAborted("章节任务认领已失效")
                return not status

            def after_chapter(info: dict[str, Any], chapter_db) -> None:
                mark_step_done(
                    chapter_db,
                    job_id,
                    f"chapter:{info['node_id']}",
                    current_claim_token(),
                )

            report = generate_document(
                work_db,
                project_id,
                _generation_client(work_db),
                ledger,
                package,
                stream=True,
                on_delta=on_delta,
                on_progress=on_progress,
                on_chapter_start=on_chapter_start,
                force=force,
                knowledge_provider=_knowledge_provider(work_db, owner_id),
                before_chapter=before_chapter,
                after_chapter=after_chapter,
            )
            flush_delta()
            project.status = "drafting"
            work_db.flush()

        with session_scope() as done_db:
            row = fenced_job(done_db, job_id)
            if row is not None:
                row.status = "done"
                row.progress = 100
                row.message = f"正文完成：{len(report.written)} 章"
                result = _report_payload(report)
                row.result = result
                # Use the idempotency ledger (done_steps), not this attempt's
                # report — on resume, earlier chapters were skipped but remain
                # earned, so their credits must not be refunded again.
                _job_refund(done_db, row)
                jobs.publish_event(
                    job_id,
                    "done",
                    {**jobs.job_payload(row), "report": result},
                    db=done_db,
                )
                done_db.flush()
    except JobAborted:
        # Lease lost / graceful shutdown.  Do NOT mark failed or refund here:
        # the job stays 'running' and the reaper returns it to 'queued'; the
        # resumed attempt skips completed chapters via the idempotency ledger.
        logger.info("生成任务 %s 已中止，等待重新排队", job_id)
    except Exception:  # noqa: BLE001 - surfaced to the UI as a stable message
        logger.exception("生成任务 %s 失败", job_id)
        failed_message = "生成失败：模型服务不可用或响应异常，详情见服务日志"
        with session_scope() as db:
            row = fenced_job(db, job_id)
            if row is not None:
                # Refund only for chapters never persisted (done_steps), and
                # never twice — the helper guards with payload['refunded'].
                _job_refund(db, row)
                _fail_job(db, row, failed_message)
