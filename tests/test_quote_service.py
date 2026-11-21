"""报价域 Service 层：脱离 FastAPI 独立测试 + 多包件改价 bug 回归 + 审计身份差异。

QuoteService 把「解析包件 → 改价 → 审计」编排从 HTTP/CLI 路由收敛到一处。本文件
直接用 db fixture（不构造 app）验证：
- 同一 Service 在传/不传 package_id 时命中正确的包件快照（旧 CLI 漏传 package_id
  会落到 package_id IS NULL 的快照，多包件项目静默改错/改空）；
- 审计身份由显式 actor 承载：HTTP 传真实用户 → 记真实 user_id/username；CLI/MCP
  传 None → 记 system（与既有入口定位一致）；
- 领域异常原样上抛，由入口映射响应格式。
"""

from __future__ import annotations

import pytest
from openpyxl import Workbook

from bidcraft import boq
from bidcraft.models import AuditLog, BidPackage
from bidcraft.pipeline import create_project
from bidcraft.services.quote_service import PRICE_AUDIT_ACTION, set_unit_price


def _write_boq(path, rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    sheet.append(["序号", "材料名称", "规格型号", "单位", "数量", "备注"])
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    return path


def _package(db, project, name: str = "包件A") -> BidPackage:
    row = BidPackage(project_id=project.id, name=name)
    db.add(row)
    db.flush()
    return row


def _audits(db, project_id: str) -> list[AuditLog]:
    from sqlalchemy import select

    return list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.project_id == project_id, AuditLog.action == PRICE_AUDIT_ACTION)
            .order_by(AuditLog.id)
        )
    )


def test_set_price_single_package_writes_audit(db, tmp_path):
    """无包件项目：Service 可独立调用，改价 + 审计在同一事务。"""
    from bidcraft.db import session_scope

    with session_scope() as session:
        project = create_project(session, "单包件改价")
        path = _write_boq(
            tmp_path / "boq.xlsx",
            [[1, "网络面板", "双口 Cat6A", "个", "2", ""]],
        )
        boq.import_boq(session, project, path)

        result = set_unit_price(session, project, 2, 12.5)
        row = next(r for r in result["rows"] if r["boq_row"] == 2)
        assert row["price"]["unit_price"] == 12.5
        assert result["summary"]["priced"] == 1
        assert result["summary"]["total_amount"] == 25.0

        audits = _audits(session, project.id)
        assert len(audits) == 1
        assert audits[0].username == "system"  # 不传 actor → system
        assert audits[0].user_id is None
        assert audits[0].detail["row"] == 2
        assert audits[0].detail["unit_price"] == 12.5
        assert audits[0].detail["package_id"] is None


def test_multi_package_requires_package_id_and_hits_right_snapshot(db, tmp_path):
    """多包件 bug 回归：不指定 package_id 直接报错；指定则命中该包件快照。"""
    from bidcraft.db import session_scope

    with session_scope() as session:
        project = create_project(session, "多包件改价")
        pkg_a = _package(session, project, "包件A")
        pkg_b = _package(session, project, "包件B")
        path = _write_boq(
            tmp_path / "boq.xlsx",
            [[1, "网络面板", "双口 Cat6A", "个", "2", ""]],
        )
        boq.import_boq(session, project, path, package_id=pkg_a.id)
        boq.import_boq(session, project, path, package_id=pkg_b.id)

        # 不指定包件 → resolve_package 抛 ValueError（旧 CLI 静默用 IS NULL 快照）
        with pytest.raises(ValueError, match="请指定包件"):
            set_unit_price(session, project, 2, 12.5)
        assert _audits(session, project.id) == []

        # 指定包件 → 改价落到正确快照，审计记 package_id
        result = set_unit_price(session, project, 2, 12.5, package_id=pkg_a.id)
        row = next(r for r in result["rows"] if r["boq_row"] == 2)
        assert row["price"]["unit_price"] == 12.5
        audits = _audits(session, project.id)
        assert len(audits) == 1
        assert audits[0].detail["package_id"] == pkg_a.id

        # 另一个包件不受影响（仍是未报价）
        b = boq.build_payload(session, project.id, pkg_b.id)
        assert b["summary"]["priced"] == 0


def test_actor_identity_override(db, tmp_path):
    """显式 actor → 审计记真实身份；None → system（HTTP/CLI 差异由参数承载）。"""
    from bidcraft.db import session_scope

    actor = {"id": "u-123", "username": "zhang", "display_name": "张工"}

    with session_scope() as session:
        project = create_project(session, "审计身份")
        path = _write_boq(
            tmp_path / "boq.xlsx",
            [[1, "线缆", "Cat6A", "米", "10", ""]],
        )
        boq.import_boq(session, project, path)

        set_unit_price(session, project, 2, 3.0, actor=actor)
        set_unit_price(session, project, 2, 3.5)  # 不传 actor

        audits = _audits(session, project.id)
        assert len(audits) == 2
        first, second = audits
        assert first.user_id == "u-123"
        assert first.username == "zhang"
        assert first.display_name == "张工"
        assert second.username == "system"
        assert second.user_id is None


def test_conflict_propagates_without_audit(db):
    """无清单快照 → BoqConflictError 原样上抛，且不写审计。"""
    from bidcraft.db import session_scope

    with session_scope() as session:
        project = create_project(session, "无快照")
        with pytest.raises(boq.BoqConflictError):
            set_unit_price(session, project, 2, 12.5)
        assert _audits(session, project.id) == []
