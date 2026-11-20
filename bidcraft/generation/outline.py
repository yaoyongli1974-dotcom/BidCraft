from __future__ import annotations

import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import OutlineNode, Section
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
    scoring = [item for item in ledger.scoring if item.name and (item.score or 0) > 0]
    if not scoring:
        return package_items
    total_score = sum(item.score or 0 for item in scoring) or 1
    items: list[OutlineItem] = list(essentials) + list(fixed)
    for score_item in scoring:
        weight = (score_item.score or 0) / total_score
        target = max(800, min(6000, int(total_words * weight)))
        best = max(package_items, key=lambda item: similarity(item.heading, score_item.name))
        intent = score_item.criteria or best.intent
        items.append(
            OutlineItem(
                heading=score_item.name,
                intent=f"评分为 {score_item.score:g} 分。{intent}".strip(),
                target_words=target,
                needs=_needs_for(score_item.name, package),
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


def save_outline(db: Session, project_id: str, spec: OutlineSpec) -> list[OutlineNode]:
    old_nodes = list(db.scalars(select(OutlineNode).where(OutlineNode.project_id == project_id)))
    old_ids = [node.id for node in old_nodes]
    if old_ids:
        db.execute(delete(Section).where(Section.node_id.in_(old_ids)))
    db.execute(delete(OutlineNode).where(OutlineNode.project_id == project_id))

    nodes: list[OutlineNode] = []
    for position, item in enumerate(spec.items):
        node = OutlineNode(
            project_id=project_id,
            position=position,
            heading=item.heading,
            intent=item.intent,
            target_words=item.target_words,
            needs=item.needs,
            status="pending",
        )
        db.add(node)
        db.flush()
        db.add(Section(node_id=node.id, body="", status="pending", words=0))
        nodes.append(node)
    return nodes


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
                "heading": node.heading,
                "intent": node.intent,
                "target_words": node.target_words,
                "needs": node.needs or [],
                "status": node.status,
                "body": section.body if section else "",
                "words": section.words if section else 0,
                "section_id": section.id if section else None,
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
