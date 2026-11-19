"""Deterministic text quality checks: typos, cross-section consistency, duplication.

Everything here is offline and explainable: each finding carries the matched
text so a reviewer can accept or dismiss it without trusting a model.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

CATEGORY_TYPO = "文本质量"
CATEGORY_CONSISTENCY = "一致性"
CATEGORY_DUPLICATION = "重复"
MAX_FINDINGS = 200
EVIDENCE_LIMIT = 80
DUPLICATE_MIN_CHARS = 80
DUPLICATE_THRESHOLD = 0.9
DUPLICATE_MAX_PAIRS = 40

# Curated pairs only: a wrong form that is unambiguously non-standard in
# formal bidding documents. Anything ambiguous (制定/制订, 必需/必须) is out.
TYPO_RULES: tuple[tuple[str, str, str], ...] = (
    ("帐号", "账号", "财务与系统用语统一为「账号」"),
    ("帐户", "账户", "财务与系统用语统一为「账户」"),
    ("按装", "安装", "错别字"),
    ("部份", "部分", "错别字"),
    ("签定", "签订", "合同用语统一为「签订」"),
    ("做为", "作为", "错别字"),
    ("其它", "其他", "公文中统一为「其他」"),
    ("通迅", "通讯", "错别字"),
    ("既使", "即使", "错别字"),
    ("按排", "安排", "错别字"),
    ("侯选", "候选", "错别字"),
    ("重新装", "重新安装", "错别字"),
)

# "截止2026年6月" should be "截至"; "截止日期/截止时间" is correct.
_DEADLINE_RE = re.compile(r"截止(?=\s*\d{4}\s*年|\s*\d{1,2}\s*月|\s*目前|\s*现在)")

_DUP_CHAR_RE = re.compile(r"([的了我你他她它和及或与在是就都而并])\1")
_DUP_PUNCT_RE = re.compile(r"([，。；：！？、])\1+")

_KEY_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("项目名称", re.compile(r"(?:项目|工程|采购项目)名称\s*[：:]\s*([^\n。；;]{2,60})"), "项目名称前后不一致"),
    ("工期", re.compile(r"(?:计划|总|合同)?工期\s*[：:为]?\s*(\d{1,4})\s*(?:个)?(?:日历)?(?:天|日)"), "工期前后不一致"),
    ("质保期", re.compile(r"(?:质保期|保修期|质量保证期)\s*[：:为]?\s*(\d{1,3})\s*(年|个月|月)"), "质保期前后不一致"),
    ("总价", re.compile(r"(?:投标总价|总报价|报价合计|投标报价合计)\s*[：:为]?\s*[¥￥]?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:元|万元)?"), "报价金额前后不一致"),
    ("投标人名称", re.compile(r"(?:投标人名称|投标人|公司名称)\s*[：:]\s*([^\n。；;]{2,60})"), "投标人名称前后不一致"),
    ("项目负责人", re.compile(r"(?:项目负责人|项目经理)\s*[：:]\s*([\u4e00-\u9fa5]{2,4})"), "项目负责人前后不一致"),
    ("质量标准", re.compile(r"质量标准\s*[：:为]?\s*(合格|优良|合格及以上)"), "质量标准前后不一致"),
)

_WS_RE = re.compile(r"\s+")


def _evidence(text: str, start: int, end: int) -> str:
    left = max(0, start - 20)
    return text[left : min(len(text), end + 20)][:EVIDENCE_LIMIT]


def find_typos(body: str, *, heading: str = "", start: int = 1) -> list[dict[str, Any]]:
    """Flag curated wrong forms, doubled characters and doubled punctuation."""

    findings: list[dict[str, Any]] = []
    text = body or ""
    if not text.strip():
        return findings
    seq = start

    def push(message: str, recommendation: str, index: int, length: int) -> None:
        nonlocal seq
        findings.append(
            {
                "id": f"TXT-{seq:03d}",
                "level": "minor",
                "category": CATEGORY_TYPO,
                "message": message,
                "location": heading,
                "recommendation": recommendation,
                "evidence": {"quote": _evidence(text, index, index + length)},
            }
        )
        seq += 1

    for wrong, right, note in TYPO_RULES:
        for match in re.finditer(re.escape(wrong), text):
            push(
                f"疑似错别字「{wrong}」，建议改为「{right}」",
                note or f"替换为「{right}」",
                match.start(),
                len(wrong),
            )
    for match in _DEADLINE_RE.finditer(text):
        push(
            "「截止」用于时间点时应写作「截至」",
            "如指时间点改为「截至」，如指期限终点保留「截止日期」",
            match.start(),
            len(match.group(0)),
        )
    for match in _DUP_CHAR_RE.finditer(text):
        char = match.group(1)
        push(f"重复字「{char}{char}」", f"删除重复的「{char}」", match.start(), 2)
    for match in _DUP_PUNCT_RE.finditer(text):
        punct = match.group(1)
        push(f"重复标点「{punct * 2}」", f"删除多余的「{punct}」", match.start(), 2)
    return findings


def _norm_value(key: str, value: str) -> str:
    value = _WS_RE.sub("", value or "").strip("，。；;、:：")
    if key == "工期":
        return re.sub(r"\D", "", value)
    if key in ("总价",):
        return value.replace(",", "")
    return value


@dataclass
class ConsistencyHit:
    key: str
    values: dict[str, list[str]] = field(default_factory=dict)


def check_consistency(sections: Iterable[dict[str, Any]], *, start: int = 1) -> list[dict[str, Any]]:
    """Compare key facts across chapters; report keys holding two or more values."""

    seen: dict[str, dict[str, list[str]]] = {}
    for section in sections:
        body = str(section.get("body") or "")
        heading = str(section.get("heading") or "")
        if not body.strip():
            continue
        for key, pattern, _message in _KEY_PATTERNS:
            for match in pattern.finditer(body):
                raw = match.group(1) if match.groups() else match.group(0)
                value = _norm_value(key, raw)
                if not value:
                    continue
                bucket = seen.setdefault(key, {})
                places = bucket.setdefault(value, [])
                if heading and heading not in places:
                    places.append(heading)

    findings: list[dict[str, Any]] = []
    seq = start
    for key, _pattern, message in _KEY_PATTERNS:
        bucket = seen.get(key) or {}
        if len(bucket) < 2:
            continue
        parts = []
        for value, places in bucket.items():
            where = "、".join(places[:3]) or "未标注章节"
            parts.append(f"{value}（{where}）")
        findings.append(
            {
                "id": f"CON-{seq:03d}",
                "level": "major",
                "category": CATEGORY_CONSISTENCY,
                "message": message,
                "location": "、".join(sorted({p for places in bucket.values() for p in places})[:3]),
                "recommendation": "统一为同一取值并复核相关章节：" + "；".join(parts),
                "evidence": {"key": key, "values": {k: v for k, v in bucket.items()}},
            }
        )
        seq += 1
    return findings


def _bigrams(text: str) -> set[str]:
    compact = re.sub(r"[\s\W_]+", "", text)
    if len(compact) < 2:
        return set()
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def _similarity(a: str, b: str) -> float:
    left, right = _bigrams(a), _bigrams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _paragraphs(sections: Iterable[dict[str, Any]]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for section in sections:
        heading = str(section.get("heading") or "")
        body = str(section.get("body") or "")
        for chunk in re.split(r"\n{2,}|\r\n{2,}", body):
            text = chunk.strip()
            if len(re.sub(r"\s+", "", text)) >= DUPLICATE_MIN_CHARS:
                out.append((heading, text, text))
    return out


def check_duplication(sections: Iterable[dict[str, Any]], *, start: int = 1) -> list[dict[str, Any]]:
    """Paragraph-level near-duplicate detection (copy-paste between chapters)."""

    items = _paragraphs(sections)
    findings: list[dict[str, Any]] = []
    seq = start
    for i in range(len(items)):
        if len(findings) >= DUPLICATE_MAX_PAIRS:
            break
        for j in range(i + 1, len(items)):
            heading_a, text_a, _ = items[i]
            heading_b, text_b, _ = items[j]
            if not text_a or not text_b:
                continue
            score = _similarity(text_a, text_b)
            if score < DUPLICATE_THRESHOLD:
                continue
            findings.append(
                {
                    "id": f"DUP-{seq:03d}",
                    "level": "minor",
                    "category": CATEGORY_DUPLICATION,
                    "message": f"段落重复（相似度 {score:.0%}）",
                    "location": f"{heading_a} / {heading_b}".strip(" /"),
                    "recommendation": "确认是否有意复用；无必要则改写或合并，避免评委判为堆砌字数",
                    "evidence": {
                        "similarity": round(score, 3),
                        "left": text_a[:EVIDENCE_LIMIT],
                        "right": text_b[:EVIDENCE_LIMIT],
                    },
                }
            )
            seq += 1
            if len(findings) >= DUPLICATE_MAX_PAIRS:
                break
    return findings


def scan_sections(
    sections: Iterable[dict[str, Any]], *, limit: int = MAX_FINDINGS
) -> list[dict[str, Any]]:
    """Run all three checks over the outline and number findings continuously."""

    section_list = [dict(section) for section in sections]
    findings: list[dict[str, Any]] = []
    for section in section_list:
        if len(findings) >= limit:
            return findings
        findings.extend(
            find_typos(
                str(section.get("body") or ""),
                heading=str(section.get("heading") or ""),
                start=len(findings) + 1,
            )
        )
    findings = findings[:limit]
    consistency = check_consistency(section_list, start=len(findings) + 1)
    findings.extend(consistency)
    if len(findings) < limit:
        findings.extend(check_duplication(section_list, start=len(findings) + 1))
    return findings[:limit]
