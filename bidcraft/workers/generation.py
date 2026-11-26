"""正文生成后台任务。

业务生成逻辑在 :mod:`bidcraft.generation.writer`；这里只负责 job 行/SSE 编排。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select

from .. import jobs, provenance
from ..db import session_scope
from ..generation.chapter_plan import (
    CONFIRMED_STATUSES,
    chapter_plan_payload,
    invalidate_stale_plan,
    load_active_plan,
    mark_plan_used,
    scoped_knowledge_provider,
)
from ..generation.outline import build_outline, load_outline, save_outline
from ..generation.writer import generate_document
from ..jobqueue import (
    JobAborted,
    _chapter_done_steps,
    assert_generation_lease,
    commit_fenced_result,
    current_claim_token,
    fenced_chapter_write,
    fenced_job,
    job_abort_event,
    log_job_failure,
    mark_step_done,
    pending_charge_heartbeat,
    raise_if_aborted,
    release_generation_lease,
    settle_pending_charge,
    step_status,
)
from ..ledger import load_ledger
from ..models import ChapterPlan, OutlineNode, Project, Section
from ..rules.engine import ledger_gate
from ..rules.loader import load_package
from ..services.job_support import (
    _fail_job,
    _generation_client,
    _job_refund,
    _knowledge_provider,
    _report_payload,
)

logger = logging.getLogger("bidcraft.workers")


def _plan_meta(plan) -> dict[str, Any]:
    """章节规划溯源字段（与同步审计口径一致）。"""

    payload = chapter_plan_payload(plan)

    def ids(field: str) -> list[str]:
        return [
            str(row.get("id") or "")
            for row in payload.get(field) or []
            if isinstance(row, dict) and row.get("id")
        ]

    return {
        "planning_id": payload["id"],
        "planning_version": payload["version"],
        "planning_status": payload["status"],
        "knowledge_reference_ids": ids("knowledge_refs"),
        "fact_reference_ids": ids("fact_refs"),
        "scoring_reference_ids": ids("score_points"),
        "generation_strategy": payload.get("strategy") or [],
        "prompt_version": payload.get("prompt_version") or "",
    }


def _resolve_worker_plan(
    db,
    project_id: str,
    package_id: str | None,
    node_id: str,
    recorded: dict[str, Any] | None,
    mode: str,
) -> ChapterPlan | None:
    """使用 enqueue 时门禁记录的规划；非 auto 模式下绝不消费未确认版本。"""

    plan: ChapterPlan | None = None
    plan_id = str((recorded or {}).get("planning_id") or "")
    if plan_id:
        candidate = db.get(ChapterPlan, plan_id)
        if (
            candidate is not None
            and candidate.project_id == project_id
            and candidate.node_id == node_id
            and not (package_id and candidate.package_id and candidate.package_id != package_id)
        ):
            plan = candidate
    if plan is None:
        # 老 Job 没有 plans 映射时退回当前有效规划，仍执行同样的状态门禁。
        plan = load_active_plan(db, project_id, node_id, package_id)
    if plan is not None:
        stale = invalidate_stale_plan(db, plan)
        if stale:
            raise ValueError(f"章节 {node_id} 的规划依赖已变化，worker 拒绝生成正文：{'；'.join(stale[:3])}")
    if plan is not None and plan.status not in CONFIRMED_STATUSES and mode != "auto":
        raise ValueError(f"章节 {node_id} 的规划尚未确认，worker 拒绝生成正文")
    return plan


def commit_generation_chapter(
    job_id: str,
    project_id: str,
    lease_token: str,
    info: dict[str, Any],
    body: str,
    source: str,
) -> None:
    """Atomically persist one chapter under BOTH Job and GenerationLease fences.

    Section, OutlineNode, the idempotency ledger and the success event are
    written in one short transaction opened by ``fenced_chapter_write``.  A
    lost Job claim or project lease aborts the whole unit before any write.
    """
    from .. import jobs

    node_id = str(info.get("node_id") or "")
    section_id = str(info.get("section_id") or "")
    words = int(info.get("words") or 0)
    status = "fallback" if source == "fallback" else "drafted"
    with fenced_chapter_write(
        job_id,
        node_id,
        project_id=project_id,
        lease_token=lease_token,
    ) as (chapter_db, _row):
        step_key = f"chapter:{node_id}"
        # Idempotency is checked INSIDE the fenced transaction: a duplicate
        # delivery must not overwrite an already committed chapter body or
        # emit a second success event.
        if step_status(chapter_db, job_id, step_key) is True:
            return
        section = (
            chapter_db.scalar(select(Section).where(Section.id == section_id))
            if section_id
            else None
        )
        if section is not None:
            section.body = body
            section.words = words
            section.status = status
        outline_node = chapter_db.get(OutlineNode, node_id)
        if outline_node is not None:
            outline_node.status = status
        # 事实引用与正文同事务重建：worker 生成的章节必须有真实 FactCitation，
        # 否则导出/provenance 侧看不到任何可复核的来源链。
        provenance.refresh_node(chapter_db, project_id, node_id, body)
        # 规划与正文同事务标记已使用；幂等，重复投递不会二次改写状态。
        plan_id = str(info.get("chapter_plan_id") or "")
        if plan_id:
            plan = chapter_db.get(ChapterPlan, plan_id)
            if plan is not None:
                mark_plan_used(chapter_db, plan)
        mark_step_done(
            chapter_db,
            job_id,
            f"chapter:{node_id}",
            current_claim_token(),
        )
        jobs.publish_event(
            job_id,
            "chapter_committed",
            {"node_id": node_id, "words": words, "status": status},
            db=chapter_db,
        )


def _release_lease(project_id: str, token: str) -> None:
    if not token:
        return
    try:
        with session_scope() as db:
            release_generation_lease(db, project_id, token)
    except Exception:  # noqa: BLE001 - 租约终态释放失败由 reaper 过期清理兜底
        logger.exception("生成租约释放失败: %s", project_id)


def _run_generation_job(job_id: str, project_id: str, force: bool) -> None:
    """Background worker: writes chapters and streams progress into the Job row.

    Progress is persisted twice: the job row remains the fallback/polling view,
    while ``job_events`` is the resumable SSE feed used by the browser.
    """
    marker_id = ""
    recorded_plans: dict[str, Any] = {}
    planning_mode = "review"
    try:
        raise_if_aborted()
        with session_scope() as db:
            job = fenced_job(db, job_id)
            if job is None:
                return
            if job.status != "running":
                return
            job.status = "running"
            job.message = "正在准备事实台账…"
            db.flush()
            jobs.publish_event(job_id, "status", jobs.job_payload(job), db=db)
            project = db.get(Project, project_id)
            if project is None:
                raise ValueError("项目不存在")
            recorded_plans = dict((job.payload or {}).get("plans") or {})
            planning_mode = str((project.meta or {}).get("chapter_planning_mode") or "review")
            owner_id = str((job.payload or {}).get("user_id") or project.owner_id or "")
            marker_id = str((job.payload or {}).get("pending_charge_id") or "")
            lease_token = str((job.payload or {}).get("generation_lease_token") or "")
            ledger = load_ledger(db, project_id)
            problems = ledger_gate(ledger)
            if problems:
                raise ValueError("必填事实缺失：" + "、".join(problems))
            package = load_package(project.package)
            if not load_outline(db, project_id):
                save_outline(db, project_id, build_outline(ledger, package))
                db.flush()

        # Keep the async charge marker alive for the whole generation attempt;
        # a crash leaves it pending so the reconciler can refund by done_steps.
        with pending_charge_heartbeat(
            marker_id,
            lease_project_id=project_id,
            lease_token=lease_token,
            abort=job_abort_event(),
        ), session_scope() as work_db:
            project = work_db.get(Project, project_id)
            ledger = load_ledger(work_db, project_id)
            package = load_package(project.package)
            package_id = str((project.meta or {}).get("package_id") or "") or None
            plans_by_node: dict[str, ChapterPlan] = {}
            for node_row in load_outline(work_db, project_id):
                plan = _resolve_worker_plan(
                    work_db,
                    project_id,
                    package_id,
                    node_row["id"],
                    recorded_plans.get(node_row["id"]),
                    planning_mode,
                )
                if plan is not None:
                    plans_by_node[node_row["id"]] = plan
            # 审计元数据必须以 writer 实际使用的规划为准，不能按 node 二次查库。
            plans_by_id = {plan.id: plan for plan in plans_by_node.values()}

            def _plan_for(info: dict[str, Any]) -> ChapterPlan | None:
                return plans_by_id.get(str(info.get("chapter_plan_id") or ""))

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
                    plan = _plan_for(info)
                    row.payload = {
                        **(row.payload or {}),
                        "heading": current["heading"],
                        "done": max(0, int(info.get("index") or 1) - 1),
                        "total": int(info.get("total") or 0),
                        **(_plan_meta(plan) if plan is not None else {}),
                    }
                    jobs.publish_event(
                        job_id,
                        "chapter_start",
                        {
                            "heading": current["heading"],
                            "index": int(info.get("index") or 0),
                            "total": int(info.get("total") or 0),
                            "target_words": int(info.get("target_words") or 0),
                            **(_plan_meta(plan) if plan is not None else {}),
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
                    plan = _plan_for(info)
                    row.payload = {
                        **(row.payload or {}),
                        "heading": info.get("heading", ""),
                        "done": info.get("done", 0),
                        "total": info.get("total", 0),
                        **(_plan_meta(plan) if plan is not None else {}),
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
                assert_generation_lease(chapter_db, project_id, lease_token)
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

            def commit_chapter(info: dict[str, Any], body: str, source: str) -> None:
                """Production chapter write path; tested directly by stage 3 suite."""
                # SQLite has a single writer: the long generation session must
                # release its transaction before the fenced short transaction
                # writes the chapter, plan usage and provenance rows.
                work_db.commit()
                commit_generation_chapter(job_id, project_id, lease_token, info, body, source)

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
                knowledge_provider=scoped_knowledge_provider(
                    _knowledge_provider, work_db, owner_id, project_id, package_id
                ),
                before_chapter=before_chapter,
                after_chapter=after_chapter,
                commit_chapter=commit_chapter,
                plan_provider=lambda node_id: plans_by_node.get(str(node_id)),
            )
            flush_delta()
            project.status = "drafting"
            work_db.flush()

        with commit_fenced_result(job_id) as (done_db, row):
            row.status = "done"
            row.progress = 100
            row.message = f"正文完成：{len(report.written)} 章"
            result = _report_payload(report)
            row.result = result
            # Use the idempotency ledger (done_steps), not this attempt's
            # report — on resume, earlier chapters were skipped but remain
            # earned, so their credits must not be refunded again.
            written = _chapter_done_steps(dict(row.payload or {}))
            if written is None:
                written = len(report.written or [])
            if marker_id:
                settle_pending_charge(
                    done_db,
                    marker_id,
                    None,
                    str(row.project_id or ""),
                    int((row.payload or {}).get("chapters") or 0),
                    int(written),
                )
            else:
                # Legacy jobs created before async markers still need the
                # job-level idempotent refund path.
                _job_refund(done_db, row)
            jobs.publish_event(
                job_id,
                "done",
                {
                **jobs.job_payload(row),
                "report": result,
                "unused_plan_refs": getattr(report, "unused_plan_refs", {}),
            },
                db=done_db,
            )
        _release_lease(project_id, lease_token)
    except JobAborted:
        # Lease lost / graceful shutdown.  Do NOT mark failed or refund here:
        # the job stays 'running' and the reaper returns it to 'queued'; the
        # resumed attempt skips completed chapters via the idempotency ledger.
        logger.info("生成任务 %s 已中止，等待重新排队", job_id)
    except Exception:  # noqa: BLE001 - surfaced to the UI as a stable message
        log_job_failure(logger)
        failed_message = "生成失败：模型服务不可用或响应异常，详情见服务日志"
        with session_scope() as db:
            row = fenced_job(db, job_id)
            if row is not None:
                # Refund only for chapters never persisted (done_steps), and
                # never twice — both helpers use conditional DB claims.  A
                # failed marker settlement is retried by the reconciler.
                if marker_id:
                    written = _chapter_done_steps(dict(row.payload or {}))
                    settle_pending_charge(
                        db,
                        marker_id,
                        None,
                        str(row.project_id or ""),
                        int((row.payload or {}).get("chapters") or 0),
                        int(written or 0),
                    )
                else:
                    _job_refund(db, row)
                _fail_job(db, row, failed_message)
        _release_lease(project_id, lease_token)
