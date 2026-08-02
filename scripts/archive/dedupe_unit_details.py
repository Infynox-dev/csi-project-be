"""Remove duplicate unit_details / unit_officials rows (keep newest per user).

Usage:
    python scripts/dedupe_unit_details.py
    python scripts/dedupe_unit_details.py --dry-run
"""

import argparse
import asyncio
import sys

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.auth.models  # noqa: F401
import app.units.models  # noqa: F401
from app.auth.models import UnitDetails, UnitOfficials
from app.common.db import get_async_engine

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def dedupe(model, *, dry_run: bool) -> int:
    engine = get_async_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)
    removed = 0

    async with Session() as db:
        dup_users = (
            await db.execute(
                select(model.registered_user_id)
                .group_by(model.registered_user_id)
                .having(func.count() > 1)
            )
        ).scalars().all()

        for user_id in dup_users:
            rows = (
                await db.execute(
                    select(model)
                    .where(model.registered_user_id == user_id)
                    .order_by(model.id.desc())
                )
            ).scalars().all()
            keep_id = rows[0].id
            delete_ids = [row.id for row in rows[1:]]
            print(f"{model.__tablename__} user_id={user_id}: keep id={keep_id}, remove {delete_ids}")
            if not dry_run and delete_ids:
                await db.execute(delete(model).where(model.id.in_(delete_ids)))
                removed += len(delete_ids)

        if not dry_run:
            await db.commit()

    await engine.dispose()
    return removed


async def main(dry_run: bool) -> None:
    details_removed = await dedupe(UnitDetails, dry_run=dry_run)
    officials_removed = await dedupe(UnitOfficials, dry_run=dry_run)
    action = "Would remove" if dry_run else "Removed"
    print(f"{action} {details_removed} duplicate unit_details rows")
    print(f"{action} {officials_removed} duplicate unit_officials rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
