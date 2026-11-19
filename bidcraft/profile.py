"""Project-level writing profile (global facts) shared by generation and checks.

The profile lives in ``project.meta["writing_profile"]``: it is deliberately a
plain dict so older projects keep working without a migration. Values are
trimmed and capped on write; ``load`` never raises on damaged data.
"""

from __future__ import annotations

from typing import Any

from .models import Project

META_KEY = "writing_profile"
TONES = ("稳健", "简洁", "技术", "亲和")
MAX_FORBIDDEN = 50
MAX_EXTRA_FACTS = 30
MAX_PHRASE = 40
MAX_EXTRA_KEY = 40
MAX_EXTRA_VALUE = 200

TEXT_LIMITS: tuple[tuple[str, int], ...] = (
    ("company_name", 80),
    ("legal_rep", 40),
    ("project_name", 120),
    ("owner_name", 120),
    ("bid_amount", 40),
    ("duration_days", 20),
    ("warranty_years", 20),
    ("quality_standard", 80),
)

LABELS: dict[str, str] = {
    "company_name": "公司名称",
    "legal_rep": "法定代表人",
    "project_name": "项目名称",
    "owner_name": "招标人",
    "bid_amount": "投标总价",
    "duration_days": "工期",
    "warranty_years": "质保期",
    "quality_standard": "质量标准",
    "tone": "语气",
}

# profile field -> textqa consistency key
ANCHOR_KEYS: dict[str, str] = {
    "project_name": "项目名称",
    "company_name": "投标人名称",
    "bid_amount": "总价",
    "duration_days": "工期",
    "warranty_years": "质保期",
}


class ProfileError(ValueError):
    """Invalid writing profile payload (中文提示给前端直接展示)。"""


def default_profile() -> dict[str, Any]:
    profile: dict[str, Any] = {key: "" for key, _ in TEXT_LIMITS}
    profile["tone"] = TONES[0]
    profile["forbidden_phrases"] = []
    profile["extra_facts"] = []
    return profile


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def normalize(payload: Any) -> dict[str, Any]:
    """Validate and trim a profile payload; raise :class:`ProfileError` on bad shapes."""

    if payload is None:
        return default_profile()
    if not isinstance(payload, dict):
        raise ProfileError("全局设定格式无效")
    profile = default_profile()
    for key, limit in TEXT_LIMITS:
        profile[key] = _text(payload.get(key), limit)

    tone = str(payload.get("tone") or TONES[0]).strip()
    if tone not in TONES:
        raise ProfileError("语气取值无效，应为：" + "、".join(TONES))
    profile["tone"] = tone

    phrases = payload.get("forbidden_phrases") or []
    if not isinstance(phrases, list):
        raise ProfileError("禁用表达必须是列表")
    cleaned: list[str] = []
    for item in phrases[:MAX_FORBIDDEN]:
        if not isinstance(item, str):
            raise ProfileError("禁用表达必须是文本")
        value = item.strip()[:MAX_PHRASE]
        if value and value not in cleaned:
            cleaned.append(value)
    profile["forbidden_phrases"] = cleaned

    extras = payload.get("extra_facts") or []
    if not isinstance(extras, list):
        raise ProfileError("自定义事实必须是列表")
    facts: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in extras[:MAX_EXTRA_FACTS]:
        if not isinstance(item, dict):
            raise ProfileError("自定义事实格式无效")
        key = _text(item.get("key"), MAX_EXTRA_KEY)
        value = _text(item.get("value"), MAX_EXTRA_VALUE)
        if not key or not value or key in seen:
            continue
        seen.add(key)
        facts.append({"key": key, "value": value})
    profile["extra_facts"] = facts
    return profile


def load(project: Project) -> dict[str, Any]:
    """Read the stored profile, falling back to defaults for damaged data."""

    meta = dict(project.meta or {})
    try:
        return normalize(meta.get(META_KEY))
    except ProfileError:
        return default_profile()


def save(project: Project, payload: Any) -> dict[str, Any]:
    profile = normalize(payload)
    meta = dict(project.meta or {})
    meta[META_KEY] = profile
    project.meta = meta
    return profile


def facts_block(profile: dict[str, Any]) -> str:
    """Render the non-empty facts as a bullet list for prompt injection."""

    lines: list[str] = []
    for key, label in LABELS.items():
        value = str(profile.get(key) or "").strip()
        if value:
            lines.append(f"- {label}：{value}")
    for item in profile.get("extra_facts") or []:
        key = str(item.get("key") or "").strip()
        value = str(item.get("value") or "").strip()
        if key and value:
            lines.append(f"- {key}：{value}")
    return "\n".join(lines)


def anchors(profile: dict[str, Any]) -> dict[str, str]:
    """Canonical values that checks can compare against."""

    result: dict[str, str] = {}
    for field, label in ANCHOR_KEYS.items():
        value = str(profile.get(field) or "").strip()
        if value:
            result[label] = value
    return result
