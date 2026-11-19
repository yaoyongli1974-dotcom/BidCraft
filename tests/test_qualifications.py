from __future__ import annotations

from datetime import date, timedelta

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import select  # noqa: E402


def _client(username: str = "qualuser"):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={"username": username, "password": "qual-pass-1", "display_name": username},
    )
    assert response.status_code == 201, response.text
    return client, response.json()["user"]


def _create(client, **overrides):
    body = {
        "kind": "license",
        "name": "电子与智能化工程专业承包二级",
        "holder": "某某科技有限公司",
        "cert_no": "D234567890",
        "issuer": "省住建厅",
        "valid_from": "2024-01-01",
        "valid_to": "2027-12-31",
    }
    body.update(overrides)
    response = client.post("/my/qualifications", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_status_of_and_days_left():
    from bidcraft.qualifications import days_left, status_of

    today = date(2026, 6, 1)
    assert status_of(None, today=today) == "none"
    assert status_of(date(2026, 8, 1), today=today) == "valid"
    assert status_of(date(2026, 7, 1), today=today) == "expiring"
    assert status_of(date(2026, 6, 1), today=today) == "expiring"
    assert status_of(date(2026, 5, 31), today=today) == "expired"
    assert days_left(None, today=today) is None
    assert days_left(date(2026, 7, 1), today=today) == 30
    assert days_left(date(2026, 5, 31), today=today) == -1


def test_crud_payload_and_owner_isolation(db):
    owner, _owner_user = _client("qualowner")
    other, _other_user = _client("qualother")

    created = _create(owner)
    assert created["kind"] == "license"
    assert created["kind_label"] == "资质证照"
    assert created["name"].startswith("电子与智能化")
    assert created["valid_from"] == "2024-01-01"
    assert created["valid_to"] == "2027-12-31"
    assert created["status"] in {"valid", "expiring", "expired"}
    assert isinstance(created["days_left"], int)
    assert created["evidence"] == []
    assert created["evidence_count"] == 0
    qualification_id = created["id"]

    listed = owner.get("/my/qualifications")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [qualification_id]
    assert other.get("/my/qualifications").json() == []
    assert other.get(f"/my/qualifications/{qualification_id}/evidence/a.png").status_code == 404

    patched = owner.patch(
        f"/my/qualifications/{qualification_id}",
        json={"cert_no": "D999", "valid_to": "2030-01-01"},
    )
    assert patched.status_code == 200
    assert patched.json()["cert_no"] == "D999"
    assert patched.json()["valid_to"] == "2030-01-01"

    assert other.patch(
        f"/my/qualifications/{qualification_id}", json={"name": "劫持"}
    ).status_code == 404
    assert other.delete(f"/my/qualifications/{qualification_id}").status_code == 404

    deleted = owner.delete(f"/my/qualifications/{qualification_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": qualification_id}
    assert owner.get("/my/qualifications").json() == []


def test_payload_no_expiry_and_kind_labels(db):
    from bidcraft.qualifications import KIND_LABELS

    owner, _user = _client("qualnone")
    created = _create(owner, kind="other", name="其他资料", valid_from=None, valid_to=None)
    assert created["status"] == "none"
    assert created["days_left"] is None
    assert created["valid_to"] is None
    assert created["kind_label"] == KIND_LABELS["other"]


def test_kind_and_name_validation(db):
    owner, _user = _client("qualinvalid")
    bad_kind = owner.post(
        "/my/qualifications", json={"kind": "bogus", "name": "某某证书"}
    )
    assert bad_kind.status_code == 400
    assert "资质类型" in bad_kind.json()["detail"]

    empty_name = owner.post("/my/qualifications", json={"kind": "license", "name": "  "})
    assert empty_name.status_code == 400
    assert empty_name.json()["detail"] == "名称不能为空"

    too_long = owner.post(
        "/my/qualifications", json={"kind": "license", "name": "证" * 121}
    )
    assert too_long.status_code == 400
    assert "120" in too_long.json()["detail"]


def test_date_validation(db):
    owner, _user = _client("qualdate")
    backwards = owner.post(
        "/my/qualifications",
        json={
            "kind": "license",
            "name": "资质证书",
            "valid_from": "2027-01-01",
            "valid_to": "2026-01-01",
        },
    )
    assert backwards.status_code == 400
    assert backwards.json()["detail"] == "到期日不能早于起始日"

    malformed = owner.post(
        "/my/qualifications",
        json={"kind": "license", "name": "资质证书", "valid_to": "2026-13-01"},
    )
    assert malformed.status_code == 400
    assert "YYYY-MM-DD" in malformed.json()["detail"]

    created = _create(owner, name="待改日期")
    patched = owner.patch(
        f"/my/qualifications/{created['id']}", json={"valid_from": "2031-01-01"}
    )
    assert patched.status_code == 400
    assert patched.json()["detail"] == "到期日不能早于起始日"


def test_cert_no_length_limit(db):
    owner, _user = _client("qualcertno")
    response = owner.post(
        "/my/qualifications",
        json={"kind": "license", "name": "资质证书", "cert_no": "X" * 81},
    )
    assert response.status_code == 400
    assert "证书号不能超过 80 字" in response.json()["detail"]


def test_list_filters_by_kind_and_status(db):
    owner, _user = _client("qualfilter")
    expired = _create(
        owner,
        kind="safety",
        name="安全生产许可证",
        valid_from=None,
        valid_to="2020-01-01",
    )
    _create(owner, kind="credit", name="信用记录", valid_to="2099-01-01")

    by_kind = owner.get("/my/qualifications", params={"kind": "safety"})
    assert by_kind.status_code == 200
    assert [row["id"] for row in by_kind.json()] == [expired["id"]]

    by_status = owner.get("/my/qualifications", params={"status": "expired"})
    assert [row["id"] for row in by_status.json()] == [expired["id"]]

    assert owner.get("/my/qualifications", params={"kind": "nope"}).status_code == 400
    assert owner.get("/my/qualifications", params={"status": "nope"}).status_code == 400


def test_evidence_upload_download_and_bounds(db):
    owner, _user = _client("qualevidence")
    other, _other = _client("qualevidence2")
    created = _create(owner)
    qualification_id = created["id"]

    uploaded = owner.post(
        f"/my/qualifications/{qualification_id}/evidence",
        files={"file": ("资质证书.png", b"\x89PNG\r\n\x1a\ncert", "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["evidence_count"] == 1
    entry = body["evidence"][0]
    assert entry["filename"] == "资质证书.png"
    assert entry["size"] == len(b"\x89PNG\r\n\x1a\ncert")
    assert entry["mime"] == "image/png"
    assert entry["path"].endswith("资质证书.png")
    assert entry["uploaded_at"]

    from pathlib import Path

    from bidcraft.qualifications import evidence_dir

    assert Path(entry["path"]).parent == evidence_dir(_user["id"])
    assert Path(entry["path"]).is_file()

    downloaded = owner.get(
        f"/my/qualifications/{qualification_id}/evidence/资质证书.png"
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"\x89PNG\r\n\x1a\ncert"

    # 他人的资质/证据不可见；越界与未知文件名同样 404。
    assert other.get(
        f"/my/qualifications/{qualification_id}/evidence/资质证书.png"
    ).status_code == 404
    assert other.post(
        f"/my/qualifications/{qualification_id}/evidence",
        files={"file": ("x.png", b"abc", "image/png")},
    ).status_code == 404
    assert owner.get(
        f"/my/qualifications/{qualification_id}/evidence/..%5Csecret.png"
    ).status_code == 404
    assert owner.get(
        f"/my/qualifications/{qualification_id}/evidence/missing.png"
    ).status_code == 404

    # 同名再传不覆盖旧文件，文件名自动加序号。
    again = owner.post(
        f"/my/qualifications/{qualification_id}/evidence",
        files={"file": ("资质证书.png", b"second", "image/png")},
    )
    assert again.status_code == 201
    names = [item["filename"] for item in again.json()["evidence"]]
    assert names == ["资质证书.png", "资质证书-1.png"]


def test_evidence_suffix_and_size_rejected(db, monkeypatch):
    from bidcraft import qualifications

    owner, _user = _client("qualevidencebad")
    created = _create(owner)
    qualification_id = created["id"]

    bad_suffix = owner.post(
        f"/my/qualifications/{qualification_id}/evidence",
        files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
    )
    assert bad_suffix.status_code == 400
    assert "证据仅支持" in bad_suffix.json()["detail"]

    monkeypatch.setattr(qualifications, "MAX_EVIDENCE_BYTES", 4)
    oversized = owner.post(
        f"/my/qualifications/{qualification_id}/evidence",
        files={"file": ("big.png", b"12345", "image/png")},
    )
    assert oversized.status_code == 400
    assert oversized.json()["detail"] == "证据文件不能超过 10MB"

    empty = owner.post(
        f"/my/qualifications/{qualification_id}/evidence",
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert empty.status_code == 400
    assert empty.json()["detail"] == "证据文件不能为空"


def test_delete_removes_evidence_files(db):
    from pathlib import Path

    owner, _user = _client("qualdel")
    created = _create(owner)
    qualification_id = created["id"]
    uploaded = owner.post(
        f"/my/qualifications/{qualification_id}/evidence",
        files={"file": ("scan.pdf", b"%PDF-1.4 evidence", "application/pdf")},
    )
    assert uploaded.status_code == 201
    stored = Path(uploaded.json()["evidence"][0]["path"])
    assert stored.is_file()

    assert owner.delete(f"/my/qualifications/{qualification_id}").status_code == 200
    assert not stored.exists()


def test_preflight_ok_expiring_expired_missing(db):
    owner, _user = _client("qualpreflight")
    deadline = date(2026, 6, 1)

    _create(owner, kind="license", name="营业执照", valid_to="2026-07-01")
    _create(owner, kind="safety", name="安全生产许可证", valid_to="2026-01-01")
    person = _create(
        owner, kind="person", name="一级建造师", valid_from=None, valid_to=None
    )
    owner.post(
        f"/my/qualifications/{person['id']}/evidence",
        files={"file": ("注册证书.png", b"cert", "image/png")},
    )

    response = owner.get("/my/preflight", params={"deadline": deadline.isoformat()})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["deadline"] == "2026-06-01"
    items = {item["key"]: item for item in report["items"]}
    assert set(items) == {
        "business_license",
        "qualification_cert",
        "safety_license",
        "project_manager",
        "performance",
        "credit",
    }

    assert items["business_license"]["status"] == "expiring"
    assert items["qualification_cert"]["status"] == "expiring"
    assert items["safety_license"]["status"] == "expired"
    assert items["project_manager"]["status"] == "ok"
    assert items["project_manager"]["evidence_count"] == 1
    assert items["project_manager"]["name"] == "一级建造师"
    assert items["performance"]["status"] == "missing"
    assert items["performance"]["qualification_id"] is None
    assert items["performance"]["valid_to"] is None
    assert items["credit"]["status"] == "missing"

    assert report["summary"] == {"ok": 1, "warn": 3, "missing": 2, "total": 6}


def test_preflight_picks_latest_qualification_of_kind(db):
    owner, _user = _client("quallatest")
    _create(owner, kind="license", name="旧营业执照", valid_to="2025-01-01")
    latest = _create(owner, kind="license", name="新营业执照", valid_to="2030-01-01")

    report = owner.get(
        "/my/preflight", params={"deadline": "2026-06-01"}
    ).json()
    items = {item["key"]: item for item in report["items"]}
    assert items["business_license"]["qualification_id"] == latest["id"]
    assert items["business_license"]["name"] == "新营业执照"
    assert items["business_license"]["status"] == "ok"


def test_preflight_deadline_changes_status(db):
    owner, _user = _client("qualdeadline")
    record = _create(owner, kind="safety", name="安全生产许可证", valid_to="2026-06-10")

    early = owner.get("/my/preflight", params={"deadline": "2026-05-01"}).json()
    exact = owner.get("/my/preflight", params={"deadline": "2026-06-01"}).json()
    late = owner.get("/my/preflight", params={"deadline": "2026-07-01"}).json()
    statuses = [
        {item["key"]: item["status"] for item in report["items"]}["safety_license"]
        for report in (early, exact, late)
    ]
    assert statuses == ["ok", "expiring", "expired"]
    late_items = {item["key"]: item for item in late["items"]}
    assert late_items["safety_license"]["qualification_id"] == record["id"]


def test_preflight_defaults_to_today_and_rejects_bad_date(db):
    owner, _user = _client("qualtoday")
    today = date.today()
    _create(
        owner,
        kind="safety",
        name="安全生产许可证",
        valid_to=(today + timedelta(days=10)).isoformat(),
    )
    report = owner.get("/my/preflight").json()
    assert report["deadline"] == today.isoformat()
    items = {item["key"]: item for item in report["items"]}
    assert items["safety_license"]["status"] == "expiring"

    bad = owner.get("/my/preflight", params={"deadline": "2026/06/01"})
    assert bad.status_code == 400
    assert bad.json()["detail"] == "日期格式无效，应为 YYYY-MM-DD"


def test_audit_trail(db):
    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    owner, _user = _client("qualaudit")
    created = _create(owner)
    qualification_id = created["id"]
    owner.patch(f"/my/qualifications/{qualification_id}", json={"note": "已核对"})
    owner.post(
        f"/my/qualifications/{qualification_id}/evidence",
        files={"file": ("audit.png", b"x", "image/png")},
    )
    owner.delete(f"/my/qualifications/{qualification_id}")

    with session_scope() as sess:
        actions = [
            row.action
            for row in sess.scalars(
                select(AuditLog).where(AuditLog.action.like("qualification.%"))
            )
        ]
    assert actions.count("qualification.create") == 1
    assert actions.count("qualification.update") == 1
    assert actions.count("qualification.evidence") == 1
    assert actions.count("qualification.delete") == 1


def test_viewer_cannot_write(db):
    from bidcraft.db import session_scope
    from bidcraft.models import User

    viewer, viewer_user = _client("qualviewer")
    _create(viewer)

    with session_scope() as sess:
        row = sess.get(User, viewer_user["id"])
        row.role = "viewer"

    assert viewer.get("/my/qualifications").status_code == 200
    assert viewer.get("/my/preflight").status_code == 200
    blocked = viewer.post("/my/qualifications", json={"kind": "license", "name": "x"})
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "只读成员不能执行此操作"


def test_evidence_delete_success_and_missing_file(db):
    from pathlib import Path

    from bidcraft.db import session_scope
    from bidcraft.models import AuditLog

    owner, _user = _client("qualevidencedel")
    created = _create(owner)
    qualification_id = created["id"]
    first = owner.post(
        f"/my/qualifications/{qualification_id}/evidence",
        files={"file": ("证书A.png", b"aaa", "image/png")},
    ).json()["evidence"][0]
    second = owner.post(
        f"/my/qualifications/{qualification_id}/evidence",
        files={"file": ("证书B.pdf", b"%PDF-bbb", "application/pdf")},
    ).json()["evidence"][1]
    assert Path(first["path"]).is_file() and Path(second["path"]).is_file()

    deleted = owner.delete(
        f"/my/qualifications/{qualification_id}/evidence/证书A.png"
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "filename": "证书A.png"}
    assert not Path(first["path"]).exists()
    assert Path(second["path"]).is_file()

    listed = owner.get("/my/qualifications").json()[0]
    assert [item["filename"] for item in listed["evidence"]] == ["证书B.pdf"]
    assert listed["evidence_count"] == 1

    # 文件已被外部删除时，仍从 JSON 移除并返回成功。
    Path(second["path"]).unlink()
    missing = owner.delete(
        f"/my/qualifications/{qualification_id}/evidence/证书B.pdf"
    )
    assert missing.status_code == 200
    assert owner.get("/my/qualifications").json()[0]["evidence"] == []

    with session_scope() as sess:
        entries = [
            row.detail
            for row in sess.scalars(
                select(AuditLog).where(AuditLog.action == "qualification.evidence")
            )
        ]
    deletes = [detail for detail in entries if detail.get("action") == "delete"]
    assert [detail["filename"] for detail in deletes] == ["证书A.png", "证书B.pdf"]


def test_evidence_delete_isolation_and_bounds(db):
    from pathlib import Path

    owner, _user = _client("qualevidencedel2")
    other, _other = _client("qualevidencedel3")
    created = _create(owner)
    qualification_id = created["id"]
    uploaded = owner.post(
        f"/my/qualifications/{qualification_id}/evidence",
        files={"file": ("keep.png", b"keep", "image/png")},
    ).json()["evidence"][0]
    stored = Path(uploaded["path"])

    assert other.delete(
        f"/my/qualifications/{qualification_id}/evidence/keep.png"
    ).status_code == 404
    assert owner.delete(
        f"/my/qualifications/{qualification_id}/evidence/unknown.png"
    ).status_code == 404
    assert owner.delete(
        f"/my/qualifications/{qualification_id}/evidence/..%5Csecret.png"
    ).status_code == 404
    assert stored.is_file()
    assert owner.get("/my/qualifications").json()[0]["evidence_count"] == 1
