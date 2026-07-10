# Units Summary Export: Officials & Councilors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the already-shipped Units Summary CSV export from one row per unit to one
row per person: each unit's 5 fixed Officials roles (sorted alphabetically by name) followed
by its Councilors (sorted alphabetically by name), with units themselves ordered
alphabetically by Unit Name.

**Architecture:** A new pure module extends `app/admin/units_summary_export.py` with two
pure row-builder functions (`build_official_rows`, `build_councilor_rows`) that are
independently unit-tested without a DB. `load_units_summary_for_export` gains two new bulk
queries (`UnitOfficials`, `UnitCouncilor`) and changes its return shape from one dict per
unit to a flattened list of person-row dicts. The CSV exporter gains 3 columns. No route or
frontend changes are needed — both already call this endpoint agnostic of column/row shape.

**Tech Stack:** Same as the base plan — FastAPI + SQLAlchemy async, pytest with
`SimpleNamespace` stand-ins (no DB fixtures in this repo).

## Global Constraints

- Amends: `docs/superpowers/specs/2026-07-10-units-summary-export-officials-councilors-addendum.md`.
- Branch: continue on `units-summary-export` (already checked out in `csi-project-be`) — this
  is a same-branch amendment to already-committed, already-reviewed work, not a new branch.
- `UnitOfficials`/`UnitCouncilor` are NOT year-versioned (same limitation already documented
  for `UnitMembers`) — do not attempt to filter either by `registration_year`.
- `UnitOfficials.registered_user_id` is `unique=True` — at most one row per unit. A unit may
  have zero `UnitOfficials` rows; still emit all 5 role rows with blank Name/Phone in that
  case (never drop a unit from the export).
- `UnitCouncilor` has no `name`/`phone` fields of its own — Name comes from
  `councilor.unit_member.name`, Phone from `councilor.unit_member.number`.
- Only `app/admin/units_summary_export.py`, `app/common/exporter.py`, and their existing
  test file `tests/test_units_summary_export.py` are in scope. Do not touch the route
  (`app/admin/routers/units.py`) or any frontend file — none of them need to change.
- This working tree has pre-existing, unrelated uncommitted changes (a councilor-requirement
  feature: `app/units/registration_cycle_service.py`, `app/units/routers/user.py`,
  `tests/test_councilor_requirement.py`). Never `git add` broadly (no `git add -A`/`git add .`)
  — always add specific file paths, so these unrelated files are never swept into a commit.

---

### Task 8: Pure row-builder functions for Officials and Councilors

**Files:**
- Modify: `app/admin/units_summary_export.py` (add two functions + one constant)
- Modify: `tests/test_units_summary_export.py` (append tests)

**Interfaces:**
- Consumes: nothing new — these are pure functions over plain objects/dicts.
- Produces: `build_official_rows(officials: Optional[Any]) -> List[Dict[str, str]]` (each
  dict has keys `role`, `name`, `phone`; always returns exactly 5 rows, sorted by `name`
  ascending) and `build_councilor_rows(councilors: List[Any]) -> List[Dict[str, str]]` (each
  councilor object must expose `.unit_member` with `.name`/`.number`; returns 0+ rows sorted
  by `name` ascending). Task 9 imports and calls both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_units_summary_export.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/pytest tests/test_units_summary_export.py -v`
Expected: FAIL/ERROR — `ImportError: cannot import name 'build_official_rows'`

- [ ] **Step 3: Write the minimal implementation**

Add to `app/admin/units_summary_export.py`, after `derive_unit_payment_status` and before
`load_units_summary_for_export`:

```python
_OFFICIAL_ROLE_FIELDS = [
    ("President", "president_name", "president_phone"),
    ("Vice President", "vice_president_name", "vice_president_phone"),
    ("Secretary", "secretary_name", "secretary_phone"),
    ("Joint Secretary", "joint_secretary_name", "joint_secretary_phone"),
    ("Treasurer", "treasurer_name", "treasurer_phone"),
]


def build_official_rows(officials: Optional[Any]) -> List[Dict[str, str]]:
    """Build the 5 fixed-role official rows for one unit, sorted alphabetically by name.
    `officials` is a UnitOfficials instance, or None if the unit has no officials record
    yet — either way, exactly 5 rows are always returned so a unit is never dropped."""
    rows = [
        {
            "role": role,
            "name": (getattr(officials, name_field, None) if officials else None) or "",
            "phone": (getattr(officials, phone_field, None) if officials else None) or "",
        }
        for role, name_field, phone_field in _OFFICIAL_ROLE_FIELDS
    ]
    return sorted(rows, key=lambda r: r["name"])


def build_councilor_rows(councilors: List[Any]) -> List[Dict[str, str]]:
    """Build councilor rows from UnitCouncilor instances (each exposing `.unit_member`),
    sorted alphabetically by name."""
    rows = [
        {
            "role": "Councilor",
            "name": (councilor.unit_member.name if councilor.unit_member else "") or "",
            "phone": (councilor.unit_member.number if councilor.unit_member else "") or "",
        }
        for councilor in councilors
    ]
    return sorted(rows, key=lambda r: r["name"])
```

This requires `Any` in the existing `from typing import Any, Dict, List, Optional` import —
already present in this file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/pytest tests/test_units_summary_export.py -v`
Expected: all tests PASS (18 total: 12 existing + 6 new)

- [ ] **Step 5: Commit**

```bash
cd /home/alex/Downloads/CSI/csi-project-be
git add app/admin/units_summary_export.py tests/test_units_summary_export.py
git commit -m "Add pure row-builders for units-summary Officials/Councilors"
```

---

### Task 9: Wire Officials/Councilors into the loader and CSV, sort units alphabetically

**Files:**
- Modify: `app/admin/units_summary_export.py` (`load_units_summary_for_export`, plus its
  imports)
- Modify: `app/common/exporter.py` (`UNITS_SUMMARY_EXPORT_HEADERS`, `_units_summary_export_rows`)
- Modify: `tests/test_units_summary_export.py` (append exporter tests)

**Interfaces:**
- Consumes: `build_official_rows`, `build_councilor_rows` (Task 8, same file).
- Produces: `load_units_summary_for_export` now returns one dict per person-row (previously
  one per unit), each dict carrying the same unit-level keys as before
  (`unit_id, unit_name, clergy_district, registration_year, registration_status,
  payment_status, total_members, female_members, male_members`) plus three new keys:
  `role, name, phone`. `create_units_summary_csv` gains the 3 matching columns.

- [ ] **Step 1: Write the failing exporter test**

Append to `tests/test_units_summary_export.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/pytest tests/test_units_summary_export.py -v`
Expected: FAIL — the two new tests fail (headers/row-builder don't yet include the 3 new
columns); the previously-passing `test_units_summary_export_rows_defaults_missing_fields`
and `test_create_units_summary_csv_contains_header_and_row` tests will also need updating in
Step 3/4 since the header/row shape is changing (see below).

- [ ] **Step 3: Update the exporter headers and row-builder**

In `app/common/exporter.py`, replace the existing `UNITS_SUMMARY_EXPORT_HEADERS` (lines
195-205):

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
    "Role",
    "Name",
    "Phone",
]
```

Replace `_units_summary_export_rows` (lines 208-222):

```python
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
```

with:

```python
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
            row.get("role", ""),
            row.get("name", ""),
            row.get("phone", ""),
        ])
    return result
```

Now fix the two now-outdated pre-existing tests in `tests/test_units_summary_export.py` (from
the original Task 3) so they match the new 12-column shape — update
`test_units_summary_export_rows_maps_headers_in_order`'s input dict and expected row to
include `"role": "President", "name": "Anna", "phone": "9999999999"` and the matching
trailing 3 values in the expected list; update
`test_units_summary_export_rows_defaults_missing_fields`'s expected row from
`["", "", "", "", "", "", 0, 0, 0]` to `["", "", "", "", "", "", 0, 0, 0, "", "", ""]`; update
`test_create_units_summary_csv_contains_header_and_row`'s input dict to include
`"role": "In Progress"... ` — actually use a realistic value like `"role": "Councilor",
"name": "Test Person", "phone": "8888888888"` and extend the expected content-line assertion
to end with `,Councilor,Test Person,8888888888`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/pytest tests/test_units_summary_export.py -v`
Expected: all tests PASS (20 total)

- [ ] **Step 5: Update the loader's imports**

In `app/admin/units_summary_export.py`, replace the model import line:

```python
from app.auth.models import CustomUser, UnitMembers, UnitName, UnitRegistrationData
```

with:

```python
from app.auth.models import (
    CustomUser,
    UnitCouncilor,
    UnitMembers,
    UnitName,
    UnitOfficials,
    UnitRegistrationData,
)
```

- [ ] **Step 6: Rewrite `load_units_summary_for_export`**

Replace the entire function body (from `async def load_units_summary_for_export` to its
final `return rows`) with:

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

    cycle_ids = [cycle.id for cycle in cycles_by_user.values()]
    payments_result = await db.execute(
        select(UnitRegistrationPayment).where(
            UnitRegistrationPayment.registered_user_id.in_(user_ids),
            UnitRegistrationPayment.registration_cycle_id.in_(cycle_ids),
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

    # Officials/Councilors are NOT year-versioned (same limitation as UnitMembers) —
    # these reflect the unit's current officials/councilors regardless of `registration_year`.
    officials_by_user: Dict[int, UnitOfficials] = {}
    officials_result = await db.execute(
        select(UnitOfficials).where(UnitOfficials.registered_user_id.in_(user_ids))
    )
    for officials in officials_result.scalars().all():
        officials_by_user[officials.registered_user_id] = officials

    councilors_by_user: Dict[int, List[UnitCouncilor]] = {}
    councilors_result = await db.execute(
        select(UnitCouncilor)
        .options(selectinload(UnitCouncilor.unit_member))
        .where(UnitCouncilor.registered_user_id.in_(user_ids))
    )
    for councilor in councilors_result.scalars().all():
        councilors_by_user.setdefault(councilor.registered_user_id, []).append(councilor)

    unit_bases: List[Dict[str, Any]] = []
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

        unit_bases.append(
            {
                "user_id": user_id,
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

    unit_bases.sort(key=lambda base: base["unit_name"])

    rows: List[Dict[str, Any]] = []
    for base in unit_bases:
        user_id = base["user_id"]
        person_rows = build_official_rows(officials_by_user.get(user_id)) + build_councilor_rows(
            councilors_by_user.get(user_id, [])
        )
        for person in person_rows:
            rows.append({**base, **person})
    return rows
```

Note `{**base, **person}` intentionally leaves the harmless `"user_id"` key in each output
dict — `_units_summary_export_rows` only reads the specific keys it needs via `.get(...)`
and ignores extras, so this is not a bug, just an unused pass-through key.

- [ ] **Step 7: Run the full test suite**

Run: `cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/pytest -v`
Expected: all tests pass (20/20 in `test_units_summary_export.py`, plus pre-existing
unrelated suite results unchanged from before this task — the same pre-existing
`test_health.py`/`test_b2_connection.py` issues noted in Task 4's review, if still present,
are not caused by this change)

- [ ] **Step 8: Manual smoke test (if credentials are available)**

If a live database and an admin bearer token are available in this environment:

```bash
cd /home/alex/Downloads/CSI/csi-project-be && .venv/bin/uvicorn main:app --reload
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/admin/units/export/units?registration_year=2026" -o /tmp/units_summary.csv
head -20 /tmp/units_summary.csv
```

Expected: header row ending in `...,Role,Name,Phone`; units appear in alphabetical order by
Unit Name; within each unit, 5 Official rows (sorted by Name, blanks first) appear before
that unit's Councilor rows (also sorted by Name).

If no live database/credentials are available in this environment (as was the case for the
base plan's Task 4), skip this step and note it in your report — rely on the full test suite
and a careful reading of Step 6's diff instead.

- [ ] **Step 9: Commit**

```bash
cd /home/alex/Downloads/CSI/csi-project-be
git add app/admin/units_summary_export.py app/common/exporter.py tests/test_units_summary_export.py
git commit -m "Add Officials/Councilors rows to units summary export, sorted alphabetically"
```
