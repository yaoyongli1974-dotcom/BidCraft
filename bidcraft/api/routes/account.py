"""API routes: /account (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ..shared import (  # noqa: F401
    AccountDelete,
    Any,
    BillingProfilePayload,
    FileResponse,
    HTTPException,
    Path,
    SubAccountCreate,
    SubAccountUpdate,
    accounts,
    audit,
    billing_user,
    compliance,
    session_scope,
    settings,
)

router = APIRouter()


@router.get("/account/sub-accounts")
def list_sub_accounts_route() -> list[dict[str, Any]]:
    with session_scope() as db:
        user = billing_user(db)
        return accounts.list_sub_accounts(db, user)


@router.post("/account/sub-accounts", status_code=201)
def create_sub_account_route(payload: SubAccountCreate) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            sub = accounts.create_sub_account(
                db,
                user,
                username=payload.username,
                password=payload.password,
                display_name=payload.display_name,
            )
        except accounts.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(db, None, "account.sub_create", {"sub_id": sub.id, "username": sub.username})
        return accounts.sub_payload(sub)


@router.patch("/account/sub-accounts/{sub_id}")
def update_sub_account_route(sub_id: str, payload: SubAccountUpdate) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        changes = payload.model_dump(exclude_unset=True)
        try:
            sub = accounts.update_sub_account(db, user, sub_id, changes)
        except accounts.AccountError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit(db, None, "account.sub_update", {"sub_id": sub.id, "keys": list(changes)})
        return accounts.sub_payload(sub)


@router.delete("/account/sub-accounts/{sub_id}")
def delete_sub_account_route(sub_id: str) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            accounts.delete_sub_account(db, user, sub_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit(db, None, "account.sub_delete", {"sub_id": sub_id})
        return {"deleted": True, "id": sub_id}


@router.get("/account/deletion")
def account_deletion() -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        return compliance.deletion_payload(user)


@router.post("/account/delete")
def account_delete(payload: AccountDelete) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        if payload.confirm.strip() != compliance.DELETE_CONFIRM:
            raise HTTPException(status_code=400, detail="请输入「注销」以确认")
        try:
            state = compliance.request_deletion(db, user, payload.password)
        except compliance.ComplianceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(
            db,
            None,
            "account.delete_request",
            {"user_id": user.id, "username": user.username, "due_at": state["due_at"]},
        )
        db.flush()
        return {"deleted": False, **state}


@router.post("/account/delete/cancel")
def account_delete_cancel() -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            state = compliance.cancel_deletion(db, user)
        except compliance.ComplianceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(db, None, "account.delete_cancel", {"user_id": user.id})
        db.flush()
        return {"deleted": False, **state}


@router.get("/account/export")
def account_export() -> FileResponse:
    with session_scope() as db:
        user = billing_user(db)
        try:
            path = compliance.export_account(db, user, settings.home)
        except Exception as exc:  # noqa: BLE001 - 导出失败需可读
            raise HTTPException(status_code=500, detail=f"导出失败：{exc}") from exc
        audit(db, None, "account.export", {"user_id": user.id})
        db.flush()
        return FileResponse(
            path,
            media_type="application/zip",
            filename=Path(path).name,
        )


@router.get("/account/billing-profile")
def billing_profile_get() -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        return compliance.billing_profile(db, user.id)


@router.put("/account/billing-profile")
def billing_profile_put(payload: BillingProfilePayload) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        data = compliance.save_billing_profile(db, user.id, payload.model_dump())
        audit(db, None, "account.billing_profile", {})
        return data


