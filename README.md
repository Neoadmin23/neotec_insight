# Neotec Insight

A Frappe / ERPNext app for metadata-driven financial reporting, with:

- **Map-sheet account mapping** — import an Excel `MAP` sheet (Chart of account → P&L Classification) and the app generates the full row tree automatically
- **Report structure importer** — upload an existing financial report `.xlsx` and the app infers section / source / formula rows, sniffs out aggregator formulas, and gives you a clickable preview before commit
- **Multi-year comparison** — 1 to 5 prior years (default 1), calendar-aligned
- **Actual vs Budget** — built-in budget grid (per row / per month / per segment) with prior-year × 1.10 seed
- **Excel / PDF / CSV / Print exports** on every report run
- **Visuals workspace** — a separate dashboard canvas that consumes any saved report run; charts, KPI tiles, and mini tables; export the dashboard to PDF or print

## Install

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app neotec_insight $URL_OF_THIS_REPO
bench --site your-site install-app neotec_insight
bench --site your-site migrate
```

After install:

- App route: `/insight`
- Default `Consolidated P&L` Report Definition is created (mirrors the structure in `Consolidated_Budget_And_P_L_-_Template.xlsx`)
- Default mapping rules are seeded (`311*` → Revenue, `41101*` → COGS, `42101*` → Total Employee Cost, etc.)

## Importing your existing template

1. Go to `/insight` → Account map tab.
2. Click "Import Excel".
3. Pick your `Consolidated_Budget_And_P_L_-_Template.xlsx`.
4. The importer reads the `MAP` sheet (column B = account, column C = P&L Classification), then optionally reads the `P&L` sheet to mirror the row order, formula links, and section headers.
5. Preview the proposed Report Definition; warnings show orphan flags, unmapped accounts, and unresolvable formula refs. Commit when satisfied.

## Architecture

See [docs/architecture.md](docs/architecture.md).

## License

MIT
