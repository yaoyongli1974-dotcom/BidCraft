from __future__ import annotations

import csv
from pathlib import Path

import pytest

from bidcraft.tender_stats import (
    DataAuditError,
    analyze_bids,
    analyze_reasons,
    audit_file,
    classify_reason,
    render_bids,
    render_reasons,
)


def _write_csv(tmp_path: Path, name: str, header: list[str], rows: list[list[str]]) -> Path:
    path = tmp_path / name
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def test_classify_reason_prefers_hard_conditions() -> None:
    assert classify_reason("资格审查不合格") == "资格条件不符"
    assert classify_reason("投标报价超过最高限价") == "报价超预算"
    assert classify_reason("未按要求密封") == "投标文件不合规"
    assert classify_reason("技术参数负偏离") == "技术参数不响应"
    assert classify_reason("有效投标人不足三家") == "程序性废标"
    assert classify_reason("投标文件雷同") == "报价异常/围标嫌疑"
    assert classify_reason("项目经理未到场") == "其他"
    assert classify_reason("") == ""


def test_analyze_reasons_counts_and_examples(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "reasons.csv",
        ["标段编号", "投标人名称", "废标原因"],
        [
            ["A", "甲", "资格审查不合格"],
            ["A", "乙", "报价超过最高限价"],
            ["B", "丙", "未按要求密封"],
            ["B", "丁", ""],
        ],
    )
    report = analyze_reasons(path)
    assert report.total == 4
    assert report.empty == 1
    assert report.categories["资格条件不符"] == 1
    assert report.categories["报价超预算"] == 1
    assert report.categories["投标文件不合规"] == 1
    assert report.examples["资格条件不符"] == ["资格审查不合格"]
    assert pytest.approx(report.coverage) == 0.75


def test_analyze_reasons_alias_column(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, "alias.csv", ["XMFBDYY"], [["有效投标人不足三家"]])
    report = analyze_reasons(path)
    assert report.categories["程序性废标"] == 1


def test_analyze_reasons_missing_column(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, "bad.csv", ["标段编号"], [["A"]])
    with pytest.raises(DataAuditError, match="未找到废标原因列"):
        analyze_reasons(path)


def test_render_reasons_markdown() -> None:
    from bidcraft.tender_stats import ReasonReport

    report = ReasonReport(total=2, empty=0)
    report.categories["资格条件不符"] = 2
    report.examples["资格条件不符"] = ["资格审查不合格"]
    text = render_reasons(report)
    assert "废标原因归因报告" in text
    assert "| 资格条件不符 | 2 | 100.0% |" in text


def test_analyze_bids_groups_lots(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "bids.csv",
        ["标段编号", "投标人名称", "投标报价金额"],
        [
            ["A", "甲", "1,000,000.00"],
            ["A", "乙", "1,005,000.00"],
            ["A", "丙", "1,002,000.00"],
            ["B", "甲", "800000"],
            ["B", "乙", "950000"],
        ],
    )
    lots = analyze_bids(path)
    assert [lot.lot for lot in lots] == ["A", "B"]
    first = lots[0]
    assert first.prices == [1_000_000.0, 1_005_000.0, 1_002_000.0]
    assert first.variation is not None and first.variation < 0.01
    assert first.suspicious is True
    assert lots[1].suspicious is False


def test_analyze_bids_requires_price_column(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, "nop.csv", ["标段编号"], [["A"]])
    with pytest.raises(DataAuditError, match="未找到报价列"):
        analyze_bids(path)


def test_render_bids_flags_identical_prices() -> None:
    from bidcraft.tender_stats import BidLot

    lots = [BidLot(lot="A", prices=[100.0, 100.0, 100.0], bidders=["甲", "乙", "丙"])]
    text = render_bids(lots)
    assert "报价高度一致" in text
    assert "1 个标段" in text


def test_audit_file_json_shape(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, "r.csv", ["废标原因"], [["资格审查不合格"]])
    result = audit_file(path, "reasons")
    assert result["mode"] == "reasons"
    assert result["counts"] == {"资格条件不符": 1}
    assert "废标原因归因报告" in result["markdown"]


def test_audit_file_unknown_mode(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, "r.csv", ["废标原因"], [["x"]])
    with pytest.raises(DataAuditError, match="未知模式"):
        audit_file(path, "nope")


def test_unsupported_suffix(tmp_path: Path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF")
    with pytest.raises(DataAuditError, match="不支持的文件类型"):
        audit_file(path, "reasons")
