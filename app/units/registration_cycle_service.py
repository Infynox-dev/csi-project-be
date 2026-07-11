"""Helpers for per-year unit registration cycles."""

from datetime import datetime
from typing import Any, Literal, Optional

from app.common.datetime_utils import current_year_ist, now_ist

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.models import SiteSettings
from app.auth.models import UnitCouncilor, UnitDetails, UnitOfficials, UnitMembers
from app.units.models import (
    PaymentProofStatus,
    UnitRegistrationCycle,
    UnitRegistrationPayment,
)

REGISTRATION_COMPLETED = "Registration Completed"
DECLARATION_SUBMITTED = "Declaration Submitted"
UNIT_OFFICIALS_COMPLETED = "Unit Officials Completed"
UNIT_COUNCILORS_COMPLETED = "Unit Councilors Completed"

COUNCILORS_REOPEN_STATUSES = frozenset(
    {UNIT_COUNCILORS_COMPLETED, DECLARATION_SUBMITTED}
)

CHANGE_REQUEST_REQUIRED_MSG = (
    "This update must be submitted as a change request. "
    "Use the request forms under My Requests."
)

MEMBER_PROFILE_FIELDS = ("name", "gender", "dob", "number", "qualification", "blood_group")

# Member fields that may be updated inline during renewal registration.
RENEWAL_WIZARD_INLINE_MEMBER_FIELDS = frozenset({"blood_group", "number", "qualification"})


def required_councilor_count(member_count: int) -> int:
    """Return the number of councilors required for a unit roster size."""
    if member_count <= 25:
        return 1
    if member_count <= 50:
        return 2
    if member_count <= 75:
        return 3
    if member_count <= 100:
        return 4
    return 5


async def ensure_councilor_requirement_met(
    db: AsyncSession,
    user_id: int,
    *,
    member_count: Optional[int] = None,
) -> None:
    """Raise when the unit has not selected the required number of councilors."""
    if member_count is None:
        member_count_result = await db.execute(
            select(func.count())
            .select_from(UnitMembers)
            .where(UnitMembers.registered_user_id == user_id)
        )
        member_count = member_count_result.scalar() or 0

    required = required_councilor_count(member_count)
    councilor_count_result = await db.execute(
        select(func.count())
        .select_from(UnitCouncilor)
        .where(UnitCouncilor.registered_user_id == user_id)
    )
    councilor_count = councilor_count_result.scalar() or 0

    if councilor_count != required:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Select exactly {required} councilor(s) before continuing. "
                f"Currently selected: {councilor_count}."
            ),
        )


async def get_site_settings(db: AsyncSession) -> Optional[SiteSettings]:
    result = await db.execute(select(SiteSettings).limit(1))
    return result.scalar_one_or_none()


async def get_current_registration_year(db: AsyncSession) -> int:
    settings = await get_site_settings(db)
    if settings and settings.current_registration_year:
        return settings.current_registration_year
    return current_year_ist()


async def is_registration_enabled(db: AsyncSession) -> bool:
    settings = await get_site_settings(db)
    return bool(settings and settings.registration_enabled)


async def get_cycle(
    db: AsyncSession,
    user_id: int,
    registration_year: int,
) -> Optional[UnitRegistrationCycle]:
    result = await db.execute(
        select(UnitRegistrationCycle).where(
            UnitRegistrationCycle.registered_user_id == user_id,
            UnitRegistrationCycle.registration_year == registration_year,
        )
    )
    return result.scalar_one_or_none()


async def get_unit_details_for_user(
    db: AsyncSession,
    user_id: int,
) -> Optional[UnitDetails]:
    """Return the canonical unit_details row (newest id if duplicates exist)."""
    result = await db.execute(
        select(UnitDetails)
        .where(UnitDetails.registered_user_id == user_id)
        .order_by(UnitDetails.id.desc())
    )
    return result.scalars().first()


async def get_unit_officials_for_user(
    db: AsyncSession,
    user_id: int,
) -> Optional[UnitOfficials]:
    """Return the canonical unit_officials row (newest id if duplicates exist)."""
    result = await db.execute(
        select(UnitOfficials)
        .where(UnitOfficials.registered_user_id == user_id)
        .order_by(UnitOfficials.id.desc())
    )
    return result.scalars().first()


async def get_latest_completed_cycle(
    db: AsyncSession,
    user_id: int,
) -> Optional[UnitRegistrationCycle]:
    result = await db.execute(
        select(UnitRegistrationCycle)
        .where(
            UnitRegistrationCycle.registered_user_id == user_id,
            UnitRegistrationCycle.status == REGISTRATION_COMPLETED,
        )
        .order_by(UnitRegistrationCycle.registration_year.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def determine_path_type(
    db: AsyncSession,
    user_id: int,
    current_year: int,
) -> Literal["fresh", "renewal"]:
    prev_year = current_year - 1
    prev_cycle = await get_cycle(db, user_id, prev_year)
    if prev_cycle and prev_cycle.status == REGISTRATION_COMPLETED:
        return "renewal"
    return "fresh"


async def create_cycle(
    db: AsyncSession,
    user_id: int,
    registration_year: int,
    *,
    path_type: Literal["fresh", "renewal"],
    initial_status: str = "Registration Started",
) -> UnitRegistrationCycle:
    cycle = UnitRegistrationCycle(
        registered_user_id=user_id,
        registration_year=registration_year,
        status=initial_status,
        path_type=path_type,
    )
    db.add(cycle)
    await db.flush()
    return cycle


async def get_or_create_current_cycle(
    db: AsyncSession,
    user_id: int,
) -> Optional[UnitRegistrationCycle]:
    """
    Return the cycle for the current registration year, creating one when
    registration is open and the unit has no cycle yet for that year.
    """
    current_year = await get_current_registration_year(db)
    existing = await get_cycle(db, user_id, current_year)
    if existing:
        return existing

    if not await is_registration_enabled(db):
        return None

    path_type = await determine_path_type(db, user_id, current_year)
    cycle = await create_cycle(db, user_id, current_year, path_type=path_type)
    await db.commit()
    await db.refresh(cycle)
    return cycle


async def resolve_active_cycle(
    db: AsyncSession,
    user_id: int,
) -> tuple[Optional[UnitRegistrationCycle], bool, bool]:
    """
    Resolve which cycle drives the application form.

    Returns (cycle, registration_enabled, has_any_completed_cycle).
    """
    current_year = await get_current_registration_year(db)
    enabled = await is_registration_enabled(db)

    cycle = await get_cycle(db, user_id, current_year)
    if not cycle and enabled:
        path_type = await determine_path_type(db, user_id, current_year)
        cycle = await create_cycle(db, user_id, current_year, path_type=path_type)
        await db.commit()
        await db.refresh(cycle)

    latest_completed = await get_latest_completed_cycle(db, user_id)
    has_completed = latest_completed is not None

    if cycle is None and latest_completed is not None:
        return latest_completed, enabled, has_completed

    return cycle, enabled, has_completed


def require_cycle_in_progress(cycle: UnitRegistrationCycle) -> None:
    if cycle.status == REGISTRATION_COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration for this year is complete. Use change request workflows for updates.",
        )
    if cycle.status == DECLARATION_SUBMITTED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Declaration has been submitted. Awaiting admin completion of registration.",
        )


def require_cycle_open_for_councilor_edits(cycle: UnitRegistrationCycle) -> None:
    """Allow councilor roster edits until admin marks registration complete."""
    if cycle.status == REGISTRATION_COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration for this year is complete. Use change request workflows for updates.",
        )


async def reopen_councilors_after_roster_change(
    cycle: UnitRegistrationCycle,
) -> bool:
    """
    Move the unit back to the councilors wizard step when the roster changes
    after councilors were confirmed or the declaration was submitted.
    """
    if cycle.status not in COUNCILORS_REOPEN_STATUSES:
        return False

    cycle.status = UNIT_OFFICIALS_COMPLETED
    cycle.member_count_at_submit = None
    cycle.total_fee_at_submit = None
    return True


def require_fresh_registration_for_direct_edits(cycle: UnitRegistrationCycle) -> None:
    """Block wizard edits on renewal that must go through change-request workflows."""
    require_cycle_in_progress(cycle)
    if cycle.path_type == "renewal":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=CHANGE_REQUEST_REQUIRED_MSG,
        )


def member_added_in_cycle(member, cycle: UnitRegistrationCycle) -> bool:
    """True when the member was created during the given registration cycle."""
    return member.added_registration_cycle_id == cycle.id


def validate_renewal_member_update(cycle: UnitRegistrationCycle, data, member) -> None:
    """On renewal, selected member fields can be updated inline via the wizard."""
    require_cycle_in_progress(cycle)
    if cycle.path_type != "renewal":
        return
    if member_added_in_cycle(member, cycle):
        return

    changed_profile_fields = [
        field
        for field in MEMBER_PROFILE_FIELDS
        if field not in RENEWAL_WIZARD_INLINE_MEMBER_FIELDS
        and getattr(data, field, None) is not None
    ]
    if changed_profile_fields:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Member profile changes during renewal must use a Member Info Change request. "
                "Phone, qualification, blood group, and living location can be updated here."
            ),
        )


async def update_cycle_status(
    db: AsyncSession,
    cycle: UnitRegistrationCycle,
    new_status: str,
) -> None:
    cycle.status = new_status
    await db.commit()


async def submit_declaration(
    db: AsyncSession,
    cycle: UnitRegistrationCycle,
    member_count: int,
    total_fee: int,
) -> None:
    """Record unit declaration submission; admin completes the cycle later."""
    cycle.status = DECLARATION_SUBMITTED
    cycle.member_count_at_submit = member_count
    cycle.total_fee_at_submit = total_fee
    await db.commit()


async def complete_cycle(
    db: AsyncSession,
    cycle: UnitRegistrationCycle,
    member_count: int,
    total_fee: int,
) -> None:
    cycle.status = REGISTRATION_COMPLETED
    cycle.member_count_at_submit = member_count
    cycle.total_fee_at_submit = total_fee
    cycle.completed_at = now_ist()

    await _sync_unit_details_after_completion(db, cycle, member_count)

    await db.commit()


async def resolve_cycle_submission_data(
    db: AsyncSession,
    cycle: UnitRegistrationCycle,
) -> tuple[int, int]:
    """Return member count and total fee from cycle snapshot or current unit data."""
    if cycle.member_count_at_submit is not None and cycle.total_fee_at_submit is not None:
        return cycle.member_count_at_submit, cycle.total_fee_at_submit

    member_count_result = await db.execute(
        select(func.count())
        .select_from(UnitMembers)
        .where(UnitMembers.registered_user_id == cycle.registered_user_id)
    )
    member_count = member_count_result.scalar() or 0
    settings = await get_site_settings(db)
    unit_fee = (
        settings.unit_registration_fee
        if settings and settings.unit_registration_fee is not None
        else 100
    )
    member_fee = (
        settings.unit_member_fee
        if settings and settings.unit_member_fee is not None
        else 10
    )
    return member_count, unit_fee + (member_count * member_fee)


def can_admin_complete_registration(
    cycle: Optional[UnitRegistrationCycle],
    *,
    payment_fully_approved: bool,
    current_registration_year: int,
    latest_payment_reviewed_at: Optional[datetime] = None,
) -> bool:
    """
    Whether admin may finalize a registration cycle via the admin UI.

    Current season: all paid registrations require admin approval before they are
    considered fully complete (including legacy auto-completed cycles).

    Older seasons are legacy data and are handled by bulk migration scripts instead.
    """
    if cycle is None:
        return False
    if cycle.registration_year != current_registration_year:
        return False
    if not payment_fully_approved:
        return False

    if cycle.status == REGISTRATION_COMPLETED:
        if latest_payment_reviewed_at is None:
            return False
        if cycle.completed_at is None:
            return True
        return cycle.completed_at < latest_payment_reviewed_at

    return True


async def _sync_unit_details_after_completion(
    db: AsyncSession,
    cycle: UnitRegistrationCycle,
    member_count: int,
) -> None:
    details_result = await db.execute(
        select(UnitDetails).where(UnitDetails.registered_user_id == cycle.registered_user_id)
    )
    unit_details = details_result.scalars().first()
    if unit_details:
        unit_details.registration_year = cycle.registration_year
        unit_details.number_of_unit_members = member_count


async def confirm_current_season_registration(
    db: AsyncSession,
    cycle: UnitRegistrationCycle,
    member_count: int,
    total_fee: int,
) -> None:
    """Admin-confirm a current-season cycle (including legacy auto-completed ones)."""
    cycle.status = REGISTRATION_COMPLETED
    cycle.member_count_at_submit = member_count
    cycle.total_fee_at_submit = total_fee
    cycle.completed_at = now_ist()
    await _sync_unit_details_after_completion(db, cycle, member_count)
    await db.commit()


async def admin_complete_registration(
    db: AsyncSession,
    user_id: int,
) -> UnitRegistrationCycle:
    """Admin-finalize the current season once full payment is approved."""
    current_year = await get_current_registration_year(db)
    cycle = await get_cycle(db, user_id, current_year)
    if cycle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No registration cycle found for the current season.",
        )
    if not await cycle_is_fully_paid(db, cycle.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Full payment must be approved before registration can be completed.",
        )

    reviewed_at = await get_latest_approved_payment_reviewed_at(db, cycle.id)
    if (
        cycle.status == REGISTRATION_COMPLETED
        and cycle.completed_at is not None
        and reviewed_at is not None
        and cycle.completed_at >= reviewed_at
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration is already completed for this season.",
        )

    member_count, total_fee = await resolve_cycle_submission_data(db, cycle)
    if cycle.status == REGISTRATION_COMPLETED:
        await confirm_current_season_registration(db, cycle, member_count, total_fee)
    else:
        await complete_cycle(db, cycle, member_count, total_fee)
    await db.refresh(cycle)
    return cycle


async def cycle_needs_legacy_bulk_completion(
    cycle: UnitRegistrationCycle,
    current_registration_year: int,
) -> bool:
    """Return True for older-season cycles that should be bulk-completed without admin approval."""
    if cycle.registration_year >= current_registration_year:
        return False

    if (
        cycle.status == REGISTRATION_COMPLETED
        and cycle.completed_at is not None
        and cycle.member_count_at_submit is not None
        and cycle.total_fee_at_submit is not None
    ):
        return False

    return True


async def bulk_complete_legacy_registrations(
    db: AsyncSession,
    *,
    dry_run: bool = False,
) -> list[dict]:
    """
    Mark older-season registration cycles as completed without admin approval.

    Current-season registrations always require per-unit admin approval in the UI.
    """
    current_year = await get_current_registration_year(db)
    result = await db.execute(select(UnitRegistrationCycle))
    cycles = list(result.scalars().all())

    updated: list[dict] = []
    for cycle in cycles:
        if not cycle_needs_legacy_bulk_completion(cycle, current_year):
            continue

        member_count, total_fee = await resolve_cycle_submission_data(db, cycle)
        old_status = cycle.status
        if dry_run:
            updated.append(
                {
                    "cycle_id": cycle.id,
                    "user_id": cycle.registered_user_id,
                    "registration_year": cycle.registration_year,
                    "old_status": old_status,
                    "new_status": REGISTRATION_COMPLETED,
                    "member_count": member_count,
                    "total_fee": total_fee,
                }
            )
            continue

        await complete_cycle(db, cycle, member_count, total_fee)
        updated.append(
            {
                "cycle_id": cycle.id,
                "user_id": cycle.registered_user_id,
                "registration_year": cycle.registration_year,
                "old_status": old_status,
                "new_status": REGISTRATION_COMPLETED,
                "member_count": member_count,
                "total_fee": total_fee,
            }
        )

    return updated


async def get_payment_status_for_cycle(
    db: AsyncSession,
    user_id: int,
    cycle_id: int,
) -> dict:
    stmt = (
        select(UnitRegistrationPayment)
        .where(
            UnitRegistrationPayment.registered_user_id == user_id,
            UnitRegistrationPayment.registration_cycle_id == cycle_id,
        )
        .order_by(UnitRegistrationPayment.submitted_at.asc())
    )
    result = await db.execute(stmt)
    payments = list(result.scalars().all())

    balance_amount = None
    if not payments:
        overall = "not_submitted"
    else:
        approved_payments = [p for p in payments if p.status == PaymentProofStatus.APPROVED]
        if approved_payments:
            latest_approved = approved_payments[-1]
            balance_amount = latest_approved.balance_amount
            if balance_amount is not None and balance_amount > 0:
                overall = "partial"
            else:
                overall = "approved"
        else:
            latest = payments[-1]
            overall = "rejected" if latest.status == PaymentProofStatus.REJECTED else "pending"

    latest_rejection_note = None
    items = []
    for p in payments:
        if p.status == PaymentProofStatus.REJECTED:
            latest_rejection_note = p.rejection_note
        items.append(p)

    return {
        "overall_status": overall,
        "balance_amount": balance_amount,
        "latest_rejection_note": latest_rejection_note,
        "payments": items,
    }


async def get_latest_approved_payment_reviewed_at(
    db: AsyncSession,
    cycle_id: int,
) -> Optional[datetime]:
    result = await db.execute(
        select(UnitRegistrationPayment.reviewed_at)
        .where(
            UnitRegistrationPayment.registration_cycle_id == cycle_id,
            UnitRegistrationPayment.status == PaymentProofStatus.APPROVED,
        )
        .order_by(UnitRegistrationPayment.submitted_at.asc())
    )
    reviewed_times = [row[0] for row in result.all() if row[0] is not None]
    return reviewed_times[-1] if reviewed_times else None


async def cycle_is_fully_paid(db: AsyncSession, cycle_id: int) -> bool:
    result = await db.execute(
        select(UnitRegistrationPayment)
        .where(
            UnitRegistrationPayment.registration_cycle_id == cycle_id,
            UnitRegistrationPayment.status == PaymentProofStatus.APPROVED,
        )
        .order_by(UnitRegistrationPayment.submitted_at.asc())
    )
    approved_payments = list(result.scalars().all())
    if not approved_payments:
        return False
    latest_approved = approved_payments[-1]
    return latest_approved.balance_amount in (None, 0)


async def cycle_has_pending_payment(db: AsyncSession, cycle_id: int) -> bool:
    result = await db.execute(
        select(func.count())
        .select_from(UnitRegistrationPayment)
        .where(
            UnitRegistrationPayment.registration_cycle_id == cycle_id,
            UnitRegistrationPayment.status == PaymentProofStatus.PENDING,
        )
    )
    return (result.scalar() or 0) > 0


async def cycle_has_blocking_pending_payment(
    db: AsyncSession,
    cycle_id: int,
    *,
    registration_total: Optional[int],
) -> bool:
    """
    Return True when a pending proof must block a new upload.

    Pending proofs submitted before a fee revision (total above current cycle
    fee) are not blocking — the unit may submit an updated proof.
    """
    result = await db.execute(
        select(UnitRegistrationPayment)
        .where(
            UnitRegistrationPayment.registration_cycle_id == cycle_id,
            UnitRegistrationPayment.status == PaymentProofStatus.PENDING,
        )
        .order_by(UnitRegistrationPayment.submitted_at.asc())
    )
    pending = list(result.scalars().all())
    if not pending:
        return False

    if registration_total is None:
        return True

    for payment in pending:
        if payment.total_amount is None or payment.total_amount <= registration_total:
            return True
    return False


async def reconcile_cycle_fee_after_member_removals(
    db: AsyncSession,
    *,
    registered_user_id: int,
    cycle: UnitRegistrationCycle,
) -> bool:
    """
    Align cycle fee snapshot with the live roster after declaration when members
    were added or removed without going through the fee-adjustment flow.
    """
    if cycle.status not in (DECLARATION_SUBMITTED, REGISTRATION_COMPLETED):
        return False
    if cycle.member_count_at_submit is None:
        return False

    member_count_result = await db.execute(
        select(func.count())
        .select_from(UnitMembers)
        .where(UnitMembers.registered_user_id == registered_user_id)
    )
    active_count = member_count_result.scalar() or 0
    delta_members = active_count - cycle.member_count_at_submit
    if delta_members == 0:
        return False

    return await adjust_fee_for_member_delta(
        db,
        registered_user_id=registered_user_id,
        delta_members=delta_members,
    )


async def supersede_stale_pending_payments(
    db: AsyncSession,
    cycle_id: int,
    *,
    registration_total: int,
) -> int:
    """Reject pending proofs that were submitted for a higher pre-revision fee."""
    result = await db.execute(
        select(UnitRegistrationPayment)
        .where(
            UnitRegistrationPayment.registration_cycle_id == cycle_id,
            UnitRegistrationPayment.status == PaymentProofStatus.PENDING,
            UnitRegistrationPayment.total_amount.isnot(None),
            UnitRegistrationPayment.total_amount > registration_total,
        )
    )
    stale = list(result.scalars().all())
    for payment in stale:
        payment.status = PaymentProofStatus.REJECTED
        payment.rejection_note = (
            "Superseded — registration fee was revised after member update. "
            "Please submit a new proof for the updated amount."
        )
        payment.reviewed_at = now_ist()
    return len(stale)


async def _get_member_fee(db: AsyncSession) -> int:
    settings = await get_site_settings(db)
    if settings and settings.unit_member_fee is not None:
        return settings.unit_member_fee
    return 10


async def _get_cycle_payments(
    db: AsyncSession,
    cycle_id: int,
) -> list[UnitRegistrationPayment]:
    result = await db.execute(
        select(UnitRegistrationPayment)
        .where(UnitRegistrationPayment.registration_cycle_id == cycle_id)
        .order_by(UnitRegistrationPayment.submitted_at.asc())
    )
    return list(result.scalars().all())


async def get_payments_by_cycle_ids(
    db: AsyncSession,
    cycle_ids: list[int],
) -> dict[int, list[UnitRegistrationPayment]]:
    """Load all payment proofs for many cycles in one query."""
    if not cycle_ids:
        return {}

    result = await db.execute(
        select(UnitRegistrationPayment)
        .where(UnitRegistrationPayment.registration_cycle_id.in_(cycle_ids))
        .order_by(
            UnitRegistrationPayment.registration_cycle_id,
            UnitRegistrationPayment.submitted_at.asc(),
        )
    )
    by_cycle: dict[int, list[UnitRegistrationPayment]] = {cid: [] for cid in cycle_ids}
    for payment in result.scalars().all():
        by_cycle[payment.registration_cycle_id].append(payment)
    return by_cycle


def compute_total_paid_for_approved_payments(
    approved: list[UnitRegistrationPayment],
    *,
    fee_owed: Optional[int] = None,
) -> int:
    """
    Sum amounts recorded as paid across approved proofs (admin-entered on approval).

    Uses approved_paid_amount when stored; otherwise falls back to the outstanding
    balance before each proof minus its remaining balance after approval.

    When deriving amounts from balances, the first partial proof uses the current
    cycle fee (fee_owed) rather than the proof's stale total_amount at upload time.
    Fully settled proofs (balance 0) without approved_paid_amount use total_amount.
    """
    if not approved:
        return 0

    sorted_approved = sorted(approved, key=lambda p: p.submitted_at)
    if all(getattr(p, "approved_paid_amount", None) is not None for p in sorted_approved):
        return sum(p.approved_paid_amount or 0 for p in sorted_approved)

    max_proof_total = max((p.total_amount or 0 for p in sorted_approved), default=0)
    total_paid = 0
    outstanding_before: Optional[int] = None

    for payment in sorted_approved:
        if getattr(payment, "approved_paid_amount", None) is not None:
            total_paid += payment.approved_paid_amount
            outstanding_before = payment.balance_amount
            continue

        if outstanding_before is None:
            if payment.balance_amount is not None and payment.balance_amount > 0:
                outstanding_before = (
                    fee_owed
                    if fee_owed is not None
                    else max(max_proof_total, payment.total_amount or 0)
                )
            else:
                outstanding_before = payment.total_amount or 0
        if payment.balance_amount is None:
            if payment.total_amount is not None:
                total_paid += payment.total_amount
            continue
        paid = outstanding_before - payment.balance_amount
        if paid > 0:
            total_paid += paid
        outstanding_before = payment.balance_amount

    return total_paid


def recalculate_latest_approved_balance(
    cycle: UnitRegistrationCycle,
    approved: list[UnitRegistrationPayment],
) -> None:
    """Set the latest approved proof's balance from fee owed minus total paid."""
    if not approved:
        return

    latest = max(approved, key=lambda p: p.submitted_at)
    fee_owed = cycle.total_fee_at_submit or 0
    total_paid = compute_total_paid_for_approved_payments(approved, fee_owed=fee_owed)
    latest.balance_amount = max(0, fee_owed - total_paid)


def build_payment_summary(
    cycle: UnitRegistrationCycle,
    approved: list[UnitRegistrationPayment],
) -> dict[str, Any]:
    """
    Human-readable registration payment snapshot for admin and unit UIs.

    payment_credit is the amount already paid above the current fee (e.g. after
    admin removals). balance_due is additional fee still owed (e.g. after adds).
    """
    fee_owed = cycle.total_fee_at_submit or 0
    member_count = cycle.member_count_at_submit or 0
    total_paid = compute_total_paid_for_approved_payments(approved, fee_owed=fee_owed)
    balance_due = max(0, fee_owed - total_paid)
    payment_credit = max(0, total_paid - fee_owed)
    return {
        "member_count": member_count,
        "fee_owed": fee_owed,
        "total_paid": total_paid,
        "balance_due": balance_due,
        "payment_credit": payment_credit,
        "is_fully_paid": bool(approved) and balance_due == 0 and total_paid > 0,
    }


def preview_payment_after_member_delta(
    cycle: UnitRegistrationCycle,
    approved: list[UnitRegistrationPayment],
    *,
    delta_members: int,
    member_fee: int,
    unit_fee: int,
) -> dict[str, Any]:
    """Dry-run payment summary after adding or removing members (no DB writes)."""
    current = build_payment_summary(cycle, approved)
    fee_delta = delta_members * member_fee
    new_member_count = max(0, (cycle.member_count_at_submit or 0) + delta_members)
    new_fee = max(
        unit_fee,
        (cycle.total_fee_at_submit or 0) + fee_delta,
    )
    total_paid = current["total_paid"]
    balance_due = max(0, new_fee - total_paid)
    payment_credit = max(0, total_paid - new_fee)
    return {
        "current": current,
        "projected": {
            "member_count": new_member_count,
            "fee_owed": new_fee,
            "total_paid": total_paid,
            "balance_due": balance_due,
            "payment_credit": payment_credit,
            "is_fully_paid": bool(approved) and balance_due == 0 and total_paid > 0,
        },
        "delta_members": delta_members,
        "fee_change": fee_delta,
    }


async def get_registration_payment_summary(
    db: AsyncSession,
    registered_user_id: int,
) -> Optional[dict[str, Any]]:
    """Payment summary for the active registration season, or None if no cycle."""
    current_year = await get_current_registration_year(db)
    cycle = await get_cycle(db, registered_user_id, current_year)
    if cycle is None:
        return None
    if cycle.status not in (DECLARATION_SUBMITTED, REGISTRATION_COMPLETED):
        return None
    if cycle.total_fee_at_submit is None:
        return None

    payments = await _get_cycle_payments(db, cycle.id)
    approved = [p for p in payments if p.status == PaymentProofStatus.APPROVED]
    summary = build_payment_summary(cycle, approved)
    return {
        **summary,
        "registration_year": cycle.registration_year,
        "cycle_id": cycle.id,
        "has_approved_payment": bool(approved),
    }


async def adjust_fee_for_member_delta(
    db: AsyncSession,
    *,
    registered_user_id: int,
    delta_members: int,
) -> bool:
    """
    After a post-declaration member add or admin removal, update the cycle fee
    snapshot and outstanding payment balance for the current season.

    Only applies when declaration has already been submitted. The cycle fee snapshot
    is updated and the latest approved proof's remaining balance is recalculated from
    total paid so far (remove-then-add swaps net to zero correctly).
    """
    if delta_members == 0:
        return False

    current_year = await get_current_registration_year(db)
    cycle = await get_cycle(db, registered_user_id, current_year)
    if cycle is None or cycle.status not in (DECLARATION_SUBMITTED, REGISTRATION_COMPLETED):
        return False

    member_fee = await _get_member_fee(db)
    fee_delta = delta_members * member_fee
    if fee_delta == 0:
        return False

    settings = await get_site_settings(db)
    unit_fee = (
        settings.unit_registration_fee
        if settings and settings.unit_registration_fee is not None
        else 100
    )

    if cycle.member_count_at_submit is not None and cycle.total_fee_at_submit is not None:
        cycle.member_count_at_submit = max(0, cycle.member_count_at_submit + delta_members)
        cycle.total_fee_at_submit = max(
            unit_fee,
            cycle.total_fee_at_submit + fee_delta,
        )
    else:
        member_count_result = await db.execute(
            select(func.count())
            .select_from(UnitMembers)
            .where(UnitMembers.registered_user_id == registered_user_id)
        )
        member_count = member_count_result.scalar() or 0
        cycle.member_count_at_submit = member_count
        cycle.total_fee_at_submit = unit_fee + (member_count * member_fee)

    payments = await _get_cycle_payments(db, cycle.id)
    approved = [p for p in payments if p.status == PaymentProofStatus.APPROVED]
    if approved:
        recalculate_latest_approved_balance(cycle, approved)
    elif payments:
        latest = payments[-1]
        if latest.status == PaymentProofStatus.PENDING:
            if fee_delta < 0:
                latest.status = PaymentProofStatus.REJECTED
                latest.rejection_note = (
                    "Registration fee was revised after member update. "
                    "Please submit a new proof for the updated amount."
                )
                latest.reviewed_at = now_ist()
            elif latest.total_amount is not None:
                latest.total_amount = max(unit_fee, latest.total_amount + fee_delta)

    return True
