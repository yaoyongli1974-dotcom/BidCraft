"""v0.34.0 P0：支付/兑换的原子状态转换与消费者保护。

所有竞争用例都使用真实线程 + 独立 SQLAlchemy Session，不 mock 条件 UPDATE，
也不使用 Python 锁替代数据库 fencing；SQLite 写锁短暂冲突时由用例重试整笔事务。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any, TypeVar

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from bidcraft import billing
from bidcraft.config import Settings, validate_billing_config
from bidcraft.db import get_engine, session_scope
from bidcraft.models import CreditTransaction, RechargeOrder, RedeemCode, User, utcnow

T = TypeVar("T")

pytestmark = pytest.mark.usefixtures("db")

TX = "V0340-TX-1"


def _user(db, username: str, *, credits: int = 0) -> User:
    row = User(
        username=username,
        password_hash="x",
        role="manager",
        is_external=True,
        credits=0,
    )
    db.add(row)
    db.flush()
    if credits:
        billing.add_credits(db, row.id, credits, kind="admin", note="seed")
    return row


def _order(
    db,
    user_id: str,
    *,
    provider: str = "wechat",
    credits: int = 10,
    amount_cents: int = 990,
) -> RechargeOrder:
    order = billing.create_order(
        db,
        user_id,
        {"id": "atomic-pack", "credits": credits, "amount_cents": amount_cents},
        provider=provider,
    )
    return order


def _vip_order(db, user_id: str, *, provider: str = "wechat") -> RechargeOrder:
    return billing.create_vip_order(
        db,
        user_id,
        {"id": "vip-month", "days": 30, "amount_cents": 3000},
        provider=provider,
    )


def _balance(user_id: str) -> int:
    with session_scope() as db:
        return int(db.get(User, user_id).credits or 0)


def _race(first: Callable[[], T], second: Callable[[], T]) -> tuple[list[Any], list[BaseException]]:
    """两个线程、两个 Session、Barrier 同步起跑；锁冲突时整笔事务重试。"""

    barrier = threading.Barrier(2)
    results: list[Any] = [None, None]
    errors: list[BaseException] = []

    def run(index: int, fn: Callable[[], T]) -> None:
        barrier.wait(timeout=10)
        for _ in range(20):
            try:
                results[index] = fn()
                return
            except BaseException as exc:  # noqa: BLE001 - 竞争用例需捕获线程内全部异常
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    time.sleep(0.01)
                    continue
                errors.append(exc)
                return
        errors.append(AssertionError("SQLite 写锁重试 20 次仍失败"))

    threads = [
        threading.Thread(target=run, args=(0, first)),
        threading.Thread(target=run, args=(1, second)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results, errors


def test_concurrent_mark_paid_credits_exactly_once() -> None:
    with session_scope() as session:
        user = _user(session, "atomic-payer", credits=1)
        order = _order(session, user.id, credits=10)
        order_id, user_id = order.id, user.id

    def mark() -> bool:
        with session_scope() as db:
            row = db.get(RechargeOrder, order_id)
            return billing.mark_paid(db, row, transaction_id=TX, provider="wechat")

    results, errors = _race(mark, mark)
    assert not errors, errors
    assert sorted(results) == [False, True]
    assert _balance(user_id) == 11
    with session_scope() as db:
        rows = db.scalars(
            select(CreditTransaction).where(
                CreditTransaction.ref_type == "order", CreditTransaction.ref_id == order_id
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].kind == "recharge" and rows[0].amount == 10
        paid = db.get(RechargeOrder, order_id)
        assert paid.status == "paid"
        assert paid.transaction_id == TX
        assert paid.paid_at is not None
        assert paid.provider == "wechat"


def test_concurrent_vip_mark_paid_extends_once() -> None:
    with session_scope() as session:
        user = _user(session, "atomic-vip-payer")
        order = _vip_order(session, user.id)
        order_id, user_id = order.id, user.id

    started = utcnow()

    def mark() -> bool:
        with session_scope() as db:
            row = db.get(RechargeOrder, order_id)
            return billing.mark_paid(db, row, transaction_id=TX, provider="wechat")

    results, errors = _race(mark, mark)
    assert not errors, errors
    assert sorted(results) == [False, True]
    assert _balance(user_id) == 0  # VIP 订单不加点数
    with session_scope() as db:
        rows = db.scalars(
            select(CreditTransaction).where(
                CreditTransaction.ref_type == "order", CreditTransaction.ref_id == order_id
            )
        ).all()
        assert len(rows) == 1 and rows[0].kind == "vip"
        user = db.get(User, user_id)
        assert user is not None and user.vip_expires_at is not None
        delta = (user.vip_expires_at - started).total_seconds()
        assert 29 * 86400 < delta < 31 * 86400


def test_paid_order_with_different_transaction_id_is_rejected() -> None:
    with session_scope() as session:
        user = _user(session, "atomic-tx-user")
        order = _order(session, user.id)
        order_id, user_id = order.id, user.id

    with session_scope() as db:
        row = db.get(RechargeOrder, order_id)
        assert billing.mark_paid(db, row, transaction_id=TX, provider="wechat") is True
    with session_scope() as db:
        row = db.get(RechargeOrder, order_id)
        assert billing.mark_paid(db, row, transaction_id=TX, provider="wechat") is False
        with pytest.raises(billing.BillingError, match="交易号"):
            billing.mark_paid(db, row, transaction_id="V0340-TX-2", provider="wechat")
    assert _balance(user_id) == 10
    with session_scope() as db:
        count = db.scalar(
            select(func.count())
            .select_from(CreditTransaction)
            .where(CreditTransaction.kind == "recharge")
        )
        assert count == 1


def test_non_pending_order_cannot_be_marked_paid() -> None:
    with session_scope() as session:
        user = _user(session, "atomic-closed-user")
        order = _order(session, user.id)
        order.status = "closed"
        session.flush()
        order_id, user_id = order.id, user.id
    with session_scope() as db:
        row = db.get(RechargeOrder, order_id)
        with pytest.raises(billing.BillingError, match="状态"):
            billing.mark_paid(db, row, transaction_id=TX, provider="wechat")
    assert _balance(user_id) == 0


@pytest.mark.parametrize("bad_provider", ["mock", "admin", "wechatpay"])
def test_mark_paid_validates_provider_against_order(bad_provider: str) -> None:
    with session_scope() as session:
        user = _user(session, f"atomic-provider-{bad_provider}")
        order = _order(session, user.id, provider="wechat")
        order_id, user_id = order.id, user.id
    with session_scope() as db:
        row = db.get(RechargeOrder, order_id)
        with pytest.raises(billing.BillingError, match="渠道|provider"):
            billing.mark_paid(db, row, transaction_id=TX, provider=bad_provider)
        assert row.status == "pending" and row.provider == "wechat"
    assert _balance(user_id) == 0


def test_mark_paid_accepts_matching_mock_provider() -> None:
    with session_scope() as session:
        user = _user(session, "atomic-mock-user")
        order = _order(session, user.id, provider="mock")
        order_id, user_id = order.id, user.id
    with session_scope() as db:
        row = db.get(RechargeOrder, order_id)
        assert billing.mark_paid(db, row, transaction_id="MOCK-1", provider="mock") is True
        assert row.provider == "mock"
    assert _balance(user_id) == 10


def test_concurrent_redeem_credits_once() -> None:
    with session_scope() as session:
        user = _user(session, "atomic-redeemer", credits=2)
        codes = billing.generate_redeem_codes(session, count=1, credits=5, created_by="test")
        code, user_id = codes[0], user.id

    def redeem() -> int | str:
        with session_scope() as db:
            row = db.get(User, user_id)
            try:
                return billing.redeem(db, row, code)
            except billing.BillingError:
                return "rejected"

    results, errors = _race(redeem, redeem)
    assert not errors, errors
    assert results.count(7) == 1  # redeem 返回加钱后的余额：2 + 5
    assert results.count("rejected") == 1
    assert _balance(user_id) == 7
    with session_scope() as db:
        rows = db.scalars(
            select(CreditTransaction).where(CreditTransaction.ref_type == "redeem")
        ).all()
        assert len(rows) == 1 and rows[0].amount == 5
        used = db.scalar(select(RedeemCode).where(RedeemCode.code == code))
        assert used.status == "used" and used.used_by == user_id and used.used_at is not None


def test_expired_and_disabled_codes_are_not_credited() -> None:
    with session_scope() as session:
        user = _user(session, "atomic-code-user", credits=1)
        codes = billing.generate_redeem_codes(session, count=2, credits=7, created_by="test")
        expired = session.scalar(select(RedeemCode).where(RedeemCode.code == codes[0]))
        disabled = session.scalar(select(RedeemCode).where(RedeemCode.code == codes[1]))
        assert expired is not None and disabled is not None
        expired.expires_at = utcnow() - timedelta(seconds=1)
        disabled.status = "disabled"
        user_id = user.id

    with session_scope() as db:
        row = db.get(User, user_id)
        with pytest.raises(billing.BillingError, match="过期"):
            billing.redeem(db, row, codes[0])
    with session_scope() as db:
        row = db.get(User, user_id)
        with pytest.raises(billing.BillingError, match="已使用|停用"):
            billing.redeem(db, row, codes[1])
    assert _balance(user_id) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("signup_credits", True),
        ("signup_credits", float("nan")),
        ("signup_credits", float("inf")),
        ("signup_credits", -1),
        ("chapter_cost", True),
        ("chapter_cost", float("nan")),
        ("chapter_cost", float("-inf")),
        ("chapter_cost", -1),
        ("rate_limit_window_seconds", True),
        ("rate_limit_window_seconds", float("nan")),
        ("rate_limit_login_ip", -1),
        ("rate_limit_login_global", True),
        ("rate_limit_sensitive_ip", float("inf")),
        ("rate_limit_sensitive_global", -1),
    ],
)
def test_settings_reject_invalid_billing_numbers(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        Settings(**{field: value})


def test_validate_billing_config_is_pure_and_detects_duplicate_ids() -> None:
    assert validate_billing_config(Settings(), raise_on_error=False) == []
    errors = validate_billing_config(
        {
            "signup_credits": 5,
            "vouchers": [{"id": "dup"}, {"id": "dup"}, {"id": "ok"}],
        },
        raise_on_error=False,
    )
    assert any("重复" in message or "duplicate" in message.lower() for message in errors)
    assert validate_billing_config(
        {"vouchers": [{"id": "a"}, {"id": "b"}]}, raise_on_error=False
    ) == []


def test_save_billing_settings_rejects_duplicate_package_ids() -> None:
    duplicate = {"id": "dup", "name": "重复包", "credits": 1, "amount_cents": 100}
    with session_scope() as session, pytest.raises(billing.BillingError, match="重复"):
        billing.save_billing_settings(
            session,
            {
                "packages": [duplicate, {**duplicate, "name": "另一个包"}],
            },
        )


def test_admin_stats_counts_only_wechat_orders_net_of_order_refunds() -> None:
    with session_scope() as session:
        user = _user(session, "atomic-revenue-user")
        mock_order = _order(session, user.id, provider="mock", amount_cents=990)
        billing.mark_paid(session, mock_order, transaction_id="MOCK-9", provider="mock")
        wechat_order = _order(session, user.id, provider="wechat", amount_cents=3000)
        billing.mark_paid(session, wechat_order, transaction_id="WX-9", provider="wechat")
        # 3 点退款按该订单 3000 分 / 10 点的单价折回 900 分。
        billing.add_credits(
            session,
            user.id,
            -3,
            kind="refund",
            note="订单退款",
            ref_type="order",
            ref_id=wechat_order.id,
        )
    with session_scope() as db:
        stats = billing.admin_stats(db)
    assert stats["revenue_cents"] == 2100


def test_transaction_id_partial_unique_index_rejects_duplicates() -> None:
    with session_scope() as session:
        user = _user(session, "atomic-index-user")
        user_id = user.id
    engine = get_engine()
    insert_order = text(
        "INSERT INTO recharge_orders (id, user_id, out_trade_no, amount_cents, credits, "
        "status, provider, package_id, code_url, transaction_id, meta, created_at, updated_at) "
        "VALUES (:id, :user_id, :trade_no, 100, 1, :status, 'wechat', '', '', :txn, '{}', "
        "'2020-01-01 00:00:00', '2020-01-01 00:00:00')"
    )
    with engine.begin() as connection:
        connection.execute(
            insert_order,
            {
                "id": "idx-1",
                "user_id": user_id,
                "trade_no": "IDX-1",
                "status": "paid",
                "txn": "IDX-DUP",
            },
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            insert_order,
            {
                "id": "idx-2",
                "user_id": user_id,
                "trade_no": "IDX-2",
                "status": "paid",
                "txn": "IDX-DUP",
            },
        )
    with engine.begin() as connection:
        for number in ("IDX-EMPTY-1", "IDX-EMPTY-2"):
            connection.execute(
                insert_order,
                {
                    "id": f"idx-{number}",
                    "user_id": user_id,
                    "trade_no": number,
                    "status": "pending",
                    "txn": "",
                },
            )
