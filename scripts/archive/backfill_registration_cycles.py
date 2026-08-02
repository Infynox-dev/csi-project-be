"""Back-fill unit_registration_cycle rows and link existing payments."""

import asyncio
import sys

from app.common.datetime_utils import current_year_ist, now_ist

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.models import UnitDetails, UnitRegistrationData
from app.admin.models import SiteSettings
from app.common.db import get_async_engine
from app.units.models import UnitRegistrationCycle, UnitRegistrationPayment

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

REGISTRATION_COMPLETED = "Registration Completed"


async def backfill() -> None:
    engine = get_async_engine()
    AsyncSession = async_sessionmaker(engine, expire_on_commit=False)

    async with AsyncSession() as db:
        settings_result = await db.execute(select(SiteSettings).limit(1))
        settings = settings_result.scalar_one_or_none()
        default_year = (
            settings.current_registration_year
            if settings and settings.current_registration_year
            else current_year_ist()
        )

        reg_result = await db.execute(select(UnitRegistrationData))
        registrations = list(reg_result.scalars().all())

        cycles_created = 0
        payments_linked = 0

        for reg in registrations:
            details_result = await db.execute(
                select(UnitDetails).where(UnitDetails.registered_user_id == reg.registered_user_id)
            )
            details = details_result.scalars().first()
            year = (
                details.registration_year
                if details and details.registration_year
                else default_year
            )

            existing = await db.execute(
                select(UnitRegistrationCycle).where(
                    UnitRegistrationCycle.registered_user_id == reg.registered_user_id,
                    UnitRegistrationCycle.registration_year == year,
                )
            )
            cycle = existing.scalar_one_or_none()

            if not cycle:
                cycle = UnitRegistrationCycle(
                    registered_user_id=reg.registered_user_id,
                    registration_year=year,
                    status=reg.status,
                    path_type="fresh",
                    completed_at=now_ist()
                    if reg.status == REGISTRATION_COMPLETED
                    else None,
                )
                db.add(cycle)
                await db.flush()
                cycles_created += 1

            pay_result = await db.execute(
                select(UnitRegistrationPayment).where(
                    UnitRegistrationPayment.registered_user_id == reg.registered_user_id,
                    UnitRegistrationPayment.registration_cycle_id.is_(None),
                )
            )
            for payment in pay_result.scalars().all():
                payment.registration_cycle_id = cycle.id
                payments_linked += 1

        if settings and settings.current_registration_year is None:
            settings.current_registration_year = default_year

        await db.commit()
        print(f"Created {cycles_created} registration cycles")
        print(f"Linked {payments_linked} payment records to cycles")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(backfill())
