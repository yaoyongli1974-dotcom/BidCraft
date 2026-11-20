"""事实台账 → 正文的来源追溯（provenance）。

「所有数字必须来自本项目事实或本项目文件原文」这条约束，此前只存在于提示词与
生成期规则里，用户看不到它是否被遵守。这里把绑定关系落成数据：

- 扫描每章正文，找出与事实台账某个字段**取值**相同的片段，记一条 ``FactCitation``；
- 台账字段被改写后，旧引用自动变成 ``stale``（正文里还是旧值），所在章节标记「需复核」；
- 引用与台账字段不一致时不猜测、不自动改写正文，只提示人。

判定刻意保守：只认「正文里出现了台账里的这个值」，不做语义匹配、不调用模型。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import clock
from .models import Fact, FactCitation, OutlineNode, Section

# 值太短会命中一大片无关文本（例如「1」「无」），不作为追溯依据。
MIN_VALUE_LEN = 2
GENERIC_VALUES = {"无", "有", "是", "否", "其他", "暂无", "不适用"}
NUMERIC = re.compile(r"^-?\d+(?:[.,]\d+)?$")
# 正文里表格/列表常见的包裹字符，比对时忽略两侧。
TRIM_CHARS = " \t\r\n，。；、（）()[]【】<>《》「」“”\"'`*"


def usable_value(value: str | None) -> str | None:
    """能作为追溯依据的台账取值；否则返回 None。"""

    text = (value or "").strip()
    if len(text) < MIN_VALUE_LEN or text in GENERIC_VALUES:
        return None
    return text


def count_occurrences(text: str, value: str) -> int:
    """统计 ``value`` 在正文中的出现次数，数字按「独立数字」对齐。"""

    if not text or not value:
        return 0
    numeric = bool(NUMERIC.match(value))
    count = 0
    start = 0
    while True:
        index = text.find(value, start)
        if index < 0:
            return count
        start = index + len(value)
        if not numeric:
            count += 1
            continue
        before = text[index - 1] if index > 0 else ""
        after = text[start] if start < len(text) else ""
        # 150 里不应命中 50；1.5 里不应命中 5。
        if before and (before.isdigit() or before in ".,"):
            continue
        if after and (after.isdigit() or after in ".,"):
            continue
        count += 1


def scan(body: str, facts: Iterable[Fact]) -> list[dict[str, Any]]:
    """返回本章正文命中的台账取值（每个字段一条，含出现次数）。"""

    hits: list[dict[str, Any]] = []
    for fact in facts:
        value = usable_value(fact.value)
        if value is None:
            continue
        occurrences = count_occurrences(body or "", value)
        if occurrences:
            hits.append({"fact_key": fact.key, "value": value, "occurrences": occurrences})
    return hits


def _section_for(db: Session, node_id: str) -> Section | None:
    return db.scalar(select(Section).where(Section.node_id == node_id))


def refresh_node(db: Session, project_id: str, node_id: str, body: str | None = None) -> int:
    """重扫单章并重建引用行，返回命中的台账字段数。"""

    if body is None:
        section = _section_for(db, node_id)
        body = section.body if section is not None else ""
    facts = db.scalars(select(Fact).where(Fact.project_id == project_id)).all()
    hits = scan(body, facts)
    db.execute(delete(FactCitation).where(FactCitation.node_id == node_id))
    stamp = clock.utc_now()
    for hit in hits:
        db.add(
            FactCitation(
                project_id=project_id,
                node_id=node_id,
                fact_key=hit["fact_key"],
                value=hit["value"],
                occurrences=int(hit["occurrences"]),
                created_at=stamp,
            )
        )
    return len(hits)


def refresh_project(db: Session, project_id: str) -> int:
    """重扫全书（核对与生成收尾时调用），返回命中字段总数。"""

    total = 0
    for section in db.scalars(select(Section).join(OutlineNode, Section.node_id == OutlineNode.id)
                              .where(OutlineNode.project_id == project_id)).all():
        total += refresh_node(db, project_id, section.node_id, section.body or "")
    return total


def _fact_map(db: Session, project_id: str) -> dict[str, Fact]:
    rows = db.scalars(select(Fact).where(Fact.project_id == project_id)).all()
    return {row.key: row for row in rows}


def _node_map(db: Session, project_id: str) -> dict[str, OutlineNode]:
    rows = db.scalars(select(OutlineNode).where(OutlineNode.project_id == project_id)).all()
    return {row.id: row for row in rows}


def _citation_payload(citation: FactCitation, fact: Fact | None, node: OutlineNode | None) -> dict[str, Any]:
    current = (fact.value if fact is not None else "") or ""
    return {
        "node_id": citation.node_id,
        "heading": node.heading if node is not None else "",
        "position": node.position if node is not None else 0,
        "fact_key": citation.fact_key,
        "label": (fact.label if fact is not None else "") or citation.fact_key,
        "value": citation.value,
        "current_value": current,
        "occurrences": citation.occurrences,
        "stale": current != citation.value,
        "status": fact.status if fact is not None else "",
        "source_quote": fact.source_quote if fact is not None else "",
        "source_file_id": fact.source_file_id if fact is not None else None,
        "source_page": fact.source_page if fact is not None else 0,
        "checked_at": clock.iso_beijing(citation.created_at),
    }


def node_citations(db: Session, project_id: str, node_id: str) -> list[dict[str, Any]]:
    facts = _fact_map(db, project_id)
    node = db.get(OutlineNode, node_id)
    rows = db.scalars(
        select(FactCitation).where(
            FactCitation.project_id == project_id, FactCitation.node_id == node_id
        )
    ).all()
    return [_citation_payload(row, facts.get(row.fact_key), node) for row in rows]


def node_flags(db: Session, project_id: str) -> dict[str, dict[str, int]]:
    """每个章节的引用数与待复核数，供大纲/章节 payload 打徽章（一次查询）。"""

    facts = _fact_map(db, project_id)
    flags: dict[str, dict[str, int]] = {}
    rows = db.scalars(select(FactCitation).where(FactCitation.project_id == project_id)).all()
    for row in rows:
        flag = flags.setdefault(row.node_id, {"citations": 0, "stale": 0})
        flag["citations"] += 1
        fact = facts.get(row.fact_key)
        if fact is None or (fact.value or "") != row.value:
            flag["stale"] += 1
    return flags


def report(db: Session, project_id: str) -> dict[str, Any]:
    """全书来源追溯视图：哪些字段被引用、哪些章节待复核。"""

    facts = _fact_map(db, project_id)
    nodes = _node_map(db, project_id)
    rows = db.scalars(
        select(FactCitation)
        .where(FactCitation.project_id == project_id)
        .order_by(FactCitation.created_at)
    ).all()
    citations = [_citation_payload(row, facts.get(row.fact_key), nodes.get(row.node_id)) for row in rows]
    citations.sort(key=lambda item: (item["position"], item["label"]))

    chapters: dict[str, dict[str, Any]] = {}
    for item in citations:
        chapter = chapters.setdefault(
            item["node_id"],
            {
                "node_id": item["node_id"],
                "heading": item["heading"],
                "position": item["position"],
                "citations": 0,
                "stale": 0,
            },
        )
        chapter["citations"] += 1
        if item["stale"]:
            chapter["stale"] += 1

    cited = {item["fact_key"] for item in citations}
    stale = [item for item in citations if item["stale"]]
    return {
        "summary": {
            "citations": len(citations),
            "facts_cited": len(cited),
            "stale": len(stale),
            "sections_citing": len(chapters),
            "sections_needing_review": sum(1 for c in chapters.values() if c["stale"]),
        },
        "citations": citations,
        "chapters": sorted(chapters.values(), key=lambda c: c["position"]),
    }


def affected_by_change(db: Session, project_id: str, fact_key: str, value: str) -> list[dict[str, Any]]:
    """某个字段改成 ``value`` 后仍需复核的章节（正文里还是旧值）。"""

    nodes = _node_map(db, project_id)
    rows = db.scalars(
        select(FactCitation).where(
            FactCitation.project_id == project_id, FactCitation.fact_key == fact_key
        )
    ).all()
    affected = []
    for row in rows:
        if (value or "") == row.value:
            continue
        node = nodes.get(row.node_id)
        affected.append(
            {
                "node_id": row.node_id,
                "heading": node.heading if node is not None else "",
                "stale_value": row.value,
            }
        )
    return affected
