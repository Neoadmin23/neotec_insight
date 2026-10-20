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
| Frappe | **v15 and v16** (`>=15,<17`) — one branch for both, from v2.93.0 |
| ERPNext | v15 or v16 — **required** (declared in `required_apps`; install stops without it) |
| HRMS | Optional. People, payroll and end-of-service use it when present |
| Database | MariaDB. Several queries use MariaDB-specific SQL; PostgreSQL is **not** supported |
| Python | 3.10+ on Frappe v15; 3.14+ on Frappe v16 (tested on 3.12 and 3.14) |
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

## Copilot on an outside database (v2.95.0)

1. **Create a read-only account on that database.** This account is the main protection; Insight's query checks are a second line.
   - MySQL / MariaDB: `CREATE USER 'insight_ro'@'%' IDENTIFIED BY '…'; GRANT SELECT ON salesdb.sales TO 'insight_ro'@'%'; GRANT SELECT ON salesdb.items TO 'insight_ro'@'%';`
   - PostgreSQL: `CREATE ROLE insight_ro LOGIN PASSWORD '…'; GRANT USAGE ON SCHEMA public TO insight_ro; GRANT SELECT ON sales, items TO insight_ro;`
   - SQL Server: create a login and user, then `GRANT SELECT ON dbo.sales TO insight_ro;` (needs the `pymssql` package on the server).
2. **Make the database reachable from your Frappe server.** On Frappe Cloud this means a public address with TLS, restricted to Frappe Cloud's outbound IPs, or a tunnel.
3. **Desk → Insight Data Source → New.**
   - Enter the connection.
   - List the **Allowed Tables**.
   - Set **Allowed Roles** (blank means System Managers only).
   - Press **Test connection & read tables**.
4. **In Insight, open ✨ Ask Insight** and pick the source in the **Data** selector.

What happens to each question, and what goes where:
- The model sees the allowed tables' structure and writes one SELECT.
- Insight refuses anything else before it reaches the database: more than one statement, comments, writes, server functions, or tables not on the list.
- The query runs in a read-only session with a timeout and a row cap.
- The query used is shown with every answer.
- Only the table structure and up to 60 result rows are sent to the AI model configured in Insight AI Settings; with a self-hosted model they stay on your infrastructure.

## AI providers (v2.96.0)

Every AI feature (Financial Health analysis, the Copilot, Studio's AI builder) uses one client, configured in **Insight AI Settings**:

| Provider (AI Provider) | AI Endpoint | AI Model (example) | AI API Key |
|---|---|---|---|
| OpenAI-compatible — Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-3.8-flash` | Google AI Studio key |
| OpenAI-compatible — OpenAI | `https://api.openai.com/v1` | `gpt-4.1-mini` | OpenAI key |
| OpenAI-compatible — Ollama (self-hosted) | `http://<host>:11434/v1` | `llama3.3` | blank |
| OpenAI-compatible — Groq, Mistral, DeepSeek, OpenRouter, vLLM, LM Studio | the provider's `/v1` URL | their model name | their key |
| Anthropic (Claude) | blank (`https://api.anthropic.com`) | `claude-sonnet-5-5` | Claude Console key (`sk-ant-…`) |
| Azure OpenAI | `https://<resource>.openai.azure.com` | your deployment name | Azure key; set Azure API Version |

Press **Test AI connection** after saving. It reports the provider, model and response time; whether the endpoint is **outside your network** (figures sent to the AI then leave your servers); and the reply, or the provider's error in plain words.

### Switching to Claude
1. **Get an API key.** Create an account at console.anthropic.com, add billing, and create a key under API Keys.
2. **Fill in Insight AI Settings:**
   - **AI Provider:** `Anthropic (Claude)`
   - **AI Endpoint:** leave blank
   - **AI Model:** e.g. `claude-sonnet-5-5`; `claude-haiku-5-5` is faster and cheaper, `claude-opus-5-5` gives the deepest analysis
   - **AI API Key:** paste the key
   - **Max Answer Tokens:** 4096
3. **Save, press Test AI connection, then try AI Deep Analysis and the Copilot.**

Model names and prices change; check Anthropic's models page before choosing.
