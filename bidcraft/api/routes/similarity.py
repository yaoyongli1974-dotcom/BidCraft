"""API routes: /similarity (对标易标第二批 v0.25.0)。

模块级只从 ``..shared`` / ``..schemas`` 取名字；业务逻辑在
:mod:`bidcraft.similarity.service`。``/projects/{ref}/...`` 路径与
``/similarity`` 前缀均已由 app 的鉴权中间件覆盖。
"""

import sys

from fastapi import APIRouter, HTTPException, Response

_core = sys.modules['bidcraft.api.app']  # noqa: F841 运行期查找，保持 monkeypatch 生效

from ...models import SimilarityReport
from ...similarity import service as similarity_service
from ..schemas import SimilarityHitReview, SimilarityRequest
from ..shared import (  # noqa: F401
    Job,
    _project,
    _require_write,
    audit,
    current_user,
    jobqueue,
    jobs,
    session_scope,
    settings,
)

router = APIRouter()


def _bad_request(exc: Exception):
    from fastapi import HTTPException

    return HTTPException(status_code=400, detail=str(exc))


@router.post("/projects/{ref}/similarity/check", status_code=202)
def similarity_check(ref: str, payload: SimilarityRequest) -> dict:
    """发起查重：先建 queued 报告，再交给 jobqueue 异步执行。

    O(n²) 的段落两两比对不能占住 HTTP 线程。项目使用 ``bidcraft serve`` 时
    后台 worker 已启动，请求只入队并返回 ``job_id``；直接以 ASGI 方式挂载且
    没有 worker 池时退回内联执行（同时兼容既有同步调用方与测试客户端）。
    """

    user_id = str((current_user() or {}).get("id") or "")
    # 显式来源项目即跨项目比对（与 service 层既有语义一致）。
    report_scope = (
        "history"
        if payload.source_project_id and payload.scope != "history"
        else payload.scope
    )
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        source_project_id = payload.source_project_id or None
        if source_project_id:
            source, _source_role = _project(db, source_project_id)
            source_project_id = source.id
        try:
            similarity_service.validate_check(payload.mode, payload.scope, payload.threshold)
        except ValueError as exc:
            raise _bad_request(exc) from exc

        report = SimilarityReport(
            project_id=project.id,
            scope=report_scope,
            mode=payload.mode,
            threshold=float(payload.threshold),
            status="queued",
            stats={},
            skipped=[],
            created_by=user_id,
        )
        db.add(report)
        db.flush()
        report_id = report.id

        # 分离部署（embedded_workers=0）必须入队给独立 worker；单进程且已有
        # worker 池时同样入队。没有可用 worker 时才内联，避免任务永久排队。
        queued = not settings.embedded_workers or jobqueue.workers_running()
        job_id = ""
        if queued:
            error = jobqueue.check_backpressure(db, user_id)
            if error:
                raise HTTPException(status_code=429, detail=error)
            job = Job(
                project_id=project.id,
                kind="similarity",
                status="queued",
                message="多维查重排队中…",
                owner_id=user_id,
                payload={
                    "owner_id": user_id,
                    "report_id": report_id,
                    "project_id": project.id,
                    "scope": report.scope,
                    "mode": payload.mode,
                    "threshold": float(payload.threshold),
                    "source_project_id": source_project_id or "",
                    "created_by": user_id,
                },
            )
            db.add(job)
            db.flush()
            job_id = job.id
            jobs.publish_event(job_id, "queued", jobs.job_payload(job), db=db)
            audit(db, project.id, "similarity.check.queued", {"report_id": report_id, "job_id": job_id})

    if job_id:
        jobqueue.submit(job_id=job_id)
        return {
            "report_id": report_id,
            "job_id": job_id,
            "status": "queued",
            "mode": payload.mode,
            "hits": 0,
        }

    with session_scope() as db:
        try:
            report = similarity_service.run_similarity(
                db,
                project.id,
                scope=report_scope,
                mode=payload.mode,
                threshold=payload.threshold,
                source_project_id=source_project_id,
                created_by=user_id,
                report_id=report_id,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return {
            "report_id": report.id,
            "job_id": "",
            "status": report.status,
            "mode": report.mode,
            "hits": int((report.stats or {}).get("hits") or 0),
        }


@router.get("/projects/{ref}/similarity/reports")
def similarity_reports(ref: str) -> list[dict]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        return [
            similarity_service.report_payload(report)
            for report in similarity_service.list_reports(db, project.id)
        ]


@router.get("/projects/{ref}/similarity/reports/{report_id}")
def similarity_report(ref: str, report_id: str) -> dict:
    with session_scope() as db:
        project, _role = _project(db, ref)
        report = similarity_service.get_report(db, project.id, report_id)
        return similarity_service.report_payload(report)


@router.get("/projects/{ref}/similarity/reports/{report_id}/hits")
def similarity_hits(
    ref: str,
    report_id: str,
    kind: str | None = None,
    review_status: str | None = None,
) -> list[dict]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        hits = similarity_service.get_hits(
            db,
            project.id,
            report_id,
            kind=kind,
            review_status=review_status,
        )
        return [similarity_service.hit_payload(hit) for hit in hits]


@router.patch("/projects/{ref}/similarity/hits/{hit_id}")
def similarity_review(ref: str, hit_id: str, payload: SimilarityHitReview) -> dict:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        try:
            hit = similarity_service.review_hit(
                db,
                project.id,
                hit_id,
                status=payload.status,
                note=payload.note,
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return similarity_service.hit_payload(hit)


@router.get("/projects/{ref}/similarity/reports/{report_id}/export")
def similarity_export(ref: str, report_id: str) -> Response:
    with session_scope() as db:
        project, _role = _project(db, ref)
        report = similarity_service.get_report(db, project.id, report_id)
        hits = similarity_service.get_hits(db, project.id, report_id)
        markdown = similarity_service.render_report_markdown(report, hits)
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="similarity-{report_id}.md"'
            )
        },
    )
