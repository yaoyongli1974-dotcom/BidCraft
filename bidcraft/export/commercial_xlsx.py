"""商务标 Excel 交付：投标函、开标一览、分项报价、资格、人员、偏离。

All values come from the assembled payload (:mod:`bidcraft.commercial`), which
only reads approved quote versions and existing ledgers. Money cells use a
thousand-separator two-decimal format; placeholders stay visible so a missing
field is never silently dropped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook

from .xlsx import _add_sheet

MONEY_KEYS = {
    "bid_amount",
    "total_amount",
    "tax_amount",
    "total_with_tax",
    "cny_total_with_tax",
    "unit_price",
    "amount",
}

SHEET_ORDER = (
    ("bid_letter", "投标函"),
    ("opening_sheet", "开标一览表"),
    ("price_schedule", "分项报价表"),
    ("qualification_checklist", "资格响应表"),
    ("performance_table", "业绩表"),
    ("personnel_table", "人员表"),
    ("deviation_table", "商务偏离表"),
)

MONEY_FORMAT = "#,##0.00"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _currency_label(currency: Any) -> str:
    code = _text(currency).strip()
    return f"{code} / 人民币" if code and code != "CNY" else "人民币（CNY）"


def _info_rows(payload: dict[str, Any]) -> list[list[Any]]:
    doc = payload.get("doc") or {}
    quote = payload.get("quote") or {}
    project = payload.get("project") or {}
    package = payload.get("package") or {}
    validation = payload.get("validation") or {}
    rows: list[list[Any]] = [
        ["文档名称", _text(doc.get("name")) or "商务标"],
        ["文档状态", _text(doc.get("status")) or "draft"],
        ["项目名称", _text(project.get("name"))],
        ["项目编号", _text(project.get("number"))],
        ["包件", _text(package.get("name")) or "整体项目"],
        ["报价版本", _text(quote.get("name")) or "{{待填:已审批报价版本}}"],
        ["版本状态", _text(quote.get("status")) or "{{待填:报价版本状态}}"],
        ["审批人", _text(quote.get("reviewed_by")) or "{{待填:审批人}}"],
        ["审批时间", _text(quote.get("reviewed_at")) or "{{待填:审批时间}}"],
        ["报价合计（元）", quote.get("total_amount")],
        ["含税合计（元）", quote.get("total_with_tax")],
        ["税率", quote.get("tax_rate")],
        ["币种", _currency_label(quote.get("currency"))],
        ["校验结论", "通过" if validation.get("ok") else "未通过"],
    ]
    for error in validation.get("errors") or []:
        rows.append(["校验问题", error])
    return rows


def _field_rows(section: dict[str, Any]) -> list[list[Any]]:
    rows = [
        [entry.get("label") or entry.get("key") or "", entry.get("value")]
        for entry in section.get("fields") or []
        if isinstance(entry, dict)
    ]
    return rows


def _table_rows(section: dict[str, Any]) -> tuple[list[str], list[str], list[list[Any]]]:
    columns = [entry for entry in section.get("columns") or [] if isinstance(entry, dict)]
    headers = [entry.get("label") or entry.get("key") or "" for entry in columns]
    keys = [str(entry.get("key") or "") for entry in columns]
    rows = [
        [row.get(key) for key in keys]
        for row in section.get("rows") or []
        if isinstance(row, dict)
    ]
    return headers, keys, rows


def _format_column(sheet, index: int, column_key: str, header: str) -> None:
    money = column_key in MONEY_KEYS or "（元）" in header
    for cell in sheet.iter_cols(min_col=index, max_col=index, min_row=2):
        for item in cell:
            if money:
                item.number_format = MONEY_FORMAT


def _write_fields(workbook: Workbook, title: str, section: dict[str, Any]) -> None:
    rows = _field_rows(section)
    _add_sheet(workbook, title, ["字段", "内容"], rows)
    sheet = workbook[title]
    for index, row in enumerate(rows, start=2):
        label = _text(row[0])
        if "（元）" in label or label in {"税率"}:
            sheet.cell(row=index, column=2).number_format = (
                MONEY_FORMAT if "（元）" in label else "0.00%"
            )


def _write_table(workbook: Workbook, title: str, section: dict[str, Any]) -> None:
    headers, keys, rows = _table_rows(section)
    _add_sheet(workbook, title, headers or ["内容"], rows)
    sheet = workbook[title]
    for index, (key, header) in enumerate(zip(keys, headers, strict=False), start=1):
        _format_column(sheet, index, key, header)


def export_commercial_xlsx(path: str | Path, payload: dict[str, Any]) -> Path:
    """Write the commercial response workbook and return its path."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    _add_sheet(workbook, "商务标信息", ["项目", "内容"], _info_rows(payload))
    info = workbook["商务标信息"]
    for row in info.iter_rows(min_row=2):
        label = _text(row[0].value)
        if "（元）" in label:
            row[1].number_format = MONEY_FORMAT
        elif label == "税率":
            row[1].number_format = "0.00%"
    for key, title in SHEET_ORDER:
        section = (payload.get("sections") or {}).get(key) or {}
        if key == "bid_letter" or section.get("kind") == "letter":
            _write_fields(workbook, title, section)
        else:
            _write_table(workbook, title, section)
        if key == "price_schedule":
            totals = section.get("totals") or {}
            if totals.get("total_amount") is not None:
                workbook[title].append(
                    ["", "合计", "", "", "", "", totals["total_amount"], ""]
                )
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(output))
    return output
