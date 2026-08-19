import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { fmtD } from '../../utils/format';
import { setActiveCompany } from '../../utils/activeCompany';
import { ExportBar } from '../ExportBar';
import type { DocRow, ReportDoc } from '../../utils/reportdoc';
import { CaptureModal, EvidenceModal, UnassignedModal } from './CaptureModals';

/* Cost pool allocation (v2.58.0).
 *
 * Replaces a spreadsheet that spread GMO cost by head count and Sales &
 * Marketing cost by leads. The model, kept identical to that workbook so the
 * numbers tie on day one:
 *
 *   distributable = pool - sum(fixed)
 *   alloc(cc)     = fixed(cc) + distributable * driver(cc) / total_driver
 *
 * Two views. **Report** spreads cost centres horizontally — every cost centre
 * side by side, months down — which is the orientation asked for and the one
 * that makes the split legible. **Data** is the driver entry grid, the interim
 * measure until head count comes from HR and leads from CRM automatically.
 */

type View = 'report' | 'data';

interface Rule {
  name: string; title: string; company: string; driver_label: string;
  pool_mode: string; pool_flag?: string; pool_report?: string; driver_source?: string;
  pool_row_key?: string; pool_cost_center?: string;
}

interface MonthBlock {
  pool: number; direct_total: number; distributable: number; driver_total: number;
  allocation: Record<string, number>; drivers: Record<string, number>;
  direct: Record<string, number>;
  allocated: number; residual: number; unallocated: boolean; no_pool?: boolean;
}

interface RunResult {
  rule: string; title: string; driver_label: string; company: string; year: number;
  months: number[]; month_labels: string[];
  cost_centers: string[]; cost_center_labels: Record<string, string>;
  by_month: Record<string, MonthBlock>;
  ytd: Record<string, number>; ytd_pool: number; ytd_residual: number;
  /** v2.78.0 — entered, never derived. `budget` is per month per cost centre;
   *  the YTD and variance roll-ups are computed server-side so the screen and
   *  every export agree on the rounding and the sign. Variance is positive
   *  when the allocation exceeds budget — an allocation is a cost. */
  budget?: Record<string, Record<string, number>>;
  budget_ytd?: Record<string, number>;
  budget_total?: number;
  variance_ytd?: Record<string, number>;
  formula: string; formula_errors: number[]; credit_back: number; pool_source: string;
  roles: Record<string, Basis | 'mixed' | 'credit'>;
  mixed: string[];
}

type Basis = 'head_count' | 'amount';
type Cells = Record<string, Record<string, { driver: number; amount: number; budget?: number }>>;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function AllocationApp() {
  const [view, setView] = useState<View>('report');
  const [companies, setCompanies] = useState<{ name: string; label?: string }[]>([]);
  const [company, setCompany] = useState('');
  const [rules, setRules] = useState<Rule[]>([]);
  const [rule, setRule] = useState('');
  const [year, setYear] = useState(new Date().getFullYear());
  const [decimals, setDecimals] = useState(0);

  const [result, setResult] = useState<RunResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const [cells, setCells] = useState<Cells>({});
  const [manualPool, setManualPool] = useState<Record<string, number>>({});
  const [basis, setBasis] = useState<Record<string, Basis>>({});
  const [ccOptions, setCcOptions] = useState<{ name: string; label: string }[]>([]);
  const [gridCcs, setGridCcs] = useState<string[]>([]);
  // Rows taken off the grid. Held until Save so a mis-click can be undone by
  // pressing Reload, and so the backend knows what to delete — an emptied row
  // cannot say "delete me", because a zero head count is a real value.
  const [removed, setRemoved] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');
  const [captureOpen, setCaptureOpen] = useState(false);
  const [evidence, setEvidence] = useState<{ cc: string; month: number } | null>(null);
  const [unassignedOpen, setUnassignedOpen] = useState(false);
  // Checked on load, not only when someone opens Capture — the whole point is
  // that it is visible before the report is trusted.
  const [unassignedCount, setUnassignedCount] = useState(0);

  const activeRule = rules.find((r) => r.name === rule) || null;

  useEffect(() => { setActiveCompany(company); }, [company]);

  useEffect(() => {
    api.listCompanies()
      .then((cs: any[]) => { setCompanies(cs || []); if (cs?.length && !company) setCompany(cs[0].name); })
      .catch(() => { /* company stays blank; the rule carries its own */ });
  }, []);

  useEffect(() => {
    if (!company) return;
    api.allocationRules(company)
      .then((rs) => { setRules(rs || []); if (rs?.length && !rs.some((r) => r.name === rule)) setRule(rs[0].name); })
      .catch((e: any) => setErr(e?.message || t('Could not load allocation rules.')));
    api.allocationCostCenters(company).then(setCcOptions).catch(() => setCcOptions([]));
  }, [company]);

  const run = useCallback(() => {
    if (!rule) return;
    setLoading(true); setErr('');
    api.allocationRun(rule, year, company)
      .then((r) => setResult(r as RunResult))
      .catch((e: any) => { setErr(e?.message || t('Run failed.')); setResult(null); })
      .finally(() => setLoading(false));
  }, [rule, year, company]);

  const loadGrid = useCallback(() => {
    if (!rule) return;
    setErr('');
    api.allocationGrid(rule, year, company)
      .then((g: any) => {
        setCells(g.cells || {});
        setBasis(g.basis || {});
        setManualPool(g.manual_pool || {});
        setGridCcs(g.cost_centers || []);
        setRemoved([]);
        setDirty(false); setSaveMsg('');
      })
      .catch((e: any) => setErr(e?.message || t('Could not load the entry grid.')));
  }, [rule, year, company]);

  useEffect(() => { if (rule) { run(); loadGrid(); } }, [rule, year]);

  useEffect(() => {
    if (!rule || activeRule?.driver_source !== 'employee_headcount') { setUnassignedCount(0); return; }
    let alive = true;
    api.allocationUnassigned(rule, year)
      .then((r: any) => { if (alive) setUnassignedCount(r?.total_people || 0); })
      .catch(() => { if (alive) setUnassignedCount(0); });
    return () => { alive = false; };
  }, [rule, year, activeRule?.driver_source]);

  function setCell(cc: string, m: number, raw: string) {
    const v = raw === '' ? 0 : Number(raw);
    if (!isFinite(v)) return;
    const field = (basis[cc] || 'head_count') === 'amount' ? 'amount' : 'driver';
    setCells((cur) => {
      const prev = cur[cc]?.[m];
      const cell = { driver: prev?.driver ?? 0, amount: prev?.amount ?? 0,
                     budget: prev?.budget ?? 0, [field]: v };
      return { ...cur, [cc]: { ...(cur[cc] || {}), [m]: cell } };
    });
    setDirty(true);
  }

  /** v2.78.0 — budget is entered, never derived, and is independent of the
   *  basis: a head-count cost centre carries one just as an amount one does.
   *  Kept as its own setter rather than a `field` argument to setCell, because
   *  driver and amount are mutually exclusive and budget is not — folding them
   *  together is how a budget would end up overwriting a driver. */
  function setBudget(cc: string, m: number, raw: string) {
    const v = raw === '' ? 0 : Number(raw);
    if (!isFinite(v)) return;
    setCells((cur) => {
      const prev = cur[cc]?.[m];
      const cell = { driver: prev?.driver ?? 0, amount: prev?.amount ?? 0, budget: v };
      return { ...cur, [cc]: { ...(cur[cc] || {}), [m]: cell } };
    });
    setDirty(true);
  }

  function setRowBasis(cc: string, b: Basis) {
    // Switching basis clears the row. The two columns are not
    // interchangeable — a head count of 8 is not an amount of 8 — and
    // carrying the old figures across is how a number ends up in the wrong
    // column without anyone noticing.
    setBasis((cur) => ({ ...cur, [cc]: b }));
    setCells((cur) => ({ ...cur, [cc]: {} }));
    setDirty(true);
  }

  function addCostCenter(cc: string, to: Basis) {
    if (!cc || gridCcs.includes(cc)) return;
    setGridCcs((c) => [...c, cc].sort());
    setBasis((b) => ({ ...b, [cc]: to }));
    setRemoved((r) => r.filter((x) => x !== cc));
    setDirty(true);
  }

  function removeCostCenter(cc: string) {
    const hasData = Object.values(cells[cc] || {})
      .some((v) => Number(v?.driver || 0) || Number(v?.amount || 0) || Number(v?.budget || 0));
    if (hasData && !confirm(t('Remove this cost centre and its entered values from the rule?'))) return;
    setGridCcs((c) => c.filter((x) => x !== cc));
    setCells((cur) => { const n = { ...cur }; delete n[cc]; return n; });
    setRemoved((r) => (r.includes(cc) ? r : [...r, cc]));
    setDirty(true);
  }

  async function save() {
    setSaving(true); setSaveMsg(''); setErr('');
    try {
      // Send every cost centre on the grid, including ones emptied out — the
      // backend deletes blank rows, which is how a cost centre is removed
      // from a rule without a separate delete action.
      const payload: Cells = {};
      for (const cc of gridCcs) payload[cc] = cells[cc] || {};
      const res: any = await api.allocationSaveGrid(
        rule, year, company, payload, manualPool, basis, removed);
      setDirty(false);
      setRemoved([]);
      setSaveMsg(t('Saved') + ` · ${res?.written ?? 0} ` + t('rows')
        + (res?.deleted ? ` · ${res.deleted} ` + t('cleared') : ''));
      run();
    } catch (e: any) {
      setErr(e?.message || t('Save failed.'));
    } finally {
      setSaving(false);
    }
  }

  /* ── the report document, shared by screen and all five exports ────────── */
  const buildDoc = useCallback((): ReportDoc | null => {
    if (!result) return null;
    const ccs = result.cost_centers;
    const lbl = (cc: string) => result.cost_center_labels[cc] || cc;
    const money = (v: number) => ({ v: Number(v || 0), text: fmtD(Number(v || 0), decimals), num: true as const });
    const rows: DocRow[] = [];

    rows.push({ kind: 'sec', cells: [{ v: t('Allocated to cost centre'), colSpan: ccs.length + 3, bold: true }] });
    for (const m of result.months) {
      const b = result.by_month[String(m)];
      if (!b) continue;
      rows.push({
        cells: [
          { v: MONTHS[m - 1] },
          money(b.pool),
          ...ccs.map((cc) => money(b.allocation[cc] || 0)),
          money(b.allocated),
        ],
      });
    }
    rows.push({
      kind: 'grand',
      cells: [
        { v: t('YTD'), bold: true },
        { ...money(result.ytd_pool), bold: true },
        ...ccs.map((cc) => ({ ...money(result.ytd[cc] || 0), bold: true })),
        { ...money(ccs.reduce((s, cc) => s + (result.ytd[cc] || 0), 0)), bold: true },
      ],
    });

    rows.push({ kind: 'gap', cells: [] });
    rows.push({ kind: 'sec', cells: [{ v: result.driver_label, colSpan: ccs.length + 3, bold: true }] });
    for (const m of result.months) {
      const b = result.by_month[String(m)];
      if (!b) continue;
      rows.push({
        cells: [
          { v: MONTHS[m - 1] },
          { v: '', num: true },
          ...ccs.map((cc) => ({ v: Number(b.drivers[cc] || 0), text: String(b.drivers[cc] ?? ''), num: true as const })),
          { v: Number(b.driver_total || 0), text: String(b.driver_total || 0), num: true as const },
        ],
      });
    }

    const drift = Math.abs(result.ytd_residual) > 0.01
      ? t('Unallocated remainder') + ': ' + fmtD(result.ytd_residual, 2)
        + ' — ' + t('some months carry no driver value.')
      : undefined;

    return {
      title: result.title,
      subtitle: `${result.driver_label} · ${result.year}`,
      company: result.company,
      companyLabel: result.company,
      period: String(result.year),
      columns: [
        { label: t('Month'), width: 12 },
        { label: t('Pool'), num: true, width: 16 },
        ...ccs.map((cc) => ({ label: lbl(cc), num: true, width: 16 })),
        { label: t('Total'), num: true, width: 16 },
      ],
      rows,
      fileBase: 'allocation_' + (result.title || 'report').toLowerCase().replace(/[^a-z0-9]+/g, '_'),
      orientation: 'landscape',
      note: drift,
    };
  }, [result, decimals]);

  const gridTotals = useMemo(() => {
    const out: Record<number, number> = {};
    for (let m = 1; m <= 12; m++) {
      out[m] = gridCcs
        .filter((cc) => (basis[cc] || 'head_count') === 'head_count')
        .reduce((s, cc) => s + Number(cells[cc]?.[m]?.driver || 0), 0);
    }
    return out;
  }, [cells, gridCcs, basis]);

  return (
    <div className="alloc-page">
      <nav className="view-subtabs" role="tablist">
        <button role="tab" aria-selected={view === 'report'}
          className={'view-subtab' + (view === 'report' ? ' is-active' : '')}
          onClick={() => setView('report')}>{t('Report')}</button>
        <button role="tab" aria-selected={view === 'data'}
          className={'view-subtab' + (view === 'data' ? ' is-active' : '')}
          onClick={() => setView('data')}>{t('Data entry')}</button>
      </nav>

      <div className="filters-card">
        <div className="filter-grid">
          <label>{t('Company')}
            <select value={company} onChange={(e) => setCompany(e.target.value)}>
              {companies.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
            </select>
          </label>
          <label>{t('Allocation')}
            <select value={rule} onChange={(e) => setRule(e.target.value)}>
              {!rules.length && <option value="">{t('No rules defined')}</option>}
              {rules.map((r) => <option key={r.name} value={r.name}>{r.title}</option>)}
            </select>
          </label>
          <label>{t('Year')}
            <input type="number" value={year} min={2000} max={2100}
              onChange={(e) => setYear(parseInt(e.target.value) || year)} />
          </label>
          <label>{t('Decimals')}
            <select value={decimals} onChange={(e) => setDecimals(parseInt(e.target.value))}>
              <option value={0}>0</option><option value={2}>2</option>
            </select>
          </label>
        </div>

        <div className="action-row">
          <button onClick={run} disabled={!rule || loading}>{loading ? t('Running…') : t('Run')}</button>
          {view === 'report' && (
            <ExportBar company={company} companyLabel={company} disabled={!result} getDoc={buildDoc} />
          )}
          {view === 'data' && (
            <>
              <button onClick={save} disabled={!dirty || saving}>
                {saving ? t('Saving…') : t('Save')}
              </button>
              <button onClick={loadGrid} disabled={saving}>{t('Reload')}</button>
              {activeRule?.driver_source === 'employee_headcount' && (
                <button onClick={() => setCaptureOpen(true)} disabled={saving}>
                  {t('Capture from HR')}
                </button>
              )}
              {saveMsg && <span className="alloc-ok">{saveMsg}</span>}
              {dirty && !saving && (
            <span className="alloc-dirty">
              {t('Unsaved changes')}
              {!!removed.length && ` · ${removed.length} ` + t('row(s) will be removed')}
            </span>
          )}
            </>
          )}
          {err && <span className="ni-exp-err">{err}</span>}
        </div>

        {activeRule && (
          <p className="alloc-hint">
            {activeRule.pool_mode === 'report_row'
              ? (activeRule.pool_row_key
                  ? t('Pool read from') + ` ${activeRule.pool_report} · ${activeRule.pool_row_key}`
                    + (activeRule.pool_cost_center ? ` · ${activeRule.pool_cost_center}` : '')
                  : t('Pool source not configured yet — set the report, row and cost centre on the rule.'))
              : activeRule.pool_mode === 'flag'
                ? (activeRule.pool_flag
                    ? t('Pool read from the GL via flag') + ` “${activeRule.pool_flag}”`
                    : t('Pool source not configured yet — no flag set on the rule.'))
                : t('Pool entered by hand, per month')}
            {' · '}{t('Amounts entered directly are taken out of the pool first; what is left is spread on')} {activeRule.driver_label}.
          </p>
        )}
      </div>

      {unassignedCount > 0 && (
        <div className="alloc-warn alloc-preflight">
          <span>
            <strong>{unassignedCount} {t('employees have no cost centre')}</strong>{' — '}
            {t('they fall out of the denominator, so every cost centre below is taking a larger share than it should. The totals will still tie, so nothing else will flag it.')}
          </span>
          <button type="button" className="bk-btn" onClick={() => setUnassignedOpen(true)}>
            {t('Review list')}
          </button>
        </div>
      )}

      {!rules.length && (
        <div className="alloc-empty">
          <h3>{t('No allocation rules yet')}</h3>
          <p>{t('Create an Insight Allocation Rule in the desk — one per pool, e.g. GMO Allocation (driver: head count) and Sales & Marketing Allocation (driver: leads count). Point it at the account flag that holds the pool cost, then enter the driver values under Data entry.')}</p>
          {/* Rules are deliberately never seeded: a pool and its driver are
              specific to one company's cost structure, and a guessed default
              would put invented numbers into management accounts. But "not set
              up yet" should not look like "failed to load", so link the way in. */}
          <p><a className="vs-btn" href="/app/insight-allocation-rule/new" target="_blank" rel="noopener noreferrer">
            {t('Create an allocation rule')}</a></p>
        </div>
      )}

      {view === 'report' && result && (
        <AllocReport result={result} decimals={decimals} onEvidence={setEvidence} />
      )}

      {unassignedOpen && rule && (
        <UnassignedModal rule={rule} year={year} onClose={() => setUnassignedOpen(false)} />
      )}
      {captureOpen && rule && (
        <CaptureModal rule={rule} year={year}
          ccLabel={(cc) => ccOptions.find((o) => o.name === cc)?.label || cc}
          onClose={() => setCaptureOpen(false)}
          onSaved={() => { loadGrid(); run(); }} />
      )}
      {evidence && rule && (
        <EvidenceModal rule={rule} costCenter={evidence.cc} year={year} month={evidence.month}
          ccLabel={result?.cost_center_labels?.[evidence.cc] || evidence.cc}
          onClose={() => setEvidence(null)} />
      )}

      {view === 'data' && rule && (
        <div className="alloc-grid-wrap">
          {/* Two tables, kept apart on purpose. A cost centre belongs to one
              or the other, and there is no column in either that a calculated
              allocation can be typed into. */}
          {(['head_count', 'amount'] as Basis[]).map((tbl) => {
            const rowsFor = gridCcs.filter((cc) => (basis[cc] || 'head_count') === tbl);
            return (
              <div className="alloc-table-block" key={tbl}>
                <div className="alloc-table-head">
                  <h4>{tbl === 'head_count'
                    ? (activeRule?.driver_label || t('Head count'))
                    : t('Amount')}</h4>
                  <span>{tbl === 'head_count'
                    ? t('the amount is calculated from each row\'s share of this table\'s total')
                    : t('no head count — the amount is entered directly and comes out of the pool first')}</span>
                </div>
                <table className="alloc-grid">
                  <thead>
                    <tr>
                      <th className="sticky-l">{t('Cost centre')}</th>
                      {MONTHS.map((mm) => <th key={mm} className="num">{t(mm)}</th>)}
                      <th className="num">{t('YTD')}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {!rowsFor.length && (
                      <tr><td className="sticky-l alloc-none" colSpan={15}>
                        {t('No cost centres in this table yet.')}
                      </td></tr>
                    )}
                    {rowsFor.map((cc) => {
                      const isAmt = tbl === 'amount';
                      const tot = MONTHS.reduce((sum, _, i) =>
                        sum + Number((isAmt ? cells[cc]?.[i + 1]?.amount : cells[cc]?.[i + 1]?.driver) || 0), 0);
                      return (
                        <tr key={cc}>
                          <td className="sticky-l">{ccOptions.find((o) => o.name === cc)?.label || cc}</td>
                          {MONTHS.map((_, i) => (
                            <td key={i} className="num">
                              <input type="number" step="any" className="alloc-in"
                                value={(isAmt ? cells[cc]?.[i + 1]?.amount : cells[cc]?.[i + 1]?.driver) ?? ''}
                                onChange={(e) => setCell(cc, i + 1, e.target.value)} />
                            </td>
                          ))}
                          <td className="num alloc-rt">{isAmt ? fmtD(tot, 0) : tot}</td>
                          <td className="alloc-move">
                            <button type="button" title={t('Move to the other table')}
                              aria-label={t('Move to the other table')}
                              onClick={() => setRowBasis(cc, isAmt ? 'head_count' : 'amount')}>⇄</button>
                            <button type="button" className="alloc-del"
                              title={t('Remove this cost centre from the rule')}
                              aria-label={t('Remove this cost centre from the rule')}
                              onClick={() => removeCostCenter(cc)}>×</button>
                          </td>
                        </tr>
                      );
                    })}
                    {tbl === 'head_count' && !!rowsFor.length && (
                      <tr className="alloc-tot">
                        <td className="sticky-l">{t('Total')}</td>
                        {MONTHS.map((_, i) => <td key={i} className="num">{gridTotals[i + 1] || 0}</td>)}
                        <td className="num">{Object.values(gridTotals).reduce((a, b2) => a + b2, 0)}</td>
                        <td />
                      </tr>
                    )}
                  </tbody>
                </table>
                {tbl === 'head_count' && !!rowsFor.length && (
                  <p className="alloc-hint">
                    {t('This total is the denominator — the sum of exactly these rows, never a company-wide figure.')}
                  </p>
                )}
              </div>
            );
          })}

          {activeRule?.pool_mode === 'manual' && (
            <div className="alloc-table-block">
              <div className="alloc-table-head"><h4>{t('Pool total')}</h4><span>{t('entered by hand')}</span></div>
              <table className="alloc-grid">
                <tbody><tr>
                  <td className="sticky-l">{t('Pool')}</td>
                  {MONTHS.map((_, i) => (
                    <td key={i} className="num">
                      <input type="number" step="any" className="alloc-in"
                        value={manualPool[String(i + 1)] ?? ''}
                        onChange={(e) => {
                          const v = e.target.value === '' ? 0 : Number(e.target.value);
                          setManualPool((p) => ({ ...p, [String(i + 1)]: v }));
                          setDirty(true);
                        }} />
                    </td>
                  ))}
                  <td className="num alloc-rt">
                    {fmtD(Object.values(manualPool).reduce((a, b2) => a + Number(b2 || 0), 0), 0)}
                  </td>
                </tr></tbody>
              </table>
            </div>
          )}

          <div className="alloc-add">
            <label>{t('Add to head count table')}
              <select value="" onChange={(e) => addCostCenter(e.target.value, 'head_count')}>
                <option value="">{t('Choose…')}</option>
                {ccOptions.filter((o) => !gridCcs.includes(o.name))
                  .map((o) => <option key={o.name} value={o.name}>{o.label}</option>)}
              </select>
            </label>
            <label>{t('Add to amount table')}
              <select value="" onChange={(e) => addCostCenter(e.target.value, 'amount')}>
                <option value="">{t('Choose…')}</option>
                {ccOptions.filter((o) => !gridCcs.includes(o.name))
                  .map((o) => <option key={o.name} value={o.name}>{o.label}</option>)}
              </select>
            </label>
            <p className="alloc-hint">
              {t('Use × to take a cost centre off a table, or ⇄ to move it to the other one. Nothing is deleted until you press Save — Reload undoes it. A blank cell and a zero are different: blank contributes nothing, zero contributes a zero share.')}
            </p>
          </div>
          {/* Budget — one table for every cost centre in the rule, regardless
              of basis. Separate from the two input tables above because it is
              a different KIND of number: those drive a calculation, this one is
              only ever compared against its result. Keeping it here rather than
              as extra columns is what stops a budget being typed into a driver
              cell by accident. */}
          <div className="alloc-table-block">
            <div className="alloc-table-head">
              <h4>{t('Budget')}</h4>
              <span>{t('entered by hand and never derived — shown against the calculated allocation on the report')}</span>
            </div>
            <table className="alloc-grid">
              <thead>
                <tr>
                  <th className="sticky-l">{t('Cost centre')}</th>
                  {MONTHS.map((mm) => <th key={mm} className="num">{t(mm)}</th>)}
                  <th className="num">{t('YTD')}</th>
                </tr>
              </thead>
              <tbody>
                {!gridCcs.length && (
                  <tr><td className="sticky-l alloc-none" colSpan={14}>
                    {t('Add a cost centre above first.')}
                  </td></tr>
                )}
                {gridCcs.map((cc) => {
                  const tot = MONTHS.reduce((sum, _, i) =>
                    sum + Number(cells[cc]?.[i + 1]?.budget || 0), 0);
                  return (
                    <tr key={cc}>
                      <td className="sticky-l">{ccOptions.find((o) => o.name === cc)?.label || cc}</td>
                      {MONTHS.map((_, i) => (
                        <td key={i} className="num">
                          <input type="number" step="any" className="alloc-in"
                            value={cells[cc]?.[i + 1]?.budget ?? ''}
                            onChange={(e) => setBudget(cc, i + 1, e.target.value)} />
                        </td>
                      ))}
                      <td className="num alloc-rt">{fmtD(tot, 0)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

        </div>
      )}
    </div>
  );
}

/* ── report table: cost centres across, months down ───────────────────────── */

function AllocReport({ result, decimals, onEvidence }: {
  result: RunResult; decimals: number;
  // Passed in rather than reached for: this component is rendered separately
  // from the state that opens the evidence dialog.
  onEvidence: (sel: { cc: string; month: number }) => void;
}) {
  const ccs = result.cost_centers;
  const lbl = (cc: string) => result.cost_center_labels[cc] || cc;
  const drift = Math.abs(result.ytd_residual) > 0.01;

  if (!ccs.length) {
    return (
      <div className="alloc-empty">
        <h3>{t('Nothing to allocate yet')}</h3>
        <p>{t('No driver values have been entered for this rule and year. Switch to Data entry and add the head count or leads count per cost centre.')}</p>
      </div>
    );
  }

  return (
    <>
      {!!result.mixed?.length && (
        <div className="alloc-warn">
          {t('These cost centres change basis part-way through the year, which is almost always a mistake')}:{' '}
          <strong>{result.mixed.map(lbl).join(', ')}</strong>.{' '}
          {t('Set one basis for the whole year under Data entry.')}
        </div>
      )}
      <div className="alloc-meta">
        <span><i className="ti ti-database" aria-hidden /> {result.pool_source}</span>
        <span><i className="ti ti-math-function" aria-hidden /> <code>{result.formula}</code></span>
        {!!result.credit_back && (
          <span className="alloc-meta-ok">
            <i className="ti ti-check" aria-hidden /> {t('source cost centre credited — company total unchanged')}
          </span>
        )}
      </div>
      {!!result.formula_errors?.length && (
        <div className="alloc-warn">
          {t('The formula could not be evaluated in')}{' '}
          <strong>{result.formula_errors.map((m) => MONTHS[m - 1]).join(', ')}</strong>.{' '}
          {t('Those months show zero. Check the expression on the rule.')}
        </div>
      )}
      {drift && (
        <div className="alloc-warn">
          {t('Unallocated remainder')}: <strong>{fmtD(result.ytd_residual, 2)}</strong>{' — '}
          {t('one or more months have a pool but no driver value, so that share could not be spread. It is excluded from the cost centre columns.')}
        </div>
      )}
      <div className="alloc-report-wrap">
        <table className="alloc-report">
          <thead>
            <tr>
              <th className="sticky-l">{t('Month')}</th>
              <th className="num">{t('Pool')}</th>
              {ccs.map((cc) => (
                <th key={cc} className="num">
                  {lbl(cc)}
                  {/* A fixed-only centre takes its amount and no share of the
                      remainder. Saying so on the column head means the number
                      underneath never has to be explained twice. */}
                  {result.roles?.[cc] === 'amount' && <span className="alloc-tag">{t('amount')}</span>}
                  {result.roles?.[cc] === 'credit' && <span className="alloc-tag alloc-tag-credit">{t('credit')}</span>}
                  {result.roles?.[cc] === 'mixed' && <span className="alloc-tag alloc-tag-warn">{t('basis changes')}</span>}
                </th>
              ))}
              <th className="num">{t('Allocated')}</th>
            </tr>
          </thead>
          <tbody>
            {result.months.map((m) => {
              const b = result.by_month[String(m)];
              if (!b) return null;
              return (
                <tr key={m} className={b.unallocated ? 'alloc-row-warn' : b.no_pool ? 'alloc-row-nopool' : ''}>
                  <td className="sticky-l">{t(MONTHS[m - 1])}</td>
                  <td className="num">{fmtD(b.pool, decimals)}</td>
                  {ccs.map((cc) => <td key={cc} className="num">{fmtD(b.allocation[cc] || 0, decimals)}</td>)}
                  <td className="num">{b.no_pool ? <span className="alloc-nopool-tag">{t('no pool')}</span> : fmtD(b.allocated, decimals)}</td>
                </tr>
              );
            })}
            <tr className="alloc-tot">
              <td className="sticky-l">{t('YTD')}</td>
              <td className="num">{fmtD(result.ytd_pool, decimals)}</td>
              {ccs.map((cc) => <td key={cc} className="num">{fmtD(result.ytd[cc] || 0, decimals)}</td>)}
              <td className="num">
                {fmtD(ccs.reduce((s, cc) => s + (result.ytd[cc] || 0), 0), decimals)}
              </td>
            </tr>
            {/* v2.78.0 — budget beside the derived actual, and the variance
                between them. Rendered only when a budget has actually been
                entered: two rows of zeros on every rule that has none would
                read as "budget is nil", which is a different statement from
                "no budget was set". */}
            {!!result.budget_total && (
              <>
                <tr className="alloc-tot alloc-budget">
                  <td className="sticky-l">{t('Budget YTD')}</td>
                  <td className="num">—</td>
                  {ccs.map((cc) => (
                    <td key={cc} className="num">{fmtD(result.budget_ytd?.[cc] || 0, decimals)}</td>
                  ))}
                  <td className="num">{fmtD(result.budget_total || 0, decimals)}</td>
                </tr>
                <tr className="alloc-tot alloc-variance">
                  <td className="sticky-l">{t('Variance')}</td>
                  <td className="num">—</td>
                  {ccs.map((cc) => {
                    const v = result.variance_ytd?.[cc] || 0;
                    return (
                      <td key={cc} className={'num' + (v > 0.005 ? ' alloc-over' : '')}
                        title={v > 0 ? t('Allocated more than budget') : t('Allocated less than budget')}>
                        {fmtD(v, decimals)}
                      </td>
                    );
                  })}
                  <td className="num">
                    {fmtD(ccs.reduce((s, cc) => s + (result.variance_ytd?.[cc] || 0), 0), decimals)}
                  </td>
                </tr>
              </>
            )}
            <tr className="alloc-sec"><td className="sticky-l" colSpan={ccs.length + 3}>{result.driver_label}</td></tr>
            {result.months.map((m) => {
              const b = result.by_month[String(m)];
              if (!b) return null;
              return (
                <tr key={'d' + m} className="alloc-driver">
                  <td className="sticky-l">{t(MONTHS[m - 1])}</td>
                  <td className="num" />
                  {ccs.map((cc) => (
                    <td key={cc} className="num">
                      {b.drivers[cc] === undefined ? '' : (
                        // The count is the link to its own supporting list —
                        // an auditor should never have to ask where it came from.
                        <button type="button" className="ev-link"
                          title={t('Show the employees behind this count')}
                          onClick={() => onEvidence({ cc, month: m })}>{b.drivers[cc]}</button>
                      )}
                    </td>
                  ))}
                  <td className="num">{b.driver_total || ''}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default AllocationApp;
