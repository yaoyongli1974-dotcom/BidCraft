"""企业资料匹配 Agent：复用知识库检索服务（含个人库隔离策略）。"""

from __future__ import annotations

from typing import Any

from ... import knowledge, material_policy
from ..registry import AgentOutput
from .base import BaseAgent, excerpt


class KnowledgeAgent(BaseAgent):
    agent_type = "knowledge"
    display_name = "企业资料匹配"
    description = "按项目/评分关注点检索企业知识库，输出来源与相关性证据。"

    def execute(self, db, project, context: dict[str, Any]) -> AgentOutput:
        payload = context.get("payload") or {}
        query = str(payload.get("query") or "").strip() or project.name
        try:
            limit = max(1, min(10, int(payload.get("limit") or 4)))
        except (TypeError, ValueError):
            limit = 4
        try:
            passages = knowledge.search(
                db, query, limit=limit, include_personal=False
            )
        except knowledge.KnowledgeError as exc:
            return AgentOutput(
                status="failed",
                result={"query": query, "error": f"知识库检索失败：{exc}"},
                warnings=["知识库检索失败"],
            )
        verdicts = material_policy.passage_verdicts(db, passages)
        items = [
            {
                "kb": passage.kb,
                "kb_id": passage.kb_id,
                "source": passage.source,
                "document_id": passage.document_id,
                "page": passage.page,
                "heading": passage.heading or "",
                "chunk_id": passage.chunk_id,
                "chunk_index": passage.chunk_index,
                "score": passage.score,
                "rerank_score": passage.rerank_score,
                "trust_tier": verdict.tier,
                "degraded": bool(verdict.downgraded),
                "text": excerpt(passage.text, 500),
            }
            for passage, verdict in zip(passages, verdicts, strict=True)
        ]
        warnings: list[str] = []
        if not items:
            warnings.append("知识库未命中相关内容，后续 Agent 需依赖事实台账")
        return AgentOutput(
            status="ok",
            result={
                "query": query,
                "limit": limit,
                "count": len(items),
                "passages": items,
            },
            evidence=items,
            warnings=warnings,
        )
