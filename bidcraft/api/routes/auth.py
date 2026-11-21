"""API routes: /auth (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ..shared import (  # noqa: F401
    Any,
    HTTPException,
    LoginRequest,
    PasswordChange,
    RedirectResponse,
    RegisterRequest,
    Request,
    Response,
    User,
    _wechat_login,
    auth,
    compliance,
    hash_password,
    quote,
    session_scope,
    set_session_cookie,
    settings,
    verify_password,
    wechat,
)

router = APIRouter()


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    with session_scope() as db:
        try:
            user, token = auth.login(
                db,
                payload.username,
                payload.password,
                user_agent=request.headers.get("user-agent", ""),
                ip=request.client.host if request.client else "",
            )
            auth.purge_expired_sessions(db)
        except PermissionError as exc:
            status = 429 if "次数过多" in str(exc) else 401
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        response.set_cookie(
            auth.SESSION_COOKIE,
            token,
            max_age=settings.session_days * 86400,
            httponly=True,
            samesite="lax",
            secure=settings.secure_cookie,
            path="/",
        )
        return {"user": auth.user_payload(user)}


@router.post("/auth/register", status_code=201)
def register(payload: RegisterRequest, request: Request, response: Response) -> dict[str, Any]:
    with session_scope() as db:
        try:
            if not payload.accept_terms:
                raise ValueError("请先阅读并同意服务条款与隐私政策")
            user = auth.register_user(
                db,
                username=payload.username,
                password=payload.password,
                display_name=payload.display_name,
            )
            compliance.record_consent(user, payload.terms_version)
            # Commit the new account before auth.login opens its own session
            # for the persistent login-throttle counter (SQLite single writer).
            db.commit()
            user, token = auth.login(
                db,
                user.username,
                payload.password,
                user_agent=request.headers.get("user-agent", ""),
                ip=request.client.host if request.client else "",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        set_session_cookie(response, token)
        return {"user": auth.user_payload(user)}


@router.get("/auth/wechat/start")
def wechat_start(next: str = "/") -> RedirectResponse:
    if not wechat.available():
        raise HTTPException(status_code=503, detail="微信登录未配置")
    return RedirectResponse(wechat.authorize_url(next))


@router.get("/auth/wechat/callback")
def wechat_callback(
    request: Request, response: Response, code: str = "", state: str = ""
) -> RedirectResponse:
    try:
        next_path = wechat.verify_state_token(state)
    except wechat.WeChatError as exc:
        return RedirectResponse(
            f"/login?reason=wechat&message={quote(str(exc))}", status_code=302
        )
    if not code:
        return RedirectResponse(
            f"/login?reason=wechat&message={quote('未获得微信授权码')}", status_code=302
        )
    try:
        profile = wechat.exchange_code(code)
    except wechat.WeChatError as exc:
        return RedirectResponse(
            f"/login?reason=wechat&message={quote(str(exc))}", status_code=302
        )
    return _wechat_login(profile, response, next_path)


@router.get("/auth/wechat/mock")
def wechat_mock_login(
    response: Response, next: str = "/", openid: str = "mock-openid"
) -> RedirectResponse:
    if not settings.wechat_mock:
        raise HTTPException(status_code=404, detail="未开启微信模拟登录")
    return _wechat_login(wechat.mock_profile(openid), response, next)


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    with session_scope() as db:
        auth.revoke_session(db, request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"logged_out": True}


@router.get("/auth/me")
def me(request: Request) -> dict[str, Any]:
    return {"user": request.state.user}


@router.post("/auth/password")
def change_password(payload: PasswordChange, request: Request, response: Response):
    with session_scope() as db:
        user = db.get(User, request.state.user["id"])
        if user is None or not verify_password(payload.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="当前密码不正确")
        user.password_hash = hash_password(payload.new_password)
        auth.revoke_user_sessions(db, user.id)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"changed": True, "relogin": True}


