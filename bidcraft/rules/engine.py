from __future__ import annotations

import re
from typing import Any

from ..schemas import CheckResult, FactLedger
from .expr import FUNCTIONS, ExpressionError, evaluate
from .loader import CheckSpec, Package


def build_context(
    ledger: FactLedger,
    sections: list[dict[str, Any]] | None = None,
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sections = sections or []
    doc = "\n".join(
        f"{section.get('heading', '')}\n{section.get('body', '')}" for section in sections
    ) or "\n".join(str(section.get("body", "")) for section in sections)
    return {
        "facts": {item.key: item.value for item in ledger.facts},
        "fact_items": [item.model_dump() for item in ledger.facts],
        "materials": [item.model_dump() for item in ledger.materials],
        "parameters": [item.model_dump() for item in ledger.parameters],
        "scoring": [item.model_dump() for item in ledger.scoring],
        "requirements": [item.model_dump() for item in ledger.requirements],
        "risks": [item.model_dump() for item in ledger.risks],
        "sections": [dict(section) for section in sections],
        "doc": doc,
        "meta": dict(meta or {}),
    }


def _location_for(
    spec: CheckSpec, ledger: FactLedger, sections: list[dict[str, Any]]
) -> str:
    keys = re.findall(r"facts\['([^']+)'\]", spec.assert_)
    wanted = [ledger.fact_map()[key].value for key in keys if key in ledger.fact_map()]
    for section in sections:
        body = str(section.get("body") or "")
        if any(value and value in body for value in wanted):
            return str(section.get("heading") or "")
    if any(word in spec.category for word in ("身份", "商务", "报价", "资格")):
        return "全局/封面"
    if any(word in spec.category for word in ("参数", "数量", "容量", "点位")):
        return "技术章节"
    return "全文"


def _evidence_for(
    spec: CheckSpec, context: dict[str, Any]
) -> dict[str, Any]:
    show = (spec.evidence or {}).get("show")
    if not show:
        return {}
    func = FUNCTIONS.get(str(show))
    if func is None:
        return {}
    try:
        if show == "param_response_missing":
            value = func(context.get("parameters"), context.get("doc"))
        elif show in {"materials_total"}:
            value = func(context.get("materials"))
        else:
            value = func(context.get("doc"))
        if isinstance(value, list):
            return {"items": value[:20], "count": len(value)}
        return {"value": value}
    except Exception:  # noqa: BLE001
        return {}


def run_checks(
    package: Package,
    ledger: FactLedger,
    sections: list[dict[str, Any]] | None = None,
    *,
    meta: dict[str, Any] | None = None,
    include_passed: bool = False,
) -> list[CheckResult]:
    context = build_context(ledger, sections, meta=meta)
    results: list[CheckResult] = []
    for spec in package.checks:
        try:
            value = evaluate(spec.assert_, context)
            passed = bool(value)
        except ExpressionError as exc:
            results.append(
                CheckResult(
                    code=spec.id,
                    level=spec.level,  # type: ignore[arg-type]
                    category=spec.category,
                    message=f"核对项执行失败：{exc}",
                    finding=spec.message,
                    passed=False,
                    evidence={"error": str(exc)},
                )
            )
            continue
        result = CheckResult(
            code=spec.id,
            level=spec.level,  # type: ignore[arg-type]
            category=spec.category,
            message=spec.message,
            finding="" if passed else spec.message,
            recommendation="" if passed else spec.recommendation,
            location="" if passed else _location_for(spec, ledger, sections or []),
            evidence={} if passed else _evidence_for(spec, context),
            passed=passed,
        )
        if passed and not include_passed:
            continue
        results.append(result)
    return results


def ledger_gate(ledger: FactLedger) -> list[str]:
    """Block generation until every required fact has a value.

    The upstream project we studied only warned on missing facts, and the model
    then invented certificate numbers and achievements. Refusing is cheaper.
    """
    problems: list[str] = []
    missing = [item for item in ledger.facts if item.required and not item.value]
    for item in missing:
        problems.append(f"缺少必填事实：{item.label}（{item.key}）")
    if not any(item.value for item in ledger.facts):
        problems.append("没有任何可核查事实，无法生成有依据的正文")
    return problems


def summarize_results(results: list[CheckResult]) -> dict[str, Any]:
    by_level: dict[str, int] = {}
    for result in results:
        if result.passed:
            continue
        by_level[result.level] = by_level.get(result.level, 0) + 1
    blocking = by_level.get("blocking", 0)
    return {
        "total": len(results),
        "failed": sum(by_level.values()),
        "by_level": by_level,
        "verdict": "blocked" if blocking else ("warn" if by_level else "pass"),
    }
