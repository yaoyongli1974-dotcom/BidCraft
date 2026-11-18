"""R22 项目复制（模板复用）：把一个项目深拷贝为可独立编辑的新项目。

复制范围：项目元信息（meta 深拷贝）、来源文件（含物理文件）、六类台账、
大纲/正文（正文 revision 重置 0）、可选的 BOQ 包件与快照、可选的配图资产。

不复制：BoqQuoteVersion（报价历史与审批属于旧投标）、Issue、Job/JobEvent、
Delivery、ProjectMember、AuditLog。

约定：
- 源物理文件缺失不阻断复制：行仍复制，stored_path/file_path 指向新目录中
  （尚不存在的）目标路径；真正的复制失败（OSError 等）向上抛出。
- 抛出异常时在异常对象上附带 ``duplicate_project_id``，供 API 层清理已创建
  的新项目目录；函数本身不做审计、不提交事务。
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

from sqlalchemy import JSON, select
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    BidPackage,
    BoqSnapshot,
    Fact,
    ImageAsset,
    Material,
    OutlineNode,
    Parameter,
    Project,
    Requirement,
    Risk,
    ScoringItem,
    Section,
    SourceFile,
)

# id/project_id 由新行重新生成；时间戳沿用各模型默认值（复制即新建）。
_SKIPPED_COLUMNS = frozenset({"id", "project_id", "created_at", "updated_at"})

_LEDGER_MODELS = (Fact, Material, Parameter, ScoringItem, Requirement, Risk)


def _column_values(row) -> dict:
    """Copy every business column; JSON payloads are deep-copied so the new
    project never shares mutable meta/items/prices/options with the source."""

    values: dict = {}
    for column in row.__table__.columns:
        if column.name in _SKIPPED_COLUMNS:
            continue
        value = getattr(row, column.name)
        if isinstance(column.type, JSON):
            value = copy.deepcopy(value)
        values[column.name] = value
    return values


def _unique_path(directory: Path, filename: str) -> Path:
    """Keep the original basename; on collision append ``-1``/``-2``… (same
    convention as ``pipeline.add_source``)."""

    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = Path(filename).stem, Path(filename).suffix
    index = 1
    while target.exists():
        target = directory / f"{stem}-{index}{suffix}"
        index += 1
    return target


def _resolve_name(name: str | None, source_name: str) -> str:
    cleaned = str(name or "").strip()
    final = cleaned or f"{source_name} 副本"
    if len(final) > 255:
        raise ValueError("项目名称需为 1–255 个字符")
    return final


def _copy_sources(
    db: Session, old_id: str, new_id: str, old_dir: Path, new_dir: Path
) -> dict[str, str]:
    source_map: dict[str, str] = {}
    rows = list(
        db.scalars(
            select(SourceFile)
            .where(SourceFile.project_id == old_id)
            .order_by(SourceFile.created_at)
        )
    )
    if not rows:
        return source_map
    sources_dir = new_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    for source in rows:
        values = _column_values(source)
        target = _unique_path(sources_dir, source.filename)
        origin = Path(source.stored_path)
        if not origin.is_absolute():
            origin = old_dir / origin
        # 缺失的源文件不阻断复制：行保留，stored_path 指向新目录目标位置。
        if origin.is_file():
            shutil.copy2(origin, target)
        values["stored_path"] = str(target)
        clone = SourceFile(project_id=new_id, **values)
        db.add(clone)
        db.flush()
        source_map[source.id] = clone.id
    return source_map


def _copy_ledger(db: Session, old_id: str, new_id: str, source_map: dict[str, str]) -> None:
    for model in _LEDGER_MODELS:
        for row in db.scalars(select(model).where(model.project_id == old_id)):
            values = _column_values(row)
            if values.get("source_file_id"):
                values["source_file_id"] = source_map.get(values["source_file_id"], "")
            db.add(model(project_id=new_id, **values))
    db.flush()


def _copy_outline(db: Session, old_id: str, new_id: str) -> dict[str, str]:
    nodes = list(
        db.scalars(
            select(OutlineNode)
            .where(OutlineNode.project_id == old_id)
            .order_by(OutlineNode.position, OutlineNode.created_at)
        )
    )
    node_map: dict[str, str] = {}
    # 第一遍：先建行（parent_id 置空），收集 old→new 映射。
    for node in nodes:
        values = _column_values(node)
        values.pop("parent_id", None)
        clone = OutlineNode(project_id=new_id, **values)
        db.add(clone)
        db.flush()
        node_map[node.id] = clone.id
    # 第二遍：按映射重挂父子关系；悬空 parent 置空。
    for node in nodes:
        if node.parent_id:
            clone = db.get(OutlineNode, node_map[node.id])
            clone.parent_id = node_map.get(node.parent_id)
    db.flush()
    return node_map


def _copy_sections(db: Session, node_map: dict[str, str]) -> None:
    if not node_map:
        return
    for section in db.scalars(select(Section).where(Section.node_id.in_(list(node_map)))):
        values = _column_values(section)
        values["node_id"] = node_map[section.node_id]
        values["revision"] = 0
        db.add(Section(**values))
    db.flush()


def _copy_boq(db: Session, old_id: str, new_id: str, source_map: dict[str, str]) -> None:
    package_map: dict[str, str] = {}
    packages = db.scalars(
        select(BidPackage)
        .where(BidPackage.project_id == old_id)
        .order_by(BidPackage.position, BidPackage.created_at)
    )
    for package in packages:
        clone = BidPackage(project_id=new_id, **_column_values(package))
        db.add(clone)
        db.flush()
        package_map[package.id] = clone.id
    for snapshot in db.scalars(select(BoqSnapshot).where(BoqSnapshot.project_id == old_id)):
        values = _column_values(snapshot)
        values["package_id"] = (
            package_map.get(snapshot.package_id) if snapshot.package_id else None
        )
        values["source_file_id"] = (
            source_map.get(snapshot.source_file_id, "") if snapshot.source_file_id else ""
        )
        db.add(BoqSnapshot(project_id=new_id, **values))
    db.flush()
    # BoqQuoteVersion 有意不复制：报价历史与审批记录属于旧投标。


def _copy_images(
    db: Session, old_id: str, new_id: str, old_dir: Path, new_dir: Path, node_map: dict[str, str]
) -> None:
    assets = list(db.scalars(select(ImageAsset).where(ImageAsset.project_id == old_id)))
    if not assets:
        return
    images_dir = new_dir / "images"
    for asset in assets:
        values = _column_values(asset)
        values["node_id"] = node_map.get(asset.node_id, "") if asset.node_id else ""
        file_path = ""
        if asset.status == "ready" and asset.file_path:
            origin = Path(asset.file_path)
            if not origin.is_absolute():
                origin = old_dir / origin
            if origin.is_file():
                images_dir.mkdir(parents=True, exist_ok=True)
                target = _unique_path(images_dir, origin.name)
                shutil.copy2(origin, target)
                file_path = str(target)
        # failed / 文件缺失的资产保留行，但 file_path 置空。
        values["file_path"] = file_path
        db.add(ImageAsset(project_id=new_id, **values))
    db.flush()


def duplicate_project(
    db: Session,
    project: Project,
    *,
    name: str | None,
    owner_id: str | None,
    include_boq: bool = True,
    include_images: bool = True,
) -> Project:
    """Deep-copy ``project`` into a new project owned by ``owner_id``.

    Raises ``ValueError`` for an over-long name (API maps it to 400). Any
    later failure propagates with ``duplicate_project_id`` attached so the
    caller can remove the partially populated project directory.
    """

    final_name = _resolve_name(name, project.name)
    new_project = Project(
        name=final_name,
        package=project.package,
        buyer=project.buyer,
        status=project.status,
        meta=copy.deepcopy(project.meta or {}),
        owner_id=owner_id,
    )
    db.add(new_project)
    db.flush()
    try:
        old_dir = settings.home / "projects" / project.id
        new_dir = settings.project_dir(new_project.id)
        source_map = _copy_sources(db, project.id, new_project.id, old_dir, new_dir)
        _copy_ledger(db, project.id, new_project.id, source_map)
        node_map = _copy_outline(db, project.id, new_project.id)
        _copy_sections(db, node_map)
        if include_boq:
            _copy_boq(db, project.id, new_project.id, source_map)
        if include_images:
            _copy_images(db, project.id, new_project.id, old_dir, new_dir, node_map)
    except BaseException as exc:
        try:
            exc.duplicate_project_id = new_project.id  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover - exceptions with __slots__
            pass
        raise
    return new_project
