# Units Summary Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-unit "Units Summary" CSV export (Unit ID, Unit Name, Clergy District,
Registration Year, Registration Status, Payment Status, Total Members, Female Members,
Male Members), selectable by registration year, exposed on both the Export Data page
(`#/admin/export`) and the existing ViewAllUnits export button.

**Architecture:** Backend gains a new focused module with pure, unit-tested mapping/derivation
functions plus one async DB-loading function, wired into the existing (currently Excel,
current-year-only) `GET /admin/units/export/units` branch — now CSV, year-parameterized.
Frontend's shared `exportData` helper gains an optional `registrationYear` param and safer
query-string building; two pages get a Registration Year `<select>` next to their export
button.

**Tech Stack:** FastAPI + SQLAlchemy async (backend, `csi-project-be`), React + TypeScript +
Vite (frontend, `csi-webapp-fe`). Backend tests: pytest, plain function tests (no DB
fixtures exist in this repo — see Global Constraints). Frontend: no test runner is configured
in this repo; frontend tasks are verified by manual smoke test, not automated tests.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-10-units-summary-export-design.md` (this repo).
- Backend repo root: `/home/alex/Downloads/CSI/csi-project-be`. Frontend repo root:
  `/home/alex/Downloads/CSI/csi-webapp-fe`. These are two separate git repositories —
  commit separately in each.
- Do not change any `export_type` branch other than `"units"` in `export_unit_data`
  (`app/admin/routers/units.py`).
- Do not change `list_all_units` (`app/admin/routers/units.py:318`) — it's a separate,
  still-used listing endpoint; only its internal call from `export_unit_data` is removed.
- `UnitMembers` has no per-season versioning (`added_registration_cycle_id` is set once at
  add-time and never updated) — total/female/male member counts must NOT be filtered by
  registration year; they reflect the unit's current roster regardless of which year is
  exported. Only the registration/payment status fields are year-scoped.
- No DB test fixtures exist in this repo (`tests/*.py` are all plain pytest functions with
  `SimpleNamespace` stand-ins, no async DB session fixture). Follow that convention: write
  real unit tests for pure functions; the async DB-loader function itself is not unit tested,
  matching the existing untested `_load_members_for_export`/`_load_officials_for_export`
  siblings.
- No frontend test runner is configured (`package.json` has no `test` script, no
  jest/vitest config). Frontend tasks end in a manual smoke-test step, not an automated one.

---

### Task 1: Backend pure functions — status mapping and payment-status derivation

**Files:**
- Create: `app/admin/units_summary_export.py`
- Test: `tests/test_units_summary_export.py`

**Interfaces:**
- Produces: `map_registration_status_for_display(raw_status: Optional[str]) -> str`,
  `map_payment_status_for_display(raw_status: str) -> str`,
  `derive_unit_payment_status(has_cycle: bool, sorted_payments: List[UnitRegistrationPayment]) -> str`
  (returns one of `"not_submitted"`, `"pending"`, `"partial"`, `"approved"`, `"rejected"`).
  Task 2 imports all three from `app.admin.units_summary_export`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_units_summary_export.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/pytest tests/test_units_summary_export.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'app.admin.units_summary_export'`

- [ ] **Step 3: Write the minimal implementation**

Create `app/admin/units_summary_export.py`:

```python
"""Pure logic and DB loader for the units-summary CSV export."""

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/pytest tests/test_units_summary_export.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/alex/Downloads/CSI/csi-project-be
git add app/admin/units_summary_export.py tests/test_units_summary_export.py
git commit -m "Add pure status-mapping helpers for units summary export"
```

---

### Task 2: Backend DB loader — `load_units_summary_for_export`

**Files:**
- Modify: `app/admin/units_summary_export.py` (add to the file created in Task 1)

**Interfaces:**
- Consumes: `map_registration_status_for_display`, `map_payment_status_for_display`,
  `derive_unit_payment_status` (Task 1, same file).
- Produces: `async def load_units_summary_for_export(db: AsyncSession, *, registration_year: int, exclude_user_id: int) -> List[Dict[str, Any]]`,
  returning dicts shaped `{"unit_id", "unit_name", "clergy_district", "registration_year",
  "registration_status", "payment_status", "total_members", "female_members", "male_members"}`.
  Task 4 imports and calls this.

This function does async DB queries and has no unit test in this repo (see Global
Constraints) — implement it carefully and verify via the manual smoke test in Task 4.

- [ ] **Step 1: Add the loader function**

Append to `app/admin/units_summary_export.py`:

```python
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
```

- [ ] **Step 2: Sanity-check imports resolve**

Run: `cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/python -c "import app.admin.units_summary_export"`
Expected: no import error

- [ ] **Step 3: Commit**

```bash
cd /home/alex/Downloads/CSI/csi-project-be
git add app/admin/units_summary_export.py
git commit -m "Add DB loader for units summary export rows"
```

---

### Task 3: Backend CSV exporter functions

**Files:**
- Modify: `app/common/exporter.py`
- Modify: `tests/test_units_summary_export.py` (add exporter tests to the file from Task 1)

**Interfaces:**
- Produces: `UNITS_SUMMARY_EXPORT_HEADERS: List[str]`, `create_units_summary_csv(rows: List[Dict[str, Any]]) -> BytesIO`.
  Task 4 imports `create_units_summary_csv` from `app.common.exporter`.
- Also removes `create_units_excel` (`app/common/exporter.py:195-215`), which becomes
  unused once Task 4 lands (its only caller is the branch being replaced — confirmed via
  `grep -rn "create_units_excel" app/`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_units_summary_export.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/pytest tests/test_units_summary_export.py -v`
Expected: FAIL/ERROR — `ImportError: cannot import name 'UNITS_SUMMARY_EXPORT_HEADERS'`

- [ ] **Step 3: Add the exporter functions, remove `create_units_excel`**

In `app/common/exporter.py`, replace the `create_units_excel` function (lines 195-215):

```python
def create_units_excel(units_data: List[Dict[str, Any]]) -> BytesIO:
    """Create Excel file for registered units."""
    headers = [
        "Unit Number",
        "Unit Name",
        "District",
        "Member Count",
        "Registration Status",
        "Payment Status",
    ]
    rows = []
    for unit in units_data:
        rows.append([
            unit.get("username", ""),
            unit.get("unit_name", ""),
            unit.get("district", ""),
            unit.get("member_count", ""),
            unit.get("status", ""),
            unit.get("payment_status", ""),
        ])
    return create_styled_excel(headers, rows, "Units")
```

with:

```python
UNITS_SUMMARY_EXPORT_HEADERS = [
    "Unit ID",
    "Unit Name",
    "Clergy District",
    "Registration Year",
    "Registration Status",
    "Payment Status",
    "Total Members",
    "Female Members",
    "Male Members",
]


def _units_summary_export_rows(rows: List[Dict[str, Any]]) -> List[List[Any]]:
    result: List[List[Any]] = []
    for row in rows:
        result.append([
            row.get("unit_id", ""),
            row.get("unit_name", ""),
            row.get("clergy_district", ""),
            row.get("registration_year", ""),
            row.get("registration_status", ""),
            row.get("payment_status", ""),
            row.get("total_members", 0),
            row.get("female_members", 0),
            row.get("male_members", 0),
        ])
    return result


def create_units_summary_csv(rows: List[Dict[str, Any]]) -> BytesIO:
    """Create CSV file for the per-unit registration/payment/membership summary."""
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(UNITS_SUMMARY_EXPORT_HEADERS)
    writer.writerows(_units_summary_export_rows(rows))
    csv_bytes = BytesIO(buffer.getvalue().encode("utf-8-sig"))
    csv_bytes.seek(0)
    return csv_bytes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/pytest tests/test_units_summary_export.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/alex/Downloads/CSI/csi-project-be
git add app/common/exporter.py tests/test_units_summary_export.py
git commit -m "Replace create_units_excel with units-summary CSV exporter"
```

---

### Task 4: Wire the new export into `export_unit_data`

**Files:**
- Modify: `app/admin/routers/units.py:2214-2308` (the `export_unit_data` route and its
  imports at lines 58-67)

**Interfaces:**
- Consumes: `load_units_summary_for_export` (Task 2), `create_units_summary_csv` (Task 3),
  `cycle_service.get_current_registration_year` (already imported in this file as
  `cycle_service`, defined `app/units/registration_cycle_service.py:91`).

- [ ] **Step 1: Update the exporter import block**

In `app/admin/routers/units.py`, replace lines 58-67:

```python
from app.common.exporter import (
    create_archived_members_csv,
    create_archived_members_excel,
    create_councilors_excel,
    create_district_payment_summary_excel,
    create_members_excel,
    create_officials_excel,
    create_registration_payments_csv,
    create_units_excel,
)
```

with:

```python
from app.common.exporter import (
    create_archived_members_csv,
    create_archived_members_excel,
    create_councilors_excel,
    create_district_payment_summary_excel,
    create_members_excel,
    create_officials_excel,
    create_registration_payments_csv,
    create_units_summary_csv,
)
from app.admin.units_summary_export import load_units_summary_for_export
```

- [ ] **Step 2: Add the `registration_year` query param and rework the `"units"` branch**

Replace the function signature (originally):

```python
@router.get("/export/{export_type}")
async def export_unit_data(
    export_type: str,
    id: Optional[int] = Query(None, description="Unit user id or district id depending on export type"),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Export unit, member, official, or councilor data to Excel."""
    export_type = export_type.strip().lower()
    timestamp = format_timestamp_ist()
```

with:

```python
@router.get("/export/{export_type}")
async def export_unit_data(
    export_type: str,
    id: Optional[int] = Query(None, description="Unit user id or district id depending on export type"),
    registration_year: Optional[int] = Query(
        None, description="Registration year to scope the 'units' export type to (defaults to current year)"
    ),
    current_user: CustomUser = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Export unit, member, official, or councilor data."""
    export_type = export_type.strip().lower()
    timestamp = format_timestamp_ist()
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
```

Replace the `"units"` branch (originally lines 2267-2297):

```python
    elif export_type == "units":
        units = await list_all_units(current_user=current_user, db=db)
        district_by_unit: dict[str, str] = {}
        unit_name_ids = [
            unit["user_id"]
            for unit in units
            if unit.get("user_id")
        ]
        if unit_name_ids:
            user_rows = await db.execute(
                select(CustomUser.id, UnitName.name, ClergyDistrict.name)
                .join(UnitName, UnitName.id == CustomUser.unit_name_id)
                .join(ClergyDistrict, ClergyDistrict.id == UnitName.clergy_district_id)
                .where(CustomUser.id.in_(unit_name_ids))
            )
            for user_id, unit_name, district_name in user_rows.all():
                district_by_unit[str(user_id)] = district_name or ""

        rows = [
            {
                "username": unit.get("username", ""),
                "unit_name": unit.get("unit_name", ""),
                "district": district_by_unit.get(str(unit.get("user_id", "")), ""),
                "member_count": unit.get("member_count", 0),
                "status": unit.get("status", ""),
                "payment_status": unit.get("payment_status", ""),
            }
            for unit in units
        ]
        export_file = create_units_excel(rows)
        filename = f"units_{timestamp}.xlsx"
```

with:

```python
    elif export_type == "units":
        year = registration_year or await cycle_service.get_current_registration_year(db)
        rows = await load_units_summary_for_export(
            db, registration_year=year, exclude_user_id=current_user.id
        )
        export_file = create_units_summary_csv(rows)
        filename = f"units_summary_{year}_{timestamp}.csv"
        media_type = "text/csv"
```

- [ ] **Step 3: Update the final response to use the `media_type` variable**

Replace (originally lines 2304-2308):

```python
    return StreamingResponse(
        export_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

with:

```python
    return StreamingResponse(
        export_file,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 4: Run the full backend test suite**

Run: `cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/pytest -v`
Expected: all tests PASS (including Task 1/3's new tests and pre-existing tests)

- [ ] **Step 5: Manual smoke test**

Run: `cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/uvicorn main:app --reload`

Then, with a valid admin bearer token:
```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/admin/units/export/units?registration_year=2026" -o /tmp/units_summary.csv
head -3 /tmp/units_summary.csv
```
Expected: CSV with header row `Unit ID,Unit Name,Clergy District,Registration Year,Registration Status,Payment Status,Total Members,Female Members,Male Members`
followed by one data row per unit.

Also verify omitting `registration_year` still returns a valid CSV (defaults to current year):
```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/admin/units/export/units" -o /tmp/units_summary_default.csv
head -3 /tmp/units_summary_default.csv
```

- [ ] **Step 6: Commit**

```bash
cd /home/alex/Downloads/CSI/csi-project-be
git add app/admin/routers/units.py
git commit -m "Switch units export to year-scoped CSV summary with gender breakdown"
```

---

### Task 5: Frontend — `api.exportData` gains `registrationYear` and safer query building

**Files:**
- Modify: `csi-webapp-fe/services/api.ts:2007-2017`

**Interfaces:**
- Produces: `exportData(type: string, id?: number, registrationYear?: number): Promise<ApiResponse<Blob>>`.
  Tasks 6 and 7 call this with a `registrationYear` argument.

No automated test exists for this file (no test runner in this repo — see Global
Constraints); this task's correctness is verified in Task 6/7's manual smoke tests.

- [ ] **Step 1: Update `exportData`**

In `csi-webapp-fe/services/api.ts`, replace lines 2007-2017:

```ts
  // GET /admin/units/export/{export_type} - Export various unit data
  async exportData(type: string, id?: number): Promise<ApiResponse<Blob>> {
    const token = this.getToken();
    if (!token) throw new Error('Authentication required');
    let endpoint = `/admin/units/export/${type}`;
    if (id) endpoint += `?id=${id}`;
    const blob = await httpGet<Blob>(endpoint, { token, asBlob: true });
    const filename = id ? `${type}_${id}.xlsx` : `${type}.xlsx`;
    downloadBlob(blob, filename);
    return { data: blob, message: 'Data exported successfully', status: 200 };
  }
```

with:

```ts
  // GET /admin/units/export/{export_type} - Export various unit data
  async exportData(
    type: string,
    id?: number,
    registrationYear?: number,
  ): Promise<ApiResponse<Blob>> {
    const token = this.getToken();
    if (!token) throw new Error('Authentication required');
    const endpoint = `/admin/units/export/${type}`;
    const query: Record<string, string | number> = {};
    if (id) query.id = id;
    if (registrationYear) query.registration_year = registrationYear;
    const blob = await httpGet<Blob>(endpoint, {
      token,
      query: Object.keys(query).length ? query : undefined,
      asBlob: true,
    });
    const extension = type === 'units' ? 'csv' : 'xlsx';
    const filenameParts = [type, id, registrationYear].filter((part) => part !== undefined);
    const filename = `${filenameParts.join('_')}.${extension}`;
    downloadBlob(blob, filename);
    return { data: blob, message: 'Data exported successfully', status: 200 };
  }
```

- [ ] **Step 2: Commit**

```bash
cd /home/alex/Downloads/CSI/csi-webapp-fe
git add services/api.ts
git commit -m "Support registration_year param and CSV filenames in exportData"
```

---

### Task 6: Frontend — "Export Units Summary" card on `#/admin/export`

**Files:**
- Modify: `csi-webapp-fe/pages/UnitAdmin/ExportData.tsx`

**Interfaces:**
- Consumes: `api.exportData('units', undefined, summaryYear)` (Task 5). Reuses the
  existing `yearOptions` memo already at component scope (`ExportData.tsx:37-43`) and the
  existing `selectClassName` constant (`ExportData.tsx:14-15`).

- [ ] **Step 1: Add state and handler**

In `ExportData.tsx`, after the existing state declarations (after line 50,
`const [exportingSummary, setExportingSummary] = useState(false);`), add:

```tsx
  const [summaryYear, setSummaryYear] = useState<number>(activeRegistrationYear);
  const [exportingUnitsSummary, setExportingUnitsSummary] = useState(false);
```

After the existing `useEffect` that resets `paymentYear` (after line 54's closing `}, [activeRegistrationYear]);`), add:

```tsx
  useEffect(() => {
    setSummaryYear(activeRegistrationYear);
  }, [activeRegistrationYear]);
```

After `handleUnitCouncilorsExport` (after line 119's closing brace), add:

```tsx
  const handleUnitsSummaryExport = async () => {
    setExportingUnitsSummary(true);
    try {
      await api.exportData('units', undefined, summaryYear);
      addToast('Units summary exported successfully', 'success');
    } catch {
      addToast('Failed to export units summary', 'error');
    } finally {
      setExportingUnitsSummary(false);
    }
  };
```

- [ ] **Step 2: Add the card JSX**

In the "Unit Data" section, immediately after the closing `</Card>` of "Export Unit-wise
Data" (after line 317) and before the closing `</div>` of that section (line 318), add:

```tsx
        <Card>
          <h3 className="text-lg font-bold text-textDark mb-1">Export Units Summary</h3>
          <p className="text-sm text-textMuted mb-4">
            One row per unit for the selected registration year, with membership and gender
            breakdown. Membership counts reflect each unit's current roster regardless of
            the year selected.
          </p>
          <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-end">
            <div className="w-full sm:w-48">
              <p className="text-sm font-medium text-textMuted mb-2">Registration Year</p>
              <select
                className={selectClassName}
                value={summaryYear}
                onChange={(e) => setSummaryYear(Number(e.target.value))}
              >
                {yearOptions.map((year) => (
                  <option key={year} value={year}>
                    {year}
                  </option>
                ))}
              </select>
            </div>
            <Button
              variant="primary"
              size="sm"
              onClick={handleUnitsSummaryExport}
              disabled={exportingUnitsSummary}
            >
              <Download className="w-4 h-4 mr-2" />
              {exportingUnitsSummary ? 'Exporting...' : 'Export (CSV)'}
            </Button>
          </div>
        </Card>
```

- [ ] **Step 3: Manual smoke test**

Run: `cd /home/alex/Downloads/CSI/csi-webapp-fe && npm run dev`

In the browser, navigate to `#/admin/export`, select a registration year in the new
"Export Units Summary" card, click "Export (CSV)", and confirm a `.csv` file downloads
and opens with the 9 expected columns and one row per unit. Repeat with a different year
from the dropdown and confirm the `Registration Year` column in the downloaded file
matches the selection.

- [ ] **Step 4: Commit**

```bash
cd /home/alex/Downloads/CSI/csi-webapp-fe
git add pages/UnitAdmin/ExportData.tsx
git commit -m "Add Export Units Summary card to admin export page"
```

---

### Task 7: Frontend — Registration Year selector on ViewAllUnits export button

**Files:**
- Modify: `csi-webapp-fe/pages/UnitAdmin/ViewAllUnits.tsx`

**Interfaces:**
- Consumes: `api.exportData('units', undefined, exportYear)` (Task 5).

- [ ] **Step 1: Update imports**

Replace lines 1 and 11:

```tsx
import React, { useMemo, useState, useCallback } from 'react';
```

```tsx
import { useUnits, useCompleteUnitRegistration } from '../../hooks/queries';
```

with:

```tsx
import React, { useEffect, useMemo, useState, useCallback } from 'react';
```

```tsx
import { useUnits, useCompleteUnitRegistration, useSiteSettings } from '../../hooks/queries';
import { getCurrentYearIST } from '../../utils/datetime';
```

- [ ] **Step 2: Add a local `selectClassName` constant**

After the `PAYMENT_STATUS_LABELS` constant (after line 33), add:

```tsx
const selectClassName =
  'px-3 py-2 border border-borderColor rounded-md bg-white text-textDark focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary';
```

- [ ] **Step 3: Add year state and update the export handler**

Replace lines 40-50:

```tsx
  const { data: units = [], isLoading: loading } = useUnits();
  const completeRegistration = useCompleteUnitRegistration();

  const handleExport = async () => {
    try {
      await api.exportData('units');
      addToast('Units data exported successfully', 'success');
    } catch {
      addToast('Failed to export data', 'error');
    }
  };
```

with:

```tsx
  const { data: units = [], isLoading: loading } = useUnits();
  const completeRegistration = useCompleteUnitRegistration();
  const { data: siteSettings } = useSiteSettings();

  const activeRegistrationYear = siteSettings?.current_registration_year ?? getCurrentYearIST();
  const yearOptions = useMemo(() => {
    const years = new Set<number>([activeRegistrationYear]);
    for (let offset = 1; offset <= 3; offset += 1) {
      years.add(activeRegistrationYear - offset);
    }
    return Array.from(years).sort((a, b) => b - a);
  }, [activeRegistrationYear]);
  const [exportYear, setExportYear] = useState<number>(activeRegistrationYear);

  useEffect(() => {
    setExportYear(activeRegistrationYear);
  }, [activeRegistrationYear]);

  const handleExport = async () => {
    try {
      await api.exportData('units', undefined, exportYear);
      addToast('Units data exported successfully', 'success');
    } catch {
      addToast('Failed to export data', 'error');
    }
  };
```

- [ ] **Step 4: Update the export button JSX**

Replace lines 192-197:

```tsx
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" size="sm" onClick={handleExport}>
            <Download className="w-4 h-4 mr-2" />
            Export to Excel
          </Button>
        </div>
```

with:

```tsx
        <div className="flex flex-wrap items-center gap-2">
          <select
            className={selectClassName}
            value={exportYear}
            onChange={(e) => setExportYear(Number(e.target.value))}
          >
            {yearOptions.map((year) => (
              <option key={year} value={year}>
                {year}
              </option>
            ))}
          </select>
          <Button variant="primary" size="sm" onClick={handleExport}>
            <Download className="w-4 h-4 mr-2" />
            Export Units Summary (CSV)
          </Button>
        </div>
```

- [ ] **Step 5: Manual smoke test**

With the dev server still running (`npm run dev`), navigate to the All Units page,
select a registration year from the new dropdown, click "Export Units Summary (CSV)",
and confirm a `.csv` downloads with the same 9 columns as Task 6, matching the selected
year. Confirm the page's existing unit table and "Mark as Completed" flow still work
unchanged.

- [ ] **Step 6: Commit**

```bash
cd /home/alex/Downloads/CSI/csi-webapp-fe
git add pages/UnitAdmin/ViewAllUnits.tsx
git commit -m "Add registration year selector to ViewAllUnits export button"
```
