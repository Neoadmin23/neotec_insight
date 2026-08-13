# Neotec Insight — bilingual UI (English / Arabic)

The Reports app has a built-in **EN/AR language toggle** in the header
(the “العربية / English” button next to the workspace tabs). Switching is
instant and flicker-free because translation is part of the React app itself
(not a DOM overlay). By design the layout stays **left-to-right** in both
languages — only the visible text is translated, so report tables and the grid
are never mirrored or disturbed.

The **Ask Neotec AI** panel shares the same language preference (stored as
`nai_lang` in the browser) and answers in the selected language.

## Editing / adding Arabic wording

All UI translations live in one file in the frontend source:

    reportManager/src/utils/i18n.ts   →  the `DICT` map

It is a flat `{ 'English text': 'Arabic text' }` map. The key must match the
exact English string shown in the UI. Anything not in the map simply stays in
English (safe fallback).

To change or add wording:

1. Edit the `DICT` entry (or add a new line) in `i18n.ts`.
2. Rebuild the frontend and deploy:

       cd reportManager
       npm install        # first time only
       npm run build      # outputs to neotec_insight/public/insight
       # then on the server:
       bench build --app neotec_insight
       bench --site <site> clear-cache

Report data values (account names, report rows, dimension values) are **not**
translated — they come from your ERP data, where you control bilingual labels.

> Scope: only the frontend display strings, the AI overlay, and the AI backend
> were added/changed. The report engine (`api/report.py`, `utils/execution.py`)
> and all calculation logic are unchanged.

---

## Arabic names for accounts, dimensions & company (from the master)

Account names, cost centres, projects, departments, branches and companies are
your ERP data, so their Arabic names come from the **master records**, not the
UI dictionary. You choose which field supplies the Arabic name.

Go to **Insight AI Settings → Arabic Label Sources** and add a row per master:

| On | Master (DocType) | Arabic Name Field |
|----|------------------|-------------------|
| ✓  | Account          | account_name      |
| ✓  | Cost Center      | cost_center_name  |
| ✓  | Project          | project_name      |
| ✓  | Company          | company_name      |
| ✓  | Department       | department_name   |
| ✓  | Branch           | custom_arabic_name|

- **Master** = the record type you pick in reports.
- **Arabic Name Field** = the fieldname on that master holding the Arabic text
  (a standard field, or a custom field you created for Arabic, e.g.
  `custom_arabic_name`).

When the language is Arabic, the app shows that field's value for accounts in
the Trial Balance, and for the cost centre / project / department / branch /
company selectors (and the AI panel). The selectable value never changes — only
the displayed label — so filtering and report logic are unaffected. If a field
is blank for a record, it falls back to the normal name.

No rebuild is needed to change these mappings — they're read live from settings.
