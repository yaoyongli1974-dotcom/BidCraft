"""API routes: /my (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ..app import (  # noqa: F401
    Any,
    File,
    FileResponse,
    HTTPException,
    Job,
    Path,
    PersonnelCreate,
    PersonnelUpdate,
    QualificationCreate,
    QualificationUpdate,
    UploadFile,
    _active_kb_reindex,
    audit,
    billing_user,
    date,
    jobs,
    mimetypes,
    personnel,
    qualifications,
    session_scope,
    threading,
    user_docs,
)

router = APIRouter()


@router.get("/my/docs")
def my_docs() -> list[dict[str, Any]]:
    with session_scope() as db:
        user = billing_user(db)
        return [user_docs.doc_payload(row) for row in user_docs.list_docs(db, user.id)]


@router.post("/my/docs", status_code=202)
async def upload_my_doc(file: UploadFile = File(...)) -> dict[str, Any]:
    raw_name = (file.filename or "").strip()
    content = await file.read(user_docs.MAX_USER_DOC_BYTES + 1)
    worker: threading.Thread | None = None
    with session_scope() as db:
        user = billing_user(db)
        try:
            doc = user_docs.save_upload(db, user, raw_name, content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        kb_id = doc.kb_id
        job = _active_kb_reindex(db, kb_id)
        if job is None:
            job = Job(
                project_id=None,
                kind="kb_reindex",
                status="queued",
                message="知识库重建索引排队中…",
                payload={
                    "owner_id": user.id,
                    "kb_id": kb_id,
                    "name": "个人知识库",
                },
            )
            db.add(job)
            db.flush()
            jobs.publish_event(job.id, "queued", jobs.job_payload(job), db=db)
            worker = threading.Thread(
                target=_core._run_kb_reindex_job,
                args=(job.id, kb_id),
                daemon=True,
                name=f"bidcraft-kb-reindex-{job.id[:8]}",
            )
        job_id = job.id
        payload = user_docs.doc_payload(doc)
        audit(
            db,
            None,
            "user_doc.upload",
            {"id": doc.id, "filename": doc.filename, "kb_id": kb_id, "job_id": job_id},
        )
    if worker is not None:
        worker.start()
    return {"doc": payload, "job_id": job_id}


@router.delete("/my/docs/{doc_id}")
def delete_my_doc(doc_id: str) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        row = user_docs.delete_doc(db, user, doc_id)
        audit(db, None, "user_doc.delete", {"id": row.id, "filename": row.filename})
        return {"deleted": True, "id": row.id}


@router.get("/my/docs/{doc_id}/file")
def my_doc_file(doc_id: str) -> FileResponse:
    with session_scope() as db:
        user = billing_user(db)
        row = user_docs.get_doc(db, user.id, doc_id)
        root = user_docs.user_dir(user.id).resolve()
        path = Path(row.stored_path).resolve()
        if root not in path.parents or not path.is_file():
            raise LookupError(f"文件不存在：{row.filename}")
        return FileResponse(path, media_type=row.mime or None, filename=row.filename)


@router.get("/my/qualifications")
def my_qualifications(kind: str = "", status: str = "") -> list[dict[str, Any]]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            rows = qualifications.list_qualifications(
                db, user, kind=kind, status=status
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return [qualifications.qualification_payload(row) for row in rows]


@router.get("/my/preflight")
def my_preflight(deadline: str = "") -> dict[str, Any]:
    raw = (deadline or "").strip()
    due: date | None = None
    if raw:
        try:
            due = date.fromisoformat(raw[:10])
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="日期格式无效，应为 YYYY-MM-DD"
            ) from exc
    with session_scope() as db:
        user = billing_user(db)
        return qualifications.preflight(db, user, deadline=due)


@router.post("/my/qualifications", status_code=201)
def create_my_qualification(payload: QualificationCreate) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            row = qualifications.create_qualification(
                db, user, payload.model_dump()
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            None,
            "qualification.create",
            {"id": row.id, "kind": row.kind, "name": row.name},
        )
        return qualifications.qualification_payload(row)


@router.patch("/my/qualifications/{qualification_id}")
def update_my_qualification(
    qualification_id: str, payload: QualificationUpdate
) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            row = qualifications.update_qualification(
                db,
                user,
                qualification_id,
                payload.model_dump(exclude_unset=True),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            None,
            "qualification.update",
            {"id": row.id, "keys": sorted(payload.model_fields_set)},
        )
        return qualifications.qualification_payload(row)


@router.delete("/my/qualifications/{qualification_id}")
def delete_my_qualification(qualification_id: str) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        row = qualifications.delete_qualification(db, user, qualification_id)
        audit(
            db,
            None,
            "qualification.delete",
            {"id": row.id, "name": row.name},
        )
        return {"deleted": True, "id": row.id}


@router.post("/my/qualifications/{qualification_id}/evidence", status_code=201)
async def upload_my_qualification_evidence(
    qualification_id: str, file: UploadFile = File(...)
) -> dict[str, Any]:
    raw_name = (file.filename or "").strip()
    content = await file.read(qualifications.MAX_EVIDENCE_BYTES + 1)
    with session_scope() as db:
        user = billing_user(db)
        row = qualifications.get_qualification(db, user, qualification_id)
        try:
            entry = qualifications.add_evidence(db, row, raw_name, content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            None,
            "qualification.evidence",
            {"id": row.id, "filename": entry["filename"]},
        )
        return qualifications.qualification_payload(row)


@router.get("/my/qualifications/{qualification_id}/evidence/{filename}")
def my_qualification_evidence(
    qualification_id: str, filename: str
) -> FileResponse:
    with session_scope() as db:
        user = billing_user(db)
        row = qualifications.get_qualification(db, user, qualification_id)
        path = qualifications.evidence_path(db, row, filename)
        media = mimetypes.guess_type(path.name)[0]
        return FileResponse(path, media_type=media, filename=path.name)


@router.delete("/my/qualifications/{qualification_id}/evidence/{filename}")
def delete_my_qualification_evidence(
    qualification_id: str, filename: str
) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        row = qualifications.get_qualification(db, user, qualification_id)
        name = qualifications.delete_evidence(db, row, filename)
        audit(
            db,
            None,
            "qualification.evidence",
            {"id": row.id, "filename": name, "action": "delete"},
        )
        return {"deleted": True, "filename": name}


@router.get("/my/personnel")
def my_personnel() -> list[dict[str, Any]]:
    with session_scope() as db:
        user = billing_user(db)
        rows = personnel.list_personnel(db, user)
        return [personnel.person_payload(row) for row in rows]


@router.post("/my/personnel", status_code=201)
def create_my_personnel(payload: PersonnelCreate) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            row = personnel.create_personnel(db, user, payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            None,
            "personnel.create",
            {"id": row.id, "name": row.name},
        )
        return personnel.person_payload(row)


@router.patch("/my/personnel/{person_id}")
def update_my_personnel(
    person_id: str, payload: PersonnelUpdate
) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            row = personnel.update_personnel(
                db, user, person_id, payload.model_dump(exclude_unset=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            None,
            "personnel.update",
            {"id": row.id, "keys": sorted(payload.model_fields_set)},
        )
        return personnel.person_payload(row)


@router.delete("/my/personnel/{person_id}")
def delete_my_personnel(person_id: str) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        row = personnel.delete_personnel(db, user, person_id)
        audit(
            db,
            None,
            "personnel.delete",
            {"id": row.id, "name": row.name},
        )
        return {"deleted": True, "id": row.id}


