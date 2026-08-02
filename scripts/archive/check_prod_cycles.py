"""One-off production DB check for registration cycle path_type."""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import select, text
from app.common.db import get_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.admin.models import SiteSettings


async def main() -> None:
    engine = get_async_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        ver = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
        print(f"ALEMBIC_VERSION={ver}")

        col = (
            await db.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='unit_members' AND column_name='added_registration_cycle_id'"
                )
            )
        ).fetchone()
        print(f"HAS_added_registration_cycle_id={col is not None}")
        print()

        current_year = (
            await db.execute(select(SiteSettings.current_registration_year).limit(1))
        ).scalar_one_or_none()
        print(f"CURRENT_REGISTRATION_YEAR={current_year}")
        print()

        q = await db.execute(
            text(
                """
            SELECT c.id, c.registered_user_id, u.username, un.name,
                   c.registration_year, c.path_type, c.status,
                   (SELECT COUNT(*) FROM unit_members m WHERE m.registered_user_id = c.registered_user_id) as members,
                   (SELECT c2.id FROM unit_registration_cycle c2
                    WHERE c2.registered_user_id = c.registered_user_id
                      AND c2.registration_year = c.registration_year - 1) as prev_cycle_id,
                   (SELECT c2.status FROM unit_registration_cycle c2
                    WHERE c2.registered_user_id = c.registered_user_id
                      AND c2.registration_year = c.registration_year - 1) as prev_status
            FROM unit_registration_cycle c
            JOIN custom_user u ON u.id = c.registered_user_id
            LEFT JOIN unit_name un ON un.id = u.unit_name_id
            WHERE c.registration_year = :year
            ORDER BY members DESC
        """
            ),
            {"year": current_year},
        )
        print("ALL_CURRENT_YEAR_CYCLES:")
        for r in q.fetchall():
            print(
                f"  {r[3]} ({r[2]}) cycle_id={r[0]} path={r[5]} status={r[6]} "
                f"members={r[7]} prev_cycle={r[8]} prev_status={r[9]}"
            )
        print()

        q2 = await db.execute(
            text(
                """
            SELECT c.id, c.registration_year, c.path_type, c.status, c.started_at, c.completed_at
            FROM unit_registration_cycle c
            JOIN custom_user u ON u.id = c.registered_user_id
            JOIN unit_name un ON un.id = u.unit_name_id
            WHERE un.name ILIKE '%KANAM%' OR u.username = 'MKDYM/MUN/0014'
            ORDER BY c.registration_year
        """
            )
        )
        print("KANAM_ALL_CYCLES:")
        for r in q2.fetchall():
            print(f"  year={r[1]} id={r[0]} path={r[2]} status={r[3]} started={r[4]} completed={r[5]}")

        q3 = await db.execute(
            text(
                "SELECT COUNT(*) FROM unit_registration_cycle "
                "WHERE registration_year = 2026 AND status = 'Registration Completed'"
            )
        )
        print()
        print(f"UNITS_COMPLETED_2026={q3.scalar_one()}")

        q4 = await db.execute(
            text(
                """
            SELECT u.username, un.name, c.registration_year, c.path_type, c.status
            FROM custom_user u
            LEFT JOIN unit_name un ON un.id = u.unit_name_id
            LEFT JOIN unit_registration_cycle c ON c.registered_user_id = u.id
            WHERE un.name ILIKE '%KANNAMANAGALAM%' OR u.username = 'MKDYM/MAV/0012'
            ORDER BY c.registration_year NULLS LAST
        """
            )
        )
        print()
        print("KANNAMANAGALAM_ON_PROD:")
        rows = q4.fetchall()
        if not rows:
            print("  (not found)")
        for r in rows:
            print(f"  {r[1]} ({r[0]}) year={r[2]} path={r[3]} status={r[4]}")

        q5 = await db.execute(
            text(
                """
            SELECT c.path_type, COUNT(*)
            FROM unit_registration_cycle c
            WHERE c.registration_year = :year
            GROUP BY c.path_type
        """
            ),
            {"year": current_year},
        )
        print()
        print("PATH_TYPE_COUNTS_CURRENT_YEAR:")
        for r in q5.fetchall():
            print(f"  {r[0]}: {r[1]}")


if __name__ == "__main__":
    asyncio.run(main())
