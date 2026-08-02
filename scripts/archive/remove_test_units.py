"""Remove test unit registrations while keeping unit names in the catalog."""

import argparse
import asyncio
import sys

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
    UserType,
)
from app.common.cache import clear_cache
from app.common.db import get_async_engine
from app.units.models import UnitRegistrationCycle, UnitRegistrationPayment

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Test units to remove (user_id, unit_name_id, label)
TEST_UNITS = [
    (393, 196, "504 COLONY"),
    (512, 350, "BOYCE ESTATE"),
    (518, 82, "VAIKKOM"),
]

DELETE_TABLES = [
    ("unit_registration_payment", "registered_user_id"),
    ("unit_councilor", "registered_user_id"),
    ("unit_members", "registered_user_id"),
    ("unit_registration_cycle", "registered_user_id"),
    ("unit_officials", "registered_user_id"),
    ("unit_details", "registered_user_id"),
    ("unit_registration_data", "registered_user_id"),
    ("refresh_token", "user_id"),
]


async def delete_test_unit(db, user_id: int, unit_name_id: int, label: str, *, dry_run: bool) -> None:
    row = (
        await db.execute(
            select(CustomUser, UnitName)
            .join(UnitName, UnitName.id == CustomUser.unit_name_id)
            .where(CustomUser.id == user_id)
        )
    ).first()
    if not row:
        print(f"  {label}: user id={user_id} not found; skipping")
        return

    custom_user, unit_name = row
    print(
        f"  {label}: delete user id={custom_user.id} username={custom_user.username} "
        f"unit_name={unit_name.name!r} (unit_name_id={unit_name.id})"
    )

    if dry_run:
        counts = {}
        for table, col in DELETE_TABLES:
            counts[table] = (
                await db.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE {col} = :uid"),
                    {"uid": user_id},
                )
            ).scalar_one()
        print(f"    rows to delete: {counts}")
        return

    await db.execute(delete(UnitRegistrationPayment).where(UnitRegistrationPayment.registered_user_id == user_id))
    await db.execute(delete(UnitCouncilor).where(UnitCouncilor.registered_user_id == user_id))
    await db.execute(delete(UnitMembers).where(UnitMembers.registered_user_id == user_id))
    await db.execute(delete(UnitRegistrationCycle).where(UnitRegistrationCycle.registered_user_id == user_id))
    await db.execute(delete(UnitOfficials).where(UnitOfficials.registered_user_id == user_id))
    await db.execute(delete(UnitDetails).where(UnitDetails.registered_user_id == user_id))
    await db.execute(delete(UnitRegistrationData).where(UnitRegistrationData.registered_user_id == user_id))
    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
    await db.execute(delete(CustomUser).where(CustomUser.id == user_id))


async def verify(db) -> None:
    print("\nVerification:")
    for user_id, unit_name_id, label in TEST_UNITS:
        user_exists = (
            await db.execute(select(CustomUser.id).where(CustomUser.id == user_id))
        ).scalar_one_or_none()
        registered = (
            await db.execute(
                select(CustomUser.id).where(
                    CustomUser.unit_name_id == unit_name_id,
                    CustomUser.user_type == UserType.UNIT,
                )
            )
        ).scalar_one_or_none()
        unit_name = (
            await db.execute(select(UnitName.name).where(UnitName.id == unit_name_id))
        ).scalar_one_or_none()
        available = (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*) FROM unit_name un
                    WHERE un.id = :uid
                      AND NOT EXISTS (
                        SELECT 1 FROM custom_user cu
                        WHERE cu.unit_name_id = un.id AND cu.user_type = 'UNIT'
                      )
                    """
                ),
                {"uid": unit_name_id},
            )
        ).scalar_one()
        print(
            f"  {label}: user_exists={user_exists is not None} "
            f"registered={registered is not None} "
            f"catalog={unit_name!r} available_for_registration={available == 1}"
        )


async def main(dry_run: bool) -> None:
    engine = get_async_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        print("Removing test units:")
        for user_id, unit_name_id, label in TEST_UNITS:
            await delete_test_unit(db, user_id, unit_name_id, label, dry_run=dry_run)

        if dry_run:
            print("\nDRY RUN — no changes committed.")
            await db.rollback()
        else:
            await db.commit()
            clear_cache("site_settings_v1")
            clear_cache("admin_dashboard")
            clear_cache("district_wise_data")
            await verify(db)

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Commit deletions (default is dry run)")
    args = parser.parse_args()
    asyncio.run(main(dry_run=not args.apply))
