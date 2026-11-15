"""Authentication, sessions and role helpers for the B/S deployment."""

from __future__ import annotations

import logging
import os
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .config import settings
from .models import AuthSession, Project, User, utcnow
from .security import hash_password, new_session_token, token_hash, verify_password

logger = logging.getLogger("bidcraft.auth")

SESSION_COOKIE = "bidcraft_session"
ROLE_SUPER_ADMIN = "super_admin"
ROLE_MANAGER = "manager"
ROLE_VIEWER = "viewer"
ROLES = {ROLE_SUPER_ADMIN, ROLE_MANAGER, ROLE_VIEWER}
ROLE_LABELS = {
    ROLE_SUPER_ADMIN: "超级管理员",
    ROLE_MANAGER: "投标经理",
    ROLE_VIEWER: "只读成员",
}
ROLE_HINTS = {
    ROLE_SUPER_ADMIN: "管理用户、模型与知识库，拥有全部项目权限",
    ROLE_MANAGER: "创建和维护项目、生成正文、执行核对与导出",
    ROLE_VIEWER: "只读查看项目、台账、正文与核对结果",
}

_FAILURES: dict[str, list[float]] = {}
_FAILURE_WINDOW = 300.0
_FAILURE_LIMIT = 8


def user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "role": user.role,
        "role_label": ROLE_LABELS.get(user.role, user.role),
        "active": user.active,
        "credits": user.credits or 0,
        "is_external": bool(user.is_external),
        "wechat_nickname": user.wechat_nickname or "",
        "avatar_url": user.avatar_url or "",
        "last_login_at": _iso(user.last_login_at),
        "created_at": _iso(user.created_at),
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def ensure_admin() -> None:
    """Create the first super administrator once per database.

    The password comes from ``BIDCRAFT_ADMIN_PASSWORD``. If it is absent a
    random password is written to ``BIDCRAFT_HOME/initial-admin.txt`` (0600) so
    that no well-known default credential exists.
    """
    from .db import session_scope

    with session_scope() as db:
        existing = db.scalar(
            select(func.count()).select_from(User).where(User.role == ROLE_SUPER_ADMIN)
        )
        if existing:
            admin = db.scalar(
                select(User).where(User.role == ROLE_SUPER_ADMIN).order_by(User.created_at)
            )
            if admin is not None:
                _claim_orphans(db, admin.id)
            return
        username = settings.admin_username.strip() or "admin"
        if db.scalar(select(User).where(User.username == username)):
            username = f"{username}-admin"
        password = settings.admin_password
        generated = not password
        if generated:
            password = secrets.token_urlsafe(12)
        user = User(
            username=username,
            display_name="超级管理员",
            password_hash=hash_password(password),
            role=ROLE_SUPER_ADMIN,
            active=True,
        )
        db.add(user)
        db.flush()
        if generated:
            path = settings.home / "initial-admin.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(
                    "BidCraft 初始超级管理员\n"
                    f"用户名: {username}\n"
                    f"密码: {password}\n"
                    "登录后请立即修改密码；本文件不会再次生成。\n"
                )
            logger.warning("已创建初始管理员 %s，密码写入 %s", username, path)
        else:
            logger.info("已创建初始管理员 %s", username)
        _claim_orphans(db, user.id)


def _claim_orphans(db, admin_id: str) -> None:
    """Existing projects from pre-RBAC databases belong to the first admin."""
    from sqlalchemy import update

    db.execute(update(Project).where(Project.owner_id.is_(None)).values(owner_id=admin_id))


def register_user(
    db,
    *,
    username: str,
    password: str,
    display_name: str = "",
    is_external: bool = True,
    wechat_openid: str | None = None,
    wechat_unionid: str | None = None,
    wechat_nickname: str = "",
    avatar_url: str = "",
) -> User:
    import re

    from .billing import signup_grant

    username = (username or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        raise ValueError("用户名需为 3–40 位字母、数字、下划线或短横线")
    if len(password or "") < 8:
        raise ValueError("密码至少 8 位")
    if db.scalar(select(User).where(User.username == username)):
        raise ValueError("用户名已存在")
    if wechat_openid and db.scalar(select(User).where(User.wechat_openid == wechat_openid)):
        raise ValueError("该微信已注册")
    user = User(
        username=username,
        display_name=(display_name or username)[:120],
        password_hash=hash_password(password),
        role=ROLE_MANAGER,
        active=True,
        is_external=is_external,
        wechat_openid=wechat_openid,
        wechat_unionid=wechat_unionid,
        wechat_nickname=wechat_nickname[:120],
        avatar_url=avatar_url[:500],
    )
    db.add(user)
    db.flush()
    signup_grant(db, user)
    return user


def _throttle_key(username: str) -> str:
    return username.strip().lower()[:80]


def _recent_failures(key: str) -> list[float]:
    now = datetime.now(UTC).timestamp()
    rows = [value for value in _FAILURES.get(key, []) if now - value < _FAILURE_WINDOW]
    _FAILURES[key] = rows
    return rows


def create_session(db, user: User, *, user_agent: str = "", ip: str = "") -> str:
    token = new_session_token()
    session = AuthSession(
        user_id=user.id,
        token_hash=token_hash(token),
        expires_at=utcnow() + timedelta(days=settings.session_days),
        user_agent=user_agent[:255],
        ip=ip[:64],
    )
    db.add(session)
    user.last_login_at = utcnow()
    db.flush()
    return token


def login(db, username: str, password: str, *, user_agent: str = "", ip: str = "") -> tuple[User, str]:
    key = _throttle_key(username)
    if len(_recent_failures(key)) >= _FAILURE_LIMIT:
        raise PermissionError("尝试次数过多，请 5 分钟后再试")
    user = db.scalar(select(User).where(User.username == username.strip()))
    if user is None or not user.active or not verify_password(password, user.password_hash):
        _recent_failures(key).append(datetime.now(UTC).timestamp())
        raise PermissionError("用户名或密码不正确")
    _FAILURES.pop(key, None)
    return user, create_session(db, user, user_agent=user_agent, ip=ip)


def resolve_session(db, token: str | None) -> User | None:
    if not token:
        return None
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    if row is None or row.revoked or row.expires_at <= utcnow():
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.active:
        return None
    return user


def revoke_session(db, token: str | None) -> None:
    if not token:
        return
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    if row is not None:
        row.revoked = True


def revoke_user_sessions(db, user_id: str, *, keep: str | None = None) -> None:
    rows = db.scalars(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked.is_(False))
    )
    keep_hash = token_hash(keep) if keep else None
    for row in rows:
        if keep_hash and row.token_hash == keep_hash:
            continue
        row.revoked = True


def purge_expired_sessions(db) -> int:
    rows = list(db.scalars(select(AuthSession).where(AuthSession.expires_at <= utcnow())))
    for row in rows:
        db.delete(row)
    return len(rows)
