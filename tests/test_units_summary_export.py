"""Tests for units-summary export pure logic (status mapping, payment derivation)."""

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.units.models import PaymentProofStatus  # noqa: E402
from app.admin.units_summary_export import (  # noqa: E402
    map_registration_status_for_display,
    map_payment_status_for_display,
    derive_unit_payment_status,
)


def _payment(proof_status, balance_amount, submitted_at):
    return SimpleNamespace(status=proof_status, balance_amount=balance_amount, submitted_at=submitted_at)


def test_map_registration_status_for_display_completed():
    assert map_registration_status_for_display("Registration Completed") == "Completed"


def test_map_registration_status_for_display_declaration_submitted():
    assert map_registration_status_for_display("Declaration Submitted") == "Awaiting Completion"


def test_map_registration_status_for_display_not_started():
    assert map_registration_status_for_display("Not Started") == "Not Started"
    assert map_registration_status_for_display(None) == "Not Started"


def test_map_registration_status_for_display_intermediate_states_collapse_to_in_progress():
    assert map_registration_status_for_display("Registration Started") == "In Progress"
    assert map_registration_status_for_display("Unit Officials Completed") == "In Progress"
    assert map_registration_status_for_display("Unit Councilors Completed") == "In Progress"


def test_map_payment_status_for_display_known_values():
    assert map_payment_status_for_display("not_submitted") == "Not submitted"
    assert map_payment_status_for_display("pending") == "Pending review"
    assert map_payment_status_for_display("partial") == "Partially paid"
    assert map_payment_status_for_display("approved") == "Fully paid"
    assert map_payment_status_for_display("rejected") == "Rejected"


def test_derive_unit_payment_status_no_cycle():
    assert derive_unit_payment_status(False, []) == "not_submitted"


def test_derive_unit_payment_status_cycle_no_payments():
    assert derive_unit_payment_status(True, []) == "not_submitted"


def test_derive_unit_payment_status_approved_fully_paid():
    payments = [_payment(PaymentProofStatus.APPROVED, 0, datetime(2026, 6, 1))]
    assert derive_unit_payment_status(True, payments) == "approved"


def test_derive_unit_payment_status_approved_with_balance_is_partial():
    payments = [_payment(PaymentProofStatus.APPROVED, 150, datetime(2026, 6, 1))]
    assert derive_unit_payment_status(True, payments) == "partial"


def test_derive_unit_payment_status_last_rejected_no_approved():
    payments = [_payment(PaymentProofStatus.REJECTED, None, datetime(2026, 6, 1))]
    assert derive_unit_payment_status(True, payments) == "rejected"


def test_derive_unit_payment_status_pending_when_awaiting_review():
    payments = [_payment(PaymentProofStatus.PENDING, None, datetime(2026, 6, 1))]
    assert derive_unit_payment_status(True, payments) == "pending"


def test_derive_unit_payment_status_uses_latest_approved_by_submission_order():
    payments = [
        _payment(PaymentProofStatus.APPROVED, 100, datetime(2026, 6, 1)),
        _payment(PaymentProofStatus.APPROVED, 0, datetime(2026, 6, 2)),
    ]
    assert derive_unit_payment_status(True, payments) == "approved"


from app.common.exporter import (  # noqa: E402
    UNITS_SUMMARY_EXPORT_HEADERS,
    _units_summary_export_rows,
    create_units_summary_csv,
)


def test_units_summary_export_rows_maps_headers_in_order():
    rows = [{
        "unit_id": 12,
        "unit_name": "Zion Unit",
        "clergy_district": "Kottayam",
        "registration_year": 2026,
        "registration_status": "Completed",
        "payment_status": "Fully paid",
        "total_members": 42,
        "female_members": 20,
        "male_members": 22,
    }]
    result = _units_summary_export_rows(rows)
    assert result == [[12, "Zion Unit", "Kottayam", 2026, "Completed", "Fully paid", 42, 20, 22]]


def test_units_summary_export_rows_defaults_missing_fields():
    result = _units_summary_export_rows([{}])
    assert result == [["", "", "", "", "", "", 0, 0, 0]]


def test_create_units_summary_csv_contains_header_and_row():
    rows = [{
        "unit_id": 1,
        "unit_name": "Test Unit",
        "clergy_district": "District A",
        "registration_year": 2026,
        "registration_status": "In Progress",
        "payment_status": "Not submitted",
        "total_members": 5,
        "female_members": 2,
        "male_members": 3,
    }]
    csv_bytes = create_units_summary_csv(rows)
    content = csv_bytes.read().decode("utf-8-sig")
    assert ",".join(UNITS_SUMMARY_EXPORT_HEADERS) in content
    assert "1,Test Unit,District A,2026,In Progress,Not submitted,5,2,3" in content
