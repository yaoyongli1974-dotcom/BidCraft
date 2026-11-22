"""API routes: /commercial（对标易标第二批 v0.25.0）——商务标自动编制。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # noqa: F841 运行期查找，保持 monkeypatch 生效

from ... import commercial  # noqa: E402
from ..schemas import (  # noqa: E402
    CommercialDocCreate,
    CommercialDocUpdate,
    CommercialReview,
)
from ..shared import (  # noqa: E402
    Any,
    FileResponse,
    HTTPException,
    _project,
    _require_owner,
    _require_write,
    audit,
    auth,
    current_user,
    re,
    session_scope,
    settings,
)

router = APIRouter()


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, commercial.CommercialForbiddenError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, commercial.CommercialConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/projects/{ref}/commercial")
def commercial_list(ref: str, package_id: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        return commercial.list_commercial(db, project, package_id)


@router.post("/projects/{ref}/commercial", status_code=201)
def commercial_create(ref: str, payload: CommercialDocCreate) -> dict[str, Any]:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        user = current_user() or {}
        try:
            return commercial.save_commercial(
                db,
                project,
                name=payload.name,
                quote_version_id=payload.quote_version_id,
                package_id=payload.package_id,
                actor=user,
            )
        except Exception as exc:  # noqa: BLE001 - map service errors to HTTP
            raise _http_error(exc) from exc


@router.get("/projects/{ref}/commercial/{doc_id}")
def commercial_detail(ref: str, doc_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        doc = commercial.get_commercial(db, project, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="商务标不存在")
        return commercial.doc_payload(doc)


@router.put("/projects/{ref}/commercial/{doc_id}")
def commercial_update(
    ref: str, doc_id: str, payload: CommercialDocUpdate
) -> dict[str, Any]:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        user = current_user() or {}
        try:
            return commercial.update_commercial(
                db,
                project,
                doc_id,
                name=payload.name,
                sections=payload.sections,
                actor=user,
            )
        except Exception as exc:  # noqa: BLE001
            raise _http_error(exc) from exc


@router.delete("/projects/{ref}/commercial/{doc_id}")
def commercial_delete(ref: str, doc_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        user = current_user() or {}
        try:
            commercial.delete_commercial(db, project, doc_id, actor=user)
        except Exception as exc:  # noqa: BLE001
            raise _http_error(exc) from exc
        return {"deleted": True, "id": doc_id}


@router.post("/projects/{ref}/commercial/{doc_id}/submit")
def commercial_submit(
    ref: str, doc_id: str, payload: CommercialReview | None = None
) -> dict[str, Any]:
    data = payload or CommercialReview()
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        user = current_user() or {}
        try:
            return commercial.submit_commercial(
                db,
                project,
                doc_id,
                note=data.note,
                actor=user,
                actor_id=str(user.get("id") or ""),
                role=role,
                is_super=user.get("role") == auth.ROLE_SUPER_ADMIN,
            )
        except Exception as exc:  # noqa: BLE001
            raise _http_error(exc) from exc


@router.post("/projects/{ref}/commercial/{doc_id}/review")
def commercial_review(
    ref: str, doc_id: str, payload: CommercialReview
) -> dict[str, Any]:
    with session_scope() as db:
        project, role = _project(db, ref)
        user = current_user() or {}
        try:
            return commercial.review_commercial(
                db,
                project,
                doc_id,
                decision=payload.action,
                note=payload.note,
                actor=user,
                actor_id=str(user.get("id") or ""),
                role=role,
                is_super=user.get("role") == auth.ROLE_SUPER_ADMIN,
            )
        except Exception as exc:  # noqa: BLE001
            raise _http_error(exc) from exc


@router.post("/projects/{ref}/commercial/{doc_id}/unapprove")
def commercial_unapprove(ref: str, doc_id: str) -> dict[str, Any]:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_owner(role)
        user = current_user() or {}
        try:
            return commercial.unapprove_commercial(db, project, doc_id, actor=user)
        except Exception as exc:  # noqa: BLE001
            raise _http_error(exc) from exc


@router.post("/projects/{ref}/commercial/{doc_id}/export")
def commercial_export(
    ref: str, doc_id: str, format: str = "xlsx"
) -> FileResponse:
    from ...export_gate import ExportGateError, enforce_export_gate

    fmt = (format or "xlsx").strip().lower()
    if fmt not in {"xlsx", "docx"}:
        raise HTTPException(status_code=400, detail="format 只能是 xlsx 或 docx")
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        doc = commercial.get_commercial(db, project, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="商务标不存在")
        payload = commercial.export_payload(db, project, doc)
        # 商务标是交付件而非内部台账，xlsx 同样走导出硬门禁的口径（export_gate
        # 只为 docx/pdf/md 建模；这里用 docx 门禁，阻断风险与未确认章节都要拦）。
        gate_fmt = "docx" if fmt == "xlsx" else fmt
        try:
            enforce_export_gate(db, project.id, gate_fmt)
        except ExportGateError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        output = settings.project_dir(project.id) / "output" / f"commercial-{doc.id}.{fmt}"
        if fmt == "xlsx":
            from ...export.commercial_xlsx import export_commercial_xlsx

            path = export_commercial_xlsx(output, payload)
        else:
            from ...export.commercial_docx import export_commercial_docx

            path = export_commercial_docx(output, payload)
        safe = re.sub(r'[\\/:*?"<>|]', "", project.name).strip() or "BidCraft"
        audit(
            db,
            project.id,
            "commercial.export",
            {"doc_id": doc.id, "format": fmt, "quote_version_id": doc.quote_version_id},
        )
        return FileResponse(
            path, filename=f"{safe[:60]}-{doc.name[:40] or '商务标'}.{fmt}"
        )
