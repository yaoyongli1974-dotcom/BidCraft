"""第三轮审计：非整除 VIP 扣费的章节退款不能留下零头。"""

from __future__ import annotations


def _marker(credits: int, chapters: int):
    from bidcraft.models import PendingCharge

    return PendingCharge(
        user_id="u1", project_id="p1", credits=credits, chapters=chapters, status="pending"
    )


def test_refund_formula_rounds_up_charge_not_down():
    from bidcraft import jobqueue

    # VIP 折扣：5 点生成 3 章，每章实际花费 ceil(5/3)=2 点。
    marker = _marker(5, 3)
    assert jobqueue._refund_for(marker, 0) == 5
    assert jobqueue._refund_for(marker, 1) == 3  # 旧公式 (5//3)*2 = 2，少退 1 点
    assert jobqueue._refund_for(marker, 2) == 1  # 花费 ceil(10/3)=4
    assert jobqueue._refund_for(marker, 3) == 0

    # 点数比章节数还少：旧公式退款恒为 0，零头全被吞掉。
    tiny = _marker(2, 5)
    assert jobqueue._refund_for(tiny, 1) == 1
    assert jobqueue._refund_for(tiny, 2) == 1
    assert jobqueue._refund_for(tiny, 5) == 0

    # 整除场景保持原语义；越界的 written 被夹到 [0, chapters]。
    divisible = _marker(9, 3)
    assert [jobqueue._refund_for(divisible, n) for n in range(4)] == [9, 6, 3, 0]
    assert jobqueue._refund_for(divisible, -2) == 9
    assert jobqueue._refund_for(divisible, 99) == 0


def test_settle_refunds_whole_remainder_of_discounted_charge(db):
    from bidcraft import billing, jobqueue
    from bidcraft.db import session_scope
    from bidcraft.models import PendingCharge, User

    with session_scope() as session:
        user = User(
            username="fix3-refund", password_hash="x", role="manager",
            is_external=True, credits=0,
        )
        session.add(user)
        session.flush()
        billing.add_credits(session, user.id, 10, kind="admin", note="seed")
        # 模拟已发生的 VIP 扣费：5 点 / 3 章。
        billing.add_credits(session, user.id, -5, kind="consume", note="charge")
        marker_id = jobqueue.begin_pending_charge(session, user, "p1", 5, 3)
        session.flush()
        user_id = user.id

    assert marker_id
    with session_scope() as session:
        user = session.get(User, user_id)
        refund = jobqueue.settle_pending_charge(session, marker_id, user, "p1", 3, 1)
        session.flush()
        assert refund == 3
        assert user.credits == 8  # 原余额 10 - 扣费 5 + 退款 3
        marker = session.get(PendingCharge, marker_id)
        assert marker.status == "refunded" and marker.refunded_credits == 3
