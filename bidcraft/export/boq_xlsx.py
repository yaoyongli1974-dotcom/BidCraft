"""工程量清单响应表：对齐结果 + 报价汇总 + 差异汇总。

Rows come from the alignment payload built by :mod:`bidcraft.boq`. Unit prices
are user-entered; no price, brand or model number is generated here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import PatternFill

from .xlsx import _add_sheet

STATUS_LABELS = {
    "matched": "一致",
    "qty_diff": "数量差异",
    "unit_diff": "单位差异",
    "spec_diff": "规格差异",
    "missing_in_ledger": "清单有·台账无",
    "missing_in_boq": "台账有·清单无",
}

STATUS_FILLS = {
    "matched": "C6EFCE",
    "qty_diff": "FCE4D6",
    "unit_diff": "FCE4D6",
    "spec_diff": "FCE4D6",
    "missing_in_ledger": "FFC7CE",
    "missing_in_boq": "FFEB9C",
}
IGNORED_FILL = "D9D9D9"

ALIGN_HEADERS = [
    "序号",
    "清单行",
    "名称",
    "清单规格",
    "清单单位",
    "清单数量",
    "单价（元）",
    "合价（元）",
    "台账规格",
    "台账单位",
    "台账数量",
    "数量偏差",
    "状态",
    "匹配度",
    "备注",
]

SUMMARY_LABELS = [
    ("清单条目", "boq_rows"),
    ("已匹配", "matched"),
    ("数量差异", "qty_diff"),
    ("单位差异", "unit_diff"),
    ("规格差异", "spec_diff"),
    ("清单有台账无", "missing_in_ledger"),
    ("台账有清单无", "missing_in_boq"),
    ("已忽略", "ignored"),
    ("差异合计", "total_diffs"),
]

QUOTE_LABELS = [
    ("清单条目", "boq_rows"),
    ("已报价", "priced"),
    ("未报价", "unpriced"),
    ("缺数量", "quantity_missing"),
    ("报价合计（元）", "total_amount"),
    ("已忽略", "ignored"),
]


def _alignment_rows(payload: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for row in payload.get("rows", []):
        ledger = row.get("ledger") or {}
        diff = row.get("diff") or {}
        qty_diff = diff.get("qty") or {}
        price = row.get("price")
        if not isinstance(price, dict):
            price = {}
        spec = ledger.get("spec", "") if ledger else ""
        unit = ledger.get("unit", "") if ledger else ""
        qty = ledger.get("qty") if ledger else None
        status = STATUS_LABELS.get(row.get("status", ""), row.get("status", ""))
        if row.get("ignored"):
            status = f"{status}（已忽略）"
        rows.append(
            [
                row.get("seq") or "",
                row.get("boq_row") if row.get("boq_row") is not None else "",
                row.get("name") or "",
                row.get("spec") or "",
                row.get("unit") or "",
                row.get("qty"),
                price.get("unit_price"),
                price.get("amount"),
                spec,
                unit,
                qty,
                qty_diff.get("delta") if qty_diff else "",
                status,
                round(float(row.get("match_score") or 0.0), 3),
                row.get("note") or "",
            ]
        )
    return rows


def _summary_rows(payload: dict[str, Any]) -> list[list[Any]]:
    summary = payload.get("summary") or {}
    rows: list[list[Any]] = [
        [label, summary.get(key, 0)] for label, key in SUMMARY_LABELS
    ]
    rows.append(["匹配率", summary.get("match_rate", 0.0)])
    return rows


def _quote_rows(payload: dict[str, Any]) -> list[list[Any]]:
    summary = payload.get("summary") or {}
    rows: list[list[Any]] = [
        [label, summary.get(key, 0)] for label, key in QUOTE_LABELS
    ]
    policy = payload.get("price_policy") or {}
    if policy.get("enabled"):
        rows.append(["调价系数", policy.get("factor", 1.0)])
        rows.append(["调整后合计（元）", summary.get("adjusted_total")])
    return rows


def export_boq_xlsx(path: str | Path, payload: dict[str, Any]) -> Path:
    """Write the response workbook and return its path."""
    workbook = Workbook()
    workbook.remove(workbook.active)

    rows = _alignment_rows(payload)
    _add_sheet(workbook, "清单对齐", ALIGN_HEADERS, rows)
    sheet = workbook["清单对齐"]
    for index, row in enumerate(payload.get("rows", []), start=2):
        for column in (7, 8):
            sheet.cell(row=index, column=column).number_format = "#,##0.00"
        if row.get("ignored"):
            color = IGNORED_FILL
        else:
            color = STATUS_FILLS.get(row.get("status", ""))
        if color is None:
            continue
        fill = PatternFill("solid", fgColor=color)
        for cell in sheet[index][: len(ALIGN_HEADERS)]:
            cell.fill = fill

    _add_sheet(workbook, "报价汇总", ["指标", "数值"], _quote_rows(payload))
    quote = workbook["报价汇总"]
    for row in quote.iter_rows(min_row=2):
        if row[0].value in {"报价合计（元）", "调整后合计（元）"}:
            row[1].number_format = "#,##0.00"

    _add_sheet(workbook, "差异汇总", ["指标", "数量"], _summary_rows(payload))

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(output))
    return output
