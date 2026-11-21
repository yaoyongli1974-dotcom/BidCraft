"""统一资源授权入口的单测（脱离 FastAPI，直接用 db fixture）。

验证：
- 项目可见性与 role 判定（super_admin / owner / member / 外人 404）；
- action=write/owner 的 403（Forbidden）；
- 任务按 project_id / payload.owner_id 的归属校验；
- 批量项目过滤（含 member.role 一致性修复）；
- 节点归属校验。
"""

from __future__ import annotations

import pytest

from bidcraft import auth
from bidcraft.authorization import (
    MSG_JOB_HIDDEN,
    MSG_OWNER_DENIED,
    MSG_PROJECT_HIDDEN,
    MSG_WRITE_DENIED,
    Forbidden,
    authorize_job,
    authorize_node,
    authorize_project,
    authorize_projects,
    check_owner,
    check_write,
)
from bidcraft.db import session_scope
from bidcraft.models import Job, OutlineNode, ProjectMember
from bidcraft.pipeline import create_project

OWNER = {"id": "u-owner", "username": "owner", "role": "manager"}
EDITOR = {"id": "u-editor", "username": "editor", "role": "manager"}
VIEWER = {"id": "u-viewer", "username": "viewer", "role": "viewer"}
STRANGER = {"id": "u-stranger", "username": "stranger", "role": "viewer"}
SUPER = {"id": "u-super", "username": "root", "role": auth.ROLE_SUPER_ADMIN}


def _project(session, name="授权项目"):
    project = create_project(session, name)
    project.owner_id = OWNER["id"]
    session.flush()
    return project


def _member(session, project, user, role):
    session.add(ProjectMember(project_id=project.id, user_id=user["id"], role=role))
    session.flush()


def test_project_visibility_and_roles(db):
    with session_scope() as session:
        project = _project(session)
        _member(session, project, EDITOR, "editor")
        _member(session, project, VIEWER, "viewer")

        assert authorize_project(session, project.id, user=OWNER)[1] == "owner"
        assert authorize_project(session, project.id, user=EDITOR)[1] == "editor"
        assert authorize_project(session, project.id, user=VIEWER)[1] == "viewer"
        assert authorize_project(session, project.id, user=SUPER)[1] == "admin"

        with pytest.raises(LookupError, match=MSG_PROJECT_HIDDEN):
            authorize_project(session, project.id, user=STRANGER)
        with pytest.raises(LookupError):
            authorize_project(session, project.id, user=None)  # 无请求上下文


def test_project_action_checks(db):
    with session_scope() as session:
        project = _project(session)
        _member(session, project, VIEWER, "viewer")

        authorize_project(session, project.id, action="read", user=VIEWER)
        with pytest.raises(Forbidden, match=MSG_WRITE_DENIED):
            authorize_project(session, project.id, action="write", user=VIEWER)
        with pytest.raises(Forbidden, match=MSG_OWNER_DENIED):
            authorize_project(session, project.id, action="owner", user=VIEWER)

        authorize_project(session, project.id, action="write", user=OWNER)
        authorize_project(session, project.id, action="owner", user=OWNER)
        authorize_project(session, project.id, action="owner", user=SUPER)


def test_check_write_and_owner_pure(db):
    check_write("owner")
    check_write("editor")
    check_write("admin")
    with pytest.raises(Forbidden, match=MSG_WRITE_DENIED):
        check_write("viewer")
    check_owner("owner")
    check_owner("admin")
    with pytest.raises(Forbidden, match=MSG_OWNER_DENIED):
        check_owner("editor")


def test_authorize_job_by_project_and_owner(db):
    with session_scope() as session:
        project = _project(session)
        session.add(Job(kind="generation", status="queued", project_id=project.id, payload={}))
        session.add(
            Job(
                kind="batch_export",
                status="queued",
                project_id=None,
                payload={"owner_id": OWNER["id"]},
            )
        )
        session.flush()
        by_project, by_owner = (
            session.query(Job).order_by(Job.created_at, Job.id).all()
        )

        authorize_job(session, by_project.id, user=OWNER)
        authorize_job(session, by_project.id, user=SUPER)
        with pytest.raises(LookupError, match=MSG_PROJECT_HIDDEN):
            authorize_job(session, by_project.id, user=STRANGER)

        authorize_job(session, by_owner.id, user=OWNER)
        authorize_job(session, by_owner.id, user=SUPER)
        with pytest.raises(LookupError, match=MSG_JOB_HIDDEN):
            authorize_job(session, by_owner.id, user=STRANGER)
        with pytest.raises(LookupError, match=MSG_JOB_HIDDEN):
            authorize_job(session, by_owner.id, user=None)


def test_authorize_projects_filters_and_member_role_consistency(db):
    with session_scope() as session:
        owned = _project(session, "自有项目")
        member_editor = _project(session, "成员项目")
        member_viewer = _project(session, "只读者项目")
        foreign = _project(session, "他人项目")
        _member(session, member_editor, EDITOR, "editor")
        _member(session, member_viewer, VIEWER, "viewer")

        ids = [owned.id, member_editor.id, member_viewer.id, foreign.id, owned.id, "missing"]
        allowed = authorize_projects(session, ids, user=EDITOR)
        ids_got = {p.id for p in allowed}
        # EDITOR 只对 member_editor 有成员关系；对其它项目既非 owner 也非成员。
        assert member_editor.id in ids_got
        assert owned.id not in ids_got
        assert member_viewer.id not in ids_got
        assert foreign.id not in ids_got
        assert len(allowed) == len({p.id for p in allowed})  # 去重


def test_authorize_projects_super_and_members(db):
    with session_scope() as session:
        owned = _project(session, "我的")
        foreign = _project(session, "别人的")
        assert {p.id for p in authorize_projects(session, [owned.id, foreign.id], user=SUPER)} == {
            owned.id,
            foreign.id,
        }
        assert authorize_projects(session, [owned.id, foreign.id], user=None) == []


def test_authorize_node_scoped_to_project(db):
    with session_scope() as session:
        p1 = _project(session, "项目一")
        p2 = _project(session, "项目二")
        node = OutlineNode(project_id=p1.id, heading="章节", position=1)
        session.add(node)
        session.flush()

        assert authorize_node(session, p1.id, node.id).id == node.id
        with pytest.raises(LookupError, match=node.id):
            authorize_node(session, p2.id, node.id)  # 跨项目节点 → 404
        with pytest.raises(LookupError):
            authorize_node(session, p1.id, "missing")
