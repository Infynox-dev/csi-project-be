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
        "role": "President",
        "name": "Anna",
        "phone": "9999999999",
    }]
    result = _units_summary_export_rows(rows)
    assert result == [[12, "Zion Unit", "Kottayam", 2026, "Completed", "Fully paid", 42, 20, 22,
                        "President", "Anna", "9999999999"]]


def test_units_summary_export_rows_defaults_missing_fields():
    result = _units_summary_export_rows([{}])
    assert result == [["", "", "", "", "", "", 0, 0, 0, "", "", ""]]


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
        "role": "Councilor",
        "name": "Test Person",
        "phone": "8888888888",
    }]
    csv_bytes = create_units_summary_csv(rows)
    content = csv_bytes.read().decode("utf-8-sig")
    assert ",".join(UNITS_SUMMARY_EXPORT_HEADERS) in content
    assert "1,Test Unit,District A,2026,In Progress,Not submitted,5,2,3,Councilor,Test Person,8888888888" in content


from types import SimpleNamespace as _SimpleNamespace  # noqa: E402

from app.admin.units_summary_export import (  # noqa: E402
    build_official_rows,
    build_councilor_rows,
)


def _officials(**overrides):
    fields = {
        "president_name": "",
        "president_phone": "",
        "vice_president_name": "",
        "vice_president_phone": "",
        "secretary_name": "",
        "secretary_phone": "",
        "joint_secretary_name": "",
        "joint_secretary_phone": "",
        "treasurer_name": "",
        "treasurer_phone": "",
    }
    fields.update(overrides)
    return _SimpleNamespace(**fields)


def test_build_official_rows_sorted_alphabetically_by_name():
    officials = _officials(
        president_name="Zachariah", president_phone="111",
        vice_president_name="Anna", vice_president_phone="222",
        secretary_name="Mathew", secretary_phone="333",
        joint_secretary_name="Beena", joint_secretary_phone="444",
        treasurer_name="Thomas", treasurer_phone="555",
    )
    rows = build_official_rows(officials)
    assert [r["name"] for r in rows] == ["Anna", "Beena", "Mathew", "Thomas", "Zachariah"]
    assert rows[0] == {"role": "Vice President", "name": "Anna", "phone": "222"}


def test_build_official_rows_none_officials_returns_five_blank_rows_in_role_order():
    rows = build_official_rows(None)
    assert len(rows) == 5
    assert all(r["name"] == "" and r["phone"] == "" for r in rows)
    assert [r["role"] for r in rows] == [
        "President", "Vice President", "Secretary", "Joint Secretary", "Treasurer",
    ]


def test_build_official_rows_partial_blanks_sort_before_named():
    officials = _officials(president_name="Zed", secretary_name="Amy")
    rows = build_official_rows(officials)
    assert rows[0]["name"] == ""
    assert [r["name"] for r in rows[-2:]] == ["Amy", "Zed"]


def test_build_councilor_rows_sorted_alphabetically_by_name():
    councilors = [
        _SimpleNamespace(unit_member=_SimpleNamespace(name="Zoe", number="1")),
        _SimpleNamespace(unit_member=_SimpleNamespace(name="Alan", number="2")),
    ]
    rows = build_councilor_rows(councilors)
    assert rows == [
        {"role": "Councilor", "name": "Alan", "phone": "2"},
        {"role": "Councilor", "name": "Zoe", "phone": "1"},
    ]


def test_build_councilor_rows_empty_list_returns_empty():
    assert build_councilor_rows([]) == []


def test_build_councilor_rows_missing_unit_member_defaults_blank():
    councilors = [_SimpleNamespace(unit_member=None)]
    assert build_councilor_rows(councilors) == [{"role": "Councilor", "name": "", "phone": ""}]


def test_units_summary_export_rows_includes_role_name_phone_columns():
    rows = [{
        "unit_id": 1,
        "unit_name": "Zion Unit",
        "clergy_district": "Kottayam",
        "registration_year": 2026,
        "registration_status": "Completed",
        "payment_status": "Fully paid",
        "total_members": 10,
        "female_members": 5,
        "male_members": 5,
        "role": "President",
        "name": "Anna Thomas",
        "phone": "9999999999",
    }]
    result = _units_summary_export_rows(rows)
    assert result == [[1, "Zion Unit", "Kottayam", 2026, "Completed", "Fully paid", 10, 5, 5,
                        "President", "Anna Thomas", "9999999999"]]


def test_units_summary_export_rows_defaults_role_name_phone_when_missing():
    result = _units_summary_export_rows([{}])
    assert result[0][-3:] == ["", "", ""]
