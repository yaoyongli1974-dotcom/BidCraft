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
    # /health 始终可达（容器/负载均衡探活）；只有内网模式才附加敏感字段。
    # health_public=True（BIDCRAFT_HEALTH_PUBLIC=minimal/public）时，版本、绝对路径、
    # 模型名与库内计数一律不出现；完整运行状态请走超管 /admin/health。
    body: dict[str, Any] = {
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
    if not settings.health_public:
        body.update(
            {
                "version": __version__,
                "model_label": endpoint.label if endpoint else "",
                "embedding_model": embedding.model_name if embedding else "",
                "models": models,
                "knowledge_bases": kbs,
                "home": str(settings.home),
                "web_ready": (
                    Path(__file__).resolve().parent.parent / "web_dist" / "index.html"
                ).is_file(),
            }
        )
    return body


