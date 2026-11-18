"""真实招标文件回归校准。

对一个目录里的招标文件逐份执行「解析 → 字段/表格/材料抽取」，只读文件系统，
不写数据库、不创建项目、不调用模型。输出结构化报告，供补充别名或规则包使用。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.table import Table

from .ledger import build_ledger, ledger_summary
from .parsing.extract import extract
from .parsing.loader import ParsedDocument, load_document
from .rules.loader import Package, load_package

logger = logging.getLogger("bidcraft.calibrate")

# loader.load_document 支持的后缀；loader 目前未导出 SUPPORTED 常量，这里保持一致。
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".markdown", ".xlsx", ".xlsm"}

FALLBACK_PACKAGE = "common-bid-facts"
CALIBRATION_PACKAGES = (
    "weakcurrent-cabling",
    "fire-protection",
    "installation",
    "civil",
    "hvac",
    "decoration",
)


def _load_calibration_packages() -> tuple[list[Package], Package]:
    packages: list[Package] = []
    for name in CALIBRATION_PACKAGES:
        try:
            packages.append(load_package(name))
        except Exception:  # noqa: BLE001, S112 - 单个规则包损坏不应阻断校准
            logger.warning("校准跳过损坏的规则包 %s", name, exc_info=True)
            continue
    return packages, load_package(FALLBACK_PACKAGE)


def _select_package(text: str, packages: list[Package], fallback: Package) -> Package:
    """按规则包 keywords 的词频选行业包；无命中时回落到通用事实包。"""
    best: Package | None = None
    best_score = 0
    for package in packages:
        score = sum(
            text.count(keyword) * max(1, len(keyword))
            for keyword in package.keywords()
            if keyword
        )
        if score > best_score:
            best, best_score = package, score
    return best or fallback


def _parse_warnings(parsed: ParsedDocument) -> list[str]:
    meta = parsed.meta
    warnings: list[str] = []
    if meta.get("pdf_error"):
        warnings.append(f"PDF 解析失败：{meta['pdf_error']}")
    if meta.get("ocr_error"):
        warnings.append(f"OCR 失败：{meta['ocr_error']}")
    if meta.get("ocr_unavailable"):
        warnings.append(f"OCR 不可用：{meta['ocr_unavailable']}")
    if meta.get("ocr_warnings"):
        warnings.extend(str(item) for item in meta["ocr_warnings"])
    if meta.get("needs_ocr"):
        warnings.append("未提取到文本，可能需要 OCR")
    if meta.get("blank_pages"):
        warnings.append(f"存在 {meta['blank_pages']} 个空白页")
    return warnings


def _needs_ocr(parsed: ParsedDocument) -> bool:
    if parsed.meta.get("needs_ocr"):
        return True
    return not parsed.text.strip() and not parsed.tables


def _parse_error(parsed: ParsedDocument) -> str | None:
    """把 loader 吞掉的不可读文件显式记为 error；纯扫描件仍走 needs_ocr。"""
    if parsed.text.strip() or parsed.tables:
        return None
    meta = parsed.meta
    reason = meta.get("pdf_error") or meta.get("ocr_error") or meta.get("ocr_unavailable")
    if reason:
        return f"文件解析失败：{reason}"
    if meta.get("needs_ocr"):
        return None
    return "未提取到文本或表格"


def _calibrate_file(
    path: Path, *, root: Path, packages: list[Package], fallback: Package
) -> dict[str, Any]:
    display = path.relative_to(root).as_posix()
    try:
        parsed = load_document(path)
    except Exception as exc:  # noqa: BLE001 - 单文件异常不得中断整目录
        logger.warning("校准无法解析文件 %s", display, exc_info=True)
        return {
            "file": display,
            "pages": 0,
            "tables": 0,
            "materials": 0,
            "facts_filled": 0,
            "facts_required": 0,
            "facts_missing_required": 0,
            "warnings": [],
            "needs_ocr": False,
            "error": f"{type(exc).__name__}: {exc}",
            "package": None,
        }

    warnings = _parse_warnings(parsed)
    needs_ocr = _needs_ocr(parsed)
    error = _parse_error(parsed)

    if error:
        return {
            "file": display,
            "pages": parsed.page_count,
            "tables": len(parsed.tables),
            "materials": 0,
            "facts_filled": 0,
            "facts_required": 0,
            "facts_missing_required": 0,
            "warnings": warnings,
            "needs_ocr": needs_ocr,
            "error": error,
            "package": None,
        }

    try:
        package = _select_package(parsed.text, packages, fallback)
        extracted = extract(
            parsed.text, package, pages=parsed.pages, tables=parsed.tables
        )
        ledger = build_ledger(package, extracted, source_filename=display)
        summary = ledger_summary(ledger)
    except Exception as exc:  # noqa: BLE001 - 抽取失败同样只记录单文件错误
        logger.warning("校准抽取失败 %s", display, exc_info=True)
        return {
            "file": display,
            "pages": parsed.page_count,
            "tables": len(parsed.tables),
            "materials": 0,
            "facts_filled": 0,
            "facts_required": 0,
            "facts_missing_required": 0,
            "warnings": warnings,
            "needs_ocr": needs_ocr,
            "error": f"{type(exc).__name__}: {exc}",
            "package": None,
        }

    return {
        "file": display,
        "pages": parsed.page_count,
        "tables": len(parsed.tables),
        "materials": summary["materials"],
        "facts_filled": summary["fields_filled"],
        "facts_required": summary["required"],
        "facts_missing_required": summary["missing_required"],
        "warnings": warnings,
        "needs_ocr": needs_ocr,
        "error": None,
        "package": package.name,
        "_hit_keys": {fact.key for fact in ledger.facts if fact.value},
    }


def _iter_files(root: Path, recursive: bool) -> list[Path]:
    candidates = root.rglob("*") if recursive else root.iterdir()
    return sorted(
        path
        for path in candidates
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def calibrate_dir(path: str | Path, recursive: bool = False) -> dict[str, Any]:
    """校准一个招标文件目录；单文件失败写入 error，不抛出。"""
    root = Path(path).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"目录不存在：{root}")
    root = root.resolve()

    packages, fallback = _load_calibration_packages()
    per_file: list[dict[str, Any]] = []
    field_hits: dict[str, int] = {}
    required_total = 0
    required_filled = 0
    by_type: dict[str, dict[str, int]] = {}

    for file_path in _iter_files(root, recursive):
        item = _calibrate_file(
            file_path, root=root, packages=packages, fallback=fallback
        )
        hit_keys = item.pop("_hit_keys", set())
        for key in hit_keys:
            field_hits[key] = field_hits.get(key, 0) + 1
        required_total += item["facts_required"]
        required_filled += item["facts_required"] - item["facts_missing_required"]
        kind = item.get("package") or FALLBACK_PACKAGE
        bucket = by_type.setdefault(
            kind, {"files": 0, "ok": 0, "required": 0, "filled": 0}
        )
        bucket["files"] += 1
        if item["error"] is None:
            bucket["ok"] += 1
        bucket["required"] += item["facts_required"]
        bucket["filled"] += item["facts_required"] - item["facts_missing_required"]
        per_file.append(item)

    ok = sum(1 for item in per_file if item["error"] is None)
    failed = len(per_file) - ok
    ordered_hits = dict(
        sorted(field_hits.items(), key=lambda pair: (-pair[1], pair[0]))
    )
    by_type_payload = {
        name: {
            "files": bucket["files"],
            "ok": bucket["ok"],
            "required_hit_rate": (
                round(bucket["filled"] / bucket["required"], 4)
                if bucket["required"]
                else 0.0
            ),
        }
        for name, bucket in sorted(by_type.items())
    }
    aggregate = {
        "files": len(per_file),
        "ok": ok,
        "failed": failed,
        "field_hits": ordered_hits,
        "by_type": by_type_payload,
        "required_hit_rate": (
            round(required_filled / required_total, 4) if required_total else 0.0
        ),
        "avg_materials": (
            round(sum(item["materials"] for item in per_file if item["error"] is None) / ok, 2)
            if ok
            else 0.0
        ),
        "ocr_files": sum(1 for item in per_file if item["needs_ocr"]),
    }
    return {
        "root": str(root),
        "recursive": bool(recursive),
        "per_file": per_file,
        "aggregate": aggregate,
    }


BASELINE_SCHEMA = 1


def save_baseline(report: dict[str, Any], path: str | Path) -> Path:
    """保存回归基线：只存 aggregate（不含 root/per_file），保证跨机器可比。"""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": BASELINE_SCHEMA,
        "saved_at": datetime.now(UTC).isoformat(),
        "aggregate": report["aggregate"],
    }
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def load_baseline(path: str | Path) -> dict[str, Any]:
    """读取基线 JSON；文件缺失抛 FileNotFoundError，schema≠1 抛 ValueError。"""
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    schema = data.get("schema")
    if schema != BASELINE_SCHEMA:
        raise ValueError(f"不支持的基线 schema：{schema!r}（期望 {BASELINE_SCHEMA}）")
    return data


def compare_baseline(
    report: dict[str, Any], baseline_path: str | Path, *, tolerance: float = 0.02
) -> dict[str, Any]:
    """当前报告 vs 基线，只比 aggregate（忽略 root/recursive/saved_at/per_file）。

    回归判定：字段不再命中/命中数下降；必填命中率下降超过 tolerance（严格 >）；
    failed 增加 / ok 减少；avg_materials 相对下降超过 10%。
    新增字段 key 与指标上升列入 improvements。ok = 无回归。
    """
    baseline = load_baseline(baseline_path)
    base = baseline["aggregate"]
    current = report["aggregate"]
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []

    def regression(metric: str, message: str, before: Any, after: Any) -> None:
        regressions.append(
            {"metric": metric, "message": message, "baseline": before, "current": after}
        )

    def improvement(metric: str, message: str, before: Any, after: Any) -> None:
        improvements.append(
            {"metric": metric, "message": message, "baseline": before, "current": after}
        )

    base_hits = base.get("field_hits", {})
    cur_hits = current.get("field_hits", {})
    for key, count in base_hits.items():
        if key not in cur_hits:
            regression(f"field_hits.{key}", f"字段不再命中：{key}", count, 0)
        elif cur_hits[key] < count:
            now = cur_hits[key]
            regression(
                f"field_hits.{key}", f"字段命中下降：{key} {count}→{now}", count, now
            )
    for key, count in cur_hits.items():
        if key not in base_hits:
            improvement(f"field_hits.{key}", f"新增字段命中：{key}", 0, count)

    base_rate = float(base.get("required_hit_rate", 0.0))
    cur_rate = float(current.get("required_hit_rate", 0.0))
    # 先舍入再比较，避免浮点误差把恰好等于 tolerance 的下降误判为回归。
    if round(base_rate - cur_rate, 6) > tolerance:
        regression(
            "required_hit_rate",
            f"必填命中率下降：{base_rate}→{cur_rate}",
            base_rate,
            cur_rate,
        )
    elif cur_rate > base_rate:
        improvement(
            "required_hit_rate",
            f"必填命中率上升：{base_rate}→{cur_rate}",
            base_rate,
            cur_rate,
        )

    base_failed = int(base.get("failed", 0))
    cur_failed = int(current.get("failed", 0))
    if cur_failed > base_failed:
        regression("failed", f"失败文件增加：{base_failed}→{cur_failed}", base_failed, cur_failed)
    base_ok = int(base.get("ok", 0))
    cur_ok = int(current.get("ok", 0))
    if cur_ok < base_ok:
        regression("ok", f"成功文件减少：{base_ok}→{cur_ok}", base_ok, cur_ok)

    base_avg = float(base.get("avg_materials", 0.0))
    cur_avg = float(current.get("avg_materials", 0.0))
    if base_avg > 0 and round((base_avg - cur_avg) / base_avg, 6) > 0.10:
        regression(
            "avg_materials", f"平均材料下降：{base_avg}→{cur_avg}", base_avg, cur_avg
        )
    if cur_avg > base_avg:
        improvement(
            "avg_materials", f"平均材料上升：{base_avg}→{cur_avg}", base_avg, cur_avg
        )

    return {
        "ok": not regressions,
        "regressions": regressions,
        "improvements": improvements,
        "baseline_saved_at": baseline.get("saved_at"),
    }


def render_report(report: dict[str, Any]) -> Table:
    """Render the per-file calibration table for the CLI."""
    table = Table(title="招标文件回归校准")
    table.add_column("文件", overflow="fold")
    table.add_column("行业包", overflow="fold")
    table.add_column("页", justify="right")
    table.add_column("表", justify="right")
    table.add_column("材料", justify="right")
    table.add_column("事实(已填/必填)", justify="right")
    table.add_column("必填缺失", justify="right")
    table.add_column("OCR")
    table.add_column("状态", overflow="fold")

    for item in report.get("per_file", []):
        if item.get("error"):
            status = f"[red]错误：{item['error']}[/red]"
        elif item.get("needs_ocr"):
            status = "[yellow]需 OCR[/yellow]"
        elif item.get("warnings"):
            status = f"[yellow]{len(item['warnings'])} 条警告[/yellow]"
        else:
            status = "[green]OK[/green]"
        table.add_row(
            str(item.get("file", "")),
            str(item.get("package") or "-"),
            str(item.get("pages", 0)),
            str(item.get("tables", 0)),
            str(item.get("materials", 0)),
            f"{item.get('facts_filled', 0)}/{item.get('facts_required', 0)}",
            str(item.get("facts_missing_required", 0)),
            "是" if item.get("needs_ocr") else "",
            status,
        )

    aggregate = report.get("aggregate", {})
    table.add_section()
    table.add_row(
        f"合计 {aggregate.get('files', 0)} 个文件",
        "",
        "",
        "",
        f"平均 {aggregate.get('avg_materials', 0)}",
        f"必填命中率 {float(aggregate.get('required_hit_rate', 0)):.1%}",
        "",
        str(aggregate.get("ocr_files", 0)),
        f"成功 {aggregate.get('ok', 0)} / 失败 {aggregate.get('failed', 0)}",
    )
    return table
