"""Image-model service for chapter illustrations.

Images are generated through the admin-configured default ``image`` model
(OpenAI-compatible ``/images/generations``), stored under
``BIDCRAFT_HOME/projects/<project_id>/images`` and referenced from the chapter
body as ``![alt](/projects/<project_id>/images/<filename>)``.

Only the configured image model is used; the service never calls a cloud
storage bucket. Generated and uploaded files are validated by magic bytes so
the stored extension always matches the real image type.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets
import struct
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select

from . import model_config, netguard
from .config import settings
from .generation.agent import ModelClient, ModelUnavailable
from .generation.writer import count_words
from .models import ImageAsset, ModelConfig, OutlineNode, Project, Section
from .security import decrypt_secret

TIMEOUT = 90.0
MAX_IMAGES = 60
MAX_GENERATED_BYTES = 12 * 1024 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 16_000_000
MIN_DIMENSION = 256
MAX_DIMENSION = 4096
CONTEXT_CHARS = 600
SANITIZE = re.compile(r'[\\/:*?"<>|\s]+')
SIZE_RE = re.compile(r"^(\d{2,4})\s*[x×*]\s*(\d{2,4})$", re.IGNORECASE)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"
MAX_SVG_BYTES = 200_000
SVG_FORBIDDEN_TAGS = re.compile(
    r"<\s*(?:script|foreignobject|iframe|image|use|animate|set|link|style)\b", re.IGNORECASE
)
SVG_FORBIDDEN_ATTRS = re.compile(r"\bon[a-z]+\s*=", re.IGNORECASE)
SVG_EXTERNAL_REF = re.compile(r"(?:href|src)\s*=\s*[\"']\s*(?:[a-z]+:)?//", re.IGNORECASE)
SVG_FENCE = re.compile(r"^\s*```(?:svg)?\s*|\s*```\s*$", re.IGNORECASE)

# Markdown export syntax: the renderer accepts safe relative URLs only.
MARKDOWN_TEMPLATE = "![{alt}]({url})"

SAFETY_CONSTRAINT = (
    "安全约束：画面仅用于工程投标技术示意，不出现真实公司名、人名、联系方式、"
    "二维码、证件、票据、logo 或可识别品牌，不含政治敏感内容与夸张营销元素。"
)

# Noise removed from section excerpts before they are folded into a prompt.
NOISE_PATTERNS = (
    re.compile(r"!\[[^\]]*\]\([^)]*\)"),
    re.compile(r"\[([^\]]*)\]\([^)]*\)"),
    re.compile(r"\{\{\s*图片占位\s*[:：][^}]*\}\}"),
    re.compile(r"\{\{[^}]*\}\}"),
    re.compile(r"<!--.*?-->", re.DOTALL),
    re.compile(r"```.*?```", re.DOTALL),
    re.compile(r"<[^>]+>"),
)

# Chapter body placeholder, e.g. ``{{图片占位:系统拓扑图}}``.
IMAGE_PLACEHOLDER_RE = re.compile(r"\{\{\s*图片占位\s*[:：]\s*([^}]*?)\s*\}\}")

# Already-inserted illustration line, used by the whole-document image budget.
EMBEDDED_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(/projects/[^)\s]+/images/[^)\s]+\)")

# Whole-document illustration policy stored in ``Project.meta["image_policy"]``.
DEFAULT_POLICY: dict = {
    "max_total": 12,
    "style": "",
    "size": "auto",
    "captions": True,
    "caption_prefix": "图",
    "width_cm": 15.0,
}
POLICY_KEYS = tuple(DEFAULT_POLICY)
MAX_POLICY_TOTAL = 60
MIN_POLICY_WIDTH_CM = 8.0
MAX_POLICY_WIDTH_CM = 19.0


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _clamp_policy(raw: dict | None) -> dict:
    """Merge a stored/partial policy onto defaults and clamp every field."""
    source = raw if isinstance(raw, dict) else {}
    max_total = _as_int(source.get("max_total", DEFAULT_POLICY["max_total"]), DEFAULT_POLICY["max_total"])
    style = str(source.get("style", DEFAULT_POLICY["style"]) or "").strip()[:80]
    size_raw = str(source.get("size", DEFAULT_POLICY["size"]) or "").strip()[:20]
    if size_raw.lower() == "auto":
        size = "auto"
    else:
        match = SIZE_RE.match(size_raw)
        size = f"{int(match.group(1))}x{int(match.group(2))}" if match else "auto"
    captions = _as_bool(source.get("captions", DEFAULT_POLICY["captions"]), True)
    caption_prefix = (
        str(source.get("caption_prefix", DEFAULT_POLICY["caption_prefix"]) or "").strip()[:10]
        or "图"
    )
    width_cm = _as_float(
        source.get("width_cm", DEFAULT_POLICY["width_cm"]), DEFAULT_POLICY["width_cm"]
    )
    return {
        "max_total": max(0, min(MAX_POLICY_TOTAL, max_total)),
        "style": style,
        "size": size,
        "captions": captions,
        "caption_prefix": caption_prefix,
        "width_cm": max(MIN_POLICY_WIDTH_CM, min(MAX_POLICY_WIDTH_CM, round(width_cm, 2))),
    }


def get_policy(project: Project) -> dict:
    """Return the effective whole-document image policy for a project."""
    meta = project.meta if isinstance(project.meta, dict) else {}
    return _clamp_policy(meta.get("image_policy"))


def set_policy(db, project: Project, payload: dict) -> dict:
    """Persist a partial policy; unknown keys are ignored and values clamped."""
    payload = payload if isinstance(payload, dict) else {}
    merged = dict(get_policy(project))
    for key in POLICY_KEYS:
        if key in payload:
            merged[key] = payload[key]
    policy = _clamp_policy(merged)
    meta = dict(project.meta or {})
    meta["image_policy"] = policy
    project.meta = meta
    db.flush()
    return policy


def count_embedded(db, project: Project) -> int:
    """Count Markdown image lines already inserted across the whole project."""
    sections = db.scalars(
        select(Section)
        .join(OutlineNode, Section.node_id == OutlineNode.id)
        .where(OutlineNode.project_id == project.id)
    )
    total = 0
    for section in sections:
        for line in (section.body or "").splitlines():
            if EMBEDDED_IMAGE_RE.search(line):
                total += 1
    return total


class ImageError(RuntimeError):
    """Base error for the image service."""


class ImageConfigError(ImageError):
    """The image model is missing or incomplete (maps to HTTP 503)."""


class ImageRequestError(ImageError):
    """The request itself is invalid (maps to HTTP 400)."""


class ImageTooLarge(ImageError):
    """An uploaded file exceeds the size limit (maps to HTTP 413)."""


@dataclass
class ImagePrompt:
    label: str
    heading: str
    project_name: str
    package_title: str
    context: str = ""
    style: str = ""
    user_prompt: str = ""


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def asset_payload(asset: ImageAsset, *, include_url: bool = True) -> dict:
    payload = {
        "id": asset.id,
        "project_id": asset.project_id,
        "node_id": asset.node_id,
        "label": asset.label,
        "prompt": asset.prompt,
        "status": asset.status,
        "filename": asset.filename,
        "size_bytes": asset.size_bytes,
        "provider": asset.provider,
        "model": asset.model,
        "last_error": asset.last_error,
        "kind": (asset.options or {}).get("kind", "bitmap"),
        "options": asset.options or {},
        "created_at": _iso(asset.created_at),
        "updated_at": _iso(asset.updated_at),
    }
    if include_url:
        payload["url"] = _url_for(asset)
    return payload


def _url_for(asset: ImageAsset) -> str:
    if not asset.filename:
        return ""
    return f"/projects/{asset.project_id}/images/{asset.filename}"


def image_dir(project_id: str) -> Path:
    path = settings.project_dir(project_id) / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_image_path(project_id: str, filename: str) -> Path | None:
    """Return the safe absolute path for a stored filename (None if unsafe)."""
    root = image_dir(project_id).resolve()
    candidate = (root / filename).resolve()
    if not candidate.is_file() or root not in candidate.parents:
        return None
    return candidate


def media_type_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".svg":
        return "image/svg+xml"
    return "image/png" if suffix == ".png" else "image/jpeg"


def image_kind(content: bytes) -> str:
    """Return the canonical extension for PNG/JPEG bytes, or ``""``."""
    if content.startswith(PNG_SIGNATURE):
        return ".png"
    if content.startswith(JPEG_SIGNATURE):
        return ".jpg"
    return ""


def sanitize_svg(raw: str) -> str:
    """Validate model-produced SVG and strip unsafe/out-of-scope constructs.

    Only self-contained vector markup is accepted: scripts, event handlers,
    external references and raster embeds are rejected rather than silently
    neutralised, so a failed guard can never become a delivered figure.
    """
    text = SVG_FENCE.sub("", (raw or "").strip()).strip()
    text = re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", text).strip()
    if not re.match(r"^\s*<svg\b", text, re.IGNORECASE) or not re.search(
        r"</svg>\s*$", text, re.IGNORECASE
    ):
        raise ImageError("模型未返回有效的 SVG 片段")
    if len(text.encode("utf-8")) > MAX_SVG_BYTES:
        raise ImageError("SVG 内容超过大小上限")
    if (
        SVG_FORBIDDEN_TAGS.search(text)
        or SVG_FORBIDDEN_ATTRS.search(text)
        or SVG_EXTERNAL_REF.search(text)
        or re.search(r"javascript\s*:", text, re.IGNORECASE)
        or re.search(r"<!doctype|<!\[cdata", text, re.IGNORECASE)
    ):
        raise ImageError("SVG 含脚本、外部引用或不支持的嵌入内容")
    if not re.search(r"\bviewBox\s*=", text, re.IGNORECASE):
        text = re.sub(r"<svg\b", '<svg viewBox="0 0 800 450"', text, count=1, flags=re.IGNORECASE)
    return text


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def placeholder_png_bytes() -> bytes:
    """Deterministic grey bitmap placeholder (pure stdlib, no provider call)."""
    width = height = 64
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes((207, 207, 207) * width)
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(row * height, 9))
        + _png_chunk(b"IEND", b"")
    )


def placeholder_png_path(project_id: str, label: str) -> Path:
    tag = hashlib.sha1((label or "placeholder").encode("utf-8")).hexdigest()[:8]
    path = image_dir(project_id) / f"placeholder-{tag}.png"
    if not path.is_file():
        path.write_bytes(placeholder_png_bytes())
    return path


def url_resolver(project_id: str):
    """DOCX resolver for markdown image URLs created by the API/CLI."""

    root = image_dir(project_id).resolve()

    def resolve(url: str) -> Path | None:
        match = re.search(r"/images/([^/?#]+)$", (url or "").strip())
        if not match:
            return None
        filename = match.group(1)
        if Path(filename).suffix.lower() == ".svg":
            # DOCX/PDF need a bitmap; an SVG is deliberately exported as a
            # visible placeholder rather than pretending it was rasterised.
            return placeholder_png_path(project_id, Path(filename).stem)
        candidate = (root / filename).resolve()
        if not candidate.is_file() or root not in candidate.parents:
            return None
        return candidate

    return resolve


def _clean_excerpt(text: str | None, limit: int = CONTEXT_CHARS) -> str:
    value = text or ""
    for pattern in NOISE_PATTERNS:
        value = pattern.sub(" ", value)
    value = re.sub(r"^\s{0,3}#{1,6}\s*", " ", value, flags=re.MULTILINE)
    value = re.sub(r"^\s*[-=]{3,}\s*$", " ", value, flags=re.MULTILINE)
    value = re.sub(r"[*_`>|]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        value = value[:limit].rstrip() + "…"
    return value


def build_prompt(info: ImagePrompt) -> str:
    label = (info.label or "配图").strip()
    style = (info.style or "").strip()
    user_prompt = (info.user_prompt or "").strip()
    parts: list[str] = []
    if user_prompt:
        parts.append(f"画面内容：{user_prompt}")
        parts.append(f"图名：{label}")
    else:
        parts.append(f"主题：{label}（{info.package_title or '弱电智能化'}）")
    parts.append(f"章节：{(info.heading or '').strip() or '投标文件章节'}")
    parts.append(f"项目：{info.project_name or '某智能化项目'}")
    if info.package_title:
        parts.append(f"专业包：{info.package_title}")
    if info.context:
        parts.append(f"章节摘要：{info.context}")
    if style:
        parts.append(f"画面风格：{style}")
    parts.append("要求：专业工程示意/流程风格，画面干净、信息清晰，不出现文字标签之外的乱码。")
    parts.append(SAFETY_CONSTRAINT)
    return "；".join(part for part in parts if part)


def normalize_size(value: str | None, default: str | None = None) -> str:
    """Normalize a requested size to ``auto`` or ``WxH``."""
    raw = (value or "").strip().lower() or (default or "").strip().lower()
    if not raw:
        return "1024x1024"
    if raw == "auto":
        return "auto"
    match = SIZE_RE.match(raw)
    if not match:
        raise ImageRequestError("图片尺寸不合法，应为 auto 或 WxH（如 1024x1024）")
    width, height = int(match.group(1)), int(match.group(2))
    if not (MIN_DIMENSION <= width <= MAX_DIMENSION and MIN_DIMENSION <= height <= MAX_DIMENSION):
        raise ImageRequestError("图片尺寸超出范围，宽高需在 256–4096 之间")
    if width * height > MAX_PIXELS:
        raise ImageRequestError("图片尺寸过大，像素总数不能超过 16MP")
    return f"{width}x{height}"


def _section_context(
    db, project_id: str, node_id: str, heading: str
) -> tuple[str, str]:
    """Resolve chapter heading/body context and enforce node ownership."""
    node_id = (node_id or "").strip()
    heading = (heading or "").strip()
    if node_id:
        node = db.scalar(
            select(OutlineNode).where(
                OutlineNode.id == node_id, OutlineNode.project_id == project_id
            )
        )
        if node is None:
            raise ImageRequestError("章节不存在或不属于当前项目")
        heading = node.heading or ""
        section = db.scalar(
            select(Section)
            .where(Section.node_id == node.id)
            .order_by(Section.updated_at.desc())
        )
        return heading, _clean_excerpt(section.body if section else "")
    if heading:
        section = db.scalar(
            select(Section)
            .join(OutlineNode, Section.node_id == OutlineNode.id)
            .where(OutlineNode.project_id == project_id, OutlineNode.heading == heading)
            .order_by(OutlineNode.position)
        )
        return heading, _clean_excerpt(section.body if section else "")
    return heading, ""


def image_batch_targets(plan: dict, include_failed: bool = False) -> list[dict]:
    """Return one generation target per unresolved placeholder label.

    A label can appear in several chapters; one image is enough, so the first
    occurrence supplies the prompt context and later occurrences are ignored.
    """
    targets: dict[str, dict] = {}
    for chapter in plan.get("chapters") or []:
        for item in chapter.get("placeholders") or []:
            status = str(item.get("status") or "")
            if status == "ready":
                continue
            if status == "failed" and not include_failed:
                continue
            label = (item.get("label") or "配图").strip() or "配图"
            if label in targets:
                continue
            targets[label] = {
                "label": label,
                "node_id": str(chapter.get("id") or ""),
                "heading": str(chapter.get("heading") or ""),
                "asset_id": str(item.get("asset_id") or ""),
                "status": status or "missing",
            }
    return list(targets.values())


def list_assets(db, project_id: str, node_id: str | None = None) -> list[ImageAsset]:
    statement = (
        select(ImageAsset)
        .where(ImageAsset.project_id == project_id)
        .order_by(ImageAsset.created_at.desc())
    )
    if node_id:
        statement = statement.where(ImageAsset.node_id == node_id)
    return list(db.scalars(statement))


def image_plan(db, project: Project) -> dict:
    """Summarize image placeholders across the whole outline.

    Each placeholder is matched by exact label against the newest ready asset in
    the project. Failed assets are reported separately so the UI can offer a
    retry without pretending the placeholder is covered.
    """
    assets = list_assets(db, project.id)
    ready_by_label: dict[str, ImageAsset] = {}
    failed_by_label: dict[str, ImageAsset] = {}
    for asset in assets:
        label = (asset.label or "配图").strip()
        if asset.status == "ready":
            ready_by_label.setdefault(label, asset)
        elif asset.status == "failed":
            failed_by_label.setdefault(label, asset)

    nodes = list(
        db.scalars(
            select(OutlineNode)
            .where(OutlineNode.project_id == project.id)
            .order_by(OutlineNode.position)
        )
    )
    sections = {
        row.node_id: row
        for row in db.scalars(
            select(Section).where(Section.node_id.in_([node.id for node in nodes] or [""]))
        )
    }

    chapters: list[dict] = []
    summary = {"placeholders": 0, "ready": 0, "failed": 0, "missing": 0}
    for node in nodes:
        section = sections.get(node.id)
        body = section.body if section else ""
        seen: dict[str, dict] = {}
        for match in IMAGE_PLACEHOLDER_RE.finditer(body or ""):
            token = match.group(0)
            label = (match.group(1) or "配图").strip() or "配图"
            item = seen.get(token)
            if item is None:
                ready = ready_by_label.get(label)
                failed = failed_by_label.get(label)
                status = "ready" if ready else "failed" if failed else "missing"
                item = {
                    "label": label,
                    "token": token,
                    "count": 0,
                    "status": status,
                    "asset_id": (ready or failed).id if (ready or failed) else None,
                    "url": _url_for(ready) if ready else "",
                    "last_error": (failed.last_error if failed else ""),
                }
                seen[token] = item
            item["count"] += 1

        if not seen:
            continue
        placeholders = list(seen.values())
        counts = {"ready": 0, "failed": 0, "missing": 0}
        for item in placeholders:
            counts[item["status"]] += item["count"]
        words = count_words(body or "")
        budget = max(
            1,
            min(
                settings.image_max_per_chapter,
                (words + settings.image_words_per_image - 1) // settings.image_words_per_image,
            ),
        )
        chapter = {
            "id": node.id,
            "heading": node.heading,
            "position": node.position,
            "placeholders": placeholders,
            "total": sum(counts.values()),
            "words": words,
            "budget": budget,
            "over_budget": sum(counts.values()) > budget,
            **counts,
        }
        chapters.append(chapter)
        summary["placeholders"] += chapter["total"]
        for key, value in counts.items():
            summary[key] += value

    policy = get_policy(project)
    used = count_embedded(db, project)
    max_total = int(policy["max_total"])
    document = {
        "used": used,
        "max_total": max_total,
        "remaining": (max(0, max_total - used) if max_total > 0 else None),
        "placeholders": summary["placeholders"],
        "ready": summary["ready"],
        "failed": summary["failed"],
        "missing": summary["missing"],
        "over_total": bool(max_total > 0 and used >= max_total),
    }
    return {
        "chapters": chapters,
        "summary": summary,
        "policy": policy,
        "document": document,
    }


def apply_image_plan(db, project: Project) -> dict:
    """Replace ready placeholders with Markdown, respecting per-chapter budget.

    Missing/failed placeholders are left untouched so the author can retry or
    write them manually. The budget is advisory but enforced here to avoid an
    accidental wall of images in one chapter.
    """
    plan = image_plan(db, project)
    assets = {
        asset.id: asset
        for asset in list_assets(db, project.id)
        if asset.status == "ready"
    }
    nodes = {
        node.id: node
        for node in db.scalars(
            select(OutlineNode).where(OutlineNode.project_id == project.id)
        )
    }
    sections = {
        section.node_id: section
        for section in db.scalars(
            select(Section).where(
                Section.node_id.in_(list(nodes) or [""])
            )
        )
    }

    applied = 0
    skipped_missing = 0
    skipped_failed = 0
    skipped_over_budget = 0
    skipped_over_total = 0
    applied_labels: list[str] = []
    chapters: list[str] = []
    policy = plan.get("policy") or get_policy(project)
    max_total = int(policy.get("max_total") or 0)
    document_used = count_embedded(db, project)
    for chapter in plan.get("chapters") or []:
        section = sections.get(chapter["id"])
        if section is None:
            continue
        body = section.body or ""
        used = 0
        changed = False
        for item in chapter.get("placeholders") or []:
            count = int(item.get("count") or 0)
            status = item.get("status")
            if status == "missing":
                skipped_missing += count
                continue
            if status != "ready":
                skipped_failed += count
                continue
            asset = assets.get(str(item.get("asset_id") or ""))
            token = str(item.get("token") or "")
            if asset is None or not item.get("url") or not token or token not in body:
                skipped_failed += count
                continue
            if used + count > int(chapter.get("budget") or 1):
                skipped_over_budget += count
                continue
            if max_total > 0 and document_used + applied + count > max_total:
                skipped_over_total += count
                continue
            markdown = markdown_for(asset, str(item.get("label") or ""))
            body = body.replace(token, f"\n{markdown}\n")
            body = re.sub(r"\n{3,}", "\n\n", body)
            used += count
            applied += count
            applied_labels.append(str(item.get("label") or "配图"))
            changed = True

        if changed:
            section.body = body
            section.words = count_words(body)
            section.status = "edited"
            node = nodes.get(chapter["id"])
            if node is not None:
                node.status = "edited"
            chapters.append(chapter["id"])

    db.flush()
    return {
        "applied": applied,
        "skipped_missing": skipped_missing,
        "skipped_failed": skipped_failed,
        "skipped_over_budget": skipped_over_budget,
        "skipped_over_total": skipped_over_total,
        "applied_labels": applied_labels,
        "chapters": chapters,
        "policy": get_policy(project),
    }


def get_asset(db, project_id: str, asset_id: str) -> ImageAsset | None:
    return db.scalar(
        select(ImageAsset).where(
            ImageAsset.project_id == project_id, ImageAsset.id == asset_id
        )
    )


def delete_asset(db, project_id: str, asset_id: str) -> bool:
    asset = get_asset(db, project_id, asset_id)
    if asset is None:
        return False
    path = resolve_image_path(project_id, asset.filename) if asset.filename else None
    if path is not None:
        path.unlink(missing_ok=True)
    db.delete(asset)
    return True


def markdown_for(asset: ImageAsset, alt: str = "") -> str:
    label = (alt or asset.label or "配图").strip()
    kind = (asset.options or {}).get("kind", "bitmap")
    if kind == "svg":
        label = f"{label}（SVG 图示，文档导出中以占位图呈现）"
    elif kind == "placeholder":
        label = f"{label}（占位图，未生成实际图片）"
    return MARKDOWN_TEMPLATE.format(alt=label, url=_url_for(asset))


def _image_config(db) -> ModelConfig:
    config = db.scalar(
        select(ModelConfig)
        .where(ModelConfig.kind == "image", ModelConfig.enabled.is_(True))
        .order_by(ModelConfig.is_default.desc(), ModelConfig.updated_at.desc())
    )
    if config is None:
        raise ImageConfigError("尚未配置可用的图片模型，请联系超级管理员")
    if not config.base_url or not config.model:
        raise ImageConfigError("图片模型配置缺少 Base URL 或模型名称")
    return config


def _config_default_size(config: ModelConfig) -> str:
    return str((config.options or {}).get("image_size") or "").strip()


def _project_package_title(project: Project) -> str:
    try:
        from .rules.loader import load_package

        return load_package(project.package).title
    except (ValueError, OSError):
        return ""


def _download(url: str) -> bytes:
    """Download a generated image with manual redirects and a hard byte cap.

    The first URL comes from the administrator-configured image provider, so it
    may legitimately point at a private service.  Every redirect target is
    untrusted: its scheme and resolved address are checked before the next
    request.  Bodies are streamed so an oversized response is aborted without
    being held in memory.
    """

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImageError("图片下载地址不合法，仅支持 http/https")
    current = url
    for hop in range(4):  # initial request + at most three redirects
        if hop:
            try:
                netguard.check_redirect_target(current)
            except netguard.NetworkError as exc:
                raise ImageError(str(exc)) from exc
        with httpx.stream(
            "GET", current, timeout=TIMEOUT, follow_redirects=False
        ) as response:
            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    raise ImageError("图片下载跳转缺少目标地址")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_GENERATED_BYTES:
                    raise ImageError("图片文件超过 12MB 限制")
                chunks.append(chunk)
            return b"".join(chunks)
    raise ImageError("图片下载跳转次数过多")


def _request_image(
    config: ModelConfig, *, prompt: str, size: str, quality: str
) -> tuple[bytes, str, str]:
    """Call the image endpoint and return validated bytes, extension, revised prompt."""
    from .model_config import api_base

    options = config.options or {}
    if "response_format" in options:
        response_format = str(options.get("response_format") or "").strip()
    else:
        response_format = "b64_json"
    body: dict = {"model": config.model, "prompt": prompt, "n": 1, "size": size}
    if quality:
        body["quality"] = quality
    if response_format:
        body["response_format"] = response_format

    headers = {"Content-Type": "application/json"}
    key = decrypt_secret(config.api_key_enc)
    if key:
        headers["Authorization"] = f"Bearer {key}"

    response = httpx.post(
        f"{api_base(config.base_url)}/images/generations",
        headers=headers,
        json=body,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise ImageError("图片接口返回了无效的 JSON") from exc
    if not isinstance(data, dict):
        raise ImageError("图片接口返回格式不正确")
    items = data.get("data") or []
    if not isinstance(items, list) or not items:
        raise ImageError("图片接口未返回数据")
    item = items[0]
    if not isinstance(item, dict):
        raise ImageError("图片接口返回格式不正确")
    revised = str(item.get("revised_prompt") or "").strip()

    if item.get("b64_json"):
        raw = str(item["b64_json"]).strip()
        if raw.startswith("data:"):
            _, _, raw = raw.partition(",")
        try:
            content = base64.b64decode(raw)
        except (binascii.Error, ValueError) as exc:
            raise ImageError("图片接口返回了无效的 base64 数据") from exc
    elif item.get("url"):
        content = _download(str(item["url"]))
    else:
        raise ImageError("图片接口既未返回 b64_json 也未返回 url")

    if not content:
        raise ImageError("图片接口返回空文件")
    if len(content) > MAX_GENERATED_BYTES:
        raise ImageError("图片文件超过 12MB 限制")
    kind = image_kind(content)
    if not kind:
        raise ImageError("图片接口返回的内容不是 PNG 或 JPEG")
    return content, kind, revised[:2000]


SVG_SYSTEM_PROMPT = (
    "你是工程技术标书配图助手。只输出一个安全、自包含的 SVG 片段，不要输出 Markdown、"
    "解释或代码块标记。viewBox 使用 0 0 800 450，使用浅色背景、清晰线条和中文标签；"
    "禁止 script、外部图片、外部链接、事件属性、foreignObject 和 data URI。"
    "这是技术示意图，不展示任何真实性凭证材料。"
)
_EVIDENCE_REQUEST_RE = re.compile(
    r"扫描件|截图|实拍|照片|公章|盖章|签字|签名|签署日期|证照|营业执照|资质证书|二维码|手写",
    re.IGNORECASE,
)


def _svg_safe_prompt(prompt: str) -> str:
    """把可能触发内容过滤的“证据类”请求改写为技术示意图口径。"""

    cleaned = _EVIDENCE_REQUEST_RE.sub("示意标注", str(prompt or ""))
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return f"{cleaned[:2600]}\n这是工程技术示意图，不展示任何真实性凭证材料。"


def _chat_svg_client(db):
    endpoint = model_config.resolve_chat_endpoint(db)
    if endpoint is None or not getattr(endpoint, "model_configured", False):
        return None, None
    client = ModelClient(endpoint)
    if not client.available:
        return None, None
    return client, endpoint


def generate_svg_figure(
    db,
    project: Project,
    *,
    label: str,
    node_id: str = "",
    heading: str = "",
    package_title: str = "",
    prompt: str = "",
    style: str = "",
) -> ImageAsset:
    """Generate a self-contained SVG figure through the configured chat model."""
    client, endpoint = _chat_svg_client(db)
    if client is None:
        raise ModelUnavailable("未配置可用的大模型，无法生成 SVG 图示")
    label = (label or "").strip() or "配图"
    package_title = (package_title or "").strip() or _project_package_title(project)
    heading_text, context = _section_context(db, project.id, node_id, heading)
    full_prompt = build_prompt(
        ImagePrompt(
            label=label,
            heading=heading_text,
            project_name=project.name,
            package_title=package_title,
            context=context,
            style=style,
            user_prompt=prompt,
        )
    )
    safe_prompt = _svg_safe_prompt(full_prompt)
    messages = [
        {"role": "system", "content": SVG_SYSTEM_PROMPT},
        {"role": "user", "content": f"{safe_prompt}\n请输出可直接渲染的 SVG 片段："},
    ]
    raw = client.complete(messages, max_tokens=4000, temperature=0)
    if not (raw or "").strip():
        # 有些模型会因招标正文里的公章/签字等措辞触发内容过滤而返回空内容；
        # 用只含图名和章节名的简化提示词重试一次，不重复携带长招标上下文。
        simple = _svg_safe_prompt(
            f"请生成「{heading_text or heading or label}」章节的技术示意图，图名：{label}。"
        )
        raw = client.complete(
            [
                {"role": "system", "content": SVG_SYSTEM_PROMPT},
                {"role": "user", "content": f"{simple}\n请只输出 SVG 片段："},
            ],
            max_tokens=4000,
            temperature=0,
        )
    svg = sanitize_svg(raw)
    filename = f"{_slug(label) or 'figure'}-{secrets.token_hex(3)}.svg"
    path = image_dir(project.id) / filename
    try:
        path.write_text(svg, encoding="utf-8")
    except OSError as exc:
        raise ImageError(f"SVG 写入失败：{exc}") from exc
    asset = ImageAsset(
        project_id=project.id,
        node_id=(node_id or "").strip(),
        label=label,
        prompt=full_prompt,
        status="ready",
        provider="llm-svg",
        model=str(getattr(endpoint, "model_name", "") or "chat-model"),
        last_error="",
        options={
            "source": "llm_svg",
            "kind": "svg",
            "document_export": "placeholder",
            "prompts": {"system": SVG_SYSTEM_PROMPT, "user": full_prompt},
        },
    )
    db.add(asset)
    db.flush()
    asset.file_path = str(path)
    asset.filename = filename
    asset.size_bytes = len(svg.encode("utf-8"))
    db.flush()
    return asset


def _placeholder_asset(db, project: Project, *, label: str, node_id: str, reason: str) -> ImageAsset:
    filename = f"placeholder-{secrets.token_hex(4)}.png"
    path = image_dir(project.id) / filename
    path.write_bytes(placeholder_png_bytes())
    asset = ImageAsset(
        project_id=project.id,
        node_id=(node_id or "").strip(),
        label=label,
        prompt="位图模型不可用，使用占位图",
        status="ready",
        provider="placeholder",
        model="",
        last_error=reason[:1000],
        options={"source": "placeholder", "kind": "placeholder"},
        file_path=str(path),
        filename=filename,
        size_bytes=path.stat().st_size,
    )
    db.add(asset)
    db.flush()
    return asset


def generate_image(
    db,
    project: Project,
    *,
    label: str,
    node_id: str = "",
    heading: str = "",
    package_title: str = "",
    prompt: str = "",
    style: str = "",
    size: str = "",
) -> ImageAsset:
    """Prefer an LLM-produced SVG; fall back to the configured bitmap model."""
    svg_error = ""
    try:
        return generate_svg_figure(
            db,
            project,
            label=label,
            node_id=node_id,
            heading=heading,
            package_title=package_title,
            prompt=prompt,
            style=style,
        )
    except (ModelUnavailable, ImageError, ValueError, OSError) as exc:
        svg_error = str(exc)[:600]

    try:
        config = _image_config(db)
    except ImageConfigError as exc:
        reason = f"SVG 生成失败：{svg_error}；位图模型未配置：{exc}" if svg_error else str(exc)
        return _placeholder_asset(
            db,
            project,
            label=(label or "").strip() or "配图",
            node_id=node_id,
            reason=reason,
        )
    label = (label or "").strip() or "配图"
    package_title = (package_title or "").strip() or _project_package_title(project)
    heading_text, context = _section_context(db, project.id, node_id, heading)
    style = (style or "").strip()[:80]
    user_prompt = (prompt or "").strip()[:2000]
    resolved_size = normalize_size(size, default=_config_default_size(config))
    quality = str((config.options or {}).get("image_quality") or "").strip()

    full_prompt = build_prompt(
        ImagePrompt(
            label=label,
            heading=heading_text,
            project_name=project.name,
            package_title=package_title,
            context=context,
            style=style,
            user_prompt=user_prompt,
        )
    )
    options: dict = {"source": "generated", "size": resolved_size}
    if style:
        options["style"] = style
    if user_prompt:
        options["user_prompt"] = user_prompt
    if quality:
        options["quality"] = quality

    asset = ImageAsset(
        project_id=project.id,
        node_id=(node_id or "").strip(),
        label=label,
        prompt=full_prompt,
        status="failed",
        provider=config.provider or "openai",
        model=config.model,
        last_error="",
        options=dict(options),
    )
    db.add(asset)
    db.flush()

    try:
        content, kind, revised = _request_image(
            config, prompt=full_prompt, size=resolved_size, quality=quality
        )
    except (httpx.HTTPError, ImageError, ValueError) as exc:
        asset.status = "failed"
        asset.last_error = str(exc)[:1000]
        db.flush()
        return asset

    filename = f"{_slug(label) or 'image'}-{secrets.token_hex(3)}{kind}"
    path = image_dir(project.id) / filename
    try:
        path.write_bytes(content)
    except OSError as exc:
        asset.status = "failed"
        asset.last_error = f"图片写入失败：{exc}"[:1000]
        db.flush()
        return asset

    asset.status = "ready"
    asset.file_path = str(path)
    asset.filename = filename
    asset.size_bytes = len(content)
    asset.last_error = ""
    if revised:
        asset.options = {**options, "revised_prompt": revised}
    db.flush()
    return asset


def regenerate_image(
    db, project: Project, asset: ImageAsset, *, style: str = "", size: str = ""
) -> ImageAsset:
    """Generate a new asset reusing the original request options.

    ``style``/``size`` act as fallbacks for legacy assets that were created
    before those options were stored (batch jobs pass the project policy).
    """
    options = asset.options or {}
    return generate_image(
        db,
        project,
        label=asset.label or "配图",
        node_id=asset.node_id or "",
        prompt=str(options.get("user_prompt") or ""),
        style=str(options.get("style") or style or ""),
        size=str(options.get("size") or size or ""),
    )


def upload_image(
    db,
    project: Project,
    *,
    label: str,
    filename: str,
    content: bytes,
    node_id: str = "",
) -> ImageAsset:
    """Store a user-uploaded PNG/JPEG inside the project image directory."""
    label = (label or "").strip()
    if not label:
        raise ImageRequestError("图片名称不能为空")
    label = label[:80]
    if len(content) > MAX_UPLOAD_BYTES:
        raise ImageTooLarge("图片不能超过 10MB")
    if not content:
        raise ImageRequestError("图片文件为空")
    kind = image_kind(content)
    if not kind:
        raise ImageRequestError("仅支持 PNG 或 JPEG 图片")
    _section_context(db, project.id, node_id, "")

    stored = f"{_slug(label) or 'image'}-{secrets.token_hex(3)}{kind}"
    path = image_dir(project.id) / stored
    try:
        path.write_bytes(content)
    except OSError as exc:
        raise ImageError(f"图片写入失败：{exc}") from exc

    asset = ImageAsset(
        project_id=project.id,
        node_id=(node_id or "").strip(),
        label=label,
        prompt="",
        status="ready",
        file_path=str(path),
        filename=stored,
        size_bytes=len(content),
        provider="upload",
        model="",
        last_error="",
        options={
            "source": "upload",
            "original_name": Path(filename or "").name[:255],
        },
    )
    db.add(asset)
    db.flush()
    return asset


def _slug(label: str) -> str:
    return SANITIZE.sub("-", label.strip()).strip("-")[:80]
