"""API routes: /collaboration（对标易标第三批 v0.26.0）。

章节编辑锁、正文修订历史/对比/回滚、章节责任分工。业务实现位于
:mod:`bidcraft.collaboration`；本模块只做鉴权、参数与 HTTP 语义映射。
"""

from __future__ import annotations

import sys

from fastapi import APIRouter, HTTPException, Query

_core = sys.modules['bidcraft.api.app']  # noqa: F841 运行期查找，保持 monkeypatch 生效

from ...authorization import Forbidden  # noqa: E402
from ...collaboration import (  # noqa: E402
    AssignmentError,
    RestoreBlocked,
    acquire_section_lock,
    assign_chapter,
    assignment_payload,
    check_section_lock,
    diff_section_revision,
    get_section_revision,
    list_assignments,
    list_section_revisions,
    release_section_lock,
    renew_section_lock,
    restore_section_revision,
    revision_payload,
    update_assignment,
)
from ...models import ChapterAssignment  # noqa: E402
from ..schemas import (  # noqa: E402
    ChapterAssignmentCreate,
    ChapterAssignmentUpdate,
    SectionLockRenew,
    SectionRevisionRestore,
)
from ..shared import (  # noqa: E402,F401
    _node,
    _project,
    _require_owner,
    _require_write,
    audit,
    current_user,
    session_scope,
)

router = APIRouter()


def _actor() -> dict:
    return current_user() or {}


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail=message)


# --------------------------------------------------------------------------- #
# 章节编辑锁
# --------------------------------------------------------------------------- #
@router.get("/projects/{ref}/chapters/{node_id}/lock")
def get_lock(ref: str, node_id: str) -> dict:
    """读取当前编辑锁；无锁返回 404，前端据此显示“未锁定”。"""
    with session_scope() as db:
        project, _role = _project(db, ref)
        node = _node(db, project.id, node_id)
        lock = check_section_lock(db, node.id)
        if lock is None:
            raise HTTPException(status_code=404, detail="章节未锁定")
        return lock


@router.post("/projects/{ref}/chapters/{node_id}/lock")
def acquire_lock(
    ref: str,
    node_id: str,
    payload: SectionLockRenew | None = None,
    ttl_seconds: int = 300,
) -> dict:
    del payload  # 获取锁不要求 token；保留 body 兼容前端统一调用
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        node = _node(db, project.id, node_id)
        user_id = str(_actor().get("id") or "")
        try:
            lock = acquire_section_lock(
                db,
                project.id,
                node.id,
                user_id,
                ttl_seconds=ttl_seconds,
                role=role,
            )
        except Forbidden as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if lock is None:
            current = check_section_lock(db, node.id)
            holder = (current or {}).get("user_id", "")
            raise _conflict(f"章节正在被其他成员编辑（{holder}）")
        audit(
            db,
            project.id,
            "chapter.lock.acquire",
            {"heading": node.heading, "node_id": node.id, "ttl_seconds": ttl_seconds},
        )
        return lock


@router.put("/projects/{ref}/chapters/{node_id}/lock")
def renew_lock(
    ref: str,
    node_id: str,
    payload: SectionLockRenew,
    ttl_seconds: int = 300,
) -> dict:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        node = _node(db, project.id, node_id)
        user_id = str(_actor().get("id") or "")
        lock = renew_section_lock(
            db,
            node.id,
            user_id,
            payload.lock_token,
            ttl_seconds=ttl_seconds,
            project_id=project.id,
        )
        if lock is None:
            raise _conflict("编辑锁已失效或不属于当前用户")
        audit(
            db,
            project.id,
            "chapter.lock.renew",
            {"heading": node.heading, "node_id": node.id, "ttl_seconds": ttl_seconds},
        )
        return lock


@router.delete("/projects/{ref}/chapters/{node_id}/lock")
def release_lock(
    ref: str,
    node_id: str,
    payload: SectionLockRenew | None = None,
    lock_token: str | None = Query(default=None),
) -> dict:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        node = _node(db, project.id, node_id)
        user_id = str(_actor().get("id") or "")
        token = payload.lock_token if payload is not None else lock_token
        released = release_section_lock(
            db, node.id, user_id, token, project_id=project.id
        )
        if not released:
            raise _conflict("编辑锁不存在、已失效或不属于当前用户")
        audit(
            db,
            project.id,
            "chapter.lock.release",
            {"heading": node.heading, "node_id": node.id},
        )
        return {"released": True}


# --------------------------------------------------------------------------- #
# 修订历史 / 对比 / 回滚
# --------------------------------------------------------------------------- #
@router.get("/projects/{ref}/chapters/{node_id}/revisions")
def revisions(ref: str, node_id: str) -> dict:
    with session_scope() as db:
        project, _role = _project(db, ref)
        node = _node(db, project.id, node_id)
        rows = list_section_revisions(db, node.id)
        return {
            "node_id": node.id,
            "heading": node.heading,
            "revisions": [revision_payload(row) for row in rows],
        }


@router.get("/projects/{ref}/chapters/{node_id}/revisions/diff")
def revision_diff(
    ref: str,
    node_id: str,
    from_revision: int = Query(alias="from"),
    to_revision: int = Query(alias="to"),
) -> dict:
    with session_scope() as db:
        project, _role = _project(db, ref)
        node = _node(db, project.id, node_id)
        old = get_section_revision(db, node.id, from_revision)
        new = get_section_revision(db, node.id, to_revision)
        if old is None:
            raise HTTPException(status_code=404, detail=f"修订版本不存在: {from_revision}")
        if new is None:
            raise HTTPException(status_code=404, detail=f"修订版本不存在: {to_revision}")
        payload = diff_section_revision(old, new)
        payload.update({"node_id": node.id, "heading": node.heading})
        return payload


@router.get("/projects/{ref}/chapters/{node_id}/revisions/{revision}")
def revision_detail(ref: str, node_id: str, revision: int) -> dict:
    with session_scope() as db:
        project, _role = _project(db, ref)
        node = _node(db, project.id, node_id)
        row = get_section_revision(db, node.id, revision)
        if row is None:
            raise HTTPException(status_code=404, detail=f"修订版本不存在: {revision}")
        return revision_payload(row)


@router.post("/projects/{ref}/chapters/{node_id}/revisions/restore")
def restore_revision(
    ref: str, node_id: str, payload: SectionRevisionRestore
) -> dict:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        node = _node(db, project.id, node_id)
        try:
            row = restore_section_revision(
                db, node.id, payload.revision, _actor(), payload.note
            )
        except RestoreBlocked as exc:
            raise _conflict(str(exc)) from exc
        result = revision_payload(row)
        result["restored_from"] = int(payload.revision)
        return result


# --------------------------------------------------------------------------- #
# 章节责任分工
# --------------------------------------------------------------------------- #
@router.get("/projects/{ref}/assignments")
def assignments(ref: str) -> dict:
    with session_scope() as db:
        project, _role = _project(db, ref)
        rows = list_assignments(db, project.id)
        return {
            "project_id": project.id,
            "assignments": [assignment_payload(db, row) for row in rows],
        }


@router.post("/projects/{ref}/assignments", status_code=201)
def create_assignment(ref: str, payload: ChapterAssignmentCreate) -> dict:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        try:
            row = assign_chapter(
                db,
                project.id,
                payload.node_id,
                payload.assignee_id,
                _actor(),
                deadline=payload.deadline,
                note=payload.note,
            )
        except AssignmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return assignment_payload(db, row)


@router.put("/projects/{ref}/assignments/{assignment_id}")
def edit_assignment(
    ref: str, assignment_id: str, payload: ChapterAssignmentUpdate
) -> dict:
    with session_scope() as db:
        project, role = _project(db, ref)
        _require_write(role)
        row = db.get(ChapterAssignment, assignment_id)
        if row is None or row.project_id != project.id:
            raise HTTPException(status_code=404, detail="分工不存在")
        try:
            row = update_assignment(
                db,
                assignment_id,
                status=payload.status,
                note=payload.note,
                actor=_actor(),
            )
        except AssignmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return assignment_payload(db, row)
