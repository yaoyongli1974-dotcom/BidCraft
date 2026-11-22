"""合规检查 Agent：复用 ``pipeline.check_project`` 的规则核对唯一口径。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ...models import Issue
from ...pipeline import check_project
from ..registry import AgentOutput
from .base import BaseAgent


class ComplianceAgent(BaseAgent):
    agent_type = "compliance"
    display_name = "合规检查"
    description = "执行规则核对并汇总阻断/严重/主要风险，阻断项不得伪装通过。"

    def execute(self, db, project, context: dict[str, Any]) -> AgentOutput:
        # check_project 把结果写入 Issue 表并返回 (CheckResult 列表, summary)。
        # 未处置判定必须以落库的 Issue 行（含人工处置 status）为准。
        _results, summary = check_project(db, project.id)
        issues = list(
            db.scalars(select(Issue).where(Issue.project_id == project.id))
        )
        open_issues = [issue for issue in issues if issue.status == "open"]
        blocking = [issue for issue in open_issues if issue.level == "blocking"]
        critical = [issue for issue in open_issues if issue.level in {"critical", "major"}]
        warnings: list[str] = []
        if blocking:
            warnings.append(f"存在 {len(blocking)} 项未处置的废标/阻断风险")
        if critical:
            warnings.append(f"存在 {len(critical)} 项未处置的严重/主要风险")
        evidence = [
            {
                "id": issue.id,
                "code": issue.code,
                "level": issue.level,
                "category": issue.category,
                "finding": issue.finding,
                "status": issue.status,
                "location": issue.location,
            }
            for issue in sorted(
                open_issues,
                key=lambda row: (
                    {"blocking": 0, "critical": 1, "major": 2}.get(row.level, 9),
                    row.code or "",
                ),
            )
        ][:30]
        return AgentOutput(
            status="ok",
            result={
                "issues": len(issues),
                "open": len(open_issues),
                "blocking": len(blocking),
                "critical_or_major": len(critical),
                "summary": summary,
                "gate": {
                    "blocked": bool(blocking),
                    "reason": (
                        f"存在 {len(blocking)} 项未处置的废标/阻断风险"
                        if blocking
                        else ""
                    ),
                },
                "items": evidence,
            },
            evidence=evidence,
            warnings=warnings,
            requires_approval=bool(blocking),
            approval_type="compliance" if blocking else "",
        )
