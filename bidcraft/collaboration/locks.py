"""章节编辑锁（与审核锁分离）。

设计要点
--------
* **一把锁一条记录**：记录主键 ``id`` 直接使用 ``node_id``，借助主键唯一约束
  让「同一章节只能有一个有效锁」在数据库层成立，避免「先查后插」的竞态。
* **原子获取**：先执行带条件的 ``UPDATE``（过期锁或本人锁可直接接管），
  未命中再 ``INSERT``；并发插入由主键冲突收敛，失败方通过 SAVEPOINT 回滚
  内层语句而不破坏外层事务。
* **拥有者校验**：续期/释放必须同时匹配 ``user_id`` 与 ``lock_token``；
  项目成员被移除（或降级为 viewer）后，其锁在读取/获取时自动失效。
* **编辑锁与审核锁分离**：本模块只服务编辑互斥，章节冻结仍以
  ``Section.review_status`` 为准（已确认/锁定不可编辑）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import auth
from ..authorization import PROJECT_WRITE_ROLES, Forbidden
from ..clock import iso_beijing, utc_now
from ..models import Project, ProjectMember, SectionEditLock, User

DEFAULT_LOCK_TTL_SECONDS = 300
MIN_LOCK_TTL_SECONDS = 30
MAX_LOCK_TTL_SECONDS = 3600

_MSG_NOT_WRITER = "只读成员不能获取编辑锁"


def _normalize_ttl(ttl_seconds: int | None) -> int:
    try:
        value = int(ttl_seconds if ttl_seconds is not None else DEFAULT_LOCK_TTL_SECONDS)
    except (TypeError, ValueError):
        value = DEFAULT_LOCK_TTL_SECONDS
    return max(MIN_LOCK_TTL_SECONDS, min(MAX_LOCK_TTL_SECONDS, value))


def _project_write_allowed(db: Session, project_id: str, user_id: str) -> bool:
    """当前用户是否仍具备该项目的写权限（owner / editor / 超管）。"""

    if not project_id or not user_id:
        return False
    project = db.get(Project, project_id)
    if project is None:
        return False
    if project.owner_id and project.owner_id == user_id:
        return True
    user = db.get(User, user_id)
    if user is not None and user.role == auth.ROLE_SUPER_ADMIN:
        return True
    member = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    return member is not None and member.role == "editor"


def _payload(
    *,
    project_id: str,
    node_id: str,
    user_id: str,
    lock_token: str,
    expires_at: datetime,
    created_at: datetime | None,
    updated_at: datetime | None,
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "node_id": node_id,
        "user_id": user_id,
        "lock_token": lock_token,
        "expires_at": iso_beijing(expires_at),
        "created_at": iso_beijing(created_at),
        "updated_at": iso_beijing(updated_at),
    }


def _lock_payload(lock: SectionEditLock) -> dict[str, Any]:
    return _payload(
        project_id=lock.project_id,
        node_id=lock.node_id,
        user_id=lock.user_id,
        lock_token=lock.lock_token,
        expires_at=lock.expires_at,
        created_at=lock.created_at,
        updated_at=lock.updated_at,
    )


def _find_lock(db: Session, node_id: str) -> SectionEditLock | None:
    return db.scalar(
        select(SectionEditLock)
        .where(SectionEditLock.node_id == node_id)
        .order_by(SectionEditLock.updated_at.desc(), SectionEditLock.id)
    )


def _delete_node_locks(db: Session, node_id: str) -> None:
    db.execute(delete(SectionEditLock).where(SectionEditLock.node_id == node_id))
    db.flush()


def acquire_section_lock(
    db: Session,
    project_id: str,
    node_id: str,
    user_id: str,
    *,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    role: str | None = None,
) -> dict[str, Any] | None:
    """尝试获取章节编辑锁。

    ``role`` 为调用方已算出的项目 effective role；viewer 直接 ``Forbidden``。
    返回锁 payload（含 ``lock_token``）；被他人有效持有时返回 ``None``。
    """

    if role is not None:
        if role not in PROJECT_WRITE_ROLES:
            raise Forbidden(_MSG_NOT_WRITER)
    elif not _project_write_allowed(db, project_id, user_id):
        raise Forbidden(_MSG_NOT_WRITER)

    ttl = _normalize_ttl(ttl_seconds)
    now = utc_now()
    expires_at = now + timedelta(seconds=ttl)
    token = str(uuid.uuid4())

    # 最多重试一次：并发插入失败后若发现旧锁已无写权限，删除后再抢。
    for _attempt in range(2):
        updated = db.execute(
            update(SectionEditLock)
            .where(
                SectionEditLock.node_id == node_id,
                or_(
                    SectionEditLock.expires_at <= now,
                    SectionEditLock.user_id == user_id,
                ),
            )
            .values(
                id=node_id,
                project_id=project_id,
                user_id=user_id,
                lock_token=token,
                expires_at=expires_at,
                updated_at=now,
            )
        )
        if updated.rowcount:
            db.flush()
            return _payload(
                project_id=project_id,
                node_id=node_id,
                user_id=user_id,
                lock_token=token,
                expires_at=expires_at,
                created_at=now,
                updated_at=now,
            )

        try:
            with db.begin_nested():
                db.execute(
                    insert(SectionEditLock).values(
                        id=node_id,
                        project_id=project_id,
                        node_id=node_id,
                        user_id=user_id,
                        lock_token=token,
                        expires_at=expires_at,
                        created_at=now,
                        updated_at=now,
                    )
                )
        except IntegrityError:
            # 主键冲突：另一个并发请求已经拿到锁；检查是否需要清理失效锁。
            current = _find_lock(db, node_id)
            if current is None:
                continue
            if current.expires_at > now and _project_write_allowed(
                db, current.project_id, current.user_id
            ):
                return None
            _delete_node_locks(db, node_id)
            continue

        db.flush()
        return _payload(
            project_id=project_id,
            node_id=node_id,
            user_id=user_id,
            lock_token=token,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )

    return None


def renew_section_lock(
    db: Session,
    node_id: str,
    user_id: str,
    lock_token: str,
    *,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    project_id: str | None = None,
) -> dict[str, Any] | None:
    """续期：仅锁拥有者且 token 匹配时成功；失效/无权锁会被清理。"""

    lock = _find_lock(db, node_id)
    if lock is None:
        return None
    if project_id is not None and lock.project_id != project_id:
        return None
    now = utc_now()
    if lock.expires_at <= now:
        # 不在这里删除：留给 reap_expired_locks 统一清理并计数；续期只返回 None。
        return None
    if lock.user_id != user_id or not lock_token or lock.lock_token != lock_token:
        return None
    if not _project_write_allowed(db, lock.project_id, user_id):
        _delete_node_locks(db, node_id)
        return None

    lock.expires_at = now + timedelta(seconds=_normalize_ttl(ttl_seconds))
    lock.updated_at = now
    db.flush()
    return _lock_payload(lock)


def release_section_lock(
    db: Session,
    node_id: str,
    user_id: str,
    lock_token: str | None,
    *,
    project_id: str | None = None,
) -> bool:
    """释放锁：只有拥有者可以释放；``lock_token`` 非空时必须匹配。

    返回是否实际删除了锁。token 不匹配时锁保持原样。
    """

    lock = _find_lock(db, node_id)
    if lock is None:
        return False
    if project_id is not None and lock.project_id != project_id:
        return False
    if lock.user_id != user_id:
        return False
    if lock_token and lock.lock_token != lock_token:
        return False
    db.delete(lock)
    db.flush()
    return True


def check_section_lock(db: Session, node_id: str) -> dict[str, Any] | None:
    """读取章节当前有效编辑锁；过期或持有者已无写权限时清理并返回 ``None``。"""

    lock = _find_lock(db, node_id)
    if lock is None:
        return None
    now = utc_now()
    if lock.expires_at <= now or not _project_write_allowed(
        db, lock.project_id, lock.user_id
    ):
        _delete_node_locks(db, node_id)
        return None
    return _lock_payload(lock)


def reap_expired_locks(db: Session) -> int:
    """清理过期锁及持有者已失去写权限的锁，返回清理条数。"""

    now = utc_now()
    removed = db.execute(
        delete(SectionEditLock).where(SectionEditLock.expires_at <= now)
    ).rowcount or 0
    rows = list(db.scalars(select(SectionEditLock)))
    for lock in rows:
        if not _project_write_allowed(db, lock.project_id, lock.user_id):
            db.delete(lock)
            removed += 1
    db.flush()
    return removed
