"""技术标 Agent：评估大纲/正文完成度，汇总评分提示与知识库证据。

默认走确定性分析（不依赖模型）。``use_model=True`` 时尝试生成技术方案
摘要；模型不可用则**显式降级**（status=skipped + degraded 标记），模型调用
报错则 status=failed，绝不伪装成功。
"""

from __future__ import annotations

from functools import partial
from typing import Any

from ... import scoring
from ...generation.outline import load_outline
from .. import registry
from ..registry import AgentOutput
from .base import BaseAgent


class TechnicalAgent(BaseAgent):
    agent_type = "technical"
    display_name = "技术标"
    description = "基于大纲、已写正文、评分提示与知识库证据评估技术标完成度。"

    def execute(self, db, project, context: dict[str, Any]) -> AgentOutput:
        payload = context.get("payload") or {}
        nodes = load_outline(db, project.id)
        written = [node for node in nodes if (node.get("body") or "").strip()]
        confirmed = [
            node
            for node in written
            if (node.get("review_status") or "draft") in {"confirmed", "locked"}
        ]
        pending = [node["heading"] for node in nodes if not (node.get("body") or "").strip()]
        hints = scoring.hints(db, project)
        upstream = context.get("upstream") or {}
        knowledge = upstream.get("knowledge_match") or {}
        passages = (knowledge.get("result") or {}).get("passages") or []
        warnings: list[str] = []
        result: dict[str, Any] = {
            "outline_chapters": len(nodes),
            "written_chapters": len(written),
            "confirmed_chapters": len(confirmed),
            "pending_chapters": pending[:20],
            "completion": round(len(written) / len(nodes), 4) if nodes else 0.0,
            "scoring_hints": hints,
            "knowledge_evidence": len(passages),
        }
        if not nodes:
            warnings.append("项目尚无大纲，技术标无法进入写作阶段")
            return AgentOutput(
                status="skipped",
                result={**result, "reason": "缺少大纲"},
                warnings=warnings,
            )
        if not written:
            warnings.append("尚无已写正文，技术标仅完成规划检查")

        tool_calls: list[dict[str, Any]] = []
        policy = dict((context.get("run") or {}).get("tool_policy") or {})
        if policy.get("enabled") and policy.get("allowed"):
            tool_outcome = self._tool_assisted(
                project, context, policy, nodes, hints, passages
            )
            tool_calls = tool_outcome["tool_calls"]
            warnings.extend(tool_outcome["warnings"])
            result.update(tool_outcome["result"])
            if tool_outcome["status"] != "ok":
                return AgentOutput(
                    status=tool_outcome["status"],
                    result=result,
                    evidence=passages[:5],
                    warnings=warnings,
                    tool_calls=tool_calls,
                )

        if payload.get("use_model"):
            model = self._model_draft(project, nodes, hints, passages)
            result.update(model["result"])
            warnings.extend(model["warnings"])
            if model["status"] != "ok":
                return AgentOutput(
                    status=model["status"],
                    result=result,
                    evidence=passages[:5],
                    warnings=warnings,
                    tool_calls=tool_calls,
                )

        return AgentOutput(
            status="ok",
            result=result,
            evidence=passages[:5],
            warnings=warnings,
            tool_calls=tool_calls,
        )

    @staticmethod
    def _tool_assisted(
        project,
        context: dict[str, Any],
        policy: dict[str, Any],
        nodes: list[dict[str, Any]],
        hints: str,
        passages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """唯一启用工具循环的 Agent 路径；工具集与只读性均由服务端策略决定。"""

        from ...config import settings
        from ...generation.agent import ModelClient, ModelUnavailable
        from ...generation.tool_loop import (
            ToolLoopBudgetError,
            ToolVerificationError,
            run_tool_loop,
        )

        empty: dict[str, Any] = {
            "status": "ok", "result": {}, "warnings": [], "tool_calls": []
        }
        actor = context.get("actor") or None
        allowed = set(policy.get("allowed") or [])
        tools = [
            spec for spec in registry.list_tools(actor=actor) if spec.get("name") in allowed
        ]
        if not tools:
            return empty
        client = ModelClient(settings)
        if not client.available:
            return {
                **empty,
                "warnings": ["工具增强未启用：模型未配置，按确定性分析继续"],
            }

        traces: list[dict[str, Any]] = []
        messages = [
            {
                "role": "system",
                "content": (
                    "你是投标技术方案专家。只能调用服务端提供的工具；"
                    "工具的项目范围由服务端绑定，参数中不得指定其他项目或包名。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"项目：{project.name}\n"
                    "大纲：\n" + "\n".join(node["heading"] for node in nodes[:30]) + "\n"
                    + (f"评分提示：\n{hints}\n" if hints else "")
                    + (
                        "知识库参考：\n"
                        + "\n".join(str(item.get("text") or "")[:300] for item in passages[:3])
                        if passages
                        else ""
                    )
                    + "\n请先用可用工具核对项目事实，再输出 5 条技术标写作要点。"
                ),
            },
        ]
        executor = partial(
            registry.call_tool_with_trace,
            readonly=bool(policy.get("readonly", True)),
            actor=actor,
            project_id=project.id,
            package_id=str(context.get("payload", {}).get("package_id") or ""),
            run_id=str((context.get("run") or {}).get("id") or ""),
            task_id=str((context.get("run") or {}).get("task_id") or ""),
        )
        try:
            loop = run_tool_loop(
                client,
                messages=messages,
                tools=tools,
                executor=executor,
                max_rounds=int(policy.get("max_rounds", 6)),
                max_chars=int(policy.get("max_chars", 8000)),
                required_tools=tuple(policy.get("required") or ()),
                on_trace=traces.append,
            )
        except (ToolLoopBudgetError, ToolVerificationError, ModelUnavailable) as exc:
            return {
                "status": "failed",
                "result": {"error": f"工具增强失败：{exc}"},
                "warnings": ["工具增强失败，任务不得标记为成功"],
                "tool_calls": traces,
            }
        return {
            "status": "ok",
            "result": {
                "model_draft": loop.content,
                "tool_rounds": loop.rounds,
                "degraded": False,
            },
            "warnings": [],
            "tool_calls": loop.tool_calls,
        }

    @staticmethod
    def _model_draft(project, nodes, hints: str, passages: list[dict[str, Any]]) -> dict[str, Any]:
        from ...config import settings
        from ...generation.agent import ModelClient, ModelUnavailable

        client = ModelClient(settings)
        if not client.available:
            return {
                "status": "skipped",
                "result": {
                    "degraded": True,
                    "degraded_reason": "模型未配置，已降级为确定性大纲分析",
                },
                "warnings": ["模型不可用：未配置模型端点，技术标使用降级分析"],
            }
        messages = [
            {
                "role": "system",
                "content": "你是投标技术方案专家，只根据给定信息给出写作要点，不得编造参数。",
            },
            {
                "role": "user",
                "content": (
                    f"项目：{project.name}\n"
                    f"大纲：\n" + "\n".join(node["heading"] for node in nodes[:30]) + "\n"
                    + (f"评分提示：\n{hints}\n" if hints else "")
                    + (
                        "知识库参考：\n"
                        + "\n".join(str(item.get("text") or "")[:300] for item in passages[:3])
                        if passages
                        else ""
                    )
                    + "\n请输出 5 条技术标写作要点。"
                ),
            },
        ]
        try:
            content = client.complete(messages, max_tokens=800)
        except ModelUnavailable as exc:
            return {
                "status": "failed",
                "result": {"degraded": False, "error": f"模型调用失败：{exc}"},
                "warnings": ["模型调用失败"],
            }
        return {
            "status": "ok",
            "result": {"model_draft": content, "degraded": False},
            "warnings": [],
        }
