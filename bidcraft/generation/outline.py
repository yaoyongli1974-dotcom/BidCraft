from __future__ import annotations

import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import OutlineNode, Project, ScoringItem, Section
from ..rules.loader import Package
from ..schemas import FactLedger, OutlineItem, OutlineSpec

NEED_RULES: list[tuple[str, list[str]]] = [
    (r"资格|资质|证明", ["qualification.requirements", "bidder.name"]),
    (r"报价|价格|开标一览|分项报价", ["bidder.price", "project.budget"]),
    (r"技术|方案|施工|实施|服务|设计", ["tech.requirements", "project.content"]),
    (r"商务|合同|付款|偏离", ["project.duration", "project.warranty", "project.payment", "project.bid_validity"]),
    (r"工期|进度|计划", ["project.duration"]),
    (r"质保|售后|运维", ["project.warranty"]),
    (r"参数|清单|点位|设备|材料", ["tech.requirements"]),
]

BASE_NEEDS = ["project.name", "project.number"]


def _bigrams(text: str) -> set[str]:
    value = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text or "")
    return {value[i : i + 2] for i in range(len(value) - 1)}


def similarity(a: str, b: str) -> float:
    left, right = _bigrams(a), _bigrams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _score_name_key(name: Any) -> str:
    """Normalize a scoring-item name so spacing/punctuation variants collapse."""

    return re.sub(r"[\s\u3000()（）\[\]【】{}：:、，,。．.\-_/\\]", "", str(name or "")).lower()


def _score_as_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    keys = ("id", "name", "score", "criteria", "category", "section_hint", "source_quote")
    return {key: getattr(item, key, "") for key in keys}


def normalize_scoring(items: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Dedupe scoring items by normalized name, keeping the maximum score.

    Returns the merged list (original order preserved) and validation issues.
    Both outline construction and the coverage matrix use this single policy so
    a duplicated scoring row cannot double-count the total score.
    """

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    issues: list[str] = []
    total_before = 0.0
    for raw in items:
        data = _score_as_dict(raw)
        name = str(data.get("name") or "").strip()
        if not name:
            continue
        try:
            score = float(data.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        total_before += max(score, 0.0)
        key = _score_name_key(name)
        if key not in merged:
            record = dict(data)
            record["name"] = name
            record["score"] = score
            merged[key] = record
            order.append(key)
            continue
        existing = merged[key]
        previous_score = float(existing.get("score") or 0)
        existing["score"] = max(previous_score, score)
        criteria = [
            part
            for part in (str(existing.get("criteria") or ""), str(data.get("criteria") or ""))
            if part.strip()
        ]
        merged_criteria = "；".join(dict.fromkeys(criteria))
        if merged_criteria:
            existing["criteria"] = merged_criteria
        issues.append(
            f"评分项「{name}」重复（已合并，保留最高分 {existing['score']:g} 分）"
        )
    result = [merged[key] for key in order]
    total_after = sum(max(float(row.get("score") or 0), 0.0) for row in result)
    if issues and abs(total_after - total_before) > 1e-6:
        issues.append(
            f"评分项去重后总分由 {total_before:g} 变为 {total_after:g} 分，"
            "请核对原始评分办法"
        )
    return result, issues


def _needs_for(heading: str, package: Package) -> list[str]:
    needs: list[str] = list(BASE_NEEDS)
    for pattern, keys in NEED_RULES:
        if re.search(pattern, heading):
            needs.extend(keys)
    for item in package.outline:
        if similarity(heading, item.heading) >= 0.25:
            needs.extend(item.needs)
    return list(dict.fromkeys(needs))


def _package_items(package: Package) -> list[OutlineItem]:
    return [
        OutlineItem(
            heading=item.heading,
            intent=item.intent,
            target_words=item.target_words,
            needs=list(item.needs),
        )
        for item in package.outline
    ]


def _scoring_outline(
    ledger: FactLedger, package: Package, total_words: int
) -> list[OutlineItem]:
    package_items = _package_items(package)
    essentials = [
        OutlineItem(
            heading="投标函及投标函附录",
            intent="按招标文件格式填写，承诺报价、工期与投标有效期",
            target_words=1000,
            needs=["project.name", "project.number", "bidder.name", "project.bid_validity"],
        ),
        OutlineItem(
            heading="法定代表人身份证明及授权委托书",
            intent="提供法定代表人与授权代表信息、签字盖章",
            target_words=800,
            needs=["bidder.name"],
        ),
        OutlineItem(
            heading="投标人资格证明文件",
            intent="逐项响应资格条件并附证明材料清单",
            target_words=1800,
            needs=["qualification.requirements", "bidder.name"],
        ),
        OutlineItem(
            heading="商务响应与偏离说明",
            intent="响应付款、工期、质保、有效期等商务条款",
            target_words=1600,
            needs=[
                "project.duration",
                "project.warranty",
                "project.bid_validity",
                "project.payment",
            ],
        ),
    ]
    fixed = [
        item
        for item in package_items
        if re.search(r"投标函|资格|商务响应|报价文件", item.heading)
        and not any(similarity(item.heading, e.heading) > 0.6 for e in essentials)
    ]
    scoring, _issues = normalize_scoring(
        [item.model_dump() for item in ledger.scoring if item.name and (item.score or 0) > 0]
    )
    if not scoring:
        return package_items
    total_score = sum(float(item.get("score") or 0) for item in scoring) or 1
    items: list[OutlineItem] = list(essentials) + list(fixed)
    for score_item in scoring:
        score_value = float(score_item.get("score") or 0)
        name = str(score_item.get("name") or "")
        weight = score_value / total_score
        target = max(800, min(6000, int(total_words * weight)))
        best = max(package_items, key=lambda item: similarity(item.heading, name))
        intent = score_item.get("criteria") or best.intent
        items.append(
            OutlineItem(
                heading=name,
                intent=f"评分为 {score_value:g} 分。{intent}".strip(),
                target_words=target,
                needs=_needs_for(name, package),
            )
        )
    return items


def _tender_format_outline(ledger: FactLedger, package: Package) -> list[OutlineItem]:
    sections = [s for s in ledger.template_sections if len(s) >= 2]
    if not sections:
        return _package_items(package)
    items: list[OutlineItem] = []
    for section in sections[:40]:
        items.append(
            OutlineItem(
                heading=section[:120],
                intent="按招标文件规定的投标文件格式章编制，保持顺序与名称一致",
                target_words=1600,
                needs=_needs_for(section, package),
            )
        )
    return items


def build_outline(
    ledger: FactLedger,
    package: Package,
    *,
    mode: str = "auto",
    total_words: int = 30000,
) -> OutlineSpec:
    if mode == "auto":
        # The industry package skeleton is the product's moat: it is already
        # weighted for scoring and contains the domain chapters. Tender-format
        # and scoring-aligned outlines stay explicit choices.
        mode = "package"

    if mode == "scoring":
        items = _scoring_outline(ledger, package, total_words)
    elif mode == "tender-format":
        items = _tender_format_outline(ledger, package)
    else:
        items = _package_items(package)
        mode = "package"

    return OutlineSpec(
        title=ledger.value("project.name") or "投标文件",
        mode=mode,  # type: ignore[arg-type]
        items=items,
    )


def _flatten_outline_items(
    items: list[OutlineItem], parent_path: tuple[int, ...] | None = None
) -> list[tuple[tuple[int, ...], OutlineItem, tuple[int, ...] | None]]:
    """Flatten the nested outline spec; parents always precede their children."""

    plan: list[tuple[tuple[int, ...], OutlineItem, tuple[int, ...] | None]] = []
    for index, item in enumerate(items):
        path = (index,) if parent_path is None else parent_path + (index,)
        plan.append((path, item, parent_path))
        plan.extend(_flatten_outline_items(item.children, path))
    return plan


def save_outline(db: Session, project_id: str, spec: OutlineSpec) -> list[OutlineNode]:
    """Upsert an outline, preserving section bodies for unchanged headings.

    Mode and hierarchy were previously discarded (all rows were deleted and
    recreated): mode now lives in ``Project.meta`` and each child keeps a
    ``parent_id``.  Rebuilding the same spec is idempotent and does not wipe
    already drafted chapters.
    """

    old_nodes = list(db.scalars(select(OutlineNode).where(OutlineNode.project_id == project_id)))
    old_by_key: dict[tuple[str, str | None], list[OutlineNode]] = {}
    for node in old_nodes:
        old_by_key.setdefault((node.heading, node.parent_id), []).append(node)

    plan = _flatten_outline_items(spec.items)
    reused: set[str] = set()
    resolved: dict[tuple[int, ...], str] = {}
    for path, item, parent_path in plan:
        parent_id = resolved.get(parent_path) if parent_path is not None else None
        candidates = [
            node for node in old_by_key.get((item.heading, parent_id), []) if node.id not in reused
        ]
        node = candidates[0] if candidates else None
        if node is None:
            node = OutlineNode(project_id=project_id, heading=item.heading)
            db.add(node)
            db.flush()  # Python-side id default must be assigned before children can reference it
        reused.add(node.id)
        resolved[path] = node.id
        node.parent_id = parent_id
        node.position = len(resolved) - 1
        node.heading = item.heading
        node.intent = item.intent
        node.target_words = item.target_words
        node.needs = list(item.needs)
        node.status = node.status or "pending"

    obsolete_ids = [node.id for node in old_nodes if node.id not in reused]
    if obsolete_ids:
        db.execute(delete(Section).where(Section.node_id.in_(obsolete_ids)))
        db.execute(delete(OutlineNode).where(OutlineNode.id.in_(obsolete_ids)))

    for node_id in resolved.values():
        exists = db.scalar(select(Section.id).where(Section.node_id == node_id))
        if exists is None:
            db.add(Section(node_id=node_id, body="", status="pending", words=0))

    project = db.get(Project, project_id)
    if project is not None:
        meta = dict(project.meta or {})
        meta["outline_mode"] = spec.mode
        scoring_rows = list(
            db.scalars(select(ScoringItem).where(ScoringItem.project_id == project_id))
        )
        _scoring, issues = normalize_scoring(scoring_rows)
        if issues:
            meta["scoring_validation"] = issues
        else:
            meta.pop("scoring_validation", None)
        project.meta = meta

    db.flush()
    # Keep outline order (parent before children) — the old sort by generated UUID
    # returned a random order and broke callers that capture ids from this list.
    nodes = [db.get(OutlineNode, resolved[path]) for path, _item, _parent in plan]
    return [node for node in nodes if node is not None]


def load_outline(db: Session, project_id: str) -> list[dict[str, Any]]:
    nodes = list(
        db.scalars(
            select(OutlineNode)
            .where(OutlineNode.project_id == project_id)
            .order_by(OutlineNode.position)
        )
    )
    sections = {
        row.node_id: row
        for row in db.scalars(
            select(Section).where(Section.node_id.in_([node.id for node in nodes] or [""]))
        )
    }
    out = []
    for node in nodes:
        section = sections.get(node.id)
        out.append(
            {
                "id": node.id,
                "parent_id": node.parent_id,
                "heading": node.heading,
                "intent": node.intent,
                "target_words": node.target_words,
                "needs": node.needs or [],
                "status": node.status,
                "body": section.body if section else "",
                "words": section.words if section else 0,
                "section_id": section.id if section else None,
                "section_status": (section.status if section else "pending") or "pending",
                "review_status": (section.review_status if section else "draft") or "draft",
            }
        )
    return out


def outline_text(items: list[dict[str, Any]] | list[OutlineItem]) -> str:
    lines = []
    for index, item in enumerate(items, start=1):
        heading = item["heading"] if isinstance(item, dict) else item.heading
        target = item["target_words"] if isinstance(item, dict) else item.target_words
        lines.append(f"{index}. {heading}（约 {target} 字）")
    return "\n".join(lines)
