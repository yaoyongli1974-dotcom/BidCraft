"""企业、成员与企业知识库容器。

权限模型（与项目成员刻意区分，避免把项目角色口径混进企业资产）：

* ``owner``（企业创建者）— 管理成员、写入/删除企业文档、登记企业共享资产；
* ``member`` — 读取企业共享资产与企业文档；
* 非成员 — 看不到企业及其任何共享资产（返回 ``LookupError``，而不是 403，
  避免泄露企业是否存在）。

资质 / 人员表的 ``enterprise_id`` 为 NULL 时仍是 owner 私有，语义与旧版完全
一致；非 NULL 时由本模块的成员关系授权。企业知识库复用 ``KnowledgeBase``，
以 ``options.enterprise_id`` 标识并标记 ``personal=True``：默认检索不会把企业
文档泄漏给非成员，生成链路通过 :func:`enterprise_kb_ids` 显式追加。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import or_, select

from ..audit import record_audit
from ..config import settings
from ..models import Enterprise, EnterpriseMember, KnowledgeBase, User

MAX_NAME = 120
ROLES = ("owner", "member")


class EnterprisePermissionError(PermissionError):
    """当前用户不是企业 owner，无权执行该写操作。"""


def _user_id(user) -> str:
    if isinstance(user, str):
        return user
    if isinstance(user, dict):
        value = user.get("id")
    else:
        value = getattr(user, "id", None)
    return str(value or "")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.replace(microsecond=0).isoformat()


def _today(today: date | datetime | None = None) -> date:
    if today is None:
        # 与资质到期判定保持同一口径（北京时间）。
        from .. import clock

        return clock.today()
    if isinstance(today, datetime):
        return today.date()
    return today


def _clean_name(name: str | None) -> str:
    value = str(name or "").strip()
    if not value:
        raise ValueError("企业名称不能为空")
    if len(value) > MAX_NAME:
        raise ValueError(f"企业名称不能超过 {MAX_NAME} 字")
    return value


# ------------------------------------------------------------------ 读取权限


def membership(db, user, enterprise_id: str) -> EnterpriseMember:
    """Return the caller's membership row or raise ``LookupError``."""

    uid = _user_id(user)
    row = db.scalar(
        select(EnterpriseMember).where(
            EnterpriseMember.enterprise_id == str(enterprise_id or ""),
            EnterpriseMember.user_id == uid,
        )
    )
    if row is not None:
        return row
    # 旧数据/迁移数据可能只有 owner_id 而没有成员行：owner 仍然可见。
    enterprise = db.get(Enterprise, str(enterprise_id or ""))
    if enterprise is not None and enterprise.owner_id == uid:
        return EnterpriseMember(
            enterprise_id=enterprise.id, user_id=uid, role="owner"
        )
    raise LookupError("企业不存在或无权访问")


def get_enterprise(db, user, enterprise_id: str) -> Enterprise:
    """Load one enterprise the caller may see (owner or member)."""

    member = membership(db, user, enterprise_id)
    enterprise = db.get(Enterprise, member.enterprise_id)
    if enterprise is None:  # pragma: no cover - defensive
        raise LookupError("企业不存在")
    return enterprise


def get_owned_enterprise(db, user, enterprise_id: str) -> Enterprise:
    """Load one enterprise only when the caller is its owner."""

    enterprise = get_enterprise(db, user, enterprise_id)
    if enterprise.owner_id != _user_id(user):
        raise EnterprisePermissionError("仅企业所有者可执行此操作")
    return enterprise


def role_in(db, user, enterprise_id: str) -> str:
    try:
        return membership(db, user, enterprise_id).role or "member"
    except LookupError:
        return ""


def is_member(db, user, enterprise_id: str) -> bool:
    return bool(role_in(db, user, enterprise_id))


def list_enterprises(db, user) -> list[Enterprise]:
    """All enterprises where the caller is owner or member, newest first."""

    uid = _user_id(user)
    member_ids = list(
        db.scalars(
            select(EnterpriseMember.enterprise_id).where(
                EnterpriseMember.user_id == uid
            )
        )
    )
    conditions = [Enterprise.owner_id == uid]
    if member_ids:
        conditions.append(Enterprise.id.in_(member_ids))
    clause = conditions[0] if len(conditions) == 1 else or_(*conditions)
    return list(
        db.scalars(
            select(Enterprise)
            .where(clause)
            .order_by(Enterprise.created_at.desc(), Enterprise.id)
        )
    )


def accessible_enterprise_ids(db, user) -> list[str]:
    return [row.id for row in list_enterprises(db, user)]


def enterprise_kb_ids(db, user_id: str) -> list[str]:
    """Enabled enterprise knowledge base ids visible to one user id."""

    if not user_id:
        return []
    rows = list(db.scalars(select(KnowledgeBase).where(KnowledgeBase.enabled.is_(True))))
    visible = {
        str((row.options or {}).get("enterprise_id") or "")
        for row in list_enterprises(db, {"id": str(user_id)})
    }
    return [
        row.id
        for row in rows
        if str((row.options or {}).get("enterprise_id") or "") in visible
    ]


def member_user_ids(db, enterprise_id: str) -> list[str]:
    return list(
        db.scalars(
            select(EnterpriseMember.user_id)
            .where(EnterpriseMember.enterprise_id == enterprise_id)
            .order_by(EnterpriseMember.created_at, EnterpriseMember.id)
        )
    )


# ------------------------------------------------------------------ 企业 CRUD


def create_enterprise(
    db,
    owner,
    name: str,
    *,
    actor: dict | None = None,
    audit: bool = True,
) -> Enterprise:
    """Create an enterprise, adding the creator as the ``owner`` member."""

    uid = _user_id(owner)
    if not uid:
        raise ValueError("缺少企业所有者")
    row = Enterprise(name=_clean_name(name), owner_id=uid)
    db.add(row)
    db.flush()
    db.add(EnterpriseMember(enterprise_id=row.id, user_id=uid, role="owner"))
    db.flush()
    if audit:
        record_audit(
            db,
            None,
            "enterprise.create",
            {"enterprise_id": row.id, "name": row.name},
            actor=actor,
        )
    return row


def update_enterprise(db, user, enterprise_id: str, name: str, *, actor=None) -> Enterprise:
    row = get_owned_enterprise(db, user, enterprise_id)
    row.name = _clean_name(name)
    db.flush()
    record_audit(
        db,
        None,
        "enterprise.update",
        {"enterprise_id": row.id, "name": row.name},
        actor=actor,
    )
    return row


def delete_enterprise(db, user, enterprise_id: str, *, actor=None) -> Enterprise:
    """Delete the enterprise container; shared asset rows are left untouched."""

    row = get_owned_enterprise(db, user, enterprise_id)
    for member in db.scalars(
        select(EnterpriseMember).where(EnterpriseMember.enterprise_id == row.id)
    ):
        db.delete(member)
    db.delete(row)
    db.flush()
    record_audit(
        db,
        None,
        "enterprise.delete",
        {"enterprise_id": row.id, "name": row.name},
        actor=actor,
    )
    return row


# ------------------------------------------------------------------ 成员 CRUD


def _find_user(db, username: str) -> User:
    value = str(username or "").strip()
    if not value:
        raise ValueError("用户名不能为空")
    row = db.scalar(select(User).where(User.username == value))
    if row is None:
        raise LookupError("用户不存在")
    return row


def add_member(
    db,
    user,
    enterprise_id: str,
    username: str,
    role: str = "member",
    *,
    actor: dict | None = None,
    audit: bool = True,
) -> EnterpriseMember:
    """Owner-only: add one user (idempotent; an existing row is updated)."""

    enterprise = get_owned_enterprise(db, user, enterprise_id)
    wanted = str(role or "member").strip() or "member"
    if wanted not in ROLES:
        raise ValueError("成员角色只能是 owner/member")
    target = _find_user(db, username)
    if target.id == enterprise.owner_id:
        raise ValueError("企业所有者已在成员列表中")
    row = db.scalar(
        select(EnterpriseMember).where(
            EnterpriseMember.enterprise_id == enterprise.id,
            EnterpriseMember.user_id == target.id,
        )
    )
    if row is None:
        row = EnterpriseMember(
            enterprise_id=enterprise.id, user_id=target.id, role=wanted
        )
        db.add(row)
    else:
        row.role = wanted
    db.flush()
    if audit:
        record_audit(
            db,
            None,
            "enterprise.member_add",
            {
                "enterprise_id": enterprise.id,
                "user_id": target.id,
                "username": target.username,
                "role": row.role,
            },
            actor=actor,
        )
    return row


def remove_member(
    db,
    user,
    enterprise_id: str,
    user_id: str,
    *,
    actor: dict | None = None,
    audit: bool = True,
) -> EnterpriseMember:
    """Owner-only: remove one membership; never removes the owner."""

    enterprise = get_owned_enterprise(db, user, enterprise_id)
    target_id = str(user_id or "")
    if target_id == enterprise.owner_id:
        raise ValueError("不能移除企业所有者")
    row = db.scalar(
        select(EnterpriseMember).where(
            EnterpriseMember.enterprise_id == enterprise.id,
            EnterpriseMember.user_id == target_id,
        )
    )
    if row is None:
        raise LookupError("成员不存在")
    db.delete(row)
    db.flush()
    if audit:
        record_audit(
            db,
            None,
            "enterprise.member_remove",
            {"enterprise_id": enterprise.id, "user_id": target_id},
            actor=actor,
        )
    return row


def list_members(db, user, enterprise_id: str) -> list[EnterpriseMember]:
    enterprise = get_enterprise(db, user, enterprise_id)
    return list(
        db.scalars(
            select(EnterpriseMember)
            .where(EnterpriseMember.enterprise_id == enterprise.id)
            .order_by(
                (EnterpriseMember.role != "owner"),
                EnterpriseMember.created_at,
                EnterpriseMember.id,
            )
        )
    )


def member_payload(row: EnterpriseMember, user: User | None = None) -> dict:
    return {
        "id": row.id,
        "enterprise_id": row.enterprise_id,
        "user_id": row.user_id,
        "username": user.username if user is not None else "",
        "display_name": (user.display_name or user.username)
        if user is not None
        else "",
        "role": row.role or "member",
        "created_at": _iso(row.created_at),
    }


def enterprise_payload(db, row: Enterprise, user=None) -> dict:
    role = ""
    if user is not None:
        try:
            role = membership(db, user, row.id).role
        except LookupError:
            role = ""
    return {
        "id": row.id,
        "name": row.name,
        "owner_id": row.owner_id,
        "role": role,
        "is_owner": bool(user is not None and row.owner_id == _user_id(user)),
        "member_count": len(member_user_ids(db, row.id)),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


# ------------------------------------------------------------ 企业知识库容器


def enterprise_kb(
    db, enterprise: Enterprise, *, create: bool = False
) -> KnowledgeBase | None:
    """Find (or lazily create) the ``KnowledgeBase`` backing one enterprise."""

    rows = list(db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.created_at)))
    for row in rows:
        if str((row.options or {}).get("enterprise_id") or "") == enterprise.id:
            return row
    if not create:
        return None
    name = f"企业知识库-{enterprise.name[:40]}-{enterprise.id[:8]}"
    existing = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == name))
    if existing is not None:  # pragma: no cover - 名称冲突兜底
        name = f"{name}-{uuid.uuid4().hex[:6]}"
        existing = None
    root = Path(settings.home) / "enterprise" / enterprise.id
    root.mkdir(parents=True, exist_ok=True)
    row = KnowledgeBase(
        name=name,
        kind="local",
        provider="local",
        location=str(root),
        # personal=True：默认检索排除，只有成员通过 extra_kb_ids 显式纳入。
        options={"enterprise_id": enterprise.id, "personal": True},
        enabled=True,
    )
    db.add(row)
    db.flush()
    return row


__all__ = [
    "EnterprisePermissionError",
    "accessible_enterprise_ids",
    "add_enterprise_member",
    "add_member",
    "create_enterprise",
    "delete_enterprise",
    "enterprise_kb",
    "enterprise_kb_ids",
    "enterprise_payload",
    "get_enterprise",
    "get_owned_enterprise",
    "is_member",
    "list_enterprise_members",
    "list_enterprises",
    "list_member",
    "list_members",
    "member_payload",
    "member_user_ids",
    "membership",
    "remove_enterprise_member",
    "remove_member",
    "role_in",
    "update_enterprise",
]

#: 契约短名的显式别名（便于 CLI / MCP / 测试按单复数两种写法调用）。
add_enterprise_member = add_member
remove_enterprise_member = remove_member
list_enterprise_members = list_members
list_member = list_members
