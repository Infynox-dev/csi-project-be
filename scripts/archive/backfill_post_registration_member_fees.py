"""Back-fill fee adjustments for units that gained members after registration payment.

Finds current-season cycles where the live member count exceeds the snapshot
taken at declaration, then applies the same fee adjustment used when approving
a post-registration member add.

Usage:
    python scripts/backfill_post_registration_member_fees.py --dry-run
    python scripts/backfill_post_registration_member_fees.py
    python scripts/backfill_post_registration_member_fees.py --year 2027
"""

import argparse
import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.units.models  # noqa: F401
from app.admin.models import SiteSettings
from app.auth.models import UnitMembers
from app.common.db import get_async_engine
from app.units import registration_cycle_service as cycle_service
from app.units.models import PaymentProofStatus, UnitRegistrationCycle, UnitRegistrationPayment

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def backfill(*, registration_year: int | None, dry_run: bool) -> None:
    engine = get_async_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        settings = (await db.execute(select(SiteSettings).limit(1))).scalar_one_or_none()
        target_year = registration_year or await cycle_service.get_current_registration_year(db)

        cycles_result = await db.execute(
            select(UnitRegistrationCycle).where(
                UnitRegistrationCycle.registration_year == target_year,
                UnitRegistrationCycle.status.in_(
                    (cycle_service.DECLARATION_SUBMITTED, cycle_service.REGISTRATION_COMPLETED)
                ),
            )
        )
        cycles = list(cycles_result.scalars().all())

        adjusted = 0
        skipped = 0

        for cycle in cycles:
            snapshot_count = cycle.member_count_at_submit
            if snapshot_count is None:
                skipped += 1
                continue

            live_count_result = await db.execute(
                select(func.count())
                .select_from(UnitMembers)
                .where(UnitMembers.registered_user_id == cycle.registered_user_id)
            )
            live_count = live_count_result.scalar() or 0
            delta_members = live_count - snapshot_count
            if delta_members <= 0:
                skipped += 1
                continue

            approved_result = await db.execute(
                select(UnitRegistrationPayment)
                .where(
                    UnitRegistrationPayment.registration_cycle_id == cycle.id,
                    UnitRegistrationPayment.status == PaymentProofStatus.APPROVED,
                )
                .order_by(UnitRegistrationPayment.submitted_at.asc())
            )
            latest_approved = approved_result.scalars().all()
            latest_approved = latest_approved[-1] if latest_approved else None

            if latest_approved is None:
                print(
                    f"user={cycle.registered_user_id} cycle={cycle.id}: "
                    f"+{delta_members} member(s) but no approved payment — updating cycle snapshot only"
                )
            else:
                print(
                    f"user={cycle.registered_user_id} cycle={cycle.id}: "
                    f"snapshot={snapshot_count} live={live_count} "
                    f"payment_total={latest_approved.total_amount} "
                    f"balance={latest_approved.balance_amount}"
                )

            if dry_run:
                adjusted += 1
                continue

            applied = await cycle_service.adjust_fee_for_member_delta(
                db,
                registered_user_id=cycle.registered_user_id,
                delta_members=delta_members,
            )
            if applied:
                adjusted += 1
            else:
                skipped += 1

        if dry_run:
            await db.rollback()
        else:
            await db.commit()

        print(f"\nYear {target_year}: adjusted={adjusted} skipped={skipped} dry_run={dry_run}")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(backfill(registration_year=args.year, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
