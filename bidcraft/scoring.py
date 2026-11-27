"""R39 评分回填：把「评分办法」从只读清单变成可复盘的数据。

每个评分项可录入实际得分（本项目或历史中标人），据此得到：
- 总分/总得分与得分率；
- 按类别聚合的强弱项；
- 失分最多的条目（按失分绝对值降序），可直接作为下一份标书的写作重点。

纯数据库读写，不联网。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .generation.outline import normalize_scoring
from .models import Project, ScoringItem

BIDDER_MAX = 80
NOTE_MAX = 200


class ScoreError(ValueError):
    """评分回填的输入不合法。"""


def _clamp_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def score_payload(item: ScoringItem) -> dict[str, Any]:
    maximum = float(item.score) if item.score is not None else None
    actual = float(item.actual_score) if item.actual_score is not None else None
    rate: float | None = None
    loss: float | None = None
    if maximum is not None and actual is not None and maximum > 0:
        rate = round(min(actual / maximum, 1.0), 4)
        loss = round(max(maximum - actual, 0.0), 4)
    return {
        "id": item.id,
        "name": item.name,
        "score": maximum,
        "category": item.category or "",
        "criteria": item.criteria or "",
        "section_hint": item.section_hint or "",
        "actual_score": actual,
        "actual_bidder": item.actual_bidder or "",
        "score_note": item.score_note or "",
        "rate": rate,
        "loss": loss,
    }


def list_scores(db: Session, project: Project) -> list[ScoringItem]:
    return list(
        db.scalars(
            select(ScoringItem)
            .where(ScoringItem.project_id == project.id)
            .order_by(ScoringItem.score.desc().nullslast(), ScoringItem.name)
        )
    )


def record_scores(db: Session, project: Project, rows: list[dict[str, Any]]) -> int:
    """批量回填实际得分；未在 rows 中出现的条目保持不变。"""

    if not rows:
        return 0
    ids = [str(row.get("id") or "") for row in rows]
    items = {
        item.id: item
        for item in db.scalars(
            select(ScoringItem).where(
                ScoringItem.project_id == project.id, ScoringItem.id.in_(ids)
            )
        )
    }
    updated = 0
    for row in rows:
        item = items.get(str(row.get("id") or ""))
        if item is None:
            raise ScoreError(f"评分项不存在: {row.get('id')}")
        raw = row.get("actual_score", "__missing__")
        if raw == "__missing__":
            continue
        if raw is None or raw == "":
            item.actual_score = None
        else:
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise ScoreError(f"实际得分必须是数字: {item.name}") from exc
            if value < 0:
                raise ScoreError(f"实际得分不能为负: {item.name}")
            if item.score is not None and value > float(item.score) + 1e-6:
                raise ScoreError(f"实际得分不能超过分值 {item.score:g}: {item.name}")
            item.actual_score = round(value, 2)
        if "actual_bidder" in row:
            item.actual_bidder = _clamp_text(row.get("actual_bidder"), BIDDER_MAX)
        if "score_note" in row:
            item.score_note = _clamp_text(row.get("score_note"), NOTE_MAX)
        updated += 1
    return updated


def analysis(db: Session, project: Project) -> dict[str, Any]:
    """汇总得分情况：总体得分率、分类强弱、失分清单。"""

    items = list_scores(db, project)
    payloads, _ = normalize_scoring([score_payload(item) for item in items])
    total_max = round(sum(p["score"] or 0 for p in payloads), 2)
    total_actual = round(sum(p["actual_score"] or 0 for p in payloads), 2)
    filled = sum(1 for p in payloads if p["actual_score"] is not None)
    rate = round(total_actual / total_max, 4) if total_max else None

    groups: dict[str, dict[str, float]] = {}
    for row in payloads:
        key = row["category"] or "未分类"
        bucket = groups.setdefault(key, {"max": 0.0, "actual": 0.0, "count": 0})
        bucket["max"] += row["score"] or 0
        bucket["actual"] += row["actual_score"] or 0
        bucket["count"] += 1
    dimensions = [
        {
            "category": name,
            "count": int(bucket["count"]),
            "max": round(bucket["max"], 2),
            "actual": round(bucket["actual"], 2),
            "rate": round(bucket["actual"] / bucket["max"], 4) if bucket["max"] else None,
        }
        for name, bucket in groups.items()
    ]
    dimensions.sort(key=lambda d: (d["rate"] if d["rate"] is not None else 1, d["category"]))

    losses = [p for p in payloads if p["loss"] and p["loss"] > 0]
    losses.sort(key=lambda p: p["loss"], reverse=True)
    return {
        "project_id": project.id,
        "total_items": len(payloads),
        "filled": filled,
        "total_max": total_max,
        "total_actual": total_actual,
        "rate": rate,
        "dimensions": dimensions,
        "losses": losses[:20],
        "items": payloads,
    }


def hints(db: Session, project: Project, *, top: int = 5) -> str:
    """生成提示用的历史失分摘要；未回填时返回空串。"""

    report = analysis(db, project)
    losses = [row for row in report["losses"] if row["actual_score"] is not None][:top]
    if not losses:
        return ""
    lines = ["历史评分失分项（写作时优先补齐证据与响应点）："]
    for row in losses:
        lines.append(
            f"- {row['name']}：{row['actual_score']:g}/{row['score']:g}"
            f"（失 {row['loss']:g} 分，类别 {row['category'] or '未分类'}）"
        )
    if report["rate"] is not None:
        lines.append(f"历史总得分率：{report['rate'] * 100:.1f}%")
    return "\n".join(lines)
