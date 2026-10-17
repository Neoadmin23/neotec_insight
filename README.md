# Neotec Insight

A Frappe / ERPNext app for metadata-driven financial reporting, with:

- **Map-sheet account mapping** — import an Excel `MAP` sheet (Chart of account → P&L Classification) and the app generates the full row tree automatically
- **Report structure importer** — upload an existing financial report `.xlsx` and the app infers section / source / formula rows, sniffs out aggregator formulas, and gives you a clickable preview before commit
- **Multi-year comparison** — 1 to 5 prior years (default 1), calendar-aligned
- **Actual vs Budget** — built-in budget grid (per row / per month / per segment) with prior-year × 1.10 seed
- **Excel / PDF / CSV / Print exports** on every report run
- **Visuals workspace** — a separate dashboard canvas that consumes any saved report run; charts, KPI tiles, and mini tables; export the dashboard to PDF or print

## Requirements

| Component | Supported |
|---|---|
| Frappe | v15 (`>=15,<16`) |
| ERPNext | v15 — **required** (declared in `required_apps`; install stops without it) |
| HRMS | Optional. People, payroll and end-of-service use it when present |
| Database | MariaDB. Several queries use MariaDB-specific SQL; PostgreSQL is **not** supported |
| Python | 3.10 or later |
| Node (building the screen only) | 20.19 or later; the built screen is committed, so Frappe Cloud needs no Node |
| AI features | Optional, self-hosted Ollama; off unless configured |

## Access model (v2.90.0)

- **Finance data** needs GL Entry read and a finance role. A user whose only Insight-relevant roles are HR roles is People-only.
- **People data** (headcount, wages, payroll, end-of-service) needs HR or payroll access: Salary Slip read where HRMS is installed, otherwise Employee read with an HR role, or Insight CFO / CEO / System Manager. Ledger access alone does not grant it.
- **Company scope:** every Insight API call that names a company, report, cost centre, project, department or branch is checked against the user's User Permissions. A user limited to one company gets it filled in when they name none; one limited to several companies is asked to choose.
- **Changes need POST:** every endpoint that changes data accepts POST only.
- **Scheduled reports** run with the permissions of the person who last saved the schedule. Recipients must be users of the site, unless a System Manager sends them, or `insight_schedule_allowed_domains` is set in site config. WhatsApp delivery is System Manager only.

## Backups — what they are and are not

- **What a backup holds:** Backup → Export and the automatic snapshots hold everything Insight stores (setup and data). Snapshots are taken daily, before every migrate, before every restore and before bulk changes.
- **Where they live:** snapshots are kept **in the same database**, and they reference attachments rather than containing them. They are not disaster recovery. Keep Frappe Cloud's (or your own) off-site database **and files** backups, and test restoring them.
- **Restores are protected:** a restore, delete or overwrite stops if its safety snapshot cannot be taken complete. A System Manager can override this on a full restore. After a restore, records that point at something missing are listed.

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
