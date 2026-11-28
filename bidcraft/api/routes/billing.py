"""API routes: /billing (从 app.py 迁移，URL 与响应格式不变)。"""

import sys

from fastapi import APIRouter

_core = sys.modules['bidcraft.api.app']  # 运行期查找，保持 monkeypatch 生效

from ..shared import (  # noqa: F401
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

#: 微信支付回调 body 上限（1 MiB）。超限直接 413，不进入签名校验/解密/DB。
MAX_NOTIFY_BODY_BYTES = 1024 * 1024


def _notify_fail(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": "FAIL", "message": message})


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
        # 临时打开模拟支付时，绝不能零成本支付真实渠道订单。
        if order.provider != "mock":
            raise HTTPException(status_code=404, detail="订单不存在")
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
    if len(body) > MAX_NOTIFY_BODY_BYTES:
        return _notify_fail("回调数据超过 1 MiB", 413)
    try:
        data = wechatpay.parse_notification(dict(request.headers), body)
    except wechatpay.PaymentError as exc:
        return _notify_fail(str(exc))

    # parse_notification 已做强校验；这里再校验一次归一化结果，防止直接
    # monkeypatch/未来新增解析实现时跳过关键字段。
    if not isinstance(data, dict):
        return _notify_fail("回调数据格式无效")
    out_trade_no = data.get("out_trade_no")
    if not isinstance(out_trade_no, str) or not out_trade_no.strip():
        return _notify_fail("回调订单号无效")
    trade_state = data.get("trade_state")
    if not isinstance(trade_state, str) or trade_state not in wechatpay.KNOWN_TRADE_STATES:
        return _notify_fail("回调交易状态无效")
    amount = data.get("amount")
    if type(amount) is not int or amount <= 0:
        return _notify_fail("回调金额无效")
    if data.get("currency") != "CNY":
        return _notify_fail("回调币种不支持")
    for field in ("mchid", "appid"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            return _notify_fail(f"回调 {field} 无效")
    event_type = data.get("event_type")
    if not isinstance(event_type, str) or not event_type.startswith("TRANSACTION."):
        return _notify_fail("回调事件类型无效")
    transaction_id = data.get("transaction_id") or ""
    if not isinstance(transaction_id, str):
        return _notify_fail("回调交易号无效")
    if trade_state == "SUCCESS" and not transaction_id.strip():
        return _notify_fail("回调交易号无效")

    with session_scope() as db:
        order = db.scalar(
            select(RechargeOrder).where(RechargeOrder.out_trade_no == out_trade_no)
        )
        if order is None:
            return _notify_fail("订单不存在", 404)
        if order.provider != "wechat":
            return _notify_fail("订单渠道不匹配")
        if amount != order.amount_cents:
            return _notify_fail("金额不一致")
        # 非 SUCCESS 状态只确认收到，绝不入账；closed/payerror 也不能让回调 500。
        if trade_state != "SUCCESS":
            return {"code": "SUCCESS", "message": "成功"}
        paid = billing.mark_paid(
            db,
            order,
            transaction_id=transaction_id,
            provider="wechat",
        )
        # mark_paid 对已支付订单幂等返回 False：重复回调不再写重复审计行。
        if paid:
            audit(
                db,
                None,
                "billing.payment.notify",
                {
                    "order_id": order.id,
                    "out_trade_no": order.out_trade_no,
                    "amount_cents": order.amount_cents,
                    "transaction_id": transaction_id,
                    "provider": "wechat",
                },
            )
        else:
            logger.info("微信支付重复通知，忽略审计：%s", order.out_trade_no)
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


