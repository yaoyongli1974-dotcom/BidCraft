"""API routes: /billing (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ..app import (  # noqa: F401
    Any,
    FileResponse,
    HTTPException,
    InvoiceCreate,
    JSONResponse,
    OrderCreate,
    RechargeOrder,
    RedeemRequest,
    Request,
    Response,
    StreamingResponse,
    _reject_sub_billing,
    audit,
    auth,
    billing,
    billing_user,
    compliance,
    invoices,
    io,
    logger,
    require_own_order,
    select,
    session_scope,
    settings,
    wechat,
    wechatpay,
)

router = APIRouter()


@router.get("/billing/summary")
def billing_summary() -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        account = billing.billing_account(db, user)
        return {
            "user": auth.user_payload(user),
            "summary": billing.summary(db, user),
            "transactions": billing.transactions(db, account.id),
            "orders": billing.orders(db, account.id),
            "wechat_login": wechat.available(),
            "wechat_mock": settings.wechat_mock,
            "wechat_pay": wechatpay.available(),
            "wechat_pay_mock": settings.wechat_pay_mock,
        }


@router.post("/billing/orders", status_code=201)
def create_order(payload: OrderCreate) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        _reject_sub_billing(user)
        package = billing.find_package(db, payload.package_id)
        if package is None:
            raise HTTPException(status_code=400, detail="充值套餐不存在")
        order = billing.create_order(db, user.id, package)
        if settings.wechat_pay_mock:
            order.provider = "mock"
            db.flush()
        elif wechatpay.available():
            try:
                order.code_url = wechatpay.create_native(order)
            except wechatpay.PaymentError:
                logger.exception("微信支付下单失败（订单 %s）", order.out_trade_no)
                order.status = "closed"
                db.flush()
                raise HTTPException(
                    status_code=502, detail="微信支付下单失败，详情见服务日志"
                ) from None
        else:
            raise HTTPException(status_code=503, detail="微信支付未配置")
        return billing.order_payload(order)


@router.post("/billing/vip/orders", status_code=201)
def create_vip_order_route(payload: OrderCreate) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        _reject_sub_billing(user)
        package = billing.find_vip_package(db, payload.package_id)
        if package is None:
            raise HTTPException(status_code=400, detail="VIP 套餐不存在")
        order = billing.create_vip_order(db, user.id, package)
        if settings.wechat_pay_mock:
            order.provider = "mock"
            db.flush()
        elif wechatpay.available():
            try:
                order.code_url = wechatpay.create_native(order)
            except wechatpay.PaymentError:
                logger.exception("微信支付下单失败（VIP 订单 %s）", order.out_trade_no)
                order.status = "closed"
                db.flush()
                raise HTTPException(
                    status_code=502, detail="微信支付下单失败，详情见服务日志"
                ) from None
        else:
            raise HTTPException(status_code=503, detail="微信支付未配置")
        return billing.order_payload(order)


@router.get("/billing/orders/{order_id}")
def get_order(order_id: str) -> dict[str, Any]:
    with session_scope() as db:
        return billing.order_payload(require_own_order(db, order_id))


@router.post("/billing/orders/{order_id}/mock-pay")
def mock_pay(order_id: str) -> dict[str, Any]:
    if not settings.wechat_pay_mock:
        raise HTTPException(status_code=404, detail="未开启支付模拟")
    with session_scope() as db:
        order = require_own_order(db, order_id)
        billing.mark_paid(db, order, transaction_id=f"MOCK-{order.out_trade_no}", provider="mock")
        user = billing_user(db)
        return {
            "order": billing.order_payload(order),
            "user": auth.user_payload(user),
            "summary": billing.summary(db, user),
        }


@router.get("/billing/orders/{order_id}/qr")
def order_qr(order_id: str):
    with session_scope() as db:
        order = require_own_order(db, order_id)
        code_url = order.code_url
    if not code_url:
        raise HTTPException(status_code=404, detail="该订单没有支付二维码")
    try:
        import qrcode
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail="缺少 qrcode 依赖") from exc
    image = qrcode.make(code_url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return StreamingResponse(
        io.BytesIO(buffer.getvalue()),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/billing/redeem")
def redeem(payload: RedeemRequest) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        _reject_sub_billing(user)
        try:
            billing.redeem(db, user, payload.code)
        except billing.BillingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "user": auth.user_payload(user),
            "summary": billing.summary(db, user),
            "transactions": billing.transactions(db, user.id),
        }


@router.post("/billing/wechat/notify")
async def wechat_notify(request: Request):
    body = await request.body()
    try:
        data = wechatpay.parse_notification(dict(request.headers), body)
    except wechatpay.PaymentError as exc:
        return JSONResponse(
            status_code=400, content={"code": "FAIL", "message": str(exc)}
        )
    with session_scope() as db:
        order = db.scalar(
            select(RechargeOrder).where(
                RechargeOrder.out_trade_no == data["out_trade_no"]
            )
        )
        if order is None:
            return JSONResponse(
                status_code=404, content={"code": "FAIL", "message": "订单不存在"}
            )
        amount = (data.get("amount") or {}).get("total")
        if amount is not None and int(amount) != order.amount_cents:
            return JSONResponse(
                status_code=400, content={"code": "FAIL", "message": "金额不一致"}
            )
        if data.get("trade_state") == "SUCCESS":
            billing.mark_paid(
                db,
                order,
                transaction_id=data.get("transaction_id", ""),
                provider="wechat",
            )
    return {"code": "SUCCESS", "message": "成功"}


@router.get("/billing/invoices")
def billing_invoices() -> list[dict[str, Any]]:
    with session_scope() as db:
        user = billing_user(db)
        return [invoices.invoice_payload(row) for row in invoices.list_requests(db, user.id)]


@router.post("/billing/invoices", status_code=201)
def billing_invoice_create(payload: InvoiceCreate) -> dict[str, Any]:
    with session_scope() as db:
        user = billing_user(db)
        try:
            row = invoices.create_request(
                db,
                user,
                title=payload.title,
                amount_cents=payload.amount_cents,
                tax_no=payload.tax_no,
                email=payload.email,
                note=payload.note,
            )
        except invoices.InvoiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit(db, None, "invoice.request", {"invoice_id": row.id, "amount_cents": row.amount_cents})
        return invoices.invoice_payload(row)


@router.get("/billing/invoices/{invoice_id}/file")
def billing_invoice_file(invoice_id: str) -> FileResponse:
    with session_scope() as db:
        user = billing_user(db)
        try:
            path = invoices.invoice_file(db, invoice_id, user.id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, filename=path.name)


@router.get("/billing/statement")
def billing_statement(start: str = "", end: str = "") -> Response:
    with session_scope() as db:
        user = billing_user(db)
        body = compliance.statement_csv(db, user.id, start=start, end=end)
    return Response(
        content=body.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="bidcraft-statement.csv"'
        },
    )


