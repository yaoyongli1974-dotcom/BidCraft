"""商务标 / 清单分析 Agent：复用 ``boq.build_payload`` 的唯一对齐口径。"""

from __future__ import annotations

from typing import Any

from ... import boq, scoring
from ..registry import AgentOutput
from .base import BaseAgent


class CommercialAgent(BaseAgent):
    agent_type = "commercial"
    display_name = "商务标"
    description = "分析清单对齐、报价完整度与评分商务项，输出风险与待办。"

    def execute(self, db, project, context: dict[str, Any]) -> AgentOutput:
        payload = context.get("payload") or {}
        phase = str(payload.get("phase") or "analysis")
        report = boq.build_payload(db, project.id, payload.get("package_id"))
        summary = dict(report.get("summary") or {})
        rows = list(report.get("rows") or [])
        snapshot = report.get("snapshot")
        unmatched = [
            {
                "name": row.get("name") or row.get("boq_name") or "",
                "status": row.get("status") or "",
                "spec": row.get("spec") or row.get("boq_spec") or "",
            }
            for row in rows
            if row.get("status") not in {None, "", "matched"}
        ][:20]
        warnings: list[str] = []
        status = "ok"
        if snapshot is None:
            warnings.append("项目尚未导入工程量清单，商务标无法完成清单分析")
            status = "skipped"
        else:
            if summary.get("missing_in_boq"):
                warnings.append(f"有 {summary['missing_in_boq']} 项台账材料未出现在清单中")
            if summary.get("unpriced"):
                warnings.append(f"有 {summary['unpriced']} 项清单未报价")
            if summary.get("quantity_missing"):
                warnings.append(f"有 {summary['quantity_missing']} 项报价缺少数量")

        commercial_scores = []
        try:
            analysis = scoring.analysis(db, project)
            commercial_scores = [
                item
                for item in analysis.get("items") or []
                if item.get("category") and "商务" in str(item.get("category"))
            ][:20]
        except Exception:  # noqa: BLE001 - scoring is supplementary here
            warnings.append("评分分析暂不可用，商务标按清单结果输出")

        return AgentOutput(
            status=status,
            result={
                "phase": phase,
                "summary": summary,
                "ledger_summary": report.get("ledger_summary") or {},
                "row_count": len(rows),
                "unmatched": unmatched,
                "commercial_scores": commercial_scores,
                "currency": "CNY",
            },
            evidence=unmatched,
            warnings=warnings,
        )
