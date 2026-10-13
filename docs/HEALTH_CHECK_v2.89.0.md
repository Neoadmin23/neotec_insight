# Neotec Insight — Health Check, v2.89.0

Scope: the whole app as shipped in v2.88.14, then re-run on v2.89.0. Codebase: 170 Python files (about 36,000 lines), 40 doctypes, 285 whitelisted endpoints, and a React/TypeScript frontend making 230 distinct API calls.

## Result

| Check | v2.88.14 | v2.89.0 |
|---|---|---|
| Python compiles | ✓ | ✓ |
| Undefined names (pyflakes) | 0 | 0 |
| `_` shadowed where `_()` is called | 0 | 0 |
| Doctype JSON valid, folder/name/controller match | 7 folders without `__init__.py` | ✓ fixed |
| Child-table references resolve | ✓ | ✓ |
| `field_order` matches `fields` | ✓ | ✓ |
| Hook paths resolve (incl. new snapshot hooks) | ✓ | ✓ |
| Patches listed and present | ✓ | ✓ (+ `v2_89.install_snapshot_store`) |
| Every frontend call has a whitelisted backend method | ✓ (manual) | ✓ (now a test) |
| TypeScript, against committed baseline | ✓ | ✓ no new errors |
| Build reproduces the committed bundle | ✓ | ✓ no orphan or missing assets |
| Test suite | 312 green | 376 green |
| Backup covers every doctype | ✗ 4 excluded, hand-kept list | ✓ discovered automatically |
| Allocation data loss paths | 6 open (below) | closed |

## Findings and fixes

**1. A wrong-year response could replace the grid (high).** The Year box fired a load on every keystroke. Replies were not sequenced, so an empty grid for "202" could replace 2026's and raise the "no entries" warning while the data was intact. Fixed with year commit on four digits and per-load sequence numbers on both grid and report.

**2. Save could delete figures unnoticed (high).**
- Blank cells deleted stored rows.
- Frozen months were overwritten or deleted.
- A stale or half-loaded grid could be saved over real figures.

Fixed in `utils/allocation_guard.py`: a stale-grid refusal, frozen protection, bulk-delete confirmation, an override flag, and a snapshot before deleting.

**3. Restore could leave a site half-restored (high).** Replace deleted first and inserted record by record; one failure meant lost data. Now all-or-nothing with a pre-restore snapshot, and replace updates in place.

**4. Backup coverage depended on a hand-kept list (high).** New doctypes were silently missed; allocation entries were missing until 2.88.11, and four data doctypes are still excluded in 2.88.14. Now discovered from installed doctypes and ordered from the link graph.

**5. No automatic backup (high).** Added daily, pre-migrate, pre-restore and pre-change snapshots, with loss alerts.

**6. Capture wrote head counts into Amount rows (medium).** Now skipped.

**7. Missing `__init__.py` in 7 Cash Flow doctype folders (medium).** These worked only as implicit namespace packages, which a non-editable install can drop. Added.

**8. Recovery popup pointed to a Merge import that did not exist (low).** The Backup screen now offers Merge (the default) and Replace.

## Left as is (no functional effect)

- 27 unused imports, 6 unused local variables and 1 unused `global` statement.
- 1 loop variable `_` in `report.py` (P&L series builder) that shadows the translation function; `_()` is not called in that function.

These are cosmetic and touch many unrelated files, so they are not mixed into a data-safety release.

## What cannot be checked offline

- **Live site:** Frappe-specific behaviour was exercised against an in-memory site with real commit/rollback (`tests/_fakefrappe.py`), not a live bench. The first migrate on a staging site is the remaining confirmation.
- **Snapshot size:** snapshot payloads live in the database. A site with very large bank-slip or report-definition volumes should keep an eye on snapshot size (shown per snapshot) against the database's packet limit.
