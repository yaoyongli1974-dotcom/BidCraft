"""Export presets: typography and layout for DOCX delivery.

Each preset is a plain dict so it can be stored in ``project.meta`` and passed
straight to :func:`bidcraft.export.docx.export_docx`. Presets only change
presentation (fonts, sizes, spacing, margins, cover/TOC/page numbers); they
never change content.
"""

from __future__ import annotations

from typing import Any

SETTING_KEY = "export_template"
DEFAULT_ID = "standard"
BODY_SIZES = (9.0, 18.0)
LINE_SPACINGS = (1.0, 2.5)
MARGIN_RANGE = (1.0, 4.0)

PRESETS: dict[str, dict[str, Any]] = {
    "standard": {
        "id": "standard",
        "name": "标准投标文件",
        "description": "宋体小四、1.5 倍行距、黑体标题，通用格式",
        "body_font": "宋体",
        "heading_font": "黑体",
        "body_size": 12.0,
        "line_spacing": 1.5,
        "cover": True,
        "toc": True,
        "page_numbers": True,
        "margins": [2.54, 2.54, 2.8, 2.6],
    },
    "gov": {
        "id": "gov",
        "name": "政府采购（公文风格）",
        "description": "仿宋、三号字、较宽行距，接近公文与政采评审习惯",
        "body_font": "仿宋_GB2312",
        "heading_font": "黑体",
        "body_size": 16.0,
        "line_spacing": 1.75,
        "cover": True,
        "toc": True,
        "page_numbers": True,
        "margins": [3.0, 3.0, 2.8, 2.6],
    },
    "tech": {
        "id": "tech",
        "name": "技术方案（图多表多）",
        "description": "雅黑、11 磅、1.4 倍行距，紧凑易读，适合图文混排",
        "body_font": "微软雅黑",
        "heading_font": "微软雅黑",
        "body_size": 11.0,
        "line_spacing": 1.4,
        "cover": True,
        "toc": True,
        "page_numbers": True,
        "margins": [2.2, 2.2, 2.4, 2.4],
    },
    "compact": {
        "id": "compact",
        "name": "商务简洁（省页）",
        "description": "宋体 10.5 磅、1.25 倍行距，适合页数受限的标书",
        "body_font": "宋体",
        "heading_font": "黑体",
        "body_size": 10.5,
        "line_spacing": 1.25,
        "cover": False,
        "toc": True,
        "page_numbers": True,
        "margins": [2.0, 2.0, 2.2, 2.2],
    },
}

_TEXT_FIELDS = ("body_font", "heading_font")


def preset(template_id: str) -> dict[str, Any] | None:
    return PRESETS.get(template_id)


def preset_list() -> list[dict[str, Any]]:
    """Lightweight list for the UI (no margins tuple)."""
    return [
        {key: value for key, value in item.items() if key != "margins"}
        for item in PRESETS.values()
    ]


def normalize(raw: Any) -> dict[str, Any]:
    """Coerce arbitrary stored data into a usable preset (fallback: standard)."""
    base = dict(PRESETS[DEFAULT_ID])
    if not isinstance(raw, dict):
        return base
    chosen = PRESETS.get(str(raw.get("id") or ""))
    if chosen is not None:
        base = dict(chosen)
    for field in _TEXT_FIELDS:
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            base[field] = value.strip()[:40]
    size = raw.get("body_size")
    if isinstance(size, (int, float)) and not isinstance(size, bool):
        base["body_size"] = round(min(max(float(size), BODY_SIZES[0]), BODY_SIZES[1]), 2)
    spacing = raw.get("line_spacing")
    if isinstance(spacing, (int, float)) and not isinstance(spacing, bool):
        base["line_spacing"] = round(
            min(max(float(spacing), LINE_SPACINGS[0]), LINE_SPACINGS[1]), 2
        )
    for flag in ("cover", "toc", "page_numbers"):
        if isinstance(raw.get(flag), bool):
            base[flag] = raw[flag]
    margins = raw.get("margins")
    if isinstance(margins, (list, tuple)) and len(margins) == 4:
        cleaned: list[float] = []
        for value in margins:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                cleaned = []
                break
            cleaned.append(round(min(max(float(value), MARGIN_RANGE[0]), MARGIN_RANGE[1]), 2))
        if cleaned:
            base["margins"] = cleaned
    return base


def load(project: Any) -> dict[str, Any]:
    """Read the preset stored on a project (tolerates missing/broken meta)."""
    meta = getattr(project, "meta", None) or {}
    raw = meta.get(SETTING_KEY)
    if isinstance(raw, str):
        chosen = PRESETS.get(raw)
        return dict(chosen) if chosen is not None else dict(PRESETS[DEFAULT_ID])
    return normalize(raw)


def save(project: Any, template_id: str) -> dict[str, Any]:
    chosen = preset(template_id)
    if chosen is None:
        raise ValueError(f"未知导出模板：{template_id}")
    meta = dict(getattr(project, "meta", None) or {})
    meta[SETTING_KEY] = template_id
    project.meta = meta
    return dict(chosen)
