## v2.87.8 — 2026-08-23

### Fixed: the actual root cause of the impossible-looking VAT return — found in real IRSAA ledger data, not guessed at

Traced this directly from the uploaded Q2 2026 GL export rather than theorize further. Found `ACC-JV-2026-01038` (30-06-2026): a Journal Entry debiting Output VAT 157,109.07 SAR against `21204002 - VAT Reconciliation`, and its pair `ACC-JV-2026-01035` (same date): debiting that same clearing account against Input VAT 30,595.85 SAR. Together, a completely standard quarter-end VAT close — moving the period's output and input VAT balances into a clearing account ahead of payment — done as two separate Journal Entries instead of one combined one.

`_non_invoice_vat`'s existing settlement-exclusion only catches a single voucher that touches **both** the Output VAT and Input VAT accounts directly. Neither of these two JEs does — each touches only one VAT side plus the clearing account, invisible to that check. The clearing account itself was already correctly excluded from being counted *as* Output or Input VAT (matched by `_NOT_VAT`'s "تسوية/settlement" keyword, specifically to stop exactly this kind of account's own balance from distorting the return) — but that exclusion had no way to also flag it as a *clearing* account whose ledger entries should void the settlement check on the other side. The 157,109.07 SAR debit went straight into `sales["box1"]["vat"]` as a real, standalone output-VAT reduction — precisely where the impossible negative figures the customer flagged were showing up.

**Fixed by adding a second, narrower pattern.** `_VAT_CLEARING` recognizes accounts that are VAT-adjacent *and* settlement/reconciliation-worded — not the same set `_NOT_VAT` excludes (which also catches unrelated tax types: Zakat, WHT, income tax), a genuinely distinct concept that needed its own name and its own regex. `_vat_accounts()` now returns this set alongside the output/input lists; every place that computes non-invoice VAT — the return itself, its drill-down, and the export-pack GL dump — now excludes a voucher touching a recognized clearing account the same way it already excludes one touching both VAT sides directly.

**Fixed comprehensively, not just the one call site that prompted this.** `_vat_accounts()`'s return signature changed from 2 values to 3 — grepped the whole `api/` folder rather than trust memory, and found two more callers (`api/packs.py`, twice, and `api/vat_settings.py`) that would have broken outright with an unpacking error if left unfixed. All five call sites updated consistently.

**Surfaced where the customer was already looking.** The VAT Settings screen's "VAT control accounts" section — the exact screen from the customer's own screenshot — now shows a third list: accounts recognized as VAT clearing/reconciliation, not a real VAT liability or asset in their own right. Checkable, not asserted, the same discipline already applied to the Output/Input heuristic lists on that same screen.

7 new tests (`test_vat_clearing.py`) against the exact real account name and JE pattern that caused this, extracted via AST since `vat.py`'s relative imports make a full module load impractical outside the real app package. 230 backend tests total, all green.

## v2.87.7 — 2026-08-23

### Verified: both GTPL scenarios reconciled against real filed VAT returns, for two different real companies

Extracted and cross-checked six quarters of real, ZATCA-filed VAT data (Q3 2025 – Q2 2026) for IRSAA and a second, unrelated company (شركة المسح الرقمي لتقنية المعلومات) uploaded together.

**IRSAA's government-deferral scenario — confirmed exact.** The existing `TestQ4PartialRelease.test_reproduces_filed_box_1_2` claims Amount 7,379,742.26 / Adjustment 1,740,443.00 / VAT 845,894.89 as IRSAA's real filed Q4 2025 box 1.2. Independently extracted the same three figures from IRSAA's actual ZATCA-filed Arabic PDF for that quarter — exact match, to the cent, on all three. The pure deferral engine (`utils/gtpl_core.py`, deliberately frappe-free specifically so it can be checked against real filed history without a site) reproduces a real filing precisely, not just plausibly.

**The "no GTPL rule" scenario — previously only tested with a synthetic placeholder, now confirmed against a real second company.** The second company's real, official ZATCA-filed form has no box 1.2 line at all — not empty, structurally absent, confirmed by the form's own printed totals adding up cleanly without it and by the form's own screening question ("do you have government-rate supplies?") going unanswered where IRSAA's identical question is answered yes. Added `TestNoRuleAgainstARealSecondCompany`, asserting the routing logic against this company's actual filed total (3,000,053.87 SAR, entirely in box 1, nothing in box 1.2) rather than only a placeholder customer name.

**A real data-organization issue found and named, not worked around.** The uploaded Q2 2026 folder mixes files from both companies — the "VAT Breakdown" workbook in that folder is IRSAA's own data (same Water Authority customer, same ~7.6M total as IRSAA's Q1 breakdown), while the "VAT Return" and official form in the same folder belong to the second company entirely (3.0M total, different VAT number). Flagged directly rather than silently pairing mismatched files into a reconciliation that would have looked verified without actually being consistent.

56 GTPL tests (1 new), 223 backend tests total, all green.

## v2.87.6 — 2026-08-22

### Added: Account, Voucher, Against, and Cost Center columns in the transaction drill-down

The transaction list only showed Date, Remarks, Amount, and Open. Added the fields the customer's own Excel process shows for every row — Account, Voucher (type + number), Against Account, and Cost Center — matching what's actually useful to see, especially now that a line can bind an entire account group (v2.87.4): a group-bound line's transactions can span several different leaf accounts, and knowing which specific one each transaction hit is exactly the case this was missing.

**Fixed the source, not just added a display column.** `fetch_binding_gl_rows` now fetches `account`, `cost_center`, `project`, `remarks`, and `against` in its one query — previously the drill-down re-queried GL Entry per surviving voucher afterward, `limit_page_length=1`, which for a multi-leg voucher risked returning whichever leg the database happened to list first, not necessarily the one this specific binding actually matched. That re-query is gone; every field now comes from the same row the amount itself was computed from, so there's no chance of a transaction's displayed account disagreeing with the account its amount was actually attributed to.

`list_binding_transactions` (the shared pure function, already used by the "amounts must sum to the total" test) carries the new fields through with `.get(...)` defaults, so a caller that still builds rows the old way keeps working with blank display fields rather than an error — checked directly by a new test. Override-sourced rows (Tier 2 manual classifications, which never go through a binding's own fetch) get the same fields from their own widened query.

Widened the drill-down panel (480px → 920px max) and added horizontal scroll to the transaction table specifically, so the extra columns don't cramp the account/bank breakdown shown above it.

2 new engine tests. 222 backend tests total, all green. Frontend typechecks clean and builds.

## v2.87.5 — 2026-08-22

### Added: open the individual transactions behind any figure, not just which bank fed it

The bank-breakdown drill-down (v2.86.1) showed which bank accounts contributed to a line's Actual figure, but not the individual transactions themselves — the customer's own Excel process shows a real transaction list (date, remarks, amount) for any figure, and the app's drill-down should give at least that, with an advantage Excel can't: an **Open** link straight to the real Payment Entry or Journal Entry in the desk.

**`list_binding_transactions()`** — a fourth consumer of `filter_and_sign_row`, the shared exclusion/sign rule already behind `attribute_binding_monthly` and `bank_breakdown_monthly`. Extracting that rule once, rather than three separate copies, is what guarantees the transaction list shown to a user always sums to *exactly* the total already displayed above it — the same reasoning this app has applied to every other shared calculation this session, applied here before it could drift.

**`list_line_transactions()`** — the API layer, with two details worth naming: manually-tagged Overrides get their own pass, since a voucher claimed by Tier 2 never went through a binding's own GL fetch and would otherwise be invisible in its own drill-down; and remarks/counterparty are enriched via GL Entry's real `against` field (not `against_account` — the exact naming mistake fixed in v2.87.1, not repeated here).

**Frontend**: a "Show transactions" toggle inside the existing bank-breakdown drill-down, each row carrying date, remarks, signed amount, and an Open link built from Frappe's own desk URL convention.

4 new engine tests (`TestListBindingTransactions`), including one confirming the listed transactions' amounts sum to exactly what the summary total already showed — the correctness guarantee the shared `filter_and_sign_row` extraction exists to provide. 220 backend tests total, all green. Frontend typechecks clean and builds.

### Investigated: a large reconciliation residual reported from a live screenshot

Traced rather than guessed at. The displayed Bank Beginning/End of Month row is built only from currently-configured Lines; the reconciliation residual independently checks against the *full* real bank ledger for the same period, regardless of whether a transaction is bound to any Line yet. A large residual while Line Setup is still in progress is the mechanism doing its job — showing how much real cash movement isn't accounted for yet — not a computation defect. Should shrink toward zero as more of the categories get bound. Noted as a reasoned assessment from reading the code, not a live-data-verified diagnosis, since no live site was available to confirm directly.

## v2.87.4 — 2026-08-22

### Added: bind a whole account group, not just one leaf account at a time

Account Bindings could only pick a single leaf account — a line that genuinely belongs to a whole branch of the chart of accounts (e.g. "Payment To Supplier" against an entire Accounts Payable group) needed one binding row per leaf, and a new sub-account added later needed a new binding row added by hand to match.

**`resolve_binding_accounts()`** resolves a bound account live, every run — a leaf resolves to itself (every existing binding's behaviour is unchanged), a group resolves to its current leaf descendants via the Account tree's nested-set (`lft`/`rgt`) bounds, not a stored snapshot. An account added under the group after the binding was saved is picked up automatically next time the report runs, the same "live group" principle the P&L engine already uses elsewhere in this app — reimplemented here standalone, not imported, so the isolation boundary holds.

**A real bug caught by the test written for this, before it ran against real data**: the first version called `frappe.db.get_value()`, inconsistent with `frappe.get_value()` used everywhere else in this file. Caught immediately by the test harness, fixed before anything else built on top of it.

**Transfer detection generalized properly, not patched around.** `classify_voucher_leg_group()` handles the case where a voucher touches more than one leaf of the *same* bound group — those legs are all "mine," not each other's "other leg." `classify_voucher_leg` (the original, already-tested single-account function) is now a thin wrapper around it, proven identical by a direct test comparing both against the same inputs, so the two paths can't silently drift apart the way earlier duplicated logic in this app has before.

**Frontend:** `LinkField` gained an `allowGroupSelection` prop, off by default — every other caller (VAT settings, etc.) is unchanged. On specifically for Cash Flow Forecast's Account picker, where a group node now shows a "Use group" button alongside its normal drill-in behaviour.

62 engine tests (9 new — including one proving a group binding actually queries every one of its live leaves, not the group name itself, which would silently match nothing since a group account never carries a balance). 211 backend tests total, all green, all correctly attributed by class this time — checked by count, the standing discipline after it slipped four times earlier this session. Frontend typechecks clean and builds.

## v2.87.3 — 2026-08-22

### Added: the config backup tool now covers every Insight doctype, and can't silently miss one again

Audited the "Backup" tool against the app's real doctype folder — it turned out to be a hand-maintained config export/import, not a full backup, with the doctype list independently duplicated in **three places** (a hardcoded dict inside `export_configuration`, a separate `_IMPORT_ORDER` list, and the frontend's `AREAS` array). The audit found **7 pre-existing doctypes already missing** before any of this session's own Cash Flow Forecast doctypes were even counted: the entire Allocation Rule config, `Insight Menu Settings` (a site's saved nav layout — arguably the single most disruptive one to lose), GTPL rules, Account Tags, Report Schedules, Translation Overrides, and Studio Reports.

**Fixed the cause, not the symptom.** New `utils/config_backup_registry.py` is now the one place a doctype gets registered — `CONFIG_REGISTRY` (portable setup, exported/restored) or `EXCLUDED_FROM_CONFIG_BACKUP` (site-specific transactional history, with a required real reason, not a placeholder). `export_configuration`, `import_configuration`, `config_section_counts`, and the frontend's checkbox list (now fetched from a new `config_areas()` endpoint instead of its own hardcoded array) all derive from this single registry.

**The actual enforcement mechanism**, not just a cleanup: `test_config_backup_registry.py`'s `TestFullCoverageAgainstRealDoctypeFolder` scans the live `doctype/` folder and fails if anything is neither registered nor explicitly excluded — the next doctype someone adds and forgets to triage gets a failing test, not three more releases of silence. A live diagnostic (`check_config_backup_coverage()`) runs the identical check against an installed site's actual `DocType` table, for custom doctypes a specific deployment might add outside this app's own source.

Two exclusions are marked lower-confidence rather than asserted as fact (`Insight Payment Order`, `Insight VAT Adjustment`) — inferred from their names, not verified against how they're actually used, since I don't have deep context on either. Flagged for the app owner to confirm, not presented as certain.

`import_configuration`'s Single-doctype handling was also generalized while this was being fixed — the old version only ever restored `Insight AI Settings` by name; `Insight Menu Settings` and `Insight Cash Flow Settings` (also Singles) would have silently never come back on a restore. Now loops every `is_single` entry in the registry.

12 new tests. 189 total at this point in the session, all green.

### Added: import already-classified history into Cash Flow Forecast, instead of re-classifying it by hand

For a customer whose existing process has already manually classified thousands of bank transactions (via the same remarks-reading judgment the Classification Queue now automates going forward) — bring that history in directly as Overrides, rather than re-doing the same work one row at a time.

Two-step, deliberately: **preview never writes anything.** It parses the uploaded workbook and matches each row's category label against real, existing Lines, reporting matched/already-classified/unmatched counts — with every unmatched category named and counted, not silently dropped, so the user knows exactly which Lines to create before anything commits. **Commit re-parses and re-matches from scratch** rather than trusting a stale preview held in the browser, so what gets written always reflects the current Line list even if one was renamed in the few seconds between preview and commit.

The header row is found by content (scanning for columns containing both "Voucher No" and "New Class"), not assumed to sit at a fixed row — verified against the real customer workbook this feature was built from, which has it at row 4 with trailing spaces on several headers ("Transaction Type ", not "Transaction Type"). 13 engine tests using a synthetic workbook (so tests don't depend on an uploaded file being present), plus direct verification against the real file: parsed all 2,209 real rows correctly, matched a 3-category sample at 597 matched / 1,612 unmatched with an itemized per-category breakdown.

Follows this app's own established upload pattern (`api/report.py`'s `import_map_sheet`: file sent base64-encoded through the standard REST envelope, decoded and parsed server-side with `openpyxl`) rather than inventing a new one — same shape, no shared code, since Cash Flow Forecast's isolation boundary still holds.

202 tests total, all green. Frontend typechecks clean and builds — `CashFlowForecastTab` grew to 30kB with the import modal bundled in.

## v2.87.2 — 2026-08-22

### Fixed: `mine_rules` crashed on first use — 500, "unsupported operand type(s) for /: 'str' and 'int'" — and a second, silent bug found by sweeping for the same pattern

A different bug class from the last two: not a wrong field name, but a wrong assumption that Python type hints (`min_purity: float = 95.0`) get enforced when a whitelisted method is called over HTTP. They don't — this site does not auto-cast form/query parameters to a function's annotations, so `min_purity` arrived as a string and `min_purity / 100` crashed. Fixed by explicitly casting with `cint`/`flt` at the top of the function, the same pattern every other numeric parameter in this feature already used — `mine_rules` and (see below) `list_lines` were the two spots that hadn't.

**Swept both API files for the same pattern rather than patch only the one that crashed.** Found a second instance in `list_lines(include_inactive: bool = False)` — and this one is worse than a crash, because it never raised an error. The frontend always sends an explicit `0`/`1`, which arrives as the string `"0"`. `if "0":` is `True` in Python — a non-empty string — so every call to this endpoint showed inactive lines regardless of what the caller actually asked for, silently, since the code path that runs is indistinguishable from correct behavior until someone notices a line they deactivated is still showing. Fixed with the same `cint()` cast.

Checked every other numeric parameter across both files (`get_budget_grid`, `save_budget_grid`, `run`, `list_unclassified_transactions`) — all already cast their `fiscal_year`/`limit` parameters explicitly at the point of use, from earlier work in this feature; only the two added in v2.87.0 had skipped it. Also defensively cast `confirm_classification`'s `confidence` parameter, lower-risk (Frappe's own DocField type coercion on save is more reliable than whitelisted-method parameter type hints, so this one likely wasn't actually broken) but cheap to close off regardless.

177 tests unchanged — both bugs lived in the same untested DB-facing orchestration layer named as the open gap in the last three point releases. Four production bugs in that layer across five point releases now (v2.86.2, v2.86.3, v2.87.1, v2.87.2) — the standing recommendation stated plainly rather than repeated as a footnote: this layer needs validation against a real bench before the next feature builds on top of it, not another individual patch after the next report.

## v2.87.1 — 2026-08-22

### Fixed: `list_unclassified_transactions` crashed on the very first Queue load — 500, "Unknown column 'against_account' in 'SELECT'"

GL Entry's real column is `against` (Data, comma-separated other-side accounts/parties) — labeled "Against Account" in the Frappe UI, which is exactly how `against_account` got requested as a fieldname instead of the real one. Third time this specific bug shape has hit this feature in production: v2.86.2 assumed a `company` column on Fiscal Year, v2.86.3 assumed `year_start_date` on Company, this one assumed `against_account` on GL Entry — three different fields, three different doctypes, same root cause every time: a field referenced without checking it against the real schema first.

This one was avoidable more cheaply than the first two. `report.py`, `ageing.py`, and `packs.py` — already in this codebase — all reference GL Entry's real `against` field correctly. Grepping the existing codebase for how a field is already used, before writing new code that references it, would have caught this before it ever ran against a live site. That's now the standing rule for any new GL Entry (or other core doctype) field reference in this app: check an existing correct usage first, don't infer a fieldname from its UI label and assume.

Fixed by requesting the real `against` column and renaming it to `against_account` immediately after the fetch — every downstream consumer (the classification engine's transaction dict, the Queue's API output, the frontend's `QueueRow` type) needed no changes, since none of them ever cared where the value came from, only its key name once received.

Confirmed no other file in the app has this same mistake — grepped for `against_account` across the whole codebase; only the one call site had it. 177 tests still green, unchanged, because this bug lived entirely in the same untested DB-facing orchestration layer flagged as an open gap in the two previous point releases. That gap is the actual thing to fix next, not another individual field name.

## v2.87.0 — 2026-08-22

### Added: Cash Flow Classification — Phase B/C/D of the build spec, the tiered decision cascade

Implements `Cash_Flow_Classification_Final_Verdict_and_Build_Spec.docx`'s architecture: Account Binding first (already built, untouched), then a learned rule, then a human via the Classification Queue — whichever tier resolves it, the reconciliation residual still checks the total independently, same as always. Same isolation boundary as the rest of Cash Flow Forecast.

**New doctype `Insight Cash Flow Classification Rule`** — full governance lifecycle (Candidate → Under Review → Approved → Active → Suspended/Retired), enforced by the controller as an explicit transition table, not left to the UI to get right. A rule cannot reach Active without having been Approved by a named person first — checked in `validate()`, not assumed.

**`Insight Cash Flow Override` extended** with provenance fields (`decision_kind`, `suggested_by_rule`, `suggested_line`, `confidence_at_decision`) rather than inventing a parallel data model — a confirmed classification, whatever tier produced it, was already the single source of truth this doctype represented; it just didn't used to say where the suggestion came from.

**The engine** (`utils/cash_flow_classification.py`, standalone, 31 tests):
- `infer_transaction_type` — the verified 98.0% structural debit/credit rule for Column E. Deliberately does not attempt the 45 exceptions (reversals, contra entries) — the build spec is explicit those need a human characterizing them first, not a guessed pattern.
- `resolve_classification` — High/Medium/Low/Conflict tiering. Conflict always overrides confidence: two rules disagreeing about the answer go to a human together, regardless of which one scores higher — a 99% match does not get to steamroll a 51% one it disagrees with. Caught a real gap while wiring the frontend to this: the resolved suggestion didn't carry which rule actually won, only its target line — without that, a Queue confirmation could never credit or correct the rule that produced it. Fixed before it shipped, both the function and its tests.
- `mine_candidate_rules` — the exact backtested configuration (2-4 word phrases, 95% purity) that produced 96.8-100% precision on the real, voucher-grouped, leakage-safe backtest two turns ago. Mines Candidates only, Remarks-only by default — Against Account is never auto-selected, matching both the build spec's caution and this module's own backtest finding that combining it sometimes hurt precision rather than helping.
- `update_rule_stats` — rolling precision from confirmed/corrected decisions only; a suggestion nobody has acted on yet moves precision in neither direction.

**The Classification Queue (Phase C)** — `list_unclassified_transactions` finds every real cash-leg transaction not already covered by an Account Binding or a prior Override, for the period, excluding internal transfers the same way every line's Actual does. Each row carries its tier-labeled suggestion; the frontend's new Classify tab shows Confirm / Change (pick a different line) / Reject, plus batch-confirm restricted to same-period high-confidence rows with a visible count before committing.

**Rule Review (Phase D)** — a Rules panel listing candidates with their evidence (support, precision, real sample transactions from the mining pass) and the only legal next actions for that status, matching the doctype's own transition table.

177 backend tests total, all green — 146 existing, 31 new. Frontend typechecks clean against baseline and builds; `CashFlowForecastTab` grew from 13kB to 26kB with the new Classify view bundled in.

**Not built, honestly:** the E-column exception layer (45 real reversal/contra cases, unmodeled by design — see above), and end-to-end orchestration tests for the new API layer (`list_unclassified_transactions`, `confirm_classification`, `mine_rules`) — same category of gap as the rest of this feature's DB-facing code, needing a live bench to close properly rather than another fake-frappe harness.

## v2.86.7 — 2026-08-22

### Added: real test coverage for `fetch_binding_gl_rows`'s filter construction

Closed the specific gap flagged at the end of v2.86.6 rather than let it sit as a known risk. Extended `_load_engine()`'s fake-frappe harness with a configurable `get_all_impl`, the same pattern already used for `get_value_impl` — lets a test capture the actual filters dict a DB-facing wrapper builds, instead of trusting it by inspection. That standard already missed two production bugs on this exact module (v2.86.2, v2.86.3); no reason to trust it a third time on the cost-centre multi-select filter added this same session.

`TestFetchBindingGlRowsFilters` (7 tests): confirms an empty/missing `cost_centers` list produces no `cost_center` filter key at all — not a `cost_center: ['in', []]`, which would silently match zero rows instead of applying no restriction; confirms multiple cost centres map into one `['in', [...]]` filter in a single query, not one call per cost centre, which is the actual feature v2.86.6 was for; confirms company/project filters only appear when given; confirms the party filter requires both `party_type` and `party` together, never a half-specified match on one alone. Still genuinely untested: the real database round trip — these tests prove the filters dict is built correctly, not that `frappe.get_all` does the right thing with it. That gap is named, not implied closed.

### Fixed: the same class-declaration-swallowed-by-an-edit mistake, a fourth time

`TestBalanceCarry`'s declaration line was dropped again while inserting the new test class above it — identical shape to v2.86.1, v2.86.2, and v2.86.3. All tests still ran and passed regardless of which class they were nested under, same as every previous occurrence; caught this time, as it should have been from the first time, by counting declared `class Test` lines against classes that actually appeared running their own tests in `-v` output, not by re-reading the diff and assuming it looked right. 53 engine tests, 146 total, all green, all 13 classes correctly attributed this time — verified by count, not by eye.

## v2.86.6 — 2026-08-22

### Added: Cost Center is now a multi-select — mapped once, not once per department

A binding's Cost Center was a single Link — a line that legitimately spans several departments (e.g. a company-wide "Kafaa Project Allowance" split by cost centre) needed one duplicate binding row per department, same account and direction mode repeated each time. Replaced with a proper Frappe `Table MultiSelect` (`Insight Cash Flow Line Binding` → `cost_centers`, backed by a new bridge doctype `Insight Cash Flow Binding Cost Center`) — one binding row now reads from every listed cost centre in a single `cost_center IN (...)` query.

Fetching a Table MultiSelect field correctly requires `frappe.get_doc()`, not a flat `frappe.get_all()` — a raw list query can't see a child table's own child rows. `run()`'s binding fetch switched accordingly; the pure engine functions (`attribute_binding_monthly` and friends) were untouched, since cost-centre filtering happens entirely at the SQL layer, not in aggregation.

Extended `api/vat_settings.py`'s generic `link_options`/`_LINKABLE` lookup to cover `Cost Center` (same tree shape as `Account`, reused rather than duplicated — this is shared UI plumbing, not P&L/report-engine logic) so the Cost Center picker gets the same tree+search experience as the Account picker added in v2.86.4. Widened `LinkField`'s type union additively; no existing consumer's behavior changed.

### Fixed: the Cost Centre/Project dimension was wrongly restricted to Cash In lines only

Flagged two turns ago, not actually fixed until now: the Line Setup mapping showed Cash Out lines (Kafaa Project Allowance, Municipalities Project Per Diem) are just as department-specific as Cash In lines are — the restriction to Cash In was arbitrary, not a real constraint. Removed from the doctype's `depends_on`, the Python validation, and the frontend's conditional rendering. A Cash Out line can now set a Dimension field and get the same "binding must carry a value" validation Cash In lines always had.

139 tests, all green — no engine-level test changes needed since cost-centre filtering lives entirely in the DB-facing layer this module's pure-function suite deliberately doesn't reach. Frontend typechecks clean and builds.

## v2.86.5 — 2026-08-21

### Fixed: `save_line` failed with "Document has been modified after you have opened it" on a save's very first attempt

`save_line` fetched a fresh doc, then called `doc.update(data)` with the entire frontend payload — including `modified`, `owner`, `creation`, `docstatus`, every metadata field `list_lines()`'s `doc.as_dict()` had put into the frontend's `editing` state in the first place. `.update()` overwrote the freshly-fetched doc's real `modified` with whatever stale value was sitting in that browser state, and Frappe's own optimistic-lock check then correctly rejected the save as a conflict — even on a save that was the very first attempt in that session, because the "conflict" was entirely an artifact of copying a field that was never meant to be writable. Fixed with an explicit whitelist of the fields this screen actually lets someone edit; `doc.set()` per field, never a blind `.update(data)`.

Also hardened the two things that made this worse in practice rather than just annoying: the Save button had no in-flight guard, so a double-click or a slow network plus an impatient second click could fire two saves against the same stale state — added a `savingLine` guard and disabled state. And the error surfaced as an untranslated background toast, leaving the form stuck on the same stale data with no obvious next step — now shown inline with a **Reload** action that re-fetches the line fresh.

### Added: an actual place to enter Budget

The Statement view's Budget column was read-only — `save_budget_grid`/`get_budget_grid` existed and worked since v2.86.0, but nothing in the frontend ever called them. There was no way to enter a budget figure anywhere in the app. New **Budget** tab: every active line, every month, editable, same blank-vs-zero contract as the allocation grid this was modeled on (a cell left blank was never entered and stays that way; a saved 0 is a real zero). Noted plainly in the UI, not just the changelog: clearing a previously-saved cell back to blank does not delete the underlying record — `save_budget_grid` only inserts or updates, never deletes — enter 0 explicitly if that's the intent.

## v2.86.4 — 2026-08-21

### Added: proper account picker on the Account Bindings table — no more raw text input

The Line Setup binding table's Account field was a plain `<input>` — no validation, no visibility into the chart of accounts, easy to mistype a name and get a silent zero with nothing telling you why. Replaced with `LinkField`, the tree+search picker already used elsewhere in the app (VAT settings, mapping rules) — reused as-is, no changes needed to it or its backend (`vat_settings.link_options`, a generic doctype-driven lookup, not P&L/report-engine code, so this doesn't cross the isolation boundary). Blocks selecting a group node, same as everywhere else it's used — a group account can't hold a balance, so letting one be picked here would silently resolve to nothing, exactly the failure mode this feature's reconciliation residual exists to catch, closed instead at the point of entry.

Company, Cost Center, and Party fields are still plain text for now — `LinkField`'s backend spec only covers Account, Customer, and Customer Group today. Worth doing next if it'd help; scoped out of this pass since it wasn't what was asked.

## v2.86.3 — 2026-08-21

### Fixed: nav button showed the raw internal key ("cash_flow_forecast") instead of its label

`mergeMenu()` merges a site's previously-saved menu layout with the current build's catalog of tabs. A tab the saved layout has never seen — true for Cash Flow Forecast on any site that customised its menu before v2.86.0 shipped, which is most real sites — falls through to an auto-append path that used the section's raw KEY (`cash_flow_forecast`) as its display label instead of the actual one (`Cash Flow Forecast`). `CATALOG_LABELS` already existed for exactly this problem at the tab level; nothing equivalent existed for sections. Added `SECTION_LABELS` and used it in the fallback.

### Fixed: `run()` still crashed on the same real site — 500, now "Unknown column 'year_start_date' in 'SELECT'"

v2.86.2 fixed the wrong query (Fiscal Year.company) but copied only the query shape from `fiscal_year.py`, not its resilience — the real `get_company_fy_start_month` wraps the read in `try/except Exception: pass` and falls back to January; `resolve_company_fy_start_month` didn't, so when this site's schema *also* doesn't have `Company.year_start_date` (a second, different missing column), it crashed the same way.

Fixed by wrapping the query in the same try/except. Worth stating plainly rather than quietly working around: `fiscal_year.py`'s identical fallback means every OTHER report in this app has likely been silently treating every company on this site as January-start this whole time, with no visible error — this function now matches that behaviour rather than being the one place that crashes instead of defaulting. That silent fallback elsewhere is a real thing worth this site's owner knowing about directly; it's not something to guess a fix for from inside an isolated feature that isn't supposed to share code with the rest of the app in the first place.

Added `TestResolveCompanyFyStartMonth` (4 tests) — extended `_load_engine()` to accept a configurable `get_value_impl` so a test can simulate the exact `OperationalError` this hit in production, not just a normal return value. Also re-caught the same class-declaration-swallowed-by-an-edit mistake as v2.86.1 and v2.86.2 (`TestClassifyVoucherLeg` again) — cross-checked this time by counting declared classes against classes that actually ran tests under their own name, not just grep for the string "ok". 46 engine tests, 139 total, all green, all correctly attributed.

## v2.86.2 — 2026-08-21

### Fixed: `run()` crashed on every real site — 500, "Unknown column 'company' in 'WHERE'"

Reported from production, first real run against a live bench: `_fy_start_month()` queried `frappe.db.get_value("Fiscal Year", {"company": company}, ...)` — the Fiscal Year doctype has no `company` column to filter on. Every call to `run()` failed before it could return anything, in both v2.86.0 and v2.86.1.

The unit suite (42 tests as of v2.86.1) never caught this because the broken code was 100% on the DB-facing side of the function, and this module's whole testing discipline — deliberately, everywhere else — is pure functions tested directly, DB wrappers trusted thin. This one DB wrapper wasn't thin; it was wrong, and nothing exercised it.

Fixed by copying the query shape from `utils/fiscal_year.py`'s `get_company_fy_start_month` (not importing it — isolation holds) rather than inventing one: the real source of truth is `Company.year_start_date`, not Fiscal Year. Split into `parse_fy_start_month()` (pure — extracts a month from a date/string/None, defaults to January on anything unparseable) and `resolve_company_fy_start_month()` (the actual DB read), the same pure/impure split as every other function in this module. The pure half now has 7 tests of its own. The DB-reading half still doesn't, and still can't without a live site — but the part of this bug that COULD be caught by a unit test now is.

Also fixed two copy-paste artifacts introduced while adding tests in v2.86.1: `class TestClassifyVoucherLeg` and `class TestBalanceCarry`'s declaration lines were dropped during editing, silently merging their test methods into the preceding class. All 42 tests still ran and passed either way — Python doesn't care which class a method sits in — but `pytest -k TestClassifyVoucherLeg` would have found nothing, and a future reader grep-ing for "which class tests the transfer fee scenario" would have been misled. Caught by grep-ing every `class Test` declaration against ones actually run, rather than assuming green output meant correctly organized.

135 tests total, all green, all correctly attributed to their own class.

## v2.86.1 — 2026-08-21

### Fixed: internal transfers with a bank fee were double-counted as real cash movements

Found while answering a direct question about how the feature handles internal transfers between banks. A KSA inter-bank transfer routinely carries a third leg — the SARIE fee, posted to a Bank Charges expense account, alongside the source-bank credit and destination-bank debit. The original transfer-exclusion rule required *every* other leg on the voucher to be a cash account; the fee leg broke that condition, so the transfer went unrecognised and the same money was counted twice — once leaving the source bank, once arriving at the destination — as if it were two unrelated real cash movements, while the fee itself would still correctly count as real spend if bound to a Bank Charges line.

Refactored the exclusion logic into a standalone, testable `classify_voucher_leg()`: a leg is now excluded as a transfer the moment *any* other leg is also a cash account, not only when every other leg is. Found a second, related gap while writing the tests for this: a line bound directly to a bank account (rather than to an Expense/Payable/Receivable account, the usual case) wasn't being recognised as ever touching cash at all, because the original check only ever looked at *other* legs — fixed so a leg on a cash account is always recognised as a real cash movement regardless of what else the voucher touches.

### Added, per direct customer feedback

- **Company as a dropdown**, sourced from the Company master — auto-selects when there's exactly one company, otherwise a real dropdown. Replaces the free-text field this shipped with in v2.86.0.
- **Bank account multi-select**, default "all," narrows to one or more specific banks — backed by a new `resolve_cash_accounts(..., restrict_to=...)` parameter, validated against the real Bank/Cash account list so a stale or mistyped name can't silently expand scope.
- **Internal transfers surfaced, not silently vanished** — a new panel lists every detected transfer for the period, with the KSA fee broken out as its own figure rather than folded into either the source or destination amount. Same "never absorbed silently" principle as the reconciliation residual, applied to the other place this feature quietly excludes something.
- **Click a number, see which bank accounts fed it** — every Actual cell is now a drill-down into its per-bank-account breakdown, for split payments and for auditing which specific bank a figure came from.

`tests/test_cash_flow_forecast_engine.py` gained `TestClassifyVoucherLeg` (6 tests, including the exact 3-leg KSA scenario), `TestBuildTransferLog` (3 tests, including the fee-detection case), and `TestBankBreakdownMonthly` (3 tests, including a split-payment case). 35 engine tests, 128 total, all green. Frontend typechecks clean against the existing baseline and builds cleanly.

## v2.86.0 — 2026-08-21

### Added: Cash Flow Forecast — direct-method, fully separate from the existing Cash Flow

New top-level nav section, own button, deliberately not a tab under Reports beside the existing indirect Cash Flow statement. Per the customer's explicit instruction: own doctypes, own API module (`api/cash_flow_forecast.py`), own engine (`utils/cash_flow_forecast.py`), own frontend folder (`features/cashflowforecast/`) — no import from `api/report.py`, `utils/execution.py`, `utils/allocation.py`, or `utils/fiscal_year.py` anywhere in this feature. See `Cash_Flow_Build_Plan.md` and `Cash_Flow_Phase2_Spec.md` for the full design history.

**New doctypes**: `Insight Cash Flow Line` (the line definition — label, direction, section, Cash In dimension field), `Insight Cash Flow Line Binding` (child table — account of any root type, direction mode, cost centre/project, party), `Insight Cash Flow Override` (Tier 2 manual voucher tagging, for the transactions a binding genuinely cannot separate — required to carry a note explaining why), `Insight Cash Flow Budget` (manual entry, same blank-vs-zero contract as the allocation Budget grid), `Insight Cash Flow Settings` (opening balance source and residual tolerance).

**The attribution model, three tiers**: (1) direct binding — account, optionally narrowed by direction mode (Net / Debit Only / Credit Only, the mechanism that lets two lines read the *same* liability account as a loan settlement and a loan draw without netting them together), cost centre/project (how department-based Cash In lines read a shared receivables account), or party (for named-individual lines sharing one account); (2) manual override, for the residue no binding rule can separate; (3) whatever neither tier claims is a visible reconciliation residual, never silently absorbed.

**Deliberate isolation cost, stated rather than hidden**: the calendar-month ↔ FY-position conversion is reimplemented standalone in `utils/cash_flow_forecast.py` rather than importing `fiscal_year.py`'s — a second implementation of the same idea, and an accepted risk of full separation. Mitigated the only way that's ever worked in this codebase: the exact January-start / April-start fixture pair that caught the original allocation-budget bug, run independently against this module's own conversion.

**Two real bugs caught while building, before either shipped**:
- The reconciliation residual was originally wired to compare the derived rollforward against itself — tautologically zero by construction, silently defeating the one check this feature exists to provide. Caught on a second read of the code, not by a test (there wasn't one yet for the orchestration layer). Fixed to check against an independently-fetched ledger balance at each month's boundary.
- Budget grid save/load and the FY-run's date range both assumed every month of a fiscal year sits in one calendar year — true for a January-start company, false for any other. Same bug shape as the original month-shift, one level up: right month, wrong year. A budget entered against "March" for an April-start company's FY2026 would have silently saved under 2026 instead of 2027. Caught before shipping by `TestFyPositionToCalendarYear`, which walks a full April-start year against hand-verified (year, month) pairs.

`tests/test_cash_flow_forecast_engine.py` — 23 tests covering the calendar/FY-position/calendar-year conversions (both fixture companies), the direction-mode split (debit-only vs credit-only vs net, and specifically that a credit row cannot leak into a debit-only line), the cash-leg rule (accrual exclusion, bank-to-bank transfer exclusion, override-claimed voucher exclusion), balance-carry, and the reconciliation residual (zero when reconciled, signed correctly for both a missing line and a double-counted one). 116 tests total, all green.

**Not yet covered**: the API orchestration layer (`api/cash_flow_forecast.py`'s `run()`) has no end-to-end test — the pure engine functions carry the test burden here the same way they do elsewhere in this app, but `run()`'s Frappe wiring needs a live site to exercise properly, the same gap noted for the v2.77.0 permission sweep. Needs validating against a real bench before this reaches customer data.

## v2.85.0 — 2026-08-19

### Fixed: hidden rows printed on the consolidated P&L

A company-wide P&L exported to PDF still listed **Net Operating Income (Before Allocation)**, **GMO Allocation** and **Sales & Marketing Allocation** — rows the screen correctly hides when no cost centre is selected.

Two independent faults, both fixed:

**The export never checked `hidden`.** The on-screen grid has skipped hidden rows since v2.62.1; the export loop in `utils/export.ts` iterated every row regardless, so Excel, PDF, Print and CSV all printed them. The printed copy is the one that leaves the building.

**The budget loader had no notion of visibility.** `_load_budget` applied no `show_when` rule, so a row hidden on the actual side still carried a budget. That is why the rows printed as Actual 0 against a real Budget figure — GMO Allocation showed 492,876 budget against nothing actual, which reads as "budgeted and not spent" rather than "not applicable at this level". The % Achieved column was computed from it.

Allocation budgets now obey the same `is_row_hidden` rule the actuals obey, resolved against the same cost-centre selection.

## v2.84.0 — 2026-08-18

### Added: an HR role that sees People and nothing else

`Insight HR` (and ERPNext’s own `HR Manager` / `HR User`) now resolve to a new `hr` role tier. A user whose only Insight access is one of these sees the **People** workspace and no other tab — no P&L, no ledger, no VAT.

**The tier is evaluated last among the named roles**, so anyone who also holds a finance role keeps that wider access rather than being narrowed to People by holding both. An HR Manager who is also an Accounts Manager is still an admin.

**Hiding tabs is the affordance, not the protection.** `_check_hr_only()` is enforced inside `_require_read()`, the choke point every financial read in `report.py` already passes through — so a People-only user calling those endpoints directly is refused, not merely un-navigated to them. This matters because HR role bundles often carry GL Entry read, which would otherwise have let them straight into the ledger. The People endpoints live in `cfo.py` and do not pass through that guard, so they keep working.

### Not included: the accruals actual/provision column

The second request — an extra column showing accrual **actuals** beside the computed provision, with a configurable account per accrual — is **not in this release**.

It needs new fields on `Insight AI Settings` (one account per accrual type: vacation, tickets, insurance, EOSB), a GL balance read per account, and a provision-vs-actual comparison in the People workspace. That is a real build, and the accruals figures feed the CFO brief and the P&L provisions. Shipping it half-verified at the end of a long session is the pattern that produced the v2.79.0 month-shift, so it is deferred deliberately rather than rushed.

## v2.83.0 — 2026-08-18

### Added: choose how ledger balances show their sign

Ledger balances printed only as `39,767.49Dr` / `1,250.00Cr`. That is unambiguous but reads as bookkeeping notation, and finance teams and auditors outside ERPNext generally expect a minus or brackets.

A **Balance shown as** control on the ledger toolbar offers five conventions:

| Style | Credit balance renders as |
|---|---|
| Dr / Cr | `1,250.00Cr` |
| Minus sign | `-1,250.00` |
| Minus sign, in red | `-1,250.00` in red |
| Brackets | `(1,250.00)` |
| Brackets, in red | `(1,250.00)` in red |

Applies to the **Accounts, Supplier and Customer** ledgers alike, and to opening balance, every transaction line, sub-totals and the report total — one helper feeds all eight places, so no view can disagree with another.

**Debit stays positive in every style.** Only the credit side changes appearance. Flipping debits as well would make an Excel export sum to something different from the same export taken yesterday.

Carries into every output. The red styles set a cell colour the shared writers already understand, so HTML, Print and PDF colour it, and Excel picks it up through the existing negative-number format. CSV keeps the plain text, since a CSV cell has no colour.

The choice is remembered per browser rather than stored on the report: it is a reading preference, not a property of the data, and a preparer who reads brackets wants brackets on every ledger every day.

## v2.82.0 — 2026-08-17

### Added: hide a row from the arithmetic, not only from the display

The **Show** control had two settings. `Only when a cost centre is selected` suppressed the display while the row’s value kept feeding every formula referencing it. There was no way to say "when this row is hidden, do not count it either".

A third option now does: **Only when a cost centre is selected — and excluded from formulas**. When hidden, the row contributes 0 to every formula that names it, and totals change accordingly.

Both behaviours are legitimate and the distinction matters:

- `cost_center` — right for a before/after-allocation line. The reader should not see a duplicated figure consolidated, but net income must still compute from it.
- `cost_center_exclude` — right for a line that only means anything for one cost centre. There, a hidden row silently inflating a total is the worst case, because the evidence for the total is invisible.

The help text under the control now states which of the two is in force, rather than describing only the first.

**Zeroed, not deleted.** An excluded row’s key stays in the formula context with a value of 0. Removing the key would make any formula naming it raise and take the whole report down; a zero contributes nothing and leaves every other row computable.

**Nothing changes for existing reports.** `always`, `cost_center` and an absent value all behave exactly as before, and an unrecognised value falls back to the per-kind default rather than excluding a row from the totals — a typo must not quietly change a figure. `tests/test_visibility.py` asserts all four cases; 21 tests there, 93 across the suite.

## v2.80.1 — 2026-08-17

### Fixed: allocation row budget printed one month late (v2.79.0 regression)

`_allocation_budget_monthly()` keyed its output by the raw **calendar** month (1–12) from `Insight Allocation Entry.period_month`. Every other row in a report keys by **FY-month position** (0–11, where 0 is the first month of the fiscal year).

For a January-start company the two look alike — "month 1" and "position 1" read the same — but position 1 *is* February. January's budget printed under February, February's under March, and calendar December (12) fell outside the 0–11 range and **vanished entirely**. That last detail is what located the fault: a shift moves a figure, only an out-of-range key loses one.

The actuals path was never affected. `_allocation_monthly()` has converted correctly since v2.62.1, which is why only the Budget column was wrong and the allocation report's own Budget YTD and Variance stayed right throughout.

Fixed by converting through `fy_month_for_calendar_month()`, the inverse the actuals path already uses. `company` and `fy_start_month_override` are threaded through `_load_budget()` to its one call site; no other caller reads them.

### Tests: the real function, not a copy of it

`tests/test_allocation_budget_month.py` extracts and executes the **actual** `_allocation_budget_monthly` body by AST, with a stub `frappe` supplying the entries. Reverting the fix fails 10 of its 12 tests.

This replaces an earlier test file that re-implemented the accumulation loop and asserted against the re-implementation. That file passed while the shipped code was broken — a mirror of a loop cannot fail when the original is wrong. It has been deleted rather than kept alongside, since a test that reports green regardless is worse than no test.

Coverage: January-start (the case that shipped, easiest to misread by eye) and April-start (position and calendar month three apart, crossing a year boundary, so an off-by-a-constant conversion cannot pass); December not dropped, on both calendars; the reported February→March symptom reproduced verbatim; multiple cost centres summing within one position; empty and explicit-zero budgets.

### Also: frontend version aligned

`reportManager/package.json` read 2.80.0 against an app version of 2.80.1. Since v2.80.0 compares the two and warns when they differ, a correct deploy would have raised a false stale-bundle banner.

## v2.80.0 — 2026-08-17

### Added: the app now tells you when the screen is out of date

The frontend bundle version and the installed Python version are read from different places and can disagree. Frappe's "Installed Apps" reports the Python version; the Insight header reports the JS bundle. A deploy that ships app code but serves a cached or stale asset bundle leaves an old UI on a new backend — and nothing compared the two.

That is not theoretical. The v2.79.1 budget-column fix lives in `RunTab.tsx`, so it was inert on a site whose backend read v2.79.1 while the header still read v2.79.0. A fixed bug looked unfixed for a full round of testing, and the only way to notice was to compare two screenshots by eye.

`navmenu.app_version` returns the installed Python version. The frontend compares it against `__APP_VERSION__`, baked in at build time, and when they differ shows a red banner across the top naming both versions and what to do.

**Loud, and not dismissible.** A stale bundle silently reinstates bugs that are already fixed, so it must not be possible to read a figure without seeing the warning. The version chip in the header also turns red — the tooltip alone was not enough, since the person who needs the message is the one looking at a wrong number.

The endpoint is deliberately unguarded: it carries no data, and a version banner has to render for whoever is looking at the broken screen, whatever their permissions.

## v2.79.1 — 2026-08-17 — deploy immediately

### Fixed: budget columns were shifted by one month

The P&L printed January's budget in the February column, February's in March, and dropped December's entirely. January read 0.000.

**Cause: budget rows were matched to display rows by ARRAY INDEX, not by key.** `run.budget?.rows[idx]` assumes the budget array is the same length and order as the display rows. The allocation rows added in v2.79.0 are built on a separate branch of the budget builder, so every row after them shifted by one. Prior-year columns were matched the same way and had the same fault.

Both now match on `row.key`, which is what every other consumer of `budget.rows` already did.

**This was invisible until one cell was edited.** Every month held 27,382, so a one-month shift looked identical to correct output. It surfaced only when a single February cell was changed to 27,000 and the figure appeared under March.

`tests/test_allocation_budget.py` budgets twelve DISTINCT values and asserts each lands in its own month, reproduces the edited-February case, and documents that index matching would have been wrong. A fixture with identical months cannot catch a shift — which is precisely why this reached live data.

### Check after deploying

Any report read while v2.79.0 was installed may have been read with shifted budget columns. Re-run and confirm January carries a figure and December shows its own.

## v2.79.0 — 2026-08-17

### Fixed: allocation rows showed 0.000 in the P&L budget column

v2.78.0 added a budget grid to the allocation rule, and the allocation report shows it correctly. The **P&L** did not: GMO Allocation and Sales & Marketing Allocation printed a budget of 0.000 against real actuals, so % Achieved was meaningless on exactly the two lines the budget had just been entered for.

The two budgets are stored in different places, and that is deliberate. A Budget Book holds one cell per P&L row per month — it cannot express "27,382 to Financial & Admin and nothing to the other five," which is the grain an allocation is actually budgeted at. So the allocation budget lives on the rule, per cost centre per month, and the P&L now reads it from there for allocation rows instead of looking for a Budget Cell that was never going to exist.

**Filtered to the same cost centre as the run.** With one cost centre selected the row shows that centre's budget; consolidated, every centre's budget sums — matching the actual, which consolidated shows the whole pool spread across all of them.

A month with no budget stays blank rather than showing zero, preserving the same distinction the Budget Book makes between "budgeted nil" and "not budgeted". A missing rule or a pre-upgrade table yields no budget rather than raising: one broken rule must not take down a P&L.

## v2.78.0 — 2026-08-17

### Added: budget for cost-centre allocations

An allocation report showed only what the engine derived. There was no budget to compare it against, so the allocation lines were the one part of the P&L with no Actual-vs-Budget.

**Budget is entered by hand and never derived.** Confirmed against two live sheets: GMO budgets a flat 27,382 to every cost centre while its actuals — split by head count — come out at 22,900 / 14,313 / 5,725. Sales & Marketing budgets an identical figure across three cost centres each month while Audit carries a flat 10,000. No driver produces those numbers, because a budget is a decision that was signed off, not a calculation. Re-deriving it from the actual driver would produce a figure nobody agreed to, and it would move every time the driver moved — which is exactly what a budget must not do.

- `budget_amount` on `Insight Allocation Entry`, stored for **every** basis. Unlike `driver_value` and `amount`, which are mutually exclusive, budget is an independent input: a head-count cost centre carries one too.
- A third grid in Data entry covering every cost centre in the rule, kept separate from the two input tables because it is a different kind of number — those drive a calculation, this one is only ever compared against its result. Separate tables are what stop a budget being typed into a driver cell.
- **Budget YTD** and **Variance** rows on the allocation report, per cost centre. Variance is positive when the allocation exceeds budget, since an allocation is a cost; roll-ups are computed server-side so screen and exports agree on rounding and sign.
- Both rows render only when a budget has actually been entered. Two rows of zeros on a rule with no budget would read as "budget is nil", which is a different statement from "no budget was set".

A row carrying only a budget is no longer treated as empty and deleted on save.

## v2.77.0 — 2026-08-17 — security audit, clean sweep

All 225 whitelisted endpoints enumerated by AST and classified. Final state: **213 guarded, 12 intentionally open, 0 unguarded.**

### Three write-side gaps, fixed

**`_set_clearance` wrote to a caller-named doctype.** The serious one. `confirm_match` takes `voucher_type` straight from the request and passes it to `frappe.db.set_value`, which performs no permission or doctype check at all — so any authenticated user could stamp a `clearance_date` onto any doctype having that column. Now allow-listed to Payment Entry, Journal Entry, Sales Invoice and Purchase Invoice, with a write check on the resolved doctype. An allow-list rather than a permission check alone: there is no legitimate fifth value.

**`confirm_match` and `unmatch`** submit and mutate Bank Transactions; both now require write on Bank Transaction.

**`stage_draft_payment_entry`** inserts its Payment Entry with `ignore_permissions=False`, so Frappe checks that — but it also mutates the Insight Bank Slip through `db_set`, which checks nothing. Both doctypes are now checked before any write.

### 106 read endpoints guarded

Every remaining financial read now calls `_require_read()`, which tests `GL Entry` read permission — inheriting ERPNext's own role configuration rather than inventing a second permission model. `@frappe.whitelist()` requires a login, not a role, so these were reachable over `/api/method/...` by any authenticated user, portal accounts included.

### Twelve endpoints left open, deliberately

`get_csrf`, `get_menu`, `get_brand`, `arabic_labels`, `company_branding`, `list_letter_heads`, `resolve_letterhead`, `list_models`, `list_ollama_models`, `list_quick_links`, `insight_get_access_profile`, `insight_has_group_access`.

These are the CSRF handshake, shell chrome and print/AI configuration — none returns a financial figure, and guarding them would prevent the app from rendering at all for a user who is about to be told they have no access. `insight_get_access_profile` is the clearest case: it reports what the user is allowed to see, so gating it behind that same permission would hide the answer to its own question.

### On the earlier report

An earlier sweep reported 25, then 18 "missing permission checks." Most were pattern-matching artefacts: `frappe.cache().set_value` matching a `set_value` search, every `ignore_permissions=False` matching an `ignore_permissions` search, `frappe.only_for("System Manager")` missed by a guard-name list, and `frappe.delete_doc()` without a bypass — which Frappe already checks. The three above were the real ones.

All suites green: 76 tests.

## v2.76.2 — 2026-08-17

### Fixed: two allocation rows for different pools showed identical bare 0.000s once any cost centre was picked, and the row loop could crash

The "Show" control asked for on this version's allocation rows already exists on **every** row kind in Studio — source, formula, and section rows all carry it (the block guarded by `row.kind !== 'allocation'` in the row editor), allocation rows carry their own copy. Nothing to add there.

The real defect was in how "a single cost centre is selected" gets applied to an allocation row specifically. Two allocation rows drawing from different Allocation Rules (e.g. GMO pool vs. Sales & Marketing pool) both default to *Only when a cost centre is selected*. Select any single cost centre and — regardless of whether that rule's pool has anything to do with the selected centre — both rows became visible, and a rule with nothing to allocate to that centre prints a bare `0.000` indistinguishable from a rule that genuinely has a zero that month. Two unrelated rows, same number, no way to tell which zero meant what.

`_allocation_monthly` now returns `(monthly, applies)`, where `applies` is False only when a single cost centre is selected and that centre is neither a driver nor the credit-back target of that rule's pool. `is_row_hidden` takes this as `cc_applies` (default `True`, so every pre-2.76.2 call site behaves exactly as before) and uses it to *narrow* the already-hidden-unless-selected default for allocation rows only — it can turn a visible allocation row invisible when its pool doesn't touch the selected centre, never the reverse, and it never touches a row explicitly set to *Always* or a non-allocation row.

This landed alongside an incomplete version of itself that would have crashed every single report run: `cc_applies` was only assigned inside the `allocation` and fallback `else` branches of the row loop, so a `source` or `formula` row raised `UnboundLocalError` before ever reaching `is_row_hidden` — and `is_row_hidden`'s own signature hadn't been updated to accept the extra argument at all, so even the allocation branch would have raised `TypeError`. Caught before release: `cc_applies = True` is now set once at the top of every row iteration, and `is_row_hidden` takes `cc_applies` as an explicit fourth parameter.

`tests/test_visibility.py` gained a `TestCcApplies` class: an irrelevant pool hides even with a cost centre picked, a relevant one still shows, the old 3-argument call sites keep their exact old behaviour, `show_when='always'` is never touched by `cc_applies`, consolidated runs are unaffected, and non-allocation rows are unaffected even if a caller mistakenly passed `cc_applies=False` for one. 76 tests total, all green.

## v2.76.1 — 2026-08-17

### Fixed: an unmapped or deleted-account row rendered as a silent zero

The root cause 2.76.0 already diagnosed in its own note below — "source rows bound to no accounts sum to zero, which renders every row at 0.000 in ~10ms" — was never actually fixed. A row with nothing bound was indistinguishable on screen from a row with genuine zero activity. On a consolidated run, several such rows together looked exactly like the reporting engine itself was broken.

Three ways a row ends up unbound, all now surfaced instead of silent:

- **Never mapped.** No Account Flag Mapping row exists for the flag at all. `flag_binding_meta` previously returned `{}` outright when the *report* had zero mapping rows, and simply omitted a flag with zero mapping rows even when other flags in the same report had some — either way, the row vanished from the response instead of reporting as unbound.
- **Mapping deleted.** The row was mapped, then every Account Flag Mapping row under it was removed (reassigned elsewhere, or deleted by mistake).
- **Account deleted.** A directly-bound account was removed from the chart of accounts. The mapping row still exists and still feeds the SQL `IN (...)`, it just can never match a GL entry again — the row quietly loses whatever that account used to contribute, with nothing on screen to say so.

`flag_binding_meta` now takes the report's row definitions and reports on every source row's flag, not only ones with a surviving mapping record, and adds `has_binding` and `missing_accounts`/`missing_count` so the three cases above are distinguishable. The Run screen shows a warning badge — not the faded, hover-only style used for the informational live-group badge — on any source row that resolves to zero accounts, and a smaller badge on a row that still resolves but is carrying dead account references.

Deliberately **not** changed: the SQL still includes dead account names in the `IN (...)` clause rather than silently dropping them. Dropping them changes which accounts a saved report reads without an edit to the report; surfacing the gap and leaving the fix to whoever owns the mapping is the safer half of this change to ship together with the visibility fix. `tests/test_flag_binding_meta.py` covers the three unbound paths and asserts the shape returned for "never mapped" and "report has zero mapping rows" is identical, so the frontend never needs to special-case which one happened.

## v2.76.0 — 2026-08-06

### Added: "Show only when a cost centre is selected", on any row

Re-added deliberately, with the tests that were missing the first time.

The case it exists for: with credit-back on, an allocation moves cost between cost centres and leaves the company total unchanged. Run consolidated, the allocation rows hide themselves and the report prints "Net Operating Income (Before Allocation)" and "Net income" as the same figure, with the allocation that explains the gap invisible between them. Arithmetically right, and it reads as a mistake.

Set the before-allocation row to *Only when a cost centre is selected*: one clean figure consolidated, the full walk when a cost centre is chosen.

**The default differs by kind, and that is the whole safety property.** Allocation rows default to `cost_center`, every other kind to `always`. An untouched report renders exactly as it did before upgrade. This shipped once with a shared default and blanked rows in reports nobody had edited — `tests/test_visibility.py` now asserts the untouched case for every row kind, consolidated and filtered.

The decision moved into `is_row_hidden()`, taking plain values so it runs without a site. Writing that test immediately found a second fault: a non-string `show_when` in a stored definition raised `TypeError` and would have taken down the entire report run rather than one row. Now falls back to the per-kind default.

Hidden rows still feed formulas; only the display is suppressed.

### Note on 2.73.0–2.75.1

The blank consolidated report seen on one site was **not** caused by this feature. A file-by-file audit against the working 2.72.0 build showed the P&L execution path byte-identical apart from comments. The cause was missing Account Flag Mapping records — source rows bound to no accounts sum to zero, which renders every row at 0.000 in ~10ms. 2.75.1 removed this feature on that mistaken diagnosis.

## v2.75.1 — 2026-08-06 — REGRESSION FIX, deploy immediately

### Fixed: rows vanished from consolidated runs

The visibility check that hides allocation rows on a consolidated run had been hoisted OUT of the allocation branch, so it evaluated for every row kind. Any row carrying `show_when='cost_center'` disappeared from a consolidated report.

Restored to the v2.65.3 structure: the check sits inside `kind == "allocation"` and nowhere else. Schema validation likewise only accepts `show_when` on allocation rows. Verified line-for-line against the original.

The Studio control that offered "Show" on non-allocation rows is removed — it wrote the value that triggered the fault.

**Affected builds: 2.74.0, 2.74.1 and 2.75.0.** 2.74.1 and 2.75.0 were byte-identical in this code; the 2.75.0 bump changed only the version string. Anyone on those three should move to 2.75.1.

**After deploying, check any report where a row was set to "Only when a cost centre is selected" on a non-allocation row.** The setting is now ignored rather than honoured, so those rows reappear consolidated. If a report was edited to rely on it, that edit needs revisiting — the underlying need (suppressing a duplicated before/after-allocation line) is real and will be addressed deliberately, with tests, rather than as an untested change.

## v2.75.0 — 2026-08-06

### Added: any row can be hidden when running consolidated

`show_when` was accepted on allocation rows only. It now applies to every row kind, editable from the row panel in Studio.

The case that needs it: with Credit Back on, an allocation moves cost between cost centres and leaves the company total unchanged. Run consolidated, the allocation rows hide themselves and a report prints "Net Operating Income (Before Allocation)" and "Net income" as the same figure, with the allocation that explains the difference invisible between them. That is arithmetically right and reads as a mistake.

Setting the before-allocation row to *Only when a cost centre is selected* gives one clean figure consolidated, and the full walk — before allocation, each pool, net income — when a single cost centre is chosen.

**Defaults differ by kind, deliberately.** Allocation rows keep defaulting to `cost_center`, every other kind to `always`. A shared default would blank out rows in every existing report on upgrade.

Hidden rows still feed formulas; only the display is suppressed. Suppression happens once in `execution.py`, so screen, Excel, CSV, PDF, Print and PNG all agree.

## v2.74.1 — 2026-08-06

### Fixed: "not configured yet" looked like "broken"

Three places where an unconfigured allocation feature reported itself as a failure.

**Row errors now name the row you can see.** `Allocation row 'alc_0yavc' must name an allocation rule` quoted an internal id the user never chose and cannot find in the editor. Errors now read `Allocation row 4 — "GMO recharge"` with the key kept in brackets for support. Applied to every row-level validation, not just allocation.

The allocation message also says what to do: pick a rule, or delete the row — and if the list is empty, that no rule exists yet and one must be created first.

**The Studio row editor warns when the rule list is empty.** An empty dropdown with no explanation reads as broken, and the save then failed with a 417 at the far end of the flow. The warning appears where it can be acted on, at the moment the row is added.

**The allocation empty state links to the new-rule form.** Rules are deliberately never seeded — a pool and its driver are specific to one company's cost structure, and a guessed default would put invented numbers into management accounts — but telling someone to "create one in the desk" without a way there is a dead end.

## v2.74.0 — 2026-08-06

### Added: exclude documents from the ledger

A **Documents** panel on every ledger tab. Exclude by document type (all credit notes) or by individual voucher number, and the opening balance, running balance, sub-totals and report total all recompute — the totals are the figures on screen, not the unfiltered ones.

**Opening balances are filtered too, deliberately.** An exclusion that only hit the window would leave closing = a real opening plus a filtered movement, a figure that describes nothing. Both ends filtered gives a coherent "as if these documents did not exist" ledger that foots against itself.

It does **not** foot against the account balance in ERPNext any more, and it cannot — that is inherent to removing documents, not a defect. The screen warns, and the exclusion list prints in the period line of every export. A statement that silently omits credit notes is the artefact that gets handed to an auditor and read as complete.

The document-type list is read from the window **before** exclusions are applied. Reading it off the filtered rows would have made every exclusion irreversible: the type you just hid would vanish from the control that hid it.

### Added: open the source document from the report

The voucher number is a link on screen, in HTML/Print/PDF, and as an Excel `HYPERLINK` formula. Exported links are absolute, so a workbook stays clickable off the machine that produced it — a relative `/app` path resolves against the reader's browser, not the site. CSV keeps plain text, since a CSV cell cannot carry a link.

### Added: columns from the source document, and a combined column

Pick any field from any document type in the window — including custom fields, which is where site-specific narration usually lives — and it becomes a ledger column. Sales Invoice `note` is the case that prompted this. Fields are discovered from live DocType meta, so nothing needs registering.

Fetching batches one query per document type, never per row, reusing the `_gl_descriptions` pattern this generalises. A quarter's ledger across a few doctypes would be unusable otherwise. A field that no longer exists degrades to a blank column rather than throwing.

**Combine into one column** joins any set of columns with a separator you choose, skipping blanks so an empty field leaves no dangling separator. Both kinds are ordinary column definitions, so all five export formats pick them up with no export-side change — special-casing them per writer is how the formats drift apart.

### Fixed: the Supplier tab offered a Customer filter

Both party filters rendered in every mode. On the Supplier tab the Customer filter could only ever return nothing — the tab has already fixed the subject to suppliers, and its accounts are payable control accounts no customer posts to. Each party ledger now shows only its own filter.

## v2.73.0 — 2026-08-06

### Changed: deferral no longer forces a separate return box

`target_box` was required and defaulted to `1.2`, which assumed every filer discloses government supplies on their own line. A second production filer does not: Digital Scan defers SWA supplies exactly as the law requires, then declares the released ones inside ordinary standard-rated sales.

Their filed Q1 2026 sales of 1,896,581.35 is 1,587,508.00 of ordinary revenue plus 309,073.35 of released 2025 SWA invoices, taxed as one line, with 429,166.66 base / 64,375.00 VAT still carried forward. Both legs reconcile to the riyal.

**Leaving the box blank now means "defer, but merge on release."** The rule's Return presentation field makes this an explicit choice rather than a blank field nobody would think to clear. The deferral is the tax treatment; the box is only presentation.

`sales_box` gained a `split` argument threaded through both the box totals and the drill. Without it, a merged filer's released supplies would route to `box1_2` while no 1.2 line was rendered — dropping them from the return entirely and understating the tax. That is the specific failure the new tests cover. 55 tests.

### Configuration note: two filers, two shapes

Scope by customer group suits a filer with many government customers. A filer with one is better served by a per-customer override, which carries the reason a group never could — and the reason is what an auditor asks for.

## v2.72.0 — 2026-08-06

### Added: link and tree pickers

Account, Customer, Customer Group and Sales Invoice fields on the VAT screens were free-text inputs — a typo produced a setting that silently resolved to nothing. They are now pickers.

Tree doctypes are navigable **and** searchable. A chart of accounts is best drilled when you know roughly where a thing lives and best searched when you know its name; forcing either one alone makes the other case tedious. Group nodes appear as branches you open, never as selections: an account that carries no balance cannot be a VAT control account, and picking one only produces a setting that resolves to nothing.

`link_options` is an **allow-list**, not a generic doctype browser — a whitelisted endpoint that reads any doctype by name is a data-exposure hole regardless of what the UI happens to ask it for. Only those four are reachable, each with its own field list and a read-permission check.

Swapped in for: the per-customer override, both ledger cross-check accounts, and the payment order's invoice. The payment order form also links the oldest deferred supply directly, since that is usually the one being ordered.

### Added: remove a false positive where you see it

Every account in the VAT control accounts list now carries a **Not VAT** action.

The heuristic matches on name, so a bank account called `Bank Saudi Hollandi - 010094309069 (IRSAA VAT)` and a supplier control called `C / A - IRSAA VAT Consultancy Co.` both resolved as VAT accounts. v2.70.1's type guard catches the structurally impossible ones, but not `Investment in VAT` or `Prepaid - Office Rent (Jeddah-VAT)`, where no safe rule distinguishes them from a real control account.

The action writes the existing `not_vat` classification tag, so it excludes **that one account** and does **not** switch the side to strict mode — a company can prune false positives one at a time rather than having to tag its whole chart in a single sitting. Tagging the genuine control accounts as Output/Input VAT remains the better end state, since it stops the guessing entirely.

## v2.71.0 — 2026-08-06

### Added: payment orders (أمر الدفع) as a recorded tax point

Under the Government Tenders and Procurement Law the payment order is itself a tax point: the supply enters the return of the quarter containing the order date, whether or not the money has arrived. Until now the only way to express this was a date field on Sales Invoice named on the rule, which nothing created and nothing validated.

**`Insight Payment Order`** records one properly — invoice, order date, the government's own order reference, and an optional amount. A record rather than a field because a contract milestone can carry several orders against one invoice, and because the reference is what ZATCA asks for when it queries why a supply was declared in a given quarter.

Recorded from **VAT Settings → Payment orders**, with the deferred population offered as suggestions so the question a preparer actually has — which supplies are still outside the return — is answered where the order is entered.

**An order with no amount covers the invoice in full**, which is the ordinary case and one nobody should have to key a figure for. Amounts are for part orders only, and a part-ordered invoice is flagged for a decision rather than released: releasing the whole supply on a part order would declare tax the government has not yet ordered paid. Part orders that together cover the invoice do release it, dated from the earliest.

Validation refuses an order against an unsubmitted invoice, against another company's invoice, dated before the invoice itself, or that would take the orders against one invoice past its own total — an over-order is usually a duplicate, and a duplicate silently turns a part order into a full one.

**`order_only` no longer falls back to payment receipt.** That hedge existed because no field was configured; now that there is a place to record orders, falling back would declare a supply in the wrong quarter. An unreleased invoice at least shows in the register as carried forward, whereas a silent basis switch shows nowhere.

Recording an order under a `receipt_only` rule changes nothing, so `save_payment_order` reports that back and the screen says so rather than accepting it quietly — otherwise someone comes away believing they have moved a supply into a quarter.

Sites already carrying a date field on the invoice keep working; the records take precedence, the field is the fallback. 51 tests.

## v2.70.1 — 2026-08-06

### Fixed: bank accounts were being counted as input VAT

**This can change filed figures. Re-check any open period before submitting.**

`_vat_accounts` matched any account whose name contained "vat", filtered only by root type. On a live IRSAA chart that pulled in:

- `11102009 Bank Saudi Hollandi - 010094309069 (IRSAA VAT)` — a bank account, Asset, counted as **input VAT**
- `11102013 Bank Saudi Fransi - 51389901309(IT VAT)` — likewise
- `23302003 C/A - IRSAA VAT Consultancy Co.` — a supplier control account, Liability, counted as **output VAT**
- `Excise 100%` / `Excise 50%` — a different tax entirely

Those accounts feed `_non_invoice_vat`, so every payment through those banks was landing in box 7. Root type could never have caught it: a bank is an Asset like input VAT, and a supplier control is a Liability like output VAT.

`_NEVER_VAT_TYPES` now excludes account types a VAT control account structurally cannot be — Bank, Cash, Receivable, Payable, Stock, Fixed Asset and similar — before any name matching, and `_NOT_VAT` excludes excise.

This is a guard, not a solution. Tagging the correct accounts in Classification switches the side to strict mode and stops the guessing entirely, which is what the VAT Settings screen now says in place on any side still running on heuristics.

### Fixed: primary buttons were invisible outside their own workspace

`.studio-run` builds its background from `--s-violet`, which is defined only on `.studio`. Used anywhere else the gradient is invalid, so the browser drops the **whole** `background` declaration — leaving white text on white rather than merely an off-palette button. `.fh-setup-btn` has the same problem differently: it is white-on-translucent, drawn for `.fh`'s dark banner.

Both were borrowed by the new VAT Settings and Classification tabs, and `.studio-run` is also used by ClassificationStudio's Save button, which renders in a modal over `.fh` and now as a tab under neither — so that has been affected too, not just the new screens.

Fallbacks were added so `.studio-run` and `.studio-ghost` stay legible anywhere. The new workspaces use `.vs-btn`, which inverts `--text` against `--surface`: those two contrast by definition in every theme, which is the one pairing guaranteed to stay readable whatever palette is active.

## v2.70.0 — 2026-08-06

### Added: VAT Settings, under Compliance

One screen for everything governing how a return is produced — VAT control accounts, government deferral rules, and the per-voucher adjustments behind a filed figure.

**It owns no configuration of its own.** VAT accounts are still resolved from Classification tags, deferral from `Insight GTPL Rule`, per-voucher overrides from `Insight VAT Adjustment`. Introducing a second place to set the same thing would guarantee the two eventually disagree, with no way afterwards to tell which produced a filed number.

What was actually missing was visibility:

- **VAT control accounts** now show *which* accounts resolved and, more importantly, **whether the engine is obeying tags or guessing**. Tagging any account on a side switches it to strict mode; untagged, the resolution is a name-and-type heuristic. Nothing previously said which was in force, so a company running entirely on guesswork looked identical to one that had been configured.
- **GTPL rules** are editable here rather than only in the Desk, with the rule currently *in force* marked — the same resolution the engine performs, shown instead of left to be inferred from a list sorted by date. Editing a rule that has already governed a filed quarter carries a warning, because that silently restates it; adding a later-dated rule is offered as the alternative. Rules deactivate rather than delete, since a deleted rule takes the record of what governed a filed quarter with it.
- **Period adjustments** are listed with their reasons in full, hand-keyed and engine-written alike. The reason is what an auditor reads.

### Correction

The plan this work started from said Classification "won't serve VAT account selection" and proposed a separate VAT-accounts setting. That was wrong: `_vat_accounts` has resolved `output_vat` / `input_vat` tags with a strict mode since v2.26.0. Building the proposed setting would have duplicated a working mechanism. The screen surfaces it instead.

`Workspace` gained `'vat_settings'`. Frontend rebuilt. 42 tests.

## v2.69.1 — 2026-08-06

### Verified against all four filed 2025 returns

The engine now reproduces IRSAA's box 1.2 for every quarter of 2025:

| Quarter | Amount | Adjustment | VAT |
|---|---|---|---|
| Q1 | 5,533,442.53 | 0 | 830,016.38 |
| Q2 | 5,390,429.00 | 1,177,115.00 | 631,997.10 |
| Q3 | — box absent from the return — | | |
| Q4 | 7,379,742.26 | 1,740,443.00 | 845,894.89 |

**Q3 is the case that rules out a fixed lag.** It raised 11,108,063.26 of government supplies (VAT 1,666,209.49) and declared none of them; the filed return answers the GTPL question لا and carries no box 1.2 at all. A one-quarter offset would have forced Q2's population into Q3 and Q3's into Q4 in equal measure, and neither happened. Q4 then released 7,379,742.26 while deferring a further 3,155,252.11 — the pool does not drain in order.

Both are now regression tests, along with the closing deferred balance at 31-12-2025: three vouchers, base 8,624,016.13, VAT 1,293,602.42, matching the workbook. 42 tests.

The Q4 test also caught a distinction worth stating: invoices raised inside the period and unpaid are `deferred`, while those carried from earlier are `still_deferred`. The register shows them separately; the closing balance is the sum of both.

## v2.69.0 — 2026-08-06

### Added: Classification is a workspace, not a button (step 4 of 4)

Account Classification now has its own tab under **Performance**, beside Financial Health. It was reachable only through a 🏷 button inside Financial Health, which understated it: the tag it sets is read by Financial Health, the Cash Flow statement and the Zakat base alike, so living inside one of its three consumers made it look like a setting belonging to that screen.

**The button stays.** Someone who has just seen a wrong COGS figure wants to fix it without leaving the page, and removing that path to tidy the navigation would cost more than the tab gains.

One component serves both, via an `embedded` prop that drops the modal chrome. Forking it would have left two copies of the tagging rules to keep in step — and the tag is what three separate reports read.

The tab reaches existing sites without a migration: `mergeMenu` already appends any catalog tab absent from a saved menu into its default section. Admins who have rearranged their nav keep their arrangement and gain the tab.

### Added: ledger cross-check on the deferral register

`output_vat_account` and `deferred_vat_account` were declared on the GTPL rule in v2.66.0 and read by nothing — a field whose description implies behaviour it does not have is worse than no field. They now drive a cross-check block: the period movement on each named account, printed against the register's carried-forward VAT.

This is the pull an accountant otherwise does by hand to satisfy themselves the VAT account agrees with what the return declares. It reports and does not reconcile away: a gap is information, and nothing here can know which of the two figures is the wrong one. Naming no accounts means the block is absent rather than showing zeros that look like a passing check.

### Notes

`Workspace` gained `'classification'`; the typecheck gate caught the omission before the build produced anything. Frontend rebuilt — `public/insight/assets` and `www/insight.html` are regenerated. 35 tests.

## v2.68.0 — 2026-08-06

### Added: Box 1.2 on the VAT return itself (step 3 of 4)

Standard-rated sales to government entities now split onto their own line, between boxes 1 and 2, matching the ZATCA form's **المبيعات للجهات الحكومية الخاضعة بالنسبة الاساسية (١٥٪)**.

The line appears **only when a GTPL rule is in force**. Without one the return keeps its previous shape exactly, so nothing changes for a company with no government supplies. Its box *number* comes from the rule rather than a constant: the number that was correct when a quarter was filed is the number it should still show when that quarter is re-run.

**Only box 1 reroutes.** The ZATCA line is standard-rated government sales, so a zero-rated or exported supply to a ministry stays in its own box.

**Which invoices, and which box, are separate questions.** The period adjustments decide which invoices fall in the return; the rule decides where they land. Keeping those apart means the deferral engine and the box split can each be changed without disturbing the other.

`vat_return` gained a `gtpl` block naming the rule, box, basis and customer count in force, so the UI can show what governed the split.

### Fixed: the PDF export would have printed a return that did not foot

`VatReturn.tsx` built its PDF from a hardcoded `[1, 2, 3, 4, 5]` while box 6 summed whatever lines the API returned. With Box 1.2 live the printed return would have omitted a line that the total still counted — an error invisible on screen, since the on-screen table was already data-driven, and one nobody would find until ZATCA did. The PDF now iterates the returned lines and takes each label from the API.

`box` is typed `number | string` end to end, and the drill resolves the government box by the rule's number rather than by string-prefixing it, since the internal key is fixed while the printed number is not.

### Changed: box routing is one function, not two

`sales_box` lives in the decision core and is called by both the box totals and the drill behind them. They were briefly two independent expressions; if those ever diverge the drill stops reconciling with the figure it exists to explain, and both screens still render correctly. 35 tests.

### Housekeeping

`utils/gtpl_core.py` and `api/gtpl.py` were re-indented to spaces to match the rest of `api/` and `utils/`; doctype controllers stay on tabs like their neighbours. Frontend rebuilt — `public/insight/assets` and `www/insight.html` are regenerated, so no separate build step is needed on deploy.

### Not yet included

Step 4: promoting Classification out of its button.

## v2.67.0 — 2026-08-06

### Added: GTPL deferral register + apply step (step 2 of 4)

**`gtpl_register`** is a new Export Pack sheet type. It sections the government population rather than listing it flat, because the three groups answer different questions and sum differently:

| Section | Feeds |
|---|---|
| Declared in this return | the box **Amount** |
| Adjustments — credit notes issued this period | the box **Adjustment** |
| Carried forward — not yet due | neither, but must reconcile |
| Needs a decision before filing | part-paid and ungrouped supplies |

It closes with the reconciliation the accountant's coloured footer was doing by hand — Amount, Adjustment, base, VAT — reusing the existing green/red register fills rather than inventing a palette.

**VAT is shown twice on purpose.** `vat` is summed off the invoices; `implied_vat` is the base at 15%. They diverge when invoices carry rounding or a non-standard rate has crept into a government supply, and the register prints the difference instead of quietly picking one. That difference is the thing a preparer needs before signing.

**`gtpl_preview`** returns the plan read-only. **`apply_gtpl_adjustments`** writes it out as `Insight VAT Adjustment` rows, so the whole existing pipeline — box totals, drill-down, pack reconciliation — consumes it with no further wiring. Applying is a deliberate act, not a side effect of opening a report: a hand-written adjustment is never overwritten, and part-paid or unknown-scope supplies are never written at all.

### Payments come from Journal Entries too

`_allocations` reads Payment Entry References **and** Journal Entry Accounts. A receipt booked as a JE against the receivable is as much a payment as one booked as a Payment Entry, and triggers the tax point identically. Counting only Payment Entries would have left JE-settled government invoices deferred indefinitely — IRSAA's own Q1 settlement ran through `ACC-JV-2025-00945-1`, so this is not a hypothetical route.

Government invoices are fetched with **no lower date bound**. A deferral has no statutory expiry; an invoice raised three years ago and settled this quarter belongs in this quarter's return, so a lookback window would drop exactly the supplies the feature exists to catch. The population stays small because it is restricted to government customers.

### Changed: the decision core moved to `utils/gtpl_core.py`

v2.66.0 claimed the core carried no frappe import and then imported frappe in the same file — the test suite caught it on the first run after the wrappers landed. Core and database layer are now separate modules, which is what made the claim true rather than aspirational. `api/gtpl.py` re-exports the core names, so nothing else changes.

`box_figures` moved into the core with the rest. It produces a figure that gets filed, so a spreadsheet renderer was the wrong place for it; it is now covered by tests against IRSAA's filed Q2 return. 30 tests, no frappe import required.

### Not yet included

Steps 3–4: Box 1.2 as a line on the VAT return itself, and promoting Classification out of its button.

## v2.66.0 — 2026-08-06

### Added: GTPL government VAT deferral — rules engine (step 1 of 4)

Output VAT on supplies to government entities under the Government Tenders and Procurement Law falls due when the supply is **paid**, not when it is invoiced. Until now Insight handled this through `Insight VAT Adjustment` — one hand-keyed Include/Exclude per invoice per quarter. This release adds the rule engine that derives those adjustments.

**`Insight GTPL Rule`** is dated, not a Single. The rule governing a period is the newest active rule effective on or before that period's end, so re-running a return filed two years ago resolves the rule that was in force then. Superseding by adding a later-dated rule — rather than editing in place — is what keeps a filed quarter reproducible after ZATCA changes its guidance. `target_box` is a field for the same reason; the return has been renumbered before.

Scope is a customer-group list plus per-customer overrides. Overrides win in **both** directions, and the negative direction is the one that matters: a sovereign fund can sit in an otherwise-governmental group while being invoiced commercially, and treating it as governmental defers VAT that was genuinely due.

**Release is derived from payment allocations, never from `outstanding_amount`.** Outstanding tells you what is unpaid today, not what was unpaid at the end of a quarter being re-filed.

**A credit note releases the invoice it cancels.** A cancelled supply will never be paid, so a payment-based trigger would defer it forever — undeclared indefinitely while its credit note reduced tax never declared. Under the three-column layout the resolution is disclosed gross: invoice into Amount, credit note into Adjustment, netting to nil VAT.

**Part-paid supplies are flagged, never auto-adjusted.** A tax return must not silently guess.

### Scope resolution: ungrouped customers, nested groups

A customer with a blank Customer Group now resolves to `scope_unknown`, not `not_government`. The figures are unchanged — an ungrouped customer is still left alone — but unknown scope and out of scope are different facts. A government customer that was never grouped would otherwise have its VAT declared a quarter early and appear nowhere in the output. `plan_period` returns `ungrouped_customers` so the preparer can resolve them, by group or by override.

Group membership walks the ancestry rather than testing the leaf. IRSAA's `Government` group is flat today; ERPNext customer groups are a nested set, and a flat test would silently stop deferring the day someone adds `Ministries` beneath it.

### Verified against filed returns

`tests/test_gtpl.py` reproduces IRSAA's filed Q1 and Q2 2025 Box 1.2 from invoices and payments alone — Amount 5,390,429.00, Adjustment 1,177,115.00, VAT 631,997.10 — and asserts that an invoice released in Q2 is not pulled into Q3. That last test exists because a manual trace of these two quarters initially looked like a double declaration; it was not, but nothing in the system could have shown that. 23 tests, no frappe import required.

### Fixed: `vat.py` raised NameError instead of its own error messages

`vat.py` called `_()` eight times without `from frappe import _`. Every occurrence is on an error path — `save_vat_adjustment` with no reason, permission denials, `clear_vat_adjustment` — so users hit `NameError: name '_' is not defined` in place of the intended message. Invisible to happy-path testing.

### Not yet included

Steps 2–4: the `gtpl_register` export sheet, Box 1.2 on the VAT return itself, and promoting Classification out of its button. The engine proposes `Insight VAT Adjustment` rows; nothing consumes them automatically yet.

## v2.65.3 — 2026-08-05

### Fixed: P&L by period returned 500 — KeyError: 'from'

`_pnl_period_slices` returns **column** definitions, which carry `key`, `label`, `months` and `kind`. The `from` and `to` dates existed only on the internal month list used to build them. The caller then iterated the columns expecting those dates, so Granularity failed on every run.

Two changes: month columns now carry their own `from`/`to`, and the caller builds its month set explicitly rather than filtering the requested columns — so the months the engine runs no longer depend on which columns were asked for.

`granularity="total"` is now an alias for a single Total column. It previously fell through to the monthly default and would have returned twelve columns to a caller asking for one.

### Why it shipped

The column builder was tested in isolation and the layout verified, but `run_pnl_statement_periods` itself was never executed — the mismatch was between two functions, which is exactly what testing one of them cannot find. It is now exercised end to end against a stubbed engine, checking that:

- quarters and halves derive from monthly figures and tie to the total (6 × 40 = 240 in every combination);
- the engine runs once per calendar month regardless of how many columns are requested — six runs whether the report asks for 3 columns or 10;
- a part-month range calls the engine with clamped dates (`2026-02-15 → 2026-02-28`, then whole months, then `2026-04-01 → 2026-04-20`).

## v2.65.2 — 2026-08-05

Audit of Reports and the General Ledger. One significant finding.

### Every report read endpoint was callable by any logged-in user

`@frappe.whitelist()` requires a login, not a role. None of the read endpoints checked one, so **any authenticated user — including Website and portal users, customers and suppliers with a portal login — could call them directly** and receive the data:

```
/api/method/neotec_insight.neotec_insight.api.report.general_ledger
    ?company=…&from_date=…&to_date=…
```

That returns the complete general ledger. The same applied to the P&L Statement, Balance Sheet, Trial Balance, the report runner, dimension pivots, the liquidity and P&L hierarchy views, and the AI financial snapshot — sixteen endpoints in all.

A `_require_read()` guard now tests `read` permission on **GL Entry**. That is the right test rather than a new permission model: ERPNext already restricts GL Entry to the accounts roles, so the guard inherits whatever each site has configured.

`export_configuration` — which bundles every report definition, account mapping, budget and dashboard into a portable file — now requires System Manager, matching `import_configuration`, which already did.

### The GL query itself is clean

Every value in the General Ledger query is bound. Only fixed column names are interpolated, and the party filter builds numbered placeholders with the values bound separately. No user input reaches raw SQL.

Also verified across the app: all seventeen f-string SQL sites are literal ternaries, whitelist-validated field names, placeholder counts, or internal constants. `pfield`/`sfield` in the dimension pivot are checked against a whitelist and rejected before interpolation.

### Corrections to my own audit

- I briefly flagged `import_configuration` as unguarded. It is not — `frappe.only_for("System Manager")` is its first line. The earlier scan was right to pass it.
- The guard insertion initially landed on `allocation.save_grid`, `allocation.list_report_rows` and `reconcile.set_print_header`. Requiring ledger access to save head counts or set a print header would have blocked users who legitimately do neither, and all three already carry their own guard. Removed.

## v2.65.1 — 2026-08-05

A full audit ahead of production use. Findings in order of severity.

### Security — 17 whitelisted endpoints bypassed permission checks

Frappe enforces permissions inside `doc.save()`, but these endpoints used `ignore_permissions=True`, `frappe.db.set_value` or raw `UPDATE`/`DELETE` SQL, all of which skip that check. Whitelisted and unguarded, they were callable directly over `/api/method/...` by **any authenticated user**, including a read-only one:

- Report definitions — `save_report` (report and studio), `import_report_structure_from_excel`, `report_integrity`
- Account mappings — `save_account_mapping`, `delete_account_mapping`, `set_account_flag`, `bulk_set_account_flags`
- Budgets — `create_budget_book`, `update_budget_book`, `delete_budget_book`, `save_budget_cells`, `derive_budget_cells`, `rollup_to_total`
- Dashboards — `save_dashboard`, `delete_dashboard`
- Bank — `read_slip`, `find_matches`

A `_require_write(doctype)` guard now restores the check each bypass removed. Verified: no whitelisted endpoint bypasses permissions without a guard.

### Regression I introduced in 2.65.0

Removing the duplicate `saveAccountMapping` fixed the Map tab but broke the Coverage panel in RunTab, which used the positional form. Both now use the object form. It reached the build because I regenerated the type-check baseline *after* making the change, so my own new error was recorded as known — the baseline must be regenerated only from a clean tree.

### The type check now works, and is small enough to read

- The baseline compared line numbers, so inserting a line made every error below it look new. Fixed, then broken again by `uniq -c` prefixing counts and destroying the sort order `comm` needs. Both corrected.
- Known errors reduced **48 → 10**, by fixing rather than recording: 11 duplicate translation keys removed (2 of which had conflicting Arabic — the later silently won), the React 19 `JSX` namespace, and several types that did not declare fields the backend genuinely returns (`filters.company`, `filters.company_currency`, trial balance `currency`, `currency.rate_missing`, `PeriodGroup.key`, drill `supplier`/`customer`).
- `RowEditor` destructured `onRenameKey` without declaring it — harmless at runtime, but it is exactly how a caller and callee drift apart unnoticed.

The remaining 10 are all the multi-select dimension filter widening (`Record<string, string>` vs `string | string[]`) and one React ref variance. None affect runtime.

### Checked and clean

All Python compiles. No whitelisted endpoint left bypassing permissions. Duplicate object keys across the frontend re-examined — the remainder are separate interfaces legitimately reusing field names, not overriding definitions.

## v2.65.0 — 2026-08-04

### Granularity on the Profit & Loss Statement

The native P&L Statement produced one column for the whole date range. It now takes the same **Granularity** selector the Consolidated P&L has — Monthly, Quarterly, Half-yearly, YTD and the combinations — and splits the same accounts into period columns. *Single column (whole period)* remains the default, so nothing changes unless it is asked for.

- **Columns interleave the way a statement is read**: `Jan Feb Mar Q1 Apr May Jun Q2 H1 Total`, not every month followed by every quarter.
- **One query per month, everything else by addition.** P&L figures are flows, so a quarter is the sum of its months. Running the engine per *column* would have meant up to twenty-eight runs for Monthly + Quarterly + Half-yearly across a year; this is twelve at worst regardless of the combination.
- **Part months are kept part.** A report from 15 February to 20 April gives a part-month first and last column rather than silently widening to whole months.
- Accounts are the union across months, in the first month's tree order, so an account that only transacts in December still appears in its proper place.
- A **Total** column always closes the report. Columns that do not add to the period figure invite the reader to add them up by hand.
- Excel, CSV, PDF, Print and Image carry the same columns, switching to landscape past four of them.

### Fixed: `saveAccountMapping` was declared twice

`api.ts` defined it once taking an object and again taking positional arguments. In an object literal the later wins, so the positional version was the live one — and the Map tab, which calls it with an object, was sending the whole object as `report` and `undefined` for account and flag. Adding a flag from that tab could not have worked, and `is_group_binding` and `dimension_filters` were being dropped.

The duplicate is removed and the object form kept. Found by the type check added in 2.64.2, which is what it is for.

### The baseline no longer moves when lines do

The first version of `typecheck-baseline.txt` recorded `file(line)`, so inserting a line above an existing error made every error below it look new — noise that would have got the check ignored again. It now records file and error code without line numbers.

While fixing that, the P&L argument types were widened from `string` to `string | string[]` to match what the multi-select dimension filters actually pass. Known type errors are down from 48 to 23, by correcting them rather than recording them.

## v2.64.3 — 2026-08-03

### Head count counts Active employees only

Capture excluded people on `relieving_date` alone, so an employee marked **Inactive** with no relieving date — Sarah Al-Sodairy, HR-EMP-00003 — was still being counted in every month of the year. New **Count Statuses** setting on the rule, defaulting to `Active`.

The dated case is deliberately unchanged: **an employee with a relieving date still counts in every month up to the date they left**, whatever their status says today. Filtering purely on current status would have made a past month move the day someone leaves, which is the retroactive-drift problem this feature exists to avoid.

Where there is no relieving date and the status is not counted, there is nothing to say when the person stopped. Counting them inflates every month; dropping them quietly hides a data gap. They are excluded **and listed** — with a link to each record — so a relieving date can be added, after which they count correctly up to it.

The pre-flight panel no longer reports "the denominator is complete" when records have been excluded this way.

### The People tab was already correct

Checked, since the same fault would matter more there: every Employee query in `cfo.py` already filters `status = "Active"`. Only the allocation capture was wrong.

### Note on the 24% you are seeing

The pre-flight is reporting 38–39 employees with no cost centre against ~160 counted. That is not a bug in the count — it is `payroll_cost_center` being unset on those records, mostly in NII and Sales. Until they are assigned or a department mapping is filled in, every other cost centre carries about a quarter more than its true share.

## v2.64.2 — 2026-08-03

### Fixed: clicking a driver figure crashed the Allocation report

`ReferenceError: setEvidence is not defined`. The click handler that opens the evidence viewer was written into `AllocReport`, but the state it sets lives in `AllocationApp` — a different component. The handler is now passed in as a prop.

Also fixed: `allocation` was never added to the `Workspace` union when the tab was introduced, and a cell object literal specified `driver` and `amount` twice.

### The check that should have caught this was broken

This shipped because the verification step was

```
npx tsc --noEmit | grep -iE "alloc|capture" || echo CLEAN
```

and typescript was not installed at that moment. `tsc` produced no output, `grep` matched nothing, and the `||` branch printed **CLEAN**. A check whose failure mode is indistinguishable from success is worse than no check, because it buys false confidence. TypeScript had in fact reported the error all along.

Replaced with `scripts/typecheck.sh`, which:

- **fails if `tsc` is missing** rather than reporting success;
- compares against a **committed baseline** of the 48 type errors this codebase already carried, so a new one stands out instead of being lost among the old ones — hand-listing exceptions was what let this through, since the filter was narrower than the problem;
- **runs as part of `npm run build`**, so it cannot be skipped by accident.

Running it against the current tree surfaced the `Workspace` omission above, which had been latent since 2.58.

## v2.64.1 — 2026-08-03

### Only assigned leads count, dated by assignment

A lead nobody has been given is a record, not a workload — it should not draw a share of the cost of chasing leads. And the month that matters is when it was handed to someone, not when it was typed in.

- **Count assigned leads only**, on by default. Assignment means a **ToDo** record against the lead. `lead_owner` is not a substitute: it defaults to whoever created the lead, so on this site nearly every lead has one whether or not anyone was ever given it.
- **`assignment` is the default date**, resolved from ToDo. Note this is `ToDo.creation`, when the assignment was made — not `ToDo.date`, which is the due date on it and would place leads in the wrong month entirely.
- **The earliest assignment wins.** Re-assigning a lead in June must not drag it out of the April it entered the pipeline in — the same principle that stops a September transfer rewriting July's head count.
- Cancelled ToDos are ignored.
- Leads skipped for having no assignment are counted and reported, so a low month reads as "nobody was given these" rather than as missing data.
- The evidence viewer and its export switch to lead columns — assigned to, assigned on, status — when the rule is lead-based.

### Verified

Against a fixture where one lead is created in March, assigned in April and reassigned in June: it counts in April only, never in March or June. A lead with no assignment is skipped and reported. A lead assigned but with no business line is surfaced as having no cost centre rather than dropped. Counting everything by creation date, the old behaviour, gives a visibly different answer for the same data.

## v2.64.0 — 2026-08-03

### Lead count captured from CRM

`Lead.business_line` is a **Link to Cost Center** on this site, so no mapping and no inference are needed — the lead already names the cost centre it belongs to, and `business_division` is fetched from it, which is why those values already read as allocation columns.

- **Which date counts is configured, not assumed.** A lead received in March and qualified in May belongs to different months under different rules, and that moves real money between cost centres. The rule chooses from `custom_creat_date` (default), `creation`, `date`, `qualified_on` or `won_or_lost_date`, falling back to `creation` where the chosen field is empty on older records.
- Status exclusions and a disabled-lead switch, both off by default so nothing is silently omitted.
- **Business Lead Unit** support: when that child table is filled, a lead can optionally be counted once per unit rather than once on its business line. Off by default, because it changes the denominator.
- The date window is filtered in Python rather than SQL, since filtering on a nullable custom field would drop exactly the records whose date is unset instead of falling back.

### Months already entered by hand are protected

The real risk with turning capture on is not that it fails, it is that it succeeds and disagrees. January to June hold values typed in against the workbook and already reported; a count that differs would rewrite figures that have been used.

The capture preview now detects **manual months that are not yet frozen** and offers to lock them before anything runs. Frozen months are compared rather than proposed: a difference is reported as drift and nothing is written. That turns "the numbers might change under me" into "here is where the CRM disagrees with what was counted", which is useful rather than dangerous.

Verified: with six manual months frozen and two of them disagreeing, the capture reports both differences and writes nothing.

## v2.63.1 — 2026-08-03

### Employees without a cost centre are now a worklist, not a footnote

2.63.0 named them inside the capture preview, truncated to six, with no way to act on the list. That is not enough to work from, and this is the quietest failure in the whole feature: an unassigned employee raises no error, produces no mismatch, and the column total still ties to the pool exactly. They just leave the denominator, and every remaining cost centre takes a larger share.

- **Checked on load**, not only when a capture is opened. A banner appears on both views the moment the rule uses head count, so the problem is visible before the report is trusted.
- **Full list** with name, department, designation, joining date and which months each is missing from. Each name links straight to the Employee record.
- **Export** for whoever is fixing them.
- **Impact quantified.** With 3 of 40 people unassigned, every cost centre is carrying about 8% more than its true share — so the panel says that, rather than leaving "3 unassigned" to be judged.
- **Per-month table** showing counted, missing and overstatement, since the problem often affects only part of the year.
- The panel also says which resolution the rule uses, and points out when a rule is set to use a department mapping whose table is empty.

## v2.63.0 — 2026-08-03

### Head count captured from Employee records, with the evidence kept

**Point in time, not "active today".** The People tab counts `status = "Active"`, which answers only about the present — re-run it in December and every past month gets December's answer. Capture works from `date_of_joining` and `relieving_date`, so an employee counts in a month if they had joined by then and had not yet left. That is what makes 12 become 11 in March.

**Transfers replayed backwards.** `payroll_cost_center` holds one current value with no history, so a transfer in September would, on a naive recount, appear to have applied all year — silently rewriting July. Employee Transfer records carry a transfer date, so they are replayed to recover the cost centre as it stood. A month rebuilt this way is labelled *reconstructed*, distinct from one captured at the time.

**Cost centre resolution** reads `Employee.payroll_cost_center`, falling back to a Department → Cost Centre mapping table on the rule.

### Evidence that reconciles

Each captured cell stores the employees behind it — id, name, department, designation, joining and leaving dates — snapshotted at capture time. Click any driver figure in the report to see the list.

Snapshotted rather than re-queried on purpose: a list rebuilt at viewing time would omit a since-deleted employee and stop adding up to the number it exists to support, which is precisely when someone is checking it. The viewer states plainly whether the list reconciles to the stored value, and says so loudly when it does not.

Manual entries have no list, and say so — who typed the value and when, rather than an empty table implying missing data.

### Nothing is written without review

Capture shows a preview first: per month and cost centre, what is stored, what was counted, the difference, and what it intends to do. Frozen months are compared rather than proposed. Manual overrides are listed as deliberately kept.

**Unassigned employees are named, not dropped.** Someone with no cost centre does not error — they simply leave the denominator, and every other cost centre quietly takes a larger share. The preview names them.

**Freezing** closes a month: later captures report drift against it instead of rewriting it. A transfer in September is a September event, not a correction to July.

### Verified

Against a fixture with a joiner, a leaver, an unassigned employee and a department fallback: the leaver is counted through March and gone from April, the joiner is absent in March and present in April, the unassigned employee is surfaced by name, the department mapping resolves, and the evidence list length equals the stored count for every cost centre in every month.

### Migration

`bench --site <site> migrate`. Set **Driver Source** to `employee_headcount` on the rule, check **Cost Centre From**, and fill the department mapping if `payroll_cost_center` is not populated on your Employee records.

## v2.62.2 — 2026-08-03

### The P&L allocation row was a month late, with a blank January

The same fiscal-versus-calendar month confusion fixed in 2.62.0 for the pool, now fixed on the other side. The report engine addresses rows by **0-based fiscal-year index**; allocation entries are stored against real dates and are keyed by **calendar month**. The row fed fiscal indices straight into the allocation lookup, so index 0 asked for "month 0" — which never exists, hence the empty January — and every later index landed one month behind.

Both bases are now translated explicitly, carrying the calendar *year* along with the month. For a January-start company the two differ by one; for an April-start company they differ by three and cross a year boundary, so a fiscal year straddling two calendar years is computed from both and merged back onto the index the report asked for.

### Consolidated runs show the pool, not zero

With credit-back on, the charges and the credit net to zero — correct for the company total, but a row reading zero says "no allocation happened". Run without a cost centre, the row now shows the pool being spread. With a cost centre it shows that centre's share, as before.

## v2.62.1 — 2026-08-03

### Allocation rows can be saved

`ALLOWED_ROW_KINDS` in the definition schema validator still read `{section, source, formula}`. The engine understood `allocation`, the Rows editor could create it, and then `validate()` rejected the whole report on save. I added the kind in three places and missed the fourth.

The validator now accepts it and checks it properly: an allocation row must name a rule, and its sign and visibility must be valid values.

### Allocation rows show only for a selected cost centre

New **Show** setting on the row, defaulting to *Only when a cost centre is selected*.

Run consolidated, an allocation row would display the whole pool beside expenses that already contain it — which reads as a real charge and double-counts to anyone scanning the column. The row is now hidden unless the report is run for a single cost centre.

The value still lands in the formula context either way, so a formula referencing the row keeps working consolidated; only the display is suppressed. *Always* is available for anyone who wants the old behaviour.

## v2.62.0 — 2026-08-03

Two bugs, both visible on the first real run.

### The pool was reading one month late

January's allocation was spread from February's expense, and so on down the year. `_fetch_monthly_for_accounts` returns figures keyed **0-based, in fiscal-year order** — 0 is the first month of the company's fiscal year, not January. The allocation asked for months 1..12 and read them as calendar months, so every figure landed one slot early.

Fixed on both sides: the report is now run for fiscal months 0..11, and each index is translated to a calendar month through `calendar_month_for_fy_month`, which respects the company's fiscal year start. For a January-start company that is index + 1; for an April-start one it is not, and the old code would have been wrong by three months rather than one.

The June figure of 40,365 was the same fault — that was July's partial month pulled back into June.

### A month with no pool no longer allocates

A direct amount entered for the whole year charged every month regardless of whether anything had been spent: 45,000 to Main in each of July–December against a pool of zero, a matching negative credit, and a 270,000 "unallocated remainder" that was pure artefact rather than a real gap.

You cannot spread a pool that does not exist. Months with no pool now allocate nothing and are marked *no pool* in the report, so an empty future month reads as empty rather than as an error.

### Verified

With the real P&L figures: January's pool is 192,064, every month ties to its pool, July–December are clean, and the year-to-date residual is 0.04 — rounding, not drift.

## v2.61.0 — 2026-08-03

### Allocation rows can be added from the Rows editor

The engine has understood `kind: allocation` since 2.58, but the Rows editor only ever offered section, source and formula — so the row could not be created without hand-editing `definition_json`. That gap is closed.

- **+ Allocation** button alongside the existing three.
- The editor picks an Allocation Rule from a dropdown and shows what it resolves to — source report, row and cost centre, and the driver it spreads on — so a misconfigured rule is visible at the point of use rather than at run time.
- Sign selector, matching source rows.
- `RowKind` and `DefinitionRow` extended; the row behaves like a source row everywhere else, so formulas reference it by key with no special syntax.

## v2.60.2 — 2026-08-03

### The report that feeds the pool is the report that carries the allocation

Reading the pool runs the source report. That report is also where the allocation rows live, so evaluating it evaluates them, and each one reads the pool again — unbounded recursion until the worker dies. Adding a `kind: allocation` row to the same report the pool is read from would have hung the site.

A guard now tracks pool reads in flight. While one is running, allocation rows evaluate to zero.

That is not only the crash fix, it is the correct accounting: the pool is the cost centre's expense **before** any allocation. Allocating a figure that already contains allocations is circular by definition, so the pool must be read pre-allocation — which is exactly what the guard produces.

Simulated both ways: without the guard the call stack blows at depth 51 and keeps going; with it, depth 1.

## v2.60.1 — 2026-08-02

### You can take a row off a table again

Adding a cost centre to the wrong table left no way back. The hint said to clear every value in the row, which does not work and was never going to: **a zero head count is a real value** — it has to stay in the table and contribute a zero share — so there is no set of cell values that means "gone". A row of blanks was equally stuck, since there was nothing to clear.

- **×** removes a cost centre from the rule. **⇄** moves it to the other table.
- Removal is confirmed only when the row actually holds values; an empty row goes without a prompt.
- Nothing is deleted until Save. Reload undoes it, and the unsaved-changes marker says how many rows are pending removal.
- The API takes an explicit `remove` list rather than inferring deletion from empty cells, so the intent survives the round trip instead of being guessed at.

### The pool hint no longer lies

A rule with no pool source configured reported `Pool read from the GL via flag "—"`, which reads as though a flag were set. It now names the report, row and cost centre for a report-row rule, and says plainly when the source has not been configured yet.

## v2.60.0 — 2026-08-02

Allocation, finished to the agreed design.

### Pool comes from the report, not from accounts

The pool is now **a row of an existing report, run with one cost centre filter** — `Total Expenses` with Cost Center = GMO is exactly the number on screen. Reading the report rather than re-deriving it from accounts means the allocation cannot drift from the statement it is spread out of: remap an account and both move together. Flag and manual sources remain for cases that need them.

### The denominator is the table's own total

`driver_total` is the sum of exactly the cost centres listed in the driver table — never a company-wide head count. Every share is therefore a share of something that adds to 100%, and the allocation ties to the pool by construction.

### Editable formula

Stored per rule, defaulting to `(pool - amount_total) * (driver / driver_total)` — the workbook's `=+(D14/D$19)*(D$6-D$12)` with names instead of cell references. Variables: `pool`, `amount_total`, `distributable`, `driver`, `driver_total`, `month`, `year`; functions `IF ROUND ABS MIN MAX`. Evaluated through the same sandboxed AST evaluator the report row formulas already use — no `eval`, no attribute access, no imports. A formula that fails yields zero for that month and names the month in the report rather than failing silently.

Amount-basis rows never run through the formula: an amount is a fact, not a derivation.

### Credit back (Q2, option B)

The source cost centre is credited with everything it gave away, so it nets to zero and the consolidated total is unchanged. Without it the same riyal counts twice at company level — once where it was spent, once where it landed — and every individual cost centre report still looks correct, which is what makes the error dangerous. Optional, on by default, with a separate credit cost centre if the credit should land elsewhere.

The report distinguishes `charged` (what the receiving cost centres carry, which ties to the pool) from `allocated` (the company-level net, which is zero when credit back is on).

### Two separate tables

Head count and Amount are now distinct tables with their own headings, and a cost centre belongs to one or the other. Moving a row between them is an explicit action that clears it — a head count of 8 is not an amount of 8.

### Verified against both sheets

GMO: all six cost centres reproduce the reference figures for all six months. Sales & Marketing: every allocated cell reproduces the sheet, `charged` ties to the pool each month, and the company net is zero with credit back on.

Where the numbers differ from the workbook, the cause is the pool, not the model — feeding May's workbook pool of 70,586 reproduces the sheet exactly, while the P&L's 70,555.647 gives 39,183 for Book Keeping against the sheet's 39,203.

### Migration

`bench --site <site> migrate`. Existing rules default to `report_row` and will need their source report, row and cost centre set.

## v2.59.0 — 2026-08-02

### The entry grid no longer invites you to paste a result into an input

The previous grid showed two blocks for every cost centre — a head count block and a "Fixed amount" block — which read as though both wanted filling in. They did not: for a head-count cost centre the amount is *calculated*, and typing one in double-counts it. The label was the problem, so it is gone.

**One row per cost centre, with a basis:**

- **Head count** (or whatever the rule's driver is) — enter the count. The amount is calculated, never typed:
  `(this cost centre ÷ company total) × (pool − amounts entered directly)`, which is the workbook's `=+(D14/D$19)*(D$6-D$12)`.
- **Amount** — for a cost centre with no head count, such as Main. The figure is entered directly and comes out of the pool before the remainder is spread.

A cost centre is on one basis or the other. There is no longer a column a calculated figure can be pasted into: the entry doctype stores only the field matching the row's basis, and the API discards the other, so this cannot be reintroduced from the desk either. Switching a row's basis clears it — a head count of 8 is not an amount of 8, and carrying figures across is how a number ends up in the wrong column unnoticed.

The report labels amount-basis columns and warns only about a cost centre whose basis *changes* mid-year, which is the case that is genuinely a mistake.

### Verified

Recomputed against the source workbook with the new model: all six cost centres match the reference figures for all six months, and every month still ties to its pool — Kafaa 47,697 · Audit 23,848 · Book Keeping 31,798 · HR Services 11,924 · GRC 31,798 · Main 45,000 for January, and correspondingly through June.

### Migration

`bench --site <site> migrate`. The `fixed_amount` field is replaced by `basis` + `amount`; any allocation entries created under 2.58.x should be re-entered, as the basis was previously inferred rather than stored.

## v2.58.1 — 2026-08-02

### Fixed-only cost centres are now labelled as such

Main on the GMO rule and Audit on the Sales rule take their fixed amount and no share of the remainder. That already fell out of the arithmetic — a blank driver gives a zero share — but nothing said so on screen, and nothing stopped a stray head count being typed against Main later. It would then start drawing a share, the totals would still reconcile, and nothing would look wrong.

- Each cost centre is classified across the year as **fixed**, **driver** or **mixed**, and the report tags its column head.
- A **mixed** centre — one carrying both a fixed amount and a driver value — raises a banner naming it, because that is the case where a mistake stays invisible.

Note the classification is per rule, not global: Audit is fixed-only under Sales & Marketing and driver-based under GMO, exactly as in the source workbook.

## v2.58.0 — 2026-08-02

### Cost pool allocation

Replaces the GMO / Sales & Marketing allocation workbook. The model is kept identical to that spreadsheet so the numbers tie from day one:

```
distributable   = pool - sum(fixed)
alloc(cc)       = fixed(cc) + distributable × driver(cc) / total_driver
```

- **Pool** — the cost being spread. Read from the GL through an account flag (resolved against that flag's own report, so the allocation and the expense lines it spreads can never read different accounts), or entered by hand per month.
- **Fixed** — carved out *before* the pro-rata and charged direct. The workbook's 45,000 to Main and 10,000 to Audit: amounts known to belong somewhere specific, which must not be smeared across everyone.
- **Driver** — head count, leads count, or anything else countable. Only the remainder moves on it.

New doctypes `Insight Allocation Rule` and `Insight Allocation Entry`, a compute module, and an `allocation` API.

### New Allocation tab

Under Reports. Two views:

- **Report** — cost centres laid out **horizontally**, every cost centre side by side, months down, with the driver values repeated underneath so the split can be checked against its basis. The cost-centre column is sticky, since the table is wide by design. Exports as Excel, CSV, PDF, Print and PNG like every other report.
- **Data entry** — the interim tool for head counts and leads counts: a cost-centre × month grid with a second block for fixed amounts, and a pool row when the rule is manual. Bulk-saves one rule-year at a time, which is how the numbers actually arrive.

A blank cell and a zero are treated as different facts — blank contributes nothing to the driver total, zero contributes a zero share. Clearing every value in a row removes that cost centre from the rule.

### Feeding the P&L

New row kind **`allocation`** in the report engine. The row names an Allocation Rule and lands in the formula context exactly like a flag-sourced row, so a formula row references it by key with no special syntax — the P&L's *GMO Allocation* and *Sales & Marketing Allocation* lines become ordinary terms. Row kinds live in `definition_json` and are not validated against a list, so this is purely additive: existing reports are untouched.

The value follows the report's cost centre filter — that centre's share when one is selected, the whole pool when none is. A missing or misconfigured rule yields zeros rather than failing the statement.

### Reconciliation is explicit

The identity that matters is that allocations add back to the pool. `compute` returns the residual rather than assuming it, and the report shows an **Unallocated remainder** banner when a month has a pool but no driver value — that money has nowhere to go, and it is a data gap, not a zero.

### Verified

Computed against the source workbook's own figures for all six months: every month ties to its pool, and spot-checked cells match the spreadsheet exactly — Jan Kafaa 47,696.59, Mar Audit 18,522.73, May HR Services 15,330.20, Jun GRC 38,209.97.

### Migration

`bench --site <site> migrate` to install the two doctypes.

## v2.57.0 — 2026-08-02

### Supplier Ledger and Customer Ledger

General Ledger now carries three sub-tabs: **Accounts · Supplier · Customer**.

They are the same query with the subject fixed to a party. Written as separate screens they would agree in month one and disagree in month nine — someone fixes a running-balance edge case in one and not the other, and two screens report different balances for the same supplier. So the tabs are configuration, not code: one engine, one running-balance path, one set of exports.

| | Accounts | Supplier | Customer |
|---|---|---|---|
| Grouping | account | party (fixed) | party (fixed) |
| Accounts | you pick | payable control accounts, preselected | receivable control accounts, preselected |
| Default columns | as before | + Account | + Account |
| Export title | General Ledger | Statement of Account | Statement of Account |

- **Control accounts preselect.** Choosing a supplier and pressing Run beats hunting for *Creditors* in a tree of 400 accounts. The picker stays available for sites with several payable accounts — retention payable, related-party payable — that need narrowing. New `report.party_control_accounts` resolves them by `account_type`.
- **Account is a default column on the party tabs**, because the party heading no longer implies it.
- **Party ledgers export as a Statement of Account** — a document you send *to* the party — with the party named in the subtitle and the Brand Kit letterhead already in place. Filenames are `supplier_ledger` / `customer_ledger`.
- Switching tabs remounts, so a supplier ledger never inherits the account ledger's column picks or half-typed filters.

### The account ledger is left alone

v2.56.0 flipped the General Ledger to party grouping automatically whenever a supplier or customer filter was present — a silent change to the behaviour of an existing screen. Reverted: grouping is explicit. The **Group by** selector (Account / Party) stays on the Accounts tab for anyone who wants it, defaulting to Account exactly as before, and the Supplier and Customer tabs ask for party grouping by name.

## v2.56.0 — 2026-07-30

### General Ledger: the party heads the report when you filter by one

Filtering by Supplier = ABREEZ GIFT TRADING and then reading a block headed **Creditors** tells you nothing you didn't just type. The party filter says the party is the subject of the report, so the party now heads the block.

- **Group by** selector on the General Ledger: *Auto* (party when a supplier or customer filter is active, account otherwise), *Account*, or *Party*. Auto is the default; the two explicit values override it either way.
- The block heading shows the party name with a Supplier/Customer badge, Arabic-resolved like any other name.
- Opening balances, running balances and sub-totals are **recomputed per party** — carrying a per-account running balance into party order would have produced numbers that tie to nothing. Opening comes from a party-scoped query over the same filter set.
- Entries with no party group under an explicit *Without party* block rather than disappearing. (With a party filter active this bucket is empty by construction; it only appears when grouping by party is forced without one.)
- Exports follow the screen: a party block is headed by the party in Excel, CSV, PDF, Print and PNG too.

### Two new pickable columns

Because the account is no longer implied by the block heading when grouping by party, it has to travel with the row.

- **Account** — the row's own account, as `number - name`. Always available, on or off like any other field.
- **Description** — the narration a human typed on the source voucher, which is *not* what Particular shows. Particular carries GL Entry's machine-written remark (`Amount SAR 172.5 to ABREEZ GIFT TRADING COMPANY Transaction…`); Description carries the accountant's own text. The right fieldname differs per doctype — `user_remark` on Journal Entry, `remarks` on Payment Entry and the invoices, `remark` on Expense Claim — so each type is looked up against its own meta and fetched in one batched query per type, never per row. HTML in narration fields is stripped to plain text.

The Description lookup is **opt-in**: it costs one query per voucher type, so it only runs when the column is switched on. A source doctype that doesn't exist or can't be read degrades to a blank cell rather than failing the report.

### Verified

Regroup exercised against the exact data in the reported screenshot: 3 supplier rows plus a bank leg, running balances reproduce `172.50Dr · 22.50Dr · 0.00Dr`, row count preserved, sub-totals tie.

## v2.55.3 — 2026-07-30

### Logo sizing — width and height, in millimetres

The letterhead offered one dropdown, *height*, with four presets. A tall-and-narrow mark behaved; a wide wordmark like `WhiteHelmet · AI FOR CONSTRUCTION` did not — the image had `height` but no `max-width`, so it overflowed its flex column and painted straight over the report title.

- New **Width (mm)** and **Height (mm)** fields in Print setup, plus S/M/L/XL chips for the old behaviour.
- Leave one blank and the image keeps its own proportions; set both and it is *fitted* inside that box (`object-fit: contain`), never stretched.
- `max-width: 100%` on the band image, so no logo can ever paint over a neighbouring column again regardless of the numbers entered.
- The same box sizes the logo in Excel, so one setting governs Print, PDF and the workbook.

### Excel now carries the letterhead logo

Print and PDF rendered the logo from a URL. A workbook cannot — it has to carry the image itself as a drawing part, so Excel was text-only while the other two had a full letterhead.

- The logo is fetched, embedded at `xl/media/`, and anchored above the title rows through a proper `oneCellAnchor` drawing, with a spacer row sized to the logo so it sits above the heading rather than on top of it.
- Intrinsic dimensions are read from the PNG/JPEG/GIF header, so an unspecified width is derived from the image's real aspect ratio instead of guessed.
- SVG is skipped deliberately — Excel's drawing model does not render it, and a broken-image placeholder is worse than a clean text header. A failed fetch is non-fatal: the workbook still downloads, just without the mark.

### Fixed: printed figures had lost their thousand separators

A regression I introduced in 2.55.0. The shared document model carried one value per cell, and Excel needs that raw (`5385470.85`) so the figures stay computable — but Print, PDF, CSV and PNG were stringifying the same raw number, so the paper showed `5385470.85` where the screen showed `5,385,470.85`.

`DocCell` now carries `v` (raw, for Excel) and `text` (formatted, for everything else). Wired through Profit & Loss, Balance Sheet, Trial Balance and the General Ledger, each using its own decimals setting.

### Verified

Workbook round-tripped through `openpyxl`: image embedded, box resolved to 42.0 × 14.0 mm from a 3:1 source at 14 mm height, spacer row 46 pt, heading rows present, Arabic company name intact, numbers stored as numbers, gridlines on, freeze `A7`, print titles `$1:$6`. All ten package parts parse.

## v2.55.2 — 2026-07-30

Header fixes, both of them regressions I introduced in 2.55.0.

- **The app brand block wrapped.** `.ni-brand` had neither `flex-shrink: 0` nor `white-space: nowrap`; it never needed them because nothing else competed for that space. Adding the company block put it under width pressure and the version pill dropped onto its own line under *Insight*. Both guards added.
- **Long company names were amputated.** A single-line clamp cut `شركة المسح الرقمى لتقنية المعلومات` down to a fragment. The name now gets two lines before ellipsis, the block widened to 300px, and the full name is on the hover tooltip and at the top of the company popover.
- **`dir="auto"` on the name.** Without it an Arabic legal name laid out LTR and clipped from the wrong end, so the visible half was the tail rather than the head.
- The company popover is left-anchored again — centred on a narrow button it clipped off the left edge of the header.

## v2.55.1 — 2026-07-30

### One section for "how are we doing"

CFO Briefing, Dashboard and Analysis were three top-level buttons holding four tabs between them — a third of the nav bar spent on one idea. They are now a single section with four sub-tabs:

- CFO Briefing · Dashboard · Financial Health · Group

That leaves a taxonomy with no overlap: **Reports** is what the books say, **Studio** is build-your-own, **Compliance** is statutory, **Operations** is day-to-day.

Note on the shape: the nav is two-level, so a sub-tab has to be an actual screen. *Analysis* wasn't one — it was a section holding Financial Health and Group — so its two tabs moved up rather than nesting a third level.

**Existing saved layouts are folded automatically**, but only when all three sections are still exactly as they shipped. If a tab has been moved, hidden or a section renamed, that was a decision and the layout is left alone. Hidden flags carry across, and the merged section lands where the earliest of the three sat so the nav doesn't reshuffle. Verified against the current layout: no tab lost, idempotent, customised layouts untouched.

The section name is editable in ☰ Menu setup and saved site-wide — no rebuild needed to rename it. (Tab names come from the app catalog and are not editable.)

### Company name below the logo

The header brand block now stacks: logo on top, company name beneath, matching the order the printed letterhead uses. Name truncates with an ellipsis rather than pushing the nav, and drops out below 1180px leaving the logo.

## v2.55.0 — 2026-07-30

### Company name and logo in the shell header

The header showed `Neotec · Insight v2.54.0` and nothing else, so a screenshot of a report — or an operator with three companies on the site — had no visible answer to *whose books is this*. The gap to the right of the app mark now carries the active company's logo and name.

- New `CompanyBrand` header block. Click it to switch company, or to upload a logo.
- **Provision for a company with no logo on file.** Logo resolution runs Brand Kit logo → `Company.company_logo` → the site's default Letter Head image → a monogram built from the company name. An upload is written back to the **Company master** when the user has write permission on Company, so it is the company's logo and not one browser's; without that permission it is saved into the Brand Kit and used for reports only. The panel says which of the two happened rather than silently doing the lesser thing.
- New `activeCompany` store: every report tab publishes its Company selection, so the header, the Brand Kit and every export key off the same company instead of each guessing.
- New endpoints `report.company_brand` and `report.set_company_logo` (the latter permission-checked against Company, and it will only accept an uploaded `/files/…` path).

### Print setup on every report menu, not just the ledger

The print setup existed once, behind the ⚙ on the General Ledger toolbar, and every other report silently inherited whatever the ledger happened to be set to. Worse, that ⚙ rendered as **a blank button** — it was `<i className="ti ti-settings">` and the Tabler icon font is not loaded on this site, which is the empty control circled in the screenshot.

- The panel is now a shared `BrandKitModal`, opened from the same **⚙ Setup** button on every report toolbar, and it is labelled in text rather than an icon that may not load.
- It is **saved site-wide**, not in `localStorage`. New `brand_json` field on *Insight Menu Settings*, with `navmenu.get_brand` / `save_brand` (key-whitelisted, capped, write-permission gated). `localStorage` stays as the synchronous read cache the print builders need; the site is the source of truth. The panel tells you which one it managed: *Saved for everyone* vs *Saved on this device only*.
- The per-report heading field was **removed from the shared panel on purpose** — a heading typed once must not leak onto every other report's letterhead. Each report supplies its own title.

### Borders now actually print

Three separate defects, all real:

- **Excel had never emitted a single border.** `buildXlsxBlob` declared `<borders count="1">` containing one *empty* border and pointed every cell style at it. Every workbook this app has produced — P&L, Balance Sheet, Trial Balance, pivots, dashboard, account map — was borderless by construction. There is now a real border table, cells are boxed, and `printOptions gridLines="1"` is set so Excel rules the page even where a style carries none.
- **The General Ledger workbook** had bottom rules only, no left/right, so it read as unruled once printed. Cells are boxed now.
- **Print and PDF dropped their rules and fills** because `printBalanceReport` — the print path for P&L, Balance Sheet and Trial Balance — set no `print-color-adjust`. Chromium and wkhtmltopdf both discard backgrounds in print media without it. Added, plus the Brand Kit letterhead frame those three statements never had.
- **Hairlines rounded to nothing.** `borderTokens` emitted `.5px` rules: fine on a retina screen, zero after the print pipeline rounds them. All rules are whole pixels now.
- New **Grid lines** control in the print setup — *Full grid* (default), *Horizontal rules only*, *No rules* — applying to Print, PDF and Excel alike, with a separate weight setting.

### Every output on every report

The Financial Reports screen in the screenshot is the native **Profit & Loss Statement** view, not the report runner — and it, the Balance Sheet and the Trial Balance each hand-rolled a toolbar with only *Run · Excel · Print*. That is why there was no PDF.

- New `ReportDoc` model: a report describes itself once — columns, rows, title, period — and shared writers render it to **Excel, CSV, PDF, Print and Image (PNG)**. Print and PDF are literally the same string, so they cannot diverge.
- New `ExportBar` component carrying all five plus ⚙ Setup. Wired into General Ledger, Profit & Loss, Balance Sheet and Trial Balance; the report runner and the Combo pivot gained the ⚙.
- The General Ledger's four bespoke export functions (`exportExcel`, `buildPrintDoc`, `exportPrint`, `exportPdf`) are gone, replaced by one `buildGlDoc()`. Per-account page breaks survive as real Excel row breaks.
- Excel output carries frozen panes, print titles, page header/footer and page setup; CSV ships a UTF-8 BOM so Arabic opens correctly in Excel.

### Fixed along the way

- The print builders for P&L, Balance Sheet and Trial Balance wrote `<th>{t('Account')}</th>` **inside a template literal**, so every printed statement carried the literal text `{t('Account')}` as a column heading — and the Trial Balance also printed `{t('Total')}` as its totals label. Both gone.
- Trial Balance party breakdowns now export exactly as shown: an expanded receivables account carries its parties into the file, a collapsed one does not.

### Verified

Generated workbook round-tripped through `openpyxl`: borders read back as `thin` on all four sides of every data cell and absent on the letterhead rows, numbers land as numbers, Arabic survives, gridline printing on, freeze pane at `A6`, print titles `$1:$5`, landscape. All six OOXML parts parse, and element order matches the `CT_Worksheet` schema sequence.

### Migration

`bench --site <site> migrate` to add `brand_json` to *Insight Menu Settings*. Existing per-browser Brand Kits keep working — they are read from `localStorage` as before and pushed to the site the first time the setup panel is saved.

## v2.54.0 — 2026-07-30

- **PDF button on the General Ledger**, next to Print. Renders server-side through Frappe's wkhtmltopdf and downloads the file directly — no print dialog, no dependence on the operator choosing *Save as PDF*, and no exposure to whatever that machine's *Headers and footers* setting happens to be. Filename carries report, company and period.
- The print document is now built once by `buildPrintDoc()` and consumed by both Print and PDF, so the two cannot drift. Letterhead, theme tokens, column selection and bidi isolation are identical in both.
- New endpoint `neotec_insight.api.pdf.render_pdf`. The client posts the HTML it already assembled rather than the server rebuilding it — the letterhead, themes and bidi handling live in the SPA and duplicating them in Python would guarantee divergence. Because that makes the payload user-supplied markup, the endpoint strips scripts, iframes, objects, embeds, external links, inline event handlers and `javascript:` URLs, caps the payload at 12 MB, and runs wkhtmltopdf with `--disable-local-file-access` and JavaScript disabled so the renderer cannot be turned into a filesystem read. Login is required (standard whitelist).
- Page geometry is passed through from the Brand Kit (orientation, paper) with zero wkhtmltopdf margins, since the document supplies its own padding.

### Note
Page numbers come from the `@page` margin box, which wkhtmltopdf does not implement. A server-rendered PDF therefore has no `Page X / Y` even with *Browser header & footer* set to Show. Print → Save as PDF still numbers pages. If numbering matters on the PDF button, wkhtmltopdf's own `--footer-center` can supply it — say so and I'll wire it.

## v2.53.0 — 2026-07-30

- **The General Ledger workbook now has a real header, not two text rows.** Previously the company name and period were flattened into a single cell and nothing repeated or froze — scroll down and the sheet lost all context; print it and pages 2+ arrived bare.
  - Each identity line gets its own row — company name, Arabic name, VAT / CR, period — matching the print letterhead instead of one concatenated string.
  - The letterhead block and the column-header row **freeze on screen**, so they stay put while scrolling the ledger.
  - The same rows are set as Excel **Print Titles**, so they repeat on every printed page of the workbook.
  - **Excel's own page header and footer** are populated: company left, report title centre, period right, footer text left and `Page N of M` right — the workbook's equivalent of the print letterhead, rendered by Excel on every page.
  - Page setup ships with the sheet: landscape (following the Brand Kit orientation), fit-to-width, A4.
- Verified by round-trip: the workbook is generated, reopened and its freeze pane, print titles, header/footer and page setup read back.

### Known gap
The logo is still not embedded. This writer hand-rolls the xlsx package to get cell styling that the bundled `xlsx` community build can't write, and an image needs a drawing part, a media part and its own relationships. The Excel page header carries the company name as text in its place.

## v2.52.0 — 2026-07-30

- **The browser's own print header and footer are suppressed by default, on every report.** Chrome and Edge draw a date, the document title and the URL into the page margin; no stylesheet can target them, and until now it depended on each operator unchecking *Headers and footers* in their own print dialog. The Brand Kit now zeroes the `@page` margin and re-creates the white space as printed body padding, so the browser has nowhere to draw them. Applies to the General Ledger, reports and pivot alike.
- **Trade-off, made explicit in the panel:** a page counter can only live in an `@page` margin box, so suppressing removes `Page X / Y` too. Brand Kit ⚙ → *Browser header & footer* switches back to **Show** if page numbers matter more, and the Page numbers selector greys out while suppression is on.
- **Fixed: the date range printed backwards** in the browser header and the Save-as-PDF filename — `30-07-2026 → 01-01-2026`. An Arabic company name gave the whole title an RTL base direction, reversing the two dates around the arrow. The period is now isolated, so it reads correctly whatever script the company name uses.

## v2.51.0 — 2026-07-30

- **The Years tab no longer disappears under Date range.** It was disabled whenever Period mode was *Date range* — correctly, since Years compares fiscal-year totals and an arbitrary range has nothing to compare — but greyed at `opacity: 0.4` over `--text-faint` it rendered as roughly `#d8d6d3` on white and read as a missing feature rather than a blocked one. Clicking it now switches Period mode back to Fiscal year and opens the view: one click instead of two, and the dependency explains itself through the action rather than through a `title` tooltip that disabled buttons don't reliably surface. Changing Period mode *to* Date range while in Years still falls back to Period, unchanged.
- Disabled view tabs are legible again — `opacity: 0.65` on `--text-muted` — so a blocked tab anywhere in the strip reads as inactive instead of absent.

## v2.50.3 — 2026-07-30

- **Fixed: `ReferenceError: tk is not defined` on Print.** v2.50.1 introduced `printVarsCss(tk)` in the shared frame without ever declaring `tk`, so every print path — General Ledger, reports, pivot — threw the moment it was invoked and took the view down with it. Bundlers do not error on an undefined global reference, so the build passed clean and the fault only surfaced at run time. Affects v2.50.1 and v2.50.2; upgrade past both.
- Both print-colour modes now smoke-tested end to end (frame built, tokens emitted, band markup produced) rather than only type-checked.

## v2.50.2 — 2026-07-30

- **Fixed: generating from a palette looked like it did nothing.** It was applying — but only the *lead* colour was used, and every surface was synthesised from its lightness. A six-grey extraction collapsed to `#F7F7F7 / #EDEDED / #DEDEDE`, within a hair of the stock warm neutrals, so the only visible change was the accent. Palettes of three or more colours are now used in full: entries are placed by lightness — darkest to text, mid tones to borders, light tones to surfaces — so the same six greys now produce `#DBDBDB` and `#BABABA` surfaces that are plainly the pasted palette. Two-colour lists keep the old lead-derived behaviour.
- `--text` is now part of a generated theme. It never was, so a dark palette re-skinned every surface and left the body text at its default.
- **The panel confirms the result.** A generated theme highlighted no preset card, so nothing acknowledged the click. It now shows the resolved swatches — surface, background, two greys, accent, text — with an applied marker, and the textarea reloads the active palette when the picker is reopened.

## v2.50.1 — 2026-07-30

- **Print colours can now follow the app theme.** Brand Kit ⚙ → *Print colours*: **Brand Kit** (default, unchanged — the configured accent and border preset) or **Match app theme**, which reads the tokens currently painted on screen and prints with them. Whatever theme is active — preset, logo-generated or pasted palette — the printed output matches the display.
- **The print documents stopped carrying their own hexes.** Band accent, hairlines, column-header fill, group / subtotal / grand-total rows and body text now resolve through `--th-*` CSS variables emitted once per document, across all three paths (GL, reports, pivot). Previously a theme change stopped at the edge of the screen because every print path had its colours hardcoded.
- Paper stays white in both modes. Tinting the whole sheet burns toner and reads as a background fill rather than as a theme, so the ground colour is not taken from the theme even in Match mode.
- Print-colour-adjust added to the pivot document, which was dropping its header fills when printed.

## v2.50.0 — 2026-07-30

- **New: build a theme from a pasted palette.** Colour theme → *From a palette* takes any hex list from any extraction tool — with or without coverage percentages, tab, comma or space separated (`#FFFFFF	86.91`, `#fff,86.91%`, or a bare `#3F3F3F`). Entries are ranked by saturation × coverage, so the brand colour leads and near-white ground tones don't. Swatches preview as you paste; the result persists like any other theme.
- **Monochrome added to the theme pack** — white ground, four greys, near-black accent (#3F3F3F), generated from a greyscale extraction.
- **Fixed: greyscale sources came out pink.** `paletteFromColors` clamped saturation up to a floor of 0.35 regardless of the input, so any palette or logo without chroma was forced to 35% saturation on hue 0 — red. Sources under 0.08 saturation now derive a genuinely neutral set instead of having a colour invented for them. This also affects logo-generated themes for monochrome logos.

### Not yet themed: print output
The theme engine drives the app's own CSS tokens. The print documents still carry their own hexes (band accent, group and total row fills), configured separately in the Brand Kit ⚙. Feeding the active theme into the print letterhead is the next step — worth doing now that the frame is shared across the GL, reports and pivot.

## v2.49.0 — 2026-07-30

- **Per-element letterhead alignment, Excel-style.** The old single *Center header block* toggle moved everything at once. Each element — Logo, Title, Header text, Company details, Period, Footer text, Generated timestamp — now gets its own Left / Center / Right / Hidden setting, so a logo can sit left while the title centres and the company details go right. Page numbers get their own position (they live in the `@page` margin box, not the table). Brands saved before this release are migrated from `centered` + `logoPos`, so nothing shifts on upgrade.
- **New: free-text Header text block**, placed like any other element.
- **The report and pivot prints moved to the in-flow band** (GL made the move in v2.48.3). This was still live in the Consolidated P&L: the letterhead and logo were being painted over the *Depreciation and Amortization* rows on page 1. Both paths now carry the band in the table's `<thead>` and the strip in `<tfoot>`, which repeat per page without overlapping anything. `position:fixed` is gone from every print path.
- `bandRow()` / `stripRow()` helpers so each print path splices the letterhead the same way instead of inventing its own markup.
- The pivot print's column-header row moved into a real `<thead>` — it was a loose `<tr>` and did not repeat across pages.

### Note on where print layout is defined
There is one Brand Kit per company, stored under `ni-brand:<company>`, and every print path reads it — GL, reports, pivot. The settings panel is still reachable only from the General Ledger ⚙; the reports use those settings but cannot yet edit them. Surfacing the panel from the report screen is the next step.

## v2.48.3 — 2026-07-30

- **Letterhead no longer overlaps the ledger rows.** The band was pinned into the page margin with `position:fixed` and a negative offset; when the browser clamps that offset back into the page box it paints the letterhead over the first rows of every page, and in the pre-print popup — which has no margin box at all — it overlaps continuously. The General Ledger now carries the band inside the table's own `<thead>` and the footer strip in `<tfoot>`, which browsers repeat on every printed page while keeping both in normal flow. In-flow content cannot be painted over rows, so the failure mode is gone rather than re-tuned. Page margins drop to a plain 10/12 mm and `Page X / Y` still comes from the `@page` margin box.
- `buildFrame` gained a `mode` option — `'flow'` (new, used by the GL) returns bare band markup for a thead/tfoot cell; `'fixed'` remains the default so the report and pivot print paths are untouched.
- Column-header styling scoped with `thead tr:not(.pr-lh)` so the strong rule and grey fill land on the column row, not the letterhead.

## v2.48.2 — 2026-07-30

- **Print heading fixed — the company name is now the company's NAME.** The letterhead was printing the ERPNext Company *docname* (the link value) because the display helper was handed the docname as its own fallback, so `company_name` was never read. Fixed in the print band, the styled-Excel title block and the on-screen ledger header. Filters keep using the docname — nothing about selection changed.
- **Letterhead identity block** (Brand ⚙, saved per company): *Company name (as printed)*, *Company name — Arabic* second line, *VAT number* and *CR number*. Blank name falls back to the Company record's `company_name`; blank VAT/CR print nothing. Flows into every branded output.
- **UTF-8 declared on the GL print document.** It was the one print path in the app without `<meta charset>`, leaving the Arabic in account names, Particular and Details at the mercy of the browser's encoding guess.
- **Bidi isolation across the printed ledger.** Every free-text field (account label, Particular, Details, Party, footer, header lines) is emitted inside `<bdi>`, so an Arabic run can no longer reorder the voucher number, digits and dashes around it — the `العملاء التجاريون Receivables Trade - 11201001` class of mangling. Numeric columns are pinned `direction:ltr` + right-aligned instead of logical `end`, so Debit/Credit/Balance never flip. Document declared `dir="ltr"` with `lang` from the active language, matching the app's LTR-grid policy.
- **The General Ledger now prints through the shared Brand Kit frame.** It had carried its own copy of the header CSS with a hardcoded band color and 15px heading, which meant the v2.48.1 Accent color, Heading size and Borders dials did nothing on the ledger. One letterhead builder for every module — no more drift.
- **Header band height is computed from the lines it actually renders** (logo, title, subtitle, name, Arabic name, identity, period) rather than a fixed 16/21 mm guess that ignored the company and period lines. Default layout goes 16 → 17 mm; the band can no longer clip or crowd the first table rows as the block grows.
- **Combo pivot print had no company in its letterhead** — now passed, and its brand is loaded for the run's own company.
- **Document titles carry report + company + period**, so Chrome's own print header and the Save-as-PDF filename read `General Ledger — <Company> — 2026-01-01 → 2026-07-30` instead of a bare `General Ledger`.
- HTML escaping in the GL print path extended to `>` and `"`; blocked pop-ups now report instead of failing silently; Brand ⚙ panel fully translated (45 Arabic strings — it was English-only in AR mode).

## v2.48.1 — 2026-07-29

- **Presentation dials on top of the defaults**: Heading size (S/Default/L/XL), Body size (Compact/Default/Large), Borders preset (Minimal hairline / Classic / Strong), and an Accent color picker — all in the Brand ⚙, saved per company, flowing into the print frame (band border, heading color/size), the GL table rules and total framing, AND the styled Excel (accent header band + title color/size + border weights/colors). Defaults untouched: change nothing and output is byte-identical to v2.48.0.

## v2.48.0 — 2026-07-29

- **Brand Kit — one presentation frame across the solution.** The per-company setup (logo + position + size, centered layout, footer line, page numbers, timestamp, paper/orientation) configured in the GL ⚙ is now the app-wide brand (shared utils/branddoc.ts). Consumers this release: Financial Reports Print/PDF, General Ledger, and the Combo pivot print — identical letterhead bands repeating on every page, auto-adapting to paper size and orientation. All injections guarded: any brand failure falls back to the previous unbranded document byte-for-byte. Roadmap noted: styled-xlsx frames app-wide, then the one-click branded Board Pack.

## v2.47.0 — 2026-07-29

- **GL Excel styled like the screen**: dedicated SpreadsheetML writer (SheetJS community cannot style) — dark column-header band, account bands, opening/sub-total shading, framed report total, thin row rules, real numeric cells, column widths, and the title/company/period block MERGED AND CENTERED. Footer text included.
- **Company logo in print**: logo URL + position (Left/Center/Right) + size (S/M/L) in Print setup, plus "Center header block" — header band height auto-grows for the logo and everything still adapts to paper size/orientation. Saved per company.

## v2.46.0 — 2026-07-29

- **GL print header & footer setup** (⚙ beside Print): header title + subtitle, company name, period, footer text, page numbers (Page X / Y via @page margin-boxes), generated timestamp — with paper size (A4/A3/Letter/Legal) and orientation. Bands repeat on EVERY printed page and auto-adjust to the chosen size/orientation; column headers repeat per page too (table-header-group). Saved per company. Excel title rows mirror the same setup; PDF = Print → Save as PDF.

## v2.45.1 — 2026-07-29

- General Ledger: hide-selected reinstated (display-only, "▸ Show selected" with Clear all reachable while collapsed) — safe now that pagination fixed the transport.
- **Ledger contents**: searchable directory of all selected accounts showing each account's page number; click jumps to that page and scrolls to the account's block (• marks the current page). Appears when the selection spans multiple pages.

## v2.45.0 — 2026-07-29

- **General Ledger pagination** (the user's design): 5/10/25 accounts per page with Prev/Next and "Page X/Y · accounts A–B of N". Each page is a small plain-GET request — Arabic names stay far under the 4094-byte line — renders instantly, and a failing page never blocks others. Selections within one page behave exactly as the classic single-request ledger; chips remain visible. Page size persisted per user; Excel/Print act on the displayed page.

## v2.44.7 — 2026-07-29

- General Ledger ROLLED BACK to its v2.43.2 state at the user's request: chips displayed as before, original GET transport, no batching — restoring guaranteed ledger generation. The header-hide and mass-selection work is parked for a calm revisit. App-wide real error messages retained (independent of GL).

## v2.44.6 — 2026-07-29

- General Ledger: POST with form-encoded BODY (the corrected doFetch encoding) + 25-account batching. The surfaced server message named the real constraint — "Request Line is too large (4583 > 4094)": URL-encoded Arabic account names overflow the 4094-byte request line even at 25 per batch, so GET can never carry mass selections on this server. Body transport has no such limit.

## v2.44.5 — 2026-07-29

- General Ledger transport reverted to GET — the transport that always worked; batching (25/request) keeps URLs short, making the v2.44.2 POST switch (and its 400) unnecessary. Regression closed.
- API errors now show the SERVER'S real message everywhere — _server_messages, the exception's last line, or the raw body — instead of a bare status code. (Body is read once as text; the old double-read returned empty detail.)

## v2.44.4 — 2026-07-29

- POST transport aligned with Frappe native encoding: form-urlencoded body, nulls dropped, objects JSON-stringified — identical semantics to the proven GET path. Fixes the 400 on batched General Ledger runs; applies to all POST endpoints uniformly (payload values unchanged, so outputs unchanged).

## v2.44.3 — 2026-07-29

- General Ledger mass selections: fetches in batches of 25 accounts and stitches blocks + report totals client-side, with batch progress on the Run button. 298 accounts in one request exceeded the gateway timeout; batches keep each request small. Per-account computation unchanged — single-batch runs are byte-identical to before.

## v2.44.2 — 2026-07-29

- General Ledger with mass selections: the run request now goes as POST — 299 bilingual account names exceeded URL length limits as GET, producing "Failed to fetch" before the request reached Frappe. Query/computation unchanged.

## v2.44.1 — 2026-07-29

- General Ledger: the entire selected-accounts section is now hidden by default behind "▸ Show selected" next to the Accounts (N) count — with Clear all still reachable while collapsed. 299 selections cost one line. Display-only; queries and outputs untouched.

## v2.44.0 — 2026-07-28

- **Coverage check**: new button beside Integrity — compares every Income/Expense leaf in the chart of accounts against the accounts mapped into this report (group bindings expanded). Unmapped accounts appear in a table with type and FY value (PCV-excluded), with an "Assign to row" dropdown that creates the Account Flag Mapping on the spot — the exact mismatch vs the native P&L, eliminated account by account.
- **General Ledger header**: selected-account chips collapse beyond 8 into "+N more ▾" (with Show less and Clear all) — 100 selections no longer wreck the layout.

## v2.43.2 — 2026-07-28

- Closer: the Years diagnostic banner is retired (error boundary and fatal overlay remain permanent). Closed-year handling final: Period Closing Voucher excluded on P&L accounts across all GL paths, retained on balance-sheet accounts — reconciling with ERPNext native statements.

## v2.43.1 — 2026-07-28

- PCV exclusion extended to ALL GL sum paths: the main per-month sums (the one priors and yearly totals actually use — v2.43.0 patched only the first query block), the Combo dimension pivot, the P&L drill hierarchy, and the account-drill prior-year sums. Full-year prior columns (Yearly FY2025/FY2024) now show true annual totals on closed years.

## v2.43.0 — 2026-07-28

- ROOT CAUSE of the Years zeros, proven by the diagnostic chain: FY2025 is CLOSED — the Period Closing Voucher reverses all P&L accounts on 31-Dec, so full-year sums telescoped to ~0 (Jan–Jun 9M was real; Jul–Dec included the reversal). The execution engine now excludes Period Closing Voucher rows on Income/Expense accounts (ERPNext-standard), keeping them on balance-sheet accounts so retained earnings stay correct. Fixes Years, full-year priors, FY comparisons and %GRW everywhere.

## v2.42.3 — 2026-07-28

- Diag refinement: prints a populated SOURCE row key, its browser-side sum, its first raw monthly entries, and a JS-side absolute total — splitting "formulas unevaluated for priors" from "value shape mismatch" in one screenshot.

## v2.42.2 — 2026-07-28

- Backend priors self-diagnosis: each prior year returns the exact resolved date window it queried plus its rows_abs_sum; empty priors log to Error Log with full context. Frontend diag line shows the dates — a wrong window (the suspected fiscal-year resolution bug) becomes visible in one screenshot.

## v2.42.1 — 2026-07-28

- Years request aligned byte-for-byte with the proven Period request (granularity month_quarter, default cache, no dimension snapshot). Diagnostic line retained — it must now read 12m-sum=9,059,518.

## v2.42.0 — 2026-07-28

- Years: stitch+matrix logic PROVEN correct by offline simulation (2025→9,059,518 from a realistic payload). Added a self-diagnosis line above the matrix showing what the live payload actually carried (priors count, rows, 12-month sample sum) so any remaining zero indicts the exact layer on screen. Years request now bypasses the execution cache (use_cache=0).

## v2.41.4 — 2026-07-28

- Years fix: the fetch sent granularity ytd (legacy of the per-year design), whose reshaping zeroed the priors monthly buckets — confirmed by the Period-tab bisect (full-year FY2025/FY2024 correct there). Years now requests plain month granularity and sums months itself.

## v2.41.3 — 2026-07-28

### Years tab — priors stitched into the right nest
- v2.41.2 replaced top-level rows, but YearsMatrix consumes
  result.current.rows — so every year column rendered the current year's
  data. The stitch now replaces the nest the matrix actually reads; each
  prior year shows its own actuals (matching the Period grid's FY columns).

## v2.41.2 — 2026-07-28

### Years tab fixed + KPI polish
- **Years view zeros solved** (a bug on the books since v1.9.55): it fired a
  separate run per fiscal year, which returns zeros on sites without Fiscal
  Year records for past years. It now rides the PRIORS engine — one run with
  prior_years=N, prior-year dates derived arithmetically — the exact
  mechanism the Period grid's FY2025/FY2024 columns prove works. Old
  diagnostic logging retired with the bug it was hunting.
- Duplicate Operating Expenses / Net Profit KPI cards in the P&L Drill
  removed (picker-era leftovers).
- KPI "vs FY" growth shows "—" when the prior-year base is near zero,
  instead of a 17-digit percentage.

## v2.41.1 — 2026-07-28

### Budget grid Excel export
- New "⬇ Excel" beside Template/Import: exports the ACTIVE book's grid
  exactly as shown — every row with its key, all twelve months, the Annual
  column, sections as bands, and formula rows COMPUTED (r_cogs + r_employee
  etc.) — so the entered budget round-trips: export, adjust in Excel, import
  back through the same dimension-aware importer.

## v2.41.0 — 2026-07-28

### Consolidation (part 2)
- **Combo pivot Excel + PDF/Print**: the pivot view exports a real workbook
  (title, FY, dimension columns, widths) and a colour-exact styled print —
  section bands, formula-row highlights, Print-size option honoured. The
  v1.6.1 placeholder is finally paid off.
- **P&L Drill KPI picker**: ⚙ next to the KPI cards — tick which of
  Revenue / Cost of Sales / Gross Profit / Operating Expenses / Net Profit
  appear and reorder with ↑/↓; persisted per user. (Custom row-key KPIs need
  per-key totals from the drill endpoint — scoped for the next feature
  release, the picker UI is ready for them.)

## v2.40.0 — 2026-07-28

### Consolidation (part 1)
- Budget Template/Import gains a dimension selector (Cost Center /
  Department / Project) — backend supported all along, UI now exposes it.
- Background-promise errors show as a dismissible bottom notice (auto-clears
  in 30s) instead of a full-screen box; render crashes keep the full box.
- Removed the stale "Excel / PDF export for pivot view coming in v1.6.1"
  placeholder from the Combo toolbar.
- Part 2 (next session): Combo pivot Excel/PDF export, P&L Drill KPI picker,
  type-debt cleanup, Arabic translation refresh.

## v2.39.5 — 2026-07-28

### Business Division / Employee Cost Center dropdowns fixed
- Dimension values only fetched on mouse-down while the placeholder claimed
  "Loading…" — a lazy loader behind a misleading label. Values now
  eager-load as soon as the Accounting Dimensions are discovered; the
  dropdowns arrive populated with their value counts, and the placeholder is
  honest from the first render.

## v2.39.4 — 2026-07-28

### Print crash fixed
- The v2.38.6 print-size code read `m.headerRows`, but the styled matrix
  returns `headerTop`/`headerSub` — Print threw before opening the window
  (the overlay's async `.length` rejection). Column count now reads the real
  property; Print renders with the size option applied.

## v2.39.3 — 2026-07-28

### Stale-HTML ghost neutralised
- The error reports from "v2.39.2" carried the OLD bundle hash
  (index-62XEUSka.js): Frappe's website cache was serving a stale
  insight.html pointing at the previous build — the second cache layer to
  masquerade as an unfixed bug (Redis payloads were the first).
- www/insight.py added with no_cache = 1: the SPA shell is never cached, so
  every deploy is live on the next refresh. Contains the v2.39.2 import fix
  unchanged.

## v2.39.2 — 2026-07-28

### Expander crash fixed — the red box named it
- The error boundary reported the exact fault: `injectExpandedQuarters is
  not defined` — the helper's IMPORT was missing in RunTab (and the print
  path failed identically), while vite bundles without type-checking and
  tsc was exiting early on a tsconfig deprecation. Import added; tsconfig
  repaired so the type gate actually runs; the grid and Print both render.

## v2.39.1 — 2026-07-28

### Crash containment + diagnosis release
- Defensive guards on period months in the grid cells and the quarter
  expander (missing/foreign shapes can no longer throw).
- A React error boundary now wraps the results grid, and a global
  fatal-error overlay catches anything outside it: a crash renders a red box
  with the exact message and stack instead of a silent white page —
  screenshot of the box = complete bug report.
- Backend period_order for "Quarterly only" verified clean (q0–q3 with
  months arrays) via direct unit execution.

## v2.39.1 — 2026-07-28

### Grid crash guards + on-screen diagnosis
- Hard guards on period months everywhere in the grid cell loop and the
  quarter-expansion helper (a period without a months array can no longer
  throw).
- New error boundary around the results grid: a render error now shows a
  red diagnostic box with the actual message and stack instead of a blank
  white page — a screenshot of the box IS the bug report.

## v2.39.0 — 2026-07-28

### Expandable quarters (+/−) — the user's design, and the better one
- "Quarterly only" (and any granularity without a month tier) now shows a
  +/− button on each quarter header: + expands that quarter's three months
  inline before the quarter total, − collapses. Purely client-side over data
  the grid already holds — no backend period_order, no cache keys, no
  selection echo, so the entire failure class behind the quarter-frame saga
  is structurally impossible here. Expansion state persists per user and is
  honoured identically by Print / Excel / PDF / CSV.
- The "Quarter frame" granularity is retired from the menu (superseded);
  backend support remains for compatibility. Apr–Jun quarter view is now:
  Quarterly only → press + on Q2.

## v2.38.6 — 2026-07-27

### Print size option
- New "Print size" selector in the options row: **Auto (fit columns)** —
  default — scales the print font to the number of VISIBLE columns (quarter
  frame + hidden columns prints large and readable; a full 12-month grid
  compresses to fit), or fixed Compact / Medium / Large. Persisted per user;
  cell padding scales with the font so the page fills properly instead of
  leaving the bottom half blank.

## v2.38.5 — 2026-07-27

### Stale-cache ghost exorcised
- Root cause of "still broken after deploying the fix": the run payload is
  cached in Redis, which SURVIVES deploys — and the first broken
  quarter-frame run (v2.38.1-era, normalised to monthly) was cached under
  the quarter_frame key and replayed on every test since, across four
  deploys.
- Two permanent fixes: (1) every execution cache key is now salted with the
  app version, so ANY deploy invalidates ALL cached payloads; (2) the
  run_report cache key now includes sel_from/sel_to, since the selection
  changes the period_order.
- No functional changes beyond caching; v2.38.4's quarter-frame builder
  (unit-verified: Q1 · Apr · May · Jun · Q2 · Q3ᶠ · Q4ᶠ) now actually
  reaches the browser.

## v2.38.4 — 2026-07-27

### Quarter-frame — fixed at the true source
- The grid renders the BACKEND's period_order, not the client builder — so
  the frame had to be built server-side. build_period_groups now has a
  dedicated quarter_frame branch producing the authoritative order
  (verified: Q1 · Apr · May · Jun · Q2 · Q3(future) · Q4(future) for an
  Apr–Jun selection), with the selection window passed through run_report.
- Column hides (Columns ▾) now filter the backend period_order client-side,
  so unticking Q1 finally removes it from the grid; exports already honour
  the same hide list via the client builder.

## v2.38.3 — 2026-07-27

### Quarter-frame — the second real bug
- The BACKEND period builder normalised the unknown granularity
  'quarter_frame' to plain 'month' and echoed that back, so the frontend
  never entered the frame branch — the grid showed Jan…Dec monthly columns
  regardless of selection. 'quarter_frame' (and 'quarter_ytd') are now in the
  backend allow-list and echo verbatim.
- KPI cards / YTD totals under quarter_frame now aggregate the USER's
  selected window (sel_from…sel_to), not the full-year fetch — this also
  kills the astronomical "% of budget" figures caused by the window
  mismatch.

## v2.38.2 — 2026-07-27

### Drill engine fix — the mismatch's real killer
- `import re` was missing at module level in report.py: the v2.38.0
  definition-driven bucket builder crashed on its first regex, the safety
  net swallowed the error, and the drill silently fell back to the old
  account-type heuristics — which is why totals still disagreed after
  deploying. Import added; buckets now genuinely come from the report
  definition.
- Fallbacks are no longer silent: failures write to the Error Log, and the
  drill header shows a provenance badge — "✓ definition (N accounts
  mapped)" or "⚠ heuristic" — so the engine in use is always visible.

## v2.38.1 — 2026-07-27

### Quarter-frame fix
- The user's month selection (e.g. Apr–Jun) is now echoed by the run
  endpoint (sel_from/sel_to) and used by the period builder — previously the
  full-year fetch window masqueraded as the selection, so ALL twelve months
  expanded and no quarter was flagged future. Apr–Jun now renders
  Q1 · Apr · May · Jun · Q2 · Q3 · Q4 with Q3/Q4 honouring the
  Future-quarters Budget/Blank option, on screen and in every export.

## v2.38.0 — 2026-07-26

### Engine unification — P&L Drill obeys the report definition
- The Combo P&L Drill's Revenue / Cost of Sales buckets now come from the
  SELECTED REPORT'S DEFINITION: the gross-profit formula's operands are
  expanded to their source rows and those rows' account mappings (group
  bindings expanded to descendants) become the buckets. Employee Cost bound
  under Total cost of revenue now lands in Cost of Sales in the Drill too —
  Standard and Drill can no longer disagree. Heuristics remain only for
  unmapped accounts.

### Quarter-frame layout (Run tab granularity)
- New granularity "Quarter frame": ALL FOUR quarter totals always shown, the
  selected months expanded in place — Jan–Mar → Jan Feb Mar Q1; Apr–Jun →
  Q1 Apr May Jun Q2; Jul–Sep → Q1 Q2 Jul Aug Sep Q3; Oct–Dec → Q1 Q2 Q3 Oct
  Nov Dec Q4. Data fetches the full year so past quarters carry real actuals.
- **Future quarters** header option: Actual column of not-yet-selected
  quarters shows the BUDGET figure (muted) or stays BLANK — persisted, and
  exports render exactly what the screen shows.
- **Columns ▾** hide control: untick any quarter and it disappears from
  screen, Print, Excel, PDF and CSV together (single-point filter in the
  shared period builder).

### Deferred to v2.38.1
- The Drill KPI picker (choose/reorder/extend KPI cards from row keys).

## v2.37.4 — 2026-07-26

- Version bump release (no functional changes over v2.37.3). Contains the
  complete budget-import fix set: derived book slug/label and lowercase
  draft status.

## v2.37.3 — 2026-07-26

### Budget import fix (2)
- Status value corrected to the doctype's lowercase options: imported books
  are created as "draft" (was "DRAFT" → 417).

## v2.37.2 — 2026-07-22

### Budget import fix
- "417 Slug is required": imported books now derive their slug (docname) and
  label automatically — "FY2026 · Cost Center: <value>" — with uniqueness
  handling. This is also why the template has no book-name column BY DESIGN:
  fiscal year + dimension value ARE a book's identity; naming is the
  engine's job, not the spreadsheet's.

## v2.37.1 — 2026-07-22

### One-click region preset (site-level, zero manual edits)
- Menu Setup gains **Region preset**: 🇸🇦 KSA (shows VAT Return, Zakat, Export
  Packs; hides GST; western digits) and 🇮🇳 India (shows GST; hides VAT/Zakat/
  Packs; lakh/crore digits). One click + Save applies the tab visibility AND
  the site-wide number format for every user of the site.
- The site number format is stored in the menu settings and applied on load
  for all users — unless a user explicitly picked their own format in the
  theme panel, which always wins for that user.

## v2.37.0 — 2026-07-22

### Budget bulk import (KSA + India, same engine)
- **⬇ Template** in the Budget tab: styled xlsx with every cost center × every
  source row × M1–M12 (M1 = the company's fiscal-year first month — Jan for
  KSA, Apr for India). **⬆ Import** parses the filled file in the browser and
  writes it in one pass: one DRAFT book per cost center (created or updated)
  plus all cells, with a validation summary — unknown row keys skipped,
  dimension values checked against ERP, warnings listed. Departments,
  projects and custom dimensions import through the same endpoint
  (dimension_type parameter).

## v2.36.1 — 2026-07-22

### One-click T-format from the existing P&L
- New "⚡ Create T-format copy of this report" button in the Rows designer
  (visible while the report is Vertical). Clones the definition, switches it
  to T-Account and AUTO-CLASSIFIES every row's T-side from the vertical
  structure: Revenue sections → Trading credit ("Other …" income → P&L
  credit), Cost-of-sales sections → Trading debit, remaining expense
  sections → P&L debit, the Gross Profit formula → GP balancer, Net
  Profit/Income → NP balancer; total formulas excluded (the T view computes
  side totals). Bilingual keywords (إيراد/تكلفة/مجمل/صافي) recognised.
- The copy is fully editable — heuristics are the starting point; fine-tune
  any row's T-side, labels, headings and positions in the designer as usual.

## v2.36.0 — 2026-07-21

### India Pack
- **GST tab** (Compliance → GST (India)): GSTR-3B-style summary — Output tax
  vs Input Tax Credit per head (CGST, SGST/UTGST, IGST, Cess), net payable,
  outward/inward taxable values from invoices, GSTIN from the Company tax_id,
  voucher drill per head, CSV with the standard header. Detection follows the
  ERPNext India account-naming convention from the GL.
- **Export Packs**: two new sheet components — **GST Sales Register (B2B)**
  and **GST Purchase Register (ITC)** — invoice-level with party GSTIN and
  per-invoice CGST/SGST/IGST/Cess split read from the invoice taxes tables.
  Build an India audit pack in the designer like any other.
- **Lakh/crore digit grouping**: theme panel now offers 1,234,567 vs
  12,34,567 (en-IN grouping) — applies app-wide including exports, persisted
  per user.
- KSA-only tabs (VAT Return, Zakat) can be hidden per site via Menu Setup.

### T-format Profit & Loss — already yours
- The traditional Indian two-column Trading and P&L exists since v1.9.48:
  Rows tab → Presentation format → **T-Account**; classify each row's T-side
  (Trading Dr/Cr, Gross Profit balancer, P&L Dr/Cr, Net Profit balancer) with
  inline "Less:" deductions (Sales less Returns). Labels, headings and
  positions are the standard rows designer. No code change needed.

## v2.35.2 — 2026-07-21

### Budget book ↔ filters, two-way sync
- The budget book is the MASTER of comparison scope: explicitly picking a
  cost-center / project / department book mirrors its value into the run
  filters (actuals, drills and KPI cards all scope to the budget's
  dimension); picking Total Company clears the mirrored dimensions.
- Combined with v2.35.1's filter→book auto-select, the sync is two-way:
  touch either side and the other follows, so a budget can never be compared
  against actuals of a different scope by accident.

## v2.35.1 — 2026-07-21

### Budget comparison — priority bug fixed + auto-select
- **The "budget not refreshing" bug**: when the Total Company book was active
  and the report had a primary budget axis, the engine summed the axis books
  FIRST and ignored the Total book's own cells — so creating a single
  cost-center book silently replaced the whole-company budget. Priority is
  now correct: the Total book's OWN cells always win; the automatic roll-up
  of cost-center books applies only when the Total book is empty.
- **Auto-select**: picking exactly one Cost Center in the run filters
  switches Compare-to to that centre's budget book automatically (when one
  exists for the FY); clearing the filter returns to the Total Company book.
- A "Budget source" caption under the Compare-to picker states exactly which
  book (or roll-up of N books) produced the budget column — no more guessing.

## v2.35.0 — 2026-07-16

### Budget books on any Accounting Dimension
- The New-book modal now offers **Custom (Accounting Dimension)**: pick the
  dimension (Branch, Business Division, Employee Cost Center, or any other
  Accounting Dimension configured in ERPNext), then its value — values load
  live from ERP. The comparison engine already honoured
  custom_dimension_fieldname; creation now sets it, so Actual-vs-Budget on a
  Business Division book filters actuals to that division automatically.

## v2.34.3 — 2026-07-16

### Bill-wise — always runnable
- With no parties selected, the Bill-wise report now runs for the TOP 20
  parties by absolute outstanding balance (button reads "Bill-wise report
  (Top 20)"), instead of sitting disabled. Tree or search selection narrows
  it as before; the hint explains the default.

## v2.34.2 — 2026-07-16

### Bill-wise — compact selection, report enablement made obvious
- Search results no longer spill across the page as a wall of chips: they
  open in a bounded, scrollable dropdown under the search box (click to add /
  ✓ to remove, Done to close). The selected parties collapse into a
  "Selected (N) ▾" popover with per-party remove and Clear all — the report
  starts right below the controls again.
- The Bill-wise report button shows the effective party count and, when it
  is disabled, a hint explains why: pick up to 20 parties via the tree or
  search — "All parties" is too many for a bill-wise statement.

## v2.34.1 — 2026-07-16

### "Account comparisons" checkbox
- The prior-year & growth values on account drill rows are now a display
  OPTION — a fifth checkbox next to % Growth / % of Revenue / % Achieved /
  Variance. Ticked by default; unticking returns drill rows to Actual + % of
  Revenue only, on screen and in every export. The choice persists per user.

## v2.34.0 — 2026-07-16

### Party tree (Summary + Bill-wise)
- New Customers/Suppliers TREE picker — grouped by Customer/Supplier Group,
  collapsible, searchable, group-level tick with indeterminate state, All /
  None shortcuts. Summary defaults to ALL selected; unticking filters the
  report. The same selection feeds Bill-wise (chips still add on top).

### ZATCA PDF — Arabic company heading fixed
- jsPDF's built-in fonts cannot render Arabic, so the taxpayer name printed
  as mojibake. The company heading is now drawn by the BROWSER (which shapes
  Arabic correctly) onto a canvas and embedded as a crisp centered image —
  any script works. Duplicate Latin 'Taxpayer' line removed.

### Export Pack — reconciliation block rebuilt
- The register footer is now a labeled mini-table (Reconciliation / Amount /
  VAT) whose arithmetic CHAINS: Invoiced in period − Deferred + Included from
  other periods = VAT for this return. Previously numbers landed under
  unrelated register columns and the base figure did not chain — confusing,
  now fixed.

### P&L account drill — comparison values
- Account rows under a source row now show REAL prior-year values (FY-1) and
  growth %, on screen and in all four exports, alongside Actual and % of
  Revenue. Budget and %ACH remain row-level by design: budget books bind
  amounts to report rows, not to chart-of-accounts entries.

## v2.33.0 — 2026-07-16

### Summary AR/AP Ageing — actual allocation (now the default)
- The summary ageing follows the LEDGER like Bill-wise: each payment settles
  exactly the invoice it was applied to (against_voucher from Payment Entry
  references); receipts/payments not applied to any invoice appear as
  negative amounts aged by their own date. Summary and Bill-wise now share
  the same allocation logic and can never disagree.
- New "Allocation" selector (persisted): **Actual (as linked)** — default —
  or **FIFO (estimate)** for ledgers where references are not maintained.
  The header badge and footnote state which mode produced the numbers.

## v2.32.1 — 2026-07-16

### Bill-wise — ACTUAL allocations + unallocated sections
- Allocation now follows the LEDGER, not FIFO: a payment or credit note is
  applied to exactly the invoice its GL row names in against_voucher (what
  ERPNext writes from Payment Entry references). Partial payments show one
  allocation row per referenced invoice with that bill's remaining balance.
- Receipts/payments with NO invoice reference are kept out of the bill flow
  (main rows marked "(unallocated)") and listed in a separate red-headed
  section at the bottom of each party — "Unallocated receipts — received from
  customer, not applied to any invoice" (AR) / "Unallocated payments — paid
  to supplier, not applied to any purchase" (AP) — with a total, on screen,
  in Print/PDF and in CSV.
- The summary AR/AP Ageing intentionally keeps FIFO (aggregate ageing);
  bill-wise is now the exact ledger truth.

## v2.32.0 — 2026-07-16

### VAT drill — per-invoice include/exclude checkboxes
- Every invoice row in a box drill has a checkbox (default: all counted).
  Unticking excludes the invoice from THIS return (governed VAT Adjustment,
  reason prompted, audit-trailed); excluded invoices move to a red-headed
  "Excluded from this return — VAT not payable this period" section at the
  bottom of the drill with their reasons, and can be re-included with one
  tick. Included-from-other-period rows show green. Return, Export Pack
  registers (green/red rows + reconciliation) and drill all stay consistent
  because they share the same adjustments engine.

### Export Pack — GL ledger sheet options
- "Print the opening balance row" toggle (on by default).
- Custom-ledger mode now offers a MULTI-SELECT of the company's leaf accounts
  instead of typing account names.

### Ageing — exclusions + Bill-wise Analysis
- "Excluded ▾" manager: parties under dispute / legal hold are kept off the
  AR/AP report — searchable add, one-click restore, persisted per side.
- New **Bill-wise** view: pick multiple customers/suppliers; each renders a
  stacked per-party statement (multi-ledger concept adopted for bills):
  documents in date order, FIFO payment allocation with Against Voucher,
  per-bill remaining balance, cumulative balance, party subtotal, and an
  "Open bills (aged)" table using the user's slabs. Print/PDF (colour-exact)
  and CSV included.

### Export format consistency (app-wide)
- New shared csvHeader standard: every CSV now opens with the same block the
  PDFs carry — Company / Report / Period / Generated (dd-mm-yyyy) — applied
  to VAT Return, Ageing, Bill-wise and Cash Flow CSVs; future reports use the
  same helper so no format is ever "naked" versus another.

## v2.31.0 — 2026-07-14

### Report exports mirror the screen (Excel / PDF / CSV / Print)
- Exports now reproduce the EXACT on-screen expand/collapse state: expanded
  source rows carry their account drill rows (indented, Actual columns) into
  every format; collapsed rows export collapsed. One shared matrix builder
  feeds all four formats, so they can never disagree.
- Export header now reads "<Company Name> — <Report Name>" instead of
  "Neotec Insight — …", and the duplicated company name is removed from the
  subtitle line (it stays when the scope differs from the company).

### Section-scoped expand / collapse
- New "Sections ▾" multi-select next to Expand all / Collapse all: tick any
  sections (Revenue, Cost of revenue, …) and Expand/Collapse selected acts on
  those sections' rows only.

### Formula row keys (report designer)
- Every non-section row now shows an editable KEY in the designer. Formula
  rows can be referenced by other formulas (rows evaluate top-to-bottom, so
  chaining like total_admin + total_ga + total_selling works) — and renaming
  a key automatically rewrites every formula that references it.

## v2.30.0 — 2026-07-13

### New Reports tab: Ageing (AR / AP)
- Receivables and payables aged per party, GL-based with **FIFO open-item
  allocation** — payments settle the oldest items first, and unallocated
  payments/advances appear as negative amounts aged by their own date
  (matching the client's manual workbook, including its negative rows).
- **User-defined slabs**: comma-separated boundaries (e.g. 30,60,90,120)
  aged in **days or calendar months** — the entered slabs persist per mode
  until the user changes them.
- **Top 5 / 10 / 20 / All** parties with an aggregated "Others" row and grand
  total; slab summary cards; ageing base switchable between due date and
  posting date; columns mirror the client workbook (Invoiced / Paid /
  Outstanding / slabs / Total Due); CSV export; advances shown in red
  parentheses.

## v2.29.0 — 2026-07-13

### VAT period adjustments — the accountant's green/red rows, governed
- New ⇄ Adjustments panel on the VAT Return: INCLUDE an out-of-period invoice
  in this return (e.g. a government/SWA invoice whose VAT falls due on
  payment, paid this quarter) or EXCLUDE/defer an in-period invoice (unpaid,
  VAT due in its payment quarter). Direction is inferred from the invoice
  date; a REASON is mandatory and every adjustment is stored per period with
  owner and timestamp — the audit trail the manual workbook approximated.
- The 16-box return recomputes with adjustments applied (both sales and
  purchase sides); a summary strip shows counts and the net VAT effect.
- Export Pack registers render included rows GREEN and deferred rows RED
  (visible but never totalled), and append an automated reconciliation
  footer: In-period + Included from other periods − Deferred = VAT for this
  return — the exact footer the accountant was building by hand, bilingual.
- New `Insight VAT Adjustment` DocType; validation refuses meaningless
  adjustments (excluding an out-of-period invoice, including an in-period one).

## v2.28.0 — 2026-07-13

### Export Packs — configurable audit workbooks (new Compliance tab)
- A PACK IS CONFIGURATION, NOT CODE: an ordered list of sheet components,
  each with schema-driven options. New client requirement → new pack in the
  designer, no build. New sheet types added to the backend catalog appear in
  the designer automatically.
- Sheet component catalog: **VAT Return (16 boxes)** · **Sales VAT Register**
  (invoice- or item-level, customer VAT numbers from tax_id, credit notes as
  negatives) · **Purchases VAT Register** (Purchase Invoices + non-invoice
  sources such as Expenses Entry from the strict-tagged Input VAT GL) ·
  **GL Ledger** (Output VAT / Input VAT tagged accounts or any custom account
  list, with opening row and running balance).
- Per-sheet column picker, bilingual EN/AR headers (or single-language),
  totals rows, dd-mm-yyyy dates, styled server-side workbook.
- Seeded default pack **"VAT Breakdown (Quarterly)"** replicating the live
  client's manual workbook sheet-for-sheet (return + item-level sales &
  purchase registers + both VAT GL ledgers). Seed is idempotent and never
  overwrites user edits.
- The ZATCA 16-box return tab is untouched — packs are additive.

## v2.27.1 — 2026-07-13

### VAT Return — account detection corrected (contamination fix)
- The old detector treated every Liability/Asset account containing 'ضريبة'
  as VAT: WHT accrual accounts (ضريبة الإستقطاع) fed Output VAT (that is why
  WHT journals like the Feb accrual appeared in the return), and the VAT
  settlement account (تسوية) pulled the quarterly ZATCA Sadad payment in as a
  large negative output line.
- Heuristics hardened: tax-named NON-VAT accounts (WHT/withholding/استقطاع,
  zakat, income tax, settlement/تسوية/Sadad) are excluded from auto-detection.
- NEW Classification tags: **Output VAT (sales)**, **Input VAT (purchases)**,
  **Not VAT (exclude)**. Tagging any account on a side switches that side to
  STRICT mode — only tagged accounts count, heuristics off. Accounts tagged
  as anything else are always excluded from VAT detection. Recommended for
  every client: tag the real VAT control accounts once and the return becomes
  fully deterministic.

## v2.27.0 — 2026-07-12

### Exports that look like the report
- **Styled Excel export** (server-side, openpyxl): the workbook mirrors the
  on-screen report — dark header, group bands, Sales/Returns bands, subtotal
  and grand-total styling, thousands separators, real date cells formatted
  DD-MM-YYYY, auto column widths — plus a letterhead block (company name,
  address, contacts, VAT number). Pivot exports keep the client-side path.
- **Print / PDF with colours and letterhead**: print output now embeds the
  selected ERPNext Letter Head (header + footer HTML) and forces
  print-color-adjust so band colours survive into printed pages and
  browser-saved PDFs. Generated date on every print.
- **Letterhead picker in the Studio toolbar** — this is where the user defines
  the printed header. Designs are managed in ERP under "Letter Head"; the
  choice persists per user and drives Print/PDF and Excel alike.

### Dates
- Filter values on Date/Datetime fields accept the formats humans type:
  01012026, 010126, 01/01/2026, 1-3-26, 31.03.2026, ISO — normalised
  day-first (dd-mm-yyyy) unless the first token is 4 digits (ISO). Fixes the
  "417: not a valid date string" on prompted parameters.
- The parameter prompt shows native DATE PICKERS for Date fields.
- Date columns render dd-mm-yyyy on screen, in print and in Excel.

### Usability
- Saved reports now appear as cards on the "Pick a document to begin" empty
  state — one click to open, no digging for the Open-saved dropdown.

## v2.26.0 — 2026-07-12

**BI intelligence release: Account Classification Studio, the Dataset wizard
with live preview, prompted report parameters — and the ERP menu fix.**

### Account Classification Studio (🏷 in Financial Health)
- Tag any account once; every report obeys: COGS → Gross Margin, Inventory /
  Payable Days, Cash Conversion Cycle; Cash / Investing / Financing /
  Provision → Cash Flow buckets and the Zakat base. User tags OVERRIDE
  account_type and every name heuristic (resolution: your tag → account_type
  → auto), fixing the classic untyped-direct-costs CoA without touching
  ERPNext. Stored per company in the new `Insight Account Tag` DocType.
- CUSTOM labels — management's own vocabulary ("Direct Project Costs") —
  taggable now, summarised via the new label_summary endpoint, and the
  foundation for the v2.27 Management P&L layout designer.

### Dataset wizard (Studio → Datasets → New dataset)
- Model straight against the DocType meta: explicit aggregation per measure
  (sum/avg/min/max/count), proper business names, deliberate dimensions,
  Submitted-only base filter — and a LIVE PREVIEW on real data before saving
  (new preview_dataset endpoint, same validation/permission path as
  run_dataset). "Save as Dataset" from the Builder remains as the shortcut.

### Prompted report parameters (Studio Builder)
- Any filter can be marked "Ask": opening the saved report prompts for those
  values before running — reusable parameterised reports (date range,
  customer, cost centre) without rebuilding. Parameters persist in the report
  config; schedules use the stored defaults.

### Fixes
- ERP menu: the v2.24.0 nav switched to overflow-x:auto, which clipped the
  ERP dropdown invisible. Nav now wraps instead of scrolling; the menu opens.

## v2.25.1 — 2026-07-10

### Colour themes — expanded pack + instant image palettes
- Six new presets (12 total): Rose Quartz, Graphite Steel, Sandstone Coffee,
  Teal Oasis, Burgundy Reserve, and Indigo Night (a second dark theme) —
  alongside Classic, Desert Gold, Emerald Riyadh, Royal Violet, Ocean Blue and
  Midnight.
- "Or from any image": pick ANY image file (brand artwork, brochure, photo)
  and the palette generates instantly through the same extractor as the
  Company-logo option. The image is read locally in the browser (FileReader →
  canvas) — it is never uploaded. Generated palette persists like any theme.

## v2.25.0 — 2026-07-10

### Menu setup — manage the navigation from the frontend
- New ☰ button (visible to users with edit access) opens the Menu Setup
  modal: reorder sections and tabs (arrow controls), rename sections, move
  tabs between sections, hide tabs, create new sections, and Reset to default.
- Layout is stored SITE-WIDE in the new `Insight Menu Settings` Single DocType
  (write gated to System Manager / Accounts Manager); everyone sees the
  arranged menu on next load.
- Robust merge on boot: saved order wins; tabs unknown to the running build
  are dropped; tabs added by newer versions are appended to their default
  section automatically — a stale saved layout can never hide new features or
  break the shell. At least one visible tab is enforced on save.

## v2.24.1 — 2026-07-10

### Navigation — two-level menu (replaces the dropdown grouping)
- Row 1 = SECTIONS: CFO Briefing · Dashboard · Reports · Studio · Analysis ·
  Compliance · Operations. The active section carries an accent underline.
- Row 2 = the active section's tabs, ALWAYS VISIBLE as a sub-menu strip (pill
  tabs with the section name as a label) — nothing hidden behind dropdowns:
  Reports → Financial Reports / General Ledger / Cash Flow / Visuals;
  Analysis → Financial Health / Group; Compliance → VAT Return / Zakat;
  Operations → Bank / People. Single-tab sections show no sub-row.
- Clicking a section returns to its last-visited tab. Utilities (theme dots,
  language, backup, ERP) stay right-aligned in row 1.

## v2.24.0 — 2026-07-10

**Phase 2: the semantic layer, live cross-filter dashboards, scheduled
distribution — plus the Zakat tab and a grouped navigation redesign.**

### Semantic layer — Insight Dataset
- New `Insight Dataset` DocType: a governed model over a base DocType with
  named MEASURES (field + aggregation, defined once) and DIMENSIONS, plus base
  filters baked into every query. "Net Revenue" now means one thing everywhere.
- Studio gains a third mode — **Datasets** — an explorer that aggregates any
  combination of a dataset's measures by any of its dimensions instantly
  (chart + table + totals). "Save as Dataset" turns the current query builder
  state into a dataset in one click.
- All dataset queries run through frappe.get_list (user permissions enforced)
  with every fieldname validated against meta before touching SQL.

### Live dashboards — cross-filtering
- Clicking any bar / slice / point on a Studio Dashboard tile broadcasts that
  category as a filter to EVERY tile; chips show active cross-filters. The
  backend silently drops the filter on doctypes that lack the field, so
  heterogeneous tiles coexist safely.
- Dashboard-level date range broadcasts posting_date to all tiles.

### Scheduled distribution
- New `Insight Report Schedule` DocType + full implementation of the
  previously stubbed daily scheduler: Daily / Weekly (weekday) / Monthly
  (day-of-month, clamped) cadences, dispatched on the long queue.
- Delivery: email with XLSX or CSV attachment (openpyxl), grand totals in the
  body — and an optional WhatsApp text summary via the WhatsApp Business Cloud
  API when site_config provides whatsapp_token / whatsapp_phone_id (silently
  skipped otherwise). Manage from Studio → Schedules; "Run now" for testing.

### New tab: Zakat (Compliance)
- Zakat base estimation by the equity (indirect) method: equity excl. profit,
  adjusted net profit, long-term funding & provisions, less net fixed assets /
  CWIP / long-term investments — each expandable to account level. Base
  floored at adjusted profit per ZATCA practice (raw base shown when the floor
  applies). Hijri 2.5% / Gregorian 2.577683% toggle. Clearly labelled as a
  preparation estimate for advisor review.

### Navigation redesign
- The 13-button flat bar is now grouped: Reports · CFO Briefing · Dashboard ·
  Studio stay direct; **Statements** (General Ledger, Cash Flow),
  **Compliance** (VAT Return, Zakat), **Analytics** (Financial Health,
  Visuals, Group), **Operations** (Bank, People) become dropdowns whose
  trigger shows the active tab's name. Utilities (theme, language, backup,
  ERP) sit right-aligned; the theme button now renders three colour dots in
  pure CSS — no icon-font dependency.

## v2.23.0 — 2026-07-10

**Studio → BI Phase 1, Cash Flow statement, and the Colour Theme pack.**

### Studio
- **Child-table sources.** Line-level doctypes (Sales Invoice Item, Purchase
  Invoice Item, …) are now first-class report sources, scoped to their primary
  parent via parenttype and permission-checked against the parent DocType. A
  synthetic `Parent (…)` link exposes every parent field (customer, dates,
  status) as joined columns — product-mix and item-margin reporting unlocked.
- **Time Intelligence.** One click computes MTD, prior-MTD, QTD, YTD,
  prior-year YTD, rolling 12M and prior 12M for up to six measures, with MoM%,
  YoY% and Δ12M% server-side; windows respect the ERPNext Fiscal Year (KSA
  Jan-start supported), optional split by the Group-by field.
- **Chart drill-through.** Click any bar, point or slice to drill into its
  underlying rows; a drill chip shows the active drill and clears it.

### New tab: Cash Flow
- Statement of Cash Flows (indirect method) assembled from the GL: net profit,
  depreciation add-back, working-capital changes, investing, financing — each
  section expandable to account level, reconciled to the actual bank & cash
  movement with any residual shown explicitly as Unclassified (never hidden).
  Opening entries and Period Closing Vouchers excluded. CSV export.

### Colour themes
- **Theme pack:** Insight Classic, Desert Gold, Emerald Riyadh, Royal Violet,
  Ocean Blue, Midnight (dark) — applied live via the CSS design-token system,
  persisted per user.
- **Theme from your company logo:** Insight samples the Company logo, extracts
  the dominant brand colours (saturation-weighted hue quantisation), and
  generates a full matching palette — accent family, tints, and
  brand-tinted surfaces. One click per company in the new palette picker.

# 2.21.2
- VAT Return: input VAT is now detected on ALL VAT control accounts (by name, not only accounts flagged account_type=Tax), so input VAT posted by Payment Entries, Journal Entries and custom expense-entry apps is included — previously only Purchase Invoices were counted. Added an 'Other input VAT (non-invoice)' reconciliation line so boxes 7-11 tie back to the GL total in box 12.

# 2.21.1
- AI analysis (Financial Health) and Ask AI now fail gracefully when the AI endpoint is unreachable: instead of a hard 417 error, they return an actionable message and log quietly. Note that 'host.docker.internal' only resolves in local Docker; on Frappe Cloud set a publicly reachable AI Endpoint in Insight AI Settings or turn AI off.

# Changelog — Neotec Insight

All notable changes are recorded here. Versions follow the package
`__version__` in `neotec_insight/__init__.py`, which is what the in-app badge
and Frappe Cloud report.

## [2.21.0] — 2026-06-24

### Added — Default Row Expand on every report; selectable backup
- **Default Row Expand** (Expanded / Collapsed) is now on-screen in the
  **Profit & Loss Statement**, **Balance Sheet**, and **Trial Balance** views too
  (it was already on Consolidated P&L), applied on each run across Period,
  Dimension and Combo. (Layout / P&L Drill is P&L-specific, so it stays on the two
  P&L reports.)
- **Backup is now selectable.** The Configuration backup lists each area —
  report definitions, account map, mapping rules, budgets, equity, dashboards,
  variance notes, quick links, AI settings — each with its record count, and you
  tick only the areas you want. Ideal for test environments where only part of
  the setup matters. Backend `export_configuration(sections)` +
  `config_section_counts`.
## [2.20.0] — 2026-06-24

### Added — Layout option in the Profit & Loss Statement combo
- The standalone **Profit & Loss Statement** report (a separate runner from
  Consolidated P&L) now has the **Layout** selector in its Combo view: **Cross-tab**
  (the existing two-dimension pivot) or **P&L Drill (with subtotals)**, which
  renders the hierarchical P&L via `pl_hierarchy` using the chosen outer/inner
  dimensions and the view's date range, finance book, and dimension filters.
- Note: Layout/P&L-Drill is P&L-specific, so it is intentionally not added to the
  Balance Sheet or Trial Balance statement views (a P&L drill doesn't apply there).
## [2.19.0] — 2026-06-24

### Added — on-screen "Default Row Expand" at the report header
- The Expanded/Collapsed choice (previously only in the report definition) is now
  a live selector in the Reports filter strip. The user decides on screen; it
  drives both the period **Matrix** drill and the **P&L Drill** hierarchy, and
  re-applies when toggled. Still initialises from the definition's default.

### Note — Layout in every view (from v2.18)
- The **Layout** selector (Standard / Cross-tab / P&L Drill with subtotals) is
  available across all report sub-tabs, not just Combo. Picking a pivot layout
  switches into the Combo presentation; Standard keeps the current view.
## [2.18.0] — 2026-06-24

### Added — on-screen row expand/collapse + Layout in every view
- **Expand all / Collapse all** controls now sit above the report matrix, so you
  decide on screen whether rows start expanded or collapsed — overriding the
  definition's "Default Row Expand" without editing the report.
- **Layout** (Standard / Cross-tab / P&L Drill with subtotals) is now available
  in **every view** (Period, Dimension, Years, Combo), not just Combo. "Standard"
  keeps the current view; choosing a pivot layout switches into Combo.
## [2.17.0] — 2026-06-24

### Added — people-liabilities line in the CFO brief
- The CFO morning brief now shows a single **People liabilities** figure —
  **EOSB + annual vacation + ticket + insurance** — as a headline stat and a
  ranked alert that breaks the total down by component. Ticket/insurance are
  included when their source is configured (People → Configure sources),
  otherwise the alert notes which sources are unset.
## [2.16.0] — 2026-06-24

### Added — app-wide Print / PDF
- Print/PDF is now a **shared utility** (`utils/printDoc` + a reusable `PrintBar`
  toolbar) with the **definable letterhead** (organisation name, address, logo —
  saved once, used everywhere) and **Portrait/Landscape** that adjusts the
  full-width header to the page.
- Any report can print two ways: custom HTML (used by the **Bank Reconciliation
  Statement**, including the balance bridge) or an on-screen element carrying the
  app styles. The **People & Payroll** view now prints with the same header.
- The main Reports tab keeps its existing letterhead export. Future reports get
  print by dropping in `<PrintBar/>`.
## [2.15.1] — 2026-06-24

### Changed — clearer "Recent payroll runs"
- Added a **Month** column (e.g. "Apr 2026") so runs are easy to read, plus the
  **payroll run ID** and a **For** column (branch/department or frequency) so
  multiple runs in the same month are distinguishable.
## [2.15.0] — 2026-06-24

### Added — bank reconciliation balance bridge (book → bank)
- The Report tab now shows the auditor-grade **balance bridge** above the
  statement: starts from the **GL (book) balance**, adds back **payments issued
  but not presented**, removes **deposits in transit**, and adjusts for
  **bank-side items not yet booked** (charges/credits) to arrive at the
  **expected bank statement balance** — all computed from posted data.
- Enter the **actual statement closing balance** to get the **difference**
  (green ✓ at zero, red ⚠ otherwise), pinpointing anything unaccounted.
- The bridge is included in the **printed/PDF statement**, completing the
  classic Bank Reconciliation Statement (bridge + detailed lines).
- New endpoint `reconcile.reconciliation_bridge`.
## [2.14.0] — 2026-06-24

### Fixed
- **Misleading payroll variance.** When no salary slips exist for the selected
  period (e.g. the current month before payroll is run), the variance no longer
  shows a large negative "paid below master". It now reads "—  payroll not
  processed yet" with guidance to pick a completed period.

### Added — people provisions (vacation, ticket, insurance)
- **Annual vacation provision** — Saudi entitlement (21 days <5y, 30 days ≥5y;
  overridable) valued on each active employee's daily wage.
- **Annual ticket provision** and **Insurance provision** with a **configurable
  source**: read an Employee numeric field, a Salary Component (annualised ×12),
  or a fixed amount per employee. "Configure sources" panel with pickers;
  choices saved in settings. Shows "not set" until configured.
## [2.13.0] — 2026-06-24

### Added — People tab: period selector, defined-vs-processed, slab-wise EOSB
- **Period selector** (this/last month, this/last quarter, half-year, YTD, last
  12 months) scoping the salary figures, with the resolved date range shown.
- **Defined (master) vs Processed (actual) vs Variance**: defined payroll comes
  from the active Salary Structure Assignment (committed per contracts),
  processed from submitted Salary Slips (actuals), plus **Additional Salary** for
  the period and the **variance** (actual − defined) with a plain-language read
  of what's driving it (overtime/off-cycle vs LOP/joiners-leavers).
- **EOSB provision is now slab-wise**: tap the card to expand a per-employee
  table — years of service, base wage, the ½-month×first-5-years slab, the
  1-month×beyond-5-years slab, and the per-employee total, with slab subtotals.
- Next-payroll in the CFO brief now uses the **defined (master)** total as the
  forward commitment.

## [2.12.0] — 2026-06-24

### Added — People (HR) tab + payroll/accruals in the CFO brief
- New **People** tab: headcount (with Saudization % when nationality is on file),
  monthly payroll cost, next payroll, **accrued unpaid salary**, **EOSB
  provision** (Saudi Labour Law: ½ month/yr first 5 years, 1 month/yr after, on
  each active employee's latest base wage), headcount by department, and recent
  payroll runs. Reads Frappe HR (Employee, Salary Slip, Salary Structure
  Assignment, Payroll Entry); shows a clear notice if HR isn't installed.
- The **CFO morning brief now reflects payroll**: next-payroll vs cash (flagged
  High when payroll exceeds cash), accrued unpaid salary, and the EOSB provision
  — so salaries and accruals move the CFO dashboard, not just the HR view.

## [2.11.0] — 2026-06-24

### Added — CFO morning brief (CFO "brain", stage 1)
- New **"What needs your attention"** panel at the top of CFO Briefing. A
  deterministic engine (`cfo.morning_brief`) computes the facts a CFO checks
  first — **cash & runway, overdue receivables (worst-aged, top debtors),
  payables due soon, VAT payable, revenue MoM/YoY, gross margin, unreconciled
  bank items** — straight from posted GL/AR/AP/VAT/bank data.
- It then **ranks what matters** into severity-tagged alerts (High/Medium/Low)
  with a recommended action each, and writes a short **CFO narrative**. The
  narrative is generated **locally via Ollama** when configured, with a
  deterministic fallback — no data leaves the server.
- This is stage 1 of the CFO brain; cash-flow forecast, margin cause-analysis,
  and a threshold/alert engine follow.

## [2.10.0] — 2026-06-24

### Added — printable reconciliation statement with definable header
- **Print / PDF** button on the reconciliation report. Opens a clean,
  print-ready Bank Reconciliation Statement and triggers the browser print
  dialog (save as PDF from there).
- **Definable header** ("Define header"): organisation name, address/sub-header,
  and logo URL — saved in settings and rendered full-width on the printout, so
  it adjusts to the page in either orientation.
- **Portrait / Landscape** selector; the page (`@page size`) and the full-width
  header + table reflow to the chosen orientation.

## [2.9.2] — 2026-06-24

### Changed — cleared date sourced from the bank statement
- The reconciliation report's **Cleared Date** now comes from the imported bank
  line's **value date** (when it actually cleared on the statement) rather than
  the often-empty voucher clearance_date — so it is never blank for a reconciled
  row.
- **Confirm** and **Book charge** now stamp the voucher's `clearance_date` with
  the bank value date, keeping ERPNext consistent. Added **"Sync cleared dates
  to vouchers"** (`backfill_clearance`) to stamp already-reconciled vouchers.

## [2.9.1] — 2026-06-24

### Added — Bank Reconciliation Statement (document view)
- The Report tab now shows, below the KPI cards, a **document-style Bank
  Reconciliation Statement**: each bank line with its value date, bank
  reference, amount (in/out), the **reconciled document type and number**
  (Payment Entry / Journal Entry), the **document date**, **cleared date**,
  party, and status (Reconciled / Open). Open items are highlighted.
- Backed by `reconcile.reconciliation_report` (period-filterable), suitable as
  the CEO/CFO bank-reconciliation evidence document.

## [2.9.0] — 2026-06-24

### Added — fee/VAT split, reconciled view, reconciliation report
- **Bank fee handling.** Fee lines (INSTANT PAYMENT FEES / BANK FEES) share the
  transfer's reference, so they previously matched the transfer's entry with a
  wrong amount. The matcher now detects them and proposes **"Book charge"** —
  posting a Journal Entry that debits **Bank Charges** and **Input VAT
  (recoverable)** and credits the bank, then reconciles the fee line to it.
- **Account pickers** for Bank Charges and Input VAT in the Reconcile tab,
  remembered (Insight AI Settings) and used when booking fees.
- **Reconciled view** — toggle to see what's matched (bank line → voucher) with
  an **Unmatch** button to undo.
- **Report sub-tab** — bank reconciliation status: reconciled %, open items,
  bank charges, **recoverable input VAT (ZATCA)**, incoming/outgoing for the
  period. Backed by `reconcile.reconciliation_summary`, ready to feed the
  CEO/CFO views.

## [2.8.0] — 2026-06-24

### Added — reconciliation matcher (Milestone 2, part 2)
- New **Reconcile** sub-tab in the Bank view. For each unreconciled Bank
  Transaction it proposes candidate vouchers — **Payment Entries and Journal
  Entries** — ranked in confidence passes:
  1. **Exact reference** — statement REF == Payment Entry Reference No / Journal
     Entry Cheque No.
  2. **Cited invoice** — an incoming line naming a Sales Invoice (SINV …) matches
     that invoice's Payment Entry.
  3. **Amount + date** — same amount and direction within an adjustable window
     (Tight −1/+3, **Standard −3/+7**, Wide −7/+14), ranked by date closeness,
     with a bonus when the beneficiary IBAN matches.
- **Propose-only**: nothing is reconciled until you click **Confirm** on a match.
  Confirming submits the Bank Transaction (if it was a draft) and allocates it to
  the chosen voucher; **Unmatch** undoes it.
- Statement import now stores the invoice ref + counterparty IBAN in the Bank
  Transaction description (feeding passes 2–3) and **submits** transactions on
  import so they are reconcilable.

## [2.7.1] — 2026-06-24

### Fixed — account statement (Excel) parsing
- Statement Import now **locates the real header row** instead of assuming row 1,
  so bank exports with a title/summary preamble (client, account number, balance)
  parse correctly. Handles merged-cell column gaps, `SAR` prefixes on amounts,
  and newlines inside cells; pulls the **account number from the preamble** for
  bank-account matching; cleans `REF`/Arabic noise from references.
- Validated end-to-end on the real Riyad Bank Excel export: **124 transactions,
  all reconcile against the running balance (0 mismatches)** — vs ~85% from PDF.
- Preview now shows a **running-balance reconciliation check** (✓ all reconcile,
  or a warning with the mismatch count) as a parse-confidence signal.

## [2.7.0] — 2026-06-24

### Added — current-account statement support (Statement Import)
- Statement Import now auto-detects and parses **current-account statements**
  (Value Date / Details / Reference / Transaction Type / Credit / Debit /
  Balance) in addition to merchant settlement files. CSV + Excel (PDF export is
  not reliable across bank layouts — tested on a 13-page Riyad statement at ~85%
  via coordinates, so CSV/Excel is recommended).
- Per-line normalisation: direction from Credit/Debit, **bank fee + VAT-on-fee
  extracted** from fee-line narration, **Sales Invoice references** (SINV …) and
  counterparty IBANs captured, and **fee lines grouped with their transfer** by
  shared reference (one entry ↔ transfer + fee line).
- Account statements import as one **Bank Transaction per line** (deposit=credit,
  withdrawal=debit), idempotent on reference. The UI shows incoming/outgoing
  totals, fees, VAT, and invoice-citing counts before import.

### Notes
- These are the inputs the reconciliation **matcher** will consume next: group
  by reference (covers transfer+fee), match outgoing to Payment Entries and
  incoming to Sales Invoices, validate against the running balance.

## [2.6.1] — 2026-06-24

### Fixed
- Re-reading an existing slip now **re-classifies its direction** under the
  current logic instead of returning the stale saved value (slips read before
  the v2.6.0 classifier could show the wrong direction). Manual ▴/▾ corrections
  are preserved and never overwritten.

## [2.6.0] — 2026-06-23

### Added — inward/outward direction classifier
- Slips are now classified **Incoming vs Outgoing** by anchoring on the
  company's own identity: account numbers + IBANs read from **company Bank
  Account records**, plus name tokens (IRSAA / ارساء, editable in AI Settings).
  Arabic is NFKC-normalized so presentation-form text still matches. Rule: if
  the company is the **beneficiary/recipient** -> Incoming; if it's the payer /
  statement holder -> Outgoing; if it can't be identified -> left unconfirmed.
  Validated on the three live slips: SNB & Riyad -> Outgoing, Alinma -> Incoming
  (previously all showed as payments).
- Applied authoritatively on the text path and injected into the LLM/vision
  prompt; the basis is shown in the review panel with one-click **Out/In**
  override (`set_slip_direction`).

## [2.5.0] — 2026-06-23

### Added — Statement Import (Milestone 2, part 1)
- **Statement parser** (`statement_reader`) for bank/merchant statements (CSV +
  Excel; PDF rejected with guidance to use CSV/Excel). Tolerant header mapping,
  DD/MM/YYYY and thousands handling. Validated on a real BSF merchant
  e-statement: 31 card transactions → 2 settlement batches with correct gross /
  fees / VAT / net.
- **Batch summarisation** — lines grouped into settlement batches (the unit that
  hits the bank); net deposit = gross − fees − VAT.
- **Import to ERPNext Bank Transactions** — `preview_statement` (review, no
  writes) and `import_statement` (creates Bank Transactions per batch deposit,
  or per transaction), idempotent on reference. Bank account matched by account
  number or picked.
- **Bank tab: "Statement Import" sub-tab** — upload, preview batches with
  gross/fees/VAT/net, pick the bank account, import. Sits beside the Slip Reader.

## [2.4.1] — 2026-06-23

### Changed
- Bank tab: slip amounts are now colour-coded by direction — **payments
  (outgoing) in red, receipts (incoming) in green**, with a small ▴/▾ marker —
  in both the list and the review panel.

## [2.4.0] — 2026-06-23

### Added — reader review: descriptions, account pickers, image & Excel
- **Description / narration captured and shown.** Slips without a clean
  beneficiary field (Riyad utility bill, Alinma transfer) now surface their
  Detail/Narration/Description text, plus a "Show full slip text" view, so the
  user can read the context and choose the account.
- **Account picker** ("book against") — searchable leaf GL accounts; and a
  **Bank account picker** (source / paid-from). Both persist on the slip and
  flow into the staged Payment Entry (paid_from from the bank account's GL
  account; paid_to from the chosen account when there's no party).
- **Image reading** — scanned/image slips now go through the Ollama vision model
  (Qwen2.5-VL): OCR + structure in one call, Arabic + English.
- **Excel reading** — `.xlsx/.xls` single slips are flattened and structured.
  (Multi-row bank *statements* belong to reconciliation, Milestone 2.)
- **Duplicate guard** — re-reading a slip with a bank reference already staged
  returns the existing slip instead of creating a second.

## [2.3.1] — 2026-06-23

### Fixed
- **Stage Draft Payment Entry: "Source Exchange Rate is mandatory" (417).**
  `stage_draft_payment_entry` now sets `source_exchange_rate` /
  `target_exchange_rate` (1.0 for same-currency payments in a same-currency
  company; the slip's own rate or a looked-up rate otherwise) and the paid
  from/to account currencies, so the draft Payment Entry validates and is
  created. (ERPNext runs the exchange-rate check even under ignore_mandatory.)

## [2.3.0] — 2026-06-23

### Added — Bank tab in the Insight app
- A **Bank** tab in the Insight navbar: upload a slip, see it parsed, review the
  extracted fields, pick the Supplier/Customer from a rich search dropdown, and
  **Stage Draft Payment Entry** (opens the draft for approval). Lists recent
  staged slips with status. This is the in-app home the reader was missing; the
  reconciliation engine (Milestone 2) will live in the same tab.

## [2.2.0] — 2026-06-23

### Added — Bank Slip Reader (Milestone 1, engine)
- **`bank_reader` pipeline** — reads a bank transfer/payment slip and stages it
  for approval, end to end:
  - **Extraction:** PyMuPDF for digital PDFs; vision path (Ollama Qwen2.5-VL)
    reserved for scanned/image slips.
  - **Structuring:** LLM-first via local **Ollama** (open-source, on-prem,
    PDPL-safe) returning the normalized slip schema, with a deterministic
    label-based **fallback** so the reader works offline. Validated on three
    live KSA slips (Riyad Bank 200.00 · SNB 12,051.75 · Alinma 13,566.15) —
    amounts, fees, VAT, bank, and reference all parse correctly.
  - **Staging:** `read_slip(file_url)` creates an `Insight Bank Slip`
    (status=Extracted); `stage_draft_payment_entry(slip)` creates a **draft,
    unsubmitted** Payment Entry carrying the bank reference — nothing posts
    without human approval.
- **Model picker:** `list_ollama_models()` returns the models actually pulled
  on the Ollama host; a "Fetch Ollama Models" button in Insight AI Settings
  lists them and one-click assigns text/vision slots (no typed tags).
- **Party picker:** `search_parties(party_type, txt)` returns full
  supplier/customer detail (name, tax id, group, currency, contact) for the
  review dropdown, plus `party_type`/`party` fields on the slip — the user's
  pick drives the draft entry's party instead of a fragile name match.
- **Embedded in Payment Entry & Journal Entry.** A "Read Bank Slip" button on
  both forms (via `doctype_js` hooks) uploads a slip, auto-fills the document
  (`read_slip_into` returns a per-doctype field map: amount, reference,
  dates, remarks), and for Payment Entry opens the rich party dropdown to set
  Supplier/Customer. Works the same in one app — no separate app needed to hook
  the core accounting documents. Each fetch also stages an Insight Bank Slip for
  the audit trail / reconciliation reference.
- **Ollama config** added to Insight AI Settings (URL + text/vision models),
  overridable via `site_config`. No keys, no cloud, data stays on-prem.
- Declares **PyMuPDF** as an app dependency.

## [2.1.0] — 2026-06-23

### Added — Bank milestone (foundation)
- **`Insight Bank Slip` doctype** — the normalized staging schema for bank
  transfer/payment slips read from PDF/image/Excel. Captures direction, amount,
  fee, VAT, computed total, currency + conversion, source & counterparty
  accounts, value/processing dates, purpose, and crucially the **bank
  reference** that becomes the reconciliation key. Links to the Payment Entry /
  Journal Entry / Bank Transaction it produces. Validated against three live KSA
  bank slips (Riyad Bank, SNB, Alinma).

## [2.0.2] — 2026-06-23

### Fixed — frontend updates now actually reach the browser
- **Cache-busting of the main bundle.** The Vite build emitted the entry and CSS
  at fixed names (`index.js` / `index.css`), and `insight.html` referenced those
  exact URLs — so after every deploy the browser kept serving the *cached* old
  bundle. Backend changes appeared immediately while anything frontend (the
  version badge, the P&L Drill, Arabic edit controls) stayed frozen. Entry and
  CSS are now content-hashed (`index-<hash>.js` / `index-<hash>.css`) and the
  generated `insight.html` references the hashed names, so each deploy forces a
  fresh fetch. This is the fix for "version not updating / changes not
  reflecting" on the UI.
- **Version badge now reflects the real build.** The badge was a hardcoded
  string (`v1.9.96`) in `App.tsx`, so it never changed regardless of the actual
  version — which made it look like deploys weren't landing even when the
  backend updated. It now reads the build version injected by Vite
  (`__APP_VERSION__` from `package.json`), so a successful frontend deploy shows
  the true version. `package.json` and `__init__.py` are both at 2.0.2.

## [2.0.1] — 2026-06-23

### Fixed / Hardened
- **`after_migrate` can no longer abort a site migrate.** Each default-seeding
  step now runs in isolation: a failure is logged via `frappe.log_error` and
  skipped instead of raising. Previously, one seed error would fail the whole
  bench's `bench migrate`, blocking the deploy rollout for every app on the
  bench. (No functional change when seeds succeed.)

## [2.0.0] — 2026-06-23

### Added
- **P&L Drill (Combo tab).** New hierarchical Profit & Loss drill-down:
  Primary dimension (e.g. Cost Center) → Secondary dimension (e.g. Intercompany)
  → P&L section (Revenue / Cost of Sales / Operating Expenses) → Account.
  Gross Profit, Net Profit and margins are computed at every node, with a
  grand-total strip, revenue-share per primary node, and Expand/Collapse.
  Backend: `report.pl_hierarchy` (single grouped GL query, read-only).
- **General Ledger account tree picker.** "Browse tree" opens the chart of
  accounts; tick a group to select all leaf accounts under it (resolved via
  nested-set lft/rgt), plus Select all / Clear. Backend: `report.account_tree`.
- **Arabic translation overrides.** New `Insight Translation Override` doctype
  + `ai.save_translation_override` / `ai.delete_translation_override`. In Arabic
  mode, account names show an inline ✎ editor; edits persist and apply
  immediately. An amber dot flags names still falling back to English.
  `ai.arabic_labels` now layers: user override → source field → English name.
- **Explicit EBITDA add-back tagging** wired end to end: the calculation now
  consumes `Insight EBITDA Addback` tags (explicit → typed Depreciation →
  bilingual/Islamic-finance keyword fallback), so EBIT/EBITDA are exact and
  naming-independent.
- **Integrity & coverage audit** for reports (coverage %, double-count, orphan
  mappings, empty rows, new members).
- **VAT Return:** ZATCA 16-box PDF export.

### Fixed
- **Trial Balance — group rows now reconcile to the totals.** Group rows show
  gross debit/credit per side instead of being re-netted, so the top-level group
  lines sum exactly to the column totals even when groups contain contra
  accounts (e.g. accumulated depreciation under Assets).
- **Trial Balance — ordering.** Rows are emitted in tree order with siblings
  sorted by account number (1, 2, 3, 4, 5, 6) instead of nested-set order.
- **General Ledger — stale results.** Clearing the account selection now clears
  the previous ledger output instead of leaving it on screen.
- **`run_trial_balance_multi_period` 500 (NameError: flt).** `flt` is now
  imported at module level in `report.py`.
- **CSRF endpoint.** Restored the missing `report.get_csrf` function header that
  was causing POST actions (e.g. saving add-backs, applying account types) to
  fail with 400.
- **VAT Return** box drill-down classification and export-country handling.

### Build / deploy
- `pyproject.toml` hardened with `[tool.bench.frappe-dependencies]`.
- Package version bumped to `2.0.0`.

[2.0.0]: https://github.com/jamunachi08/neotec_insight/releases/tag/v2.0.0
