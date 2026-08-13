import { useEffect, useMemo, useState } from 'react';
import { t } from '../../utils/i18n';
import { api } from '../../utils/api';
import { fmtD, FY_RANGE } from '../../utils/format';

/* ─── Statement of Shareholder's Equity (v1.9.49, v1.9.51) ───────────────
 *
 * Renders the canonical Beginning + Movements = Ending table per equity
 * component, driven from Insight Equity Movement entries.
 *
 * v1.9.51 — Components and movement types are now read from configurable
 * DocTypes (Insight Equity Component, Insight Equity Movement Type). The
 * dropdown options come from API calls, NOT from hardcoded arrays. Admins
 * manage the list via the Frappe desk; no code change needed to add new
 * equity components or movement types.
 *
 * Editors (CFO / Accounts Manager / System Manager) can add, edit, and
 * delete movements inline. Read-only users see the same statement without
 * the controls.
 *
 * Design notes:
 *   - We deliberately do NOT auto-derive movements from GL. Categorising
 *     equity postings ("is this a transfer to statutory reserve or a
 *     dividend?") needs judgement that ERP can't infer from account
 *     names alone.
 *   - Opening AND closing are explicit. If a user enters movements that
 *     don't reconcile against a target closing, that's their data — we
 *     show the derived ending honestly.
 *   - Period dropdown supports FY/Q1-Q4/H1-H2; the underlying records are
 *     stamped with the same period, so the same fiscal year can hold a
 *     full set of quarterly statements and an annual one independently.
 */

interface ComponentOption {
  value: string;
  label: string;
  display_order: number;
  is_seeded: number;
  description?: string;
}

interface MovementTypeOption {
  value: string;
  label: string;
  display_order: number;
  is_opening_balance: number;
  default_sign: string;
  is_seeded: number;
  description?: string;
}

const PERIODS = ['FY', 'Q1', 'Q2', 'Q3', 'Q4', 'H1', 'H2'];

interface EditDraft {
  name?: string;
  component: string;
  movement_type: string;
  amount: string;        // string to permit empty during typing
  narration: string;
}

// v1.9.51 — BLANK_DRAFT is now built at the call site from the loaded
// option lists, since the seed could be customised by the admin (the first
// configured component / type may not be 'Paid-up Share Capital' /
// 'Beginning Balance' in their setup).

export function EquityTab({ defaultCompany, canEdit }: { defaultCompany: string; canEdit: boolean }) {
  const [company, setCompany] = useState(defaultCompany);
  const [fy, setFy] = useState<number>(new Date().getFullYear());
  const [period, setPeriod] = useState<string>('FY');
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [companies, setCompanies] = useState<{ name: string; label: string }[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [draft, setDraft] = useState<EditDraft | null>(null);

  // v1.9.51 — configurable lookups loaded once on mount.
  const [componentOptions, setComponentOptions] = useState<ComponentOption[]>([]);
  const [movementTypeOptions, setMovementTypeOptions] = useState<MovementTypeOption[]>([]);
  const [lookupsLoaded, setLookupsLoaded] = useState(false);

  // Load company list once.
  useEffect(() => {
    api.listCompanies?.()?.then((rs: any[]) => setCompanies(rs || [])).catch(() => setCompanies([]));
  }, []);

  // v1.9.51 — Load configurable lookups once on mount. If either fails
  // (e.g. the DocType doesn't exist on an older bench, or the admin deleted
  // every option), we show a clear empty state rather than a half-broken UI.
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.listEquityComponents(), api.listEquityMovementTypes()])
      .then(([comps, types]) => {
        if (cancelled) return;
        setComponentOptions(comps || []);
        setMovementTypeOptions(types || []);
        setLookupsLoaded(true);
      })
      .catch(() => {
        if (cancelled) return;
        setComponentOptions([]);
        setMovementTypeOptions([]);
        setLookupsLoaded(true);
      });
    return () => { cancelled = true; };
  }, []);

  // Default component / movement type for new drafts — first configured option.
  // If the admin has no options configured, we fall back to empty strings
  // and the modal will show a clear "configure these first" message.
  function buildBlankDraft(): EditDraft {
    return {
      component: componentOptions[0]?.value || '',
      movement_type: movementTypeOptions[0]?.value || '',
      amount: '',
      narration: '',
    };
  }

  // Default company when one becomes available.
  useEffect(() => {
    if (!company && defaultCompany) setCompany(defaultCompany);
    if (!company && companies.length > 0) setCompany(companies[0].name);
  }, [defaultCompany, companies, company]);

  // Load the statement whenever the key params change.
  useEffect(() => {
    if (!company || !fy) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    api.getEquityMovement(company, fy, period)
      .then((r) => { if (!cancelled) setData(r); })
      .catch((e: any) => { if (!cancelled) setError(String(e?.message || e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [company, fy, period, refreshKey]);

  const currencyHint = useMemo(() => {
    const c = companies.find((c) => c.name === company);
    return (c as any)?.default_currency || '';
  }, [company, companies]);

  async function saveDraft() {
    if (!draft || !company || !fy) return;
    const amount = parseFloat(draft.amount);
    if (!Number.isFinite(amount)) {
      alert('Amount must be a number.');
      return;
    }
    try {
      await api.saveEquityMovement({
        name: draft.name,
        company,
        fiscal_year: fy,
        period,
        component: draft.component,
        movement_type: draft.movement_type,
        amount,
        narration: draft.narration,
      });
      setDraft(null);
      setRefreshKey((k) => k + 1);
    } catch (e: any) {
      alert('Save failed: ' + (e?.message || 'unknown'));
    }
  }

  async function deleteMovement(name: string) {
    if (!confirm('Delete this movement? This cannot be undone.')) return;
    try {
      await api.deleteEquityMovement(name);
      setRefreshKey((k) => k + 1);
    } catch (e: any) {
      alert('Delete failed: ' + (e?.message || 'unknown'));
    }
  }

  return (
    <div className="equity-tab">
      <div className="equity-head">
        <div>
          <h2 className="equity-title">Statement of Shareholder's Equity</h2>
          <div className="equity-sub">
            Beginning balance + period movements = ending balance, per equity component.
            Movements are entered explicitly — they aren't auto-derived from GL.
          </div>
        </div>
        <div className="equity-filters">
          <label><span className="flbl">Company</span>
            <select value={company} onChange={(e) => setCompany(e.target.value)}>
              <option value="">— pick a company —</option>
              {companies.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
            </select>
          </label>
          <label><span className="flbl">Fiscal year</span>
            <select value={fy} onChange={(e) => setFy(parseInt(e.target.value))}>
              {FY_RANGE.map((y) => <option key={y} value={y}>FY{y}</option>)}
            </select>
          </label>
          <label><span className="flbl">{t('Period')}</span>
            <select value={period} onChange={(e) => setPeriod(e.target.value)}>
              {PERIODS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          {canEdit && (
            <button
              className="primary-btn"
              onClick={() => setDraft(buildBlankDraft())}
              disabled={!lookupsLoaded || componentOptions.length === 0 || movementTypeOptions.length === 0}
              title={
                !lookupsLoaded ? 'Loading…'
                : componentOptions.length === 0 ? 'No equity components configured. Set up via Insight Equity Component in the Frappe desk.'
                : movementTypeOptions.length === 0 ? 'No movement types configured. Set up via Insight Equity Movement Type in the Frappe desk.'
                : 'Add a new equity movement'
              }
            >
              <i className="ti ti-plus" aria-hidden /> Add movement
            </button>
          )}
        </div>
      </div>

      {error && <div className="run-error">{error}</div>}
      {loading && <div className="dash-empty">Loading…</div>}

      {!loading && data && (
        <>
          {data.components.length === 0 ? (
            <div className="equity-empty">
              <div className="equity-empty-title">No equity movements recorded for this period.</div>
              <p>
                {canEdit
                  ? 'Click "Add movement" to start. At minimum, enter a Beginning Balance for each equity component (Share Capital, Reserves, Retained Earnings, etc.), then the period movements (Net Income, Transfers, OCI movements).'
                  : 'Ask an editor (Insight CFO or Accounts Manager) to record the equity movements for this period.'}
              </p>
            </div>
          ) : (
            <table className="equity-table">
              <thead>
                <tr>
                  <th>Equity Component</th>
                  <th className="num">{t('Beginning')}</th>
                  <th>Movements</th>
                  <th className="num">Total Movements</th>
                  <th className="num">{t('Ending')}</th>
                </tr>
              </thead>
              <tbody>
                {data.components.map((c: any) => (
                  <tr key={c.component} className={c.unconfigured ? 'eq-unconfigured' : ''}>
                    <td className="eq-component">
                      {c.component}
                      {c.unconfigured && (
                        <span className="eq-unconfigured-tag" title="This component name does not exist in Insight Equity Component. The data is preserved, but configure the component to control its display order.">
                          unconfigured
                        </span>
                      )}
                    </td>
                    <td className="num">{fmtD(c.beginning)}</td>
                    <td className="eq-movements">
                      {c.movements.length === 0 ? (
                        <span className="muted">(no movements)</span>
                      ) : (
                        <ul>
                          {c.movements.map((m: any, i: number) => (
                            <li key={i} className="eq-mvmt">
                              <span className="eq-mvmt-type">{m.type}</span>
                              <span className={'eq-mvmt-amt ' + (m.amount < 0 ? 'is-neg' : '')}>
                                {fmtD(m.amount)}
                              </span>
                              {m.narration && <span className="eq-mvmt-narr">— {m.narration}</span>}
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                    <td className={'num ' + (c.movements_total < 0 ? 'is-neg' : '')}>{fmtD(c.movements_total)}</td>
                    <td className="num eq-ending">{fmtD(c.ending_derived)}</td>
                  </tr>
                ))}
                <tr className="eq-total-row">
                  <td><strong>Total Shareholder's Equity</strong></td>
                  <td className="num"><strong>{fmtD(data.total_beginning)}</strong></td>
                  <td />
                  <td className="num"><strong>{fmtD(data.total_movements)}</strong></td>
                  <td className="num eq-ending"><strong>{fmtD(data.total_ending)}</strong></td>
                </tr>
              </tbody>
            </table>
          )}

          {data.opening_type_status === 'none' && (
            <div className="equity-warn">
              <strong>Configuration incomplete:</strong> No movement type is marked as the Opening Balance.
              The statement can't identify opening figures until a movement type has the
              <em> Is Opening Balance</em> flag set. Configure via the Frappe desk → Insight Equity Movement Type.
            </div>
          )}
          {data.opening_type_status === 'multiple' && (
            <div className="equity-warn">
              <strong>Configuration ambiguous:</strong> More than one movement type is marked as the Opening Balance.
              The statement will accept any of them as openings, but for clarity you should leave the flag set on
              exactly one type. Configure via the Frappe desk → Insight Equity Movement Type.
            </div>
          )}

          {data.missing_components && data.missing_components.length > 0 && (
            <div className="equity-hint">
              <strong>Not yet recorded:</strong> {data.missing_components.join(', ')}.
              {canEdit && ' Add an opening balance for each to show them on the statement.'}
            </div>
          )}

          {/* Detailed entries with edit/delete affordances */}
          {canEdit && data.components.length > 0 && (
            <div className="equity-entries">
              <h3 className="equity-entries-title">Movement entries</h3>
              <table className="equity-entries-table">
                <thead>
                  <tr>
                    <th>Component</th>
                    <th>Type</th>
                    <th className="num">{t('Amount')}</th>
                    <th>Narration</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  <EquityEntriesRows
                    company={company} fiscalYear={fy} period={period}
                    refreshKey={refreshKey}
                    onEdit={(d) => setDraft(d)}
                    onDelete={deleteMovement}
                  />
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {/* Inline draft editor — modal-lite */}
      {draft && (
        <div className="equity-draft">
          <div className="equity-draft-card">
            <div className="equity-draft-title">
              {draft.name ? 'Edit movement' : 'New movement'}
            </div>
            <div className="form-grid-3">
              <label><span className="flbl">Component</span>
                <select value={draft.component} onChange={(e) => setDraft({ ...draft, component: e.target.value })}>
                  {componentOptions.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </label>
              <label><span className="flbl">Movement type</span>
                <select value={draft.movement_type} onChange={(e) => setDraft({ ...draft, movement_type: e.target.value })}>
                  {movementTypeOptions.map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.label}{m.is_opening_balance ? ' (opening)' : ''}
                    </option>
                  ))}
                </select>
              </label>
              <label><span className="flbl">Amount {currencyHint && <em>({currencyHint})</em>}</span>
                <input type="number" value={draft.amount} onChange={(e) => setDraft({ ...draft, amount: e.target.value })} placeholder="0" />
              </label>
            </div>
            <label className="full-w"><span className="flbl">Narration (optional)</span>
              <input value={draft.narration} onChange={(e) => setDraft({ ...draft, narration: e.target.value })} placeholder="e.g. Transfer per board resolution dated 15-Mar-2024" />
            </label>
            <div className="equity-draft-foot">
              <button onClick={() => setDraft(null)}>Cancel</button>
              <button className="primary-btn" onClick={saveDraft}>
                <i className="ti ti-device-floppy" aria-hidden /> Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* Inline rows for the editable entries table. We fetch separately to keep
 * the main statement and the entries list each a clean concern. */
function EquityEntriesRows({ company, fiscalYear, period, refreshKey, onEdit, onDelete }: {
  company: string; fiscalYear: number; period: string;
  refreshKey: number;
  onEdit: (d: EditDraft) => void;
  onDelete: (name: string) => void;
}) {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => {
    let cancelled = false;
    // Reuse the same endpoint payload — entries are nested under components.
    // We could add a dedicated list endpoint, but reusing keeps the data
    // path single-source-of-truth. We need name + raw fields, which means
    // a separate frappe.client call. For simplicity we use frappe.client.get_list.
    fetch('/api/method/frappe.client.get_list?doctype=Insight Equity Movement'
      + '&filters=' + encodeURIComponent(JSON.stringify({ company, fiscal_year: fiscalYear, period }))
      + '&fields=' + encodeURIComponent(JSON.stringify(['name', 'component', 'movement_type', 'amount', 'narration']))
      + '&order_by=creation asc&limit_page_length=0', {
        headers: { 'X-Frappe-CSRF-Token': (window as any).csrf_token || '' },
      })
      .then((r) => r.json())
      .then((j) => { if (!cancelled) setRows(j?.message || []); })
      .catch(() => { if (!cancelled) setRows([]); });
    return () => { cancelled = true; };
  }, [company, fiscalYear, period, refreshKey]);

  if (rows.length === 0) {
    return <tr><td colSpan={5} className="muted">No entries.</td></tr>;
  }
  return (
    <>
      {rows.map((r) => (
        <tr key={r.name}>
          <td>{r.component}</td>
          <td>{r.movement_type}</td>
          <td className={'num ' + (r.amount < 0 ? 'is-neg' : '')}>{fmtD(r.amount)}</td>
          <td className="muted">{r.narration || '—'}</td>
          <td className="eq-actions">
            <button title="Edit" onClick={() => onEdit({
              name: r.name, component: r.component, movement_type: r.movement_type,
              amount: String(r.amount), narration: r.narration || '',
            })}><i className="ti ti-edit" aria-hidden /></button>
            <button title="Delete" onClick={() => onDelete(r.name)}><i className="ti ti-trash" aria-hidden /></button>
          </td>
        </tr>
      ))}
    </>
  );
}
