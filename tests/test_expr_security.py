"""B307 抑制的行为证据：evaluate 只执行白名单 AST，恶意表达式不产生任何副作用。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bidcraft.rules.expr import ExpressionError, evaluate, number


class _Tripwire:
    """一旦被属性访问/调用/下标触碰就记录并抛错，证明表达式从未真正执行。"""

    def __init__(self) -> None:
        self.touched: list[str] = []

    def __getattribute__(self, name: str):
        if name not in {"touched", "_touch"}:
            object.__getattribute__(self, "_touch")(f"attribute:{name}")
        return object.__getattribute__(self, name)

    def _touch(self, what: str) -> None:
        object.__getattribute__(self, "touched").append(what)
        raise AssertionError(f"恶意表达式触碰了 {what}：白名单被绕过并已执行")

    def __call__(self, *args, **kwargs):
        self._touch("call")

    def __getitem__(self, key):
        self._touch("subscript")


_MALICIOUS = [
    pytest.param("__import__('os')", id="import"),
    pytest.param("open({canary!r}, 'w')", id="open"),
    pytest.param("().__class__.__bases__[0].__subclasses__()", id="attribute-traversal"),
    pytest.param("{probe}.__class__", id="probe-attribute"),
    pytest.param("(lambda: 1)()", id="lambda"),
    pytest.param("[x for x in {probe}]", id="comprehension"),
    pytest.param("[x for x in (1, 2) if {probe}.flag]", id="comprehension-attribute"),
    pytest.param("__builtins__['eval']", id="dunder-builtins"),
    pytest.param("__loader__", id="dunder-name"),
    pytest.param("1+" * 1001 + "1", id="overlong-2003-chars"),
]


@pytest.mark.parametrize("template", _MALICIOUS)
def test_malicious_expression_rejected_without_side_effects(template: str, tmp_path: Path) -> None:
    canary = tmp_path / "pwned.txt"
    tripwire = _Tripwire()
    payload = [1, 2, 3]
    # 这些名字即使出现在 context 里也绝不应被调用；open 的 canary 文件也不能被创建。
    context = {"probe": tripwire, "open": tripwire, "__import__": tripwire, "payload": payload}
    expression = template.format(canary=str(canary), probe="probe")

    with pytest.raises(ExpressionError):
        evaluate(expression, context)

    assert tripwire.touched == []
    assert not canary.exists()
    assert payload == [1, 2, 3]


def test_legitimate_expression_still_evaluates() -> None:
    context = {
        "facts": {"budget": "350 万元", "price": "320 万元"},
        "doc": "投标有效期 90 天",
    }
    assert number(context["facts"]["budget"]) == 3500000
    assert evaluate("number(facts['budget']) >= number(facts['price'])", context) is True
    assert evaluate("contains(doc, '90 天')", context) is True
    assert evaluate("not contains(doc, '180 天')", context) is True
