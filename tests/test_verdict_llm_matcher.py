"""可选 LLM 语义匹配器：解析/缓存/回退/工厂/流水线贯通，全部无真实网络。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bidcraft import substantive
from bidcraft.config import Settings
from bidcraft.generation.agent import ModelUnavailable
from bidcraft.substantive import REQ_TYPE_PARAMETER
from bidcraft.verdict import UNSATISFIED, RuleSemanticMatcher, judge
from bidcraft.verdict_llm import (
    LLMSemanticMatcher,
    _parse_score,
    build_verdict_matcher,
)


class FakeClient:
    available = True

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        item = self.responses.pop(0) if self.responses else "0.5"
        if isinstance(item, Exception):
            raise item
        return item


class AlwaysOneMatcher:
    def coverage(self, text, body, req_type=REQ_TYPE_PARAMETER):
        return 1.0


def _settings(**overrides):
    data = {
        "verdict_llm": True,
        "model_configured": True,
        "offline": False,
        "model_base_url": "https://model.example/v1",
        "model_api_key": "k",
        "model_name": "fake",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_parse_score_accepts_number_and_first_numeric_token():
    assert _parse_score("0.8") == pytest.approx(0.8)
    assert _parse_score("分数: 0.35") == pytest.approx(0.35)
    assert _parse_score(1) == pytest.approx(1.0)
    assert _parse_score(1.4) is None
    assert _parse_score("无法判断") is None
    assert _parse_score(True) is None


def test_llm_matcher_returns_parsed_score():
    matcher = LLMSemanticMatcher(FakeClient("0.82"))
    assert matcher.coverage("须提供一级资质", "我方为一级资质") == pytest.approx(0.82)
    assert matcher.calls == 1


def test_llm_matcher_caches_identical_pairs():
    client = FakeClient("0.7", "0.2")
    matcher = LLMSemanticMatcher(client)
    args = ("须提供一级资质", "我方为一级资质", REQ_TYPE_PARAMETER)
    assert matcher.coverage(*args) == pytest.approx(0.7)
    assert matcher.coverage(*args) == pytest.approx(0.7)
    assert client.calls.__len__() == 1
    assert matcher.calls == 1


def test_llm_matcher_falls_back_on_unparsable_output():
    rule = RuleSemanticMatcher()
    text, body = "须提供一级资质", "我方为一级资质"
    expected = rule.coverage(text, body)
    matcher = LLMSemanticMatcher(FakeClient("抱歉，我无法判断"))
    assert matcher.coverage(text, body) == pytest.approx(expected)


def test_llm_matcher_falls_back_when_model_unavailable():
    rule = RuleSemanticMatcher()
    text, body = "须提供一级资质", "我方为一级资质"
    matcher = LLMSemanticMatcher(FakeClient(ModelUnavailable("模型不可用")))
    assert matcher.coverage(text, body) == pytest.approx(rule.coverage(text, body))
    assert matcher.calls == 1


def test_llm_matcher_respects_call_budget():
    client = FakeClient("0.9", "0.9")
    matcher = LLMSemanticMatcher(client, max_calls=1)
    assert matcher.coverage("要求A", "响应A") == pytest.approx(0.9)
    rule = RuleSemanticMatcher()
    assert matcher.coverage("要求B", "响应B") == pytest.approx(
        rule.coverage("要求B", "响应B")
    )
    assert matcher.calls == 1


def test_build_matcher_disabled_by_default():
    matcher = build_verdict_matcher(settings_override=_settings(verdict_llm=False))
    assert isinstance(matcher, RuleSemanticMatcher)


def test_build_matcher_without_model_falls_back():
    matcher = build_verdict_matcher(
        settings_override=_settings(verdict_llm=True, model_configured=False)
    )
    assert isinstance(matcher, RuleSemanticMatcher)


def test_build_matcher_enabled_returns_llm(monkeypatch):
    import bidcraft.verdict_llm as module

    monkeypatch.setattr(module, "ModelClient", lambda target: FakeClient("0.5"))
    matcher = build_verdict_matcher(settings_override=_settings())
    assert isinstance(matcher, LLMSemanticMatcher)


def test_verdict_still_blocks_on_constraint_with_llm_score_one():
    """语义满分也不能越过数字硬约束（核心原则）。"""
    req = SimpleNamespace(text="传输带宽不小于 1000 Mbps", marker="★", mandatory=True)
    nodes = [{"id": "n1", "position": 0, "heading": "技术方案", "body": "带宽为 500 Mbps"}]
    verdict = judge(req, nodes, matcher=AlwaysOneMatcher())
    assert verdict.state == UNSATISFIED
    assert verdict.evidence["layer4"]["matcher"] == "AlwaysOneMatcher"


def test_requirement_issues_threads_matcher():
    req = SimpleNamespace(
        id="r1",
        text="传输带宽不小于 1000 Mbps",
        marker="★",
        mandatory=True,
        response_status="pending",
        source_quote="招标原文",
    )
    nodes = [{"id": "n1", "position": 0, "heading": "技术方案", "body": "带宽为 500 Mbps"}]
    results = substantive.requirement_issues([req], nodes, matcher=AlwaysOneMatcher())
    assert results and results[0].evidence["layers"]["layer4"]["matcher"] == "AlwaysOneMatcher"


def test_settings_exposes_verdict_llm_env(monkeypatch):
    monkeypatch.setenv("BIDCRAFT_VERDICT_LLM", "1")
    assert Settings().verdict_llm is True
    monkeypatch.setenv("BIDCRAFT_VERDICT_LLM", "0")
    assert Settings().verdict_llm is False
