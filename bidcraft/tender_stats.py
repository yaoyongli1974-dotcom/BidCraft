"""公共资源交易开放数据分析：废标原因归因与开标报价离散度。

只读分析用户下载的 CSV/XLSX（山东公共数据开放网、全国公共资源交易平台等
「无条件开放」数据集），不联网、不写库。输出 Markdown/JSON 报告，用于：

* 把「废标原因」字段整理成分类分布，反推 blocking 级核对规则的覆盖面；
* 对同一标段的投标报价做离散度统计，标出异常聚集（围标嫌疑的弱信号）。

字段识别容忍中英文表头与常见别名，缺失列时给出明确错误。
"""

from __future__ import annotations

import csv
import io
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 废标原因分类：关键词命中即归类，顺序即优先级（先判硬性资格，再判文件瑕疵）。
REASON_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("资格条件不符", ("资格", "资质", "营业执照", "许可证", "业绩", "人员", "社保", "信用", "失信")),
    ("报价超预算", ("超预算", "超过预算", "最高限价", "报价过高", "超出控制价", "控制价")),
    ("保证金问题", ("保证金", "保函", "担保")),
    ("投标文件不合规", ("未按要求", "未按招标文件", "格式", "签字", "盖章", "密封", "装订", "缺页", "缺少")),
    ("技术参数不响应", ("技术参数", "不响应", "负偏离", "实质性", "★", "星号")),
    ("工期/质量承诺不符", ("工期", "交货期", "质量", "保修", "承诺")),
    ("报价异常/围标嫌疑", ("围标", "串标", "串通", "异常一致", "雷同", "同一")),
    ("程序性废标", ("不足三家", "有效投标人", "解密", "上传失败", "逾期", "迟到", "撤回")),
)

REASON_ALIASES = ("废标原因", "废标理由", "无效原因", "无效投标原因", "XMFBDYY", "原因", "备注")
BID_ALIASES = {
    "lot": ("标段编号", "标段", "包号", "包件", "标包", "bid_section", "lot"),
    "price": ("投标报价金额", "投标报价", "报价", "price", "amount"),
    "bidder": ("投标人名称", "投标人", "投标单位", "供应商名称", "bidder"),
}


class DataAuditError(ValueError):
    """输入文件不可用或缺少必需列。"""


@dataclass
class ReasonReport:
    total: int = 0
    empty: int = 0
    categories: Counter = field(default_factory=Counter)
    examples: dict[str, list[str]] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return (self.total - self.empty) / self.total if self.total else 0.0


def _read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """读取 CSV/XLSX 为统一表头+行字典，容忍 BOM 与 GB18030。"""
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt"}:
        raw = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise DataAuditError("无法识别文件编码（支持 UTF-8/GB18030）")
        reader = csv.DictReader(io.StringIO(text))
        rows = [{str(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]
        return list(reader.fieldnames or []), rows
    if suffix in {".xlsx", ".xlsm"}:
        try:
            from openpyxl import load_workbook
        except ImportError as exc:  # pragma: no cover - 依赖缺失时给出中文提示
            raise DataAuditError("读取 XLSX 需要 openpyxl") from exc
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet = workbook.active
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header = [str(cell or "").strip() for cell in next(rows_iter)]
        except StopIteration as exc:
            raise DataAuditError("表格为空") from exc
        rows: list[dict[str, str]] = []
        for values in rows_iter:
            if values is None or all(value is None for value in values):
                continue
            rows.append(
                {
                    header[index]: ("" if value is None else str(value).strip())
                    for index, value in enumerate(values)
                    if index < len(header)
                }
            )
        workbook.close()
        return header, rows
    raise DataAuditError(f"不支持的文件类型：{suffix}（支持 .csv/.txt/.xlsx）")


def _pick(header: Iterable[str], aliases: Iterable[str]) -> str | None:
    """按别名优先、包含匹配次之的方式选列。"""
    header = list(header)
    for alias in aliases:
        if alias in header:
            return alias
    for name in header:
        if any(alias and alias in name for alias in aliases):
            return name
    return None


def classify_reason(text: str) -> str:
    """将一条废标原因归入固定类别；无法归类的记为「其他」。"""
    value = (text or "").strip()
    if not value:
        return ""
    for label, keywords in REASON_RULES:
        if any(keyword in value for keyword in keywords):
            return label
    return "其他"


def analyze_reasons(path: Path, *, limit_examples: int = 3) -> ReasonReport:
    header, rows = _read_table(path)
    column = _pick(header, REASON_ALIASES)
    if column is None:
        raise DataAuditError(f"未找到废标原因列（可识别：{'、'.join(REASON_ALIASES[:5])}）")
    report = ReasonReport()
    for row in rows:
        reason = row.get(column, "")
        report.total += 1
        if not reason.strip():
            report.empty += 1
            continue
        label = classify_reason(reason)
        report.categories[label] += 1
        report.rows.append(
            {
                "reason": reason,
                "category": label,
                "lot": row.get(_pick(header, BID_ALIASES["lot"]) or "", ""),
                "bidder": row.get(_pick(header, BID_ALIASES["bidder"]) or "", ""),
            }
        )
        bucket = report.examples.setdefault(label, [])
        if len(bucket) < limit_examples and reason not in bucket:
            bucket.append(reason)
    return report


def render_reasons(report: ReasonReport) -> str:
    lines = [
        "# 废标原因归因报告",
        "",
        f"- 记录数：{report.total}",
        f"- 空原因：{report.empty}",
        f"- 有效原因：{report.total - report.empty}（{report.coverage:.1%}）",
        "",
        "| 类别 | 条数 | 占比 | 示例 |",
        "| --- | ---: | ---: | --- |",
    ]
    baseline = max(report.total - report.empty, 1)
    for label, count in report.categories.most_common():
        example = "；".join(report.examples.get(label, [])[:1]).replace("|", "/")
        lines.append(f"| {label} | {count} | {count / baseline:.1%} | {example[:60]} |")
    lines.append("")
    lines.append("> 类别优先判定资格/报价等硬性条件，其余按文件瑕疵、技术响应、程序性依次归并；")
    lines.append("> 未命中关键词的统一记为「其他」，建议抽查后把新表述补进 REASON_RULES。")
    return "\n".join(lines)


@dataclass
class BidLot:
    lot: str
    prices: list[float]
    bidders: list[str]

    @property
    def variation(self) -> float | None:
        if len(self.prices) < 2:
            return None
        mean = statistics.fmean(self.prices)
        if mean <= 0:
            return None
        return statistics.pstdev(self.prices) / mean

    @property
    def ratio(self) -> float | None:
        low = min(self.prices) if self.prices else 0
        return (max(self.prices) / low) if low > 0 else None

    @property
    def suspicious(self) -> bool:
        """低离散度 + 高报价：投标价高度一致通常意味着竞争不充分。"""
        variation = self.variation
        return variation is not None and len(self.prices) >= 3 and variation < 0.01


def _to_amount(value: str) -> float | None:
    text = (value or "").strip().replace(",", "").replace("，", "").replace("￥", "").replace("¥", "")
    text = text.replace("元", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def analyze_bids(path: Path) -> list[BidLot]:
    header, rows = _read_table(path)
    lot_col = _pick(header, BID_ALIASES["lot"])
    price_col = _pick(header, BID_ALIASES["price"])
    bidder_col = _pick(header, BID_ALIASES["bidder"])
    if price_col is None:
        raise DataAuditError(f"未找到报价列（可识别：{'、'.join(BID_ALIASES['price'][:3])}）")
    buckets: dict[str, BidLot] = defaultdict(lambda: BidLot(lot="", prices=[], bidders=[]))
    for index, row in enumerate(rows):
        amount = _to_amount(row.get(price_col, ""))
        if amount is None:
            continue
        lot = (row.get(lot_col, "") if lot_col else "") or f"第{index + 1}行"
        bucket = buckets[lot]
        bucket.lot = lot
        bucket.prices.append(amount)
        if bidder_col:
            bucket.bidders.append(row.get(bidder_col, ""))
    return sorted(buckets.values(), key=lambda item: (-len(item.prices), item.lot))


def render_bids(lots: list[BidLot]) -> str:
    lines = [
        "# 开标报价离散度报告",
        "",
        "| 标段 | 投标家数 | 最低 | 最高 | 极差比 | 变异系数 | 提示 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    flagged = 0
    for lot in lots:
        variation = lot.variation
        ratio = lot.ratio
        flag = "报价高度一致，建议人工复核" if lot.suspicious else ""
        if lot.suspicious:
            flagged += 1
        lines.append(
            "| {lot} | {count} | {low:,.2f} | {high:,.2f} | {ratio} | {var} | {flag} |".format(
                lot=lot.lot,
                count=len(lot.prices),
                low=min(lot.prices),
                high=max(lot.prices),
                ratio="-" if ratio is None else f"{ratio:.3f}",
                var="-" if variation is None else f"{variation:.4f}",
                flag=flag,
            )
        )
    lines.append("")
    lines.append(f"共 {len(lots)} 个标段，其中 {flagged} 个标段的报价变异系数 < 1%。")
    lines.append("> 变异系数只作弱信号：家数少、清单差异大的标段容易误报，必须结合投标文件人工判断。")
    return "\n".join(lines)


def audit_file(path: Path, mode: str) -> dict[str, Any]:
    """统一入口：mode=reasons|bids，返回可 JSON 序列化的结果。"""
    if mode == "reasons":
        report = analyze_reasons(path)
        return {
            "mode": "reasons",
            "file": str(path),
            "total": report.total,
            "empty": report.empty,
            "counts": dict(report.categories),
            "examples": report.examples,
            "markdown": render_reasons(report),
        }
    if mode == "bids":
        lots = analyze_bids(path)
        return {
            "mode": "bids",
            "file": str(path),
            "lots": [
                {
                    "lot": lot.lot,
                    "count": len(lot.prices),
                    "min": min(lot.prices),
                    "max": max(lot.prices),
                    "ratio": lot.ratio,
                    "variation": lot.variation,
                    "suspicious": lot.suspicious,
                }
                for lot in lots
            ],
            "markdown": render_bids(lots),
        }
    raise DataAuditError(f"未知模式：{mode}（可选 reasons|bids）")


def write_json(result: dict[str, Any], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
