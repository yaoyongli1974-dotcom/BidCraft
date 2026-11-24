"""投标质量评估中心（对标易标第四批 v0.27.0）。

把现有规则核对、★/▲ 判定、来源追溯、评分覆盖、章节确认、导出门禁等结果
**聚合**成一份可审计的质量报告。刻意不重复实现任何合规/判定规则——本包只读
现有权威组件的结果。
"""

from .commercial import collect_commercial_completeness
from .coverage import (
    calculate_chapter_coverage,
    calculate_evidence_coverage,
    calculate_requirement_coverage,
    calculate_scoring_coverage,
)
from .quality_metrics import collect_quality_metrics
from .report import (
    FINDING_STATUSES,
    build_quality_report,
    find_report_for_job,
    finding_for_project,
    finding_payload,
    list_reports,
    load_report,
    report_payload,
    update_finding,
)
from .risk import (
    build_risk_recommendations,
    calculate_risk_summary,
    classify_quality_risks,
    collect_project_risks,
)
from .scorer import (
    DIMENSION_WEIGHTS,
    calculate_dimension_scores,
    calculate_overall_quality,
    evaluate_project_quality,
)
from .tender_dataset import DATASET_SCHEMA, dataset_status, load_dataset

__all__ = [
    "DATASET_SCHEMA",
    "DIMENSION_WEIGHTS",
    "FINDING_STATUSES",
    "build_quality_report",
    "build_risk_recommendations",
    "calculate_chapter_coverage",
    "calculate_dimension_scores",
    "calculate_evidence_coverage",
    "calculate_overall_quality",
    "calculate_requirement_coverage",
    "calculate_risk_summary",
    "calculate_scoring_coverage",
    "classify_quality_risks",
    "collect_commercial_completeness",
    "collect_project_risks",
    "collect_quality_metrics",
    "dataset_status",
    "evaluate_project_quality",
    "find_report_for_job",
    "finding_for_project",
    "finding_payload",
    "list_reports",
    "load_dataset",
    "load_report",
    "report_payload",
    "update_finding",
]
