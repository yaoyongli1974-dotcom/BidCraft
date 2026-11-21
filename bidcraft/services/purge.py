"""显式的关联数据清理与孤儿检测（对应代码审查 P1-4）。

背景：SQLite 从未开启 ``foreign_keys=ON``，28 处 ForeignKey 声明只是 DDL 纸面
约束；删除路径散落在 HTTP / CLI / compliance 各处且普遍漏表，导致孤儿行。

本模块把「删除某主体时连带清理哪些行」与「检测/清理既有孤儿」收敛到一处，
供各入口复用。刻意**不**用数据库级 ``ON DELETE CASCADE``：投标项目、报价与
审计数据有长期留存要求，删除必须是显式、可审计、可测试的业务动作。

留存红线：
- ``AuditLog.user_id`` 故意允许悬挂（``account.purge`` 在删除用户后写入该 id
  作为追责依据），因此**永不**按 user_id 清理审计，也不给它加外键。
- ``BoqQuoteVersion`` 报价/审批历史：随项目显式删除（否则无项目可读、必成孤
  儿），但删除前由调用方写入审计；不做静默级联。
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    BidPackage,
    BoqQuoteVersion,
    BoqSnapshot,
    Delivery,
    Fact,
    FactCitation,
    ImageAsset,
    Issue,
    Job,
    JobEvent,
    Material,
    OutlineNode,
    Parameter,
    Project,
    ProjectMember,
    Requirement,
    Risk,
    ScoringItem,
    Section,
    SourceFile,
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
    """显式删除项目的全部关联行，返回各表删除条数（含审计）。

    不删除 ``Project`` 本体（由调用方决定何时 ``db.delete(project)``），
    也不碰 ``AuditLog.user_id``（留存红线）。
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
    removed["audit_logs"] = db.execute(
        delete(AuditLog).where(AuditLog.project_id == project_id)
    ).rowcount or 0
    return removed


# ------------------------------------------------------------------ 孤儿检测

#: (子表, 子表字段, 父表, 父表字段)。AuditLog.user_id 刻意排除（留存红线）。
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
    (AuditLog, "project_id", Project, "id"),
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
