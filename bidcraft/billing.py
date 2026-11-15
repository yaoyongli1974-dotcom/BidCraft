"""Credits, recharge orders and redeem codes.

External (self-registered) users consume credits per generated chapter. Staff
accounts created by an administrator are billing-exempt. All balance changes
are written to an append-only transaction log.
"""

from __future__ import annotations

import secrets
import string
from datetime import timedelta

from sqlalchemy import func, select, update

from .config import settings
from .models import AppSetting, CreditTransaction, RechargeOrder, RedeemCode, User, utcnow

DEFAULT_PACKAGES = [
    {"id": "starter", "name": "入门包", "credits": 10, "amount_cents": 990},
    {"id": "pro", "name": "专业包", "credits": 50, "amount_cents": 3990},
    {"id": "studio", "name": "工作室包", "credits": 120, "amount_cents": 7990},
]


class BillingError(RuntimeError):
    pass


class InsufficientCredits(BillingError):
    pass


def billing_settings(db) -> dict:
    row = db.get(AppSetting, "billing")
    stored = row.value if row is not None and isinstance(row.value, dict) else {}
    packages = stored.get("packages")
    if not isinstance(packages, list) or not packages:
        packages = DEFAULT_PACKAGES
    return {
        "signup_credits": int(stored.get("signup_credits", settings.signup_credits)),
        "chapter_cost": int(stored.get("chapter_cost", settings.chapter_cost)),
        "packages": packages,
    }


def save_billing_settings(db, payload: dict) -> dict:
    packages = payload.get("packages")
    if not isinstance(packages, list) or not packages:
        raise BillingError("至少需要一个充值套餐")
    normalized = []
    for item in packages:
        try:
            package = {
                "id": str(item["id"]).strip()[:40],
                "name": str(item["name"]).strip()[:60],
                "credits": int(item["credits"]),
                "amount_cents": int(item["amount_cents"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise BillingError("套餐字段不完整") from exc
        if not package["id"] or not package["name"]:
            raise BillingError("套餐 ID 与名称不能为空")
        if package["credits"] <= 0 or package["amount_cents"] <= 0:
            raise BillingError("套餐点数与金额必须大于 0")
        normalized.append(package)
    signup = max(0, int(payload.get("signup_credits", settings.signup_credits)))
    cost = max(0, int(payload.get("chapter_cost", settings.chapter_cost)))
    value = {"signup_credits": signup, "chapter_cost": cost, "packages": normalized}
    row = db.get(AppSetting, "billing")
    if row is None:
        row = AppSetting(key="billing", value=value)
        db.add(row)
    else:
        row.value = value
    db.flush()
    return value


def billing_exempt(user: User) -> bool:
    return user.role == "super_admin" or not user.is_external


def add_credits(
    db,
    user_id: str,
    amount: int,
    *,
    kind: str,
    note: str = "",
    ref_type: str = "",
    ref_id: str = "",
) -> int:
    if amount == 0:
        return db.scalar(select(User.credits).where(User.id == user_id)) or 0
    if amount < 0:
        result = db.execute(
            update(User)
            .where(User.id == user_id, User.credits + amount >= 0)
            .values(credits=User.credits + amount)
        )
        if result.rowcount == 0:
            raise InsufficientCredits("点数不足，请先充值")
    else:
        result = db.execute(
            update(User).where(User.id == user_id).values(credits=User.credits + amount)
        )
        if result.rowcount == 0:
            raise BillingError("用户不存在")
    balance = db.scalar(select(User.credits).where(User.id == user_id)) or 0
    db.add(
        CreditTransaction(
            user_id=user_id,
            amount=amount,
            balance_after=balance,
            kind=kind,
            note=note[:500],
            ref_type=ref_type[:40],
            ref_id=ref_id[:80],
        )
    )
    db.flush()
    return balance


def signup_grant(db, user: User) -> int:
    credits = billing_settings(db)["signup_credits"]
    if credits <= 0:
        return 0
    return add_credits(
        db, user.id, credits, kind="signup", note="注册赠送", ref_type="user", ref_id=user.id
    )


def charge_chapters(db, user: User, count: int, *, project_id: str = "", note: str = "") -> int:
    if count <= 0 or billing_exempt(user):
        return 0
    cost = billing_settings(db)["chapter_cost"] * count
    if cost <= 0:
        return 0
    add_credits(
        db,
        user.id,
        -cost,
        kind="consume",
        note=note or f"生成 {count} 章",
        ref_type="project",
        ref_id=project_id,
    )
    return cost


def refund_chapters(db, user: User, credits: int, *, project_id: str = "") -> int:
    if credits <= 0:
        return 0
    return add_credits(
        db,
        user.id,
        credits,
        kind="refund",
        note="生成未完成退还",
        ref_type="project",
        ref_id=project_id,
    )


def summary(db, user: User) -> dict:
    config = billing_settings(db)
    balance = db.scalar(select(User.credits).where(User.id == user.id)) or 0
    return {
        "balance": balance,
        "chapter_cost": config["chapter_cost"],
        "signup_credits": config["signup_credits"],
        "billing_exempt": billing_exempt(user),
        "packages": config["packages"],
    }


def transactions(db, user_id: str, limit: int = 50) -> list[dict]:
    rows = db.scalars(
        select(CreditTransaction)
        .where(CreditTransaction.user_id == user_id)
        .order_by(CreditTransaction.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": row.id,
            "amount": row.amount,
            "balance_after": row.balance_after,
            "kind": row.kind,
            "note": row.note,
            "ref_type": row.ref_type,
            "ref_id": row.ref_id,
            "created_at": row.created_at.isoformat() + "+00:00" if row.created_at else None,
        }
        for row in rows
    ]


def orders(db, user_id: str, limit: int = 20) -> list[dict]:
    rows = db.scalars(
        select(RechargeOrder)
        .where(RechargeOrder.user_id == user_id)
        .order_by(RechargeOrder.created_at.desc())
        .limit(limit)
    )
    return [order_payload(row) for row in rows]


def order_payload(order: RechargeOrder) -> dict:
    return {
        "id": order.id,
        "out_trade_no": order.out_trade_no,
        "amount_cents": order.amount_cents,
        "credits": order.credits,
        "status": order.status,
        "provider": order.provider,
        "package_id": order.package_id,
        "code_url": order.code_url,
        "transaction_id": order.transaction_id,
        "paid_at": _iso(order.paid_at),
        "created_at": _iso(order.created_at),
    }


def _iso(value) -> str | None:
    from datetime import UTC

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def find_package(db, package_id: str) -> dict | None:
    for package in billing_settings(db)["packages"]:
        if str(package.get("id")) == package_id:
            return package
    return None


def create_order(db, user_id: str, package: dict, provider: str = "wechat") -> RechargeOrder:
    out_trade_no = "BC" + utcnow().strftime("%Y%m%d%H%M%S") + _random_code(6)
    order = RechargeOrder(
        user_id=user_id,
        out_trade_no=out_trade_no,
        amount_cents=int(package["amount_cents"]),
        credits=int(package["credits"]),
        package_id=str(package["id"]),
        provider=provider,
    )
    db.add(order)
    db.flush()
    return order


def mark_paid(db, order: RechargeOrder, *, transaction_id: str = "", provider: str = "") -> bool:
    if order.status == "paid":
        return False
    if order.status != "pending":
        raise BillingError("订单状态不可支付")
    order.status = "paid"
    order.paid_at = utcnow()
    order.transaction_id = transaction_id[:80]
    if provider:
        order.provider = provider
    add_credits(
        db,
        order.user_id,
        order.credits,
        kind="recharge",
        note=f"充值 {order.credits} 点",
        ref_type="order",
        ref_id=order.id,
    )
    db.flush()
    return True


def _random_code(length: int) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_redeem_codes(
    db,
    *,
    count: int,
    credits: int,
    amount_cents: int = 0,
    days: int = 365,
    created_by: str = "",
) -> list[str]:
    count = max(1, min(count, 200))
    if credits <= 0:
        raise BillingError("兑换点数必须大于 0")
    batch = utcnow().strftime("B%Y%m%d%H%M%S")
    codes = []
    for _ in range(count):
        code = f"BC-{_random_code(4)}-{_random_code(4)}-{_random_code(4)}"
        while db.scalar(select(RedeemCode).where(RedeemCode.code == code)):
            code = f"BC-{_random_code(4)}-{_random_code(4)}-{_random_code(4)}"
        row = RedeemCode(
            code=code,
            credits=credits,
            amount_cents=amount_cents,
            batch=batch,
            created_by=created_by,
            expires_at=utcnow() + timedelta(days=max(1, days)),
        )
        db.add(row)
        codes.append(code)
    db.flush()
    return codes


def redeem(db, user: User, code: str) -> int:
    normalized = (code or "").strip().upper()
    row = db.scalar(select(RedeemCode).where(RedeemCode.code == normalized))
    if row is None:
        raise BillingError("兑换码不存在")
    if row.status != "unused":
        raise BillingError("兑换码已使用或已停用")
    if row.expires_at is not None and row.expires_at <= utcnow():
        raise BillingError("兑换码已过期")
    row.status = "used"
    row.used_by = user.id
    row.used_at = utcnow()
    return add_credits(
        db,
        user.id,
        row.credits,
        kind="redeem",
        note="兑换码充值",
        ref_type="redeem",
        ref_id=row.id,
    )


def redeem_codes(db, limit: int = 100) -> list[dict]:
    rows = db.scalars(select(RedeemCode).order_by(RedeemCode.created_at.desc()).limit(limit))
    return [
        {
            "id": row.id,
            "code": row.code,
            "credits": row.credits,
            "amount_cents": row.amount_cents,
            "status": row.status,
            "batch": row.batch,
            "used_by": row.used_by,
            "used_at": _iso(row.used_at),
            "expires_at": _iso(row.expires_at),
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


def admin_stats(db) -> dict:
    users = db.scalar(select(func.count()).select_from(User)) or 0
    external = db.scalar(
        select(func.count()).select_from(User).where(User.is_external.is_(True))
    ) or 0
    revenue = db.scalar(
        select(func.coalesce(func.sum(RechargeOrder.amount_cents), 0)).where(
            RechargeOrder.status == "paid"
        )
    ) or 0
    outstanding = db.scalar(select(func.coalesce(func.sum(User.credits), 0))) or 0
    return {
        "users": users,
        "external_users": external,
        "revenue_cents": int(revenue),
        "outstanding_credits": int(outstanding),
    }
