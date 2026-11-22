"""内置 7 类 Agent 的注册入口。

导入本包即完成注册；``registry.get_agent`` 在首次调用时也会懒加载这里，
因此 ``bidcraft.orchestration`` 未显式导入 agents 时同样可用。
"""

from __future__ import annotations

from ..registry import register_agent
from .commercial import CommercialAgent
from .compliance import ComplianceAgent
from .knowledge import KnowledgeAgent
from .reviewer import ReviewerAgent
from .technical import TechnicalAgent
from .tender import TenderAgent

_BUILTIN_AGENTS = (
    TenderAgent,
    TechnicalAgent,
    CommercialAgent,
    KnowledgeAgent,
    ComplianceAgent,
    ReviewerAgent,
)

for _agent in _BUILTIN_AGENTS:
    register_agent(_agent)

__all__ = [
    "CommercialAgent",
    "ComplianceAgent",
    "KnowledgeAgent",
    "ReviewerAgent",
    "TechnicalAgent",
    "TenderAgent",
]
