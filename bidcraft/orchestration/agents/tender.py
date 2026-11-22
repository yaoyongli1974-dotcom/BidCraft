"""招标解析 Agent：读取事实台账，输出必填缺口与条款/评分/风险摘要。"""

from __future__ import annotations

from typing import Any

from ...ledger import ledger_summary, load_ledger
from ..registry import AgentOutput
from .base import BaseAgent, excerpt, project_brief


class TenderAgent(BaseAgent):
    agent_type = "tender"
    display_name = "招标解析"
    description = "汇总事实台账、必填缺口、条款、评分项与风险，作为后续 Agent 的输入。"

    def execute(self, db, project, context: dict[str, Any]) -> AgentOutput:
        ledger = load_ledger(db, project.id)
        summary = ledger_summary(ledger)
        missing = [
            {
                "key": fact.key,
                "label": fact.label,
                "required": bool(fact.required),
            }
            for fact in ledger.facts
            if fact.required and not (fact.value or "").strip()
        ]
        evidence = [
            {
                "key": fact.key,
                "label": fact.label,
                "value": fact.value,
                "page": getattr(fact.source, "page", None),
                "quote": excerpt(getattr(fact.source, "quote", ""), 200),
            }
            for fact in ledger.facts
            if (fact.value or "").strip()
        ][:12]
        warnings: list[str] = []
        if not ledger.facts:
            warnings.append("事实台账为空：尚未解析招标文件")
        if missing:
            warnings.append(f"存在 {len(missing)} 项必填事实缺口")
        return AgentOutput(
            status="ok",
            result={
                "project": project_brief(project),
                "summary": summary,
                "missing_required": missing,
                "requirements": len(ledger.requirements),
                "scoring_items": len(ledger.scoring),
                "risks": len(ledger.risks),
                "materials": len(ledger.materials),
            },
            evidence=evidence,
            warnings=warnings,
        )
