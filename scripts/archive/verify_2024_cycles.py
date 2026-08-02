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
        row = (
            await db.execute(
                text(
                    """
                    SELECT registration_year,
                           COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE status = 'Registration Completed') AS completed,
                           COUNT(*) FILTER (WHERE status != 'Registration Completed') AS incomplete
                    FROM unit_registration_cycle
                    WHERE registration_year = 2024
                    GROUP BY registration_year
                    """
                )
            )
        ).fetchone()
        print(f"2024 cycles: {row}")
        approved = (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*) FROM unit_registration_payment p
                    JOIN unit_registration_cycle c ON p.registration_cycle_id = c.id
                    WHERE c.registration_year = 2024 AND p.status = 'APPROVED'
                    """
                )
            )
        ).scalar()
        print(f"2024 approved payments: {approved}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
