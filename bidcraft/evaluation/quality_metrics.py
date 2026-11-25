"""项目级系统质量指标（只读现有权威数据，不改写任何业务状态）。

三项指标的分母在这里逐一固定，避免调用方自行推断：

* ``manual_edit_rate`` = 有正文的章节中留下人工修订（且仅有
  ``SectionRevision.change_type='edit'`` 算人工修改，``restore``/``generate``
  不算）的比例（分母：``body`` 去除空白后非空的章节数；分母为 0 → 0.0）。
  有修订快照时只认真实 ``edit`` 行；没有快照的历史章节才回退到 ``revision > 1``。
* ``issue_density`` = 未处置且级别为 blocking/major 的质量 finding 数 /
  章节正文总词数 × 1000（分母：章节正文词数，按
  ``generation.writer.count_words`` 口径：中日韩字符逐字 + 拉丁字母/数字词块；
  分母为 0 → 0.0；该指标表示每千词问题数，**不**做 0..1 截断）。
* ``boq_match_rate`` = 已匹配清单行 / 项目清单总行数
  （分母：项目所有在用 BOQ 快照的 ``items`` 行数；分母为 0 → 0.0）。

BOQ 没有落库的匹配标记：``boq.align()`` 每次依据快照 items 与实时材料台账
重新计算每行的 ``status``。这里复用该规范结果，把 status ∈
``{matched, qty_diff, unit_diff, spec_diff}``（与 ``boq._summarize`` 的
``match_rate`` 同口径：找到台账材料，即使数量/单位/规格有差异）视为已匹配。
若快照/台账数据异常无法对齐，则记入 ``notes`` 并返回 0.0，绝不臆造标记。

每项同时输出 ``coverage``（分子/分母/样本数）、``samples``（最多 20 条具体
样本）与 ``mismatches``（未处置 finding / 未匹配清单行）明细，便于人工抽查
与回归比对；样本只用于展示，比率始终由全量计数计算。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import boq
from ..models import (
    BoqSnapshot,
    Material,
    OutlineNode,
    QualityFinding,
    QualityReport,
    Section,
    SectionRevision,
)

logger = logging.getLogger("bidcraft.evaluation.quality_metrics")

#: 未处置的 finding 状态：新契约 open/new/reopened/pending（含空值），
#: 以及界面/历史数据里对应的中文「待处理/未处置」写法。handled/ignored/
#: resolved/closed（含「已处理/已忽略」）均视为已处置。
_UNRESOLVED_STATUSES = frozenset(
    {
        "",
        "open",
        "new",
        "reopened",
        "pending",
        "未解决",
        "未处置",
        "待处理",
        "待解决",
        "未关闭",
        "未修复",
    }
)
#: 计密度的严重级别；中文为前端展示用的同义写法。critical/info/minor 不计。
_SEVERE_LEVELS = frozenset({"blocking", "major", "阻断", "废标", "主要"})
#: boq.align() 中"已找到台账材料"的行状态（与 boq._summarize.match_rate 一致）。
_MATCHED_BOQ_STATUSES = frozenset({"matched", "qty_diff", "unit_diff", "spec_diff"})
#: 明细最多保留多少条样本；覆盖率计数始终基于全量，不受样本截断影响。
MAX_SAMPLES = 20


def _rate(numerator: int, denominator: int) -> float:
    """比率：四舍五入到 4 位并截断到 0..1（分母为 0 时返回 0.0）。"""
    if denominator <= 0:
        return 0.0
    return min(1.0, max(0.0, round(numerator / denominator, 4)))


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _word_count(text: str) -> int:
    """与 ``generation.writer.count_words`` 同口径的正文词数。

    局部实现以免为本指标加载生成链路的额外依赖；口径变化时以 writer 为准。
    """
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    latin = len(re.findall(r"[A-Za-z0-9]+", text or ""))
    return chinese + latin


def _section_metrics(db: Session, project_id: str, metrics: dict[str, Any]) -> None:
    rows = db.execute(
        select(
            Section.id,
            Section.node_id,
            OutlineNode.heading,
            Section.body,
            Section.revision,
        )
        .join(OutlineNode, Section.node_id == OutlineNode.id)
        .where(OutlineNode.project_id == project_id)
    ).all()
    written_rows = [
        (section_id, node_id, heading, str(body or ""), int(revision or 0))
        for section_id, node_id, heading, body, revision in rows
        if str(body or "").strip()
    ]
    written = [body for _sid, _nid, _heading, body, _rev in written_rows]
    # 人工修改只认真实的 edit 修订；restore/generate 不计。有快照的章节严格按
    # 快照判定（即使 Section.revision 因回滚/重新生成而 >1）；完全没有快照的
    # 历史章节沿用旧的 revision > 1 回退口径，避免历史数据全部归零。
    revision_types: dict[str, set[str]] = {}
    for node_id, change_type in db.execute(
        select(SectionRevision.node_id, SectionRevision.change_type).where(
            SectionRevision.project_id == project_id
        )
    ):
        revision_types.setdefault(str(node_id), set()).add(_normalized(change_type))
    def is_edited(node_id: str, revision: int) -> bool:
        types = revision_types.get(str(node_id))
        return "edit" in types if types else revision > 1

    edited = sum(
        1
        for _sid, node_id, _heading, _body, revision in written_rows
        if is_edited(node_id, revision)
    )
    metrics["manual_edit_rate"] = _rate(edited, len(written))
    metrics["coverage"]["manual_edit_rate"] = {
        "numerator": edited,
        "denominator": len(written),
        "sample_count": min(len(written_rows), MAX_SAMPLES),
        "ratio": metrics["manual_edit_rate"],
    }
    metrics["samples"]["manual_edit_rate"] = [
        {
            "section_id": section_id,
            "heading": heading,
            "revision": revision,
            "edited": is_edited(node_id, revision),
        }
        for section_id, node_id, heading, _body, revision in written_rows[:MAX_SAMPLES]
    ]
    metrics["notes"].append(f"manual_edit_rate 分母: {len(written)} 个有正文的章节")
    metrics["notes"].append(
        "manual_edit_rate 计数: SectionRevision.change_type='edit'（restore/generate 不计）；"
        "无修订快照的历史章节按 revision>1 回退"
    )

    findings = db.execute(
        select(
            QualityFinding.id,
            QualityFinding.level,
            QualityFinding.status,
            QualityFinding.title,
        )
        .join(QualityReport, QualityFinding.report_id == QualityReport.id)
        .where(QualityReport.project_id == project_id)
    ).all()
    severe_rows = [
        (finding_id, level, status, title)
        for finding_id, level, status, title in findings
        if _normalized(level) in _SEVERE_LEVELS
        and _normalized(status) in _UNRESOLVED_STATUSES
    ]
    words = sum(_word_count(body) for body in written)
    metrics["issue_density"] = (
        round(len(severe_rows) / words * 1000, 4) if words else 0.0
    )
    metrics["coverage"]["issue_density"] = {
        "numerator": len(severe_rows),
        "denominator": words,
        "unit": "findings_per_1000_words",
        "sample_count": min(len(severe_rows), MAX_SAMPLES),
        "ratio_per_1000": metrics["issue_density"],
    }
    metrics["samples"]["issue_density"] = [
        {"finding_id": finding_id, "level": level, "status": status, "title": title}
        for finding_id, level, status, title in severe_rows[:MAX_SAMPLES]
    ]
    metrics["mismatches"].extend(
        {
            "metric": "issue_density",
            "kind": "unresolved_finding",
            "finding_id": finding_id,
            "level": level,
            "status": status,
            "title": title,
        }
        for finding_id, level, status, title in severe_rows[:MAX_SAMPLES]
    )
    metrics["notes"].append(
        f"issue_density 分母: {words} 词（未处置 blocking/major finding: {len(severe_rows)}）"
    )
    metrics["notes"].append("issue_density 为每千词问题数，不按 0..1 截断")
    metrics["notes"].append("issue_density 计数范围: 项目全部报告的 finding（含当前报告）")


def _boq_metrics(db: Session, project_id: str, metrics: dict[str, Any]) -> None:
    snapshots = list(
        db.scalars(
            select(BoqSnapshot)
            .where(BoqSnapshot.project_id == project_id)
            .order_by(BoqSnapshot.created_at.desc(), BoqSnapshot.id.desc())
        )
    )
    # 每个包件/旧版 project-wide 快照只取最新一份（与 boq.get_snapshot 一致）。
    active: dict[str | None, BoqSnapshot] = {}
    for snapshot in snapshots:
        active.setdefault(snapshot.package_id, snapshot)
    materials = list(
        db.scalars(select(Material).where(Material.project_id == project_id).order_by(Material.id))
    )
    total = 0
    matched = 0
    samples: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for snapshot in active.values():
        items = list(snapshot.items or [])
        total += len(items)
        rows = boq.align(items, materials, list(snapshot.ignored or []), snapshot.sheet or "")
        for row in rows:
            if row.get("boq_row") is None:
                continue
            is_match = row.get("status") in _MATCHED_BOQ_STATUSES
            if is_match:
                matched += 1
            detail = {
                "snapshot_id": snapshot.id,
                "sheet": snapshot.sheet,
                "row": row.get("boq_row"),
                "name": row.get("name"),
                "status": row.get("status"),
                "matched": is_match,
            }
            if len(samples) < MAX_SAMPLES:
                samples.append(detail)
            if not is_match and len(mismatches) < MAX_SAMPLES:
                mismatches.append({"metric": "boq_match_rate", "kind": "boq_row", **detail})
    metrics["boq_match_rate"] = _rate(matched, total)
    metrics["coverage"]["boq_match_rate"] = {
        "numerator": matched,
        "denominator": total,
        "snapshot_count": len(active),
        "sample_count": len(samples),
        "ratio": metrics["boq_match_rate"],
    }
    metrics["samples"]["boq_match_rate"] = samples
    metrics["mismatches"].extend(mismatches)
    metrics["notes"].append(
        f"boq_match_rate 分母: {total} 条清单行（{len(active)} 份在用快照）"
    )
    if not total:
        metrics["notes"].append("boq_match_rate 为 0.0：项目没有 BOQ 清单行")
    metrics["notes"].append(
        "boq_match_rate 匹配依据: boq.align() 行状态 "
        "{matched, qty_diff, unit_diff, spec_diff}（无落库匹配标记，按规范实时对齐）"
    )


def collect_quality_metrics(db: Session | None, project_id: str) -> dict[str, Any]:
    """收集项目级质量指标；任何缺失数据都不抛异常，返回全键零值 + 说明。

    Returns exactly ``{manual_edit_rate, issue_density, boq_match_rate,
    coverage, samples, mismatches, notes}``. ``coverage`` records per-metric
    numerator/denominator, ``samples`` keeps up to :data:`MAX_SAMPLES` concrete
    sample rows, and ``mismatches`` lists unresolved findings / unmatched BOQ
    rows. Ratios are computed from full counts, never from truncated samples.
    """
    metrics: dict[str, Any] = {
        "manual_edit_rate": 0.0,
        "issue_density": 0.0,
        "boq_match_rate": 0.0,
        "coverage": {},
        "samples": {},
        "mismatches": [],
        "notes": [],
    }
    notes: list[str] = metrics["notes"]
    if db is None:
        notes.append("数据库会话不可用，三项指标均为 0.0")
        return metrics
    for name, collector in (("章节指标", _section_metrics), ("清单指标", _boq_metrics)):
        try:
            collector(db, project_id, metrics)
        except Exception as exc:  # noqa: BLE001 - 指标是旁路观测，不能拖垮评估主流程
            logger.warning("collect %s failed for project %s: %s", name, project_id, exc)
            notes.append(f"{name}不可用，相关指标保持 0.0: {type(exc).__name__}")
    return metrics


__all__ = ["collect_quality_metrics"]
