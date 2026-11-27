"""v0.33.2：招标文件评分项抽取噪声过滤、稳定去重与覆盖口径回归。"""

from __future__ import annotations

import pytest
from openpyxl import Workbook

pytest.importorskip("fastapi")

from sqlalchemy import select  # noqa: E402

from bidcraft.db import session_scope  # noqa: E402
from bidcraft.ledger import FactLedgerV032, save_ledger  # noqa: E402
from bidcraft.models import Project, ScoringItem  # noqa: E402
from bidcraft.parsing.extract import extract, extract_scoring  # noqa: E402
from bidcraft.parsing.loader import load_document  # noqa: E402
from bidcraft.rules.loader import load_package  # noqa: E402
from bidcraft.schemas import ScoringItemModel  # noqa: E402


def _workbook(path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    sheet.append(["序号", "名称", "规格型号", "单位", "数量"])
    sheet.append([1, "信息面板", "双口 Cat6A", "个", 480])
    scoring = workbook.create_sheet("评分办法")
    scoring.append(["评审因素", "分值", "评分标准"])
    scoring.append(["技术方案", 40, "方案完整针对性强"])
    scoring.append(["技术方案 ", "40 分", "方案完整针对性强"])
    scoring.append(["投标报价", 30, "低价优先"])
    scoring.append(["投标报价", "30 分", "低价优先"])
    scoring.append(["24J", "24J", "小写：24J"])
    scoring.append(["30日", "30日", "工期 30 日"])
    scoring.append(["小写：", "小写：", "金额小写"])
    scoring.append(["合同工期", "30日", "工期不超过 30 日"])
    scoring.append(["合计", 100, "总分"])
    workbook.save(path)
    return path


def test_extract_scoring_filters_noise_and_normalized_duplicates(tmp_path):
    parsed = load_document(_workbook(tmp_path / "tender.xlsx"))
    items = extract_scoring(parsed.tables, parsed.text)

    names = [item.name for item in items]
    assert names == ["技术方案", "投标报价"]
    assert [item.score for item in items] == [40, 30]
    assert items[0].category == "技术"
    assert items[1].category == "价格"
    assert not any("小写" in item.criteria for item in items)


def test_extract_pipeline_keeps_scoring_clean(tmp_path):
    parsed = load_document(_workbook(tmp_path / "tender.xlsx"))
    result = extract(parsed.text, load_package("weakcurrent-cabling"), pages=parsed.pages, tables=parsed.tables)
    assert [item.name for item in result.scoring] == ["技术方案", "投标报价"]


def test_coverage_matches_name_in_intent_and_skips_noise_anchor():
    from bidcraft.generation.coverage import STATUS_COVERED, calculate_score_coverage

    scores = [
        {
            "id": "s1",
            "name": "技术方案",
            "score": 30,
            "category": "技术",
            "section_hint": "",
            "criteria": "小写：24J；方案完整针对性强",
        }
    ]
    nodes = [
        {
            "id": "n1",
            "heading": "实施方案",
            "intent": "围绕技术方案展开",
            "words": 800,
            "body": "方案完整针对性强，逐项响应。",
            "review_status": "draft",
        }
    ]
    report = calculate_score_coverage(scores, nodes)
    assert report["rows"][0]["status"] == STATUS_COVERED
    assert report["rows"][0]["anchors"] == ["方案完整针对性强"]


def test_coverage_filters_existing_noise_rows_without_deleting_them():
    from bidcraft.generation.coverage import calculate_score_coverage

    scores = [
        {"id": "bad1", "name": "24J", "score": 24, "criteria": "小写：24J"},
        {"id": "bad2", "name": "30日", "score": 30, "criteria": "工期 30 日"},
        {"id": "s1", "name": "技术方案", "score": 40, "criteria": "方案完整"},
    ]
    report = calculate_score_coverage(scores, [])
    assert [row["name"] for row in report["rows"]] == ["技术方案"]
    assert report["total_score"] == 40
    assert any("已过滤 2 个非评分项碎片" in issue for issue in report["validation_issues"])


def test_save_ledger_dedupes_scoring_rows():
    with session_scope() as db:
        project = Project(name="评分去重项目")
        db.add(project)
        db.flush()
        project_id = project.id

    ledger = FactLedgerV032(
        scoring=[
            ScoringItemModel(name="技术方案 ", score=40, criteria="完整"),
            ScoringItemModel(name="技术方案", score=40, criteria="完整"),
            ScoringItemModel(name="投标报价", score=30, criteria="低价优先"),
        ]
    )
    with session_scope() as db:
        save_ledger(db, project_id, ledger, actor="cli")

    with session_scope() as db:
        rows = list(
            db.scalars(select(ScoringItem).where(ScoringItem.project_id == project_id).order_by(ScoringItem.name))
        )
    assert [row.name for row in rows] == ["技术方案", "投标报价"]
    assert [row.score for row in rows] == [40, 30]
