"""显式的关联数据清理与孤儿检测（对应代码审查 P1-4）。

背景：SQLite 从未开启 ``foreign_keys=ON``，28 处 ForeignKey 声明只是 DDL 纸面
约束；删除路径散落在 HTTP / CLI / compliance 各处且普遍漏表，导致孤儿行。

本模块把「删除某主体时连带清理哪些行」与「检测/清理既有孤儿」收敛到一处，
供各入口复用。刻意**不**用数据库级 ``ON DELETE CASCADE``：投标项目、报价与
审计数据有长期留存要求，删除必须是显式、可审计、可测试的业务动作。

留存红线：
- ``AuditLog.user_id`` 故意允许悬挂（``account.purge`` 在删除用户后写入该 id
  作为追责依据），因此**永不**按 user_id 清理审计，也不给它加外键。
- ``AuditLog.project_id`` 同样不随项目删除而丢失：删除项目时只把该列置空
  （审计行仍全局可查），既不按 project_id 清理，也不纳入孤儿清理规则。
- ``BoqQuoteVersion`` 报价/审批历史：随项目显式删除（否则无项目可读、必成孤
  儿），但删除前由调用方写入审计；不做静默级联。
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    AuthSession,
    BidPackage,
    BoqQuoteVersion,
    BoqSnapshot,
    CreditTransaction,
    Delivery,
    Fact,
    FactCitation,
    ImageAsset,
    InvoiceRequest,
    Issue,
    Job,
    JobEvent,
    Material,
    OutlineNode,
    Parameter,
    Personnel,
    Project,
    ProjectMember,
    Qualification,
    RechargeOrder,
    Requirement,
    Risk,
    ScoringItem,
    Section,
    SourceFile,
    UserDoc,
)

# ------------------------------------------------------------------ 项目删除

#: 删除项目时需要显式清理的子表，按依赖顺序（先子后父）。
#: Section/JobEvent 依赖 node/job 先删；其余都是直接挂 project_id。
PROJECT_CHILDREN = (
    Section,          # 通过 node_id 间接挂项目
    FactCitation,     # project_id + node_id
    JobEvent,         # 通过 job_id 间接挂项目
    Issue,
    Delivery,
    Job,
    Fact,
    Material,
    Parameter,
    ScoringItem,
    Requirement,
    Risk,
    SourceFile,
    ImageAsset,
    BoqSnapshot,
    BidPackage,
    BoqQuoteVersion,
    OutlineNode,
)


def project_child_counts(db: Session, project_id: str) -> dict[str, int]:
    """返回项目各子表的行数（删除前预览 / 审计用）。"""
    counts: dict[str, int] = {}
    node_ids = list(
        db.scalars(select(OutlineNode.id).where(OutlineNode.project_id == project_id))
    )
    job_ids = list(db.scalars(select(Job.id).where(Job.project_id == project_id)))
    for model in PROJECT_CHILDREN:
        if model is Section:
            query = select(Section.id).where(Section.node_id.in_(node_ids or [""]))
        elif model is JobEvent:
            query = select(JobEvent.id).where(JobEvent.job_id.in_(job_ids or [""]))
        else:
            query = select(model.id).where(model.project_id == project_id)  # type: ignore[attr-defined]
        counts[model.__tablename__] = len(db.scalars(query).all())
    counts["project_members"] = len(
        db.scalars(
            select(ProjectMember.id).where(ProjectMember.project_id == project_id)
        ).all()
    )
    counts["audit_logs"] = len(
        db.scalars(select(AuditLog.id).where(AuditLog.project_id == project_id)).all()
    )
    return counts


def delete_project_rows(db: Session, project_id: str) -> dict[str, int]:
    """显式删除项目的全部关联行，返回各表删除条数。

    不删除 ``Project`` 本体（由调用方决定何时 ``db.delete(project)``），
    也不碰 ``AuditLog.user_id``（留存红线）。审计行只解绑 ``project_id``：
    行本身与其 action/detail 全部保留，仍可在 ``/admin/audit`` 全局查询。
    ``removed["audit_logs"]`` 为被解绑的审计条数（兼容旧调用方的计数口径）。
    """
    removed: dict[str, int] = {}
    node_ids = list(
        db.scalars(select(OutlineNode.id).where(OutlineNode.project_id == project_id))
    )
    job_ids = list(db.scalars(select(Job.id).where(Job.project_id == project_id)))
    for model in PROJECT_CHILDREN:
        if model is Section:
            stmt = delete(Section).where(Section.node_id.in_(node_ids or [""]))
        elif model is JobEvent:
            stmt = delete(JobEvent).where(JobEvent.job_id.in_(job_ids or [""]))
        else:
            stmt = delete(model).where(model.project_id == project_id)  # type: ignore[attr-defined]
        removed[model.__tablename__] = db.execute(stmt).rowcount or 0
    removed["project_members"] = db.execute(
        delete(ProjectMember).where(ProjectMember.project_id == project_id)
    ).rowcount or 0
    # 留存红线：审计行永不随项目删除；只把外键解绑，项目维度过滤自然不再命中。
    detached = (
        db.execute(
            update(AuditLog).where(AuditLog.project_id == project_id).values(project_id=None)
        ).rowcount
        or 0
    )
    removed["audit_logs"] = detached
    removed["audit_logs_detached"] = detached
    return removed


# ------------------------------------------------------------------ 用户删除


def delete_user_rows(db: Session, user_id: str) -> dict[str, int]:
    """显式清理某用户拥有的全部行（**不删除 User 本身**）。

    SQLite 从不启用外键，过去删除路径漏掉的子表会静默变成孤儿；PostgreSQL
    默认强制 ForeignKey，任何漏表都会直接让 ``DELETE FROM users`` 失败。这里
    把所有 ``ForeignKey("users.id")`` 关联与成员关系收敛到一处，供账户注销、
    超管删除、子账号删除共用。

    留存红线：不按 ``AuditLog.user_id`` 清理（注销后仍要留追责记录）。
    """

    removed: dict[str, int] = {}
    owned_ids = list(db.scalars(select(Project.id).where(Project.owner_id == user_id)))
    for project_id in owned_ids:
        for table, count in delete_project_rows(db, project_id).items():
            removed[table] = removed.get(table, 0) + count
    removed["projects"] = db.execute(
        delete(Project).where(Project.owner_id == user_id)
    ).rowcount or 0
    for model in (
        AuthSession,
        UserDoc,
        CreditTransaction,
        RechargeOrder,
        InvoiceRequest,
    ):
        removed[model.__tablename__] = db.execute(
            delete(model).where(model.user_id == user_id)
        ).rowcount or 0
    # Qualification/Personnel 用 owner_id 而非 user_id。
    for model in (Qualification, Personnel):
        removed[model.__tablename__] = db.execute(
            delete(model).where(model.owner_id == user_id)
        ).rowcount or 0
    removed["project_members"] = db.execute(
        delete(ProjectMember).where(ProjectMember.user_id == user_id)
    ).rowcount or 0
    return removed


# ------------------------------------------------------------------ 孤儿检测

#: (子表, 子表字段, 父表, 父表字段)。审计的两个字段都刻意排除：user_id 可悬挂，
#: project_id 在删项目时置空；两者都不允许被 clean_orphans 清理（留存红线）。
ORPHAN_RULES = (
    (Section, "node_id", OutlineNode, "id"),
    (FactCitation, "node_id", OutlineNode, "id"),
    (JobEvent, "job_id", Job, "id"),
    (Issue, "node_id", OutlineNode, "id"),
    (Issue, "section_id", Section, "id"),
    (Requirement, "node_id", OutlineNode, "id"),
    (Requirement, "section_id", Section, "id"),
    (ImageAsset, "node_id", OutlineNode, "id"),
    (BoqSnapshot, "source_file_id", SourceFile, "id"),
    (Fact, "source_file_id", SourceFile, "id"),
    (Job, "project_id", Project, "id"),
)


def find_orphans(db: Session, rules: Iterable = ORPHAN_RULES) -> dict[str, list[str]]:
    """返回每张子表里指向不存在父行的 id 列表（只读）。

    用于开启 ``PRAGMA foreign_keys=ON`` / 整表重建之前核对历史数据。
    键为 ``子表.字段``，值为孤儿行 id。
    """
    found: dict[str, list[str]] = {}
    for child, child_field, parent, parent_field in rules:
        child_col = getattr(child, child_field)
        parent_col = getattr(parent, parent_field)
        stmt = select(child.id).where(
            child_col.is_not(None),
            ~select(parent_col).where(parent_col == child_col).exists(),
        )
        ids = [str(row[0]) for row in db.execute(stmt).all()]
        if ids:
            found[f"{child.__tablename__}.{child_field}"] = ids
    return found


def clean_orphans(db: Session, rules: Iterable = ORPHAN_RULES) -> dict[str, int]:
    """删除孤儿行（显式调用，不自动在启动时跑），返回各规则删除条数。

    只对「派生/可重建」数据生效；调用方应先用 :func:`find_orphans` 审阅。
    """
    removed: dict[str, int] = {}
    for child, child_field, parent, parent_field in rules:
        child_col = getattr(child, child_field)
        parent_col = getattr(parent, parent_field)
        stmt = delete(child).where(
            child_col.is_not(None),
            ~select(parent_col).where(parent_col == child_col).exists(),
        )
        count = db.execute(stmt).rowcount or 0
        if count:
            removed[f"{child.__tablename__}.{child_field}"] = count
    return removed
