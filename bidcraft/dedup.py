"""跨文件段落查重（只读、不联网）。

递归扫描目录中的 txt/md/markdown 文件；若安装了 python-docx 则一并读取
docx 段落，否则跳过并在 ``skipped`` 中注明原因（不抛错）。段落按空行切分，
去空白后不少于 ``MIN_CHARS`` 字才参与比对；相似度复用 ``textqa`` 的字符
二元组 Jaccard（归一化后计算）。同一对文件只保留相似度最高的前 3 对，
避免刷屏。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# 复用 textqa 的字符二元组 Jaccard（私有实现，包内共享，避免两份算法漂移）。
from .textqa import _bigrams, _similarity

SUFFIXES = {".txt", ".md", ".markdown"}
MIN_CHARS = 80
DEFAULT_THRESHOLD = 0.85
DEFAULT_MAX_PAIRS = 200
DEFAULT_MAX_PARAGRAPHS = 4000
EXCERPT_CHARS = 80
TOP_PER_FILE_PAIR = 3

_WS_RE = re.compile(r"\s+")
_STRIP_RE = re.compile(r"[\s\W_]+")
_BLANK_RE = re.compile(r"\n\s*\n")
_NEWLINE_RE = re.compile(r"[ \t]*\n[ \t]*")


def split_paragraphs(text: str) -> list[str]:
    """按空行切段；去首尾空白；同一段内换行折叠为空格。"""

    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for chunk in _BLANK_RE.split(normalized):
        para = _NEWLINE_RE.sub(" ", chunk).strip()
        if para:
            out.append(para)
    return out


def normalize(text: str) -> str:
    """去掉所有空白与标点（中英文），转小写；仅用于相似度计算。"""

    return _STRIP_RE.sub("", text or "").lower()


def similarity(a: str, b: str) -> float:
    """归一化后的字符二元组 Jaccard（复用 textqa 的实现）。"""

    return _similarity(normalize(a), normalize(b))


def _eligible(paragraphs: list[str], min_chars: int) -> list[str]:
    return [p for p in paragraphs if len(_WS_RE.sub("", p)) >= min_chars]


def _read_paragraphs(path: Path) -> tuple[list[str], str]:
    """读取一个文件的原始段落；返回 (段落列表, 跳过原因)，原因为空表示成功。"""

    suffix = path.suffix.lower()
    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError:
            return [], "未安装 python-docx，跳过 docx 文件"
        try:
            document = Document(str(path))
            text = "\n\n".join(p.text for p in document.paragraphs)
        except Exception as exc:  # noqa: BLE001 - 损坏的 docx 不应中断整体查重
            return [], f"docx 读取失败：{exc}"
        return split_paragraphs(text), ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], f"读取失败：{exc}"
    return split_paragraphs(text), ""


def _walk(root: Path) -> list[Path]:
    if root.is_file():
        candidates = [root] if root.suffix.lower() in SUFFIXES | {".docx"} else []
    else:
        candidates = [
            p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES | {".docx"}
        ]
    return sorted(candidates, key=lambda p: (p.name, str(p)))


def _file_entry(path: Path, root: Path, paragraphs: int, chars: int, reason: str) -> dict[str, Any]:
    rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
    return {
        "path": rel,
        "name": path.name,
        "paragraphs": paragraphs,
        "chars": chars,
        "skipped": bool(reason),
        "reason": reason,
    }


def collect(path: str | Path, *, min_chars: int = MIN_CHARS) -> dict[str, Any]:
    """递归收集目录（或单个文件）的段落概况，只读。

    ``chars`` 为参与比对段落的原文字符数之和；``total_paragraphs`` 为参与
    比对的段落总数。无法读取的文件进入 ``skipped`` 并保留在 ``files`` 中
    （``skipped=True``）。
    """

    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"目录不存在：{root}")
    files: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    total = 0
    for item in _walk(root):
        raw, reason = _read_paragraphs(item)
        kept = _eligible(raw, min_chars) if not reason else []
        chars = sum(len(p) for p in kept)
        entry = _file_entry(item, root, len(kept), chars, reason)
        files.append(entry)
        if reason:
            skipped.append({"path": entry["path"], "name": entry["name"], "reason": reason})
        total += len(kept)
    return {"files": files, "skipped": skipped, "total_paragraphs": total}


def _excerpt(text: str) -> str:
    if len(text) <= EXCERPT_CHARS:
        return text
    return text[:EXCERPT_CHARS] + "…"


def _pair_entry(
    a: tuple[str, int, str], b: tuple[str, int, str], score: float
) -> dict[str, Any]:
    return {
        "a": {"file": a[0], "index": a[1], "excerpt": _excerpt(a[2])},
        "b": {"file": b[0], "index": b[1], "excerpt": _excerpt(b[2])},
        "similarity": round(score, 3),
    }


def compare(
    path: str | Path,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    max_pairs: int = DEFAULT_MAX_PAIRS,
    max_paragraphs: int = DEFAULT_MAX_PARAGRAPHS,
    min_chars: int = MIN_CHARS,
) -> dict[str, Any]:
    """跨文件段落查重。

    ``pairs`` 仅含跨文件重复（同一对文件最多保留前 ``TOP_PER_FILE_PAIR``
    对）；``same_file_pairs`` 为同文件内跨段重复。两者均按相似度降序、最多
    ``max_pairs`` 条。段落总数超过 ``max_paragraphs`` 时按文件顺序截断。
    ``index`` 为该文件参与比对段落列表中的下标（从 0 开始）。
    """

    root = Path(path)
    summary = collect(root, min_chars=min_chars)
    files = summary["files"]
    total_paragraphs = summary["total_paragraphs"]

    # 按文件顺序截断到 max_paragraphs，并保留段落原文用于比对。
    items: list[tuple[str, int, str]] = []  # (相对路径, 段内下标, 原文)
    budget = max_paragraphs
    truncated = False
    for entry in files:
        if entry["skipped"]:
            continue
        item_path = root / entry["path"] if root.is_dir() else root
        raw, reason = _read_paragraphs(item_path)
        if reason:
            continue
        kept = _eligible(raw, min_chars)
        if budget <= 0:
            if kept:
                truncated = True
            continue
        if len(kept) > budget:
            kept = kept[:budget]
            truncated = True
        for index, para in enumerate(kept):
            items.append((entry["path"], index, para))
        budget -= len(kept)

    # 预计算二元组集合；长度比低于阈值时 Jaccard 不可能达标，先剪枝。
    sets = [_bigrams(normalize(text)) for _, _, text in items]
    cross: list[tuple[float, int, int]] = []
    same: list[tuple[float, int, int]] = []
    for i in range(len(items)):
        left = sets[i]
        if not left:
            continue
        for j in range(i + 1, len(items)):
            right = sets[j]
            if not right:
                continue
            if min(len(left), len(right)) / max(len(left), len(right)) < threshold:
                continue
            score = len(left & right) / len(left | right)
            if score < threshold:
                continue
            if items[i][0] == items[j][0]:
                same.append((score, i, j))
            else:
                cross.append((score, i, j))

    def _ordered(hits: list[tuple[float, int, int]]) -> list[tuple[float, int, int]]:
        return sorted(hits, key=lambda hit: (-hit[0], hit[1], hit[2]))

    # 同一对文件只保留相似度最高的前 TOP_PER_FILE_PAIR 对。
    per_pair: dict[tuple[str, str], int] = {}
    kept_cross: list[tuple[float, int, int]] = []
    for score, i, j in _ordered(cross):
        key = tuple(sorted((items[i][0], items[j][0])))
        count = per_pair.get(key, 0)
        if count >= TOP_PER_FILE_PAIR:
            continue
        per_pair[key] = count + 1
        kept_cross.append((score, i, j))

    pairs = [_pair_entry(items[i], items[j], score) for score, i, j in kept_cross[:max_pairs]]
    same_file_pairs = [
        _pair_entry(items[i], items[j], score) for score, i, j in _ordered(same)[:max_pairs]
    ]
    if len(kept_cross) > max_pairs or len(same) > max_pairs:
        truncated = True

    coverage = len(items) / total_paragraphs if total_paragraphs else 0.0
    return {
        "root": str(root),
        "threshold": threshold,
        "files": files,
        "skipped": summary["skipped"],
        "pairs": pairs,
        "same_file_pairs": same_file_pairs,
        "stats": {
            "files": len(files),
            "paragraphs": total_paragraphs,
            "cross_pairs": len(pairs),
            "same_file_pairs": len(same_file_pairs),
            "coverage": round(coverage, 3),
            "truncated": truncated,
        },
    }


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(report: dict[str, Any]) -> str:
    """把查重报告渲染成 Markdown（概览表 / Top 重复对表 / 跳过文件清单）。"""

    stats = report["stats"]
    lines = [
        "# 跨文件查重报告",
        "",
        f"目录：`{report['root']}`（阈值 {report['threshold']}）",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 文件数 | {stats['files']} |",
        f"| 段落数 | {stats['paragraphs']} |",
        f"| 跨文件重复对 | {stats['cross_pairs']} |",
        f"| 同文件重复对 | {stats['same_file_pairs']} |",
        "",
        "## Top 重复对",
        "",
    ]
    if report["pairs"]:
        lines += [
            "| 相似度 | A 文件:段号 | B 文件:段号 | 摘要 |",
            "| --- | --- | --- | --- |",
        ]
        for pair in report["pairs"]:
            a, b = pair["a"], pair["b"]
            lines.append(
                f"| {pair['similarity']:.0%} | {_md_cell(a['file'])}:{a['index']} "
                f"| {_md_cell(b['file'])}:{b['index']} | {_md_cell(a['excerpt'])} |"
            )
    else:
        lines.append("（未发现跨文件重复段落）")
    lines += ["", "## 同文件重复对", ""]
    if report["same_file_pairs"]:
        lines += [
            "| 相似度 | 文件:段号 A | 文件:段号 B | 摘要 |",
            "| --- | --- | --- | --- |",
        ]
        for pair in report["same_file_pairs"]:
            a, b = pair["a"], pair["b"]
            lines.append(
                f"| {pair['similarity']:.0%} | {_md_cell(a['file'])}:{a['index']} "
                f"| {_md_cell(b['file'])}:{b['index']} | {_md_cell(a['excerpt'])} |"
            )
    else:
        lines.append("（未发现同文件重复段落）")
    lines += ["", "## 跳过文件", ""]
    skipped = [entry for entry in report["files"] if entry["skipped"]]
    if skipped:
        for entry in skipped:
            lines.append(f"- `{_md_cell(entry['path'])}`：{entry['reason']}")
    else:
        lines.append("（无）")
    if stats["truncated"]:
        lines += ["", "> 注意：结果已截断（段落数或重复对数超出上限）。"]
    return "\n".join(lines) + "\n"


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def write_json(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_json(report) + "\n", encoding="utf-8")
    return target
