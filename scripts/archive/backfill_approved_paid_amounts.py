"""Backfill approved_paid_amount for approved proofs where admin-entered amounts were never stored.

Before the approved_paid_amount column was populated on approval, the system inferred
paid totals from balance_amount alone. That breaks when the registration fee increased
after upload (unit paid the upload-time fee, but balance math used the higher current fee).

Usage:
    python scripts/backfill_approved_paid_amounts.py --dry-run
    python scripts/backfill_approved_paid_amounts.py
    python scripts/backfill_approved_paid_amounts.py --unit-name KUMPLAMPOIKA
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.units.models  # noqa: F401
from app.auth.models import CustomUser, UnitName
from app.common.db import get_async_engine
from app.units import registration_cycle_service as cycle_service
from app.units.models import PaymentProofStatus, UnitRegistrationCycle, UnitRegistrationPayment

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def infer_approved_paid_amounts(
    cycle: UnitRegistrationCycle,
    approved: list[UnitRegistrationPayment],
) -> list[tuple[UnitRegistrationPayment, int | None, int | None]]:
    """Return (payment, old_value, new_value) for proofs needing backfill."""
    fee_owed = cycle.total_fee_at_submit or 0
    sorted_approved = sorted(approved, key=lambda p: p.submitted_at)
    updates: list[tuple[UnitRegistrationPayment, int | None, int | None]] = []
    outstanding_before = fee_owed

    for index, payment in enumerate(sorted_approved):
        if payment.approved_paid_amount is not None:
            if payment.balance_amount is not None:
                outstanding_before = payment.balance_amount
            continue

        old_value = payment.approved_paid_amount
        new_value: int | None

        if (
            len(sorted_approved) == 1
            and payment.total_amount is not None
            and fee_owed > payment.total_amount
        ):
            # Fee rose after upload; proof total_amount is what the unit actually paid.
            new_value = payment.total_amount
        elif payment.balance_amount is None:
            new_value = payment.total_amount or fee_owed or None
        else:
            new_value = max(0, outstanding_before - payment.balance_amount)

        if new_value is None:
            continue

        updates.append((payment, old_value, new_value))
        payment.approved_paid_amount = new_value
        outstanding_before = payment.balance_amount if payment.balance_amount is not None else 0

    return updates


async def backfill(*, unit_name: str | None, dry_run: bool) -> None:
    engine = get_async_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        user_ids: list[int] | None = None
        if unit_name:
            row = (
                await db.execute(
                    select(CustomUser.id)
                    .join(UnitName, UnitName.id == CustomUser.unit_name_id)
                    .where(UnitName.name == unit_name)
                )
            ).scalar_one_or_none()
            if row is None:
                print(f"Unit not found: {unit_name}")
                return
            user_ids = [row]

        stmt = select(UnitRegistrationCycle).where(
            UnitRegistrationCycle.status.in_(
                (cycle_service.DECLARATION_SUBMITTED, cycle_service.REGISTRATION_COMPLETED)
            )
        )
        if user_ids is not None:
            stmt = stmt.where(UnitRegistrationCycle.registered_user_id.in_(user_ids))

        cycles = list((await db.execute(stmt)).scalars().all())
        proof_updates = 0
        balance_updates = 0

        for cycle in cycles:
            payments = await cycle_service._get_cycle_payments(db, cycle.id)
            approved = [p for p in payments if p.status == PaymentProofStatus.APPROVED]
            if not approved:
                continue

            missing = [p for p in approved if p.approved_paid_amount is None]
            if not missing:
                continue

            updates = infer_approved_paid_amounts(cycle, approved)
            if not updates:
                continue

            latest = max(approved, key=lambda p: p.submitted_at)
            old_balance = latest.balance_amount
            cycle_service.recalculate_latest_approved_balance(cycle, approved)
            new_balance = latest.balance_amount

            unit_label = f"cycle={cycle.id} year={cycle.registration_year}"
            if unit_name:
                unit_label = f"{unit_name} {unit_label}"

            for payment, old_value, new_value in updates:
                print(
                    f"{unit_label} proof={payment.id}: "
                    f"approved_paid_amount {old_value} -> {new_value}"
                )
                proof_updates += 1

            if old_balance != new_balance:
                summary = cycle_service.build_payment_summary(cycle, approved)
                print(
                    f"{unit_label}: balance {old_balance} -> {new_balance}, "
                    f"total_paid={summary['total_paid']}, balance_due={summary['balance_due']}"
                )
                balance_updates += 1

        if dry_run:
            await db.rollback()
        else:
            await db.commit()

        print(
            f"\n{'Would update' if dry_run else 'Updated'} "
            f"{proof_updates} proof(s), {balance_updates} balance(s) dry_run={dry_run}"
        )

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit-name", default=None, help="Unit name (e.g. KUMPLAMPOIKA)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(backfill(unit_name=args.unit_name, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
