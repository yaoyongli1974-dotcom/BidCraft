"""可选 LLM 语义匹配器：只回答「段落是否正面回应要求」这一语义问题。

设计边界（与 ``verdict.SemanticMatcher`` 协议一致）：
* 数字、等级、时间窗、否定等硬性约束仍由 ``constraint_check`` / 五层判定引擎裁决；
  LLM 的分数只作为第 4 层语义相似度，**单独永远不能判定 SATISFIED**。
* 默认关闭（``BIDCRAFT_VERDICT_LLM``），未配置模型、模型不可用或调用失败时
  一律回退 ``RuleSemanticMatcher``；网络错误不会让核对流程失败。
* 段落级调用带缓存与次数上限，避免同一要求在多节点/多原子上重复付费。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .config import settings
from .generation.agent import ModelClient, ModelUnavailable
from .substantive import REQ_TYPE_GENERAL
from .verdict import RuleSemanticMatcher, SemanticMatcher

logger = logging.getLogger("bidcraft.verdict.llm")

#: 单次核对任务默认最多发起的真实 LLM 调用次数（缓存命中不计数）。
DEFAULT_MAX_CALLS = 200

_SYSTEM_PROMPT = (
    "你是投标文件条款符合性评估助手。"
    "只判断响应段落是否在语义上正面回应了招标要求，不要判断数字、等级或日期是否精确——"
    "这些由其它规则负责。"
    "输出 0 到 1 之间的一个数字：0=无关或明确否定，0.45=部分回应，1=完整正面回应。"
    "只输出数字，不要解释、不要标点、不要 Markdown。"
)
_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")


class LLMSemanticMatcher:
    """通过已配置模型给出语义回应分，失败自动回退本地规则。"""

    def __init__(self, client: Any, *, max_calls: int = DEFAULT_MAX_CALLS) -> None:
        self.client = client
        self.max_calls = max(1, int(max_calls or DEFAULT_MAX_CALLS))
        self._fallback = RuleSemanticMatcher()
        self._cache: dict[tuple[str, str, str], float] = {}
        self._calls = 0

    @property
    def calls(self) -> int:
        """已发起的真实模型调用数，供观测与测试。"""
        return self._calls

    def coverage(
        self, text: str, body: str, req_type: str = REQ_TYPE_GENERAL
    ) -> float:
        source = str(text or "").strip()
        response = str(body or "").strip()
        if not source or not response:
            return 0.0
        key = (source, response, str(req_type or REQ_TYPE_GENERAL))
        if key in self._cache:
            return self._cache[key]

        if self._calls >= self.max_calls:
            return self._fallback.coverage(source, response, req_type)
        prompt = (
            f"招标要求：{source}\n"
            f"响应段落：{response}\n"
            f"要求类型：{key[2]}\n"
            "语义回应分："
        )
        self._calls += 1
        try:
            raw = self.client.complete(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=32,
                temperature=0,
            )
            score = _parse_score(raw)
            if score is None:
                raise ValueError("模型未返回可解析的 0-1 分数")
        except (ModelUnavailable, ValueError, TypeError, OSError) as exc:
            logger.debug("LLM 语义匹配回退本地规则：%s", exc)
            score = self._fallback.coverage(source, response, req_type)
        self._cache[key] = score
        return score


def _parse_score(raw: Any) -> float | None:
    """从模型输出中提取 0-1 分数；无法解析返回 None。"""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if 0.0 <= value <= 1.0 else None
    text = str(raw or "").strip()
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    value = float(match.group(1))
    return value if 0.0 <= value <= 1.0 else None


def build_verdict_matcher(db: Any = None, *, settings_override: Any = None) -> SemanticMatcher:
    """按配置构造语义匹配器；不可用时安全回退规则匹配器。

    ``db`` 用于解析管理员在后台配置的模型端点；``settings_override`` 便于测试与
    非 Web 场景直接传入配置对象。两者都不传时使用全局 ``settings``。
    """
    cfg = settings_override if settings_override is not None else settings
    if not getattr(cfg, "verdict_llm", False):
        return RuleSemanticMatcher()

    target = cfg
    if db is not None:
        from . import model_config

        endpoint = model_config.resolve_chat_endpoint(db)
        if endpoint is not None:
            target = endpoint
    if not getattr(target, "model_configured", False):
        return RuleSemanticMatcher()

    try:
        client = ModelClient(target)
    except Exception as exc:  # noqa: BLE001 - 构造失败按不可用处理
        logger.debug("LLM 语义匹配器初始化失败，回退规则：%s", exc)
        return RuleSemanticMatcher()
    if not getattr(client, "available", False):
        return RuleSemanticMatcher()
    return LLMSemanticMatcher(client)
