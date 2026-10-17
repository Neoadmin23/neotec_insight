# Neotec Insight 2.90.0 — Response to the 2.89.6 Health Check

Each finding was first checked against the 2.89.6 source. All thirteen were confirmed and are addressed below. Every fix has tests in `neotec_insight/tests/test_v2_90_security.py`; the test class for each finding is named after it (`TestHC01…` to `TestHC13…`).

## Result

| Check | 2.89.6 (review) | 2.90.0 |
|---|---|---|
| Offline tests | 415 passed | **466 passed** (51 new, one class per finding) |
| Raw TypeScript compile | 11 errors, hidden by a baseline | **0 errors**, exit 0; baseline removed |
| Type-check gate | Printed "OK" on a fatal compiler error | Fails on any error, non-zero exit, or silent crash (tested) |
| Lint | Not operational | ESLint 9 runs in every build: 0 errors, 83 style warnings |
| npm audit | 19 entries (1 critical, 11 high) | **1** (xlsx — no fixed release on npm; usage bounded, see HC-09) |
| Unrestricted endpoints that change data | 87 | 0 outside an explicit list of 11 reads that only refresh a cache, plus the `report.run_*` reads (tested) |
| Python compile / undefined names | Clean | Clean (181 files) |
| Production bundle | Reproducible | Rebuilt; no orphan or missing assets; main bundle about 60 KB smaller |

## Findings

**HC-01 — Company and report-record restrictions (High).** A single policy module, `utils/access.py`, is enforced by a `before_request` hook on every Insight API call.
- **Named arguments are checked:** a company, a list of companies, a report, or a single cost centre, project, department or branch must be one the user's User Permissions allow.
- **Restricted users are scoped:** a user restricted to one company has it filled in on endpoints that take a company. A user restricted to several must choose one. If the endpoint cannot be inspected, the request is refused (fail closed).
- **Reports:** `list_companies` and `list_reports` now use `get_list`. Disabled reports are listed only for managers. `_resolve_report_doc` checks the report's own permission and company.

**HC-02 — HR/payroll authorization (High).** The ten copies of `_require_read` now delegate to one policy that also applies the People-only rule everywhere.
- **People endpoints** (`hr_summary`, `eosb_breakdown`, provision config) need HR or payroll access: Salary Slip read, or Employee read with an HR role, or an executive role. Ledger access alone no longer reads wages.
- **HR-only users** can open People without ledger access, and are refused finance endpoints.

**HC-03 — Private-file parsing (High).** `access.readable_file` is used by both statement and slip reading. It checks the File permission through Frappe, an allowed-type list, a 25 MB limit, and that the real path is inside the site's `public` or `private` files folder.

**HC-04 — Report Studio linked fields (High).**
- **Fields:** fields are offered, selected, filtered on, grouped and joined only if they are at a permission level the user holds. Password fields never are.
- **Records:** joined records and value suggestions use `get_list`, so a linked record the user cannot read gives blanks.

**HC-05 — Conditional formulas (High).**
- **Lazy `IF`:** only the chosen branch is evaluated, so `IF(0,1/0,42)` = 42.
- **Validation:** wrong argument counts and keyword arguments are rejected when the formula is saved.
- **Visible failures:** division by zero and non-finite results raise a `FormulaError`. The cell still shows 0 so totals add up, but the run result lists every failed row and month in `formula_errors`, and the Run screen shows them with a "check before exporting" warning.

**HC-06 — Fiscal-year and account caches (High).** Both process-wide dictionaries are replaced by `utils/site_cache.py`.
- **Two layers:** a per-request memo, over Frappe's per-site shared cache with a 10-minute limit.
- **Invalidation:** a Company save, and any Account insert, update, delete or rename, clears the shared key for every worker.
- **Tested:** two sites with the same company name keep separate calendars; a new account appears after invalidation.

**HC-07 — CFO briefing windows (Medium).**
- **AR/AP as at the date:** computed from ERPNext's Payment Ledger, so invoices dated after it are excluded and payments are counted up to it. Aggregated in SQL, with no row cap. Older ERPNext without a Payment Ledger falls back to current outstanding, labelled as such.
- **Revenue:** month-to-date is compared with the same days of last month and of last year.
- **Runway:** net cash outflow over the last three full months, divided by three. The expense run-rate is shown separately and labelled.
- **Unreconciled transactions:** counted in SQL, uncapped.

**HC-08 — Type check and lint (Medium).**
- **Errors fixed:** all 11 compiler errors, mostly API type annotations.
- **Gate:** `typecheck.sh` requires tsc exit 0 and no diagnostics. It is tested against a fatal `TS5083`, a silent crash and an error with exit 0.
- **Lint:** ESLint 9 with typescript-eslint and React-hooks rules. Hook-rule violations and other defects are errors; style is warnings. It runs in `npm run build` and `npm run check`.

**HC-09 — Dependencies (High).**
- **frappe-react-sdk removed:** it was imported only for an unused provider, and carried socket.io, ws and axios.
- **Upgraded:** jsPDF 2.5.2 → 4.2.1 and jspdf-autotable 3.8.4 → 5.0.8. A PDF with Arabic text and the per-cell style hook renders correctly. Vite 5.4 → 6.4.3.
- **Fixed indirect versions:** postcss 8.5.28, nanoid 3.3.19 and baseline-browser-mapping 2.11.26 are pinned as overrides. Every chosen release is at least two weeks old.
- **Remaining — xlsx:** SheetJS publishes fixed versions only on its own CDN, not npm. The app parses Excel in the browser only for the budget-template upload, which is now limited to `.xlsx`/`.xls`, 10 MB, the first sheet, with formulas and HTML not evaluated. **Recommended on your build machine:** `npm install https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz` (that CDN was not reachable from the build environment used here).

**HC-10 — PDF resources (Medium).** Only these may load: inline `data:image/` images, the site's own `/files`, `/private/files` and `/assets`, and absolute URLs on the site's own host (how the letterhead logo is referenced). Everything else is removed: CSS `@import`, other hosts, loopback and metadata addresses, and `file:`/`gopher:` links. `srcset` and CSS `url()` are covered. Renderer network egress should still be restricted at the server.

**HC-11 — State-changing GET (Medium).**
- **62 endpoints** that change data now accept POST only. Every screen call to them already used POST; a test checks that every screen call to a POST-only endpoint sends POST.
- **Left open on purpose:** reads that only refresh a cache. They are listed in the test, and the test fails if any other endpoint that changes data is left open.

**HC-12 — Statement import (Medium).**
- **One state per line:** submitted, left as draft (with the reason), failed (with the reason), or already imported.
- **Retry:** importing the same file again retries submitting earlier drafts instead of skipping them by reference.
- **Checks:** `ignore_mandatory` is removed, the bank account must be readable and in the user's company scope, and the import screen lists drafts and failures.

**HC-13 — Backup and schedule safeguards (Medium).**
- **Snapshot guard:** the pre-change snapshot is now taken inside a savepoint of the caller's transaction. It no longer commits the caller's pending work early, and when it fails it no longer rolls that work back; a test covers both.
- **Required snapshots:** every restore, delete and overwrite requires a complete snapshot, or the operation is refused. A System Manager can override this on full restores.
- **Incomplete snapshots** are marked "INCOMPLETE" and cannot satisfy a required snapshot.
- **After a restore:** dangling links are listed, and a bundle from a different app version is flagged.
- **Schedules run as the person who last saved them** (`run_as`, back-filled from the owner), not as Administrator. A disabled owner fails the run. Saving a schedule runs the report once as the saver, and applies the recipient policy: site users only unless a System Manager sends, or a domain allowlist is configured; WhatsApp is System Manager only.

## Behaviour changes to communicate before release

1. **Multi-company users must choose a company.** A user limited to several companies is now asked to choose a company on screens where they previously saw all companies combined.
2. **People access changes.** Finance users without HR or payroll access lose the People workspace; HR users without ledger access gain it.
3. **Schedules may stop sending.** A schedule saved by someone who has since lost access now fails (shown in its status) instead of sending. Schedules with external recipients must be re-saved by a System Manager, or `insight_schedule_allowed_domains` must be set.
4. **Failed safety snapshots now block.** A restore or bulk delete stops if its snapshot fails, with an explicit override for System Managers on full restores.

## Still requires your staging site

These checks were run against an in-memory model of Frappe, not a live bench. The review's staging checklist (section 7) still applies, in particular:
- real User Permission resolution for restricted, HR-only and mixed-role users;
- Payment Ledger figures against ERPNext's Accounts Receivable report for a past date;
- wkhtmltopdf output with your letterhead;
- the two new patches on an upgrade from 2.89.6;
- schedules sending as their owner.

Statutory VAT, Zakat and end-of-service formulas still need validation by your finance and payroll specialists.

## Not changed in this release

As the review advised, these are kept out of a security release:
- splitting `api/report.py` into smaller modules;
- loading heavy screens on demand to shrink the bundle;
- Ruff style clean-up;
- the 83 lint warnings.
