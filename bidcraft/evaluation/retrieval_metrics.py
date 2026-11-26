"""检索基准指标（RC-06）：纯 IR 计算 + 真实 qrels 契约校验。

只做确定性数学，不查库、不调用检索、不依赖 numpy/scipy。没有真实 qrels 时
``collect_retrieval_metrics()`` 一律返回 ``status='未测量'``、``measured=False``
且所有指标为 ``None``（绝不写成 0.0）；合成夹具即使算法自检通过也不能冒充真实
基准。所有比率先逐查询/逐资产计算再取宏平均，样本展示与指标计算完全解耦。

口径：

* ``recall_at_k`` / ``precision_at_5`` / ``mrr`` / ``ndcg_at_10``：qrels 为
  ``{qid: {doc_id}}``，run 为 ``{qid: [doc_id]}``；同一文档重复出现先按首次
  出现去重，避免平局/重复命中虚增指标。precision 分母固定为 k；MRR 截止 10；
  nDCG 使用二值相关性，等增益文档 IDCG 只取决于标注数量。
* ``source_completeness``：溯源四要素（document_id/page/heading/chunk_id）
  齐全的 passage 比例，对应 :class:`bidcraft.knowledge.Passage` 的文档级/块级字段。
* ``scope_violation``：``scope_ok`` 非真的 passage 比例（project/package 范围
  或个人库授权校验失败，对应 ``search()`` 的 diagnostics.skipped）。
* ``invalid_asset_rate``：``valid_asset`` 非真的 passage 比例（未发布/过期/
  块不可解析；失败关闭，缺失标记也算违规）。
* ``degraded_retrieval_rate``：``searched == 0`` 或 ``failed > 0`` 的查询比例，
  对应 ``SearchResult.diagnostics``。

零分母（缺 qid、空 run、无 passage、无诊断）时纯函数返回 0.0，汇总指标返回
``None``——两者语义不同：前者是“可测但没命中”，后者是“没有数据可测”。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DATASET_SCHEMA = 1
DEFAULT_ROOT = Path("tests/fixtures/retrieval_real")
MANIFEST_NAME = "dataset.json"
STATUS_MEASURED = "已测量"
STATUS_UNMEASURED = "未测量"
SYNTHETIC_MARKERS = ("SYNTHETIC", "模拟", "示例数据")

#: 汇总指标固定键；未测量时全部为 None。
METRIC_KEYS = (
    "recall_at_5",
    "recall_at_10",
    "precision_at_5",
    "mrr",
    "ndcg_at_10",
    "source_completeness",
    "scope_violation",
    "invalid_asset_rate",
    "degraded_retrieval_rate",
)

Qrels = Mapping[str, Any]
Run = Mapping[str, Any]


def dataset_path(root: Path | str = DEFAULT_ROOT) -> Path:
    return Path(root) / MANIFEST_NAME


def _relevant(qrels: Qrels, qid: str) -> set[str]:
    docs = qrels.get(qid) or ()
    return {str(doc) for doc in docs}


def _ranked(run: Run, qid: str, k: int | None = None) -> list[str]:
    """按首次出现去重的有序结果；k<=0 视为空结果。"""
    docs = run.get(qid) or ()
    if isinstance(docs, str):
        docs = [docs]
    if k is not None and k <= 0:
        return []
    seen: set[str] = set()
    unique: list[str] = []
    for doc in docs:
        key = str(doc)
        if key in seen:
            continue
        seen.add(key)
        unique.append(key)
    return unique[:k] if k is not None else unique


def recall_at_k(qrels: Qrels, run: Run, qid: str, k: int) -> float:
    """命中标注文档数 / 标注文档总数；缺 qid 或空标注返回 0.0。"""
    if k <= 0:
        return 0.0
    relevant = _relevant(qrels, qid)
    if not relevant:
        return 0.0
    hits = sum(1 for doc in _ranked(run, qid, k) if doc in relevant)
    return round(hits / len(relevant), 4)


def precision_at_k(qrels: Qrels, run: Run, qid: str, k: int) -> float:
    """top-k 命中数 / k（重复文档已去重）；缺 qid 或 k<=0 返回 0.0。"""
    if k <= 0:
        return 0.0
    relevant = _relevant(qrels, qid)
    hits = sum(1 for doc in _ranked(run, qid, k) if doc in relevant)
    return round(hits / k, 4)


def mrr(qrels: Qrels, run: Run, qid: str, *, k: int = 10) -> float:
    """首个命中文档位次的倒数（截止 k）；无命中返回 0.0。"""
    relevant = _relevant(qrels, qid)
    for rank, doc in enumerate(_ranked(run, qid, k), start=1):
        if doc in relevant:
            return round(1.0 / rank, 4)
    return 0.0


def ndcg_at_k(qrels: Qrels, run: Run, qid: str, k: int) -> float:
    """二值相关性 nDCG@k；同等相关文档增益相同，重复命中不放大 DCG。"""
    if k <= 0:
        return 0.0
    relevant = _relevant(qrels, qid)
    if not relevant:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, doc in enumerate(_ranked(run, qid, k), start=1)
        if doc in relevant
    )
    idcg = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, min(k, len(relevant)) + 1)
    )
    return round(dcg / idcg, 4) if idcg > 0 else 0.0


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _ratio(hit: int, total: int) -> float | None:
    return round(hit / total, 4) if total else None


def evaluate_benchmark(data: dict[str, Any]) -> dict[str, Any]:
    """对已校验（或合成自检）的清单计算全部聚合指标。"""
    raw_qrels = data.get("qrels") or {}
    raw_run = data.get("run") or {}
    qrels = {
        str(qid): {str(doc) for doc in (docs or ())}
        for qid, docs in raw_qrels.items()
        if isinstance(docs, (list, tuple, set))
    }
    run = {
        str(qid): [str(doc) for doc in (docs or ())]
        for qid, docs in raw_run.items()
        if isinstance(docs, (list, tuple, set))
    }
    qids = sorted(qrels)
    metrics: dict[str, float | None] = {
        "recall_at_5": _mean([recall_at_k(qrels, run, qid, 5) for qid in qids]),
        "recall_at_10": _mean([recall_at_k(qrels, run, qid, 10) for qid in qids]),
        "precision_at_5": _mean([precision_at_k(qrels, run, qid, 5) for qid in qids]),
        "mrr": _mean([mrr(qrels, run, qid) for qid in qids]),
        "ndcg_at_10": _mean([ndcg_at_k(qrels, run, qid, 10) for qid in qids]),
    }
    passages = [p for p in (data.get("passages") or []) if isinstance(p, dict)]
    complete = sum(
        1
        for passage in passages
        if all(str(passage.get(key) or "").strip() for key in ("document_id", "page", "heading", "chunk_id"))
    )
    scope_violations = sum(1 for passage in passages if passage.get("scope_ok") is not True)
    invalid_assets = sum(1 for passage in passages if passage.get("valid_asset") is not True)
    diagnostics = [
        row for row in (data.get("diagnostics") or {}).values() if isinstance(row, dict)
    ]
    degraded = sum(
        1
        for row in diagnostics
        if int(row.get("failed") or 0) > 0 or int(row.get("searched") or 0) <= 0
    )
    metrics["source_completeness"] = _ratio(complete, len(passages))
    metrics["scope_violation"] = _ratio(scope_violations, len(passages))
    metrics["invalid_asset_rate"] = _ratio(invalid_assets, len(passages))
    metrics["degraded_retrieval_rate"] = _ratio(degraded, len(diagnostics))
    return {
        "measured": bool(qids) and bool(run),
        "queries": len(qids),
        "passages": len(passages),
        "metrics": metrics,
    }


def load_benchmark(root: Path | str = DEFAULT_ROOT) -> dict[str, Any]:
    """校验真实检索基准清单；合成标记/结构错误一律拒绝。"""
    manifest_path = dataset_path(root)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"真实检索基准清单不存在：{manifest_path}")
    raw = manifest_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("schema") != DATASET_SCHEMA:
        raise ValueError(f"检索基准 schema 必须为 {DATASET_SCHEMA}")
    if data.get("synthetic") is True:
        raise ValueError("数据集标记为 synthetic，不能作为真实检索基准")
    if any(marker in raw for marker in SYNTHETIC_MARKERS):
        raise ValueError("数据集含合成/示例标记，不能作为真实样本")
    for key in ("qrels", "run"):
        value = data.get(key)
        if not isinstance(value, dict):
            raise TypeError(f"{key} 必须是 qid 到文档 ID 数组的映射")
        for qid, docs in value.items():
            if not isinstance(docs, list) or any(not str(doc).strip() for doc in docs):
                raise ValueError(f"{key}[{qid}] 必须是非空文档 ID 数组")
    passages = data.get("passages") or []
    if not isinstance(passages, list) or any(not isinstance(p, dict) for p in passages):
        raise TypeError("passages 必须是对象数组")
    diagnostics = data.get("diagnostics") or {}
    if not isinstance(diagnostics, dict) or any(
        not isinstance(row, dict) for row in diagnostics.values()
    ):
        raise TypeError("diagnostics 必须是 qid 到诊断对象的映射")
    return {
        "schema": DATASET_SCHEMA,
        "label": str(data.get("label") or "real-retrieval-regression"),
        "qrels": data["qrels"],
        "run": data["run"],
        "passages": passages,
        "diagnostics": diagnostics,
    }


def retrieval_status(root: Path | str = DEFAULT_ROOT) -> dict[str, Any]:
    """机器可读的就绪状态；缺数据不抛异常。"""
    try:
        dataset = load_benchmark(root)
    except (FileNotFoundError, TypeError, ValueError, OSError) as exc:
        return {"ready": False, "queries": 0, "reason": str(exc)}
    if not dataset["qrels"] or not any(
        dataset["run"].get(qid) for qid in dataset["qrels"]
    ):
        return {
            "ready": False,
            "queries": 0,
            "reason": "qrels 或 run 为空，需要用户提供脱敏后的真实检索基准",
        }
    return {
        "ready": True,
        "queries": len(dataset["qrels"]),
        "reason": "",
        "label": dataset["label"],
    }


def collect_retrieval_metrics(root: Path | str = DEFAULT_ROOT) -> dict[str, Any]:
    """真实检索质量汇总；未就绪时全键 None，不返回 0.0。"""
    status = retrieval_status(root)
    if not status.get("ready"):
        return {
            "status": STATUS_UNMEASURED,
            "measured": False,
            "dataset": status,
            "queries": 0,
            "metrics": {key: None for key in METRIC_KEYS},
            "notes": [status.get("reason") or "真实检索基准未就绪"],
        }
    evaluated = evaluate_benchmark(load_benchmark(root))
    notes = [
        "IR 指标按查询宏平均（先逐查询计算再取算术平均，precision 分母固定为 k）",
        "来源/范围/资产/降级指标按 passage 或查询计数；零分母记 None，不记 0.0",
    ]
    if evaluated["passages"] == 0:
        notes.append("没有 passage 溯源信号，来源/范围/资产指标为未测量")
    return {
        "status": STATUS_MEASURED,
        "measured": True,
        "dataset": status,
        "queries": evaluated["queries"],
        "metrics": evaluated["metrics"],
        "notes": notes,
    }


__all__ = [
    "collect_retrieval_metrics",
    "evaluate_benchmark",
    "load_benchmark",
    "mrr",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "retrieval_status",
]
