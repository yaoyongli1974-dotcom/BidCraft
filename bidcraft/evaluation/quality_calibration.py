"""AI 质量分与专家评分校准（RC-07）：纯统计，不查库、不依赖 numpy/scipy。

输入是人工整理的一组成对样本 ``(predicted_score, expert_rating)``，两域均为
0..100 的内部质量参考分。产出：

* ``spearman``：并列分数用平均名次，再对名次算 Pearson；
* ``pearson``：原始分数的积矩相关；
* ``brier_score``：两域同时除以 100 归一化到 0..1 后的均方误差；
* 分桶校准：预测分 ``[0,60) / [60,75) / [75,90) / [90,100]`` 逐桶给出样本数、
  预测均值、专家均值与 ``gap=专家均值-预测均值``；空桶均值为 ``None``。

样本少于 2 对或任一域零方差时相关性一律 ``None``（未测量），绝不伪造 0.0。
没有真实专家评分时 :func:`collect_quality_calibration` 返回 ``status='未测量'``、
``measured=False`` 且所有指标为 ``None``；合成夹具不能冒充真实评分。
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

DATASET_SCHEMA = 1
DEFAULT_ROOT = Path("tests/fixtures/expert_ratings_real")
MANIFEST_NAME = "dataset.json"
STATUS_MEASURED = "已测量"
STATUS_UNMEASURED = "未测量"
SYNTHETIC_MARKERS = ("SYNTHETIC", "模拟", "示例数据")

#: 每一个元素为 (下界, 上界, 展示标签)；最后一桶双端闭合。
CALIBRATION_BINS = (
    (0.0, 60.0, "[0, 60)"),
    (60.0, 75.0, "[60, 75)"),
    (75.0, 90.0, "[75, 90)"),
    (90.0, 100.0, "[90, 100]"),
)
METRIC_KEYS = ("spearman", "pearson", "brier_score")

Pair = tuple[float, float]


def dataset_path(root: Path | str = DEFAULT_ROOT) -> Path:
    return Path(root) / MANIFEST_NAME


def _pairs(pairs: Iterable[Sequence[Any]]) -> list[Pair]:
    """校验成对评分并归一化到 float；0..100 之外或非数值直接拒绝。"""
    normalized: list[Pair] = []
    for item in pairs:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("预测分与专家评分必须成对给出 [predicted, expert]")
        try:
            predicted, expert = float(item[0]), float(item[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("预测分与专家评分必须是数值") from exc
        if not (
            math.isfinite(predicted)
            and math.isfinite(expert)
            and 0.0 <= predicted <= 100.0
            and 0.0 <= expert <= 100.0
        ):
            raise ValueError("预测分与专家评分必须在 0..100 之间")
        normalized.append((predicted, expert))
    return normalized


def _ranks(values: Sequence[float]) -> list[float]:
    """平均名次：并列值共享名次，避免人为制造相关。"""
    order = sorted(range(len(values)), key=lambda index: values[index])
    result = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        average = (start + end) / 2 + 1
        for index in range(start, end + 1):
            result[order[index]] = average
        start = end + 1
    return result


def _pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) != len(y) or len(x) < 2:
        return None
    mean_x = sum(x) / len(x)
    mean_y = sum(y) / len(y)
    covariance = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    spread_x = sum((a - mean_x) ** 2 for a in x)
    spread_y = sum((b - mean_y) ** 2 for b in y)
    denominator = math.sqrt(spread_x * spread_y)
    if denominator <= 0:
        return None
    return round(covariance / denominator, 4)


def pearson_correlation(pairs: Iterable[Sequence[Any]]) -> float | None:
    """原始分数的 Pearson 相关；样本不足或零方差返回 None。"""
    rows = _pairs(pairs)
    return _pearson([p for p, _ in rows], [r for _, r in rows])


def spearman_correlation(pairs: Iterable[Sequence[Any]]) -> float | None:
    """平均名次上的 Spearman 相关；样本不足或零方差返回 None。"""
    rows = _pairs(pairs)
    return _pearson(_ranks([p for p, _ in rows]), _ranks([r for _, r in rows]))


def brier_score(pairs: Iterable[Sequence[Any]]) -> float | None:
    """归一化到 0..1 后的均方误差；样本不足返回 None。"""
    rows = _pairs(pairs)
    if len(rows) < 2:
        return None
    return round(sum(((p - r) / 100.0) ** 2 for p, r in rows) / len(rows), 4)


def binned_calibration(pairs: Iterable[Sequence[Any]]) -> list[dict[str, Any]]:
    """按预测分分桶比较预测均值与专家均值；保留空桶，均值为 None。"""
    rows = _pairs(pairs)
    bins: list[dict[str, Any]] = []
    for low, high, label in CALIBRATION_BINS:
        members = [
            row
            for row in rows
            if low <= row[0] < high or (label.endswith("]") and row[0] == high)
        ]
        predicted = round(sum(p for p, _ in members) / len(members), 4) if members else None
        expert = round(sum(r for _, r in members) / len(members), 4) if members else None
        bins.append(
            {
                "range": label,
                "n": len(members),
                "mean_predicted": predicted,
                "mean_expert": expert,
                "gap": round(expert - predicted, 4)
                if predicted is not None and expert is not None
                else None,
            }
        )
    return bins


def calibrate_ratings(pairs: Iterable[Sequence[Any]]) -> dict[str, Any]:
    """一组评分的完整校准结果；未测量时指标全为 None。"""
    rows = _pairs(pairs)
    result: dict[str, Any] = {
        "measured": False,
        "n": len(rows),
        "spearman": None,
        "pearson": None,
        "brier_score": None,
        "bins": [],
        "notes": [],
    }
    if len(rows) < 2:
        result["notes"].append("成对样本少于 2 对，相关性与 Brier 记为未测量")
        return result
    predicted = [p for p, _ in rows]
    expert = [r for _, r in rows]
    if len(set(predicted)) < 2 or len(set(expert)) < 2:
        result["notes"].append("预测分或专家评分零方差，相关性记为未测量")
        return result
    result["spearman"] = _pearson(_ranks(predicted), _ranks(expert))
    result["pearson"] = _pearson(predicted, expert)
    result["brier_score"] = brier_score(rows)
    result["bins"] = binned_calibration(rows)
    result["measured"] = True
    result["notes"].append(f"基于 {len(rows)} 对人工评分；Brier 按 0..1 归一化计算")
    return result


def load_ratings(root: Path | str = DEFAULT_ROOT) -> dict[str, Any]:
    """校验真实专家评分清单；合成标记/结构错误一律拒绝。"""
    manifest_path = dataset_path(root)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"真实专家评分清单不存在：{manifest_path}")
    raw = manifest_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("schema") != DATASET_SCHEMA:
        raise ValueError(f"专家评分 schema 必须为 {DATASET_SCHEMA}")
    if data.get("synthetic") is True:
        raise ValueError("数据集标记为 synthetic，不能作为真实专家评分")
    if any(marker in raw for marker in SYNTHETIC_MARKERS):
        raise ValueError("数据集含合成/示例标记，不能作为真实样本")
    pairs = data.get("pairs")
    if not isinstance(pairs, list):
        raise TypeError("pairs 必须是成对评分数组")
    _pairs(pairs)  # 结构错误在加载阶段就拒绝，不把坏数据带进汇总
    return {
        "schema": DATASET_SCHEMA,
        "label": str(data.get("label") or "real-expert-rating-calibration"),
        "pairs": pairs,
    }


def expert_status(root: Path | str = DEFAULT_ROOT) -> dict[str, Any]:
    """机器可读的就绪状态；缺数据不抛异常。"""
    try:
        dataset = load_ratings(root)
    except (FileNotFoundError, TypeError, ValueError, OSError) as exc:
        return {"ready": False, "pairs": 0, "reason": str(exc)}
    if not dataset["pairs"]:
        return {
            "ready": False,
            "pairs": 0,
            "reason": "评分清单为空，需要用户提供脱敏后的真实专家评分",
        }
    return {
        "ready": True,
        "pairs": len(dataset["pairs"]),
        "reason": "",
        "label": dataset["label"],
    }


def collect_quality_calibration(root: Path | str = DEFAULT_ROOT) -> dict[str, Any]:
    """真实专家评分校准汇总；未就绪时全键 None，不返回 0.0。"""
    status = expert_status(root)
    if not status.get("ready"):
        return {
            "status": STATUS_UNMEASURED,
            "measured": False,
            "dataset": status,
            "n": 0,
            "metrics": {key: None for key in METRIC_KEYS},
            "bins": [],
            "notes": [status.get("reason") or "真实专家评分未就绪"],
        }
    calibrated = calibrate_ratings(load_ratings(root)["pairs"])
    return {
        "status": STATUS_MEASURED if calibrated["measured"] else STATUS_UNMEASURED,
        "measured": bool(calibrated["measured"]),
        "dataset": status,
        "n": calibrated["n"],
        "metrics": {
            "spearman": calibrated["spearman"],
            "pearson": calibrated["pearson"],
            "brier_score": calibrated["brier_score"],
        },
        "bins": calibrated["bins"],
        "notes": calibrated["notes"],
    }


__all__ = [
    "binned_calibration",
    "brier_score",
    "calibrate_ratings",
    "collect_quality_calibration",
    "expert_status",
    "load_ratings",
    "pearson_correlation",
    "spearman_correlation",
]
