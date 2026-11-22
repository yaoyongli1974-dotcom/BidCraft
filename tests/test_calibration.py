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
