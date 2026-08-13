# Neotec Insight — architecture

## Layers

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (React 19 + Vite + Chart.js + SheetJS + jsPDF)     │
│  ─ App.tsx (workspace switcher: Reports / Visuals)          │
│  ─ features/run/      → run tab, KPIs, matrix, exports      │
│  ─ features/rows/     → row tree editor                     │
│  ─ features/map/      → Account map + import dialogs        │
│  ─ features/budget/   → editable monthly budget grid        │
│  ─ features/visuals/  → standalone dashboard canvas         │
│  ─ utils/api.ts       → typed wrappers around fetch         │
│  ─ utils/export.ts    → Excel / PDF / CSV / dashboard PDF   │
└──────────────────────────────┬──────────────────────────────┘
                               │ fetch /api/method/…
┌──────────────────────────────▼──────────────────────────────┐
│  Frappe HTTP layer                                          │
│  ─ /api/method/neotec_insight.neotec_insight.api.report.*   │
│  ─ /api/method/neotec_insight.neotec_insight.api.dashboard.*│
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│  Python services                                            │
│  ─ utils/execution.py          → SQL-pushdown engine        │
│  ─ utils/map_importer.py       → MAP sheet parser           │
│  ─ utils/report_structure_importer.py → P&L sheet parser    │
│  ─ utils/mapping_rules.py      → prefix-rule auto-suggest   │
│  ─ utils/formula.py            → AST-whitelist evaluator    │
│  ─ utils/schema.py             → row tree validation        │
│  ─ utils/report_presets.py     → ships default P&L preset   │
└──────────────────────────────┬──────────────────────────────┘
                               │ ORM / SQL
┌──────────────────────────────▼──────────────────────────────┐
│  Persistence (DocTypes)                                     │
│  ─ Insight Report Definition  → row tree + comparison       │
│  ─ Account Flag Mapping       → one row per (report, acc)   │
│  ─ Insight Mapping Rule       → code-prefix rules           │
│  ─ Insight Budget Cell        → per (fy, month, row, seg)   │
│  ─ Insight Dashboard          → Visuals workspace persisted │
│  + ERPNext Account, GL Entry, Cost Center, Project, Dept    │
└─────────────────────────────────────────────────────────────┘
```

## Data flow for a single report run

1. Browser calls `report.run_report` with `{report, fiscal_year, month_from, month_to, segment, cost_center, project, department, prior_years, comparison_mode}`.
2. The handler resolves the `Insight Report Definition`, loads `definition_json`, and pulls the flag→accounts map from `Account Flag Mapping` rows.
3. `execute_report` walks rows in order:
   - **section** → empty monthly bucket.
   - **source** → one `SELECT EXTRACT(MONTH, posting_date), SUM(credit - debit) FROM GL Entry WHERE account IN (…) AND fiscal_year = ? AND dim filters GROUP BY month`.
   - **formula** → evaluated with the safe AST walker against the row context built so far.
4. Prior years are computed the same way, one query batch per year (capped at 5).
5. Budget rows come from `Insight Budget Cell` filtered by `(report, fy, segment)`; missing cells default to FY-1 actual × 1.10. Formula rows in the budget recompute against the source rows in the budget context, never reading from saved formula values.
6. Result is cached under `neotec_insight:exec:{report}:g{gen}:{sig}` for 5 minutes. Cache key includes the user and a generation counter; `_bump_cache_gen` is called on save_report, save_budget_cells, set_account_flag, autosuggest_mappings, and import_map_sheet so writes invalidate cleanly.

## The two importers

### Map sheet importer (`utils/map_importer.py`)

- Takes the raw `.xlsx` bytes (base64-encoded over the wire).
- Opens with `openpyxl` in `data_only=True, read_only=True` mode.
- Default column layout matches the IRSAA template: column B = account string, column C = P&L Classification, data starts at row 5. All four parameters (`sheet_name`, `account_col`, `flag_col`, `header_rows`) are exposed in the UI in case a different template uses different positions.
- Each account string `"31101024 - Sales - IRSAA"` is parsed by one regex into `{code, name, entity}`.
- `resolve_account_to_frappe(code, name, company)` finds the matching ERPNext Account doc by `account_number` first, falling back to a `LIKE` on `account_name`. The company filter is the report's default company.
- `apply_map_to_report` either replaces existing mappings (default) or merges them, and returns `{created, skipped_no_flag, skipped_no_match, warnings}`.

### Report-structure importer (`utils/report_structure_importer.py`)

- Reads the P&L sheet in formulas mode (`data_only=False`).
- Walks column B; non-formula labels become rows.
- Decision tree for each row:
  - First data cell is `="…"`? → **formula** row, translate the Excel formula.
  - Label matches a flag found in the MAP sheet? → **source** row, with `flag = label`, accounts left empty (they're picked up from `Account Flag Mapping` at runtime).
  - Otherwise → **section** if it looks like a header (short, no totals/EBITDA/PBT keywords), else **source** with a warning that the flag isn't established.
- `translate_formula` converts Excel cell references in the data column to row keys. `=D8-D10-D12` → `r_revenue - r_cogs - r_employee`. `SUM(D17:D25)` → `(r_rent + r_utilities + ... + r_supplies)` by expanding every row index in the range and looking up the row key at that row.
- The endpoint can preview the inferred tree (`create_report=0`) or create the `Insight Report Definition` directly (`create_report=1`).

## Granularity tiers

The `granularity` parameter on `run_report` is one of eight values, computed by `utils/periods.py` into a list of tiered period groups:

| Value | Tiers shown |
|---|---|
| `month` | Monthly columns only |
| `quarter` | Quarter rollups only |
| `half` | Half-yearly rollups only |
| `ytd` | A single YTD column |
| `month_quarter` (default) | Months **and** quarter rollups |
| `month_half` | Months **and** half-yearly rollups |
| `quarter_half` | Quarters **and** half-yearly rollups |
| `month_quarter_half` | All three tiers |

A "rollup" column re-aggregates the source rows for its month range, then re-evaluates the formula rows against those summed source values. That guarantees Gross Profit for Q1 equals `(Q1 Revenue) - (Q1 COGS)`, never the sum of monthly GP figures, which would only match when GP is linear (it usually isn't).

Rollups respect the From/To window: ask for May–Aug with `month_quarter` and you'll see partial Q2 (May+Jun only) followed by partial Q3 (Jul+Aug only).

## Comparison rules

- `comparison_mode = "actuals_only"` → only the current year and prior-year columns.
- `comparison_mode = "vs_budget"` → adds a Budget column per period plus the YTD total.
- `prior_years` is enforced 0–5 at the DocType, API, and frontend levels (defense in depth).
- Calendar-aligned: Jan FY26 lines up with Jan FY25, Jan FY24, etc.

## Visuals workspace

A `RunSnapshot` is created when the user clicks Visualize on the Run tab. The snapshot is a deep clone of the run result + the row definitions — independent of any later changes to filters on the Run tab.

A `Tile` carries `{runId, title, type, rowKeys, series, palette}` — nothing about how the run was filtered. The same tile spec works against any snapshot that contains the referenced row keys, so a "Revenue vs Budget by quarter" tile can be reused across reports.

Persisting a dashboard saves both the tiles and the run snapshots into `Insight Dashboard.run_snapshots_json` so the dashboard reopens with the same data.

## Permissions

Three roles:

- **System Manager** — full control.
- **Accounts Manager** — create, edit, delete reports / mappings / budget / dashboards.
- **Accounts User** — read access to active reports and dashboards; can run reports.

Per-row access control on `Insight Report Definition` (owner-inherit + active-default-readable) lives in `doctype/insight_report_definition/insight_report_definition.py` and is wired in `hooks.py` via `permission_query_conditions` and `has_permission`.

## What's intentionally out of scope for v1.0

- Currency conversion (the engine returns numbers in source currency).
- Scheduled email delivery (the daily cron entrypoint exists in `tasks.py` but the scheduler module is a stub).
- Conditional formatting rules on the matrix.
- Trial Balance, AR Aging, AP Aging starter templates (only Consolidated P&L ships).

These were deliberate cuts to keep v1.0 shippable. The DocType schema for `Insight Report Definition` already has room for currency config, and the scheduler tasks hook is wired; adding either is additive.
