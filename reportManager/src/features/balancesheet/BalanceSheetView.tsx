import { useEffect, useMemo, useState } from 'react';
import { t } from '../../utils/i18n';
import type { ReportSummary, BalanceSheetResult, BalancePivotResult } from '../../types';
import { api } from '../../utils/api';
import { fmtD, dropEmptyDimensions } from '../../utils/format';
import { exportDimensionPivotXlsx, printDimensionPivot } from '../../utils/export';
import { ExportBar } from '../ExportBar';
import { setActiveCompany } from '../../utils/activeCompany';
import type { ReportDoc, DocRow } from '../../utils/reportdoc';
import { useDimFilters } from '../../utils/dimFilters';
import { DimensionMultiSelect } from '../DimensionMultiSelect';

interface Props {
  reports: ReportSummary[];
  selectedReport: string;
  setSelectedReport: (s: string) => void;
  companies: any[];
  costCenters: any[];
  projects: any[];
  departments: any[];
  branches: any[];
}

export function BalanceSheetView({ reports, selectedReport, setSelectedReport, companies, costCenters, projects, departments, branches }: Props) {
  // v1.9.57 — read global dimension catalogue for the Pivot by dropdown.
  const { dimensions: accountingDims } = useDimFilters();
  const today = new Date().toISOString().slice(0, 10);
  // Default prior date = same date one year earlier.
  const yearAgo = (() => {
    const d = new Date();
    d.setFullYear(d.getFullYear() - 1);
    return d.toISOString().slice(0, 10);
  })();
  const [company, setCompany] = useState('');
  useEffect(() => { setActiveCompany(company); }, [company]);
  const [asOfDate, setAsOfDate] = useState(today);
  const [priorAsOfDate, setPriorAsOfDate] = useState(yearAgo);
  const [showPrior, setShowPrior] = useState(true);
  // v1.9.58 — native dimension filters are arrays (multi-select).
  const [costCenter, setCostCenter] = useState<string[]>([]);
  const [project, setProject] = useState<string[]>([]);
  const [department, setDepartment] = useState<string[]>([]);
  const [branch, setBranch] = useState<string[]>([]);
  // v1.9 filters
  const [financeBook, setFinanceBook] = useState('');
  const [presentationCurrency, setPresentationCurrency] = useState('');
  const [showGroupAccounts, setShowGroupAccounts] = useState(true);
  const [showZeroValues, setShowZeroValues] = useState(false);
  const [showUnclosedPl, setShowUnclosedPl] = useState(true);
  // v1.9.61 — dimFilters values widened to string | string[] for multi-
  // select consistency with native dims.
  const [dimFilters, setDimFilters] = useState<Record<string, string | string[]>>({});
  const [filterOptions, setFilterOptions] = useState<{
    dimensions: { label: string; fieldname: string; options: string[] }[];
    finance_books: string[];
    currencies: { name: string; currency_name: string; symbol: string }[];
    company_currency: string;
  } | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [decimals, setDecimals] = useState(0);
  // v1.9.63 — view modes extended (see TrialBalanceView for full notes).
  const [viewMode, setViewMode] = useState<'period' | 'dimension' | 'combo' | 'multi_period'>('period');
  const [comboDim1, setComboDim1] = useState<string>('cost_center');
  const [comboDim2, setComboDim2] = useState<string>('project');
  const [comboResult, setComboResult] = useState<any | null>(null);
  const [mpGranularity, setMpGranularity] = useState<'month' | 'quarter' | 'half' | 'year'>('quarter');
  const [mpResult, setMpResult] = useState<any | null>(null);
  const [bsFy, setBsFy] = useState<string | number>(2026);
  // v1.9.57 — pivotBy widened from a closed union to string so configured
  // custom Accounting Dimensions can be picked. Backend validates against
  // the dimension whitelist (_validate_pivot_by in balance_execution.py).
  const [pivotBy, setPivotBy] = useState<string>('cost_center');
  const [pivotResult, setPivotResult] = useState<BalancePivotResult | null>(null);
  const [hideEmptyDims, setHideEmptyDims] = useState(true);
  const [result, setResult] = useState<BalanceSheetResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // v2.21 — on-screen Default Row Expand, consistent across reports.
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
    if (!company || !asOfDate) return;
    setLoading(true);
    setError('');
    try {
      if (viewMode === 'dimension') {
        const r = (await api.runBalanceSheetPivot({
          report: selectedReport,
          company,
          as_of_date: asOfDate,
          pivot_by: pivotBy,
          finance_book: financeBook || null,
          show_group_accounts: showGroupAccounts ? 1 : 0,
          show_zero_values: showZeroValues ? 1 : 0,
          presentation_currency: presentationCurrency || null,
        })) as BalancePivotResult;
        setPivotResult(r);
        setResult(null);
        setComboResult(null);
        setMpResult(null);
      } else if (viewMode === 'combo') {
        if (comboDim1 === comboDim2) {
          setError('Pick two different dimensions for combo view.');
          setComboResult(null);
        } else {
          const r = await api.runBalanceSheetComboPivot({
            report: selectedReport,
            company,
            as_of_date: asOfDate,
            dim1: comboDim1,
            dim2: comboDim2,
            finance_book: financeBook || null,
            show_group_accounts: showGroupAccounts ? 1 : 0,
            show_zero_values: showZeroValues ? 1 : 0,
            presentation_currency: presentationCurrency || null,
          });
          setComboResult(r);
          setResult(null);
          setPivotResult(null);
          setMpResult(null);
        }
      } else if (viewMode === 'multi_period') {
        const r = await api.runBalanceSheetMultiPeriod({
          report: selectedReport,
          company,
          fiscal_year: bsFy,
          granularity: mpGranularity,
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
        setMpResult(r);
        setResult(null);
        setPivotResult(null);
        setComboResult(null);
      } else {
        const r = (await api.runBalanceSheet({
          report: selectedReport,
          company,
          as_of_date: asOfDate,
          prior_as_of_date: showPrior ? priorAsOfDate : null,
          cost_center: costCenter.length ? costCenter : null,
          project: project.length ? project : null,
          department: department.length ? department : null,
          branch: branch.length ? branch : null,
          finance_book: financeBook || null,
          dimension_filters: Object.keys(dimFilters).length ? dimFilters : undefined,
          show_group_accounts: showGroupAccounts ? 1 : 0,
          show_zero_values: showZeroValues ? 1 : 0,
          show_unclosed_pl: showUnclosedPl ? 1 : 0,
          presentation_currency: presentationCurrency || null,
        })) as BalanceSheetResult;
        setResult(r);
        setPivotResult(null);
        setComboResult(null);
        setMpResult(null);
      }
    } catch (e: any) {
      setError(e?.message || 'Balance sheet failed.');
      setResult(null);
      setPivotResult(null);
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
    selectedReport, company, asOfDate, priorAsOfDate, showPrior, costCenter,
    project, department, branch, financeBook, presentationCurrency,
    showGroupAccounts, showZeroValues, showUnclosedPl, JSON.stringify(dimFilters),
    viewMode, pivotBy,
  ]);

  function toggleGroup(name: string) {
    setCollapsed((s) => { const n = new Set(s); if (n.has(name)) n.delete(name); else n.add(name); return n; });
  }

  const visibleAccounts = useMemo(() => {
    if (!result) return [];
    const accs = result.result.accounts;
    const accByName = new Map(accs.map((a) => [a.name, a]));

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

    const out = [];
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
  }, [result, collapsed, searchTerm]);

  const effectivePivot = useMemo(() => {
    if (!pivotResult) return null;
    if (!hideEmptyDims) return pivotResult;
    return { ...pivotResult, result: dropEmptyDimensions(pivotResult.result) };
  }, [pivotResult, hideEmptyDims]);

  const filterVisibleAccounts = (accs: any[]) => {
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
  };

  const visiblePivotAccounts = useMemo(
    () => (effectivePivot ? filterVisibleAccounts(effectivePivot.result.accounts) : []),
    [effectivePivot, collapsed, searchTerm]
  );
  // v1.9.94 — Combo reuses the dimension tree; only the columns differ.
  const visibleComboAccounts = useMemo(
    () => (comboResult ? filterVisibleAccounts(comboResult.result.accounts) : []),
    [comboResult, collapsed, searchTerm]
  );

  useEffect(() => {
    const accs = result?.result?.accounts || effectivePivot?.result?.accounts || comboResult?.result?.accounts || [];
    if (!accs.length) return;
    if (rowExpand === 'collapsed') setCollapsed(new Set(accs.filter((a: any) => a.is_group).map((a: any) => a.name)));
    else setCollapsed(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowExpand, result, effectivePivot, comboResult]);
  const activePivot = viewMode === 'combo' ? comboResult : effectivePivot;
  const activeVisible = viewMode === 'combo' ? visibleComboAccounts : visiblePivotAccounts;

  function rowsForRootType(rt: string) {
    return visibleAccounts.filter((a) => a.root_type === rt);
  }

  // v1.9.57 — same pivot-label helper as TrialBalanceView. Handles natives
  // by name; for custom dimensions, falls back to the discovered label.
  function pivotLabel(pb: string): string {
    if (pb === 'cost_center') return 'Cost Center';
    if (pb === 'department') return 'Department';
    if (pb === 'project') return 'Project';
    if (pb === 'branch') return 'Branch';
    const found = accountingDims.find((d) => d.fieldname === pb);
    return found?.label || pb;
  }

  return (
    <div>
      {/* v1.9.64 — View sub-tabs (consistent with RunTab + TB pattern). */}
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
          aria-selected={viewMode === 'multi_period'}
          className={'view-subtab' + (viewMode === 'multi_period' ? ' is-active' : '')}
          onClick={() => setViewMode('multi_period')}
          title="Closing balance per period boundary"
        >
          <i className="ti ti-calendar-stats" aria-hidden /> Periods
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === 'combo'}
          className={'view-subtab' + (viewMode === 'combo' ? ' is-active' : '')}
          onClick={() => setViewMode('combo')}
          title="Two-dimension combo view"
        >
          <i className="ti ti-table" aria-hidden /> Combo
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
          <label><span className="flbl">As of date</span>
            <input type="date" value={asOfDate} onChange={(e) => setAsOfDate(e.target.value)} />
          </label>
          <label><span className="flbl">{t('Default Row Expand')}</span>
            <select value={rowExpand} onChange={(e) => setRowExpand(e.target.value as 'expanded' | 'collapsed')}>
              <option value="expanded">{t('Expanded')}</option>
              <option value="collapsed">{t('Collapsed')}</option>
            </select>
          </label>
          {viewMode === 'multi_period' && (
            <>
              <label><span className="flbl">Fiscal year</span>
                <input type="number" value={bsFy as any} onChange={(e) => setBsFy(parseInt(e.target.value) || 2026)} />
              </label>
              <label><span className="flbl">Granularity</span>
                <select value={mpGranularity} onChange={(e) => setMpGranularity(e.target.value as any)}>
                  <option value="month">Month (12 cols)</option>
                  <option value="quarter">Quarter (4 cols)</option>
                  <option value="half">Half-year (2 cols)</option>
                  <option value="year">Year (1 col)</option>
                </select>
              </label>
            </>
          )}
          {viewMode === 'combo' && (
            <>
              <label><span className="flbl">Outer dim</span>
                <select value={comboDim1} onChange={(e) => setComboDim1(e.target.value)}>
                  <option value="cost_center">Cost Center</option>
                  <option value="department">Department</option>
                  <option value="project">Project</option>
                  <option value="branch">Branch</option>
                  {accountingDims.length > 0 && <option disabled>──── Custom ────</option>}
                  {accountingDims.map((dim) => (
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
                  {accountingDims.length > 0 && <option disabled>──── Custom ────</option>}
                  {accountingDims.map((dim) => (
                    <option key={dim.fieldname} value={dim.fieldname}>{dim.label}</option>
                  ))}
                </select>
              </label>
            </>
          )}
          {viewMode === 'dimension' && (
            <label><span className="flbl">Pivot by</span>
              <select value={pivotBy} onChange={(e) => setPivotBy(e.target.value)}>
                <option value="cost_center">Cost Center</option>
                <option value="department">Department</option>
                <option value="project">Project</option>
                <option value="branch">Branch</option>
                {accountingDims.length > 0 && <option disabled>──── Custom dimensions ────</option>}
                {accountingDims.map((dim) => (
                  <option key={dim.fieldname} value={dim.fieldname}>{dim.label}</option>
                ))}
              </select>
            </label>
          )}
          <label><span className="flbl">Prior date (compare)</span>
            <input type="date" value={priorAsOfDate} onChange={(e) => setPriorAsOfDate(e.target.value)} disabled={!showPrior} />
          </label>
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
          {filterOptions && filterOptions.finance_books.length > 0 && (
            <label><span className="flbl">Finance book</span>
              <select value={financeBook} onChange={(e) => setFinanceBook(e.target.value)}>
                <option value="">— All finance books —</option>
                {filterOptions.finance_books.map((fb) => (
                  <option key={fb} value={fb}>{fb}</option>
                ))}
              </select>
            </label>
          )}
          <label><span className="flbl">Currency</span>
            <select value={presentationCurrency} onChange={(e) => setPresentationCurrency(e.target.value)}>
              <option value="">
                {filterOptions?.company_currency
                  ? `${filterOptions.company_currency} (company)`
                  : '— Company currency —'}
              </option>
              {filterOptions?.currencies
                .filter((c) => c.name !== filterOptions.company_currency)
                .map((c) => (
                  <option key={c.name} value={c.name}>{c.name} — {c.currency_name}</option>
                ))}
            </select>
          </label>
          {/* v1.9.61 — Custom accounting dimensions, multi-select picker. */}
          {filterOptions?.dimensions.map((dim: any) => {
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
            <input
              type="text"
              placeholder="Code or name…"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </label>
          <label><span className="flbl">Decimals</span>
            <select value={decimals} onChange={(e) => setDecimals(parseInt(e.target.value))}>
              {[0, 1, 2, 3, 4].map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
        </div>
        <div className="derived-row">
          <label className="chk"><input type="checkbox" checked={showPrior} onChange={(e) => setShowPrior(e.target.checked)} /> Show prior period column</label>
          <label className="chk"><input type="checkbox" checked={showGroupAccounts} onChange={(e) => setShowGroupAccounts(e.target.checked)} /> Show group accounts</label>
          <label className="chk"><input type="checkbox" checked={showZeroValues} onChange={(e) => setShowZeroValues(e.target.checked)} /> Show zero values</label>
          {viewMode === 'dimension' && (
            <label className="chk"><input type="checkbox" checked={hideEmptyDims} onChange={(e) => setHideEmptyDims(e.target.checked)} /> Hide empty dimensions</label>
          )}
          <label className="chk"><input type="checkbox" checked={showUnclosedPl} onChange={(e) => setShowUnclosedPl(e.target.checked)} /> Show unclosed P&amp;L in equity</label>
          {result?.result && (result.result as any).currency?.rate_missing && (
            <span style={{ fontSize: 11, color: '#854f0b' }}>
              ⚠ No exchange rate found — showing unconverted figures.
            </span>
          )}
          {result?.result && (result.result as any).currency && (result.result as any).currency.conversion_rate !== 1 && (
            <span style={{ fontSize: 11, color: 'var(--info, #0c447c)' }}>
              Converted to {(result.result as any).currency.presentation_currency} @ {(result.result as any).currency.conversion_rate}
            </span>
          )}
        </div>
        <div className="action-row">
          <button onClick={run} disabled={loading}>
            <i className="ti ti-refresh" aria-hidden /> {loading ? 'Running…' : 'Run'}
          </button>
          {viewMode === 'period' ? (
            <ExportBar
              company={company}
              companyLabel={company}
              disabled={!result}
              getDoc={() => (result ? buildBsDoc(result, showPrior, decimals) : null)}
            >
              <button onClick={() => result && copyBsToClipboard(result)}>
                <i className="ti ti-clipboard" aria-hidden /> Copy
              </button>
            </ExportBar>
          ) : (
            <>
              <button onClick={() => {
                if (viewMode === 'dimension' && effectivePivot) exportDimensionPivotXlsx(effectivePivot.result as any, 'balance_sheet_by_dimension.xlsx', decimals, 'balancesheet');
                else if (viewMode === 'combo' && comboResult) exportDimensionPivotXlsx(comboResult.result as any, 'balance_sheet_combo.xlsx', decimals, 'balancesheet');
              }}><i className="ti ti-file-spreadsheet" aria-hidden /> Excel</button>
              <button onClick={() => {
                if (viewMode === 'dimension' && effectivePivot) exportPivotCsv(effectivePivot.result as any, `balance-sheet-by-dimension-${asOfDate}.csv`, decimals);
                else if (viewMode === 'combo' && comboResult) exportPivotCsv(comboResult.result as any, `balance-sheet-combo-${asOfDate}.csv`, decimals);
              }}><i className="ti ti-file-export" aria-hidden /> CSV</button>
              <button onClick={() => {
                if (viewMode === 'dimension' && effectivePivot) printDimensionPivot(
                  'Balance Sheet — by ' + pivotLabel(pivotBy),
                  `${company} · as of ${asOfDate}`, effectivePivot.result as any, decimals, 'balancesheet');
                else if (viewMode === 'combo' && comboResult) printDimensionPivot(
                  `Balance Sheet — ${pivotLabel(comboDim1)} × ${pivotLabel(comboDim2)}`,
                  `${company} · as of ${asOfDate}`, comboResult.result as any, decimals, 'balancesheet');
              }}><i className="ti ti-printer" aria-hidden /> Print</button>
            </>
          )}
          <span style={{ flex: 1 }} />
          {result?.performance && <span className="run-meta">{result.performance.execution_ms}ms{result.performance.cache_hit ? ' · cached' : ''}</span>}
        </div>
        {error && <div className="run-error">{error}</div>}
      </div>

      {viewMode === 'period' && result && (
        <div className="matrix-wrap">
          <div className="matrix-scroll">
            <table className="pivot-matrix bs-table">
              <thead>
                <tr>
                  <th className="pivot-row-head">{t('Account')}</th>
                  <th>{result.filters.as_of_date}</th>
                  {showPrior && result.filters.prior_as_of_date && (
                    <th>{result.filters.prior_as_of_date}</th>
                  )}
                </tr>
              </thead>
              <tbody>
                {renderBsSection(decimals, 'Assets', 'Asset', rowsForRootType('Asset'), result, showPrior, collapsed, toggleGroup)}
                {renderBsSection(decimals, 'Liabilities', 'Liability', rowsForRootType('Liability'), result, showPrior, collapsed, toggleGroup)}
                {renderBsSection(decimals, 'Equity', 'Equity', rowsForRootType('Equity'), result, showPrior, collapsed, toggleGroup, result.result.sections.current_year_earnings)}

                <tr className="pivot-row-subtotal" style={{ background: 'var(--total-bg, #e4e9f0)' }}>
                  <td className="pivot-row-label" style={{ fontWeight: 600 }}>Total Liabilities + Equity</td>
                  <td>{fmtD(result.result.sections.lia_plus_eq.current, decimals)}</td>
                  {showPrior && (
                    <td>{fmtD(result.result.sections.lia_plus_eq.prior || 0, decimals)}</td>
                  )}
                </tr>
                <tr style={{ background: Math.abs(result.result.sections.diff.current) > 0.01 ? '#faeeda' : 'transparent' }}>
                  <td className="pivot-row-label">
                    Difference (Assets - Liab&amp;Eq)
                    {Math.abs(result.result.sections.diff.current) > 0.01 && (
                      <span style={{ marginLeft: 8, fontSize: 10, color: '#854f0b' }}>
                        ⚠ Balance sheet doesn't balance
                      </span>
                    )}
                  </td>
                  <td>{fmtD(result.result.sections.diff.current, decimals)}</td>
                  {showPrior && <td>{fmtD(result.result.sections.diff.prior || 0, decimals)}</td>}
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Dimension view — one balance column per dimension value. */}
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
                {activeVisible.length === 0 && (
                  <tr><td colSpan={activePivot.result.dimensions.length + 2} className="muted" style={{ fontStyle: 'italic', padding: 16 }}>
                    No accounts to show. {activePivot.result.dimensions.length === 0 ? 'No GL activity carries this dimension as of the selected date.' : ''}
                  </td></tr>
                )}
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
                      <td className={'pivot-col-total ' + (a.total < 0 ? 'neg' : a.total === 0 ? 'zero' : '')}>
                        {a.total === 0 ? '-' : fmtD(a.total, decimals)}
                      </td>
                      {activePivot.result.dimensions.map((d: any) => {
                        const v = a.by_dim[d.name] || 0;
                        return <td key={d.name} className={v < 0 ? 'neg' : v === 0 ? 'zero' : ''}>
                          {v === 0 ? '-' : fmtD(v, decimals)}
                        </td>;
                      })}
                    </tr>
                  );
                })}
                {(() => {
                  // Balance Sheet totals are root-type-aware: a BS balances
                  // when Total Assets == Total Liabilities + Equity. Showing a
                  // single grand total would be meaningless.
                  const leaves = activePivot.result.accounts.filter((a: any) => !a.is_group);
                  const isAsset = (a: any) => (a.root_type || '') === 'Asset';
                  const isLiabEq = (a: any) => ['Liability', 'Equity'].includes(a.root_type || '');
                  const dims = activePivot.result.dimensions;
                  const sum = (rows: any[], key?: string) =>
                    rows.reduce((s: number, a: any) => s + (key ? (a.by_dim?.[key] || 0) : (a.total || 0)), 0);
                  const assets = leaves.filter(isAsset);
                  const liabEq = leaves.filter(isLiabEq);

                  const totalsRow = (label: string, rows: any[], strong: boolean) => (
                    <tr className="pivot-row-subtotal" style={strong ? { borderTop: '2px solid var(--border-strong)' } : undefined}>
                      <td className="pivot-row-label" style={{ fontWeight: 700 }}>{label}</td>
                      <td className="pivot-col-total">{fmtD(sum(rows), decimals)}</td>
                      {dims.map((d: any) => {
                        const v = sum(rows, d.name);
                        return <td key={d.name} className={v < 0 ? 'neg' : v === 0 ? 'zero' : ''}>
                          {v === 0 ? '-' : fmtD(v, decimals)}
                        </td>;
                      })}
                    </tr>
                  );

                  // Difference per column — Assets minus Liab+Equity.
                  const diffTotal = sum(assets) - sum(liabEq);
                  const anyOff = Math.abs(diffTotal) > 0.005
                    || dims.some((d: any) => Math.abs(sum(assets, d.name) - sum(liabEq, d.name)) > 0.005);

                  return (
                    <>
                      {totalsRow('Total Assets', assets, true)}
                      {totalsRow('Total Liabilities + Equity', liabEq, false)}
                      {anyOff && (
                        <tr style={{ background: 'var(--neg-bg, #fbe9e9)' }}>
                          <td className="pivot-row-label" style={{ color: 'var(--neg, #a02323)', fontWeight: 700 }}>
                            Difference
                          </td>
                          <td className="pivot-col-total" style={{ color: 'var(--neg, #a02323)', fontWeight: 700 }}>
                            {fmtD(diffTotal, decimals)}
                          </td>
                          {dims.map((d: any) => {
                            const v = sum(assets, d.name) - sum(liabEq, d.name);
                            return <td key={d.name} style={Math.abs(v) > 0.005
                              ? { color: 'var(--neg, #a02323)', fontWeight: 700 }
                              : { color: 'var(--text-muted)' }}>
                              {Math.abs(v) > 0.005 ? fmtD(v, decimals) : '—'}
                            </td>;
                          })}
                        </tr>
                      )}
                    </>
                  );
                })()}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Combo now renders via the shared dimension tree above
          (same structure, composite dim1 × dim2 columns). */}
      {viewMode === 'combo' && !comboResult && !loading && comboDim1 !== comboDim2 && (
        <div className="empty-state">
          <div className="empty-state-icon"><i className="ti ti-table" aria-hidden /></div>
          <h3 className="empty-state-title">Combo view ready</h3>
          <p className="empty-state-body">Click Run to see the {comboDim1} × {comboDim2} breakdown.</p>
        </div>
      )}

      {/* v1.9.63 — Multi-period view */}
      {viewMode === 'multi_period' && mpResult && (
        <div className="matrix-wrap">
          <div className="matrix-scroll">
            <table className="matrix">
              <thead>
                <tr className="h1">
                  <th>{t('Account')}</th>
                  {mpResult.periods.map((p: any) => (
                    <th key={p.key} className="num" title={p.end_date}>{p.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {mpResult.accounts.map((a: any) => (
                  <tr key={a.name} className={a.is_group ? 'r-section' : ''}>
                    <td title={a.name}>{a.account_name}</td>
                    {mpResult.periods.map((p: any) => {
                      const v = a.balances[p.key] || 0;
                      return (
                        <td key={p.key} className={'num' + (v < 0 ? ' neg' : '')}>
                          {fmtD(v, decimals)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {viewMode === 'multi_period' && !mpResult && !loading && (
        <div className="empty-state">
          <div className="empty-state-icon"><i className="ti ti-calendar-stats" aria-hidden /></div>
          <h3 className="empty-state-title">Multi-period view ready</h3>
          <p className="empty-state-body">Click Run to see closing balances at each period boundary.</p>
        </div>
      )}
    </div>
  );
}

function renderBsSection(
  decimals: number,
  title: string,
  rootType: string,
  rows: any[],
  result: BalanceSheetResult,
  showPrior: boolean,
  collapsed: Set<string>,
  toggleGroup: (n: string) => void,
  cyEarnings?: { current: number; prior: number | null },
) {
  const section = (result.result.sections as any)[rootType.toLowerCase()] as { current: number; prior: number | null };
  return (
    <>
      <tr className="pivot-row-section"><td colSpan={showPrior ? 3 : 2}>{title}</td></tr>
      {rows.map((a) => {
        const padLeft = Math.max(0, a.depth - 1) * 14 + 8;
        const isCollapsed = collapsed.has(a.name);
        return (
          <tr key={a.name} className={a.is_group ? 'pivot-row-subtotal' : ''}>
            <td className="pivot-row-label" style={{ paddingLeft: padLeft }}>
              {a.is_group ? (
                <button
                  onClick={() => toggleGroup(a.name)}
                  style={{ background: 'transparent', border: 0, cursor: 'pointer', padding: 0, marginRight: 4, color: 'var(--text-muted)', fontSize: 11 }}
                >{isCollapsed ? '▶' : '▼'}</button>
              ) : null}
              <span style={{ fontWeight: a.is_group ? 500 : 400 }}>
                {a.code && <span className="muted" style={{ marginRight: 6 }}>{a.code}</span>}
                {a.label}
              </span>
            </td>
            <td className={a.current < 0 ? 'neg' : a.current === 0 ? 'zero' : ''}>{fmtD(a.current, decimals)}</td>
            {showPrior && a.prior != null && (
              <td className={a.prior < 0 ? 'neg' : a.prior === 0 ? 'zero' : ''}>{fmtD(a.prior, decimals)}</td>
            )}
            {showPrior && a.prior == null && <td className="zero">-</td>}
          </tr>
        );
      })}
      {rootType === 'Equity' && cyEarnings && Math.abs(cyEarnings.current) > 0.01 && (
        <tr style={{ fontStyle: 'italic' }}>
          <td className="pivot-row-label" style={{ paddingLeft: 22 }}>
            <span className="muted" title="Income - Expense from fiscal year start to as-of date. Included in Equity total above. Disappears once books are closed.">
              ↳ Current year earnings (unposted)
            </span>
          </td>
          <td>{fmtD(cyEarnings.current, decimals)}</td>
          {showPrior && <td>{cyEarnings.prior != null ? fmtD(cyEarnings.prior, decimals) : '-'}</td>}
        </tr>
      )}
      <tr className="pivot-row-subtotal" style={{ borderTop: '2px solid var(--border-strong)' }}>
        <td className="pivot-row-label" style={{ fontWeight: 600 }}>Total {title}</td>
        <td style={{ fontWeight: 600 }}>{fmtD(section.current, decimals)}</td>
        {showPrior && <td style={{ fontWeight: 600 }}>{fmtD(section.prior || 0, decimals)}</td>}
      </tr>
    </>
  );
}

function exportPivotCsv(pr: any, fileName: string, decimals: number) {
  // Works for both the dimension pivot and the combo pivot (same shape):
  // accounts[] with by_dim keyed by the dimension/combination columns.
  const dims: any[] = pr.dimensions || [];
  const headers = ['Code', 'Account', 'Total', ...dims.map((d) => d.label)];
  const esc = (s: any) => `"${String(s ?? '').replace(/"/g, '""')}"`;
  const num = (v: number) => (v == null ? '' : Number(v).toFixed(decimals));
  const lines = [headers.map(esc).join(',')];
  for (const a of pr.accounts || []) {
    const indent = '  '.repeat(Math.max(0, (a.depth || 1) - 1));
    const row = [esc(a.code || ''), esc(indent + (a.label || '')), num(a.total)];
    for (const d of dims) row.push(num(a.by_dim?.[d.name] || 0));
    lines.push(row.join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const el = document.createElement('a');
  el.href = url; el.download = fileName;
  el.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportBsCsv(r: BalanceSheetResult) {
  const hasPrior = !!r.filters.prior_as_of_date;
  const headers = ['Account', 'Code', r.filters.as_of_date];
  if (hasPrior) headers.push(r.filters.prior_as_of_date as string);
  const lines = [headers.join(',')];
  for (const rt of ['Asset', 'Liability', 'Equity']) {
    lines.push(`"${rt === 'Asset' ? 'Assets' : rt === 'Liability' ? 'Liabilities' : 'Equity'}"${','.repeat(headers.length - 1)}`);
    for (const a of r.result.accounts.filter((x) => x.root_type === rt)) {
      const indent = '  '.repeat(Math.max(0, a.depth));
      const row = [`"${indent}${(a.label || '').replace(/"/g, '""')}"`, `"${a.code || ''}"`, String(a.current)];
      if (hasPrior) row.push(String(a.prior ?? 0));
      lines.push(row.join(','));
    }
    const sec = (r.result.sections as any)[rt.toLowerCase()] as { current: number; prior: number | null };
    const totRow = [`"Total ${rt === 'Liability' ? 'Liabilities' : rt + 's'}"`, '', String(sec.current)];
    if (hasPrior) totRow.push(String(sec.prior ?? 0));
    lines.push(totRow.join(','));
  }
  const leRow = ['"Total Liabilities + Equity"', '', String(r.result.sections.lia_plus_eq.current)];
  if (hasPrior) leRow.push(String(r.result.sections.lia_plus_eq.prior ?? 0));
  lines.push(leRow.join(','));
  const diffRow = ['"Difference"', '', String(r.result.sections.diff.current)];
  if (hasPrior) diffRow.push(String(r.result.sections.diff.prior ?? 0));
  lines.push(diffRow.join(','));
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `balance-sheet-${r.filters.as_of_date}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function copyBsToClipboard(r: BalanceSheetResult) {
  const hasPrior = !!r.filters.prior_as_of_date;
  const headers = ['Account', r.filters.as_of_date];
  if (hasPrior) headers.push(r.filters.prior_as_of_date as string);
  const lines = [headers.join('\t')];
  for (const rt of ['Asset', 'Liability', 'Equity']) {
    lines.push(rt === 'Asset' ? 'Assets' : rt === 'Liability' ? 'Liabilities' : 'Equity');
    for (const a of r.result.accounts.filter((x) => x.root_type === rt)) {
      const indent = '  '.repeat(Math.max(0, a.depth));
      const row = [`${indent}${a.label}`, String(a.current)];
      if (hasPrior) row.push(String(a.prior ?? 0));
      lines.push(row.join('\t'));
    }
  }
  navigator.clipboard.writeText(lines.join('\n'));
}

/** v2.55.0 — the balance sheet as a portable document. Supersedes printBs(),
 *  whose hand-built markup emitted the literal string `{t('Account')}` as a
 *  column heading and carried no borders into print. */
function buildBsDoc(r: any, hasPrior: boolean, decimals: number): ReportDoc {
  const cur = r.result.currency;
  const note = cur && cur.conversion_rate !== 1
    ? `Converted to ${cur.presentation_currency} @ ${cur.conversion_rate}`
    : undefined;
  const rows: DocRow[] = [];
  const span = hasPrior ? 3 : 2;

  // v stays raw so Excel can compute; text carries the formatting the screen
  // shows, for Print, PDF, CSV and PNG.
  const cell = (v: any) => ({ v: Number(v || 0), text: fmtD(Number(v || 0), decimals), num: true as const });
  const money = (a: any) => hasPrior ? [cell(a.current), cell(a.prior)] : [cell(a.current)];

  const section = (title: string, rootType: string, key: string) => {
    rows.push({ kind: 'sec', cells: [{ v: title, colSpan: span, bold: true }] });
    for (const a of r.result.accounts.filter((x: any) => x.root_type === rootType)) {
      rows.push({
        kind: a.is_group ? 'grp' : 'normal',
        cells: [
          { v: (a.code ? a.code + '  ' : '') + a.label, indent: Math.max(0, a.depth), bold: !!a.is_group },
          ...money(a).map((c) => ({ ...c, bold: !!a.is_group })),
        ],
      });
    }
    const sec = (r.result.sections as any)[key];
    rows.push({ kind: 'tot', cells: [{ v: `Total ${title}`, bold: true }, ...money(sec).map((c) => ({ ...c, bold: true }))] });
  };

  section('Assets', 'Asset', 'asset');
  section('Liabilities', 'Liability', 'liability');
  section('Equity', 'Equity', 'equity');

  const le = r.result.sections.lia_plus_eq;
  rows.push({ kind: 'grand', cells: [{ v: 'Total Liabilities + Equity', bold: true }, ...money(le).map((c) => ({ ...c, bold: true }))] });
  const diff = r.result.sections.diff;
  rows.push({ kind: 'tot', cells: [{ v: 'Difference (Assets − Liab & Eq)', bold: true }, ...money(diff).map((c) => ({ ...c, bold: true }))] });

  const columns = [
    { label: 'Account', width: 46 },
    { label: String(r.filters.as_of_date), num: true, width: 20 },
  ];
  if (hasPrior) columns.push({ label: String(r.filters.prior_as_of_date), num: true, width: 20 });

  return {
    title: 'Balance Sheet',
    company: r.filters.company,
    companyLabel: r.filters.company,
    period: `As of ${r.filters.as_of_date}`
      + (hasPrior ? ` (compared with ${r.filters.prior_as_of_date})` : ''),
    columns,
    rows,
    fileBase: 'balance_sheet',
    orientation: 'portrait',
    note,
  };
}
