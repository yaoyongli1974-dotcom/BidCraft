"""历史人工处置经验（只读聚合，供正文/规划提示词参考）。

本模块把项目内**已经真实发生**的人工处置痕迹聚合成一小段「未审核建议经验」：
已处置的质量 finding、人工章节修订的说明、复核/强制导出审计、章节规划的人工
调整。它刻意只做只读查询——不写知识库、不写企业资产、不改写任何业务状态，也
不把建议包装成已核实事实。所有条目的 ID 都来自真实行的主键；找不到真实行或
真实节点锚点时宁可丢弃，也不猜测 ID。

输出上限 :data:`MAX_LESSONS`，按时间倒序去重后截断；``node_id`` 参数只有在
传入的 ID 确实是本项目 ``OutlineNode`` 时才会过滤出该章节的经验，项目级且没有
章节锚点的审计条目在按章节过滤时不参与。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import (
    AuditLog,
    ChapterPlan,
    OutlineNode,
    QualityFinding,
    QualityReport,
    SectionRevision,
)

#: 提示词中经验段的最大条数（硬上限，调用方无法通过参数突破）。
MAX_LESSONS = 12
#: 经验段统一标签；正文/规划提示词都以「未审核」前缀明确非事实。
LESSON_LABEL = "未审核建议经验"
#: 只有真正处置过的 finding 才沉淀经验；open/新/待处理不算。
_HANDLED_FINDING_STATUSES = frozenset(
    {"handled", "ignored", "resolved", "closed", "已处理", "已忽略", "已解决", "已关闭"}
)
#: 与人工经验有关的审计动作（其他动作不进入提示词）。
_ADVISORY_AUDIT_ACTIONS = frozenset(
    {"chapter.review", "chapter.reject", "export.force", "commercial.export_force"}
)
#: 只有已确认/已使用/被拒绝的规划才承载人工调整经验。
_PLAN_ADVISORY_STATUSES = frozenset({"CONFIRMED", "USED_FOR_GENERATION", "REJECTED"})
#: 审计 detail 中可能承载人工说明的键（按优先级取第一个非空值）。
_NOTE_KEYS = ("note", "reason", "comment", "message")
#: 经验正文的最大长度，避免单条超长说明挤占事实/规划上下文。
_MAX_TEXT = 600
#: 单条经验在纯文本块中的最大长度（含标签）。
_MAX_LINE = 700

Lesson = dict[str, Any]
"""一条经验的可序列化契约（``lessons_payload`` 保证恰好这些键）：

``id`` 真实来源行 ID（字符串）；``source`` 来源类型（``quality_finding`` /
``section_revision`` / ``audit_log`` / ``chapter_plan``）；``kind`` 中文经验类别；
``text`` 可展示的中文建议正文；``node_id`` 真实章节 ID 或 ``None``；
``created_at`` 北京时间 ISO 字符串或 ``None``；``author`` 处置人/编辑人；
``status`` 原始状态（finding 状态 / ``edit`` / 审计动作 / 规划状态）。
"""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _project_id(project: Any) -> str:
    if isinstance(project, str):
        return project
    return _text(getattr(project, "id", ""))


def _iso(value: Any) -> str | None:
    """datetime → 北京时间 ISO；已经是 ISO 字符串的保持原样（人工调整的 at）。"""

    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value.strip() or None
    return clock.iso_beijing(value)


def _dedupe_key(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "").lower()


def _first_note(detail: Any) -> str:
    if not isinstance(detail, dict):
        return ""
    for key in _NOTE_KEYS:
        note = _text(detail.get(key))
        if note:
            return note
    return ""


def _collect(db: Session, project_id: str) -> list[dict[str, Any]]:
    """从项目真实行收集经验候选；不写入任何表。"""

    raw: list[dict[str, Any]] = []
    if not project_id:
        return raw

    finding_rows = db.execute(
        select(QualityFinding, QualityReport)
        .join(QualityReport, QualityReport.id == QualityFinding.report_id)
        .where(QualityReport.project_id == project_id)
    ).all()
    for finding, report in finding_rows:
        if _text(finding.status).lower() not in _HANDLED_FINDING_STATUSES:
            continue
        evidence = finding.evidence if isinstance(finding.evidence, dict) else {}
        note = _text(evidence.get("resolution_note"))
        title = _text(finding.title)
        text = (
            f"质量问题「{title}」处置经验：{note}"
            if note
            else f"质量问题「{title}」已标记 {finding.status}"
        )
        raw.append(
            {
                "id": finding.id,
                "source": "quality_finding",
                "kind": "质量问题处置",
                "text": text[:_MAX_TEXT],
                "node_id": finding.node_id or None,
                "created_at": _iso(evidence.get("resolved_at") or report.created_at),
                "author": _text(evidence.get("resolved_by")),
                "status": finding.status or "",
                "_sort": _iso(evidence.get("resolved_at") or report.created_at) or "",
                "_dedupe": note or title,
            }
        )

    revision_rows = db.scalars(
        select(SectionRevision).where(SectionRevision.project_id == project_id)
    )
    for revision in revision_rows:
        if _text(revision.change_type).lower() != "edit":
            continue
        note = _text(revision.change_note)
        if not note:
            continue
        created = _iso(revision.created_at)
        raw.append(
            {
                "id": revision.id,
                "source": "section_revision",
                "kind": "章节人工修订",
                "text": note[:_MAX_TEXT],
                "node_id": revision.node_id or None,
                "created_at": created,
                "author": _text(revision.changed_by),
                "status": "edit",
                "_sort": created or "",
                "_dedupe": note,
            }
        )

    audit_rows = db.scalars(
        select(AuditLog).where(
            AuditLog.project_id == project_id,
            AuditLog.action.in_(sorted(_ADVISORY_AUDIT_ACTIONS)),
        )
    )
    for audit in audit_rows:
        detail = audit.detail if isinstance(audit.detail, dict) else {}
        note = _first_note(detail)
        if not note:
            continue
        heading = _text(detail.get("heading"))
        prefix = f"章节「{heading}」" if heading else ""
        created = _iso(audit.created_at)
        raw.append(
            {
                "id": str(audit.id),
                "source": "audit_log",
                "kind": f"审计经验·{audit.action}",
                "text": f"{prefix}{audit.action}：{note}"[:_MAX_TEXT],
                "node_id": detail.get("node_id") or None,
                "created_at": created,
                "author": _text(audit.display_name or audit.username),
                "status": audit.action or "",
                "_sort": created or "",
                "_dedupe": note,
            }
        )

    plan_rows = db.scalars(
        select(ChapterPlan).where(
            ChapterPlan.project_id == project_id,
            ChapterPlan.status.in_(sorted(_PLAN_ADVISORY_STATUSES)),
        )
    )
    for plan in plan_rows:
        adjustments = plan.user_adjustments if isinstance(plan.user_adjustments, list) else []
        for adjustment in adjustments:
            if not isinstance(adjustment, dict):
                continue
            note = _text(adjustment.get("note"))
            if not note:
                continue
            created = _iso(adjustment.get("at") or plan.confirmed_at or plan.updated_at)
            text = f"章节规划调整（版本 {plan.version or 0}）：{note}"
            raw.append(
                {
                    "id": plan.id,
                    "source": "chapter_plan",
                    "kind": "章节规划人工调整",
                    "text": text[:_MAX_TEXT],
                    "node_id": plan.node_id or None,
                    "created_at": created,
                    "author": _text(
                        adjustment.get("by") or plan.confirmed_by or plan.created_by
                    ),
                    "status": plan.status or "",
                    "_sort": created or "",
                    "_dedupe": note,
                }
            )
    return raw


def lesson_items(
    db: Session, project: Any, node_id: str | None = None
) -> list[dict[str, Any]]:
    """返回项目（可选某章节）的去重、按时间倒序、上限 12 条经验字典。

    返回的每个字典恰好包含 :data:`Lesson` 契约的八个键；``db`` 为 ``None``
    或项目不存在时返回空列表。本函数只读，不产生任何审计/知识库/企业资产写入。
    """

    if db is None:
        return []
    project_id = _project_id(project)
    if not project_id:
        return []
    valid_nodes = set(db.scalars(select(OutlineNode.id).where(OutlineNode.project_id == project_id)))
    # 只有确实是本项目真实章节的 ID 才允许作为过滤条件。
    if node_id and node_id not in valid_nodes:
        return []
    candidates = _collect(db, project_id)
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in sorted(candidates, key=lambda item: (item["_sort"], item["id"]), reverse=True):
        node = row.get("node_id")
        # 拒绝指向其他项目/不存在章节的锚点，避免跨项目 ID 泄漏。
        if node and node not in valid_nodes:
            row["node_id"] = None
        if node_id and row.get("node_id") != node_id:
            continue
        key = _dedupe_key(row["_dedupe"] or row["text"])
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "id": row["id"],
                "source": row["source"],
                "kind": row["kind"],
                "text": row["text"],
                "node_id": row.get("node_id") or None,
                "created_at": row.get("created_at"),
                "author": row.get("author") or "",
                "status": row.get("status") or "",
            }
        )
        if len(result) >= MAX_LESSONS:
            break
    return result


def lessons_payload(
    db: Session, project: Any, node_id: str | None = None
) -> dict[str, Any]:
    """``GET /projects/{ref}/lessons`` 的只读响应体。

    ``items`` 中每条字典恰好包含 :data:`Lesson` 契约的八个键；``status`` 恒为
    :data:`LESSON_LABEL`，明确整批条目都是未审核建议。路由只做分页/审计，不在
    这里写入知识库、企业资产或任何业务状态。
    """

    return {
        "items": lesson_items(db, project, node_id),
        "node_id": str(node_id or ""),
        "status": LESSON_LABEL,
    }


def lessons_block(db: Session, project: Any, node_id: str | None = None) -> str:
    """渲染可直接插入提示词的只读经验段；没有经验时返回空字符串。

    文案固定以「未审核建议经验」标注，并明确这些条目**不是**已核实事实，不能
    写入正文当作事实依据，也不会自动写入知识库/企业资产。
    """

    rows = lesson_items(db, project, node_id)
    if not rows:
        return ""
    lines = [
        (
            f"【{LESSON_LABEL}（仅供人工复核参考，非核实事实，不得作为正文事实依据；"
            "不会自动写入知识库或企业资产）】"
        )
    ]
    for row in rows:
        node = f" node_id={row['node_id']}" if row["node_id"] else ""
        author = f" 处置人={row['author']}" if row["author"] else ""
        line = (
            f"- [{row['kind']} id={row['id']} source={row['source']}{node}] "
            f"{row['text']}{author}"
        )
        lines.append(line[:_MAX_LINE])
    return "\n".join(lines)


__all__ = [
    "LESSON_LABEL",
    "MAX_LESSONS",
    "Lesson",
    "lesson_items",
    "lessons_block",
    "lessons_payload",
]
