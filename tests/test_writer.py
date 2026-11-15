from __future__ import annotations

from bidcraft.generation.writer import dedupe_body


def test_dedupe_preserves_markdown_structure() -> None:
    body = (
        "第一段保留。\n\n"
        "| 项目 | 取值 |\n| --- | --- |\n| 工期 | 90 日历天 |\n\n"
        "本章依据招标文件编制，正文口径与事实台账保持一致，不得虚构任何业绩或证书。"
    )
    previous = ["本章依据招标文件编制，正文口径与事实台账保持一致，不得虚构任何业绩或证书。"]
    result = dedupe_body(body, previous)
    assert "| 项目 | 取值 |" in result
    assert "| --- | --- |" in result
    assert "| 工期 | 90 日历天 |" in result
    assert result.count("\n") >= 4
    assert "重复段落" not in result


def test_dedupe_without_history_is_identity() -> None:
    body = "a\n\nb"
    assert dedupe_body(body, []) == body
