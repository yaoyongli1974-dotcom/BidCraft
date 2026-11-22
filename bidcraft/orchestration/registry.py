"""Agent 统一接口、输出契约与注册表。

Agent 的唯一职责是：校验输入 → 构建上下文 → 调用既有业务 Service → 返回
结构化结果。**不得**在 Agent 内直接写 ORM 表来绕过业务 Service。工具同理：
这里只薄包装 :mod:`bidcraft.mcp` 的工具实现（沙箱 / allowlist / 审计不重写）。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..models import Project

logger = logging.getLogger("bidcraft.orchestration.registry")

#: 允许的 Agent 类型（与 planner 的 DAG 节点一致）。
AGENT_TYPES = (
    "tender",
    "planning",
    "technical",
    "commercial",
    "knowledge",
    "compliance",
    "reviewer",
)

#: 人工审批类型。
APPROVAL_TYPES = (
    "technical",
    "commercial",
    "asset",
    "price",
    "compliance",
    "delivery",
)

AgentStatus = Literal["ok", "failed", "skipped", "requires_approval"]


class AgentError(RuntimeError):
    """Agent 输入/输出契约错误。"""


class AgentOutput(BaseModel):
    """所有 Agent 的唯一输出契约。

    ``requires_approval`` 为真时，orchestrator 会在该任务后挂起运行，等待
    人工审批；下游任务不会提前执行。``status='failed'`` 必须同时把原因写入
    ``result['error']``，不得伪装成功。
    """

    status: AgentStatus = "ok"
    agent_type: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    evidence: Any = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    approval_type: str = ""

    def with_defaults(self) -> AgentOutput:
        if self.status == "failed" and not self.result.get("error"):
            self.result = {**self.result, "error": "Agent 执行失败"}
        return self


class AgentConfig(BaseModel):
    """可校验的 Agent 配置（注册时与 API 展示共用）。"""

    agent_type: str
    display_name: str = ""
    description: str = ""
    requires_approval: bool = False
    approval_type: str = ""


class BaseAgent:
    """Agent 统一接口。

    子类实现 :meth:`execute`；其余步骤可按需覆盖。``run`` 是 orchestrator 的
    唯一调用入口，统一做输入校验、上下文构建和输出校验。
    """

    agent_type: str = ""
    display_name: str = ""
    description: str = ""
    requires_approval: bool = False
    approval_type: str = ""

    # ------------------------------------------------------------------ input
    def validate_input(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise AgentError(f"{self.agent_type or 'agent'} 输入必须是对象")
        return dict(payload)

    def build_context(
        self,
        db: Session,
        project: Project,
        payload: dict[str, Any],
        *,
        upstream: dict[str, Any] | None = None,
        run: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "payload": payload,
            "upstream": upstream or {},
            "run": run or {},
            "project_id": project.id,
        }

    # ----------------------------------------------------------------- execute
    def execute(
        self, db: Session, project: Project, context: dict[str, Any]
    ) -> AgentOutput | dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    # ------------------------------------------------------------------ output
    def validate_output(self, output: AgentOutput | dict[str, Any]) -> AgentOutput:
        if isinstance(output, AgentOutput):
            validated = output
        elif isinstance(output, dict):
            validated = AgentOutput(**output)
        else:
            raise AgentError(f"{self.agent_type or 'agent'} 输出必须是对象")
        if not validated.agent_type:
            validated.agent_type = self.agent_type
        elif validated.agent_type != self.agent_type:
            raise AgentError(
                f"Agent 输出类型不匹配：期望 {self.agent_type}，实际 {validated.agent_type}"
            )
        if validated.requires_approval and not validated.approval_type:
            validated.approval_type = self.approval_type or "delivery"
        return validated.with_defaults()

    # --------------------------------------------------------------------- run
    def run(
        self,
        db: Session,
        project: Project,
        payload: dict[str, Any] | None = None,
        *,
        upstream: dict[str, Any] | None = None,
        run: dict[str, Any] | None = None,
    ) -> AgentOutput:
        cleaned = self.validate_input(payload)
        context = self.build_context(db, project, cleaned, upstream=upstream, run=run)
        output = self.execute(db, project, context)
        return self.validate_output(output)

    # ------------------------------------------------------------------ config
    def config(self) -> dict[str, Any]:
        return AgentConfig(
            agent_type=self.agent_type,
            display_name=self.display_name or self.agent_type,
            description=self.description,
            requires_approval=self.requires_approval,
            approval_type=self.approval_type,
        ).model_dump()


_AGENTS: dict[str, BaseAgent] = {}


def register_agent(agent: BaseAgent | type[BaseAgent] | None = None) -> Any:
    """Register an agent instance or class.

    Usable directly (``register_agent(MyAgent())``) or as a decorator
    (``@register_agent``).  Re-registering the same type replaces the instance,
    which keeps test stubs isolated without leaking between tests.
    """

    def _register(target: BaseAgent | type[BaseAgent]) -> Any:
        instance = target() if isinstance(target, type) else target
        if not isinstance(instance, BaseAgent):
            raise AgentError("注册对象必须继承 BaseAgent")
        if not instance.agent_type:
            raise AgentError("Agent 必须声明 agent_type")
        if instance.agent_type not in AGENT_TYPES and not instance.agent_type.startswith("stub"):
            # Custom agents are allowed (tests / extensions), but built-in names
            # are reserved so a typo cannot silently shadow a real agent.
            logger.debug("注册自定义 Agent 类型 %s", instance.agent_type)
        _AGENTS[instance.agent_type] = instance
        return target if isinstance(target, type) else instance

    if agent is None:
        return _register
    return _register(agent)


def _ensure_builtins() -> None:
    if not _AGENTS:
        try:
            from . import agents  # noqa: F401 - import registers built-ins
        except Exception:  # noqa: BLE001 - registry stays usable without agents
            logger.debug("内置 Agent 导入失败", exc_info=True)


def get_agent(agent_type: str) -> BaseAgent:
    _ensure_builtins()
    agent = _AGENTS.get(str(agent_type or ""))
    if agent is None:
        raise LookupError(f"未知 Agent 类型: {agent_type}")
    return agent


def list_agents() -> list[dict[str, Any]]:
    _ensure_builtins()
    return [agent.config() for agent in sorted(_AGENTS.values(), key=lambda a: a.agent_type)]


def validate_agent_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Validate/normalize an agent config payload; raises ``AgentError``."""

    if config is None:
        raise AgentError("Agent 配置不能为空")
    if not isinstance(config, dict):
        raise AgentError("Agent 配置必须是对象")
    agent_type = str(config.get("agent_type") or "").strip()
    if not agent_type:
        raise AgentError("缺少 agent_type")
    _ensure_builtins()
    if agent_type not in _AGENTS:
        raise AgentError(f"未知 Agent 类型: {agent_type}")
    approval_type = str(config.get("approval_type") or "").strip()
    if approval_type and approval_type not in APPROVAL_TYPES:
        raise AgentError(f"未知审批类型: {approval_type}")
    return {
        **config,
        "agent_type": agent_type,
        "approval_type": approval_type,
    }


# --------------------------------------------------------------------------- #
# 工具注册表：薄包装 mcp / plugins，不重写沙箱、allowlist 与审计
# --------------------------------------------------------------------------- #
def list_tools() -> list[dict[str, Any]]:
    """Return built-in MCP tools plus enabled plugin tools."""

    from .. import mcp

    try:
        plugin_tools = mcp._plugin_tools()
    except Exception:  # noqa: BLE001 - plugin discovery must not break listing
        plugin_tools = []
    return [*mcp.TOOL_SPECS, *plugin_tools]


def call_tool(name: str, arguments: dict[str, Any] | None = None, *, readonly: bool = True) -> Any:
    """Invoke a built-in or plugin tool through the shared MCP implementation."""

    from .. import mcp

    args = dict(arguments or {})
    target = mcp._plugin_target(name)
    if target is not None:
        plugin, tool_name = target
        return mcp._call_plugin_tool(plugin, tool_name, args, readonly=readonly)
    return mcp._call_tool(name, args, readonly=readonly)


__all__ = [
    "AGENT_TYPES",
    "APPROVAL_TYPES",
    "AgentConfig",
    "AgentError",
    "AgentOutput",
    "BaseAgent",
    "call_tool",
    "get_agent",
    "list_agents",
    "list_tools",
    "register_agent",
    "validate_agent_config",
]
