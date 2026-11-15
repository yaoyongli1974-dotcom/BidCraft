"""Excel delivery: fact ledger, bill of materials, parameters, scoring and risks.

The workbook is intended for internal review and for aligning the technical bid
with the tender's bill of quantities; values come from the fact ledger only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..schemas import FactLedger

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def _add_sheet(workbook: Workbook, title: str, headers: list[str], rows: list[list[Any]]) -> None:
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in rows:
        sheet.append(row)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for index, header in enumerate(headers, start=1):
        width = min(48, max(10, len(header) * 2 + 4))
        if header in {"内容", "评分标准", "规格型号", "出处", "响应值/要求"}:
            width = 52
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _facts_rows(ledger: FactLedger) -> list[list[Any]]:
    rows = []
    for item in ledger.facts:
        rows.append(
            [
                item.key,
                item.label,
                item.value,
                item.unit,
                item.status,
                "是" if item.required else "否",
                item.source.page,
                item.source.quote,
            ]
        )
    return rows


def _material_rows(ledger: FactLedger) -> list[list[Any]]:
    rows = []
    for index, item in enumerate(ledger.materials, start=1):
        rows.append([index, item.name, item.spec, item.unit, item.qty, item.note])
    return rows


def _parameter_rows(ledger: FactLedger) -> list[list[Any]]:
    rows = []
    for index, item in enumerate(ledger.parameters, start=1):
        rows.append(
            [
                index,
                item.name,
                item.value,
                item.unit,
                item.requirement,
                "是" if item.mandatory else "否",
                item.category,
            ]
        )
    return rows


def _scoring_rows(ledger: FactLedger) -> list[list[Any]]:
    rows = []
    for index, item in enumerate(ledger.scoring, start=1):
        rows.append([index, item.name, item.score, item.criteria, item.section_hint])
    return rows


def _risk_rows(ledger: FactLedger) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for item in ledger.requirements:
        rows.append(["实质性要求", item.kind, item.text, item.source_quote])
    for item in ledger.risks:
        rows.append(["废标/风险", item.level, item.text, item.source_quote])
    return rows


def export_xlsx(path: str | Path, ledger: FactLedger) -> Path:
    workbook = Workbook()
    workbook.remove(workbook.active)

    _add_sheet(
        workbook,
        "事实台账",
        ["key", "字段", "取值", "单位", "状态", "必填", "页码", "原文出处"],
        _facts_rows(ledger),
    )
    _add_sheet(
        workbook,
        "材料清单",
        ["序号", "名称", "规格型号", "单位", "数量", "备注"],
        _material_rows(ledger),
    )
    _add_sheet(
        workbook,
        "技术参数",
        ["序号", "参数", "响应值", "单位", "招标要求", "强制", "类别"],
        _parameter_rows(ledger),
    )
    _add_sheet(
        workbook,
        "评分响应",
        ["序号", "评分因素", "分值", "评分标准", "建议章节"],
        _scoring_rows(ledger),
    )
    _add_sheet(
        workbook,
        "风险与要求",
        ["类型", "级别/类别", "内容", "出处"],
        _risk_rows(ledger),
    )

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(output))
    return output
