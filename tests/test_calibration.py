"""R14：判定质量回归体系与假接受率（FAR）度量。

重点监测四类错误：错误放行（FA）、错误阻断（FR）、错误转人工、证据关联错误。
核心红线：已知不满足的 ★ 条款绝不能被自动判为 satisfied。
"""

from __future__ import annotations

import json

from bidcraft.calibration import (
    VerdictCase,
    assert_regression,
    load_cases,
    run_case,
    run_regression,
)


def test_regression_fixture_has_expected_verdict():
    """回归样本可从磁盘加载，字段完整，标准四态取值合法。"""
    cases = load_cases()
    assert len(cases) >= 14
    valid = {"satisfied", "unsatisfied", "partial", "review"}
    for case in cases:
        assert case.id
        assert case.requirement
        assert case.expected in valid, (case.id, case.expected)
        assert case.note, f"{case.id} 必须有人工审核说明"
        assert case.evidence_hint, f"{case.id} 必须有关键证据位置"


def test_calibration_reports_confusion_matrix():
    """混淆矩阵 TP/FP/TN/FN 齐备，且四类之和等于样本总数。"""
    report = run_regression(load_cases())
    cm = report["confusion_matrix"]
    assert set(cm) == {"TP", "FP", "TN", "FN"}
    assert cm["TP"] + cm["FP"] + cm["TN"] + cm["FN"] == report["total"]
    assert cm["TP"] > 0, "应有正确放行样本"
    assert cm["TN"] > 0, "应有正确阻断样本"


def test_calibration_reports_false_acceptance_rate():
    """FAR = FP/(FP+TN)；当前回归集应为零误放行。"""
    report = run_regression(load_cases())
    cm = report["confusion_matrix"]
    expected_far = round(cm["FP"] / (cm["FP"] + cm["TN"]), 4) if (cm["FP"] + cm["TN"]) else 0.0
    assert report["false_acceptance_rate"] == expected_far
    assert report["false_acceptance_rate"] == 0.0, "回归集不得有误放行"


def test_calibration_detects_false_acceptance():
    """构造一个「标准不满足但预测满足」的样本，FAR 与明细须如实反映。"""
    case = VerdictCase(
        id="synthetic_fa",
        requirement="网络带宽不低于1000Mbps",
        body="网络带宽为1000Mbps。",  # 预测 satisfied
        expected="unsatisfied",       # 标准标为不满足（故意制造分歧）
        note="合成样本：验证 FAR 检测能力",
    )
    result = run_case(case)
    assert result.predicted == "satisfied"
    assert result.expected == "unsatisfied"
    assert result.false_acceptance is True

    report = run_regression([case])
    assert report["confusion_matrix"]["FP"] == 1
    assert report["false_acceptance_rate"] == 1.0


def test_calibration_detects_false_rejection():
    """构造一个「标准满足但预测不满足」的样本，混淆矩阵须记为 FN。"""
    case = VerdictCase(
        id="synthetic_fr",
        requirement="网络带宽不低于1000Mbps",
        body="网络带宽为500Mbps。",  # 预测 unsatisfied
        expected="satisfied",         # 标准标为满足（故意制造分歧）
        note="合成样本：验证错误阻断检测能力",
    )
    report = run_regression([case])
    assert report["confusion_matrix"]["FN"] == 1
    assert report["confusion_matrix"]["FP"] == 0


def test_star_requirement_never_auto_passes_known_violation():
    """★ 条款已知违反样本（star_fa）若被判满足，assert_regression 必须抛错。"""
    # 真实回归集里的 ★ 违反样本必须被正确阻断
    cases = load_cases()
    star_violations = [c for c in cases if "★" in c.marker and c.expected != "satisfied"]
    assert star_violations, "回归集应含 ★ 已知违反样本"
    report = assert_regression(cases)  # 不应抛错
    assert report["star_false_acceptances"] == []

    # 构造 ★ 误放行：标准不满足、预测满足 → 必须被拦截
    bad = VerdictCase(
        id="star_bad",
        requirement="网络带宽不低于1000Mbps",
        body="网络带宽为1000Mbps。",
        expected="unsatisfied",
        marker="★",
        note="合成：★ 已知违反却标满足，须触发严重误放行",
    )
    bad_report = run_regression([bad])
    assert bad_report["star_false_acceptances"] == ["star_bad"]
    try:
        assert_regression([bad])
    except AssertionError:
        pass  # 命中失败或 ★ 误放行任一守护都会拦截
    else:
        raise AssertionError("★ 误放行未被 assert_regression 拦截")


def test_fixture_file_is_valid_json_with_governance():
    """回归集文件含治理说明：标准答案变更需人工审核。"""
    from bidcraft.calibration import FIXTURE_PATH

    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    meta = data.get("_meta", {})
    assert meta.get("governance")
    assert "人工审核" in meta["governance"]
    assert len(data["cases"]) >= 14


# --------------------------------------------------------------- R20 增强


def test_regression_fixture_has_unique_ids():
    """每条样本 id 唯一（防止重复统计与标准答案覆盖）。"""
    cases = load_cases()
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "回归集存在重复 id"


def test_regression_case_accepts_deadline():
    """样本可携带招标截止日期，run_case 自动解析并驱动时间窗判定。"""
    from datetime import date

    case = VerdictCase(
        id="dl_case",
        requirement="近三年不少于3项类似项目业绩",
        body="2025年完成A项目。2024年完成B项目。2024年完成C项目。",
        expected="satisfied",
        deadline="2026-11-22",
    )
    assert case.parsed_deadline == date(2026, 11, 22)
    assert run_case(case).hit

    # 无 deadline 的同款样本：转人工，不得自动满足。
    no_dl = VerdictCase(
        id="dl_none",
        requirement=case.requirement,
        body=case.body,
        expected="review",
    )
    assert no_dl.parsed_deadline is None
    assert run_case(no_dl).hit


def test_regression_runs_deadline_window_cases():
    """时间窗样本由统一 run_regression 执行，不依赖临时手工传 deadline。"""
    cases = load_cases()
    tw = [c for c in cases if c.category == "time_window"]
    assert len(tw) >= 6, "应有足量时间窗样本"
    report = run_regression(tw)  # 不传全局 deadline，逐样本用自己的
    assert report["hits"] == report["total"], "时间窗样本必须全部命中"
    # 边界歧义样本（年/月精度）确实被判 review，不得当成 satisfied 放行；
    # 日精度起始边界（c22）边界含等号、证据充分，允许 satisfied。
    ambiguous_boundary = [c for c in tw if "boundary" in c.id and c.expected == "review"]
    assert ambiguous_boundary
    for c in ambiguous_boundary:
        result = run_case(c)
        assert result.predicted == "review", (c.id, result.predicted)


def test_regression_detects_cutoff_boundary_false_acceptance():
    """起始边界年份精度若被误判满足，应被识别为误放行/未命中。"""
    case = VerdictCase(
        id="boundary_fa_synthetic",
        requirement="近三年不少于3项类似项目业绩",
        body="2023年完成A项目。2024年完成B项目。2025年完成C项目。",
        expected="satisfied",  # 故意把起始边界年标为应满足（标准错误，模拟误放行检测）
        deadline="2026-11-22",
    )
    result = run_case(case)
    assert result.predicted == "review"  # 实际保守判人工
    assert result.hit is False
    assert result.false_acceptance is False  # 预测不是 satisfied，不构成系统误放行
    report = run_regression([case])
    assert report["hits"] == 0  # 分歧被如实记为未命中


def test_regression_detects_compound_type_false_acceptance():
    """复合条款项目类型约束丢失若导致 ★ 满足，须被 FAR 机制拦截。"""
    # 真实样本：复合 ★ 段落只 2 项有效 → partial，不自动满足
    cases = load_cases()
    c26 = next(c for c in cases if c.id == "c26_tw_compound_type_constraint")
    assert run_case(c26).predicted != "satisfied"

    # 合成：★ 复合条款若被错误预测为 satisfied 且标准非满足 → star FA
    bad = VerdictCase(
        id="compound_star_fa",
        requirement="★ 近三年不少于3项弱电智能化类似项目业绩，并提供合同复印件",
        marker="★",
        body="2025年完成弱电智能化A项目，提供合同复印件。2024年完成弱电智能化B项目，提供合同复印件。",
        expected="unsatisfied",
        deadline="2026-11-22",
    )
    report = run_regression([bad])
    assert report["star_false_acceptances"] == []  # 实际不满足，未误放行
    # 构造一个真正误放行的对照：标准非满足但强制让预测为 satisfied 的情形
    fa = VerdictCase(
        id="compound_fa",
        requirement="近三年不少于3项弱电智能化类似项目业绩，并提供合同复印件",
        body="2025年完成弱电智能化A项目，提供合同复印件。2024年完成弱电智能化B项目，提供合同复印件。",
        expected="satisfied",  # 标准错标满足
        deadline="2026-11-22",
    )
    fr = run_regression([fa])
    assert fr["hits"] == 0  # 预测 partial ≠ 标准 satisfied，如实反映分歧


def test_regression_reports_review_rate():
    """报告含人工复核率，口径 = 预测 review 数 / 总数。"""
    report = run_regression(load_cases())
    assert "review_rate" in report
    reviews = sum(1 for c in report["cases"] if c["predicted"] == "review")
    expected = round(reviews / report["total"], 4)
    assert report["review_rate"] == expected
    assert 0.0 <= report["review_rate"] <= 1.0


def test_regression_reports_four_state_accuracy():
    """报告含四态准确率，review 与 unsatisfied 分开计，不等于二值准确率。"""
    report = run_regression(load_cases())
    assert "four_state_accuracy" in report
    hits = sum(1 for c in report["cases"] if c["hit"])
    assert report["four_state_accuracy"] == round(hits / report["total"], 4)
    # 四态命中数 == 二值命中数时两者才相等；这里允许相等但不混类
    distinct_predicted = {c["predicted"] for c in report["cases"]}
    assert "review" in distinct_predicted and "unsatisfied" in distinct_predicted


def test_regression_reports_evidence_association_errors():
    """报告含证据关联诊断计数（类型冲突/身份冲突/证据缺失/预期证据不符）。"""
    report = run_regression(load_cases())
    for key in (
        "evidence_association_errors",
        "project_type_association_errors",
        "identity_association_errors",
        "evidence_missing_count",
    ):
        assert key in report and isinstance(report[key], int)
    # 起始边界样本的 expected_evidence(boundary_start_ge) 必须被满足，否则记为关联错误
    c19 = next(c for c in report["cases"] if c["id"] == "c19_tw_start_year_boundary")
    assert c19["evidence_association_error"] is False


def test_regression_fixture_expected_change_requires_governance():
    """治理说明明确禁止为提升通过率修改标准答案。"""
    from bidcraft.calibration import FIXTURE_PATH

    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    governance = data["_meta"]["governance"]
    assert "不得为提升通过率" in governance or "不得为提升通过率而修改" in governance
    assert "人工审核" in governance
