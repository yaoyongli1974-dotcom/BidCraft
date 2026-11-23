"""商务标完整性证据：把 CommercialDoc 的真实校验结果接入质量评估。

商务完整性不再只用 major 风险数做代理。这里读取项目最新的商务标文档，
优先使用保存时落盘的 ``validation.completeness``，旧文档缺字段时按当前
必填规则重算；没有商务标文档时返回 ``None``，评分层仍走原代理逻辑。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CommercialDoc


def collect_commercial_completeness(
    db: Session, project_id: str
) -> dict[str, Any] | None:
    """返回最新商务标的完整性证据；项目没有商务标时返回 None。"""
    doc = db.scalar(
        select(CommercialDoc)
        .where(CommercialDoc.project_id == project_id)
        .order_by(CommercialDoc.updated_at.desc(), CommercialDoc.created_at.desc())
    )
    if doc is None:
        return None

    validation = dict(doc.validation or {})
    completeness = dict(validation.get("completeness") or {})
    recomputed = False
    if not completeness:
        from ..commercial import validate_commercial_completeness

        completeness = validate_commercial_completeness(
            {
                "sections": dict(doc.sections or {}),
                "quote": dict(validation.get("quote") or {}),
            }
        )
        recomputed = True

    missing = list(completeness.get("missing") or [])
    checked = int(completeness.get("checked") or 0)
    ratio = (checked - len(missing)) / checked if checked else 0.0
    ratio = max(0.0, min(1.0, ratio))
    return {
        "ok": bool(completeness.get("ok")) and not missing,
        "ratio": round(ratio, 4),
        "missing": missing,
        "checked": checked,
        "doc_id": doc.id,
        "doc_status": doc.status or "draft",
        "source": "recomputed" if recomputed else "commercial_doc",
    }
