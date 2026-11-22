"""AI Agent 编排系统（对标易标第三批 Lane D）。

模块边界：

* :mod:`~bidcraft.orchestration.registry` —— Agent 统一契约与注册表；
* :mod:`~bidcraft.orchestration.planner` —— 工作流 DAG 规划与校验；
* :mod:`~bidcraft.orchestration.orchestrator` —— 运行/派发/重试/取消/恢复；
* :mod:`~bidcraft.orchestration.approval` —— 人工审批闸门。

所有 Agent 只通过既有业务 Service 访问数据，不绕过事实台账、评分、清单、
合规核对等唯一口径。工作流状态完全落在 ``workflow_runs`` /
``workflow_tasks`` / ``workflow_approvals`` 表，审批挂起不占 Job 租约。
"""

from . import agents  # noqa: F401 - 导入即注册内置 Agent
from .approval import (
    approve_task,
    reject_task,
    request_approval,
    resume_after_approval,
)
from .orchestrator import (
    cancel_workflow,
    dispatch_task,
    execute_agent_task,
    get_workflow_status,
    resume_workflow,
    retry_agent_task,
    run_workflow,
    start_workflow,
)
from .planner import (
    build_task_dependencies,
    build_workflow,
    validate_workflow_graph,
)
from .registry import (
    AgentOutput,
    BaseAgent,
    get_agent,
    list_agents,
    register_agent,
    validate_agent_config,
)

__all__ = [
    "AgentOutput",
    "BaseAgent",
    "approve_task",
    "build_task_dependencies",
    "build_workflow",
    "cancel_workflow",
    "dispatch_task",
    "execute_agent_task",
    "get_agent",
    "get_workflow_status",
    "list_agents",
    "register_agent",
    "reject_task",
    "request_approval",
    "resume_after_approval",
    "resume_workflow",
    "retry_agent_task",
    "run_workflow",
    "start_workflow",
    "validate_agent_config",
    "validate_workflow_graph",
]
