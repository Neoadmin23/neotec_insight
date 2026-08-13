import { useEffect, useMemo, useState } from 'react';
import { t } from '../../utils/i18n';
import type { ReportSummary, PnlStatementResult, PnlStatementPivotResult } from '../../types';
import { api } from '../../utils/api';
import { fmtD, dropEmptyDimensions, GRANULARITY_OPTIONS } from '../../utils/format';
import { exportDimensionPivotXlsx, printDimensionPivot } from '../../utils/export';
import { ExportBar } from '../ExportBar';
import { setActiveCompany } from '../../utils/activeCompany';
import type { ReportDoc, DocRow } from '../../utils/reportdoc';
import { DimensionMultiSelect } from '../DimensionMultiSelect';
import { PlHierarchyView } from '../PlHierarchyView';

interface Props {
  reports: ReportSummary[];
  selectedReport: string;
  setSelectedReport: (s: string) => void;
  companies: any[];
  costCenters: any[];
  projects: any[];
  departments: any[];
  branches: any[];
  fiscalYears: any[];
}

type ViewMode = 'period' | 'dimension' | 'combo';
type PivotBy = 'cost_center' | 'department' | 'project' | 'branch';

export function ProfitLossStatementView({
  reports, selectedReport, setSelectedReport,
  companies, costCenters, projects, departments, branches, fiscalYears,
}: Props) {
  // Default date range: current fiscal year so far.
  const now = new Date();
  const jan1 = `${now.getFullYear()}-01-01`;
  const today = now.toISOString().slice(0, 10);

  const [company, setCompany] = useState('');
  // v2.65.0 — 'total' keeps the classic single-column statement; anything else
  // splits the same accounts into period columns.
  const [granularity, setGranularity] = useState('total');
  const [periods, setPeriods] = useState<any>(null);
  // v2.55.0 — publish the selection so the shell header, the Brand Kit and
  // every export key off the same company.
  useEffect(() => { setActiveCompany(company); }, [company]);
  const [fromDate, setFromDate] = useState(jan1);
  const [toDate, setToDate] = useState(today);
  // v1.9.58/v1.9.59 — multi-select native dimension filters.
  const [costCenter, setCostCenter] = useState<string[]>([]);
  const [project, setProject] = useState<string[]>([]);
  const [department, setDepartment] = useState<string[]>([]);
  const [branch, setBranch] = useState<string[]>([]);
  const [financeBook, setFinanceBook] = useState('');
  const [presentationCurrency, setPresentationCurrency] = useState('');
  const [showGroupAccounts, setShowGroupAccounts] = useState(true);
  const [showZeroValues, setShowZeroValues] = useState(false);
  // v1.9.61 — multi-select for custom dims too.
  const [dimFilters, setDimFilters] = useState<Record<string, string | string[]>>({});
  const [filterOptions, setFilterOptions] = useState<any>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [decimals, setDecimals] = useState(0);

  // View-by toggle.
  const [viewMode, setViewMode] = useState<ViewMode>('period');
  const [pivotBy, setPivotBy] = useState<PivotBy>('cost_center');
  // v1.9.90 — Combo view (report-row × dim1 × dim2), consistent with Trial Balance.
  const [comboDim1, setComboDim1] = useState<string>('cost_center');
  const [comboDim2, setComboDim2] = useState<string>('project');
  const [comboResult, setComboResult] = useState<any | null>(null);
  // v2.20 — Combo Layout: 'crosstab' (existing two-dim pivot) or 'pldrill'
  // (hierarchical P&L with subtotals), matching Consolidated P&L.
  const [comboLayout, setComboLayout] = useState<'crosstab' | 'pldrill'>('crosstab');
  const [plHier, setPlHier] = useState<any | null>(null);

  const [result, setResult] = useState<PnlStatementResult | null>(null);
  const [pivotResult, setPivotResult] = useState<PnlStatementPivotResult | null>(null);
  const [hideEmptyDims, setHideEmptyDims] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // v2.21 — on-screen Default Row Expand (Expanded/Collapsed), consistent with
  // Consolidated P&L. Applies the default each time a result loads.
  const [rowExpand, setRowExpand] = useState<'expanded' | 'collapsed'>('expanded');

  useEffect(() => {
    if (!company && companies.length > 0) setCompany(companies[0].name);
  }, [companies, company]);

  useEffect(() => {
    if (!company) return;
    api.listReportFilterOptions(company)
      .then((o: any) => { setFilterOptions(o); if (typeof o?.float_precision === 'number') setDecimals(o.float_precision); })
      .catch(() => setFilterOptions(null));
  }, [company]);

  async function run() {
    if (!company || !fromDate || !toDate) return;
    setLoading(true);
    setError('');
    try {
      if (viewMode === 'period' && granularity !== 'total') {
        const r = await api.runPnlStatementPeriods({
          report: selectedReport, company,
          from_date: fromDate, to_date: toDate, granularity,
          cost_center: costCenter.length ? costCenter : null,
          project: project.length ? project : null,
          department: department.length ? department : null,
          branch: branch.length ? branch : null,
          finance_book: financeBook || null,
          dimension_filters: Object.keys(dimFilters).length ? dimFilters : undefined,
          show_group_accounts: showGroupAccounts ? 1 : 0,
          show_zero_values: showZeroValues ? 1 : 0,
          presentation_currency: presentationCurrency || null,
        });
        setPeriods(r);
        setResult(null); setPivotResult(null); setComboResult(null);
      } else if (viewMode === 'period') {
        const r = (await api.runPnlStatement({
          report: selectedReport, company,
          from_date: fromDate, to_date: toDate,
          cost_center: costCenter.length ? costCenter : null,
          project: project.length ? project : null,
          department: department.length ? department : null,
          branch: branch.length ? branch : null,
          finance_book: financeBook || null,
          dimension_filters: Object.keys(dimFilters).length ? dimFilters : undefined,
          show_group_accounts: showGroupAccounts ? 1 : 0,
          show_zero_values: showZeroValues ? 1 : 0,
          presentation_currency: presentationCurrency || null,
        })) as PnlStatementResult;
        setResult(r);
        setPeriods(null);
        setPivotResult(null);
        setComboResult(null);
      } else if (viewMode === 'combo') {
        if (comboDim1 === comboDim2) {
          setError(t('Pick two different dimensions for combo view.'));
          setComboResult(null); setPlHier(null);
        } else if (comboLayout === 'pldrill') {
          const r = await api.plHierarchy({
            company: company || null,
            fiscal_year: new Date(fromDate).getFullYear(),
            month_from: 1, month_to: 12,
            primary_dim: comboDim1,
            secondary_dim: comboDim2 && comboDim2 !== comboDim1 ? comboDim2 : '',
            period_mode: 'date_range',
            period_from_date: fromDate, period_to_date: toDate,
            cost_center: costCenter.length ? costCenter : null,
            project: project.length ? project : null,
            finance_book: financeBook || null,
            dimension_filters: Object.keys(dimFilters).length ? dimFilters : null,
          });
          setPlHier(r);
          setComboResult(null); setResult(null); setPivotResult(null);
        } else {
          const r = await api.runPnlStatementComboPivot({
            report: selectedReport, company,
            from_date: fromDate, to_date: toDate,
            dim1: comboDim1, dim2: comboDim2,
            finance_book: financeBook || null,
            show_group_accounts: showGroupAccounts ? 1 : 0,
            show_zero_values: showZeroValues ? 1 : 0,
            presentation_currency: presentationCurrency || null,
          });
          setComboResult(r);
          setResult(null);
          setPivotResult(null);
          setPlHier(null);
        }
      } else {
        const r = (await api.runPnlStatementPivot({
          report: selectedReport, company,
          from_date: fromDate, to_date: toDate,
          pivot_by: pivotBy,
          finance_book: financeBook || null,
          show_group_accounts: showGroupAccounts ? 1 : 0,
          show_zero_values: showZeroValues ? 1 : 0,
          presentation_currency: presentationCurrency || null,
        })) as PnlStatementPivotResult;
        setPivotResult(r);
        setResult(null);
        setComboResult(null);
      }
    } catch (e: any) {
      setError(e?.message || 'Profit & Loss Statement failed.');
      setResult(null);
      setPivotResult(null);
      setComboResult(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!selectedReport || !company) return;
    const t = setTimeout(() => { run(); }, 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    selectedReport, company, fromDate, toDate, costCenter, project, department, branch,
    financeBook, presentationCurrency, showGroupAccounts, showZeroValues,
    viewMode, pivotBy, comboDim1, comboDim2, comboLayout, JSON.stringify(dimFilters),
  ]);

  function toggleGroup(name: string) {
    setCollapsed((s) => { const n = new Set(s); if (n.has(name)) n.delete(name); else n.add(name); return n; });
  }

  // Shared visible-rows computation — works for both period and pivot
  // accounts since both have name/parent/depth/code/label.
  function computeVisible(accs: any[]): any[] {
    if (!accs) return [];
    const accByName = new Map(accs.map((a: any) => [a.name, a]));
    let searchMatch: Set<string> | null = null;
    if (searchTerm.trim()) {
      const term = searchTerm.trim().toLowerCase();
      searchMatch = new Set();
      for (const a of accs) {
        const hit = (a.code || '').toLowerCase().includes(term) ||
                    (a.label || '').toLowerCase().includes(term);
        if (hit) {
          searchMatch.add(a.name);
          let p = a.parent;
          while (p) {
            searchMatch.add(p);
            const pa = accByName.get(p);
            if (!pa) break;
            p = pa.parent;
          }
        }
      }
    }
    const out: any[] = [];
    for (const a of accs) {
      if (searchMatch && !searchMatch.has(a.name)) continue;
      let p = a.parent;
      let hidden = false;
      while (p) {
        if (collapsed.has(p)) { hidden = true; break; }
        const pa = accByName.get(p);
        if (!pa) break;
        p = pa.parent;
      }
      if (!hidden) out.push(a);
    }
    return out;
  }

  const visiblePeriod = useMemo(
    () => computeVisible(result?.result.accounts || []),
    [result, collapsed, searchTerm]
  );

  // Pivot result with empty dimension columns optionally dropped. Must be
  // declared BEFORE visiblePivot, which depends on it.
  const effectivePivot = useMemo(() => {
    if (!pivotResult) return null;
    if (!hideEmptyDims) return pivotResult;
    return { ...pivotResult, result: dropEmptyDimensions(pivotResult.result) } as PnlStatementPivotResult;
  }, [pivotResult, hideEmptyDims]);

  const visiblePivot = useMemo(
    () => computeVisible(effectivePivot?.result.accounts || []),
    [effectivePivot, collapsed, searchTerm]
  );

  // v1.9.93 — Combo is a two-dimension pivot with the SAME shape as the
  // dimension pivot, so it reuses the identical statement-tree table, Excel
  // and Print. Only the columns differ (composite dim1 × dim2).
  const visibleCombo = useMemo(
    () => computeVisible(comboResult?.result?.accounts || []),
    [comboResult, collapsed, searchTerm]
  );

  // Apply the Default Row Expand selector whenever a fresh result loads.
  useEffect(() => {
    const accs = result?.result?.accounts || effectivePivot?.result?.accounts || comboResult?.result?.accounts || [];
    if (!accs.length) return;
    if (rowExpand === 'collapsed') {
      setCollapsed(new Set(accs.filter((a: any) => a.is_group).map((a: any) => a.name)));
    } else {
      setCollapsed(new Set());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowExpand, result, effectivePivot, comboResult]);
  const activePivot = viewMode === 'combo' ? comboResult : effectivePivot;
  const activeVisible = viewMode === 'combo' ? visibleCombo : visiblePivot;

  const currency = (result?.result || pivotResult?.result)?.currency;

  return (
    <div>
      {/* v1.9.64 — View sub-tabs (consistent across reports). */}
      <div className="view-subtabs" role="tablist" aria-label="View by">
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === 'period'}
          className={'view-subtab' + (viewMode === 'period' ? ' is-active' : '')}
          onClick={() => setViewMode('period')}
        >
          <i className="ti ti-calendar" aria-hidden /> {t('Period')}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === 'dimension'}
          className={'view-subtab' + (viewMode === 'dimension' ? ' is-active' : '')}
          onClick={() => setViewMode('dimension')}
        >
          <i className="ti ti-layout-columns" aria-hidden /> Dimension
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === 'combo'}
          className={'view-subtab' + (viewMode === 'combo' ? ' is-active' : '')}
          onClick={() => setViewMode('combo')}
          title="One row per (report row × dim1 × dim2) tuple"
        >
          <i className="ti ti-table" aria-hidden /> {t('Combo')}
        </button>
      </div>

      <div className="filter-strip">
        <div className="filter-grid">
          <label><span className="flbl">Report</span>
            <select value={selectedReport} onChange={(e) => setSelectedReport(e.target.value)}>
              {reports.map((r) => <option key={r.name} value={r.slug || r.name}>{r.report_name}</option>)}
            </select>
          </label>
          <label><span className="flbl">Company</span>
            <select value={company} onChange={(e) => setCompany(e.target.value)}>
              {companies.length === 0 && <option value="">— No companies found —</option>}
              {companies.map((c: any) => (
                <option key={c.name} value={c.name}>
                  {c.label}{c.abbr ? ` (${c.abbr})` : ''}
                </option>
              ))}
            </select>
          </label>
          <label><span className="flbl">From date</span>
            <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
          </label>
          <label><span className="flbl">To date</span>
            <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
          </label>
          <label><span className="flbl">{t('Default Row Expand')}</span>
            <select value={rowExpand} onChange={(e) => setRowExpand(e.target.value as 'expanded' | 'collapsed')}>
              <option value="expanded">{t('Expanded')}</option>
              <option value="collapsed">{t('Collapsed')}</option>
            </select>
          </label>
          {viewMode === 'dimension' && (
            <label><span className="flbl">Pivot by</span>
              <select value={pivotBy} onChange={(e) => setPivotBy(e.target.value as PivotBy)}>
                <option value="cost_center">Cost Center</option>
                <option value="department">Department</option>
                <option value="project">Project</option>
                <option value="branch">Branch</option>
              </select>
            </label>
          )}
          {viewMode === 'combo' && (
            <>
              <label><span className="flbl">Outer dim</span>
                <select value={comboDim1} onChange={(e) => setComboDim1(e.target.value)}>
                  <option value="cost_center">Cost Center</option>
                  <option value="department">Department</option>
                  <option value="project">Project</option>
                  <option value="branch">Branch</option>
                  {filterOptions?.dimensions?.length > 0 && <option disabled>──── Custom ────</option>}
                  {filterOptions?.dimensions?.map((dim: any) => (
                    <option key={dim.fieldname} value={dim.fieldname}>{dim.label}</option>
                  ))}
                </select>
              </label>
              <label><span className="flbl">Inner dim</span>
                <select value={comboDim2} onChange={(e) => setComboDim2(e.target.value)}>
                  <option value="cost_center">Cost Center</option>
                  <option value="department">Department</option>
                  <option value="project">Project</option>
                  <option value="branch">Branch</option>
                  {filterOptions?.dimensions?.length > 0 && <option disabled>──── Custom ────</option>}
                  {filterOptions?.dimensions?.map((dim: any) => (
                    <option key={dim.fieldname} value={dim.fieldname}>{dim.label}</option>
                  ))}
                </select>
              </label>
              <label><span className="flbl">{t('Layout')}</span>
                <select value={comboLayout} onChange={(e) => setComboLayout(e.target.value as 'crosstab' | 'pldrill')}>
                  <option value="crosstab">{t('Cross-tab')}</option>
                  <option value="pldrill">{t('P&L Drill (with subtotals)')}</option>
                </select>
              </label>
            </>
          )}
          {viewMode === 'period' && <>
            <label><span className="flbl">Cost center</span>
              <DimensionMultiSelect
                value={costCenter}
                options={costCenters.map((c: any) => ({ name: c.name, label: c.label }))}
                onChange={setCostCenter}
                placeholder="— All cost centers —"
              />
            </label>
            <label><span className="flbl">Project</span>
              <DimensionMultiSelect
                value={project}
                options={projects.map((p: any) => ({ name: p.name, label: p.label }))}
                onChange={setProject}
                placeholder="— All projects —"
              />
            </label>
            <label><span className="flbl">Department</span>
              <DimensionMultiSelect
                value={department}
                options={departments.map((d: any) => ({ name: d.name, label: d.label }))}
                onChange={setDepartment}
                placeholder="— All departments —"
              />
            </label>
            <label><span className="flbl">Branch {branches.length > 0 && <span className="muted" style={{ fontSize: 9 }}>({branches.length})</span>}</span>
              <DimensionMultiSelect
                value={branch}
                options={branches.map((b: any) => ({ name: b.name, label: b.label }))}
                onChange={setBranch}
                placeholder="— All branches —"
              />
            </label>
          </>}
          {filterOptions && filterOptions.finance_books.length > 0 && (
            <label><span className="flbl">Finance book</span>
              <select value={financeBook} onChange={(e) => setFinanceBook(e.target.value)}>
                <option value="">— All finance books —</option>
                {filterOptions.finance_books.map((fb: string) => <option key={fb} value={fb}>{fb}</option>)}
              </select>
            </label>
          )}
          <label><span className="flbl">Currency</span>
            <select value={presentationCurrency} onChange={(e) => setPresentationCurrency(e.target.value)}>
              <option value="">
                {filterOptions?.company_currency ? `${filterOptions.company_currency} (company)` : '— Company currency —'}
              </option>
              {filterOptions?.currencies
                ?.filter((c: any) => c.name !== filterOptions.company_currency)
                .map((c: any) => <option key={c.name} value={c.name}>{c.name} — {c.currency_name}</option>)}
            </select>
          </label>
          {viewMode === 'period' && filterOptions?.dimensions?.map((dim: any) => {
            const cur = dimFilters[dim.fieldname];
            const curArr: string[] = Array.isArray(cur) ? cur : cur ? [cur as string] : [];
            return (
              <label key={dim.fieldname}><span className="flbl">{dim.label}</span>
                <DimensionMultiSelect
                  value={curArr}
                  options={(dim.options || []).map((o: string) => ({ name: o, label: o }))}
                  onChange={(next) => setDimFilters((prev) => {
                    const out = { ...prev };
                    if (next.length === 0) delete out[dim.fieldname];
                    else out[dim.fieldname] = next;
                    return out;
                  })}
                  placeholder={`— All ${dim.label.toLowerCase()} —`}
                />
              </label>
            );
          })}
          <label><span className="flbl">Search accounts</span>
            <input type="text" placeholder="Code or name…" value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} />
          </label>
          <label><span className="flbl">Decimals</span>
            <select value={decimals} onChange={(e) => setDecimals(parseInt(e.target.value))}>
              {[0, 1, 2, 3, 4].map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
        </div>
        <div className="derived-row">
          {viewMode === 'period' && (
            <label className="gran-inline">{t('Granularity')}
              <select value={granularity} onChange={(e) => setGranularity(e.target.value)}>
                <option value="total">{t('Single column (whole period)')}</option>
                {GRANULARITY_OPTIONS.filter((g) => g.value !== 'quarter_ytd')
                  .map((g) => <option key={g.value} value={g.value}>{t(g.label)}</option>)}
              </select>
            </label>
          )}
          <label className="chk"><input type="checkbox" checked={showGroupAccounts} onChange={(e) => setShowGroupAccounts(e.target.checked)} /> Show group accounts</label>
          <label className="chk"><input type="checkbox" checked={showZeroValues} onChange={(e) => setShowZeroValues(e.target.checked)} /> Show zero values</label>
          {viewMode === 'dimension' && (
            <label className="chk"><input type="checkbox" checked={hideEmptyDims} onChange={(e) => setHideEmptyDims(e.target.checked)} /> Hide empty dimensions</label>
          )}
          {currency?.rate_missing && (
            <span style={{ fontSize: 11, color: '#854f0b' }}>⚠ No exchange rate found — showing unconverted figures.</span>
          )}
          {currency && currency.conversion_rate !== 1 && (
            <span style={{ fontSize: 11, color: 'var(--info, #0c447c)' }}>
              Converted to {currency.presentation_currency} @ {currency.conversion_rate}
            </span>
          )}
        </div>
        <div className="action-row">
          <button onClick={run} disabled={loading}>
            <i className="ti ti-refresh" aria-hidden /> {loading ? 'Running…' : 'Run'}
          </button>
          {/* v2.55.0 — every output the ledger has, on the statements too.
              Dimension and Combo views keep their own pivot exporters, which
              understand the crosstab shape the flat document model does not. */}
          {viewMode === 'period' ? (
            <ExportBar
              company={company}
              companyLabel={company}
              disabled={!result && !periods}
              getDoc={() => (periods ? buildPnlPeriodsDoc(periods, decimals) : result ? buildPnlDoc(result, decimals) : null)}
            />
          ) : (
            <>
              <button
                onClick={() => {
                  if (viewMode === 'dimension' && effectivePivot) {
                    exportDimensionPivotXlsx(effectivePivot.result as any, 'profit_and_loss_by_dimension.xlsx', decimals);
                  } else if (viewMode === 'combo' && comboResult) {
                    exportDimensionPivotXlsx(comboResult.result as any, 'profit_and_loss_combo.xlsx', decimals);
                  }
                }}
                disabled={(viewMode === 'dimension' && !pivotResult) || (viewMode === 'combo' && !comboResult)}
              >
                <i className="ti ti-file-spreadsheet" aria-hidden /> Excel
              </button>
              <button
                onClick={() => {
                  const dimName = (d: string) => d === 'cost_center' ? 'Cost Center' : d === 'department' ? 'Department' : d === 'branch' ? 'Branch' : d === 'project' ? 'Project' : d;
                  if (viewMode === 'dimension' && effectivePivot) {
                    const co = effectivePivot.result.currency;
                    printDimensionPivot(
                      'Profit & Loss Statement — by ' + dimName(pivotBy),
                      `${company} · as of ${toDate}` + (co?.presentation_currency ? ` · ${co.presentation_currency}` : ''),
                      effectivePivot.result as any, decimals);
                  } else if (viewMode === 'combo' && comboResult) {
                    const co = comboResult.result.currency;
                    printDimensionPivot(
                      `Profit & Loss Statement — ${dimName(comboDim1)} × ${dimName(comboDim2)}`,
                      `${company} · ${fromDate} → ${toDate}` + (co?.presentation_currency ? ` · ${co.presentation_currency}` : ''),
                      comboResult.result as any, decimals);
                  }
                }}
                disabled={(viewMode === 'dimension' && !pivotResult) || (viewMode === 'combo' && !comboResult)}
              >
                <i className="ti ti-printer" aria-hidden /> Print
              </button>
            </>
          )}
          <span style={{ flex: 1 }} />
          {(result?.performance || pivotResult?.performance) && (
            <span className="run-meta">
              {(result?.performance || pivotResult?.performance)!.execution_ms}ms
              {(result?.performance || pivotResult?.performance)!.cache_hit ? ' · cached' : ''}
            </span>
          )}
        </div>
        {error && <div className="run-error">{error}</div>}
      </div>

      {/* ── Combo view (report-row × dim1 × dim2) ─────────────────────── */}
      {/* Combo now renders through the shared dimension table below
          (same statement structure, composite dim1 × dim2 columns). */}

      {/* ── Period view ───────────────────────────────────────────────── */}
      {viewMode === 'period' && result && (
        <div className="matrix-wrap">
          <div className="matrix-scroll">
            <table className="pivot-matrix bs-table">
              <thead>
                <tr>
                  <th className="pivot-row-head">{t('Account')}</th>
                  <th>{result.filters.from_date} → {result.filters.to_date}</th>
                </tr>
              </thead>
              <tbody>
                {renderPnlSection(decimals, 'Income', visiblePeriod.filter((a) => a.root_type === 'Income'), collapsed, toggleGroup, result.result.summary.total_income)}
                {renderPnlSection(decimals, 'Expense', visiblePeriod.filter((a) => a.root_type === 'Expense'), collapsed, toggleGroup, result.result.summary.total_expense)}
                <tr className="pivot-row-subtotal" style={{ background: 'var(--total-bg, #e4e9f0)', borderTop: '2px solid var(--border-strong)' }}>
                  <td className="pivot-row-label" style={{ fontWeight: 700 }}>
                    {result.result.summary.is_loss ? 'Net Loss' : 'Net Profit'}
                  </td>
                  <td style={{
                    fontWeight: 700,
                    color: result.result.summary.is_loss ? 'var(--neg, #a02323)' : 'var(--good, #0f6e56)',
                  }}>
                    {result.result.summary.is_loss
                      ? `(${fmtD(Math.abs(result.result.summary.net_profit), decimals)})`
                      : fmtD(result.result.summary.net_profit, decimals)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Period view, split by granularity (v2.65.0) ───────────────── */}
      {viewMode === 'period' && periods && (
        <div className="matrix-wrap">
          <div className="matrix-scroll">
            <table className="pivot-matrix bs-table">
              <thead>
                <tr>
                  <th className="pivot-row-head">{t('Account')}</th>
                  {periods.columns.map((c: any) => (
                    <th key={c.key} className={'gran-col gran-' + c.kind}>{c.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(['Income', 'Expense'] as const).map((rt) => {
                  const rows = (periods.result.accounts || []).filter((a: any) => a.root_type === rt);
                  if (!rows.length) return null;
                  const tot = rt === 'Income'
                    ? periods.result.summary.total_income
                    : periods.result.summary.total_expense;
                  return [
                    <tr key={rt + '-h'} className="pivot-row-section">
                      <td className="pivot-row-label" colSpan={periods.columns.length + 1}>{t(rt)}</td>
                    </tr>,
                    ...rows.map((a: any) => (
                      <tr key={rt + a.name} className={a.is_group ? 'pivot-row-group' : ''}>
                        <td className="pivot-row-label" style={{ paddingLeft: 8 + 14 * Math.max(0, a.depth) }}>
                          {a.code ? a.code + '  ' : ''}{a.label}
                        </td>
                        {periods.columns.map((c: any) => (
                          <td key={c.key} className={'gran-col gran-' + c.kind}
                              style={{ fontWeight: a.is_group ? 600 : undefined }}>
                            {fmtD(a.amounts?.[c.key] ?? 0, decimals)}
                          </td>
                        ))}
                      </tr>
                    )),
                    <tr key={rt + '-t'} className="pivot-row-subtotal">
                      <td className="pivot-row-label" style={{ fontWeight: 700 }}>{t('Total')} {t(rt)}</td>
                      {periods.columns.map((c: any) => (
                        <td key={c.key} className={'gran-col gran-' + c.kind} style={{ fontWeight: 700 }}>
                          {fmtD(tot?.[c.key] ?? 0, decimals)}
                        </td>
                      ))}
                    </tr>,
                  ];
                })}
                <tr className="pivot-row-subtotal"
                    style={{ background: 'var(--total-bg, #e4e9f0)', borderTop: '2px solid var(--border-strong)' }}>
                  <td className="pivot-row-label" style={{ fontWeight: 700 }}>{t('Net Profit')}</td>
                  {periods.columns.map((c: any) => {
                    const v = periods.result.summary.net_profit?.[c.key] ?? 0;
                    return (
                      <td key={c.key} className={'gran-col gran-' + c.kind}
                          style={{ fontWeight: 700, color: v < 0 ? 'var(--neg, #a02323)' : 'var(--good, #0f6e56)' }}>
                        {v < 0 ? `(${fmtD(Math.abs(v), decimals)})` : fmtD(v, decimals)}
                      </td>
                    );
                  })}
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Combo · P&L Drill (hierarchical with subtotals) ───────────── */}
      {viewMode === 'combo' && comboLayout === 'pldrill' && plHier && (
        <div className="matrix-wrap">
          <PlHierarchyView data={plHier} decimals={decimals} />
        </div>
      )}

      {/* ── Dimension pivot view ──────────────────────────────────────── */}
      {(viewMode === 'dimension' || viewMode === 'combo') && activePivot && (
        <div className="matrix-wrap">
          {viewMode === 'combo' && activePivot.result?.combo?.truncated && (
            <div className="studio-hint" style={{ margin: '0 0 8px' }}>
              {t('Showing the top {n} dimension combinations by value.').replace('{n}', String(activePivot.result.combo.shown))}
            </div>
          )}
          <div className="matrix-scroll">
            <table className="pivot-matrix">
              <thead>
                <tr>
                  <th className="pivot-row-head">{t('Account')}</th>
                  <th className="pivot-col-total">{t('Total')}</th>
                  {activePivot.result.dimensions.map((d: any) => <th key={d.name}>{d.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {activeVisible.map((a: any) => {
                  const padLeft = Math.max(0, a.depth - 1) * 14 + 8;
                  const isCollapsed = collapsed.has(a.name);
                  return (
                    <tr key={a.name} className={a.is_group ? 'pivot-row-subtotal' : ''}>
                      <td className="pivot-row-label" style={{ paddingLeft: padLeft }}>
                        {a.is_group ? (
                          <button onClick={() => toggleGroup(a.name)}
                            style={{ background: 'transparent', border: 0, cursor: 'pointer', padding: 0, marginRight: 4, color: 'var(--text-muted)', fontSize: 11 }}>
                            {isCollapsed ? '▶' : '▼'}
                          </button>
                        ) : null}
                        <span style={{ fontWeight: a.is_group ? 500 : 400 }}>
                          {a.code && <span className="muted" style={{ marginRight: 6 }}>{a.code}</span>}
                          {a.label}
                        </span>
                      </td>
                      <td className={'pivot-col-total ' + (a.total < 0 ? 'neg' : a.total === 0 ? 'zero' : '')}>{fmtD(a.total, decimals)}</td>
                      {activePivot.result.dimensions.map((d: any) => {
                        const v = a.by_dim[d.name] || 0;
                        return <td key={d.name} className={v < 0 ? 'neg' : v === 0 ? 'zero' : ''}>{fmtD(v, decimals)}</td>;
                      })}
                    </tr>
                  );
                })}
                {/* Summary rows */}
                <tr className="pivot-row-subtotal" style={{ borderTop: '2px solid var(--border-strong)' }}>
                  <td className="pivot-row-label" style={{ fontWeight: 600 }}>Total Income</td>
                  <td className="pivot-col-total" style={{ fontWeight: 600 }}>{fmtD(activePivot.result.summary.total.total_income, decimals)}</td>
                  {activePivot.result.dimensions.map((d: any) => (
                    <td key={d.name} style={{ fontWeight: 600 }}>{fmtD(activePivot.result.summary.by_dim[d.name]?.total_income || 0, decimals)}</td>
                  ))}
                </tr>
                <tr className="pivot-row-subtotal">
                  <td className="pivot-row-label" style={{ fontWeight: 600 }}>Total Expense</td>
                  <td className="pivot-col-total" style={{ fontWeight: 600 }}>{fmtD(activePivot.result.summary.total.total_expense, decimals)}</td>
                  {activePivot.result.dimensions.map((d: any) => (
                    <td key={d.name} style={{ fontWeight: 600 }}>{fmtD(activePivot.result.summary.by_dim[d.name]?.total_expense || 0, decimals)}</td>
                  ))}
                </tr>
                <tr className="pivot-row-subtotal" style={{ background: 'var(--total-bg, #e4e9f0)' }}>
                  <td className="pivot-row-label" style={{ fontWeight: 700 }}>
                    {activePivot.result.summary.total.is_loss ? 'Net Loss' : 'Net Profit'}
                  </td>
                  <td className="pivot-col-total" style={{
                    fontWeight: 700,
                    color: activePivot.result.summary.total.is_loss ? 'var(--neg, #a02323)' : 'var(--good, #0f6e56)',
                  }}>
                    {activePivot.result.summary.total.is_loss
                      ? `(${fmtD(Math.abs(activePivot.result.summary.total.net_profit), decimals)})`
                      : fmtD(activePivot.result.summary.total.net_profit, decimals)}
                  </td>
                  {activePivot.result.dimensions.map((d: any) => {
                    const np = activePivot.result.summary.by_dim[d.name]?.net_profit || 0;
                    return (
                      <td key={d.name} style={{ fontWeight: 700, color: np < 0 ? 'var(--neg, #a02323)' : 'var(--good, #0f6e56)' }}>
                        {np < 0 ? `(${fmtD(Math.abs(np), decimals)})` : fmtD(np, decimals)}
                      </td>
                    );
                  })}
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function renderPnlSection(
  decimals: number,
  rootType: string,
  rows: any[],
  collapsed: Set<string>,
  toggleGroup: (n: string) => void,
  sectionTotal: number,
) {
  const title = rootType === 'Income' ? 'Income' : 'Expense';
  return (
    <>
      <tr className="pivot-row-section"><td colSpan={2}>{title}</td></tr>
      {rows.map((a) => {
        const padLeft = Math.max(0, a.depth - 1) * 14 + 8;
        const isCollapsed = collapsed.has(a.name);
        return (
          <tr key={a.name} className={a.is_group ? 'pivot-row-subtotal' : ''}>
            <td className="pivot-row-label" style={{ paddingLeft: padLeft }}>
              {a.is_group ? (
                <button onClick={() => toggleGroup(a.name)}
                  style={{ background: 'transparent', border: 0, cursor: 'pointer', padding: 0, marginRight: 4, color: 'var(--text-muted)', fontSize: 11 }}>
                  {isCollapsed ? '▶' : '▼'}
                </button>
              ) : null}
              <span style={{ fontWeight: a.is_group ? 500 : 400 }}>
                {a.code && <span className="muted" style={{ marginRight: 6 }}>{a.code}</span>}
                {a.label}
              </span>
            </td>
            <td className={a.amount < 0 ? 'neg' : a.amount === 0 ? 'zero' : ''}>{fmtD(a.amount, decimals)}</td>
          </tr>
        );
      })}
      <tr className="pivot-row-subtotal" style={{ borderTop: '2px solid var(--border-strong)' }}>
        <td className="pivot-row-label" style={{ fontWeight: 600 }}>Total {title}</td>
        <td style={{ fontWeight: 600 }}>{fmtD(sectionTotal, decimals)}</td>
      </tr>
    </>
  );
}

/** v2.65.0 — the period-split statement as a portable document, so Excel,
 *  CSV, PDF, Print and PNG all carry the same columns the screen shows. */
function buildPnlPeriodsDoc(p: any, decimals: number): ReportDoc {
  const cols = p.columns || [];
  const money = (v: any) => ({ v: Number(v || 0), text: fmtD(Number(v || 0), decimals), num: true as const });
  const rows: DocRow[] = [];

  (['Income', 'Expense'] as const).forEach((rt) => {
    const list = (p.result.accounts || []).filter((a: any) => a.root_type === rt);
    if (!list.length) return;
    rows.push({ kind: 'sec', cells: [{ v: rt, colSpan: cols.length + 1, bold: true }] });
    for (const a of list) {
      rows.push({
        kind: a.is_group ? 'grp' : 'normal',
        cells: [
          { v: (a.code ? a.code + '  ' : '') + a.label, indent: Math.max(0, a.depth), bold: !!a.is_group },
          ...cols.map((c: any) => ({ ...money(a.amounts?.[c.key]), bold: !!a.is_group })),
        ],
      });
    }
    const tot = rt === 'Income' ? p.result.summary.total_income : p.result.summary.total_expense;
    rows.push({
      kind: 'tot',
      cells: [{ v: `Total ${rt}`, bold: true },
              ...cols.map((c: any) => ({ ...money(tot?.[c.key]), bold: true }))],
    });
  });

  rows.push({
    kind: 'grand',
    cells: [{ v: 'Net Profit', bold: true },
            ...cols.map((c: any) => {
              const v = Number(p.result.summary.net_profit?.[c.key] || 0);
              return { ...money(v), bold: true, fg: v < 0 ? '#a02323' : '#0f6e56' };
            })],
  });

  const cur = p.result.currency;
  return {
    title: 'Profit & Loss Statement',
    subtitle: (p.filters.granularity || '').replace(/_/g, ' + '),
    company: p.filters.company,
    companyLabel: p.filters.company,
    period: `${p.filters.from_date} → ${p.filters.to_date}`,
    columns: [{ label: 'Account', width: 46 },
              ...cols.map((c: any) => ({ label: c.label, num: true, width: 15 }))],
    rows,
    fileBase: 'profit_and_loss_by_period',
    // Twelve months plus quarters will not fit portrait.
    orientation: cols.length > 4 ? 'landscape' : 'portrait',
    note: cur && cur.conversion_rate !== 1
      ? `Converted to ${cur.presentation_currency} @ ${cur.conversion_rate}` : undefined,
  };
}

/** v2.55.0 — the statement as a portable document: one description that the
 *  shared writers turn into Excel, CSV, PDF, Print and PNG. Replaces the old
 *  printPnl(), which hand-built HTML and — because the header cells were
 *  written inside a template literal — printed the literal text `{t('Account')}`
 *  as a column heading. */
function buildPnlDoc(r: PnlStatementResult, decimals: number): ReportDoc {
  const cur = r.result.currency;
  const note = cur && cur.conversion_rate !== 1
    ? `Converted to ${cur.presentation_currency} @ ${cur.conversion_rate}`
    : undefined;
  // v: raw for Excel · text: formatted for Print/PDF/CSV/PNG. Passing only
  // the raw number printed `5385470.85` where the screen showed thousands
  // separators.
  const money = (v: number | null | undefined) =>
    ({ v: Number(v || 0), text: fmtD(Number(v || 0), decimals), num: true as const });
  const rows: DocRow[] = [];

  const section = (title: string, rootType: string, total: number) => {
    rows.push({ kind: 'sec', cells: [{ v: title, colSpan: 2, bold: true }] });
    for (const a of r.result.accounts.filter((x) => x.root_type === rootType)) {
      rows.push({
        kind: a.is_group ? 'grp' : 'normal',
        cells: [
          { v: (a.code ? a.code + '  ' : '') + a.label, indent: Math.max(0, a.depth), bold: !!a.is_group },
          { ...money(a.amount), bold: !!a.is_group },
        ],
      });
    }
    rows.push({ kind: 'tot', cells: [{ v: `Total ${title}`, bold: true }, { ...money(total), bold: true }] });
  };

  section('Income', 'Income', r.result.summary.total_income);
  section('Expense', 'Expense', r.result.summary.total_expense);

  const isLoss = !!r.result.summary.is_loss;
  const np = r.result.summary.net_profit;
  rows.push({
    kind: 'grand',
    cells: [
      { v: isLoss ? 'Net Loss' : 'Net Profit', bold: true },
      { ...money(isLoss ? -Math.abs(Number(np || 0)) : np), bold: true, fg: isLoss ? '#a02323' : '#0f6e56' },
    ],
  });

  return {
    title: 'Profit & Loss Statement',
    company: r.filters.company,
    companyLabel: r.filters.company,
    period: `${r.filters.from_date} → ${r.filters.to_date}`,
    columns: [
      { label: 'Account', width: 46 },
      { label: `${r.filters.from_date} → ${r.filters.to_date}`, num: true, width: 20 },
    ],
    rows,
    fileBase: 'profit_and_loss',
    orientation: 'portrait',
    note,
  };
}
