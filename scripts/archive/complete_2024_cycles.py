"""Mark incomplete 2024 registration cycles as completed on production.

Uses the same completion semantics as advance_registration_season.py:
  - status -> Registration Completed
  - member_count_at_submit / total_fee_at_submit from current members
  - completed_at set if missing
  - approved payment created/updated and linked to cycle
"""

import argparse
import asyncio
import sys
from app.common.datetime_utils import now_ist

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.auth.models  # noqa: F401
import app.units.models  # noqa: F401
from app.admin.models import SiteSettings
from app.auth.models import CustomUser, UnitDetails, UnitMembers, UnitName
from app.common.db import get_async_engine
from app.units import registration_cycle_service as cycle_service
from app.units.models import PaymentProofStatus, UnitRegistrationCycle, UnitRegistrationPayment

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

REGISTRATION_COMPLETED = "Registration Completed"
TARGET_YEAR = 2024


async def get_fees(db) -> tuple[int, int]:
    settings = (await db.execute(select(SiteSettings).limit(1))).scalar_one_or_none()
    unit_fee = settings.unit_registration_fee if settings and settings.unit_registration_fee is not None else 100
    member_fee = settings.unit_member_fee if settings and settings.unit_member_fee is not None else 10
    return unit_fee, member_fee


async def fetch_incomplete_cycles(db):
    return (
        await db.execute(
            select(UnitRegistrationCycle, CustomUser, UnitName)
            .join(CustomUser, CustomUser.id == UnitRegistrationCycle.registered_user_id)
            .outerjoin(UnitName, UnitName.id == CustomUser.unit_name_id)
            .where(
                UnitRegistrationCycle.registration_year == TARGET_YEAR,
                UnitRegistrationCycle.status != REGISTRATION_COMPLETED,
            )
            .order_by(UnitName.name.nulls_last(), CustomUser.username)
        )
    ).all()


async def complete_cycle_row(db, cycle: UnitRegistrationCycle, *, dry_run: bool) -> dict:
    unit_fee, member_fee = await get_fees(db)
    now = now_ist()
    user_id = cycle.registered_user_id

    members = (
        await db.execute(select(UnitMembers).where(UnitMembers.registered_user_id == user_id))
    ).scalars().all()
    member_count = len(members)
    total_amount = unit_fee + (member_count * member_fee)

    payment_result = await db.execute(
        select(UnitRegistrationPayment)
        .where(UnitRegistrationPayment.registration_cycle_id == cycle.id)
        .order_by(UnitRegistrationPayment.submitted_at.desc())
    )
    payment = payment_result.scalars().first()
    payment_action = "unchanged"

    if payment is None:
        payment_action = "created"
        if not dry_run:
            payment = UnitRegistrationPayment(
                registered_user_id=user_id,
                registration_cycle_id=cycle.id,
                file_path=None,
                total_amount=total_amount,
                status=PaymentProofStatus.APPROVED,
                submitted_at=now,
                reviewed_at=now,
            )
            db.add(payment)
    else:
        changed = False
        if payment.status != PaymentProofStatus.APPROVED:
            payment.status = PaymentProofStatus.APPROVED
            changed = True
        if payment.file_path is not None:
            payment.file_path = None
            changed = True
        if payment.total_amount != total_amount:
            payment.total_amount = total_amount
            changed = True
        if payment.rejection_note:
            payment.rejection_note = None
            changed = True
        if not payment.reviewed_at:
            payment.reviewed_at = now
            changed = True
        if payment.registration_cycle_id != cycle.id:
            payment.registration_cycle_id = cycle.id
            changed = True
        payment_action = "updated" if changed else "unchanged"

    old_status = cycle.status
    if not dry_run:
        cycle.status = REGISTRATION_COMPLETED
        cycle.completed_at = cycle.completed_at or (payment.reviewed_at if payment else now)
        cycle.member_count_at_submit = member_count
        cycle.total_fee_at_submit = total_amount

    return {
        "cycle_id": cycle.id,
        "user_id": user_id,
        "old_status": old_status,
        "member_count": member_count,
        "total_amount": total_amount,
        "payment_action": payment_action,
    }


async def main(dry_run: bool) -> None:
    engine = get_async_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        rows = await fetch_incomplete_cycles(db)
        print(f"Found {len(rows)} incomplete cycles for {TARGET_YEAR}")
        if not rows:
            return

        for cycle, user, unit_name in rows:
            label = unit_name.name if unit_name else user.username
            result = await complete_cycle_row(db, cycle, dry_run=dry_run)
            print(
                f"  cycle_id={result['cycle_id']} unit={label!r} ({user.username}) "
                f"status={result['old_status']!r} -> {REGISTRATION_COMPLETED!r} "
                f"members={result['member_count']} fee={result['total_amount']} "
                f"payment={result['payment_action']}"
            )

        if dry_run:
            print("\nDRY RUN — no changes committed.")
            await db.rollback()
        else:
            await db.commit()
            remaining = (
                await db.execute(
                    select(UnitRegistrationCycle.id).where(
                        UnitRegistrationCycle.registration_year == TARGET_YEAR,
                        UnitRegistrationCycle.status != REGISTRATION_COMPLETED,
                    )
                )
            ).scalars().all()
            print(f"\nCommitted. Remaining incomplete {TARGET_YEAR} cycles: {len(remaining)}")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Complete incomplete {TARGET_YEAR} registration cycles")
    parser.add_argument("--apply", action="store_true", help="Persist changes (default is dry-run)")
    args = parser.parse_args()
    asyncio.run(main(dry_run=not args.apply))
