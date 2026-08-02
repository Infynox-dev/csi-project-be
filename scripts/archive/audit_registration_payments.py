"""Audit registration payment data against registration years/cycles."""

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
            "payments_by_cycle_year": """
                SELECT c.registration_year,
                       p.status,
                       COUNT(*) AS payment_count,
                       COUNT(DISTINCT p.registered_user_id) AS distinct_units
                FROM unit_registration_payment p
                LEFT JOIN unit_registration_cycle c ON c.id = p.registration_cycle_id
                GROUP BY c.registration_year, p.status
                ORDER BY c.registration_year NULLS LAST, p.status
            """,
            "orphan_payments_no_cycle": """
                SELECT COUNT(*) AS orphan_count
                FROM unit_registration_payment
                WHERE registration_cycle_id IS NULL
            """,
            "orphan_payment_details": """
                SELECT p.id, p.registered_user_id, u.username, un.name, p.status, p.total_amount, p.submitted_at
                FROM unit_registration_payment p
                JOIN custom_user u ON u.id = p.registered_user_id
                LEFT JOIN unit_name un ON un.id = u.unit_name_id
                WHERE p.registration_cycle_id IS NULL
                ORDER BY p.id
                LIMIT 20
            """,
            "completed_cycles_without_approved_payment": """
                SELECT c.registration_year, COUNT(*) AS units
                FROM unit_registration_cycle c
                WHERE c.status = 'Registration Completed'
                  AND NOT EXISTS (
                    SELECT 1 FROM unit_registration_payment p
                    WHERE p.registration_cycle_id = c.id
                      AND p.status = 'APPROVED'
                  )
                GROUP BY c.registration_year
                ORDER BY c.registration_year
            """,
            "units_missing_payment_for_completed_cycle": """
                SELECT c.registration_year, u.username, un.name, c.id AS cycle_id, c.status
                FROM unit_registration_cycle c
                JOIN custom_user u ON u.id = c.registered_user_id
                LEFT JOIN unit_name un ON un.id = u.unit_name_id
                WHERE c.status = 'Registration Completed'
                  AND NOT EXISTS (
                    SELECT 1 FROM unit_registration_payment p
                    WHERE p.registration_cycle_id = c.id AND p.status = 'APPROVED'
                  )
                ORDER BY c.registration_year, un.name
                LIMIT 30
            """,
            "payment_cycle_user_mismatch": """
                SELECT p.id, p.registered_user_id, p.registration_cycle_id,
                       c.registered_user_id AS cycle_user_id,
                       c.registration_year, u.username, un.name
                FROM unit_registration_payment p
                JOIN unit_registration_cycle c ON c.id = p.registration_cycle_id
                JOIN custom_user u ON u.id = p.registered_user_id
                LEFT JOIN unit_name un ON un.id = u.unit_name_id
                WHERE p.registered_user_id != c.registered_user_id
                LIMIT 20
            """,
            "duplicate_approved_per_cycle": """
                SELECT c.registration_year, c.id AS cycle_id, u.username, un.name, COUNT(*) AS approved_count
                FROM unit_registration_payment p
                JOIN unit_registration_cycle c ON c.id = p.registration_cycle_id
                JOIN custom_user u ON u.id = c.registered_user_id
                LEFT JOIN unit_name un ON un.id = u.unit_name_id
                WHERE p.status = 'APPROVED'
                GROUP BY c.registration_year, c.id, u.username, un.name
                HAVING COUNT(*) > 1
                ORDER BY approved_count DESC, c.registration_year
                LIMIT 30
            """,
            "in_progress_2027_payments": """
                SELECT u.username, un.name, c.id AS cycle_id, c.status,
                       COUNT(p.id) AS payment_count,
                       STRING_AGG(DISTINCT p.status::text, ', ') AS payment_statuses
                FROM unit_registration_cycle c
                JOIN custom_user u ON u.id = c.registered_user_id
                LEFT JOIN unit_name un ON un.id = u.unit_name_id
                LEFT JOIN unit_registration_payment p ON p.registration_cycle_id = c.id
                WHERE c.registration_year = (SELECT current_registration_year FROM site_settings LIMIT 1)
                GROUP BY u.username, un.name, c.id, c.status
                ORDER BY un.name
            """,
            "boyce_payments_remain": """
                SELECT COUNT(*) FROM unit_registration_payment
                WHERE registered_user_id = 506
            """,
            "summary_counts": """
                SELECT
                  (SELECT current_registration_year FROM site_settings LIMIT 1) AS current_year,
                  (SELECT COUNT(*) FROM unit_registration_cycle WHERE registration_year=2026 AND status='Registration Completed') AS completed_2026,
                  (SELECT COUNT(*) FROM unit_registration_payment p JOIN unit_registration_cycle c ON c.id=p.registration_cycle_id WHERE c.registration_year=2026 AND p.status='APPROVED') AS approved_payments_2026,
                  (SELECT COUNT(*) FROM unit_registration_cycle WHERE registration_year=2027) AS cycles_2027,
                  (SELECT COUNT(*) FROM unit_registration_payment p JOIN unit_registration_cycle c ON c.id=p.registration_cycle_id WHERE c.registration_year=2027) AS payments_2027
            """,
        }

        for label, sql in queries.items():
            print(f"\n=== {label} ===")
            rows = (await db.execute(text(sql))).fetchall()
            if not rows:
                print("(no rows)")
            for row in rows:
                print(row)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
