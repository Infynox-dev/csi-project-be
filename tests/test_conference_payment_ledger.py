"""Conference district ledger helpers — statuses, partials, stale pending."""

from datetime import datetime
from types import SimpleNamespace

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.conference.models import PaymentStatusEnum  # noqa: E402
from app.conference.service import (  # noqa: E402
    build_conference_payment_summary,
    has_blocking_pending,
    overall_conference_status,
    overall_status_legacy,
)


def _proof(
    *,
    status: PaymentStatusEnum,
    total: int | None = 900,
    approved: int | None = None,
    balance: int | None = None,
    submitted_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        total_amount=total,
        approved_paid_amount=approved,
        balance_amount=balance,
        submitted_at=submitted_at or datetime(2026, 9, 1),
        date=submitted_at or datetime(2026, 9, 1),
    )


def test_not_submitted_when_empty():
    summary = build_conference_payment_summary(
        fee_owed=900, official_count=1, member_count=2, approved=[]
    )
    assert summary["fee_owed"] == 900
    assert summary["delegate_count"] == 3
    assert summary["is_fully_paid"] is False
    assert overall_conference_status([], summary) == "not_submitted"
    assert overall_status_legacy("not_submitted") is None


def test_partial_then_paid_with_admin_amounts():
    first = _proof(
        status=PaymentStatusEnum.PAID,
        total=900,
        approved=400,
        submitted_at=datetime(2026, 9, 1),
    )
    second = _proof(
        status=PaymentStatusEnum.PAID,
        total=900,
        approved=500,
        submitted_at=datetime(2026, 9, 2),
    )
    summary = build_conference_payment_summary(
        fee_owed=900, official_count=1, member_count=2, approved=[first]
    )
    assert summary["total_paid"] == 400
    assert summary["balance_due"] == 500
    assert overall_conference_status([first], summary) == "partial"

    summary = build_conference_payment_summary(
        fee_owed=900, official_count=1, member_count=2, approved=[first, second]
    )
    assert summary["total_paid"] == 900
    assert summary["balance_due"] == 0
    assert summary["is_fully_paid"] is True
    assert overall_conference_status([first, second], summary) == "paid"
    assert overall_status_legacy("paid") == "PAID"


def test_roster_drop_creates_credit_not_new_balance():
    paid = _proof(status=PaymentStatusEnum.PAID, total=900, approved=900)
    summary = build_conference_payment_summary(
        fee_owed=600, official_count=1, member_count=1, approved=[paid]
    )
    assert summary["total_paid"] == 900
    assert summary["balance_due"] == 0
    assert summary["payment_credit"] == 300
    assert summary["is_fully_paid"] is True


def test_blocking_pending_and_stale_higher_total():
    pending = _proof(status=PaymentStatusEnum.PROOF_UPLOADED, total=900)
    assert has_blocking_pending([pending], fee_owed=900) is True
    assert has_blocking_pending([pending], fee_owed=600) is False

    declined = _proof(status=PaymentStatusEnum.DECLINED, total=900)
    summary = build_conference_payment_summary(
        fee_owed=900, official_count=1, member_count=2, approved=[]
    )
    assert overall_conference_status([declined], summary) == "declined"
    assert has_blocking_pending([declined], fee_owed=900) is False


if __name__ == "__main__":
    test_not_submitted_when_empty()
    test_partial_then_paid_with_admin_amounts()
    test_roster_drop_creates_credit_not_new_balance()
    test_blocking_pending_and_stale_higher_total()
    print("ok")
