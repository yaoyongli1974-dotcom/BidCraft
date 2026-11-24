"""API routes: /health (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter, HTTPException

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
                    Path(__file__).resolve().parents[2] / "web_dist" / "index.html"
                ).is_file(),
            }
        )
    return body


@router.get("/health/live")
def health_live() -> dict[str, Any]:
    """容器存活探针：固定 200，不查库、不依赖任何外部组件。"""

    return {"status": "ok"}


@router.get("/health/ready")
def health_ready() -> dict[str, Any]:
    """就绪探针：DB 可读、模型可用（离线模式豁免）且 Web 客户端已构建。

    任一项不满足返回 503；存活探针不受影响，避免 DB 抖动触发无谓重启。
    """

    try:
        with session_scope() as db:
            db.execute(select(1))
            endpoint = model_config.resolve_chat_endpoint(db)
    except Exception:  # noqa: BLE001 - 探针必须返回 503 而不是 500
        raise HTTPException(status_code=503, detail="依赖检查失败：数据库不可用") from None
    if endpoint is None and not settings.offline:
        raise HTTPException(status_code=503, detail="依赖检查失败：对话模型未配置")
    web_ready = (
        Path(__file__).resolve().parents[2] / "web_dist" / "index.html"
    ).is_file()
    if not web_ready:
        raise HTTPException(status_code=503, detail="依赖检查失败：Web 客户端未构建")
    return {
        "status": "ready",
        "db": "ok",
        "model_configured": bool(endpoint),
        "web_ready": True,
    }


