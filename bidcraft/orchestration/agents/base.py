"""Agent 公共辅助：只做数据整形，不写业务表。"""

from __future__ import annotations

from typing import Any

from ...models import Project
from ..registry import AgentError, AgentOutput, BaseAgent

__all__ = ["AgentError", "AgentOutput", "BaseAgent", "excerpt", "project_brief"]


def project_brief(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "package": project.package,
        "status": project.status,
        "buyer": project.buyer or "",
    }


def excerpt(text: str | None, limit: int = 300) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "…"
