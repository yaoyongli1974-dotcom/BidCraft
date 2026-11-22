"""复核 / 交付 Agent：汇总上游结果，输出交付检查清单。

章节确认仍走既有 ``review_chapter`` 流程；本 Agent 不另建章节审核状态机，
只做工作流级复核。
"""

from __future__ import annotations

from typing import Any

from ..registry import AgentOutput
from .base import BaseAgent


class ReviewerAgent(BaseAgent):
    agent_type = "reviewer"
    display_name = "复核与交付"
    description = "检查上游 Agent 结果完整性，汇总未解决警告并给出交付清单。"

    def execute(self, db, project, context: dict[str, Any]) -> AgentOutput:
        payload = context.get("payload") or {}
        if payload.get("approval"):
            return AgentOutput(
                status="requires_approval",
                result={"approval_type": payload.get("approval_type") or "delivery"},
                requires_approval=True,
                approval_type=str(payload.get("approval_type") or "delivery"),
            )
        upstream = context.get("upstream") or {}
        failed = [
            key
            for key, output in upstream.items()
            if str((output or {}).get("status") or "") == "failed"
        ]
        warnings: list[str] = []
        for key, output in upstream.items():
            for warning in (output or {}).get("warnings") or []:
                warnings.append(f"{key}: {warning}")
        phase = str(payload.get("phase") or "review")
        required = [
            "tender_parse",
            "technical_draft",
            "commercial_draft",
            "compliance_check",
        ]
        missing = [key for key in required if key not in upstream]
        checklist = [
            {
                "item": "招标解析完成",
                "ok": "tender_parse" in upstream,
            },
            {
                "item": "技术标完成度已评估",
                "ok": "technical_draft" in upstream,
            },
            {
                "item": "商务标/清单已完成",
                "ok": "commercial_draft" in upstream,
            },
            {
                "item": "合规检查已执行",
                "ok": "compliance_check" in upstream,
            },
            {
                "item": "无阻断风险",
                "ok": not any(
                    "阻断" in warning for warning in warnings
                ),
            },
        ]
        if failed:
            return AgentOutput(
                status="failed",
                result={
                    "phase": phase,
                    "error": f"上游任务失败：{', '.join(failed)}",
                    "checklist": checklist,
                    "missing": missing,
                },
                evidence=[],
                warnings=warnings,
            )
        if phase == "delivery":
            for key in missing:
                warnings.append(f"交付清单缺少上游结果：{key}")
        status = "ok"
        return AgentOutput(
            status=status,
            result={
                "phase": phase,
                "checklist": checklist,
                "missing": missing,
                "upstream_tasks": sorted(upstream),
                "deliverable": phase == "delivery" and not missing,
            },
            evidence=checklist,
            warnings=warnings,
        )
