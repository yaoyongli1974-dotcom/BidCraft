"""Authentication, sessions and role helpers for the B/S deployment."""

from __future__ import annotations

import logging
import os
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from .audit import record_audit
from .config import settings
from .context import current_user
from .models import AuthSession, LoginRateLimit, Project, User, utcnow
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

_FAILURE_WINDOW = 300.0
_FAILURE_LIMIT = 8


def _audit_event(db, action: str, detail: dict | None = None, *, actor: dict | None = None) -> None:
    """尽力写一条审计；审计故障不应阻断登录/退出等主流程。

    口令、会话令牌与 API Key 永不进入 detail。失败事件用独立已提交会话写入，
    因为调用方的请求会话在抛异常时会回滚。
    """

    try:
        record_audit(db, None, action, detail or {}, actor=actor)
    except Exception:  # noqa: BLE001 - audit must never break authentication
        logger.warning("审计写入失败 action=%s", action, exc_info=True)


def _user_actor(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
    }


def _login_actor(username: str) -> dict:
    return {"id": None, "username": username[:80], "display_name": username[:80]}


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
        "vip_expires_at": _iso(user.vip_expires_at),
        "is_vip": bool(user.vip_expires_at and user.vip_expires_at > utcnow()),
        "parent_id": user.parent_id or "",
        "is_sub": bool(user.parent_id),
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
    _audit_event(
        db,
        "auth.register",
        {"username": username, "external": bool(is_external)},
        actor=_user_actor(user),
    )
    return user


def _throttle_key(username: str) -> str:
    return username.strip().lower()[:80]


def _failure_count(db, key: str, *, now: datetime | None = None) -> int:
    """Failures still inside the current window (expired counters count as 0)."""
    row = db.get(LoginRateLimit, key)
    if row is None or row.window_start is None:
        return 0
    moment = now or utcnow()
    if (moment - row.window_start).total_seconds() >= _FAILURE_WINDOW:
        return 0
    return int(row.hits or 0)


def purge_expired_rate_limits(db, *, now: datetime | None = None) -> int:
    """Delete counters whose window has fully elapsed; returns the row count."""
    moment = now or utcnow()
    cutoff = moment - timedelta(seconds=_FAILURE_WINDOW)
    rows = list(
        db.scalars(select(LoginRateLimit).where(LoginRateLimit.window_start <= cutoff))
    )
    for row in rows:
        db.delete(row)
    db.flush()
    return len(rows)


def _record_failure(db, key: str, *, now: datetime | None = None) -> int:
    moment = now or utcnow()
    purge_expired_rate_limits(db, now=moment)
    row = db.get(LoginRateLimit, key)
    if row is None:
        row = LoginRateLimit(key=key, hits=1, window_start=moment)
        db.add(row)
    else:
        row.hits = int(row.hits or 0) + 1
    db.flush()
    return int(row.hits)


def clear_rate_limit(db, key: str) -> bool:
    row = db.get(LoginRateLimit, key)
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


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
    from .db import session_scope

    key = _throttle_key(username)
    # Throttling lives in its own committed session: the request session below
    # rolls back on a bad password, and counters must survive that.
    with session_scope() as limits:
        purge_expired_rate_limits(limits)
        blocked = _failure_count(limits, key) >= _FAILURE_LIMIT
    if blocked:
        with session_scope() as limits:
            _audit_event(
                limits,
                "auth.login_locked",
                {"username": key, "ip": ip[:64]},
                actor=_login_actor(key),
            )
        raise PermissionError("尝试次数过多，请 5 分钟后再试")
    user = db.scalar(select(User).where(func.lower(User.username) == key).limit(1))
    if user is None or not user.active or not verify_password(password, user.password_hash):
        reason = "inactive_user" if user is not None and not user.active else "bad_credentials"
        with session_scope() as limits:
            _record_failure(limits, key)
            _audit_event(
                limits,
                "auth.login_failed",
                {"username": key, "ip": ip[:64], "reason": reason},
                actor=_login_actor(key),
            )
        raise PermissionError("用户名或密码不正确")
    with session_scope() as limits:
        clear_rate_limit(limits, key)
    token = create_session(db, user, user_agent=user_agent, ip=ip)
    _audit_event(
        db,
        "auth.login",
        {"username": user.username, "ip": ip[:64]},
        actor=_user_actor(user),
    )
    return user, token


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
        _audit_event(
            db,
            "auth.logout",
            {"session_id": row.id, "user_id": row.user_id},
            actor=current_user() or None,
        )


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
