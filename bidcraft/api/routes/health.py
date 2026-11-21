"""API routes: /health (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ..shared import (  # noqa: F401
    Any,
    KnowledgeBase,
    ModelConfig,
    Path,
    __version__,
    func,
    model_config,
    select,
    session_scope,
    settings,
    wechat,
    wechatpay,
)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    with session_scope() as db:
        endpoint = model_config.resolve_chat_endpoint(db)
        embedding = model_config.resolve_embedding_endpoint(db)
        models = db.scalar(select(func.count()).select_from(ModelConfig)) or 0
        kbs = db.scalar(select(func.count()).select_from(KnowledgeBase)) or 0
    if settings.health_public:
        # 公网探活只返回前端必需的最少字段，不泄露版本、路径与库内计数
        return {
            "status": "ok",
            "offline": settings.offline,
            "model_configured": bool(endpoint),
            "embedding_configured": bool(embedding),
            "auth_required": True,
            "registration": True,
            "wechat_login": wechat.available(),
            "wechat_mock": settings.wechat_mock,
            "wechat_pay": wechatpay.available(),
            "wechat_pay_mock": settings.wechat_pay_mock,
        }
    return {
        "version": __version__,
        "model_configured": bool(endpoint),
        "model_label": endpoint.label if endpoint else "",
        "embedding_configured": bool(embedding),
        "embedding_model": embedding.model_name if embedding else "",
        "offline": settings.offline,
        "models": models,
        "knowledge_bases": kbs,
        "auth_required": True,
        "registration": True,
        "wechat_login": wechat.available(),
        "wechat_mock": settings.wechat_mock,
        "wechat_pay": wechatpay.available(),
        "wechat_pay_mock": settings.wechat_pay_mock,
        "home": str(settings.home),
        "web_ready": (Path(__file__).resolve().parent.parent / "web_dist" / "index.html").is_file(),
    }


