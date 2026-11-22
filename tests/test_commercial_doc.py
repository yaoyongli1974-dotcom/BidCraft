"""商务标汇编：台账填充、占位符、已审批报价约束与一致性校验。"""

from __future__ import annotations

import copy

import pytest

from bidcraft import commercial
from bidcraft.db import session_scope
from bidcraft.models import (
    BidPackage,
    BoqQuoteVersion,
    Fact,
    Personnel,
    Project,
    Qualification,
    User,
    utcnow,
)

TAX_RATE = 0.13
TOTAL = 250.5
TAX = 32.56
TOTAL_WITH_TAX = 283.06


@pytest.fixture(autouse=True)
def _isolated_db(db):
    yield db


def _seed_project(
    *, with_quote: bool = True, approved: bool = True, with_package: bool = False
) -> dict[str, str]:
    with session_scope() as db:
        user = User(
            username="comm-owner",
            display_name="王经理",
            password_hash="x",
            role="manager",
        )
        db.add(user)
        db.flush()
        project = Project(name="园区智能化工程", buyer="某科技园区管委会", owner_id=user.id)
        db.add(project)
        db.flush()
        package_id = None
        if with_package:
            package = BidPackage(project_id=project.id, name="一标段", position=1)
            db.add(package)
            db.flush()
            package_id = package.id
        for key, label, value in (
            ("project.name", "项目名称", "园区智能化工程"),
            ("project.number", "项目编号", "ZB-2026-0412"),
            ("project.buyer", "招标人", "某科技园区管委会"),
            ("bidder.name", "投标人名称", "某某科技有限公司"),
            ("project.bid_validity", "投标有效期", "90 天"),
        ):
            db.add(
                Fact(
                    project_id=project.id,
                    key=key,
                    label=label,
                    value=value,
                    status="confirmed",
                )
            )
        db.add(
            Qualification(
                owner_id=user.id,
                kind="license",
                name="电子与智能化工程专业承包二级",
                holder="某某科技有限公司",
                cert_no="DZ-2026-001",
            )
        )
        db.add(
            Qualification(
                owner_id=user.id,
                kind="performance",
                name="某大楼弱电工程",
                holder="某建设集团",
                cert_no="HT-2025-88",
            )
        )
        db.add(
            Personnel(
                owner_id=user.id,
                name="张三",
                title="项目经理",
                specialty="机电工程",
                cert_no="P-2026-01",
                social_months=24,
            )
        )
        version_id = ""
        if with_quote:
            version = BoqQuoteVersion(
                project_id=project.id,
                package_id=package_id,
                name="定稿报价V1",
                status="approved" if approved else "draft",
                prices={"清单!2": 100.0, "清单!3": 50.5},
                summary={
                    "total_amount": TOTAL,
                    "tax_amount": TAX,
                    "total_with_tax": TOTAL_WITH_TAX,
                },
                basis={
                    "qty": {"清单!2": 2.0, "清单!3": 1.0},
                    "tax_rate": TAX_RATE,
                    "currency": "CNY",
                    "exchange_rate": 1.0,
                    "factor": 1.0,
                    "round_digits": 2,
                },
                reviewed_by="李总",
                reviewed_at=utcnow(),
            )
            db.add(version)
            db.flush()
            version_id = version.id
        return {
            "project_id": project.id,
            "version_id": version_id,
            "user_id": user.id,
            "package_id": package_id,
        }


def _payload(seed: dict[str, str], **kwargs) -> dict:
    with session_scope() as db:
        project = db.get(Project, seed["project_id"])
        return commercial.build_commercial_payload(db, project, **kwargs)


def _fields(section: dict) -> dict:
    return {entry["key"]: entry["value"] for entry in section.get("fields") or []}


def test_bid_letter_fills_from_ledger_without_fabrication():
    seed = _seed_project()
    payload = _payload(seed)
    assert payload["ready"] is True
    assert payload["blocked_reason"] == ""
    assert payload["validation"]["ok"] is True

    letter = _fields(payload["sections"]["bid_letter"])
    assert letter["project_name"] == "园区智能化工程"
    assert letter["project_number"] == "ZB-2026-0412"
    assert letter["buyer"] == "某科技园区管委会"
    assert letter["bidder"] == "某某科技有限公司"
    assert letter["bid_amount"] == TOTAL_WITH_TAX
    assert letter["total_amount"] == TOTAL
    assert letter["currency"] == "CNY"
    # 台账缺少工期/质保期：必须显示占位符而不是编造。
    assert letter["duration"] == "{{待填:工期}}"
    assert letter["warranty"] == "{{待填:质保期}}"


def test_price_schedule_uses_approved_version_prices_and_quantities():
    seed = _seed_project()
    payload = _payload(seed)
    rows = payload["sections"]["price_schedule"]["rows"]
    assert [row["amount"] for row in rows] == [200.0, 50.5]
    assert [row["qty"] for row in rows] == [2.0, 1.0]
    assert payload["sections"]["price_schedule"]["totals"]["total_amount"] == TOTAL
    assert payload["quote"]["reviewed_by"] == "李总"
    assert payload["quote"]["version_id"] == seed["version_id"]


def test_ledger_sections_are_assembled_from_registers():
    seed = _seed_project()
    payload = _payload(seed)
    sections = payload["sections"]
    assert sections["qualification_checklist"]["rows"][0]["name"].startswith("电子与智能化")
    assert sections["performance_table"]["rows"][0]["name"] == "某大楼弱电工程"
    assert sections["personnel_table"]["rows"][0]["name"] == "张三"
    assert _fields(sections["personnel_table"])["project_manager"] == "{{待填:项目负责人}}"
    assert _fields(sections["deviation_table"])["response"] == "{{待填:商务偏离说明}}"


def test_unapproved_version_is_never_used_as_final_basis():
    seed = _seed_project(approved=False)
    payload = _payload(seed, quote_version_id=seed["version_id"])
    assert payload["ready"] is False
    assert "未审批" in payload["blocked_reason"]
    assert payload["quote"]["total_amount"] is None
    assert payload["quote"]["is_approved"] is False
    assert payload["sections"]["price_schedule"]["rows"] == []
    letter = _fields(payload["sections"]["bid_letter"])
    assert letter["bid_amount"].startswith("{{待填:")
    assert payload["validation"]["ok"] is False


def test_no_approved_version_is_marked_explicitly():
    seed = _seed_project(with_quote=False)
    payload = _payload(seed)
    assert payload["ready"] is False
    assert "尚无已审批报价版本" in payload["blocked_reason"]
    assert payload["sections"]["price_schedule"]["rows"] == []
    completeness = payload["completeness"]
    assert completeness["ok"] is False


def test_consistency_detects_amount_tax_currency_and_package_drift():
    seed = _seed_project(with_package=True)
    payload = _payload(seed, package_id=seed["package_id"])
    tampered = copy.deepcopy(payload)
    tampered["sections"]["opening_sheet"]["rows"][0]["total_amount"] = 999.0
    tampered["sections"]["opening_sheet"]["rows"][0]["currency"] = "USD"
    tampered["sections"]["price_schedule"]["rows"][0]["amount"] = 1.0
    tampered["sections"]["price_schedule"]["totals"]["package_id"] = "other-package"
    result = commercial.validate_commercial_consistency(tampered)
    assert result["ok"] is False
    joined = "；".join(result["errors"])
    assert "开标一览投标报价" in joined
    assert "币种" in joined
    assert "包件" in joined


def test_consistency_detects_stale_quote_after_revision():
    seed = _seed_project()
    payload = _payload(seed)
    result = commercial.validate_commercial_consistency(
        payload, current_quote={"version_id": "new-approved-version"}
    )
    assert result["ok"] is False
    assert any("报价已变更" in error for error in result["errors"])


def test_completeness_lists_missing_fields_and_rows():
    seed = _seed_project(with_quote=False)
    payload = _payload(seed)
    missing = {(item["section"], item["field"]) for item in payload["completeness"]["missing"]}
    assert ("quote", "approved_version") in missing
    assert ("bid_letter", "bid_amount") in missing
    assert ("deviation_table", "response") in missing
    assert ("price_schedule", "rows") in missing


def test_explicit_version_infers_package_when_omitted():
    seed = _seed_project(with_package=True)
    payload = _payload(seed, quote_version_id=seed["version_id"])
    assert payload["ready"] is True
    assert payload["package"]["id"] == seed["package_id"]
    assert payload["sections"]["opening_sheet"]["rows"][0]["package"] == "一标段"


def test_completeness_is_ok_when_all_required_fields_are_filled():
    seed = _seed_project()
    with session_scope() as db:
        for key, label, value in (
            ("qualification.requirements", "资格要求响应", "完全响应招标资格要求"),
            ("personnel.project_manager", "项目负责人", "张三"),
            ("commercial.deviation", "商务偏离说明", "完全响应，无偏离"),
        ):
            db.add(
                Fact(
                    project_id=seed["project_id"],
                    key=key,
                    label=label,
                    value=value,
                    status="confirmed",
                )
            )
        db.flush()
    payload = _payload(seed)
    assert payload["completeness"]["ok"] is True
    assert payload["completeness"]["missing"] == []
    assert ("price_schedule", "total_amount") not in {
        (item["section"], item["field"]) for item in payload["completeness"]["missing"]
    }
