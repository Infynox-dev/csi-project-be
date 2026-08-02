"""Fix mid-wizard units for active 2027 season and remove test BOYCE ESTATE unit."""

import argparse
import asyncio
import sys
from app.common.datetime_utils import now_ist

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.auth.models  # noqa: F401
import app.units.models  # noqa: F401
from app.auth.models import (
    CustomUser,
    RefreshToken,
    UnitCouncilor,
    UnitDetails,
    UnitMembers,
    UnitOfficials,
    UnitRegistrationData,
    UnitName,
)
from app.common.cache import clear_cache
from app.common.db import get_async_engine
from app.units.models import UnitRegistrationCycle, UnitRegistrationPayment

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

ACTIVE_YEAR = 2027
SITE_SETTINGS_CACHE_KEY = "site_settings_v1"

MID_WIZARD_UNITS = {
    14: {  # KANAM
        "username": "MKDYM/MUN/0014",
        "status": "Unit Details",
        "path_type": "renewal",
    },
    228: {  # THEEPANY
        "username": "MKDYM/THI/00228",
        "status": "Unit Councilors Completed",
        "path_type": "renewal",
    },
}

BOYCE_USER_ID = 506
BOYCE_UNIT_NAME_ID = 350


async def open_active_cycles(db, *, dry_run: bool) -> None:
    now = now_ist()
    for user_id, cfg in MID_WIZARD_UNITS.items():
        existing = (
            await db.execute(
                select(UnitRegistrationCycle).where(
                    UnitRegistrationCycle.registered_user_id == user_id,
                    UnitRegistrationCycle.registration_year == ACTIVE_YEAR,
                )
            )
        ).scalar_one_or_none()
        if existing:
            print(f"  {cfg['username']}: 2027 cycle already exists (id={existing.id}, status={existing.status})")
            if not dry_run:
                existing.status = cfg["status"]
                existing.path_type = cfg["path_type"]
                existing.completed_at = None
                existing.member_count_at_submit = None
                existing.total_fee_at_submit = None
                existing.started_at = existing.started_at or now
            continue

        print(
            f"  {cfg['username']}: create 2027 cycle "
            f"status={cfg['status']!r} path_type={cfg['path_type']!r}"
        )
        if not dry_run:
            db.add(
                UnitRegistrationCycle(
                    registered_user_id=user_id,
                    registration_year=ACTIVE_YEAR,
                    status=cfg["status"],
                    path_type=cfg["path_type"],
                    started_at=now,
                )
            )

        details = (
            await db.execute(select(UnitDetails).where(UnitDetails.registered_user_id == user_id))
        ).scalar_one_or_none()
        if details:
            print(f"    unit_details.registration_year -> {ACTIVE_YEAR}")
            if not dry_run:
                details.registration_year = ACTIVE_YEAR

        reg_data = (
            await db.execute(
                select(UnitRegistrationData).where(UnitRegistrationData.registered_user_id == user_id)
            )
        ).scalar_one_or_none()
        if reg_data:
            print(f"    unit_registration_data.status -> {cfg['status']!r}")
            if not dry_run:
                reg_data.status = cfg["status"]


async def delete_boyce_estate(db, *, dry_run: bool) -> None:
    user = (
        await db.execute(
            select(CustomUser, UnitName)
            .join(UnitName, UnitName.id == CustomUser.unit_name_id)
            .where(CustomUser.id == BOYCE_USER_ID)
        )
    ).first()
    if not user:
        print("  BOYCE ESTATE user not found; skipping delete")
        return

    custom_user, unit_name = user
    print(
        f"  delete user id={custom_user.id} username={custom_user.username} "
        f"unit_name={unit_name.name!r} (unit_name_id={unit_name.id})"
    )

    if dry_run:
        counts = {}
        for table, col in [
            ("unit_registration_payment", "registered_user_id"),
            ("unit_registration_cycle", "registered_user_id"),
            ("unit_councilor", "registered_user_id"),
            ("unit_members", "registered_user_id"),
            ("unit_officials", "registered_user_id"),
            ("unit_details", "registered_user_id"),
            ("unit_registration_data", "registered_user_id"),
            ("refresh_token", "user_id"),
        ]:
            counts[table] = (
                await db.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE {col} = :uid"),
                    {"uid": BOYCE_USER_ID},
                )
            ).scalar_one()
        print(f"    rows to delete: {counts}")
        return

    await db.execute(delete(UnitRegistrationPayment).where(UnitRegistrationPayment.registered_user_id == BOYCE_USER_ID))
    await db.execute(delete(UnitCouncilor).where(UnitCouncilor.registered_user_id == BOYCE_USER_ID))
    await db.execute(delete(UnitMembers).where(UnitMembers.registered_user_id == BOYCE_USER_ID))
    await db.execute(delete(UnitRegistrationCycle).where(UnitRegistrationCycle.registered_user_id == BOYCE_USER_ID))
    await db.execute(delete(UnitOfficials).where(UnitOfficials.registered_user_id == BOYCE_USER_ID))
    await db.execute(delete(UnitDetails).where(UnitDetails.registered_user_id == BOYCE_USER_ID))
    await db.execute(delete(UnitRegistrationData).where(UnitRegistrationData.registered_user_id == BOYCE_USER_ID))
    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == BOYCE_USER_ID))
    await db.execute(delete(CustomUser).where(CustomUser.id == BOYCE_USER_ID))


async def verify(db) -> None:
    current_year = (await db.execute(text("SELECT current_registration_year FROM site_settings LIMIT 1"))).scalar_one()
    print(f"\nVerification (current_registration_year={current_year}):")

    for user_id, cfg in MID_WIZARD_UNITS.items():
        row = (
            await db.execute(
                text(
                    """
                    SELECT c.id, c.registration_year, c.status, c.path_type, d.registration_year
                    FROM unit_registration_cycle c
                    LEFT JOIN unit_details d ON d.registered_user_id = c.registered_user_id
                    WHERE c.registered_user_id = :uid AND c.registration_year = :year
                    """
                ),
                {"uid": user_id, "year": ACTIVE_YEAR},
            )
        ).fetchone()
        print(f"  {cfg['username']}: {row}")

    boyce_user = (
        await db.execute(select(CustomUser.id).where(CustomUser.id == BOYCE_USER_ID))
    ).scalar_one_or_none()
    boyce_registered = (
        await db.execute(
            select(CustomUser.id).where(
                CustomUser.unit_name_id == BOYCE_UNIT_NAME_ID,
                CustomUser.user_type == "2",
            )
        )
    ).scalar_one_or_none()
    unit_name = (
        await db.execute(select(UnitName.name).where(UnitName.id == BOYCE_UNIT_NAME_ID))
    ).scalar_one_or_none()
    print(f"  BOYCE user row exists: {boyce_user is not None}")
    print(f"  BOYCE unit_name registered: {boyce_registered is not None}")
    print(f"  BOYCE unit_name available in catalog: {unit_name!r} (id={BOYCE_UNIT_NAME_ID})")

    totals = (
        await db.execute(
            text(
                """
                SELECT
                  (SELECT COUNT(*) FROM custom_user WHERE user_type='UNIT' AND is_active=true) AS active_units,
                  (SELECT COUNT(*) FROM unit_registration_cycle WHERE registration_year=2026) AS cycles_2026,
                  (SELECT COUNT(*) FROM unit_registration_cycle WHERE registration_year=2027) AS cycles_2027
                """
            )
        )
    ).fetchone()
    print(f"  active_units={totals[0]} cycles_2026={totals[1]} cycles_2027={totals[2]}")


async def main(dry_run: bool) -> None:
    engine = get_async_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        print("Opening active-season cycles for mid-wizard units:")
        await open_active_cycles(db, dry_run=dry_run)

        print("\nRemoving test BOYCE ESTATE unit:")
        await delete_boyce_estate(db, dry_run=dry_run)

        if dry_run:
            print("\nDRY RUN — no changes committed.")
            await db.rollback()
        else:
            await db.commit()
            clear_cache(SITE_SETTINGS_CACHE_KEY)
            clear_cache("admin_dashboard")
            clear_cache("district_wise_data")
            await verify(db)

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=not args.apply))
