# Units Summary Export: Officials & Councilors Addendum

Amends `2026-07-10-units-summary-export-design.md`. That spec shipped a one-row-per-unit
CSV (`GET /admin/units/export/units`). This addendum changes the row shape to one row per
person (per Official, per Councilor) within each unit, per user request after reviewing the
first version's output.

## Goals

- Add each unit's Officials (President, Vice President, Secretary, Joint Secretary,
  Treasurer) and Councilors as rows in the same CSV, replacing the current one-row-per-unit
  shape.
- Units ordered alphabetically by Unit Name. Within a unit: Officials rows first (sorted
  alphabetically by Name), then Councilor rows (sorted alphabetically by Name).
- Keep all existing unit-level columns (Unit ID, Unit Name, Clergy District, Registration
  Year, Registration Status, Payment Status, Total Members, Female Members, Male Members),
  repeated identically on every row belonging to that unit.
- Add three columns: Role, Name, Phone.

## Non-goals

- No change to the route signature, query params, or the frontend (both already call this
  endpoint agnostic of column shape — no frontend changes needed).
- No change to any other `export_type` branch.

## Data model

- `UnitOfficials` (`app/auth/models.py:209-231`): one row per unit
  (`registered_user_id unique=True`), with 5 fixed role slots, each an
  independent nullable `name`/`phone` pair: `president_name/president_phone`,
  `vice_president_name/vice_president_phone`, `secretary_name/secretary_phone`,
  `joint_secretary_name/joint_secretary_phone`, `treasurer_name/treasurer_phone`. Not
  year-versioned — same limitation already documented for `UnitMembers` in the base spec.
  A unit may have zero `UnitOfficials` rows (not yet reached that registration step).
- `UnitCouncilor` (`app/auth/models.py:234-242`): zero or more rows per unit
  (`registered_user_id`, no unique constraint), each linking to a `UnitMembers` row via
  `unit_member_id` for `name`; phone comes from `unit_member.number` (Councilors don't have
  their own phone field — they reuse the linked member's). Also not year-versioned.

## Row generation (per unit)

1. **5 Official rows**, always emitted regardless of whether the unit has a `UnitOfficials`
   row or any individual field is blank:
   - Role="President", Name=`president_name or ""`, Phone=`president_phone or ""`
   - Role="Vice President", Name=`vice_president_name or ""`, Phone=`vice_president_phone or ""`
   - Role="Secretary", Name=`secretary_name or ""`, Phone=`secretary_phone or ""`
   - Role="Joint Secretary", Name=`joint_secretary_name or ""`, Phone=`joint_secretary_phone or ""`
   - Role="Treasurer", Name=`treasurer_name or ""`, Phone=`treasurer_phone or ""`
   - If the unit has no `UnitOfficials` row at all, all 5 rows still emit with blank
     Name/Phone (so the unit is never silently dropped from the export).
   - Sort these 5 rows by Name ascending. Since Python's sort is stable, ties (all-blank
     units) retain the role order listed above as a deterministic tiebreak — no special
     tiebreak logic needed.
2. **Councilor rows**, one per `UnitCouncilor` belonging to the unit (0 or more): Role="Councilor",
   Name=`unit_member.name or ""`, Phone=`unit_member.number or ""`. Sort by Name ascending.
3. Concatenate: unit's 5 Official rows (sorted) + unit's Councilor rows (sorted).

Units themselves are ordered alphabetically by Unit Name in the final output (changed from
the base spec's implicit DB-fetch order).

## Columns (final order)

Unit ID, Unit Name, Clergy District, Registration Year, Registration Status, Payment
Status, Total Members, Female Members, Male Members, Role, Name, Phone.

## Implementation shape

- `load_units_summary_for_export` (`app/admin/units_summary_export.py`) changes its return
  shape from one dict per unit to a flattened `List[Dict[str, Any]]` with one dict per
  person-row (multiple rows share the same unit-level values). Two new bulk queries are
  added (mirroring the existing bulk-query style already in this function for cycles/
  payments/members — not reusing `_load_officials_for_export`/`_load_councilors_for_export`
  in `units.py`, since those are scoped to a single unit or district and this function needs
  all units at once):
  - `select(UnitOfficials).where(UnitOfficials.registered_user_id.in_(user_ids))` →
    dict keyed by `registered_user_id` (0 or 1 row each).
  - `select(UnitCouncilor).options(selectinload(UnitCouncilor.unit_member)).where(UnitCouncilor.registered_user_id.in_(user_ids))`
    → dict keyed by `registered_user_id` → list of councilor rows.
  - Final unit iteration order changes from DB-fetch order to sorted by `unit_name`.
- `UNITS_SUMMARY_EXPORT_HEADERS`/`_units_summary_export_rows`/`create_units_summary_csv`
  (`app/common/exporter.py`) gain the 3 new columns; the row-builder is otherwise unchanged
  (still one row per dict — the "multiple rows per unit" behavior is entirely the loader's
  responsibility, not the CSV writer's).

## Testing

- Pure-function coverage for the row-generation logic (which 5 official rows, in what
  order, with what blanks) should be testable independent of the DB, similar to Task 1's
  pure functions — a new pure function that takes a `UnitOfficials`-shaped input (or `None`)
  and a list of councilor dicts and returns the sorted list of person-row dicts, so the
  sorting/blank-row logic is unit-tested without a live DB.
- `_units_summary_export_rows`/`create_units_summary_csv` get new tests for the 3 additional
  columns, following the existing test file's conventions.
- The DB-loading part of `load_units_summary_for_export` remains untested (no DB fixtures in
  this repo, consistent with the base spec).
