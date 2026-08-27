import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import LinkField from '../../components/LinkField';
import { CashFlowClassificationView } from './CashFlowClassificationView';

/* Cash Flow Forecast (v2.86.0).
 *
 * Fully separate feature — own doctypes, own API module (api/cash_flow_forecast.py),
 * own engine (utils/cash_flow_forecast.py), own frontend folder. Nothing here
 * imports from features/run, features/allocation, or utils/reportdoc. See
 * Cash_Flow_Phase2_Spec.md for why.
 *
 * Direct-method statement: named cash-out categories, cash-in by cost centre,
 * Budget entered by hand against Actual derived from GL cash-leg activity,
 * a monthly bank-balance rollforward, and a reconciliation residual that is
 * never absorbed silently — a nonzero residual means a binding is missing,
 * overlapping, or a transfer was misclassified. That residual is this
 * feature's core trust mechanism; it is rendered prominently on purpose.
 */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

type Binding = {
  name?: string;
  account: string;
  direction_mode: 'Net' | 'Debit Only' | 'Credit Only';
  // v2.86.6 — multiple, mapped once. Kept as a plain string[] here for the
  // UI's sake; toApiShape/fromApiShape convert to/from the Table
  // MultiSelect's nested row shape ({cost_center: string}[]) that Frappe
  // actually stores and returns.
  cost_centers?: string[];
  project?: string;
  party_type?: string;
  party?: string;
};

/** Frappe returns/expects a Table MultiSelect field as a list of child rows
 *  ({cost_center: "X"}, one per selection), not a plain string array — these
 *  two functions are the only place that shape needs to be known, so the
 *  rest of this component can just work with string[]. */
function fromApiShape(line: Line): Line {
  return {
    ...line,
    bindings: (line.bindings || []).map((b: any) => ({
      ...b,
      cost_centers: (b.cost_centers || []).map((row: any) =>
        typeof row === 'string' ? row : row.cost_center),
    })),
  };
}
function toApiShape(line: Line): any {
  return {
    ...line,
    bindings: (line.bindings || []).map((b) => ({
      ...b,
      cost_centers: (b.cost_centers || []).map((cc) => ({ cost_center: cc })),
    })),
  };
}

type Line = {
  name?: string;
  label: string;
  direction: 'Cash Out' | 'Cash In';
  section?: string;
  sort_key?: number;
  is_active?: number;
  dimension_field?: string;
  bindings?: Binding[];
};

type RunLine = {
  line: string;
  label: string;
  direction: 'Cash Out' | 'Cash In';
  section?: string;
  actual: Record<number, number>;
  budget: Record<number, number>;
  binding_count: number;
  by_bank: Record<number, Record<string, number>>;
};

type Transfer = {
  voucher_type: string;
  voucher_no: string;
  from_accounts: string[];
  to_accounts: string[];
  amount_sent: number;
  amount_received: number;
  fee: number;
  fy_position: number | null;
};

type RunResult = {
  fiscal_year: number;
  fy_start_month: number;
  lines: RunLine[];
  cash_in_total: Record<number, number>;
  cash_out_total: Record<number, number>;
  rollforward: Record<number, { opening: number; closing: number }>;
  residuals: Record<number, number>;
  residual_tolerance_pct: number;
  month_labels: string[];
  cash_accounts: string[];
  transfers: Transfer[];
};

type BankAccount = { name: string; account_name: string; account_type: string };
type Company = { name: string; default_currency: string };

function fmt(n: number | undefined): string {
  const v = n || 0;
  if (v === 0) return '—';
  const s = Math.abs(Math.round(v)).toLocaleString('en-US');
  return v < 0 ? `(${s})` : s;
}

export function CashFlowForecastTab() {
  const [view, setView] = useState<'statement' | 'budget' | 'classify' | 'setup'>('statement');
  const [fiscalYear, setFiscalYear] = useState<number>(new Date().getFullYear());
  const [companies, setCompanies] = useState<Company[]>([]);
  const [company, setCompany] = useState<string | null>(null);
  const [bankAccounts, setBankAccounts] = useState<BankAccount[]>([]);
  const [selectedBanks, setSelectedBanks] = useState<string[]>([]); // empty = all
  const [run, setRun] = useState<RunResult | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Line | null>(null);
  const [savingLine, setSavingLine] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [budgetCells, setBudgetCells] = useState<Record<string, Record<number, string>>>({});
  const [budgetLoading, setBudgetLoading] = useState(false);
  const [budgetSaving, setBudgetSaving] = useState(false);
  const [budgetError, setBudgetError] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);
  const [importMode, setImportMode] = useState<'history' | 'statement'>('history');
  const [importFileB64, setImportFileB64] = useState<string | null>(null);
  const [importFileName, setImportFileName] = useState<string | null>(null);
  const [importPreview, setImportPreview] = useState<any>(null);
  const [importResult, setImportResult] = useState<any>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [drill, setDrill] = useState<{ line: RunLine; monthIdx: number } | null>(null);
  const [drillTransactions, setDrillTransactions] = useState<any[] | null>(null);
  const [drillTxLoading, setDrillTxLoading] = useState(false);
  const [drillTxError, setDrillTxError] = useState<string | null>(null);
  const [showTransfers, setShowTransfers] = useState(false);

  const loadLines = useCallback(async () => {
    const rows = await api.cashFlowForecastLines(false);
    setLines((rows || []).map(fromApiShape));
  }, []);

  const loadCompanies = useCallback(async () => {
    const rows = await api.cashFlowForecastCompanies();
    setCompanies(rows || []);
    // Per the customer's request: auto-select when there's exactly one
    // company, leave the dropdown for the user to choose when there's more.
    if (rows && rows.length === 1) setCompany(rows[0].name);
  }, []);

  const loadBankAccounts = useCallback(async () => {
    const rows = await api.cashFlowForecastBankAccounts(company);
    setBankAccounts(rows || []);
    // Company changed — the previous bank selection may no longer apply.
    setSelectedBanks([]);
  }, [company]);

  const loadRun = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.cashFlowForecastRunFiltered(fiscalYear, company, selectedBanks);
      setRun(r);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [fiscalYear, company, selectedBanks]);

  useEffect(() => { loadLines(); loadCompanies(); }, [loadLines, loadCompanies]);
  useEffect(() => { loadBankAccounts(); }, [loadBankAccounts]);
  useEffect(() => { if (view === 'statement') loadRun(); }, [view, loadRun]);

  const loadBudget = useCallback(async () => {
    setBudgetLoading(true);
    setBudgetError(null);
    try {
      const grid = await api.cashFlowForecastBudgetGrid(fiscalYear, company);
      const cells: Record<string, Record<number, string>> = {};
      for (const [line, months] of Object.entries<any>(grid || {})) {
        cells[line] = {};
        for (const [m, v] of Object.entries<any>(months || {})) {
          cells[line][parseInt(m, 10)] = String(v);
        }
      }
      setBudgetCells(cells);
    } catch (e: any) {
      setBudgetError(e?.message || String(e));
    } finally {
      setBudgetLoading(false);
    }
  }, [fiscalYear, company]);

  useEffect(() => { if (view === 'budget') loadBudget(); }, [view, loadBudget]);

  function setBudgetCell(line: string, month: number, value: string) {
    setBudgetCells((prev) => ({ ...prev, [line]: { ...(prev[line] || {}), [month]: value } }));
  }

  async function saveBudget() {
    if (budgetSaving) return;
    setBudgetSaving(true);
    setBudgetError(null);
    try {
      // Only cells with a real, parseable value are sent — a cell left
      // blank was never entered and must stay that way server-side too
      // (blank contributes nothing to totals; a saved 0 is a real zero).
      // Clearing a PREVIOUSLY-saved cell back to blank in this screen does
      // not delete the underlying record — save_budget_grid only inserts
      // or updates, never deletes. Overwrite with an explicit 0 instead if
      // that's the intent.
      const payload: Record<string, Record<string, number>> = {};
      for (const [line, months] of Object.entries(budgetCells)) {
        const clean: Record<string, number> = {};
        for (const [m, v] of Object.entries(months)) {
          if (v !== '' && v != null && !Number.isNaN(Number(v))) clean[m] = Number(v);
        }
        if (Object.keys(clean).length) payload[line] = clean;
      }
      await api.cashFlowForecastSaveBudgetGrid(fiscalYear, payload, company);
      await loadBudget();
    } catch (e: any) {
      setBudgetError(e?.message || String(e));
    } finally {
      setBudgetSaving(false);
    }
  }

  function onImportFilePicked(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setImportError(null); setImportPreview(null); setImportResult(null);
    setImportFileName(f.name);
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || '');
      // FileReader.readAsDataURL gives "data:<mime>;base64,<payload>" —
      // only the payload half is what the backend's base64.b64decode wants.
      const b64 = result.split(',')[1] || '';
      setImportFileB64(b64);
    };
    reader.readAsDataURL(f);
  }

  async function doPreviewImport() {
    if (!importFileB64) return;
    setImportBusy(true); setImportError(null);
    try {
      const res = importMode === 'history'
        ? await api.cashFlowForecastPreviewImport(importFileB64)
        : await api.cashFlowForecastPreviewStatementImport(importFileB64, null, fiscalYear);
      setImportPreview(res);
    } catch (e: any) {
      setImportError(e?.message || String(e));
    } finally {
      setImportBusy(false);
    }
  }

  async function doCommitImport() {
    if (!importFileB64 || !importPreview) return;
    const confirmMsg = importMode === 'history'
      ? `Import ${importPreview.new_count} new classified transactions? This cannot be bulk-undone.`
      : `Create ${importPreview.new_line_count} new line(s) and write ${importPreview.total_budget_cells} budget figure(s)?`;
    if (!window.confirm(t(confirmMsg))) return;
    setImportBusy(true); setImportError(null);
    try {
      const res = importMode === 'history'
        ? await api.cashFlowForecastCommitImport(importFileB64)
        : await api.cashFlowForecastCommitStatementImport(importFileB64, null, fiscalYear);
      setImportResult(res);
      setImportPreview(null);
    } catch (e: any) {
      setImportError(e?.message || String(e));
    } finally {
      setImportBusy(false);
    }
  }

  function closeImport() {
    setShowImport(false);
    setImportFileB64(null); setImportFileName(null);
    setImportPreview(null); setImportResult(null); setImportError(null);
    loadLines(); // a statement import may have created new lines
  }

  function switchImportMode(mode: 'history' | 'statement') {
    setImportMode(mode);
    setImportFileB64(null); setImportFileName(null);
    setImportPreview(null); setImportResult(null); setImportError(null);
  }

  function toggleBank(name: string) {
    setSelectedBanks((prev) => prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]);
  }

  const loadDrillTransactions = useCallback(async () => {
    if (!drill) return;
    setDrillTxLoading(true); setDrillTxError(null);
    try {
      const res = await api.cashFlowForecastLineTransactions(
        fiscalYear, drill.line.line, drill.monthIdx, company, selectedBanks);
      setDrillTransactions(res.transactions || []);
    } catch (e: any) {
      setDrillTxError(e?.message || String(e));
    } finally {
      setDrillTxLoading(false);
    }
  }, [drill, fiscalYear, company, selectedBanks]);

  // Frappe's own desk document URL, e.g. "Payment Entry" -> /app/payment-entry/<name>.
  // Standard convention, not something this app needs to look up per doctype.
  function deskUrl(voucherType: string, voucherNo: string) {
    const slug = voucherType.toLowerCase().replace(/\s+/g, '-');
    return `/app/${slug}/${encodeURIComponent(voucherNo)}`;
  }

  const sections = useMemo(() => {
    if (!run) return [];
    const bySection = new Map<string, RunLine[]>();
    for (const l of run.lines) {
      const key = l.section || (l.direction === 'Cash Out' ? 'Cash Out' : 'Cash In');
      if (!bySection.has(key)) bySection.set(key, []);
      bySection.get(key)!.push(l);
    }
    return Array.from(bySection.entries());
  }, [run]);

  async function saveLine(line: Line) {
    if (savingLine) return; // guards the double-click / slow-network double-submit
    setSavingLine(true);
    setSaveError(null);
    try {
      const saved = await api.cashFlowForecastSaveLine(toApiShape(line));
      await loadLines();
      setEditing(fromApiShape(saved));
    } catch (e: any) {
      const msg = e?.message || String(e);
      // Frappe's own wording ("Document has been modified…") is accurate
      // but assumes desk familiarity — reframe it as an action, and offer
      // the fix directly rather than leaving the form stuck on stale data.
      if (/modified after you have opened it/i.test(msg)) {
        setSaveError(t('Someone (or another tab) saved this line after you opened it. Reload it, then redo your change.'));
      } else {
        setSaveError(msg);
      }
    } finally {
      setSavingLine(false);
    }
  }

  async function reloadEditing() {
    if (!editing?.name) return;
    const rows = (await api.cashFlowForecastLines(false)).map(fromApiShape);
    setLines(rows || []);
    const fresh = (rows || []).find((r: Line) => r.name === editing.name);
    setEditing(fresh || null);
    setSaveError(null);
  }

  async function deleteLine(name: string) {
    if (!confirm(t('Delete this line? Past budget entries against it will block this — deactivate instead if unsure.'))) return;
    await api.cashFlowForecastDeleteLine(name);
    await loadLines();
    setEditing(null);
  }

  function newLine(direction: 'Cash Out' | 'Cash In') {
    setSaveError(null);
    setEditing({ label: '', direction, section: direction, sort_key: 0, is_active: 1, bindings: [] });
  }

  function addBinding() {
    if (!editing) return;
    setEditing({
      ...editing,
      bindings: [...(editing.bindings || []), { account: '', direction_mode: 'Net' }],
    });
  }

  function updateBinding(idx: number, patch: Partial<Binding>) {
    if (!editing) return;
    const next = [...(editing.bindings || [])];
    next[idx] = { ...next[idx], ...patch };
    setEditing({ ...editing, bindings: next });
  }

  function removeBinding(idx: number) {
    if (!editing) return;
    const next = [...(editing.bindings || [])];
    next.splice(idx, 1);
    setEditing({ ...editing, bindings: next });
  }

  // v2.86.6 — mapped once: add a cost centre to a binding's list rather
  // than needing a whole extra binding row per cost centre.
  function addCostCenter(idx: number, cc: string) {
    if (!cc || !editing) return;
    const current = editing.bindings![idx].cost_centers || [];
    if (current.includes(cc)) return;
    updateBinding(idx, { cost_centers: [...current, cc] });
  }
  function removeCostCenter(idx: number, cc: string) {
    if (!editing) return;
    const current = editing.bindings![idx].cost_centers || [];
    updateBinding(idx, { cost_centers: current.filter((c) => c !== cc) });
  }

  return (
    <div className="cff-wrap">
      <div className="cff-hdr">
        <div>
          <h1 className="cff-title">
            {t('Cash Flow Forecast')}
            <span className="cff-iso-badge" title={t('Own doctype, own API module, own frontend folder — no shared code with the P&L/report engine or the indirect Cash Flow statement.')}>
              {t('Isolated feature')}
            </span>
          </h1>
          <div className="cff-sub">{t('Direct method — Budget entered by hand, Actual from GL cash-leg activity')}</div>
        </div>
      </div>

      <div className="cff-view-toggle">
        <button className={view === 'statement' ? 'active' : ''} onClick={() => setView('statement')}>{t('Statement')}</button>
        <button className={view === 'budget' ? 'active' : ''} onClick={() => setView('budget')}>{t('Budget')}</button>
        <button className={view === 'classify' ? 'active' : ''} onClick={() => setView('classify')}>{t('Classify')}</button>
        <button className={view === 'setup' ? 'active' : ''} onClick={() => setView('setup')}>{t('Line Setup')}</button>
      </div>

      {view === 'classify' && (
        <CashFlowClassificationView fiscalYear={fiscalYear} company={company} bankAccounts={selectedBanks} />
      )}

      {view === 'statement' && (
        <div className="cff-statement">
          <div className="cff-toolbar">
            <input type="number" className="cff-input" value={fiscalYear}
              onChange={(e) => setFiscalYear(parseInt(e.target.value, 10) || fiscalYear)} />
            {companies.length <= 1 ? (
              <span className="cff-company-fixed">{company || (companies[0] && companies[0].name) || t('(no company)')}</span>
            ) : (
              <select className="cff-input" value={company || ''} onChange={(e) => setCompany(e.target.value || null)}>
                <option value="">{t('— select company —')}</option>
                {companies.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            )}

            <div className="cff-bank-picker">
              <button className="cff-btn-sm" onClick={() => setDrill(null)} disabled>
                {selectedBanks.length === 0
                  ? `${t('All banks')} (${bankAccounts.length})`
                  : `${selectedBanks.length} ${t('of')} ${bankAccounts.length} ${t('banks')}`}
              </button>
              <div className="cff-bank-list">
                <label className="cff-bank-item">
                  <input type="checkbox" checked={selectedBanks.length === 0}
                    onChange={() => setSelectedBanks([])} />
                  <strong>{t('All banks')}</strong>
                </label>
                {bankAccounts.map((b) => (
                  <label className="cff-bank-item" key={b.name}>
                    <input type="checkbox" checked={selectedBanks.includes(b.name)}
                      onChange={() => toggleBank(b.name)} />
                    {b.account_name} <span className="cff-bank-type">{b.account_type}</span>
                  </label>
                ))}
              </div>
            </div>

            <button className="cff-btn-primary" onClick={loadRun} disabled={loading}>
              {loading ? t('Running…') : t('Run')}
            </button>
          </div>

          {error && <div className="cff-error">{error}</div>}

          {run && (
            <>
              {run.transfers.length > 0 && (
                <div className="cff-transfers-bar">
                  <button className="cff-btn-sm" onClick={() => setShowTransfers((s) => !s)}>
                    {showTransfers ? t('Hide') : t('Show')} {t('internal transfers')} ({run.transfers.length})
                  </button>
                  {!showTransfers && (
                    <span className="cff-transfers-hint">
                      {t('Excluded from every line above — money that moved bank to bank, not out of the business.')}
                    </span>
                  )}
                </div>
              )}
              {showTransfers && (
                <div className="cff-transfers-panel">
                  <table className="cff-transfers-tbl">
                    <thead>
                      <tr>
                        <th>{t('Voucher')}</th><th>{t('From')}</th><th>{t('To')}</th>
                        <th>{t('Sent')}</th><th>{t('Received')}</th><th>{t('Fee')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {run.transfers.map((tr, i) => (
                        <tr key={i}>
                          <td>{tr.voucher_type} {tr.voucher_no}</td>
                          <td>{tr.from_accounts.join(', ')}</td>
                          <td>{tr.to_accounts.join(', ')}</td>
                          <td>{fmt(tr.amount_sent)}</td>
                          <td>{fmt(tr.amount_received)}</td>
                          <td className={tr.fee > 0 ? 'cff-fee-flag' : ''}>{fmt(tr.fee)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="cff-recon-note">
                    {t('KSA transfers routinely carry a SARIE fee — the destination bank receives less than the source bank sent. That gap is shown here, and also flows through as real spend on whichever line is bound to the Bank Charges account, if any is.')}
                  </div>
                </div>
              )}

              <div className="cff-tbl-scroll">
                <table className="cff-tbl">
                  <thead>
                    <tr>
                      <th className="cff-rowlbl"></th>
                      {run.month_labels.map((m, i) => (
                        <th key={i} colSpan={2}>{m}</th>
                      ))}
                    </tr>
                    <tr className="cff-ba-row">
                      <th className="cff-rowlbl"></th>
                      {run.month_labels.map((_, i) => (
                        <Fragment key={i}>
                          <th className="cff-b">{t('Budget')}</th>
                          <th>{t('Actual')}</th>
                        </Fragment>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sections.map(([section, rows]) => (
                      <Fragment key={section}>
                        <tr className="cff-section-hdr">
                          <td colSpan={run.month_labels.length * 2 + 1}>{section}</td>
                        </tr>
                        {rows.map((r) => (
                          <tr className="cff-item" key={r.line}>
                            <td className="cff-rowlbl">
                              {r.label}
                              {r.binding_count === 0 && (
                                <span className="cff-warn-badge" title={t('No accounts bound — this line will always show 0.')}>
                                  {t('unbound')}
                                </span>
                              )}
                            </td>
                            {run.month_labels.map((_, i) => (
                              <Fragment key={i}>
                                <td className="cff-b">{fmt(r.budget[i])}</td>
                                <td className={r.actual[i] ? 'cff-drillable' : ''}
                                  title={r.actual[i] ? t('Click to see which bank accounts fed this figure') : undefined}
                                  onClick={() => { if (r.actual[i]) { setDrill({ line: r, monthIdx: i }); setDrillTransactions(null); setDrillTxError(null); } }}>
                                  {fmt(r.actual[i])}
                                </td>
                              </Fragment>
                            ))}
                          </tr>
                        ))}
                      </Fragment>
                    ))}
                    <tr className="cff-total">
                      <td className="cff-rowlbl">{t('Bank Beginning Balance')}</td>
                      {run.month_labels.map((_, i) => (
                        <td key={i} colSpan={2}>{fmt(run.rollforward[i]?.opening)}</td>
                      ))}
                    </tr>
                    <tr className="cff-total">
                      <td className="cff-rowlbl">{t('Bank Balance, End of Month')}</td>
                      {run.month_labels.map((_, i) => (
                        <td key={i} colSpan={2}>{fmt(run.rollforward[i]?.closing)}</td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>

              <div className="cff-recon-strip">
                {run.month_labels.map((m, i) => {
                  const residual = run.residuals[i] || 0;
                  const turnover = (run.cash_in_total[i] || 0) + (run.cash_out_total[i] || 0);
                  const pct = turnover ? Math.abs(residual) / turnover * 100 : 0;
                  const flagged = pct > (run.residual_tolerance_pct || 0.5);
                  return (
                    <div key={i} className={`cff-recon ${flagged ? 'warn' : 'ok'}`}>
                      <div className="cff-recon-m">{m}</div>
                      <div className="cff-recon-v">{fmt(residual)}</div>
                    </div>
                  );
                })}
              </div>
              <div className="cff-recon-note">
                {t('Reconciliation residual per month — the classified lines above checked against the actual bank ledger movement, independently. Zero means every binding accounts for itself; nonzero means a line is missing, overlapping, or a transfer was misclassified.')}
              </div>

              {drill && (
                <div className="cff-drill-overlay" onClick={() => setDrill(null)}>
                  <div className="cff-drill-panel" onClick={(e) => e.stopPropagation()}>
                    <div className="cff-drill-hdr">
                      <div>
                        <strong>{drill.line.label}</strong> — {run.month_labels[drill.monthIdx]}
                        <div className="cff-drill-sub">{t('Which bank accounts fed this figure')}</div>
                      </div>
                      <button className="cff-btn-x" onClick={() => setDrill(null)}>×</button>
                    </div>
                    <table className="cff-drill-tbl">
                      <tbody>
                        {Object.entries(drill.line.by_bank[drill.monthIdx] || {}).length === 0 && (
                          <tr><td className="cff-drill-empty">{t('No bank breakdown available for this cell.')}</td></tr>
                        )}
                        {Object.entries(drill.line.by_bank[drill.monthIdx] || {})
                          .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
                          .map(([bank, amt]) => (
                            <tr key={bank}>
                              <td>{bank}</td>
                              <td className="cff-drill-amt">{fmt(amt)}</td>
                            </tr>
                          ))}
                      </tbody>
                      <tfoot>
                        <tr>
                          <td>{t('Total')}</td>
                          <td className="cff-drill-amt">{fmt(drill.line.actual[drill.monthIdx])}</td>
                        </tr>
                      </tfoot>
                    </table>

                    <div className="cff-drill-tx-section">
                      {drillTransactions === null && (
                        <button className="cff-btn-sm" disabled={drillTxLoading} onClick={loadDrillTransactions}>
                          {drillTxLoading ? t('Loading…') : t('Show transactions')}
                        </button>
                      )}
                      {drillTxError && <div className="cff-error">{drillTxError}</div>}
                      {drillTransactions !== null && (
                        <>
                          <div className="cff-drill-sub" style={{ marginTop: 10, marginBottom: 4 }}>
                            {t('Individual transactions')} — {drillTransactions.length}
                          </div>
                          <div className="cff-drill-tx-scroll">
                            <table className="cff-drill-tx-tbl">
                              <thead>
                                <tr>
                                  <th>{t('Date')}</th>
                                  <th>{t('Voucher')}</th>
                                  <th>{t('Account')}</th>
                                  <th>{t('Against')}</th>
                                  <th>{t('Cost Center')}</th>
                                  <th>{t('Remarks')}</th>
                                  <th>{t('Amount')}</th>
                                  <th></th>
                                </tr>
                              </thead>
                              <tbody>
                                {drillTransactions.length === 0 && (
                                  <tr><td colSpan={8} className="cff-drill-empty">{t('No transactions found.')}</td></tr>
                                )}
                                {drillTransactions.map((tx: any, i: number) => (
                                  <tr key={i}>
                                    <td>{tx.posting_date}</td>
                                    <td className="cff-drill-tx-voucher">{tx.voucher_type} {tx.voucher_no}</td>
                                    <td className="cff-drill-tx-account" title={tx.account}>{tx.account || '—'}</td>
                                    <td className="cff-drill-tx-account" title={tx.against_account}>{tx.against_account || '—'}</td>
                                    <td>{tx.cost_center || '—'}</td>
                                    <td className="cff-drill-tx-remarks" title={tx.remarks}>{tx.remarks || <em>{t('(no remarks)')}</em>}</td>
                                    <td className="cff-drill-amt">{fmt(tx.amount)}</td>
                                    <td>
                                      <a className="cff-drill-open" href={deskUrl(tx.voucher_type, tx.voucher_no)}
                                        target="_blank" rel="noopener noreferrer" title={`${tx.voucher_type} ${tx.voucher_no}`}>
                                        {t('Open')} ↗
                                      </a>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {view === 'budget' && (
        <div className="cff-budget">
          <div className="cff-toolbar">
            <input type="number" className="cff-input" value={fiscalYear}
              onChange={(e) => setFiscalYear(parseInt(e.target.value, 10) || fiscalYear)} />
            {companies.length <= 1 ? (
              <span className="cff-company-fixed">{company || (companies[0] && companies[0].name) || t('(no company)')}</span>
            ) : (
              <select className="cff-input" value={company || ''} onChange={(e) => setCompany(e.target.value || null)}>
                <option value="">{t('— select company —')}</option>
                {companies.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            )}
            <button className="cff-btn-primary" disabled={budgetSaving} onClick={saveBudget}>
              {budgetSaving ? t('Saving…') : t('Save Budget')}
            </button>
          </div>

          <div className="cff-budget-note">
            {t('Entered by hand and never derived — shown against the calculated Actual on the Statement. A blank cell and a zero are different: blank contributes nothing, zero contributes a real zero. Clearing a cell back to blank here does not delete a previously-saved value — enter 0 explicitly if that is the intent.')}
          </div>

          {budgetError && <div className="cff-error">{budgetError}</div>}
          {budgetLoading && <div className="cff-note-loading">{t('Loading…')}</div>}

          {!budgetLoading && (
            <div className="cff-tbl-scroll">
              <table className="cff-tbl">
                <thead>
                  <tr>
                    <th className="cff-rowlbl">{t('Line')}</th>
                    {MONTHS.map((m) => <th key={m}>{m}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    const bySection = new Map<string, Line[]>();
                    for (const l of lines) {
                      if (l.is_active === 0) continue;
                      const key = l.section || l.direction;
                      if (!bySection.has(key)) bySection.set(key, []);
                      bySection.get(key)!.push(l);
                    }
                    return Array.from(bySection.entries()).map(([section, rows]) => (
                      <Fragment key={section}>
                        <tr className="cff-section-hdr">
                          <td colSpan={13}>{section}</td>
                        </tr>
                        {rows.map((l) => (
                          <tr className="cff-item" key={l.name}>
                            <td className="cff-rowlbl">{l.label}</td>
                            {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map((m) => (
                              <td key={m} className="cff-budget-cell">
                                <input type="number" className="cff-budget-input"
                                  value={budgetCells[l.name!]?.[m] ?? ''}
                                  placeholder="—"
                                  onChange={(e) => setBudgetCell(l.name!, m, e.target.value)} />
                              </td>
                            ))}
                          </tr>
                        ))}
                      </Fragment>
                    ));
                  })()}
                  {lines.filter((l) => l.is_active !== 0).length === 0 && (
                    <tr><td colSpan={13} className="cff-setup-empty">
                      {t('No active lines yet — add one under Line Setup first.')}
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {view === 'setup' && (
        <div className="cff-setup">
          <div className="cff-setup-cols">
            <div className="cff-setup-list">
              <div className="cff-setup-list-hdr">
                <button className="cff-btn-sm" onClick={() => newLine('Cash Out')}>+ {t('Cash Out line')}</button>
                <button className="cff-btn-sm" onClick={() => newLine('Cash In')}>+ {t('Cash In line')}</button>
                <button className="cff-btn-sm" onClick={() => setShowImport(true)}>{t('Import history')}</button>
              </div>
              {lines.map((l) => (
                <div key={l.name} className={`cff-line-row ${editing?.name === l.name ? 'active' : ''}`}
                  onClick={() => { setSaveError(null); setEditing(l); }}>
                  <span className={`cff-dir-tag ${l.direction === 'Cash Out' ? 'out' : 'in'}`}>
                    {l.direction === 'Cash Out' ? t('OUT') : t('IN')}
                  </span>
                  {l.label}
                  {(!l.bindings || l.bindings.length === 0) && <span className="cff-warn-dot" title={t('No bindings')} />}
                </div>
              ))}
            </div>

            <div className="cff-setup-editor">
              {!editing && <div className="cff-setup-empty">{t('Select a line, or create one.')}</div>}
              {editing && (
                <div className="cff-line-card">
                  <div className="cff-lc-grid">
                    <label className="cff-field">
                      <span>{t('Label')}</span>
                      <input value={editing.label} onChange={(e) => setEditing({ ...editing, label: e.target.value })} />
                    </label>
                    <label className="cff-field">
                      <span>{t('Direction')}</span>
                      <select value={editing.direction}
                        onChange={(e) => setEditing({ ...editing, direction: e.target.value as any })}>
                        <option value="Cash Out">{t('Cash Out')}</option>
                        <option value="Cash In">{t('Cash In')}</option>
                      </select>
                    </label>
                    <label className="cff-field">
                      <span>{t('Section')}</span>
                      <input value={editing.section || ''} onChange={(e) => setEditing({ ...editing, section: e.target.value })} />
                    </label>
                    <label className="cff-field">
                      <span>{t('Dimension field')}</span>
                      <select value={editing.dimension_field || ''}
                        onChange={(e) => setEditing({ ...editing, dimension_field: e.target.value })}>
                        <option value="">{t('— none —')}</option>
                        <option value="Cost Center">{t('Cost Center')}</option>
                        <option value="Project">{t('Project')}</option>
                      </select>
                    </label>
                  </div>

                  <div className="cff-bindings-hdr">
                    <span>{t('Account Bindings')}</span>
                    <button className="cff-btn-sm" onClick={addBinding}>+ {t('Binding')}</button>
                  </div>
                  <table className="cff-bind-tbl">
                    <thead>
                      <tr>
                        <th>{t('Account')}</th>
                        <th>{t('Direction Mode')}</th>
                        <th>{t('Cost Center')}</th>
                        <th>{t('Party Type')}</th>
                        <th>{t('Party')}</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {(editing.bindings || []).map((b, idx) => (
                        <tr key={idx}>
                          <td className="cff-bind-account-cell">
                            <LinkField doctype="Account" company={company} allowGroupSelection
                              value={b.account} placeholder={t('Search or browse the chart of accounts…')}
                              onChange={(v) => updateBinding(idx, { account: v })} />
                          </td>
                          <td>
                            <select value={b.direction_mode} onChange={(e) => updateBinding(idx, { direction_mode: e.target.value as any })}>
                              <option value="Net">{t('Net')}</option>
                              <option value="Debit Only">{t('Debit Only')}</option>
                              <option value="Credit Only">{t('Credit Only')}</option>
                            </select>
                          </td>
                          <td className="cff-bind-cc-cell">
                            <div className="cff-cc-tags">
                              {(b.cost_centers || []).map((cc) => (
                                <span className="cff-cc-tag" key={cc}>
                                  {cc}
                                  <button type="button" onClick={() => removeCostCenter(idx, cc)}>×</button>
                                </span>
                              ))}
                            </div>
                            <LinkField doctype="Cost Center" company={company}
                              value="" placeholder={t('+ add cost centre…')}
                              onChange={(v) => addCostCenter(idx, v)} />
                          </td>
                          <td><input value={b.party_type || ''} onChange={(e) => updateBinding(idx, { party_type: e.target.value })} /></td>
                          <td><input value={b.party || ''} onChange={(e) => updateBinding(idx, { party: e.target.value })} /></td>
                          <td><button className="cff-btn-x" onClick={() => removeBinding(idx)}>×</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {saveError && (
                    <div className="cff-error cff-save-error">
                      {saveError}
                      {editing.name && (
                        <button className="cff-btn-sm" onClick={reloadEditing}>{t('Reload')}</button>
                      )}
                    </div>
                  )}

                  <div className="cff-lc-actions">
                    <button className="cff-btn-primary" disabled={savingLine} onClick={() => saveLine(editing)}>
                      {savingLine ? t('Saving…') : t('Save')}
                    </button>
                    {editing.name && (
                      <button className="cff-btn-danger" disabled={savingLine} onClick={() => deleteLine(editing.name!)}>{t('Delete')}</button>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {showImport && (
        <div className="cff-drill-overlay" onClick={closeImport}>
          <div className="cff-drill-panel cff-import-panel" onClick={(e) => e.stopPropagation()}>
            <div className="cff-drill-hdr">
              <div>
                <strong>{importMode === 'history' ? t('Import classified history') : t('Import Lines + Budget from a statement')}</strong>
                <div className="cff-drill-sub">
                  {importMode === 'history'
                    ? t('Bring in already-classified transactions from a workbook — matched by category label to your existing Lines — instead of re-classifying the same history one row at a time in the Queue.')
                    : t('Bring in a month-by-month Budget statement (like the one you already maintain by hand) — creates any Line that doesn\u2019t exist yet and writes every Budget figure found, in one pass.')}
                </div>
              </div>
              <button className="cff-btn-x" onClick={closeImport}>×</button>
            </div>

            <div className="cff-import-mode-toggle">
              <button className={importMode === 'history' ? 'active' : ''} onClick={() => switchImportMode('history')}>
                {t('Transaction history')}
              </button>
              <button className={importMode === 'statement' ? 'active' : ''} onClick={() => switchImportMode('statement')}>
                {t('Lines + Budget statement')}
              </button>
              <a className="cff-import-sample-link"
                href={importMode === 'history' ? '/api/method/neotec_insight.neotec_insight.api.cash_flow_forecast.download_sample_history_template'
                                                : '/api/method/neotec_insight.neotec_insight.api.cash_flow_forecast.download_sample_statement_template'}
                target="_blank" rel="noopener noreferrer">
                {t('Download sample')} ↓
              </a>
            </div>

            <input type="file" accept=".xlsx" onChange={onImportFilePicked} />
            {importFileName && <div className="cff-import-filename">{importFileName}</div>}

            {importError && <div className="cff-error">{importError}</div>}

            {!importPreview && !importResult && (
              <button className="cff-btn-primary" style={{ marginTop: 10 }}
                disabled={!importFileB64 || importBusy} onClick={doPreviewImport}>
                {importBusy ? t('Reading…') : t('Preview')}
              </button>
            )}

            {importPreview && !importResult && importMode === 'history' && (
              <div className="cff-import-preview">
                <div className="cff-import-stat">
                  <b>{importPreview.total_rows}</b> {t('rows found')} ({t('sheet')}: {importPreview.sheet_used})
                </div>
                <div className="cff-import-stat cff-import-ok">
                  <b>{importPreview.new_count}</b> {t('will be imported')}
                </div>
                {importPreview.already_classified_count > 0 && (
                  <div className="cff-import-stat">
                    {importPreview.already_classified_count} {t('already classified — will be skipped')}
                  </div>
                )}
                {importPreview.unmatched_count > 0 && (
                  <div className="cff-import-stat cff-import-warn">
                    <div><b>{importPreview.unmatched_count}</b> {t('rows have no matching Line yet — create these first, or they will be skipped')}:</div>
                    <ul className="cff-import-unmatched-list">
                      {Object.entries(importPreview.unmatched_labels as Record<string, number>).slice(0, 12).map(([label, count]) => (
                        <li key={label}>{label} — {count}</li>
                      ))}
                      {Object.keys(importPreview.unmatched_labels).length > 12 && (
                        <li>… {Object.keys(importPreview.unmatched_labels).length - 12} {t('more')}</li>
                      )}
                    </ul>
                  </div>
                )}
                {importPreview.warnings?.length > 0 && (
                  <div className="cff-import-stat cff-import-warn">{importPreview.warnings.join(' ')}</div>
                )}
                <button className="cff-btn-primary" disabled={importBusy || importPreview.new_count === 0} onClick={doCommitImport}>
                  {importBusy ? t('Importing…') : `${t('Import')} ${importPreview.new_count} ${t('rows')}`}
                </button>
              </div>
            )}

            {importPreview && !importResult && importMode === 'statement' && (
              <div className="cff-import-preview">
                <div className="cff-import-stat">
                  {t('Sheet')}: {importPreview.sheet_used} · {t('Fiscal year')}: {importPreview.fiscal_year_guess ?? t('unknown — set the Statement year first, then reopen this dialog')}
                </div>
                <div className="cff-import-stat">
                  <b>{importPreview.total_lines_found}</b> {t('line items found')}
                </div>
                <div className="cff-import-stat cff-import-ok">
                  <b>{importPreview.new_line_count}</b> {t('new lines will be created')}
                </div>
                <div className="cff-import-stat">
                  {importPreview.existing_line_count} {t('already exist — their Budget will be updated, nothing duplicated')}
                </div>
                <div className="cff-import-stat cff-import-ok">
                  <b>{importPreview.total_budget_cells}</b> {t('budget figures will be written')}
                </div>
                {importPreview.new_lines?.length > 0 && (
                  <details className="cff-import-errors">
                    <summary>{t('New lines to be created')} ({importPreview.new_lines.length})</summary>
                    <ul>
                      {importPreview.new_lines.slice(0, 20).map((l: any, i: number) => (
                        <li key={i}>{l.direction === 'Cash Out' ? t('OUT') : t('IN')} — {l.section} — {l.label}</li>
                      ))}
                      {importPreview.new_lines.length > 20 && <li>… {importPreview.new_lines.length - 20} {t('more')}</li>}
                    </ul>
                  </details>
                )}
                {importPreview.warnings?.length > 0 && (
                  <div className="cff-import-stat cff-import-warn">{importPreview.warnings.join(' ')}</div>
                )}
                <button className="cff-btn-primary" disabled={importBusy || !importPreview.fiscal_year_guess} onClick={doCommitImport}>
                  {importBusy ? t('Importing…') : t('Import lines and budget')}
                </button>
              </div>
            )}

            {importResult && importMode === 'history' && (
              <div className="cff-import-preview">
                <div className="cff-import-stat cff-import-ok">
                  <b>{importResult.created}</b> {t('imported')}
                </div>
                {importResult.skipped_already_classified > 0 && (
                  <div className="cff-import-stat">
                    {importResult.skipped_already_classified} {t('already classified — skipped')}
                  </div>
                )}
                {importResult.unmatched_count > 0 && (
                  <div className="cff-import-stat cff-import-warn">
                    {importResult.unmatched_count} {t('rows left unmatched — create the missing Lines and re-import to pick them up')}
                  </div>
                )}
                {importResult.errors?.length > 0 && (
                  <details className="cff-import-errors">
                    <summary>{importResult.errors.length} {t('errors')}</summary>
                    <ul>{importResult.errors.map((e: string, i: number) => <li key={i}>{e}</li>)}</ul>
                  </details>
                )}
                <button className="cff-btn-sm" onClick={closeImport}>{t('Done')}</button>
              </div>
            )}

            {importResult && importMode === 'statement' && (
              <div className="cff-import-preview">
                <div className="cff-import-stat cff-import-ok">
                  <b>{importResult.lines_created}</b> {t('lines created')}
                </div>
                <div className="cff-import-stat cff-import-ok">
                  <b>{importResult.budget_cells_written}</b> {t('budget figures written')} ({t('fiscal year')} {importResult.fiscal_year})
                </div>
                {importResult.errors?.length > 0 && (
                  <details className="cff-import-errors">
                    <summary>{importResult.errors.length} {t('errors')}</summary>
                    <ul>{importResult.errors.map((e: string, i: number) => <li key={i}>{e}</li>)}</ul>
                  </details>
                )}
                <button className="cff-btn-sm" onClick={closeImport}>{t('Done')}</button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
