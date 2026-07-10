"""Pure logic for the units-summary CSV export."""

from typing import Any, Dict, List, Optional

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.models import CustomUser, UnitMembers, UnitName, UnitRegistrationData
from app.units.models import PaymentProofStatus, UnitRegistrationCycle, UnitRegistrationPayment


def map_registration_status_for_display(raw_status: Optional[str]) -> str:
    """Collapse raw UnitRegistrationCycle.status values to the four display statuses
    already used by the frontend's mapUnitStatus (csi-webapp-fe/services/api.ts)."""
    if raw_status == "Registration Completed":
        return "Completed"
    if raw_status == "Declaration Submitted":
        return "Awaiting Completion"
    if raw_status in ("Not Started", "Not Registered", None):
        return "Not Started"
    return "In Progress"


_PAYMENT_STATUS_DISPLAY_LABELS: Dict[str, str] = {
    "not_submitted": "Not submitted",
    "pending": "Pending review",
    "partial": "Partially paid",
    "approved": "Fully paid",
    "rejected": "Rejected",
}


def map_payment_status_for_display(raw_status: str) -> str:
    return _PAYMENT_STATUS_DISPLAY_LABELS.get(raw_status, raw_status)


def derive_unit_payment_status(
    has_cycle: bool,
    sorted_payments: List[UnitRegistrationPayment],
) -> str:
    """Mirror list_all_units's per-unit payment status derivation
    (app/admin/routers/units.py:361-400). `sorted_payments` must already be sorted
    ascending by submitted_at and scoped to the relevant registration cycle."""
    if not has_cycle or not sorted_payments:
        return "not_submitted"
    approved_payments = [p for p in sorted_payments if p.status == PaymentProofStatus.APPROVED]
    if approved_payments:
        latest_approved = approved_payments[-1]
        if latest_approved.balance_amount is not None and latest_approved.balance_amount > 0:
            return "partial"
        return "approved"
    if sorted_payments[-1].status == PaymentProofStatus.REJECTED:
        return "rejected"
    return "pending"


async def load_units_summary_for_export(
    db: AsyncSession,
    *,
    registration_year: int,
    exclude_user_id: int,
) -> List[Dict[str, Any]]:
    stmt = (
        select(UnitRegistrationData)
        .where(UnitRegistrationData.registered_user_id != exclude_user_id)
        .options(
            selectinload(UnitRegistrationData.registered_user)
            .selectinload(CustomUser.unit_name)
            .selectinload(UnitName.district)
        )
    )
    result = await db.execute(stmt)
    units_data = list(result.scalars().all())

    user_ids = [u.registered_user_id for u in units_data]
    if not user_ids:
        return []

    cycles_by_user: Dict[int, UnitRegistrationCycle] = {}
    cycles_result = await db.execute(
        select(UnitRegistrationCycle).where(
            UnitRegistrationCycle.registered_user_id.in_(user_ids),
            UnitRegistrationCycle.registration_year == registration_year,
        )
    )
    for cycle in cycles_result.scalars().all():
        cycles_by_user[cycle.registered_user_id] = cycle

    payments_result = await db.execute(
        select(UnitRegistrationPayment).where(
            UnitRegistrationPayment.registered_user_id.in_(user_ids),
        )
    )
    payments_by_user: Dict[int, List[UnitRegistrationPayment]] = {}
    for payment in payments_result.scalars().all():
        payments_by_user.setdefault(payment.registered_user_id, []).append(payment)

    # Member/gender counts are intentionally NOT year-filtered — UnitMembers has no
    # per-season versioning (added_registration_cycle_id is stamped once at add-time),
    # so this matches list_all_units's existing unfiltered member_count behavior.
    male_gender = or_(UnitMembers.gender == "M", UnitMembers.gender == "Male")
    female_gender = or_(UnitMembers.gender == "F", UnitMembers.gender == "Female")
    member_counts_result = await db.execute(
        select(
            UnitMembers.registered_user_id,
            func.count(UnitMembers.id).label("total"),
            func.count(case((female_gender, 1))).label("female"),
            func.count(case((male_gender, 1))).label("male"),
        )
        .where(UnitMembers.registered_user_id.in_(user_ids))
        .group_by(UnitMembers.registered_user_id)
    )
    member_counts_by_user: Dict[int, Dict[str, int]] = {
        row.registered_user_id: {
            "total": row.total or 0,
            "female": row.female or 0,
            "male": row.male or 0,
        }
        for row in member_counts_result.all()
    }

    rows: List[Dict[str, Any]] = []
    for unit_data in units_data:
        user_id = unit_data.registered_user_id
        user = unit_data.registered_user
        cycle = cycles_by_user.get(user_id)
        unit_payments = sorted(
            [
                p
                for p in payments_by_user.get(user_id, [])
                if cycle and p.registration_cycle_id == cycle.id
            ],
            key=lambda p: p.submitted_at,
        )
        raw_registration_status = cycle.status if cycle else "Not Started"
        raw_payment_status = derive_unit_payment_status(cycle is not None, unit_payments)
        counts = member_counts_by_user.get(user_id, {"total": 0, "female": 0, "male": 0})
        unit_name = user.unit_name if user else None

        rows.append(
            {
                "unit_id": unit_name.id if unit_name else None,
                "unit_name": unit_name.name if unit_name else "",
                "clergy_district": unit_name.district.name if unit_name and unit_name.district else "",
                "registration_year": registration_year,
                "registration_status": map_registration_status_for_display(raw_registration_status),
                "payment_status": map_payment_status_for_display(raw_payment_status),
                "total_members": counts["total"],
                "female_members": counts["female"],
                "male_members": counts["male"],
            }
        )
    return rows
