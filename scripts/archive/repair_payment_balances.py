"""Recalculate registration payment balances after member roster changes.

Fixes units affected by the remove-then-add swap bug where balance was
incrementally adjusted with a floor at zero, leaving a spurious per-member
balance after admin removed one member and approved a member-add request.

Usage:
    python scripts/repair_payment_balances.py --dry-run
    python scripts/repair_payment_balances.py
    python scripts/repair_payment_balances.py --unit-name "MKDYM/KUM/0050"
"""

import argparse
import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.units.models  # noqa: F401
from app.admin.models import SiteSettings
from app.auth.models import CustomUser, UnitMembers, UnitName
from app.common.db import get_async_engine
from app.units import registration_cycle_service as cycle_service
from app.units.models import (
    PaymentProofStatus,
    RemovedUnitMember,
    RequestStatus,
    UnitMemberAddRequest,
    UnitRegistrationCycle,
    UnitRegistrationPayment,
)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def repair(*, unit_name: str | None, dry_run: bool) -> None:
    engine = get_async_engine()
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        user_ids: list[int] | None = None
        if unit_name:
            row = (
                await db.execute(
                    select(CustomUser.id)
                    .join(UnitName, UnitName.id == CustomUser.unit_name_id)
                    .where(UnitName.name == unit_name)
                )
            ).scalar_one_or_none()
            if row is None:
                print(f"Unit not found: {unit_name}")
                return
            user_ids = [row]

        stmt = select(UnitRegistrationCycle).where(
            UnitRegistrationCycle.status.in_(
                (cycle_service.DECLARATION_SUBMITTED, cycle_service.REGISTRATION_COMPLETED)
            )
        )
        if user_ids is not None:
            stmt = stmt.where(UnitRegistrationCycle.registered_user_id.in_(user_ids))

        cycles = list((await db.execute(stmt)).scalars().all())
        fixed = 0

        settings = (await db.execute(select(SiteSettings).limit(1))).scalar_one_or_none()
        unit_fee = settings.unit_registration_fee if settings and settings.unit_registration_fee else 100
        member_fee = settings.unit_member_fee if settings and settings.unit_member_fee else 10

        for cycle in cycles:
            payments = await cycle_service._get_cycle_payments(db, cycle.id)
            approved = [p for p in payments if p.status == PaymentProofStatus.APPROVED]
            if not approved:
                continue

            live_count = (
                await db.execute(
                    select(func.count())
                    .select_from(UnitMembers)
                    .where(UnitMembers.registered_user_id == cycle.registered_user_id)
                )
            ).scalar() or 0

            latest = max(approved, key=lambda p: p.submitted_at)
            old_balance = latest.balance_amount
            old_snapshot = cycle.member_count_at_submit
            old_fee = cycle.total_fee_at_submit

            expected_fee = unit_fee + live_count * member_fee
            if cycle.member_count_at_submit != live_count or cycle.total_fee_at_submit != expected_fee:
                cycle.member_count_at_submit = live_count
                cycle.total_fee_at_submit = expected_fee

            cycle_service.recalculate_latest_approved_balance(cycle, approved)
            new_balance = latest.balance_amount

            # Swap corruption: admin removed then approved a member-add (net zero roster
            # change) while payment was fully settled — incremental balance math left a
            # spurious per-member balance on a single approved proof.
            if (
                new_balance
                and new_balance > 0
                and len(approved) == 1
                and live_count == cycle.member_count_at_submit
                and latest.reviewed_at is not None
            ):
                removed_after = (
                    await db.execute(
                        select(func.count())
                        .select_from(RemovedUnitMember)
                        .where(
                            RemovedUnitMember.registered_user_id == cycle.registered_user_id,
                            RemovedUnitMember.archived_at >= latest.reviewed_at,
                        )
                    )
                ).scalar() or 0
                adds_after = (
                    await db.execute(
                        select(func.count())
                        .select_from(UnitMemberAddRequest)
                        .where(
                            UnitMemberAddRequest.registered_user_id == cycle.registered_user_id,
                            UnitMemberAddRequest.status == RequestStatus.APPROVED,
                            UnitMemberAddRequest.updated_at >= latest.reviewed_at,
                        )
                    )
                ).scalar() or 0
                if removed_after > 0 and removed_after == adds_after:
                    new_balance = 0
                    latest.balance_amount = 0

            label = f"user={cycle.registered_user_id} cycle={cycle.id}"
            if unit_name:
                label = f"{unit_name} {label}"

            changed = (
                old_balance != new_balance
                or old_snapshot != cycle.member_count_at_submit
                or old_fee != cycle.total_fee_at_submit
            )
            if changed:
                print(
                    f"{label}: live={live_count} snapshot {old_snapshot}->{cycle.member_count_at_submit} "
                    f"fee {old_fee}->{cycle.total_fee_at_submit} balance {old_balance}->{new_balance}"
                )
                fixed += 1
            elif unit_name:
                print(
                    f"{label}: live={live_count} snapshot={cycle.member_count_at_submit} "
                    f"fee={cycle.total_fee_at_submit} balance={new_balance} (unchanged)"
                )

        if dry_run:
            await db.rollback()
        else:
            await db.commit()

        print(f"\n{'Would fix' if dry_run else 'Fixed'} {fixed} cycle(s) dry_run={dry_run}")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit-name", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(repair(unit_name=args.unit_name, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
