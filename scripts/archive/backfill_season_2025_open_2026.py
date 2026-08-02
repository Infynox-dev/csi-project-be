"""Backfill completed 2025 registration cycles and set active season to 2026.

Also migrates any in-progress cycles on a future year (e.g. 2027) down to the
open year so units mid-registration continue under the correct season.

Usage:
    python scripts/backfill_season_2025_open_2026.py
    python scripts/backfill_season_2025_open_2026.py --dry-run
"""

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.auth.models  # noqa: F401
import app.units.models  # noqa: F401
from app.common.cache import clear_cache
from app.common.db import get_async_engine
from app.units.models import UnitRegistrationCycle, UnitRegistrationPayment

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

CLOSE_YEAR = 2025
OPEN_YEAR = 2026
SITE_SETTINGS_CACHE_KEY = "site_settings_v1"


async def migrate_future_cycles_to_open_year(db, open_year: int, *, dry_run: bool) -> dict[str, int]:
    """Move in-progress cycles from years > open_year onto open_year as renewals."""
    stats = {"migrated": 0, "merged": 0, "payments_relinked": 0}

    future_cycles = (
        await db.execute(
            select(UnitRegistrationCycle)
            .where(UnitRegistrationCycle.registration_year > open_year)
            .order_by(UnitRegistrationCycle.registered_user_id, UnitRegistrationCycle.registration_year)
        )
    ).scalars().all()

    for cycle in future_cycles:
        existing_open = (
            await db.execute(
                select(UnitRegistrationCycle).where(
                    UnitRegistrationCycle.registered_user_id == cycle.registered_user_id,
                    UnitRegistrationCycle.registration_year == open_year,
                )
            )
        ).scalar_one_or_none()

        if existing_open:
            payments = (
                await db.execute(
                    select(UnitRegistrationPayment).where(
                        UnitRegistrationPayment.registration_cycle_id == cycle.id
                    )
                )
            ).scalars().all()
            for payment in payments:
                payment.registration_cycle_id = existing_open.id
                stats["payments_relinked"] += 1
            await db.delete(cycle)
            stats["merged"] += 1
            print(
                f"  merged future cycle id={cycle.id} year={cycle.registration_year} "
                f"into open cycle id={existing_open.id} for user={cycle.registered_user_id}"
            )
        else:
            old_year = cycle.registration_year
            cycle.registration_year = open_year
            cycle.path_type = "renewal"
            stats["migrated"] += 1
            print(
                f"  migrated cycle id={cycle.id} user={cycle.registered_user_id} "
                f"{old_year} -> {open_year} (status={cycle.status}, path=renewal)"
            )

    if dry_run:
        await db.rollback()
    return stats


async def run(*, dry_run: bool) -> None:
    from app.admin.models import SiteSettings
    from scripts.advance_registration_season import reconcile_closed_season

    engine = get_async_engine()
    AsyncSession = async_sessionmaker(engine, expire_on_commit=False)

    async with AsyncSession() as db:
        print(f"=== Step 1: Reconcile closed season {CLOSE_YEAR - 1}-{CLOSE_YEAR} ===")
        stats = await reconcile_closed_season(db, CLOSE_YEAR)
        for key, value in stats.items():
            print(f"  {key}: {value}")

        print(f"\n=== Step 2: Migrate future-year in-progress cycles to {OPEN_YEAR} ===")
        migrate_stats = await migrate_future_cycles_to_open_year(db, OPEN_YEAR, dry_run=False)
        for key, value in migrate_stats.items():
            print(f"  {key}: {value}")

        settings = (await db.execute(select(SiteSettings).limit(1))).scalar_one_or_none()
        if settings:
            settings.current_registration_year = OPEN_YEAR
            print(f"\n=== Step 3: Set active season to {OPEN_YEAR - 1}-{OPEN_YEAR} ===")

        if dry_run:
            await db.rollback()
            print("\n(dry-run: rolled back — no changes saved)")
        else:
            await db.commit()
            clear_cache(SITE_SETTINGS_CACHE_KEY)
            clear_cache("admin_dashboard")
            clear_cache("district_wise_data")
            print("\nCommitted all changes.")

    await engine.dispose()
    print("\n=== Done ===")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without committing")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
