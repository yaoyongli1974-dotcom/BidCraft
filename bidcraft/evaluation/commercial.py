"""商务标完整性证据：把 CommercialDoc 的真实校验结果接入质量评估。

商务完整性不再只用 major 风险数做代理。这里读取项目的商务标文档，
优先使用保存时落盘的 ``validation.completeness``，旧文档缺字段时按当前
必填规则重算；没有商务标文档时返回 ``None``，评分层仍走原代理逻辑。

包件模式下每个包件各自有一份商务标，只取「全局最新一份」会漏掉其它包件
的缺失项。这里按包件取各自最新文档，再取最差完整度并合并缺失清单。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CommercialDoc


def _doc_completeness(doc: CommercialDoc) -> dict[str, Any]:
    """One doc's stored (or recomputed) completeness evidence."""

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


def _missing_key(item: Any) -> tuple:
    if isinstance(item, dict):
        return (
            str(item.get("section") or ""),
            str(item.get("field") or ""),
            str(item.get("label") or ""),
        )
    return (str(item),)


def collect_commercial_completeness(
    db: Session, project_id: str
) -> dict[str, Any] | None:
    """返回项目商务标（按包件取最新）的汇总完整性证据；没有商务标时返回 None。"""

    docs = list(
        db.scalars(
            select(CommercialDoc)
            .where(CommercialDoc.project_id == project_id)
            .order_by(
                CommercialDoc.updated_at.desc(),
                CommercialDoc.created_at.desc(),
                CommercialDoc.id.desc(),
            )
        )
    )
    if not docs:
        return None

    latest_by_package: dict[str | None, CommercialDoc] = {}
    for doc in docs:
        key = doc.package_id or None
        if key not in latest_by_package:
            latest_by_package[key] = doc
    selected = list(latest_by_package.values())
    infos = [_doc_completeness(doc) for doc in selected]
    if len(infos) == 1:
        return infos[0]

    merged_missing: list[Any] = []
    seen: set[tuple] = set()
    for info in infos:
        for item in info["missing"]:
            key = _missing_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged_missing.append(item)

    checked = max(int(info["checked"]) for info in infos)
    union_ratio = (checked - len(merged_missing)) / checked if checked else 0.0
    union_ratio = max(0.0, min(1.0, union_ratio))
    ratio = min(min(info["ratio"] for info in infos), union_ratio)
    worst = min(infos, key=lambda info: info["ratio"])
    return {
        "ok": all(info["ok"] for info in infos) and not merged_missing,
        "ratio": round(ratio, 4),
        "missing": merged_missing,
        "checked": checked,
        "doc_id": worst["doc_id"],
        "doc_status": worst["doc_status"],
        "source": (
            "recomputed"
            if any(info["source"] == "recomputed" for info in infos)
            else "commercial_doc"
        ),
        # Additive detail: consumers of the original keys above stay unchanged.
        "doc_ids": [info["doc_id"] for info in infos],
        "package_ids": [doc.package_id for doc in selected],
        "docs": len(infos),
        "by_package": [
            dict(info, package_id=doc.package_id) for doc, info in zip(selected, infos)
        ],
    }
