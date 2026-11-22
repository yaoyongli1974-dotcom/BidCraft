"""技术标 Agent：评估大纲/正文完成度，汇总评分提示与知识库证据。

默认走确定性分析（不依赖模型）。``use_model=True`` 时尝试生成技术方案
摘要；模型不可用则**显式降级**（status=skipped + degraded 标记），模型调用
报错则 status=failed，绝不伪装成功。
"""

from __future__ import annotations

from typing import Any

from ... import scoring
from ...generation.outline import load_outline
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
                )

        return AgentOutput(
            status="ok",
            result=result,
            evidence=passages[:5],
            warnings=warnings,
        )

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
