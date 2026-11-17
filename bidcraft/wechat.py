"""WeChat Open Platform website QR login (OAuth 2.0).

Real mode requires an approved website application:

* ``BIDCRAFT_WECHAT_APP_ID``
* ``BIDCRAFT_WECHAT_APP_SECRET``
* ``BIDCRAFT_WECHAT_REDIRECT_URI`` (must match the approved callback domain)

``BIDCRAFT_WECHAT_MOCK=1`` enables a local demo path that never contacts
Tencent. It is intended for development and must not be used in production.
"""

from __future__ import annotations

import secrets
from urllib.parse import quote, urlencode

import httpx
from sqlalchemy import select

from .config import settings
from .models import User
from .security import sign_state, verify_state

AUTHORIZE_URL = "https://open.weixin.qq.com/connect/qrconnect"
TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"
USERINFO_URL = "https://api.weixin.qq.com/sns/userinfo"
TIMEOUT = 15.0


class WeChatError(RuntimeError):
    pass


def get_config(db):
    from .models import WechatConfig

    return db.get(WechatConfig, "default")


def effective(db=None) -> dict:
    from .security import decrypt_secret

    if db is not None:
        row = get_config(db)
    else:
        from .db import session_scope

        with session_scope() as session:
            row = get_config(session)
    if row is not None and row.enabled and (
        row.mock or (row.app_id and row.app_secret_enc and row.redirect_uri)
    ):
        return {
            "app_id": row.app_id or "",
            "app_secret": decrypt_secret(row.app_secret_enc) if row.app_secret_enc else "",
            "redirect_uri": row.redirect_uri or "",
            "mock": bool(row.mock) or settings.wechat_mock,
            "source": "db",
        }
    return {
        "app_id": settings.wechat_app_id,
        "app_secret": settings.wechat_app_secret,
        "redirect_uri": settings.wechat_redirect_uri,
        "mock": settings.wechat_mock,
        "source": "env",
    }


def configured(db=None) -> bool:
    cfg = effective(db)
    return bool(cfg["app_id"] and cfg["app_secret"] and cfg["redirect_uri"])


def available(db=None) -> bool:
    return configured(db) or effective(db)["mock"]


def authorize_url(next_path: str = "/", db=None) -> str:
    cfg = effective(db)
    if not (cfg["app_id"] and cfg["app_secret"] and cfg["redirect_uri"]):
        raise WeChatError("微信扫码登录尚未配置")
    state = sign_state(
        {"n": secrets.token_urlsafe(8), "next": _safe_next(next_path)}, ttl_seconds=600
    )
    query = urlencode(
        {
            "appid": cfg["app_id"],
            "redirect_uri": cfg["redirect_uri"],
            "response_type": "code",
            "scope": "snsapi_login",
            "state": state,
        },
        quote_via=quote,
    )
    return f"{AUTHORIZE_URL}?{query}#wechat_redirect"


def _safe_next(value: str) -> str:
    value = (value or "/").strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value[:500]


def verify_state_token(token: str) -> str:
    payload = verify_state(token)
    if payload is None:
        raise WeChatError("登录状态已过期，请重新扫码")
    return _safe_next(str(payload.get("next") or "/"))


def exchange_code(code: str, db=None) -> dict:
    cfg = effective(db)
    if not (cfg["app_id"] and cfg["app_secret"] and cfg["redirect_uri"]):
        raise WeChatError("微信扫码登录尚未配置")
    try:
        response = httpx.get(
            TOKEN_URL,
            params={
                "appid": cfg["app_id"],
                "secret": cfg["app_secret"],
                "code": code,
                "grant_type": "authorization_code",
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        token = response.json()
        if token.get("errcode"):
            raise WeChatError(f"微信授权失败：{token.get('errmsg') or token['errcode']}")
        openid = token.get("openid")
        if not openid:
            raise WeChatError("微信未返回 openid")
        profile = {
            "openid": openid,
            "unionid": token.get("unionid") or "",
            "nickname": "",
            "avatar": "",
        }
        info = httpx.get(
            USERINFO_URL,
            params={
                "access_token": token.get("access_token"),
                "openid": openid,
                "lang": "zh_CN",
            },
            timeout=TIMEOUT,
        )
        if info.status_code == 200:
            data = info.json()
            if not data.get("errcode"):
                profile["nickname"] = data.get("nickname") or ""
                profile["avatar"] = data.get("headimgurl") or ""
                profile["unionid"] = data.get("unionid") or profile["unionid"]
        return profile
    except httpx.HTTPError as exc:
        raise WeChatError(f"微信接口连接失败：{exc}") from exc
    except ValueError as exc:
        raise WeChatError("微信接口返回了无法解析的数据") from exc


def mock_profile(openid: str) -> dict:
    suffix = (openid or "demo").strip()[:40] or "demo"
    return {
        "openid": f"mock-{suffix}",
        "unionid": f"mock-union-{suffix}",
        "nickname": f"微信演示用户{suffix[-4:]}",
        "avatar": "",
        "mock": True,
    }


def login_or_register(db, profile: dict) -> User:
    from .auth import register_user

    openid = profile.get("openid")
    unionid = profile.get("unionid")
    if not openid:
        raise WeChatError("微信身份缺少 openid")
    user = None
    if unionid:
        user = db.scalar(select(User).where(User.wechat_unionid == unionid))
    if user is None:
        user = db.scalar(select(User).where(User.wechat_openid == openid))
    if user is not None:
        if not user.active:
            raise WeChatError("该账号已停用，请联系管理员")
        user.wechat_openid = openid
        user.wechat_unionid = unionid or user.wechat_unionid
        user.wechat_nickname = profile.get("nickname") or user.wechat_nickname
        user.avatar_url = profile.get("avatar") or user.avatar_url
        if profile.get("nickname") and not user.display_name:
            user.display_name = profile["nickname"]
        return user
    base = (unionid or openid).replace("-", "")[:12]
    username = f"wx_{base}"
    index = 1
    while db.scalar(select(User).where(User.username == username)):
        index += 1
        username = f"wx_{base}{index}"
    return register_user(
        db,
        username=username,
        password=secrets.token_urlsafe(18),
        display_name=profile.get("nickname") or "微信用户",
        is_external=True,
        wechat_openid=openid,
        wechat_unionid=unionid or None,
        wechat_nickname=profile.get("nickname") or "",
        avatar_url=profile.get("avatar") or "",
    )
