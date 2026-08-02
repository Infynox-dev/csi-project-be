"""Cross-check unit_details, cycles, and payments per registration year."""

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
        checks = {
            "details_vs_latest_completed_cycle": """
                SELECT ud.registration_year AS details_year,
                       lc.registration_year AS latest_completed_year,
                       COUNT(*) AS units
                FROM unit_details ud
                JOIN custom_user u ON u.id = ud.registered_user_id
                LEFT JOIN LATERAL (
                    SELECT registration_year
                    FROM unit_registration_cycle c
                    WHERE c.registered_user_id = u.id
                      AND c.status = 'Registration Completed'
                    ORDER BY c.registration_year DESC
                    LIMIT 1
                ) lc ON true
                WHERE ud.registration_year IS NOT NULL
                GROUP BY ud.registration_year, lc.registration_year
                ORDER BY units DESC
            """,
            "completed_cycle_payment_year_mismatch": """
                SELECT c.registration_year AS cycle_year,
                       c2.registration_year AS payment_cycle_year,
                       COUNT(*) AS payments
                FROM unit_registration_payment p
                JOIN unit_registration_cycle c ON c.id = p.registration_cycle_id
                JOIN unit_registration_cycle c2 ON c2.id = p.registration_cycle_id
                WHERE p.status = 'APPROVED'
                GROUP BY c.registration_year, c2.registration_year
                ORDER BY cycle_year
            """,
            "units_with_2026_payment_but_no_2026_cycle": """
                SELECT COUNT(DISTINCT p.registered_user_id)
                FROM unit_registration_payment p
                JOIN unit_registration_cycle c ON c.id = p.registration_cycle_id
                WHERE c.registration_year = 2026 AND p.status = 'APPROVED'
                  AND NOT EXISTS (
                    SELECT 1 FROM unit_registration_cycle c2
                    WHERE c2.registered_user_id = p.registered_user_id
                      AND c2.registration_year = 2026
                      AND c2.status = 'Registration Completed'
                  )
            """,
            "2024_missing_payment_count": """
                SELECT COUNT(*) FROM unit_registration_cycle c
                WHERE c.registration_year = 2024
                  AND c.status = 'Registration Completed'
                  AND NOT EXISTS (
                    SELECT 1 FROM unit_registration_payment p
                    WHERE p.registration_cycle_id = c.id AND p.status = 'APPROVED'
                  )
            """,
            "payment_amounts_2026_sample": """
                SELECT p.total_amount, COUNT(*) 
                FROM unit_registration_payment p
                JOIN unit_registration_cycle c ON c.id = p.registration_cycle_id
                WHERE c.registration_year = 2026 AND p.status = 'APPROVED'
                GROUP BY p.total_amount
                ORDER BY COUNT(*) DESC
                LIMIT 10
            """,
            "cycle_fee_vs_payment_2026_mismatch": """
                SELECT COUNT(*) FROM unit_registration_cycle c
                JOIN unit_registration_payment p ON p.registration_cycle_id = c.id AND p.status = 'APPROVED'
                WHERE c.registration_year = 2026
                  AND c.total_fee_at_submit IS NOT NULL
                  AND p.total_amount IS NOT NULL
                  AND c.total_fee_at_submit != p.total_amount
            """,
        }
        for label, sql in checks.items():
            print(f"\n=== {label} ===")
            for row in (await db.execute(text(sql))).fetchall():
                print(row)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
