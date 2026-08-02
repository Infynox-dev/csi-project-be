"""One-off script: back-fill archive_year='2025' on all existing archived_unit_member rows."""

import asyncio
import sys
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import text
from app.common.db import get_async_engine

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def backfill() -> None:
    engine = get_async_engine()
    AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    async with AsyncSession() as db:
        sql = text(
            "UPDATE archived_unit_member "
            "SET archive_year = '2025', "
            "archive_reason = 'Archived as part of 2025 yearly registration age limit update' "
            "WHERE archive_year IS NULL"
        )
        result = await db.execute(sql)
        await db.commit()
        print(f"Updated {result.rowcount} records with archive_year='2025'")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(backfill())
