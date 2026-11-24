"""v0.30 stage 7：前端出站 payload 与后端 Pydantic 契约的一致性校验。

只读校验：直接 import 生产 schema / service，用本地 FastAPI 应用 + TestClient
重放前端实际发出的字面 payload（与 web/src/api/outboundContract.test.ts 同一批
示例）。不注册生产路由、不写数据库、不改业务规则。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

# 模块级导入：FastAPI 通过函数所在模块的 globals 解析（延迟注释的）类型注解。
from bidcraft.api.schemas import (  # noqa: E402
    CommercialReview,
    KbDocumentIngest,
    QualityFindingUpdate,
    SimilarityHitReview,
    SimilarityRequest,
)
from bidcraft.similarity.service import validate_check  # noqa: E402

# 与 web/src/api/outboundContract.test.ts 中的 UI payload 一一对应。
SIMILARITY_HISTORY = {
    "scope": "history",
    "mode": "semantic",
    "threshold": 0.7,
    "source_project_id": "p0",
}
SIMILARITY_PROJECT = {"scope": "project", "mode": "text", "threshold": 0.85}
HIT_REVIEW = {"status": "suspicious", "note": "待人工判断"}
KB_DOC = {
    "source": "https://example.org/standard.pdf",
    "content": "综合布线系统技术要求",
    "kind": "standard",
    "tags": ["布线", "规范"],
    "valid_to": "2027-12-31",
}
QUALITY_FINDING = {"status": "handled", "note": ""}
COMMERCIAL_REVIEW = {"action": "submit", "note": "", "require_complete": True}

# 旧 UI 发出的非法值（audit fact）：value 本身可被 Pydantic 解析，但 service 会拒绝。
LEGACY_SIMILARITY = {"scope": "external", "mode": "sentence", "threshold": 0.85}


def _client():
    """本地最小应用：路由只包一层生产 schema，不触碰 create_app()。"""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/similarity/check")
    def check(payload: SimilarityRequest):
        try:
            validate_check(payload.mode, payload.scope, payload.threshold)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return payload.model_dump()

    @app.patch("/similarity/hits/{hit_id}")
    def review(hit_id: str, payload: SimilarityHitReview):
        return payload.model_dump()

    @app.post("/kb/documents")
    def kb_document(payload: KbDocumentIngest):
        return payload.model_dump()

    @app.patch("/quality/findings/{finding_id}")
    def quality_finding(finding_id: str, payload: QualityFindingUpdate):
        return payload.model_dump()

    @app.post("/commercial/review")
    def commercial(payload: CommercialReview):
        return payload.model_dump()

    return TestClient(app)


def test_real_ui_payloads_are_accepted_by_backend_schemas():
    client = _client()
    post_cases = [
        ("/similarity/check", SIMILARITY_HISTORY),
        ("/similarity/check", SIMILARITY_PROJECT),
        ("/kb/documents", KB_DOC),
        ("/commercial/review", COMMERCIAL_REVIEW),
    ]
    patch_cases = [
        ("/similarity/hits/h1", HIT_REVIEW),
        ("/quality/findings/f1", QUALITY_FINDING),
    ]
    for method, cases in ((client.post, post_cases), (client.patch, patch_cases)):
        for path, payload in cases:
            response = method(path, json=payload)
            assert response.status_code == 200, (path, response.text)
            for key, value in payload.items():
                assert response.json()[key] == value


def test_pydantic_models_validate_the_same_payloads_directly():
    assert SimilarityRequest.model_validate(SIMILARITY_HISTORY).scope == "history"
    assert SimilarityRequest.model_validate(SIMILARITY_PROJECT).mode == "text"
    assert SimilarityHitReview.model_validate(HIT_REVIEW).status == "suspicious"
    assert KbDocumentIngest.model_validate(KB_DOC).kind == "standard"
    assert QualityFindingUpdate.model_validate(QUALITY_FINDING).status == "handled"
    assert CommercialReview.model_validate(COMMERCIAL_REVIEW).require_complete is True


def test_legacy_ui_enums_are_rejected_by_backend_validation():
    client = _client()
    response = client.post("/similarity/check", json=LEGACY_SIMILARITY)
    assert response.status_code == 400, response.text
    assert "mode" in response.json()["detail"]

    # 质量复核状态是 Literal，非法值在请求体解析阶段被 422 拒绝。
    invalid_finding = client.patch("/quality/findings/f1", json={"status": "resolved"})
    assert invalid_finding.status_code == 422, invalid_finding.text


@pytest.mark.parametrize(
    "mode",
    ["text", "structure", "semantic", "image", "all"],
)
def test_all_backend_modes_and_scopes_are_valid(mode):
    validate_check(mode, "project", 0.85)
    validate_check(mode, "history", 0.85)

    with pytest.raises(ValueError):
        validate_check(mode, "external", 0.85)
