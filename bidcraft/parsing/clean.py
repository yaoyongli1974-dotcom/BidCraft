from __future__ import annotations

import re
import unicodedata
from collections import Counter

_TOC_LEADER = re.compile(r"[.．。·•‥…]{3,}\s*\d{0,4}\s*$")
_PAGE_ONLY = re.compile(r"^\s*(?:第\s*)?\d{1,4}\s*(?:页)?\s*$")
_HEADER_NOISE = re.compile(r"^(?:招标文件|投标文件|技术标|商务标|第[一二三四五六七八九十\d]+[章节])\s*$")
_MATERIAL_PLACEHOLDER_RE = re.compile(r"^(?:[—\-–/\\·.．。]+|n/?a|待定|无|略)$", re.IGNORECASE)
_MATERIAL_HINT_RE = re.compile(r"按清单序号|逐项填列|详见本章|见本章|同上")


def normalize_material_key(name: str, spec: str = "") -> str:
    """材料名称+规格的稳定去重键（全角/空白/标点差异折叠）。"""

    def folded(value: str) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).lower()
        return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    return f"{folded(name)}|{folded(spec)}"


def is_material_noise(name: str, spec: str = "") -> bool:
    """识别招标文件材料表中的提示行/占位符，不当成真实台账行。"""

    value = str(name or "").strip()
    if not value or _MATERIAL_PLACEHOLDER_RE.fullmatch(value):
        return True
    if _MATERIAL_HINT_RE.search(value):
        return True
    folded_name = normalize_material_key(value, "").split("|", 1)[0]
    return len(folded_name) < 2


def strip_noise(text: str) -> str:
    """Remove TOC leaders, page numbers and repeated short header/footer lines."""
    text = (text or "").replace("\u3000", " ")
    raw_lines = [line.rstrip() for line in text.splitlines()]
    counts = Counter(line.strip() for line in raw_lines if 0 < len(line.strip()) <= 40)
    repeated = {
        line
        for line, count in counts.items()
        if count >= 5 and (len(line) <= 20 or _HEADER_NOISE.match(line) or re.search(r"\d{4}", line))
    }

    cleaned: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append("")
            continue
        if _PAGE_ONLY.match(stripped) or _TOC_LEADER.search(stripped):
            continue
        if stripped in repeated and len(stripped) <= 20:
            continue
        if _HEADER_NOISE.match(stripped):
            continue
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        cleaned.append(line)

    result: list[str] = []
    for line in cleaned:
        if not line and (not result or not result[-1]):
            continue
        result.append(line)
    return "\n".join(result).strip()


_HEADING_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百0-9]+[章节部分篇]"
    r"|[一二三四五六七八九十]{1,3}[、.．]"
    r"|[（(][一二三四五六七八九十]{1,3}[）)]"
    r"|\d{1,2}(?:\.\d{1,2}){0,2}[、.．\s])"
)


def is_heading(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped or len(stripped) > 60:
        return False
    if stripped.endswith(("。", "；", "，", ",")):
        return False
    return bool(_HEADING_RE.match(stripped))


def join_wrapped_lines(text: str) -> str:
    """Join PDF line wraps inside paragraphs while keeping headings and list rows."""
    lines = [line.strip() for line in (text or "").splitlines()]
    out: list[str] = []
    for line in lines:
        if not line:
            out.append("")
            continue
        if not out or not out[-1]:
            out.append(line)
            continue
        prev = out[-1]
        if is_heading(line) or is_heading(prev):
            out.append(line)
            continue
        if prev.endswith(("。", "；", "！", "？", ":", "：", ")", "）", "】")):
            out.append(line)
            continue
        if re.match(r"^(?:\d+[、.．)]|[（(][一二三四五六七八九十\d]+[）)]|[-*•])", line):
            out.append(line)
            continue
        # A label at the start of the next line must not be glued to the previous value.
        if re.match(r"^[^\s:：]{2,12}\s*[:：]", line):
            out.append(line)
            continue
        out[-1] = prev + line
    return "\n".join(out)


def section_slices(text: str, patterns: list[str]) -> list[tuple[str, str, int]]:
    """Split text into (heading, body, line_offset) using the first pattern that matches."""
    compiled = [re.compile(p) for p in patterns]
    lines = text.splitlines()
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 80:
            continue
        if any(p.match(stripped) for p in compiled):
            starts.append((index, stripped))
    slices: list[tuple[str, str, int]] = []
    for pos, (index, heading) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        slices.append((heading, "\n".join(lines[index + 1 : end]).strip(), index + 1))
    return slices


def detect_sections(text: str, patterns: list[str]) -> list[dict[str, str]]:
    return [{"heading": heading, "body": body} for heading, body, _ in section_slices(text, patterns)]
