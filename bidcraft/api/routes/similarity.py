"""API routes: /similarity (对标易标第二批 v0.25.0)。

模块级只从 ``..shared`` / ``..schemas`` 取名字；业务逻辑在
:mod:`bidcraft.similarity.service`。``/projects/{ref}/...`` 路径与
``/similarity`` 前缀均已由 app 的鉴权中间件覆盖。
"""

import sys

from fastapi import APIRouter, Response

_core = sys.modules['bidcraft.api.app']  # noqa: F841 运行期查找，保持 monkeypatch 生效

from ...similarity import service as similarity_service
from ..schemas import SimilarityHitReview, SimilarityRequest
from ..shared import (  # noqa: F401
    _project,
    _require_write,
    audit,
    current_user,
    session_scope,
)

router = APIRouter()


def _bad_request(exc: Exception):
    from fastapi import HTTPException

    return HTTPException(status_code=400, detail=str(exc))


@router.post("/projects/{ref}/similarity/check", status_code=202)
def similarity_check(ref: str, payload: SimilarityRequest) -> dict:
    """发起查重；返回 202 与报告 ID。

    当前为同步执行（项目级文档规模可控），service 层可独立单测；
    若后续接入 jobqueue，只需把 ``run_similarity`` 移到 runner。
    """

    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        source_project_id = payload.source_project_id or None
        if source_project_id:
            source, _source_role = _project(db, source_project_id)
            source_project_id = source.id
        try:
            report = similarity_service.run_similarity(
                db,
                project.id,
                scope=payload.scope,
                mode=payload.mode,
                threshold=payload.threshold,
                source_project_id=source_project_id,
                created_by=str((current_user() or {}).get("id") or ""),
            )
        except ValueError as exc:
            raise _bad_request(exc) from exc
        return {
            "report_id": report.id,
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
