"""图片查重：字节哈希 + 可选感知哈希（Pillow 缺失时自动降级）。

Pillow 不是核心依赖，因此这里绝不硬依赖：

* 有 Pillow：dHash（8x8）识别缩放/轻微压缩后的相同图片；
* 无 Pillow：只比较 sha256，缩放图不会被判为相同，并在结果中标记
  ``perceptual_available=False``。

输出一律是「候选 + 相似度」，不直接认定抄袭。
"""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..images import resolve_image_path
from ..models import ImageAsset, OutlineNode, Section

DEFAULT_THRESHOLD = 0.9
DEFAULT_MAX_HITS = 500

_IMAGE_MD_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
_IMAGE_PATH_RE = re.compile(r"/images/([^/?#)\s]+)")


def perceptual_available() -> bool:
    """当前环境是否安装了 Pillow。"""

    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def _read_bytes(source: Any) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if hasattr(source, "read"):
        data = source.read()
        return bytes(data) if isinstance(data, (bytes, bytearray)) else b""
    if isinstance(source, (str, Path)):
        try:
            return Path(source).read_bytes()
        except OSError:
            return b""
    return b""


def calculate_image_hash(source: Any) -> str:
    """返回图片字节的 sha256；无法读取时返回空字符串（不抛错）。"""

    data = _read_bytes(source)
    if not data:
        return ""
    return hashlib.sha256(data).hexdigest()


def calculate_perceptual_hash(source: Any, *, hash_size: int = 8) -> str:
    """dHash 感知哈希；Pillow 不可用或解码失败时返回空字符串。

    先把图片灰度化并缩放到 ``(hash_size + 1, hash_size)``，再逐行比较相邻
    像素亮度，得到 ``hash_size ** 2`` 位。缩放、JPEG 重压缩通常仍保持稳定。
    """

    try:
        from PIL import Image
    except ImportError:
        return ""
    data = _read_bytes(source)
    if not data:
        return ""
    try:
        with Image.open(io.BytesIO(data)) as image:
            gray = image.convert("L").resize((hash_size + 1, hash_size))
            pixels = gray.tobytes()
    except Exception:  # noqa: BLE001 - 损坏图片不应中断整批查重
        return ""
    value = 0
    for row in range(hash_size):
        offset = row * (hash_size + 1)
        for col in range(hash_size):
            left = pixels[offset + col]
            right = pixels[offset + col + 1]
            value = (value << 1) | (1 if left > right else 0)
    width = (hash_size * hash_size + 3) // 4
    return f"{value:0{width}x}"


def _hamming_similarity(left: str, right: str) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    try:
        distance = (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None
    bits = len(left) * 4
    return max(0.0, 1.0 - distance / bits)


def compare_image_hashes(
    images_a: list[dict[str, Any]],
    images_b: list[dict[str, Any]] | None = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[dict[str, Any]]:
    """比较两组图片的哈希；返回 ``[{"i", "j", "similarity", "method"}]``。

    完全相同的字节给 1.0；否则用感知哈希的 Hamming 相似度。没有感知哈希
    （未装 Pillow）时只有完全相同字节会命中。
    """

    left = list(images_a or [])
    same_set = images_b is None
    right = left if same_set else list(images_b or [])
    pairs: list[dict[str, Any]] = []
    for i, item_a in enumerate(left):
        start = i + 1 if same_set else 0
        for j in range(start, len(right)):
            item_b = right[j]
            if same_set and (
                i == j
                or (item_a.get("id") and item_a.get("id") == item_b.get("id"))
                or (item_a.get("path") and item_a.get("path") == item_b.get("path"))
            ):
                continue
            method = ""
            score: float | None = None
            sha_a = str(item_a.get("sha256") or "")
            sha_b = str(item_b.get("sha256") or "")
            if sha_a and sha_a == sha_b:
                score, method = 1.0, "sha256"
            else:
                score = _hamming_similarity(
                    str(item_a.get("phash") or ""), str(item_b.get("phash") or "")
                )
                method = "perceptual"
            if score is None or score + 1e-12 < threshold:
                continue
            pairs.append(
                {"i": i, "j": j, "similarity": round(min(1.0, max(0.0, score)), 4), "method": method}
            )
    pairs.sort(key=lambda item: (-item["similarity"], item["i"], item["j"]))
    return pairs


def _image_entry(
    *,
    asset_id: str,
    filename: str,
    path: Path,
    source: str,
    loc: str,
) -> dict[str, Any]:
    data = _read_bytes(path)
    return {
        "id": asset_id,
        "filename": filename,
        "path": str(path),
        "source": source[:500],
        "loc": loc[:120],
        "sha256": calculate_image_hash(data),
        "phash": calculate_perceptual_hash(data),
        "size": len(data),
    }


def extract_document_images(db, project_id: str) -> list[dict[str, Any]]:
    """收集项目配图（ImageAsset）与正文 Markdown 引用，计算哈希（只读）。

    同一路径只保留首次出现；无法解析的图片不会进入结果（由调用方统计）。
    """

    out: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    assets = db.scalars(
        select(ImageAsset)
        .where(ImageAsset.project_id == project_id)
        .order_by(ImageAsset.created_at, ImageAsset.id)
    ).all()
    for asset in assets:
        filename = asset.filename or ""
        path = resolve_image_path(project_id, filename) if filename else None
        if path is None or str(path) in seen_paths:
            continue
        seen_paths.add(str(path))
        label = asset.label or filename
        out.append(
            _image_entry(
                asset_id=asset.id,
                filename=filename,
                path=path,
                source=f"配图：{label}",
                loc=f"配图 {filename}（章节 {asset.node_id or '未关联'}）",
            )
        )

    nodes = db.scalars(
        select(OutlineNode)
        .where(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.position, OutlineNode.created_at)
    ).all()
    headings = {node.id: node.heading or "" for node in nodes}
    sections = (
        db.scalars(select(Section).where(Section.node_id.in_(list(headings)))).all()
        if headings
        else []
    )
    for section in sections:
        heading = headings.get(section.node_id, "")
        body = section.body or ""
        for line_no, line in enumerate(body.splitlines(), start=1):
            refs = [match.group(1) for match in _IMAGE_MD_RE.finditer(line)]
            refs += [match.group(1) for match in _IMAGE_PATH_RE.finditer(line)]
            for ref in refs:
                filename = Path(ref.split("?")[0]).name
                path = resolve_image_path(project_id, filename)
                if path is None or str(path) in seen_paths:
                    continue
                seen_paths.add(str(path))
                out.append(
                    _image_entry(
                        asset_id="",
                        filename=filename,
                        path=path,
                        source=f"正文配图 / {heading}",
                        loc=f"{heading} 第{line_no}行配图 {filename}",
                    )
                )
    return out


def find_image_duplicates(
    db,
    project_id: str,
    *,
    source_project_id: str | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    max_hits: int = DEFAULT_MAX_HITS,
) -> dict[str, Any]:
    """查找相同/近似图片，返回候选命中（不认定抄袭）。"""

    from ..models import Project

    project = db.get(Project, project_id)
    project_name = project.name if project is not None else project_id
    left = extract_document_images(db, project_id)
    same_set = not source_project_id or source_project_id == project_id
    right = (
        left
        if same_set
        else extract_document_images(db, str(source_project_id))
    )
    source_name = project_name
    if not same_set:
        source = db.get(Project, str(source_project_id))
        source_name = source.name if source is not None else str(source_project_id)

    skipped: list[dict[str, Any]] = []
    if not left:
        skipped.append({"mode": "image", "path": project_id, "reason": "项目没有可查重图片"})
    if not same_set and not right:
        skipped.append(
            {"mode": "image", "path": source_project_id or "", "reason": "来源项目没有可查重图片"}
        )

    hits: list[dict[str, Any]] = []
    for pair in compare_image_hashes(left, right if not same_set else None, threshold=threshold):
        item_a, item_b = left[pair["i"]], right[pair["j"]]
        hits.append(
            {
                "kind": "image",
                "source_a": item_a["source"][:500],
                "source_b": item_b["source"][:500],
                "loc_a": item_a["loc"][:120],
                "loc_b": item_b["loc"][:120],
                "excerpt_a": item_a["filename"][:120],
                "excerpt_b": item_b["filename"][:120],
                "similarity": pair["similarity"],
                "evidence": {
                    "method": pair["method"],
                    "sha256_a": item_a["sha256"],
                    "sha256_b": item_b["sha256"],
                    "phash_a": item_a["phash"],
                    "phash_b": item_b["phash"],
                    "perceptual_available": perceptual_available(),
                    "project_a": project_name,
                    "project_b": source_name,
                    "advisory": True,
                    "note": "图片相似仅为候选，不直接认定抄袭，请人工复核来源与授权。",
                },
            }
        )
    hits = hits[:max_hits]
    stats = {
        "mode": "image",
        "threshold": threshold,
        "assets": len(left) + (0 if same_set else len(right)),
        "hits": len(hits),
        "perceptual_available": perceptual_available(),
        "advisory": True,
        "cross_project": not same_set,
    }
    return {"hits": hits, "stats": stats, "skipped": skipped}
