"""知识库素材体检与章节覆盖度对比（只读，不写库）。

用途：在把一批 Markdown/文本灌进知识库之前，先看清里面有什么、有没有明显问题，
并把它与内置行业包的章节骨架做交叉比对，回答「哪章我们有、哪章缺、哪章冲突」。

设计原则：本模块只读文件、不索引、不联网；输出既能人读也能 JSON 化。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUFFIXES = {".md", ".markdown", ".txt"}
MAX_READ_BYTES = 8_000_000

# 版权/来源可疑标记：命中即提示人工复核，不自动判定侵权。
SUSPICIOUS_MARKERS = (
    "版权所有",
    "版权归",
    "转载",
    "来源网络",
    "来源于网络",
    "免责声明",
    "公众号",
    "微信号",
    "www.",
    "http://",
    "https://",
    "仅供学习",
    "如有侵权",
)

_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_CN_CHAPTER = re.compile(r"^\s*(第[一二三四五六七八九十百千0-9]+[章节篇部分])\s*[、.．:：]?\s*(.*)$")
_CN_ITEM = re.compile(r"^\s*([一二三四五六七八九十]{1,3})\s*[、.．]\s*(.+)$")
_CN_PAREN = re.compile(r"^\s*[（(]\s*([一二三四五六七八九十]{1,3})\s*[）)]\s*(.+)$")
_NUM_ITEM = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})\s*[、.．\s]\s*(.+)$")

# 章节名归一化时要去掉的序号前缀与标点。
_STRIP_PREFIX = re.compile(
    r"^(?:第[一二三四五六七八九十百千0-9]+[章节篇部分]|[一二三四五六七八九十]{1,3}|[（(][一二三四五六七八九十]{1,3}[）)]|\d+(?:\.\d+)*)[、.．:：\s]*"
)
_NON_WORD = re.compile(r"[\s\-—_（）()【】\[\]「」『』:：,，。.;；、·|/\\\"'“”‘’!！?？]+")


def _read_text(path: Path) -> tuple[str | None, str]:
    """读取文本；返回 (文本, 错误说明)。编码依次尝试 utf-8-sig / utf-8 / gb18030。"""
    try:
        size = path.stat().st_size
    except OSError as exc:  # pragma: no cover - 权限/文件消失
        return None, f"无法读取：{exc}"
    if size > MAX_READ_BYTES:
        return None, f"文件过大（{size} 字节），已跳过"
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding), ""
        except UnicodeDecodeError:
            continue
    return None, "编码无法识别（非 UTF-8/GB18030）"


def normalize_heading(text: str) -> str:
    """把章节标题归一化到可比较的形式：去序号、去标点、统一空白。"""
    value = (text or "").strip()
    value = re.sub(r"^#{1,6}\s*", "", value)
    value = _STRIP_PREFIX.sub("", value)
    value = _NON_WORD.sub("", value)
    return value.lower()


def extract_headings(text: str) -> list[str]:
    """抽取 Markdown 标题与中文序号标题，返回原始标题文本（未归一化）。"""
    headings: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        match = _MD_HEADING.match(line)
        if match:
            headings.append(match.group(2).strip())
            continue
        for pattern in (_CN_CHAPTER, _CN_ITEM, _CN_PAREN, _NUM_ITEM):
            hit = pattern.match(line)
            if hit:
                tail = hit.group(hit.lastindex or 1)
                title = tail.strip() if isinstance(tail, str) else ""
                if title and len(title) <= 60:
                    headings.append(title)
                break
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for item in headings:
        key = normalize_heading(item)
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def similarity(a: str, b: str) -> float:
    """章节名相似度：包含关系给 1，否则看字符二元组 Jaccard。"""
    left, right = normalize_heading(a), normalize_heading(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return 1.0
    def bigrams(value: str) -> set[str]:
        if len(value) < 2:
            return {value}
        return {value[i : i + 2] for i in range(len(value) - 1)}
    x, y = bigrams(left), bigrams(right)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def suspicious_hits(text: str) -> list[str]:
    return [marker for marker in SUSPICIOUS_MARKERS if marker in text]


@dataclass
class DocInfo:
    path: str
    chars: int = 0
    headings: list[str] = field(default_factory=list)
    error: str = ""
    duplicate_of: str = ""
    suspicious: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "chars": self.chars,
            "headings": self.headings,
            "error": self.error,
            "duplicate_of": self.duplicate_of,
            "suspicious": self.suspicious,
        }


def collect(root: Path) -> list[DocInfo]:
    """递归收集素材文件的体检信息（按路径排序）。"""
    docs: list[DocInfo] = []
    if not root.exists():
        return docs
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        text, error = _read_text(path)
        info = DocInfo(path=str(path.relative_to(root)), error=error)
        if text is not None:
            info.chars = len(text)
            info.headings = extract_headings(text)
            info.suspicious = suspicious_hits(text)
        docs.append(info)
    return docs


def mark_duplicates(docs: Iterable[DocInfo], root: Path) -> None:
    """按归一化正文哈希标记重复文件（保留首个，后续标 duplicate_of）。"""
    seen: dict[str, str] = {}
    for doc in docs:
        if doc.error:
            continue
        text, _ = _read_text(root / doc.path)
        if text is None:
            continue
        digest = hashlib.sha256(normalize_heading(text).encode("utf-8")).hexdigest()
        if digest in seen:
            doc.duplicate_of = seen[digest]
        else:
            seen[digest] = doc.path


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def package_outlines(directory: Path | None = None) -> dict[str, list[str]]:
    """读取内置行业包的章节骨架，解析 extends 继承后返回 {包名: [章节标题]}。"""
    base = directory or Path(__file__).resolve().parent / "resources" / "packages"
    raw: dict[str, dict[str, Any]] = {}
    for path in sorted(base.glob("*.yaml")):
        data = _load_yaml(path)
        name = data.get("package")
        if name:
            raw[name] = data

    resolved: dict[str, list[str]] = {}

    def resolve(name: str, stack: tuple[str, ...] = ()) -> list[str]:
        if name in resolved:
            return resolved[name]
        if name in stack or name not in raw:
            return []
        data = raw[name]
        headings: list[str] = []
        for parent in data.get("extends") or []:
            headings.extend(resolve(parent, stack + (name,)))
        outline = data.get("outline") or {}
        items = outline.get("items") if isinstance(outline, dict) else None
        for item in items or []:
            if isinstance(item, dict) and item.get("heading"):
                headings.append(str(item["heading"]))
        # replace 模式：子包自带 items 时不再叠加父包（与加载语义一致）
        if isinstance(outline, dict) and outline.get("mode") == "replace" and items:
            headings = [str(item["heading"]) for item in items if item.get("heading")]
        resolved[name] = headings
        return headings

    for name in raw:
        resolve(name)
    if directory is not None:
        # 自定义目录下没有 package 字段时，以文件名作为包名
        for path in sorted(base.glob("*.yaml")):
            if path.stem not in raw:
                data = _load_yaml(path)
                items = (data.get("outline") or {}).get("items") or []
                resolved[path.stem] = [
                    str(item["heading"]) for item in items if item.get("heading")
                ]
    return resolved


def coverage(
    docs: list[DocInfo],
    outline: list[str],
    *,
    threshold: float = 0.62,
) -> dict[str, Any]:
    """对比文档章节与某个行业包的骨架。"""
    doc_headings = [heading for doc in docs for heading in doc.headings]
    matched: list[dict[str, Any]] = []
    missing: list[str] = []
    for target in outline:
        best = (0.0, "")
        for candidate in doc_headings:
            score = similarity(target, candidate)
            if score > best[0]:
                best = (score, candidate)
        if best[0] >= threshold:
            matched.append(
                {"heading": target, "score": round(best[0], 3), "sample": best[1]}
            )
        else:
            missing.append(target)

    outline_norm = [normalize_heading(item) for item in outline]
    extra: list[str] = []
    for candidate in doc_headings:
        if any(similarity(candidate, item) >= threshold for item in outline):
            continue
        if normalize_heading(candidate) in {normalize_heading(e) for e in extra}:
            continue
        extra.append(candidate)
    del outline_norm
    total = len(outline) or 1
    return {
        "total": len(outline),
        "matched": matched,
        "missing": missing,
        "extra": extra[:80],
        "coverage": round(len(matched) / total, 3),
    }


def audit(root: Path, *, threshold: float = 0.62) -> dict[str, Any]:
    """一次完成体检 + 与全部行业包的覆盖度对比。"""
    docs = collect(root)
    mark_duplicates(docs, root)
    outlines = package_outlines()
    by_package = {
        name: coverage(docs, headings, threshold=threshold)
        for name, headings in outlines.items()
        if headings
    }
    total_chars = sum(doc.chars for doc in docs)
    return {
        "root": str(root),
        "files": len(docs),
        "total_chars": total_chars,
        "errors": [doc.as_dict() for doc in docs if doc.error],
        "duplicates": [
            {"path": doc.path, "duplicate_of": doc.duplicate_of}
            for doc in docs
            if doc.duplicate_of
        ],
        "suspicious": [
            {"path": doc.path, "markers": doc.suspicious}
            for doc in docs
            if doc.suspicious
        ],
        "headings": [
            {"path": doc.path, "chars": doc.chars, "headings": doc.headings}
            for doc in docs
        ],
        "packages": by_package,
    }


def render(report: dict[str, Any]) -> str:
    """把报告渲染成便于人读的文本。"""
    lines = [
        f"目录：{report['root']}",
        f"文件：{report['files']} 个，共 {report['total_chars']:,} 字符",
    ]
    if report["errors"]:
        lines.append(f"读取异常：{len(report['errors'])} 个")
    if report["duplicates"]:
        lines.append(f"重复文件：{len(report['duplicates'])} 个")
        for item in report["duplicates"][:10]:
            lines.append(f"  - {item['path']} ＝ {item['duplicate_of']}")
    if report["suspicious"]:
        lines.append(f"来源待复核：{len(report['suspicious'])} 个")
        for item in report["suspicious"][:10]:
            lines.append(f"  - {item['path']}：{'、'.join(item['markers'])}")
    lines.append("")
    lines.append("章节覆盖度（阈值内视为已有）：")
    for name, data in sorted(report["packages"].items(), key=lambda kv: -kv[1]["coverage"]):
        lines.append(
            f"  {name:<28} {data['coverage'] * 100:5.1f}%  "
            f"已有 {len(data['matched'])}/{data['total']}，缺 {len(data['missing'])}"
        )
    return "\n".join(lines)


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)
