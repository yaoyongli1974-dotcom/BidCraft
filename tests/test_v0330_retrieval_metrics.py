"""v0.33.0 RC-06：检索基准 IR 指标与真实数据未测量语义（纯函数，合成夹具不冒充真实）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bidcraft.evaluation import retrieval_metrics as rm

FIXTURES = Path(__file__).parent / "fixtures"


def _write_manifest(root: Path, payload: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "dataset.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _real_manifest() -> dict:
    """临时自检数据（仅 tmp_path 内使用，不提交为真实 qrels）。"""
    return {
        "schema": rm.DATASET_SCHEMA,
        "label": "framework-self-check",
        "synthetic": False,
        "qrels": {"q1": ["d1", "d2"], "q2": ["d3"]},
        "run": {"q1": ["d1", "d9", "d2"], "q2": ["d9", "d3"]},
        "passages": [
            {
                "doc_id": "d1",
                "document_id": "doc-1",
                "page": 5,
                "heading": "资格要求",
                "chunk_id": "c1",
                "scope_ok": True,
                "valid_asset": True,
            },
            {
                "doc_id": "d9",
                "document_id": "",
                "page": None,
                "heading": "",
                "chunk_id": None,
                "scope_ok": False,
                "valid_asset": False,
            },
            {
                "doc_id": "d2",
                "document_id": "doc-2",
                "page": 6,
                "heading": "技术要求",
                "chunk_id": "c2",
                "scope_ok": True,
                "valid_asset": True,
            },
            {
                "doc_id": "d3",
                "document_id": "doc-3",
                "page": 7,
                "heading": "商务要求",
                "chunk_id": "c3",
                "scope_ok": True,
                "valid_asset": True,
            },
        ],
        "diagnostics": {
            "q1": {"searched": 2, "skipped": 0, "failed": 0},
            "q2": {"searched": 1, "skipped": 0, "failed": 1},
        },
    }


def test_pure_ir_metrics_hand_computed_values() -> None:
    qrels = {"q1": {"d1", "d2", "d3"}}
    run = {"q1": ["d4", "d1", "d5", "d2", "d6", "d3", "d1"]}
    # Recall@5 = 命中 d1/d2 两条 / 标注 3 条；Recall@10 = 3/3。
    assert rm.recall_at_k(qrels, run, "q1", 5) == round(2 / 3, 4)
    assert rm.recall_at_k(qrels, run, "q1", 10) == 1.0
    # Precision@5 = 2/5；MRR = 首个相关文档位次 2 的倒数。
    assert rm.precision_at_k(qrels, run, "q1", 5) == 0.4
    assert rm.mrr(qrels, run, "q1") == 0.5
    # DCG = 1/log2(3) + 1/log2(5)；IDCG = 1 + 1/log2(3) + 1/log2(4)。
    assert rm.ndcg_at_k(qrels, run, "q1", 5) == 0.4982


def test_duplicate_ranked_ids_are_deduplicated_before_scoring() -> None:
    qrels = {"q1": {"d1", "d2"}}
    run = {"q1": ["d1", "d1", "d2", "d2"]}
    # 同一文档重复出现不能放大命中数：置顶去重后命中 2 条，分母仍为 k=4。
    assert rm.precision_at_k(qrels, run, "q1", 4) == 0.5
    assert rm.recall_at_k(qrels, run, "q1", 4) == 1.0
    # 二值相关性同分时 IDCG 固定，重复命中不能让 nDCG 超过 1。
    assert rm.ndcg_at_k(qrels, run, "q1", 4) == 1.0
    assert rm.mrr(qrels, run, "q1") == 1.0


def test_missing_qid_empty_run_and_zero_k_are_zero_not_unmeasured() -> None:
    qrels = {"q1": {"d1"}}
    run = {"q1": []}
    for value in (
        rm.recall_at_k(qrels, run, "q1", 5),
        rm.precision_at_k(qrels, run, "q1", 5),
        rm.mrr(qrels, run, "q1"),
        rm.ndcg_at_k(qrels, run, "q1", 5),
        rm.recall_at_k(qrels, run, "missing-qid", 5),
        rm.precision_at_k(qrels, run, "missing-qid", 5),
        rm.mrr(qrels, run, "missing-qid"),
        rm.ndcg_at_k(qrels, run, "missing-qid", 5),
        rm.recall_at_k(qrels, run, "q1", 0),
        rm.precision_at_k(qrels, run, "q1", 0),
        rm.ndcg_at_k(qrels, run, "q1", 0),
    ):
        assert value == 0.0
        assert value is not None


def test_aggregate_metrics_exact_and_queries_averaged(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _real_manifest())
    result = rm.collect_retrieval_metrics(tmp_path)
    assert result["status"] == rm.STATUS_MEASURED
    assert result["measured"] is True
    assert result["queries"] == 2
    assert set(result["metrics"]) == set(rm.METRIC_KEYS)
    metrics = result["metrics"]
    # q1: 2/2, q2: 1/1；q1: 2/5, q2: 1/5；q1: 1/1, q2: 1/2。
    assert metrics["recall_at_5"] == 1.0
    assert metrics["recall_at_10"] == 1.0
    assert metrics["precision_at_5"] == 0.3
    assert metrics["mrr"] == 0.75
    # q1 命中第 1/3 位 → 1.5/(1+1/log2(3))=0.9197；q2 命中第 2 位 → 0.6309。
    assert metrics["ndcg_at_10"] == 0.7753
    # 四要素溯源 3/4；越权 1/4；无效资产 1/4；失败检索 1/2。
    assert metrics["source_completeness"] == 0.75
    assert metrics["scope_violation"] == 0.25
    assert metrics["invalid_asset_rate"] == 0.25
    assert metrics["degraded_retrieval_rate"] == 0.5
    assert result["dataset"]["ready"] is True
    assert any("宏平均" in note for note in result["notes"])


def test_no_real_dataset_is_unmeasured_with_all_none(tmp_path: Path) -> None:
    status = rm.retrieval_status(tmp_path)
    result = rm.collect_retrieval_metrics(tmp_path)
    assert status["ready"] is False
    assert "dataset.json" in status["reason"]
    assert result["status"] == rm.STATUS_UNMEASURED
    assert result["measured"] is False
    assert result["queries"] == 0
    assert set(result["metrics"]) == set(rm.METRIC_KEYS)
    for value in result["metrics"].values():
        assert value is None  # 未测量绝不能写成 0.0
    assert result["notes"]


def test_empty_qrels_or_run_manifest_is_not_ready(tmp_path: Path) -> None:
    payload = _real_manifest()
    payload["qrels"] = {}
    payload["run"] = {}
    _write_manifest(tmp_path, payload)
    assert rm.retrieval_status(tmp_path)["ready"] is False
    assert "用户提供" in rm.retrieval_status(tmp_path)["reason"]
    result = rm.collect_retrieval_metrics(tmp_path)
    assert result["measured"] is False
    assert result["status"] == rm.STATUS_UNMEASURED


def test_synthetic_benchmark_fixture_is_rejected_as_real(tmp_path: Path) -> None:
    fixture = FIXTURES / "retrieval_benchmark.json"
    assert fixture.is_file()
    (tmp_path / "dataset.json").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    assert rm.retrieval_status(tmp_path)["ready"] is False
    assert "synthetic" in rm.retrieval_status(tmp_path)["reason"]
    with pytest.raises(ValueError, match="synthetic"):
        rm.load_benchmark(tmp_path)
    # 合成夹具仅用于算法自检，不能冒充真实基准；自检本身仍可计算精确值。
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    checked = rm.evaluate_benchmark(payload)
    assert checked["measured"] is True
    assert checked["metrics"]["recall_at_5"] == 1.0
    assert checked["metrics"]["mrr"] == 1.0


def test_repository_ships_no_real_retrieval_dataset() -> None:
    root = FIXTURES / "retrieval_real"
    assert not (root / "dataset.json").exists(), "真实 qrels 清单不得提交到仓库"
    assert (root / "README.md").is_file()
    assert (root / "dataset.example.json").is_file()
    assert rm.retrieval_status(root)["ready"] is False


def test_public_exports_are_sorted_and_callable() -> None:
    from bidcraft import evaluation

    assert evaluation.__all__ == sorted(evaluation.__all__)
    for name in (
        "recall_at_k",
        "precision_at_k",
        "mrr",
        "ndcg_at_k",
        "collect_retrieval_metrics",
        "collect_quality_calibration",
    ):
        assert name in evaluation.__all__
        assert callable(getattr(evaluation, name))


@pytest.mark.usefixtures("db")
def test_report_summary_injects_unmeasured_retrieval_benchmark(monkeypatch) -> None:
    pytest.importorskip("fastapi")
    from bidcraft.db import session_scope
    from bidcraft.evaluation import report as report_module
    from bidcraft.models import Project

    with session_scope() as db:
        project = Project(name="retrieval-benchmark-summary", package="weakcurrent-cabling")
        db.add(project)
        db.flush()
        report = report_module.build_quality_report(db, project.id)
        summary = report.summary
        assert {"coverage", "recommendations", "note", "metrics", "tender_quality"} <= set(summary)
        bench = summary["retrieval_benchmark"]
        assert bench["status"] == rm.STATUS_UNMEASURED
        assert bench["measured"] is False
        assert all(value is None for value in bench["metrics"].values())
        assert summary["calibration"]["status"] == "未测量"
        # report_payload 结构未发生任何变化。
        assert set(report_module.report_payload(report)) == {
            "id",
            "project_id",
            "report_version",
            "overall_score",
            "grade",
            "verdict",
            "dimensions",
            "summary",
            "risk_summary",
            "created_by",
            "created_at",
            "findings",
        }
        assert report_module.report_payload(report)["summary"]["retrieval_benchmark"] == bench
