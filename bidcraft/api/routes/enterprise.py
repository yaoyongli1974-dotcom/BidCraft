"""API routes: /enterprise（对标易标第二批 v0.25.0）。

企业容器、成员、企业文档与企业资产匹配。权限全部在后端 Service 层判定：
owner 才能管理成员 / 入库文档 / 删除文档；member 只读。全局 viewer 的写操作
由认证中间件统一拦截（403）。
"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # noqa: F841 运行期查找，保持 monkeypatch 生效

from ...enterprise import assets, matching  # noqa: E402
from ...knowledge import (  # noqa: E402
    KnowledgeError,
    delete_document,
    document_payload,
    ingest_document,
    list_documents,
)
from ...models import User  # noqa: E402
from ..schemas import (  # noqa: E402
    EnterpriseCreate,
    EnterpriseMatchRequest,
    EnterpriseMemberAdd,
    KbDocumentIngest,
)
from ..shared import (  # noqa: E402
    Any,
    HTTPException,
    _project,
    audit,
    billing_user,
    select,
    session_scope,
)

router = APIRouter()


def _forbidden(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "无权执行此操作")


@router.post("/enterprise", status_code=201)
def enterprise_create(payload: EnterpriseCreate) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            row = assets.create_enterprise(db, user, payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return assets.enterprise_payload(db, row, user)


@router.get("/enterprise")
def enterprise_list() -> list[dict[str, Any]]:
    with session_scope() as db:
        user = billing_user(db)
        return [
            assets.enterprise_payload(db, row, user)
            for row in assets.list_enterprises(db, user)
        ]


@router.post("/enterprise/{enterprise_id}/members", status_code=201)
def enterprise_member_add(
    enterprise_id: str, payload: EnterpriseMemberAdd
) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            row = assets.add_member(
                db, user, enterprise_id, payload.username, payload.role
            )
        except assets.EnterprisePermissionError as exc:
            raise _forbidden(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target = db.get(User, row.user_id)
        return assets.member_payload(row, target)


@router.get("/enterprise/{enterprise_id}/members")
def enterprise_member_list(enterprise_id: str) -> list[dict[str, Any]]:
    with session_scope() as db:
        user = billing_user(db)
        rows = assets.list_members(db, user, enterprise_id)
        user_ids = {row.user_id for row in rows}
        users = {
            item.id: item
            for item in db.scalars(select(User).where(User.id.in_(user_ids)))
        }
        return [assets.member_payload(row, users.get(row.user_id)) for row in rows]


@router.delete("/enterprise/{enterprise_id}/members/{user_id}")
def enterprise_member_remove(
    enterprise_id: str, user_id: str
) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            assets.remove_member(db, user, enterprise_id, user_id)
        except assets.EnterprisePermissionError as exc:
            raise _forbidden(exc) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": True, "user_id": user_id}


@router.get("/enterprise/{enterprise_id}/documents")
def enterprise_document_list(enterprise_id: str) -> list[dict[str, Any]]:
    with session_scope() as db:
        user = billing_user(db)
        enterprise = assets.get_enterprise(db, user, enterprise_id)
        kb = assets.enterprise_kb(db, enterprise, create=False)
        if kb is None:
            return []
        return [document_payload(row) for row in list_documents(db, kb)]


@router.post("/enterprise/{enterprise_id}/documents", status_code=201)
def enterprise_document_ingest(
    enterprise_id: str, payload: KbDocumentIngest
) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            enterprise = assets.get_owned_enterprise(db, user, enterprise_id)
        except assets.EnterprisePermissionError as exc:
            raise _forbidden(exc) from exc
        kb = assets.enterprise_kb(db, enterprise, create=True)
        try:
            row = ingest_document(
                db,
                kb,
                payload.source,
                payload.content,
                kind=payload.kind,
                tags=payload.tags,
                valid_to=payload.valid_to,
            )
        except (KnowledgeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            None,
            "enterprise.document_ingest",
            {
                "enterprise_id": enterprise.id,
                "document_id": row.id,
                "source": row.source,
                "sha256": row.sha256,
            },
        )
        return document_payload(row)


@router.delete("/enterprise/{enterprise_id}/documents/{document_id}")
def enterprise_document_delete(
    enterprise_id: str, document_id: str
) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            enterprise = assets.get_owned_enterprise(db, user, enterprise_id)
        except assets.EnterprisePermissionError as exc:
            raise _forbidden(exc) from exc
        kb = assets.enterprise_kb(db, enterprise, create=False)
        if kb is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        row = delete_document(db, kb, document_id)
        audit(
            db,
            None,
            "enterprise.document_delete",
            {"enterprise_id": enterprise.id, "document_id": row.id, "source": row.source},
        )
        return {"deleted": True, "id": row.id}


@router.post("/projects/{ref}/enterprise/match")
def project_enterprise_match(
    ref: str, payload: EnterpriseMatchRequest
) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        user = billing_user(db)
        try:
            result = matching.match_any(
                db,
                user,
                payload.requirement,
                kind=payload.kind,
                enterprise_ids=assets.accessible_enterprise_ids(db, user),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"project_id": project.id, **result}


@router.get("/projects/{ref}/enterprise/package")
def project_enterprise_package(ref: str) -> dict[str, Any]:
    with session_scope() as db:
        project, _role = _project(db, ref)
        user = billing_user(db)
        return matching.build_project_asset_package(db, user, project)
