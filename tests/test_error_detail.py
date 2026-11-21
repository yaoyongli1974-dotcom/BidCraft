"""对外错误响应不回传内部异常细节（路径 / SQL / 第三方报错）。

记实：全库审查发现 ``str(exc)`` 回传绝大多数来自项目自身的领域异常
（ValueError / LookupError / ComplianceError 等），文案是可读的用户提示，
属于正常做法。唯一需要修的是「宽泛 except Exception 直接回传原始异常文本」——
那种情况下异常可能来自文件系统、数据库或第三方库，文本会泄露内部路径与结构。

本文件锁住这条边界：已知领域异常保持可读，未知异常只回安全文案、细节进日志。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

PASSWORD = "detail-pass-1"


def _client(username: str = "detailuser"):
    from fastapi.testclient import TestClient

    from bidcraft.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/auth/register",
        json={
            "username": username,
            "password": PASSWORD,
            "display_name": username,
            "accept_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    return client


def test_account_export_hides_internal_error(db, monkeypatch):
    """账户导出抛未知异常时，500 响应不得含内部路径或原始报错文本。"""

    import bidcraft.compliance as compliance_module

    secret = "/srv/bidcraft/private/secret.key"

    def boom(*_args, **_kwargs):
        raise OSError(f"zipfile cannot open {secret}: permission denied")

    monkeypatch.setattr(compliance_module, "export_account", boom)
    client = _client("detail-export")

    response = client.get("/account/export")
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail == "导出失败，请稍后重试"
    assert secret not in response.text
    assert "permission denied" not in response.text


def test_account_export_still_succeeds_normally(db):
    """安全文案不能掩盖正常导出路径。"""

    client = _client("detail-export-ok")
    response = client.get("/account/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.content[:2] == b"PK"
