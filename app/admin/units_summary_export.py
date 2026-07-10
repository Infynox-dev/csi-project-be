"""Pure logic for the units-summary CSV export."""

from typing import Dict, List, Optional

from app.units.models import PaymentProofStatus, UnitRegistrationPayment


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
