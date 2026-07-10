# Units Summary Export (CSV, by Registration Year)

## Problem

The admin "Export Data" page (`csi-webapp-fe`, route `#/admin/export`) lets admins export
district-wise/unit-wise officials & councilors, and registration payment data. There is no
option to export a single summary row per unit — with membership counts and a gender
breakdown — for a chosen registration year.

A related export already exists elsewhere: the `ViewAllUnits` page (`#/admin/units`, a
different page from `#/admin/export`) has an "Export" button that downloads an Excel file
via `GET /admin/units/export/units` (backend: `export_unit_data`, `export_type="units"`).
That existing export:

- Is always locked to the *current* registration year (`list_all_units` has no year
  parameter and is cached per current year).
- Has no `Unit ID` column (only `username`, i.e. the registered user's id).
- Has no gender breakdown (only aggregate `member_count`).
- Is Excel (`.xlsx`), not CSV.

This spec upgrades that shared export type in place — rather than creating a parallel
export type — so both entry points (the Export Data page and the ViewAllUnits page) stay
consistent and share one implementation.

## Goals

- Add a "Units Summary" CSV export, selectable by Registration Year, with columns:
  Unit ID, Unit Name, Clergy District, Registration Year, Registration Status, Payment
  Status, Total Members, Female Members, Male Members.
- Expose this on the Export Data page (`#/admin/export`) as a new card.
- Upgrade the existing ViewAllUnits export button to use the same backend export type,
  and add a Registration Year selector there too, for consistency.
- Preserve backward compatibility: if `registration_year` is omitted, behavior defaults to
  the current registration year (same as today).

## Non-goals

- No changes to the other export types on `/admin/units/export/{export_type}`
  (`members`, `officials`, `councilors`, `district-officials`, `district-councilors`,
  `unit-officials`, `unit-councilors`) — these remain Excel, unchanged.
- No changes to the Registration Payments / District Payment Summary exports.
- No new page or route; this only adds a card to an existing page and a control to
  another existing page.

## Backend design (`csi-project-be`)

### New per-unit data loader

Add `_load_units_summary_for_export(db, *, registration_year: int) -> List[dict]` in
`app/admin/routers/units.py`, near the other `_load_*_for_export` helpers
(`_load_members_for_export`, `_load_officials_for_export`, `_load_councilors_for_export`).

Query shape:

- Base: `UnitName` joined to `ClergyDistrict` (gives Unit ID, Unit Name, Clergy District
  for every registered unit, regardless of whether they have a cycle row for the
  requested year).
- Left join `CustomUser` (`unit_name_id`) to get the registered user id (needed to look up
  cycle/members).
- Left join `UnitRegistrationCycle` filtered to `registration_year == :registration_year`
  to get `status` (registration status) and derive `payment_status` the same way
  `list_all_units` currently does (lines ~351-398 of `units.py`) — reuse that logic rather
  than reimplementing it, factored into a small shared helper if it isn't already
  standalone.
- Where there is no cycle row for that year, `status` becomes `"Not Started"` (matching
  today's synthesized fallback) and `payment_status` becomes `"not_submitted"`.
- Separately, aggregate `UnitMembers` grouped by `registered_user_id` and `gender`
  (`'M'` / `'F'`) for members belonging to that same registration cycle, to get
  `total_members`, `female_members`, `male_members` per unit. This is a new query — no
  existing query aggregates gender per individual unit; the only precedent is a
  per-*district* dashboard aggregate.

Each returned dict:

```python
{
    "unit_id": ...,           # UnitName.id
    "unit_name": ...,
    "clergy_district": ...,
    "registration_year": ...,
    "registration_status": ...,   # display label, see mapping below
    "payment_status": ...,        # display label, see mapping below
    "total_members": ...,
    "female_members": ...,
    "male_members": ...,
}
```

### Status label mapping

Map raw values to the same display labels the frontend already uses, so the CSV is
human-readable without post-processing:

- Registration status: raw values `"Registration Started"`, `"Declaration Submitted"`,
  `"Registration Completed"`, synthesized `"Not Started"` — passed through as-is (they're
  already human-readable); no further mapping needed.
- Payment status: raw values `not_submitted` / `pending` / `partial` / `approved` /
  `rejected` → labels currently duplicated in the frontend's
  `PAYMENT_STATUS_LABELS` (`ViewAllUnits.tsx`): `"Not submitted"`, `"Pending review"`,
  `"Partially paid"`, `"Fully paid"`, `"Rejected"`. Add an equivalent mapping dict in the
  backend (co-located with the loader) so the CSV carries friendly labels directly.

### CSV builder

Add to `app/common/exporter.py`, following the existing pattern used by
`_registration_payment_export_rows` / `create_registration_payments_csv`:

- `_UNITS_SUMMARY_EXPORT_HEADERS` constant:
  `["Unit ID", "Unit Name", "Clergy District", "Registration Year", "Registration Status",
  "Payment Status", "Total Members", "Female Members", "Male Members"]`
- `_units_summary_export_rows(rows: List[dict]) -> List[List[Any]]` — maps each dict to a
  row list in header order.
- `create_units_summary_csv(rows: List[dict]) -> BytesIO` — `csv.writer` over `StringIO`,
  same shape as `create_registration_payments_csv`.

### Route changes

In `export_unit_data` (`app/admin/routers/units.py`, `GET /admin/units/export/{export_type}`):

- Add an optional query param `registration_year: Optional[int] = Query(None)`.
- In the `export_type == "units"` branch: if `registration_year` is not provided, resolve
  it via `cycle_service.get_current_registration_year(db)` (same default as today).
  Replace the current `list_all_units`-based row building with
  `_load_units_summary_for_export(db, registration_year=registration_year)`, and call
  `create_units_summary_csv(rows)` instead of `create_units_excel(rows)`.
- Update the response for this branch: `media_type="text/csv"`,
  filename `f"units_summary_{registration_year}_{timestamp}.csv"`.
- All other `export_type` branches are untouched (still Excel, still ignore the new
  param).

## Frontend design (`csi-webapp-fe`)

### `services/api.ts` — `exportData`

Current signature hardcodes `.xlsx` as the download filename and doesn't read
`Content-Disposition` (unlike `exportRegistrationPayments`, which uses `downloadBlob` +
`getFilenameFromContentDisposition`).

Change to:

```ts
async exportData(type: string, id?: number, registrationYear?: number): Promise<ApiResponse<Blob>>
```

- Append `registration_year` to the query string when provided.
- Read the filename from the response's `Content-Disposition` header via
  `getFilenameFromContentDisposition`, falling back to `${type}.xlsx` only when the header
  is missing (preserves current behavior for the untouched Excel export types).

### `ExportData.tsx` (`#/admin/export`)

Add a new card in the "Unit Data" section, after "Export Unit-wise Data":

- Heading: "Export Units Summary".
- A Registration Year `<select>` reusing the existing `yearOptions` memo (already computed
  in this file for the payment section — lift it to component scope if not already, no
  behavior change).
- An "Export (CSV)" button calling
  `api.exportData('units', undefined, selectedSummaryYear)`.
- Loading/disabled state and toast handling follow the same pattern as
  `handlePaymentExport`.

### `ViewAllUnits.tsx`

- Add a Registration Year `<select>` next to the existing "Export" button, using the same
  `yearOptions` derivation pattern (current registration year + prior 3 years), defaulting
  to the current registration year.
- Update `handleExport` to pass the selected year:
  `api.exportData('units', undefined, selectedYear)`.

## Error handling

- If `registration_year` is provided but out of the plausible range (e.g. before the
  platform existed or in the future beyond the current year), the query simply returns
  units with `"Not Started"` / `"not_submitted"` for all of them (no cycle rows exist for
  that year) — no special-cased validation needed, this falls out of the left-join
  design.
- Existing error/toast handling in both frontend files (try/catch around `api.exportData`,
  `addToast` on failure) is reused as-is.

## Testing

- Backend: unit/integration test for `_load_units_summary_for_export` covering a unit with
  a completed cycle + members of both genders, a unit with no cycle row for the requested
  year, and a unit with a cycle row but zero members.
- Backend: route test hitting `/admin/units/export/units?registration_year=YYYY` asserting
  CSV content-type, headers row, and row values.
- Backend: route test omitting `registration_year` asserting it defaults to the current
  registration year (regression check for `ViewAllUnits`'s existing call pattern).
- Frontend: manual smoke test of both the new Export Data card and the updated
  ViewAllUnits button, confirming the downloaded CSV opens with the expected columns for
  at least two different registration years.
