import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

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
  cost_center?: string;
  project?: string;
  party_type?: string;
  party?: string;
};

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
};

function fmt(n: number | undefined): string {
  const v = n || 0;
  if (v === 0) return '—';
  const s = Math.abs(Math.round(v)).toLocaleString('en-US');
  return v < 0 ? `(${s})` : s;
}

export function CashFlowForecastTab() {
  const [view, setView] = useState<'statement' | 'setup'>('statement');
  const [fiscalYear, setFiscalYear] = useState<number>(new Date().getFullYear());
  const [company, setCompany] = useState<string | null>(null);
  const [run, setRun] = useState<RunResult | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Line | null>(null);

  const loadLines = useCallback(async () => {
    const rows = await api.cashFlowForecastLines(false);
    setLines(rows || []);
  }, []);

  const loadRun = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.cashFlowForecastRun(fiscalYear, company);
      setRun(r);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [fiscalYear, company]);

  useEffect(() => { loadLines(); }, [loadLines]);
  useEffect(() => { if (view === 'statement') loadRun(); }, [view, loadRun]);

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
    const saved = await api.cashFlowForecastSaveLine(line);
    await loadLines();
    setEditing(saved);
  }

  async function deleteLine(name: string) {
    if (!confirm(t('Delete this line? Past budget entries against it will block this — deactivate instead if unsure.'))) return;
    await api.cashFlowForecastDeleteLine(name);
    await loadLines();
    setEditing(null);
  }

  function newLine(direction: 'Cash Out' | 'Cash In') {
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
        <button className={view === 'setup' ? 'active' : ''} onClick={() => setView('setup')}>{t('Line Setup')}</button>
      </div>

      {view === 'statement' && (
        <div className="cff-statement">
          <div className="cff-toolbar">
            <input type="number" className="cff-input" value={fiscalYear}
              onChange={(e) => setFiscalYear(parseInt(e.target.value, 10) || fiscalYear)} />
            <input type="text" className="cff-input" placeholder={t('Company (optional)')}
              value={company || ''} onChange={(e) => setCompany(e.target.value || null)} />
            <button className="cff-btn-primary" onClick={loadRun} disabled={loading}>
              {loading ? t('Running…') : t('Run')}
            </button>
          </div>

          {error && <div className="cff-error">{error}</div>}

          {run && (
            <>
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
                                <td>{fmt(r.actual[i])}</td>
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
            </>
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
              </div>
              {lines.map((l) => (
                <div key={l.name} className={`cff-line-row ${editing?.name === l.name ? 'active' : ''}`}
                  onClick={() => setEditing(l)}>
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
                    {editing.direction === 'Cash In' && (
                      <label className="cff-field">
                        <span>{t('Dimension field')}</span>
                        <select value={editing.dimension_field || ''}
                          onChange={(e) => setEditing({ ...editing, dimension_field: e.target.value })}>
                          <option value="">{t('— none —')}</option>
                          <option value="Cost Center">{t('Cost Center')}</option>
                          <option value="Project">{t('Project')}</option>
                        </select>
                      </label>
                    )}
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
                          <td><input value={b.account} onChange={(e) => updateBinding(idx, { account: e.target.value })} /></td>
                          <td>
                            <select value={b.direction_mode} onChange={(e) => updateBinding(idx, { direction_mode: e.target.value as any })}>
                              <option value="Net">{t('Net')}</option>
                              <option value="Debit Only">{t('Debit Only')}</option>
                              <option value="Credit Only">{t('Credit Only')}</option>
                            </select>
                          </td>
                          <td><input value={b.cost_center || ''} onChange={(e) => updateBinding(idx, { cost_center: e.target.value })} /></td>
                          <td><input value={b.party_type || ''} onChange={(e) => updateBinding(idx, { party_type: e.target.value })} /></td>
                          <td><input value={b.party || ''} onChange={(e) => updateBinding(idx, { party: e.target.value })} /></td>
                          <td><button className="cff-btn-x" onClick={() => removeBinding(idx)}>×</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  <div className="cff-lc-actions">
                    <button className="cff-btn-primary" onClick={() => saveLine(editing)}>{t('Save')}</button>
                    {editing.name && (
                      <button className="cff-btn-danger" onClick={() => deleteLine(editing.name!)}>{t('Delete')}</button>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
