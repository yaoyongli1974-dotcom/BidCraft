"""Sub-account management: a VIP member can add child accounts that share the
parent's credit balance. Sub-accounts never hold their own credits; every charge
and refund is routed to the parent via :func:`billing.billing_account`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth, billing, clock
from .models import Project, User
from .security import hash_password


class AccountError(RuntimeError):
    """Raised for invalid sub-account operations (mapped to HTTP 400/403/409)."""


def _sub_count(db: Session, parent_id: str) -> int:
    return int(
        db.scalar(select(func.count(User.id)).where(User.parent_id == parent_id)) or 0
    )


def list_sub_accounts(db: Session, parent: User) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(User).where(User.parent_id == parent.id).order_by(User.created_at)
    ).all()
    return [sub_payload(row) for row in rows]


def sub_payload(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "active": bool(user.active),
        "created_at": clock.iso_utc(user.created_at),
        "last_login_at": clock.iso_utc(user.last_login_at),
    }


def create_sub_account(
    db: Session,
    parent: User,
    *,
    username: str,
    password: str,
    display_name: str = "",
) -> User:
    if parent.parent_id:
        raise AccountError("子账号不能再创建子账号")
    if not billing.is_vip(parent):
        raise AccountError("仅 VIP 会员可创建子账号，请先开通 VIP")
    limit = int(billing.billing_settings(db).get("sub_account_limit", 5))
    if _sub_count(db, parent.id) >= limit:
        raise AccountError(f"子账号数量已达上限（{limit} 个）")
    username = (username or "").strip()
    if len(username) < 3 or len(username) > 40:
        raise AccountError("子账号用户名需为 3–40 个字符")
    if len(password or "") < 8:
        raise AccountError("子账号密码至少 8 位")
    if db.scalar(select(User).where(User.username == username)):
        raise AccountError("用户名已存在")
    row = User(
        username=username,
        display_name=(display_name or "").strip()[:60] or username,
        password_hash=hash_password(password),
        role="manager",
        active=True,
        credits=0,
        is_external=True,
        parent_id=parent.id,
    )
    db.add(row)
    db.flush()
    return row


def update_sub_account(
    db: Session, parent: User, sub_id: str, changes: dict[str, Any]
) -> User:
    row = _own_sub(db, parent, sub_id)
    if "display_name" in changes:
        row.display_name = (changes["display_name"] or "").strip()[:60] or row.username
    if "active" in changes:
        row.active = bool(changes["active"])
        if not row.active:
            auth.revoke_user_sessions(db, row.id)
    if changes.get("password"):
        if len(changes["password"]) < 8:
            raise AccountError("子账号密码至少 8 位")
        row.password_hash = hash_password(changes["password"])
        auth.revoke_user_sessions(db, row.id)
    db.flush()
    return row


def delete_sub_account(db: Session, parent: User, sub_id: str) -> None:
    row = _own_sub(db, parent, sub_id)
    # Hand any projects the sub-account owns back to the parent so nothing is lost.
    for project in db.scalars(select(Project).where(Project.owner_id == row.id)).all():
        project.owner_id = parent.id
    auth.revoke_user_sessions(db, row.id)
    db.delete(row)
    db.flush()


def _own_sub(db: Session, parent: User, sub_id: str) -> User:
    row = db.get(User, sub_id)
    if row is None or row.parent_id != parent.id:
        raise LookupError("子账号不存在")
    return row
