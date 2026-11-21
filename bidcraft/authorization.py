"""统一资源授权入口（HTTP / CLI / MCP 共用同一套判定口径）。

设计约束
--------
本模块**不 import ``bidcraft.api``**：后者是可选依赖（FastAPI），业务层与
CLI/MCP 不能依赖它，否则会形成循环依赖。因此这里只抛领域异常，HTTP 语义
由 ``api.app`` 的薄封装/全局 handler 映射：

* ``LookupError``  → 404（资源不存在，或对当前用户不可见——故意不泄露存在性）；
* ``Forbidden``    → 403（资源可见但动作不被允许）。

CLI/MCP 无请求用户上下文（``current_user()`` 为空），调用时 ``user=None``
会回退到空字典，从而得到与 HTTP 一致的「不可见→404」结果；调用方可显式
传入 ``user`` 来指定身份。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth
from .context import current_user
from .models import Job, OutlineNode, Project, ProjectMember
from .pipeline import resolve_project

#: 项目级写角色（owner/editor 及系统超管的 effective role "admin"）。
PROJECT_WRITE_ROLES = {"owner", "editor", "admin"}
#: 有效的项目成员角色。
MEMBER_ROLES = {"editor", "viewer"}
#: 项目所有者角色。
OWNER_ROLES = {"owner", "admin"}

MSG_PROJECT_HIDDEN = "项目不存在"
MSG_WRITE_DENIED = "只读成员不能修改该项目"
MSG_OWNER_DENIED = "仅项目负责人可执行此操作"
MSG_JOB_HIDDEN = "任务不存在"
MSG_NODE_HIDDEN = "章节不存在: {node_id}"


class Forbidden(Exception):
    """资源可见但动作不被允许；由入口映射为 HTTP 403。"""


# --------------------------------------------------------------------------- #
# 动作校验（纯角色判定，单一事实来源）
# --------------------------------------------------------------------------- #
def check_write(role: str) -> None:
    """写动作要求：owner / editor / admin，否则抛 ``Forbidden``。"""

    if role not in PROJECT_WRITE_ROLES:
        raise Forbidden(MSG_WRITE_DENIED)


def check_owner(role: str) -> None:
    """所有者动作要求：owner / admin，否则抛 ``Forbidden``。"""

    if role not in OWNER_ROLES:
        raise Forbidden(MSG_OWNER_DENIED)


def _effective_role(db: Session, project: Project, user: dict[str, Any]) -> str | None:
    """返回用户对项目的 effective role；无可见关系返回 ``None``。"""

    if user.get("role") == auth.ROLE_SUPER_ADMIN:
        return "admin"
    if project.owner_id and project.owner_id == user.get("id"):
        return "owner"
    member = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.get("id"),
        )
    )
    if member is not None and member.role in MEMBER_ROLES:
        return member.role
    return None


# --------------------------------------------------------------------------- #
# 资源授权入口
# --------------------------------------------------------------------------- #
def authorize_project(
    db: Session,
    ref: str,
    action: str = "read",
    user: dict[str, Any] | None = None,
) -> tuple[Project, str]:
    """解析一个当前用户可见的项目，返回 ``(project, effective_role)``。

    ``action`` 可为 ``read`` / ``write`` / ``owner``；后两者在做完可见性
    判定后追加动作校验。项目对当前用户不可见时一律 ``LookupError``（→404），
    不泄露项目是否存在。
    """

    try:
        project = resolve_project(db, ref)
    except ValueError as exc:
        raise LookupError(str(exc)) from exc
    resolved_user = current_user() if user is None else user
    if not resolved_user:
        raise LookupError(MSG_PROJECT_HIDDEN)
    role = _effective_role(db, project, resolved_user)
    if role is None:
        raise LookupError(MSG_PROJECT_HIDDEN)
    if action == "write":
        check_write(role)
    elif action == "owner":
        check_owner(role)
    return project, role


def authorize_job(
    db: Session,
    job_id: str,
    user: dict[str, Any] | None = None,
) -> Job:
    """加载任务并施加与项目同口径的归属校验。

    有 ``project_id`` 的任务走项目授权；无 ``project_id`` 的任务按
    ``payload.owner_id`` 校验（超管放行），其余一律 ``LookupError``（→404）。
    """

    job = db.get(Job, job_id)
    if job is None:
        raise LookupError(f"任务不存在: {job_id}")
    resolved_user = current_user() if user is None else user
    if job.project_id:
        authorize_project(db, job.project_id, user=resolved_user)
    else:
        owner_id = str((job.payload or {}).get("owner_id") or "")
        if not resolved_user or (
            resolved_user.get("role") != auth.ROLE_SUPER_ADMIN
            and owner_id != str(resolved_user.get("id") or "")
        ):
            raise LookupError(MSG_JOB_HIDDEN)
    return job


def authorize_projects(
    db: Session,
    ids: list[str],
    user: dict[str, Any] | None = None,
) -> list[Project]:
    """把请求的项目 id 列表过滤为当前用户可操作的项目（保持请求顺序、去重）。

    与 :func:`authorize_project` 口径一致：超管全部放行，其余仅限自有项目和
    有效成员身份的项目；未知 id 丢弃。成员分支显式校验 ``role in MEMBER_ROLES``，
    与 ``_project`` 保持一致（历史遗漏已在此收口）。
    """

    resolved_user = current_user() if user is None else user
    resolved_user = resolved_user or {}
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in ids:
        ref = str(raw or "").strip()
        if ref and ref not in seen:
            seen.add(ref)
            ordered.append(ref)
    projects: list[Project] = []
    is_admin = resolved_user.get("role") == auth.ROLE_SUPER_ADMIN
    for ref in ordered:
        project = db.get(Project, ref)
        if project is None:
            continue
        if is_admin or (project.owner_id and project.owner_id == resolved_user.get("id")):
            projects.append(project)
            continue
        member = db.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == resolved_user.get("id"),
            )
        )
        if member is not None and member.role in MEMBER_ROLES:
            projects.append(project)
    return projects


def authorize_node(
    db: Session,
    project_id: str,
    node_id: str,
) -> OutlineNode:
    """加载大纲节点并校验其归属项目；不匹配按「不存在」处理。"""

    node = db.get(OutlineNode, node_id)
    if node is None or node.project_id != project_id:
        raise LookupError(MSG_NODE_HIDDEN.format(node_id=node_id))
    return node
