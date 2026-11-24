"""Bandit 结果门禁：只放行已登记的精确豁免，其余中/高危发现一律失败。

用法（两种等价方式）：:

    uvx --from bandit bandit -q -ll -f json -r bidcraft -o /tmp/bandit.json
    uv run --frozen python scripts/bandit_gate.py /tmp/bandit.json

    uvx --from bandit bandit -q -ll -f json -r bidcraft | \
        uv run --frozen python scripts/bandit_gate.py -

门禁自己做 MEDIUM/HIGH 过滤，等价于 bandit 的 ``-ll`` 阈值；LOW 发现不参与判定。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_REPORT = "/tmp/bandit.json"

# 精确豁免表：(相对仓库根的文件名, 行号, 规则 ID, 中文理由)。
# 只有四项完全匹配才放行；行号漂移或新增发现都会让门禁失败。
ALLOWED: frozenset[tuple[str, int, str, str]] = frozenset(
    {
        (
            "bidcraft/migrations/versions/0009_v028_schema_parity.py",
            89,
            "B608",
            "Alembic 迁移静态 DDL，JSON 取值表达式按方言固定，无外部输入。",
        ),
        (
            "bidcraft/migrations/versions/0010_batch1_billing_reliability.py",
            59,
            "B608",
            "Alembic 迁移静态 DDL，COALESCE 片段按方言固定，无外部输入。",
        ),
    }
)

# 与 bandit -ll 相同的阈值语义：只判定中危和高危。
GATED_SEVERITIES = frozenset({"MEDIUM", "HIGH"})


class GateError(Exception):
    """报告缺失、无法解析或结构不符合预期。"""


def _read_report(report: str) -> str:
    if report == "-":
        return sys.stdin.read()
    try:
        return Path(report).read_text(encoding="utf-8")
    except OSError as exc:
        raise GateError(f"无法读取 Bandit 报告 {report}: {exc}") from exc


def _extract_json(text: str) -> dict:
    """从第一个 '{' 开始解析，容忍 uvx/进度日志前缀及尾部杂讯。"""
    start = text.find("{")
    if start < 0:
        raise GateError("报告中没有 JSON（未生成报告或只有进度日志）")
    try:
        payload, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise GateError(f"Bandit JSON 解析失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise GateError("Bandit JSON 顶层不是对象，疑似不是 bandit -f json 的输出")
    return payload


def _normalize_filename(filename: object) -> str:
    """统一成 bandit 在仓库根运行时的相对 POSIX 路径，不做 basename 模糊匹配。"""
    path = str(filename or "").replace("\\", "/")
    path = path.removeprefix("./")
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            path = candidate.relative_to(Path.cwd()).as_posix()
        except ValueError:
            pass
    return path


def _line_number(item: dict) -> int:
    try:
        return int(item.get("line_number", -1))
    except (TypeError, ValueError):
        return -1


def _check(report: dict) -> int:
    errors = report.get("errors")
    if errors:
        print("BANDIT GATE FAILED: Bandit 扫描报错：", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 2

    results = report.get("results")
    if not isinstance(results, list):
        print(
            "BANDIT GATE FAILED: 报告缺少 results 列表，确认使用 bandit -f json 输出。",
            file=sys.stderr,
        )
        return 2

    reasons = {(filename, line, test_id): reason for filename, line, test_id, reason in ALLOWED}
    if len(reasons) != len(ALLOWED):
        print("BANDIT GATE FAILED: ALLOWED 中存在重复的 (文件, 行号, 规则) 键。", file=sys.stderr)
        return 2

    findings: list[tuple[tuple[str, int, str], dict]] = []
    for item in results:
        if not isinstance(item, dict):
            print("BANDIT GATE FAILED: results 中出现非对象条目。", file=sys.stderr)
            return 2
        severity = str(item.get("issue_severity", "")).upper()
        if severity not in GATED_SEVERITIES:
            continue
        key = (
            _normalize_filename(item.get("filename")),
            _line_number(item),
            str(item.get("test_id", "")),
        )
        findings.append((key, item))

    violations: list[tuple[tuple[str, int, str], dict]] = []
    waived: list[tuple[tuple[str, int, str], dict]] = []
    used: set[tuple[str, int, str]] = set()

    print(f"Bandit gate：中/高危发现 {len(findings)} 项，精确豁免 {len(reasons)} 项。")
    for key, item in findings:
        filename, line, test_id = key
        severity = str(item.get("issue_severity", "")).upper()
        location = f"{filename}:{line} [{test_id}/{severity}]"
        if key in reasons:
            waived.append((key, item))
            used.add(key)
            print(f"  [豁免] {location} — {reasons[key]}")
        else:
            violations.append((key, item))
            text = str(item.get("issue_text", ""))
            print(f"  [违规] {location} — {text}")

    unused = set(reasons) - used
    if unused:
        print("  提示：以下豁免本次未命中，请确认行号是否漂移：")
        for filename, line, test_id in sorted(unused):
            print(f"    - {filename}:{line} [{test_id}]")

    if violations:
        print(
            f"BANDIT GATE FAILED: {len(violations)} 项中/高危发现不在 ALLOWED 中；"
            "请修复或提交带理由的精确豁免。",
            file=sys.stderr,
        )
        return 1

    print(
        f"BANDIT GATE OK: {len(waived)} 项中/高危发现全部在 ALLOWED 中"
        "（低危不参与判定，-ll 阈值不变）。"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bandit 中/高危结果的精确豁免门禁")
    parser.add_argument(
        "report",
        nargs="?",
        default=DEFAULT_REPORT,
        help=f"bandit -f json 报告路径（默认 {DEFAULT_REPORT}，- 表示 stdin）",
    )
    args = parser.parse_args(argv)

    try:
        report = _extract_json(_read_report(args.report))
    except GateError as exc:
        print(f"BANDIT GATE FAILED: {exc}", file=sys.stderr)
        return 2
    return _check(report)


if __name__ == "__main__":
    raise SystemExit(main())
