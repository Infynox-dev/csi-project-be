"""Back-fill approved registration payments for a season (no proof file).

For closing a season and advancing to the next year (cycles + payments + site
settings), use advance_registration_season.py instead — it is idempotent and
covers both unit_registration_cycle and unit_registration_payment.

Usage:
    python scripts/backfill_season_payments.py
    python scripts/backfill_season_payments.py --year 2026
"""

import argparse
import asyncio
import sys
from app.common.datetime_utils import current_year_ist, now_ist

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.admin.models import SiteSettings
from app.auth.models import CustomUser, UnitMembers, UnitRegistrationData, UserType  # noqa: F401
import app.units.models  # noqa: F401
from app.common.db import get_async_engine
from app.units import registration_cycle_service as cycle_service
from app.units.models import PaymentProofStatus, UnitRegistrationCycle, UnitRegistrationPayment

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def get_fees(db) -> tuple[int, int]:
    settings = (await db.execute(select(SiteSettings).limit(1))).scalar_one_or_none()
    unit_fee = settings.unit_registration_fee if settings and settings.unit_registration_fee is not None else 100
    member_fee = settings.unit_member_fee if settings and settings.unit_member_fee is not None else 10
    return unit_fee, member_fee


async def backfill(registration_year: int | None = None) -> None:
    engine = get_async_engine()
    AsyncSession = async_sessionmaker(engine, expire_on_commit=False)

    async with AsyncSession() as db:
        settings = (await db.execute(select(SiteSettings).limit(1))).scalar_one_or_none()
        target_year = registration_year or (
            settings.current_registration_year if settings and settings.current_registration_year else current_year_ist()
        )
        unit_fee, member_fee = await get_fees(db)
        now = now_ist()

        stmt = (
            select(UnitRegistrationData, CustomUser)
            .join(CustomUser, CustomUser.id == UnitRegistrationData.registered_user_id)
            .where(
                CustomUser.user_type == UserType.UNIT,
                CustomUser.is_active.is_(True),
            )
        )
        rows = (await db.execute(stmt)).all()

        cycles_created = 0
        payments_created = 0
        payments_updated = 0
        skipped = 0

        for reg, user in rows:
            user_id = user.id
            cycle = await cycle_service.get_cycle(db, user_id, target_year)
            if not cycle:
                path_type = await cycle_service.determine_path_type(db, user_id, target_year)
                cycle = await cycle_service.create_cycle(db, user_id, target_year, path_type=path_type)
                cycles_created += 1

            approved_result = await db.execute(
                select(UnitRegistrationPayment).where(
                    UnitRegistrationPayment.registration_cycle_id == cycle.id,
                    UnitRegistrationPayment.status == PaymentProofStatus.APPROVED,
                )
            )
            if approved_result.scalar_one_or_none():
                skipped += 1
                continue

            member_count = (
                await db.execute(
                    select(UnitMembers).where(UnitMembers.registered_user_id == user_id)
                )
            ).scalars().all()
            total_amount = unit_fee + (len(member_count) * member_fee)

            existing_result = await db.execute(
                select(UnitRegistrationPayment)
                .where(UnitRegistrationPayment.registration_cycle_id == cycle.id)
                .order_by(UnitRegistrationPayment.submitted_at.desc())
            )
            existing = existing_result.scalars().first()

            if existing:
                existing.file_path = None
                existing.total_amount = total_amount
                existing.status = PaymentProofStatus.APPROVED
                existing.rejection_note = None
                existing.reviewed_at = now
                payments_updated += 1
            else:
                db.add(
                    UnitRegistrationPayment(
                        registered_user_id=user_id,
                        registration_cycle_id=cycle.id,
                        file_path=None,
                        total_amount=total_amount,
                        status=PaymentProofStatus.APPROVED,
                        submitted_at=now,
                        reviewed_at=now,
                    )
                )
                payments_created += 1

        await db.commit()
        print(f"Season ending year: {target_year} ({target_year - 1}-{target_year})")
        print(f"Active registered units processed: {len(rows)}")
        print(f"Cycles created: {cycles_created}")
        print(f"Approved payments created: {payments_created}")
        print(f"Existing payments updated to approved: {payments_updated}")
        print(f"Skipped (already approved): {skipped}")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Back-fill approved season payments without proof files.")
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Registration ending year (default: site_settings.current_registration_year)",
    )
    args = parser.parse_args()
    asyncio.run(backfill(args.year))


if __name__ == "__main__":
    main()
