"""Bulk-mark older-season registration cycles as completed (no admin approval).

Legacy = registration seasons before the current registration year only.
Current-season (e.g. 2026-2027) registrations always require per-unit admin approval
via the All Units page.

Usage:
    python scripts/bulk_complete_legacy_registrations.py
    python scripts/bulk_complete_legacy_registrations.py --apply
"""

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker

import app.auth.models  # noqa: F401
import app.units.models  # noqa: F401
from app.common.db import get_async_engine
from app.units import registration_cycle_service as cycle_service

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def main(dry_run: bool) -> None:
    engine = get_async_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        updated = await cycle_service.bulk_complete_legacy_registrations(db, dry_run=dry_run)
        print(f"{'Would update' if dry_run else 'Updated'} {len(updated)} legacy cycle(s)")
        for row in updated:
            print(
                f"  cycle_id={row['cycle_id']} user_id={row['user_id']} "
                f"year={row['registration_year']} "
                f"status={row['old_status']!r} -> {row['new_status']!r} "
                f"members={row['member_count']} fee={row['total_fee']}"
            )

        if dry_run:
            print("\nDRY RUN — no changes committed.")
            await db.rollback()
        elif updated:
            print(f"\nCommitted {len(updated)} cycle(s).")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bulk-complete legacy registration cycles without admin approval"
    )
    parser.add_argument("--apply", action="store_true", help="Persist changes (default is dry-run)")
    args = parser.parse_args()
    asyncio.run(main(dry_run=not args.apply))
