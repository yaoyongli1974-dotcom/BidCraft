"""工作流 DAG 规划与图校验。

标准工作流（对标易标第三批）：

``招标解析 → 评分规划 → [企业资料匹配 | 清单分析]（并行）
→ 技术标 → 商务标 → 合规检查 → 人工审批 → 交付``

任务只声明 ``depends_on``；调度器按依赖是否全部完成决定可执行任务，因此
并行分支不需要线程也能保证「下游必须等两个分支都完成」。
"""

from __future__ import annotations

from typing import Any

from .. import scoring
from ..models import Project
from .registry import AgentOutput, BaseAgent, register_agent


class PlanError(ValueError):
    """工作流图不合法（重复 key / 未知依赖 / 成环 / 孤立节点）。"""


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
    return {
        "task_key": task_key,
        "agent_type": agent_type,
        "seq": int(raw.get("seq") if raw.get("seq") is not None else seq),
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
    empty workflow.
    """

    options = dict(payload or {})
    kind = str(workflow_type or "standard")
    if kind in {"standard", "full", "bid"}:
        tasks = _standard_tasks(options)
    elif kind == "custom":
        raw_tasks = options.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise PlanError("custom 工作流必须提供非空 tasks 列表")
        tasks = [_normalize_task(row, index) for index, row in enumerate(raw_tasks)]
    else:
        raise PlanError(f"未知工作流类型: {kind}")

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
    "PlanError",
    "PlanningAgent",
    "build_task_dependencies",
    "build_workflow",
    "validate_workflow_graph",
    "workflow_order",
]
