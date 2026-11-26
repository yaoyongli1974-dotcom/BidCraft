"""工作流 DAG 规划与图校验。

标准工作流（对标易标第三批）：

``招标解析 → 评分规划 → [企业资料匹配 | 清单分析]（并行）
→ 技术标 → 商务标 → 合规检查 → 人工审批 → 交付``

任务只声明 ``depends_on``；调度器按依赖是否全部完成决定可执行任务，因此
并行分支不需要线程也能保证「下游必须等两个分支都完成」。

``full`` / ``bid`` 是 ``standard`` 的兼容别名（见 :data:`WORKFLOW_ALIASES`）：
不会生成第二套 DAG，但会在任务 input 上写入 ``_workflow_alias`` 元数据并记
debug 日志，让请求种类在审计/排障中可辨认。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .. import scoring
from ..models import Project
from .registry import AgentOutput, BaseAgent, register_agent

logger = logging.getLogger("bidcraft.orchestration.planner")

#: 历史/兼容别名：``full`` 与 ``bid`` 不是独立 DAG，而是 ``standard`` 的别名。
#: 任务键与依赖完全一致，但每个任务会带上 ``_workflow_alias`` 元数据，
#: API/日志/审计可明确区分用户实际请求的种类，避免「看起来有三种工作流」。
WORKFLOW_ALIASES = {"full": "standard", "bid": "standard"}

CANONICAL_WORKFLOWS = frozenset({"standard"})


def canonical_workflow_type(workflow_type: str) -> str:
    """Resolve compatibility aliases to the canonical workflow type."""

    kind = str(workflow_type or "standard")
    return WORKFLOW_ALIASES.get(kind, kind)


class PlanError(ValueError):
    """工作流图不合法（重复 key / 未知依赖 / 成环 / 孤立节点）。"""


# --------------------------------------------------------------------------- #
# 服务端工具策略：只由本模块的代码配置决定，绝不读取用户 goal/input 里的自由文本。
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ToolPolicy:
    enabled: bool = False
    allowed: tuple[str, ...] = ()
    readonly: bool = True
    required: tuple[str, ...] = ()
    max_rounds: int = 6
    max_chars: int = 8000
    max_seconds: float = 60.0
    strict: bool = True
    dedupe: bool = False

    @property
    def mutating(self) -> bool:
        """是否包含需要写授权/审批的写工具（含所有插件工具）。"""

        return bool(self.enabled and not self.readonly and self.allowed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "allowed": list(self.allowed),
            "readonly": self.readonly,
            "required": list(self.required),
            "max_rounds": self.max_rounds,
            "max_chars": self.max_chars,
            "max_seconds": self.max_seconds,
            "strict": self.strict,
            "dedupe": self.dedupe or self.mutating,
            "mutating": self.mutating,
        }


def _policy(**values: Any) -> ToolPolicy:
    allowed = tuple(str(item).strip() for item in (values.get("allowed") or ()) if str(item).strip())
    return ToolPolicy(
        enabled=bool(values.get("enabled", bool(allowed))),
        allowed=allowed,
        readonly=bool(values.get("readonly", True)),
        required=tuple(str(item).strip() for item in (values.get("required") or ()) if str(item).strip()),
        max_rounds=max(1, int(values.get("max_rounds", 6))),
        max_chars=max(1, int(values.get("max_chars", 8000))),
        max_seconds=max(1.0, float(values.get("max_seconds", 60.0))),
        strict=bool(values.get("strict", True)),
        dedupe=bool(values.get("dedupe", False)),
    )


#: 服务端配置的策略表（可按部署修改代码/配置，不接受请求体注入）。
#: 标准工作流仅给 technical_draft 开放只读项目工具；写工具默认不在任何策略中。
TOOL_POLICIES: dict[str, dict[str, ToolPolicy]] = {
    "standard": {
        "technical": _policy(
            allowed=("get_project", "get_ledger", "search_knowledge"), readonly=True
        ),
    },
    "custom": {},
}

EMPTY_TOOL_POLICY = ToolPolicy()


def tool_policy(workflow_type: str, agent_type: str) -> ToolPolicy:
    """按工作流类型与 Agent 类型解析服务端策略；无配置则完全关闭工具。"""

    kind = canonical_workflow_type(str(workflow_type or "standard"))
    return TOOL_POLICIES.get(kind, {}).get(
        str(agent_type or ""), EMPTY_TOOL_POLICY
    )


def _normalize_task(raw: dict[str, Any], seq: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PlanError("任务定义必须是对象")
    task_key = str(raw.get("task_key") or raw.get("key") or "").strip()
    agent_type = str(raw.get("agent_type") or "").strip()
    if not task_key:
        raise PlanError(f"第 {seq} 个任务缺少 task_key")
    if not agent_type:
        raise PlanError(f"任务 {task_key} 缺少 agent_type")
    depends_on = raw.get("depends_on") or []
    if isinstance(depends_on, str):
        depends_on = [depends_on]
    try:
        normalized_seq = int(raw.get("seq") if raw.get("seq") is not None else seq)
    except (TypeError, ValueError) as exc:
        raise PlanError(f"任务 {task_key} 的 seq 必须是整数") from exc
    return {
        "task_key": task_key,
        "agent_type": agent_type,
        "seq": normalized_seq,
        "input": dict(raw.get("input") or {}),
        "depends_on": [str(item) for item in depends_on if str(item).strip()],
    }


def build_task_dependencies(
    tasks: list[dict[str, Any]] | dict[str, Any],
) -> dict[str, list[str]]:
    """Return ``{task_key: [dependency keys]}`` for a task list/workflow dict."""

    rows = tasks.get("tasks") if isinstance(tasks, dict) else tasks
    normalized = [_normalize_task(row, index) for index, row in enumerate(rows or [])]
    return {row["task_key"]: list(row["depends_on"]) for row in normalized}


def _detect_cycle(graph: dict[str, list[str]]) -> list[str]:
    errors: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in visiting:
            cycle = " → ".join([*path, node])
            errors.append(f"工作流存在循环依赖：{cycle}")
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, []):
            if dependency in graph:
                visit(dependency, [*path, node])
        visiting.remove(node)
        visited.add(node)

    for key in graph:
        visit(key, [])
    return errors


def validate_workflow_graph(
    tasks: list[dict[str, Any]] | dict[str, Any],
) -> list[str]:
    """Validate the DAG.  Returns a list of human-readable errors ([] = valid)."""

    rows = tasks.get("tasks") if isinstance(tasks, dict) else tasks
    try:
        normalized = [_normalize_task(row, index) for index, row in enumerate(rows or [])]
    except PlanError as exc:
        return [str(exc)]
    if not normalized:
        return ["工作流至少需要一个任务"]

    errors: list[str] = []
    keys = [row["task_key"] for row in normalized]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        errors.append(f"任务 key 重复：{', '.join(duplicates)}")

    known = set(keys)
    graph: dict[str, list[str]] = {}
    for row in normalized:
        missing = [dep for dep in row["depends_on"] if dep not in known]
        if missing:
            errors.append(f"任务 {row['task_key']} 依赖不存在的任务：{', '.join(missing)}")
        if row["task_key"] in row["depends_on"]:
            errors.append(f"任务 {row['task_key']} 不能依赖自身")
        graph[row["task_key"]] = [dep for dep in row["depends_on"] if dep in known]

    errors.extend(_detect_cycle(graph))

    # 孤立节点：既无上游也无下游，却与其他任务共存。单节点工作流允许。
    if len(normalized) > 1:
        downstream = {dep for deps in graph.values() for dep in deps}
        for row in normalized:
            key = row["task_key"]
            if not graph.get(key) and key not in downstream:
                errors.append(f"任务 {key} 是孤立节点（既无依赖也无下游）")
    return errors


def _topological_order(graph: dict[str, list[str]]) -> list[str]:
    state: dict[str, int] = {}

    def visit(node: str) -> None:
        if state.get(node) == 1:
            raise PlanError("工作流存在循环依赖")
        if state.get(node) == 2:
            return
        state[node] = 1
        for dependency in graph.get(node, []):
            if dependency in graph:
                visit(dependency)
        state[node] = 2
        order.append(node)

    order: list[str] = []
    for node in graph:
        visit(node)
    return order


def workflow_order(tasks: list[dict[str, Any]]) -> list[str]:
    """Topological order used only for display/tests; dispatch uses readiness."""

    graph = build_task_dependencies(tasks)
    errors = validate_workflow_graph(tasks)
    if errors:
        raise PlanError("；".join(errors))
    return _topological_order(graph)


def _standard_tasks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    approval_type = str(payload.get("approval_type") or "delivery")
    if approval_type not in {
        "technical",
        "commercial",
        "asset",
        "price",
        "compliance",
        "delivery",
    }:
        approval_type = "delivery"
    query = str(payload.get("query") or "")
    package_id = payload.get("package_id")
    rows = [
        {
            "task_key": "tender_parse",
            "agent_type": "tender",
            "input": {},
            "depends_on": [],
        },
        {
            "task_key": "score_plan",
            "agent_type": "planning",
            "input": {},
            "depends_on": ["tender_parse"],
        },
        {
            "task_key": "knowledge_match",
            "agent_type": "knowledge",
            "input": {"query": query, "limit": 4, "phase": "match"},
            "depends_on": ["score_plan"],
        },
        {
            "task_key": "boq_analysis",
            "agent_type": "commercial",
            "input": {"phase": "analysis", "package_id": package_id},
            "depends_on": ["score_plan"],
        },
        {
            "task_key": "technical_draft",
            "agent_type": "technical",
            "input": {"phase": "draft"},
            "depends_on": ["score_plan", "knowledge_match"],
        },
        {
            "task_key": "commercial_draft",
            "agent_type": "commercial",
            "input": {"phase": "draft", "package_id": package_id},
            "depends_on": ["boq_analysis", "technical_draft"],
        },
        {
            "task_key": "compliance_check",
            "agent_type": "compliance",
            "input": {},
            "depends_on": ["technical_draft", "commercial_draft", "knowledge_match"],
        },
        {
            "task_key": "approval_gate",
            "agent_type": "reviewer",
            "input": {"approval": True, "approval_type": approval_type},
            "depends_on": ["compliance_check"],
        },
        {
            "task_key": "delivery",
            "agent_type": "reviewer",
            "input": {"phase": "delivery"},
            "depends_on": ["approval_gate"],
        },
    ]
    return [_normalize_task(row, index) for index, row in enumerate(rows)]


def build_workflow(
    workflow_type: str = "standard",
    payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build a validated workflow task list.

    ``workflow_type='custom'`` reads ``payload['tasks']`` (used by extensions
    and tests); any unknown type is rejected rather than silently running an
    empty workflow.  ``full`` / ``bid`` resolve to the canonical ``standard``
    graph and are annotated with alias metadata instead of forking a DAG.
    """

    options = dict(payload or {})
    kind = str(workflow_type or "standard")
    canonical = canonical_workflow_type(kind)
    if canonical in CANONICAL_WORKFLOWS:
        tasks = _standard_tasks(options)
    elif canonical == "custom":
        raw_tasks = options.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise PlanError("custom 工作流必须提供非空 tasks 列表")
        tasks = [_normalize_task(row, index) for index, row in enumerate(raw_tasks)]
    else:
        raise PlanError(f"未知工作流类型: {kind}")

    if canonical != kind:
        logger.debug("工作流类型 %s 使用 %s 的标准 DAG", kind, canonical)
        for item in tasks:
            item["input"] = {
                **item["input"],
                "_workflow_alias": kind,
                "_canonical_workflow_type": canonical,
            }
    errors = validate_workflow_graph(tasks)
    if errors:
        raise PlanError("；".join(errors))
    return tasks


# --------------------------------------------------------------------------- #
# 内置 planning Agent：把评分分析转成写作/商务策略
# --------------------------------------------------------------------------- #
class PlanningAgent(BaseAgent):
    """评分规划：读取评分项与历史失分，输出可执行的写作优先级。"""

    agent_type = "planning"
    display_name = "评分规划"
    description = "汇总评分项、失分项与得分率，生成技术/商务写作优先级。"

    def execute(self, db, project: Project, context: dict[str, Any]) -> AgentOutput:
        report = scoring.analysis(db, project)
        hints = scoring.hints(db, project)
        items = report.get("items") or []
        losses = report.get("losses") or []
        warnings: list[str] = []
        if not items:
            warnings.append("项目尚未录入评分项，计划按行业规则包默认权重执行")
        if report.get("rate") is None:
            warnings.append("尚未回填历史得分，无法计算得分率")
        plan = []
        for row in losses[:10]:
            plan.append(
                {
                    "category": row.get("category") or "未分类",
                    "name": row.get("name") or "",
                    "score": row.get("score"),
                    "actual_score": row.get("actual_score"),
                    "loss": row.get("loss"),
                    "priority": "high" if (row.get("loss") or 0) >= 3 else "normal",
                }
            )
        return AgentOutput(
            status="ok",
            result={
                "total_items": report.get("total_items", 0),
                "filled": report.get("filled", 0),
                "total_max": report.get("total_max", 0),
                "total_actual": report.get("total_actual", 0),
                "rate": report.get("rate"),
                "dimensions": report.get("dimensions") or [],
                "priority_plan": plan,
                "hints": hints,
            },
            evidence=losses[:10],
            warnings=warnings,
        )


register_agent(PlanningAgent)

__all__ = [
    "CANONICAL_WORKFLOWS",
    "TOOL_POLICIES",
    "WORKFLOW_ALIASES",
    "PlanError",
    "PlanningAgent",
    "ToolPolicy",
    "build_task_dependencies",
    "build_workflow",
    "canonical_workflow_type",
    "tool_policy",
    "validate_workflow_graph",
    "workflow_order",
]
