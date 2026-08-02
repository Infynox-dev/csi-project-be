import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.common.db import get_async_engine


async def main() -> None:
    engine = get_async_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        queries = {
            "site_settings": """
                SELECT current_registration_year, registration_enabled FROM site_settings
            """,
            "cycles_by_year": """
                SELECT registration_year,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE status = 'Registration Completed') AS completed,
                       COUNT(*) FILTER (WHERE status != 'Registration Completed') AS incomplete
                FROM unit_registration_cycle
                GROUP BY registration_year
                ORDER BY registration_year
            """,
            "details_by_year": """
                SELECT registration_year, COUNT(*)
                FROM unit_details
                WHERE registration_year IS NOT NULL
                GROUP BY registration_year
                ORDER BY registration_year
            """,
            "payments_by_year": """
                SELECT c.registration_year, COUNT(p.id)
                FROM unit_registration_payment p
                JOIN unit_registration_cycle c ON p.registration_cycle_id = c.id
                WHERE p.status = 'APPROVED'
                GROUP BY c.registration_year
                ORDER BY c.registration_year
            """,
            "incomplete_any": """
                SELECT registration_year, status, COUNT(*)
                FROM unit_registration_cycle
                WHERE status != 'Registration Completed'
                GROUP BY registration_year, status
                ORDER BY registration_year
            """,
            "details_mismatch_current": """
                SELECT COUNT(*) FROM unit_details
                WHERE registration_year != (SELECT current_registration_year FROM site_settings LIMIT 1)
            """,
        }
        for label, sql in queries.items():
            print(f"\n=== {label} ===")
            rows = (await db.execute(text(sql))).fetchall()
            for row in rows:
                print(row)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
