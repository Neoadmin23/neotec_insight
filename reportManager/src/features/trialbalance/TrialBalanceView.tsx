import { useEffect, useMemo, useState } from 'react';
import { t, arName, loadArabicLabels } from '../../utils/i18n';
import { ArName } from '../../components/ArName';
import type { ReportSummary, TrialBalanceResult, TrialBalanceParty, BalancePivotResult } from '../../types';
import { api } from '../../utils/api';
import { fmtD, dropEmptyDimensions } from '../../utils/format';
import { exportDimensionPivotXlsx, printDimensionPivot } from '../../utils/export';
import { ExportBar } from '../ExportBar';
import { setActiveCompany } from '../../utils/activeCompany';
import type { ReportDoc, DocRow } from '../../utils/reportdoc';
import { useDimFilters } from '../../utils/dimFilters';
import { DimensionMultiSelect } from '../DimensionMultiSelect';
import { ComboView } from '../ComboView';

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

const ROOT_TYPES = ['Asset', 'Liability', 'Equity', 'Income', 'Expense'];

export function TrialBalanceView({ reports, selectedReport, setSelectedReport, companies, costCenters, projects, departments, branches, fiscalYears }: Props) {
  // v1.9.57 — read the global dimension catalogue so the Pivot by dropdown
  // can offer custom Accounting Dimensions in addition to the four natives.
  // The Run tab is the writer for this context; TB consumes read-only.
  const { dimensions: accountingDims } = useDimFilters();
  const today = new Date().toISOString().slice(0, 10);
  const yearStart = today.slice(0, 4) + '-01-01';
  const [company, setCompany] = useState('');
  useEffect(() => { setActiveCompany(company); }, [company]);
  const [fy, setFy] = useState<string | number>(2026);
  const [asOfDate, setAsOfDate] = useState(today);
  // v1.9.16 — explicit period start. Opening = balance before this date,
  // Period = fromDate→asOfDate, Closing = as of asOfDate.
  const [fromDate, setFromDate] = useState(yearStart);
  // v1.9.58 — native dimension filters are arrays (multi-select).
  const [costCenter, setCostCenter] = useState<string[]>([]);
  const [project, setProject] = useState<string[]>([]);
  const [department, setDepartment] = useState<string[]>([]);
  const [branch, setBranch] = useState<string[]>([]);
  const [rootTypes, setRootTypes] = useState<string[]>([...ROOT_TYPES]);
  // v1.9 filters
  const [financeBook, setFinanceBook] = useState('');
  const [presentationCurrency, setPresentationCurrency] = useState('');
  const [showGroupAccounts, setShowGroupAccounts] = useState(true);
  const [showZeroValues, setShowZeroValues] = useState(false);
  // v1.9.61 — dimFilters values widened to string | string[] for multi-
  // select consistency with native dims. Backend (_sanitise_dimension_filters)
  // already accepts both shapes since v1.9.58.
  const [dimFilters, setDimFilters] = useState<Record<string, string | string[]>>({});
  const [filterOptions, setFilterOptions] = useState<{
    dimensions: { label: string; fieldname: string; options: string[] }[];
    finance_books: string[];
    currencies: { name: string; currency_name: string; symbol: string }[];
    company_currency: string;
  } | null>(null);
  // Account-tree search box.
  const [searchTerm, setSearchTerm] = useState('');
  // Decimal precision — default from System Settings, user can override.
  const [decimals, setDecimals] = useState(0);
  // View-by toggle (v1.9.3): 'period' = the six-column TB; 'dimension' =
  // one closing-balance column per cost center / department / project.
  // v1.9.57 — pivotBy widened from a closed union to string so custom
  // Accounting Dimensions configured on the bench can also be selected.
  // The backend's _validate_pivot_by sanitises against the dimension
  // whitelist; an invalid value throws cleanly, so the loose frontend
  // type doesn't compromise safety.
  // v1.9.63 — view modes extended:
  //   'period'       — existing six-column (opening/period/closing) display
  //   'dimension'    — pivot by one dimension (v1.9.57)
  //   'combo'        — two-dim tuple breakdown (v1.9.63)
  //   'multi_period' — closing balance per period boundary (v1.9.63)
  const [viewMode, setViewMode] = useState<'period' | 'dimension' | 'combo' | 'multi_period'>('period');
  // Combo state
  const [comboDim1, setComboDim1] = useState<string>('cost_center');
  const [comboDim2, setComboDim2] = useState<string>('project');
  const [comboResult, setComboResult] = useState<any | null>(null);
  // Multi-period state
  const [mpGranularity, setMpGranularity] = useState<'month' | 'quarter' | 'half' | 'year'>('quarter');
  const [mpResult, setMpResult] = useState<any | null>(null);
  const [pivotBy, setPivotBy] = useState<string>('cost_center');
  const [pivotResult, setPivotResult] = useState<BalancePivotResult | null>(null);
  // v1.9.12 — hide dimension columns that are empty for every account.
  const [hideEmptyDims, setHideEmptyDims] = useState(true);
  const [result, setResult] = useState<TrialBalanceResult | null>(null);
  const [, setAccTick] = useState(0);
  useEffect(() => {
    const accs: any[] = (result as any)?.result?.accounts || [];
    const names = accs.map((a) => a.name).filter(Boolean);
    if (names.length) loadArabicLabels('Account', names).then(() => setAccTick((x) => x + 1)).catch(() => {});
  }, [result]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // v2.21 — on-screen Default Row Expand, consistent across reports.
  const [rowExpand, setRowExpand] = useState<'expanded' | 'collapsed'>('expanded');
  const [partyData, setPartyData] = useState<Record<string, TrialBalanceParty[]>>({});
  const [partyOpen, setPartyOpen] = useState<Set<string>>(new Set());
  const [partyLoading, setPartyLoading] = useState<Set<string>>(new Set());

  // Load filter options (dimensions, finance books, currencies) when company
  // changes — these are bench-specific and discovered at runtime.
  useEffect(() => {
    if (!company) return;
    api.listReportFilterOptions(company)
      .then((o: any) => {
        setFilterOptions(o);
        if (typeof o?.float_precision === 'number') setDecimals(o.float_precision);
      })
      .catch(() => setFilterOptions(null));
  }, [company]);

  // Default company from list.
  useEffect(() => {
    if (!company && companies.length > 0) setCompany(companies[0].name);
  }, [companies, company]);

  // Default FY from list.
  useEffect(() => {
    if (Array.isArray(fiscalYears) && fiscalYears.length > 0) {
      const cur = fiscalYears.find((f) => f.is_current) || fiscalYears[0];
      if (cur) setFy(cur.name || cur.year_int || 2026);
    }
  }, [fiscalYears]);

  async function run() {
    if (!company || !asOfDate) return;
    setLoading(true);
    setError('');
    setPartyData({});
    setPartyOpen(new Set());
    try {
      if (viewMode === 'dimension') {
        const r = (await api.runTrialBalancePivot({
          report: selectedReport,
          company,
          fiscal_year: fy,
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
          const r = await api.runComboReport({
            report: selectedReport,
            dim1: comboDim1,
            dim2: comboDim2,
            company,
            as_of_date: asOfDate,
            cost_center: costCenter.length ? costCenter : null,
            project: project.length ? project : null,
            department: department.length ? department : null,
            branch: branch.length ? branch : null,
            dimension_filters: Object.keys(dimFilters).length ? dimFilters : undefined,
          });
          setComboResult(r);
          setResult(null);
          setPivotResult(null);
          setMpResult(null);
        }
      } else if (viewMode === 'multi_period') {
        const r = await api.runTrialBalanceMultiPeriod({
          report: selectedReport,
          company,
          fiscal_year: fy,
          granularity: mpGranularity,
          cost_center: costCenter.length ? costCenter : null,
          project: project.length ? project : null,
          department: department.length ? department : null,
          branch: branch.length ? branch : null,
          root_types: rootTypes.length === ROOT_TYPES.length ? undefined : rootTypes,
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
        const r = (await api.runTrialBalance({
          report: selectedReport,
          company,
          fiscal_year: fy,
          as_of_date: asOfDate,
          from_date: fromDate || null,
          cost_center: costCenter.length ? costCenter : null,
          project: project.length ? project : null,
          department: department.length ? department : null,
          branch: branch.length ? branch : null,
          root_types: rootTypes.length === ROOT_TYPES.length ? undefined : rootTypes,
          finance_book: financeBook || null,
          dimension_filters: Object.keys(dimFilters).length ? dimFilters : undefined,
          show_group_accounts: showGroupAccounts ? 1 : 0,
          show_zero_values: showZeroValues ? 1 : 0,
          presentation_currency: presentationCurrency || null,
        })) as TrialBalanceResult;
        setResult(r);
        setPivotResult(null);
        setComboResult(null);
        setMpResult(null);
      }
    } catch (e: any) {
      setError(e?.message || 'Trial balance failed.');
      setResult(null);
      setPivotResult(null);
    } finally {
      setLoading(false);
    }
  }

  // Auto-run on key filter changes.
  useEffect(() => {
    if (!selectedReport || !company) return;
    const t = setTimeout(() => { run(); }, 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    selectedReport, company, fy, asOfDate, fromDate, costCenter, project, department, branch,
    rootTypes.join(','), financeBook, presentationCurrency,
    viewMode, pivotBy,
    showGroupAccounts, showZeroValues, JSON.stringify(dimFilters),
  ]);

  async function loadParties(account: string) {
    if (partyOpen.has(account)) {
      // Collapse
      const next = new Set(partyOpen); next.delete(account); setPartyOpen(next);
      return;
    }
    if (partyData[account]) {
      const next = new Set(partyOpen); next.add(account); setPartyOpen(next);
      return;
    }
    const ld = new Set(partyLoading); ld.add(account); setPartyLoading(ld);
    try {
      const r = await api.runTrialBalanceParties({
        report: selectedReport, account, company,
        fiscal_year: fy, as_of_date: asOfDate,
        cost_center: costCenter.length ? costCenter : null,
        presentation_currency: presentationCurrency || null,
      });
      setPartyData((d) => ({ ...d, [account]: r.result.parties || [] }));
      setPartyOpen((s) => { const n = new Set(s); n.add(account); return n; });
    } catch (e: any) {
      setError(`Failed to load parties for ${account}: ${e?.message || ''}`);
    } finally {
      setPartyLoading((s) => { const n = new Set(s); n.delete(account); return n; });
    }
  }

  function toggleGroup(name: string) {
    setCollapsed((s) => {
      const n = new Set(s);
      if (n.has(name)) n.delete(name); else n.add(name);
      return n;
    });
  }

  // Compute which rows are visible (respecting collapsed groups + search).
  const visibleAccounts = useMemo(() => {
    if (!result) return [];
    const accs = result.result.accounts;
    const accByName = new Map(accs.map((a) => [a.name, a]));

    // Search: when a term is entered, show only accounts whose code or label
    // matches, plus their ancestor chain (so the tree context is preserved).
    let searchMatchNames: Set<string> | null = null;
    if (searchTerm.trim()) {
      const term = searchTerm.trim().toLowerCase();
      searchMatchNames = new Set();
      for (const a of accs) {
        const hit = (a.code || '').toLowerCase().includes(term) ||
                    (a.label || '').toLowerCase().includes(term);
        if (hit) {
          searchMatchNames.add(a.name);
          // Walk up and include ancestors.
          let p = a.parent;
          while (p) {
            searchMatchNames.add(p);
            const pa = accByName.get(p);
            if (!pa) break;
            p = pa.parent;
          }
        }
      }
    }

    const out = [];
    for (const a of accs) {
      if (searchMatchNames && !searchMatchNames.has(a.name)) continue;
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

  // Pivot result with empty dimension columns optionally dropped. Everything
  // downstream — the table, the Excel/Print export — uses this, so screen and
  // file always agree.
  const effectivePivot = useMemo(() => {
    if (!pivotResult) return null;
    if (!hideEmptyDims) return pivotResult;
    return { ...pivotResult, result: dropEmptyDimensions(pivotResult.result) };
  }, [pivotResult, hideEmptyDims]);

  // Same visibility logic for the dimension-pivot account list.
  const visiblePivotAccounts = useMemo(() => {
    if (!effectivePivot) return [];
    const accs = effectivePivot.result.accounts;
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
  }, [effectivePivot, collapsed, searchTerm]);

  useEffect(() => {
    const accs = result?.result?.accounts || effectivePivot?.result?.accounts || comboResult?.result?.accounts || [];
    if (!accs.length) return;
    if (rowExpand === 'collapsed') setCollapsed(new Set(accs.filter((a: any) => a.is_group).map((a: any) => a.name)));
    else setCollapsed(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowExpand, result, effectivePivot, comboResult]);

  // v1.9.57 — resolve a human label for the active pivot dimension. Handles
  // the four natives plus any custom Accounting Dimension by falling back
  // to the dimension's `label` from the discovery payload.
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
      {/* v1.9.64 — View sub-tabs (consistent with RunTab pattern). */}
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
          <label><span className="flbl">Fiscal year</span>
            <select value={String(fy)} onChange={(e) => setFy(e.target.value)}>
              {fiscalYears.length === 0 && (
                <>
                  {[2024, 2025, 2026].map((y) => <option key={y} value={y}>FY{y}</option>)}
                </>
              )}
              {fiscalYears.map((f: any) => (
                <option key={f.name} value={f.name}>{f.name}</option>
              ))}
            </select>
          </label>
          <label><span className="flbl">From date</span>
            <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
          </label>
          <label><span className="flbl">To date</span>
            <input type="date" value={asOfDate} onChange={(e) => setAsOfDate(e.target.value)} />
          </label>
          <label><span className="flbl">{t('Default Row Expand')}</span>
            <select value={rowExpand} onChange={(e) => setRowExpand(e.target.value as 'expanded' | 'collapsed')}>
              <option value="expanded">{t('Expanded')}</option>
              <option value="collapsed">{t('Collapsed')}</option>
            </select>
          </label>
          {viewMode === 'multi_period' && (
            <label><span className="flbl">Granularity</span>
              <select value={mpGranularity} onChange={(e) => setMpGranularity(e.target.value as any)}>
                <option value="month">Month (12 cols)</option>
                <option value="quarter">Quarter (4 cols)</option>
                <option value="half">Half-year (2 cols)</option>
                <option value="year">Year (1 col)</option>
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
          <label><span className="flbl">Cost center {costCenters.length > 0 && <span className="muted" style={{ fontSize: 9 }}>({costCenters.length})</span>}</span>
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
          {/* v1.9.61 — Custom accounting dimensions, multi-select picker
            * for consistency with native dim filters above. */}
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
        <div className="chip-strip" style={{ marginTop: 10 }}>
          <span className="chip-strip-label">Root types</span>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {ROOT_TYPES.map((rt) => (
              <div
                key={rt}
                className={'pivot-chip' + (rootTypes.includes(rt) ? ' on' : '')}
                onClick={() => {
                  setRootTypes((cur) => cur.includes(rt) ? cur.filter((x) => x !== rt) : [...cur, rt]);
                }}
              >{rt}</div>
            ))}
          </div>
        </div>
        <div className="derived-row">
          <label className="chk"><input type="checkbox" checked={showGroupAccounts} onChange={(e) => setShowGroupAccounts(e.target.checked)} /> Show group accounts</label>
          <label className="chk"><input type="checkbox" checked={showZeroValues} onChange={(e) => setShowZeroValues(e.target.checked)} /> Show zero values</label>
          {viewMode === 'dimension' && (
            <label className="chk"><input type="checkbox" checked={hideEmptyDims} onChange={(e) => setHideEmptyDims(e.target.checked)} /> Hide empty dimensions</label>
          )}
          {result?.result?.currency?.rate_missing && (
            <span style={{ fontSize: 11, color: '#854f0b' }}>
              ⚠ No exchange rate found for {result.result.currency.presentation_currency} on {result.result.currency.as_of_date} — showing unconverted figures.
            </span>
          )}
          {result?.result?.currency && result.result.currency.conversion_rate !== 1 && (
            <span style={{ fontSize: 11, color: 'var(--info, #0c447c)' }}>
              Converted to {result.result.currency.presentation_currency} @ {result.result.currency.conversion_rate} (rate as of {result.result.currency.as_of_date})
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
              getDoc={() => (result ? buildTbDoc(result, partyData, partyOpen, decimals) : null)}
            >
              <button onClick={() => copyTbToClipboard(result, partyData, partyOpen)}>
                <i className="ti ti-clipboard" aria-hidden /> Copy
              </button>
            </ExportBar>
          ) : (
            <>
              <button onClick={() => {
                if (effectivePivot) exportDimensionPivotXlsx(effectivePivot.result as any, 'trial_balance_by_dimension.xlsx', decimals);
              }}><i className="ti ti-file-spreadsheet" aria-hidden /> Excel</button>
              <button onClick={() => {
                if (effectivePivot) printDimensionPivot(
                  'Trial Balance — by ' + pivotLabel(pivotBy),
                  `${company} · as of ${asOfDate}`, effectivePivot.result as any, decimals);
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
            <table className="pivot-matrix tb-table">
              <thead>
                <tr>
                  <th className="pivot-row-head">{t('Account')}</th>
                  <th colSpan={2}>{t('Opening')}</th>
                  <th colSpan={2}>{t('Period')}</th>
                  <th colSpan={2}>{t('Closing')}</th>
                </tr>
                <tr className="h2">
                  <th />
                  <th>{t('Debit')}</th><th>{t('Credit')}</th>
                  <th>{t('Debit')}</th><th>{t('Credit')}</th>
                  <th>{t('Debit')}</th><th>{t('Credit')}</th>
                </tr>
              </thead>
              <tbody>
                {visibleAccounts.map((a) => {
                  const isCollapsed = collapsed.has(a.name);
                  const padLeft = Math.max(0, a.depth - 1) * 14 + 8;
                  const isPartyOpen = partyOpen.has(a.name);
                  const isPartyLoading = partyLoading.has(a.name);
                  return (
                    <>
                      <tr key={a.name} className={a.is_group ? 'pivot-row-subtotal' : ''}>
                        <td className="pivot-row-label" style={{ paddingLeft: padLeft }}>
                          {a.is_group ? (
                            <button
                              onClick={() => toggleGroup(a.name)}
                              style={{
                                background: 'transparent', border: 0, cursor: 'pointer',
                                padding: 0, marginRight: 4, color: 'var(--text-muted)',
                                fontSize: 11,
                              }}
                              aria-label={isCollapsed ? 'Expand' : 'Collapse'}
                            >{isCollapsed ? '▶' : '▼'}</button>
                          ) : null}
                          {a.has_parties && (
                            <button
                              onClick={() => loadParties(a.name)}
                              style={{
                                background: 'transparent', border: 0, cursor: 'pointer',
                                padding: '0 4px', marginRight: 4,
                                color: 'var(--info, #0c447c)', fontWeight: 600,
                              }}
                              aria-label={isPartyOpen ? 'Hide parties' : 'Show parties'}
                              title={a.account_type === 'Receivable' ? 'Drill into customers' : 'Drill into suppliers'}
                            >{isPartyLoading ? '⋯' : (isPartyOpen ? '−' : '+')}</button>
                          )}
                          <span style={{ fontWeight: a.is_group ? 500 : 400 }}>
                            {a.code ? <span className="muted" style={{ marginRight: 6 }}>{a.code}</span> : null}
                            <ArName name={a.name} fallback={a.label} source="Account" />
                          </span>
                        </td>
                        {renderTbCells(a, decimals)}
                      </tr>
                      {a.has_parties && isPartyOpen && partyData[a.name] && partyData[a.name].map((p) => (
                        <tr key={a.name + '|' + p.party_type + '|' + p.party} className="tb-party-row">
                          <td className="pivot-row-label" style={{ paddingLeft: padLeft + 24, fontSize: 11 }}>
                            <span className="muted" style={{ marginRight: 6 }}>{p.party_type}</span>
                            {p.party_name}
                          </td>
                          {renderTbCells(p, decimals)}
                        </tr>
                      ))}
                      {a.has_parties && isPartyOpen && partyData[a.name] && partyData[a.name].length === 0 && (
                        <tr><td colSpan={7} className="muted" style={{ paddingLeft: padLeft + 24, fontStyle: 'italic' }}>No parties with activity in this period.</td></tr>
                      )}
                    </>
                  );
                })}
                <tr className="pivot-row-subtotal" style={{ borderTop: '2px solid var(--border-strong)' }}>
                  <td className="pivot-row-label">{t('Total')}</td>
                  <td>{fmtD(result.result.totals.opening_debit, decimals)}</td>
                  <td>{fmtD(result.result.totals.opening_credit, decimals)}</td>
                  <td>{fmtD(result.result.totals.period_debit, decimals)}</td>
                  <td>{fmtD(result.result.totals.period_credit, decimals)}</td>
                  <td>{fmtD(result.result.totals.closing_debit, decimals)}</td>
                  <td>{fmtD(result.result.totals.closing_credit, decimals)}</td>
                </tr>
                {(() => {
                  // TB balance check — each section's Debit must equal its
                  // Credit. Show a bold red Difference row only on mismatch.
                  const t = result.result.totals;
                  const od = (t.opening_debit || 0) - (t.opening_credit || 0);
                  const pd = (t.period_debit || 0) - (t.period_credit || 0);
                  const cd = (t.closing_debit || 0) - (t.closing_credit || 0);
                  const off = (v: number) => Math.abs(v) > 0.005;
                  if (!off(od) && !off(pd) && !off(cd)) return null;
                  const diffCell = (v: number) => off(v)
                    ? <td style={{ color: 'var(--neg, #a02323)', fontWeight: 700 }}>{fmtD(v, decimals)}</td>
                    : <td style={{ color: 'var(--text-muted)' }}>—</td>;
                  return (
                    <tr style={{ background: 'var(--neg-bg, #fbe9e9)' }}>
                      <td className="pivot-row-label" style={{ color: 'var(--neg, #a02323)', fontWeight: 700 }}>
                        TB Difference
                      </td>
                      {diffCell(od)}<td />
                      {diffCell(pd)}<td />
                      {diffCell(cd)}<td />
                    </tr>
                  );
                })()}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Dimension view — one closing-balance column per dimension value. */}
      {viewMode === 'dimension' && effectivePivot && (
        <div className="matrix-wrap">
          <div className="matrix-scroll">
            <table className="pivot-matrix">
              <thead>
                <tr>
                  <th className="pivot-row-head">{t('Account')}</th>
                  <th className="pivot-col-total">Total Closing</th>
                  {effectivePivot.result.dimensions.map((d) => <th key={d.name}>{d.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {visiblePivotAccounts.length === 0 && (
                  <tr><td colSpan={effectivePivot.result.dimensions.length + 2} className="muted" style={{ fontStyle: 'italic', padding: 16 }}>
                    No accounts to show. {effectivePivot.result.dimensions.length === 0 ? 'No GL activity carries this dimension in the selected period.' : ''}
                  </td></tr>
                )}
                {visiblePivotAccounts.map((a) => {
                  const padLeft = Math.max(0, a.depth - 1) * 14 + 8;
                  const isCollapsed = collapsed.has(a.name);
                  return (
                    <tr key={a.name} className={a.is_group ? 'pivot-row-subtotal' : ''}>
                      <td className="pivot-row-label" style={{ paddingLeft: padLeft }}>
                        {a.is_group ? (
                          <button onClick={() => toggleGroup(a.name)}
                            style={{ background: 'transparent', border: 0, cursor: 'pointer', padding: 0, marginRight: 4, color: 'var(--text-muted)', fontSize: 11 }}
                            aria-label={isCollapsed ? 'Expand' : 'Collapse'}>
                            {isCollapsed ? '▶' : '▼'}
                          </button>
                        ) : null}
                        <span style={{ fontWeight: a.is_group ? 500 : 400 }}>
                          {a.code ? <span className="muted" style={{ marginRight: 6 }}>{a.code}</span> : null}
                          <ArName name={a.name} fallback={a.label} source="Account" />
                        </span>
                      </td>
                      <td className={'pivot-col-total ' + (a.total < 0 ? 'neg' : a.total === 0 ? 'zero' : '')}>
                        {a.total === 0 ? '-' : fmtD(a.total, decimals)}
                      </td>
                      {effectivePivot.result.dimensions.map((d) => {
                        const v = a.by_dim[d.name] || 0;
                        return <td key={d.name} className={v < 0 ? 'neg' : v === 0 ? 'zero' : ''}>
                          {v === 0 ? '-' : fmtD(v, decimals)}
                        </td>;
                      })}
                    </tr>
                  );
                })}
                {(() => {
                  const leaves = effectivePivot.result.accounts.filter((a: any) => !a.is_group);
                  const grand = leaves.reduce((s: number, a: any) => s + (a.total || 0), 0);
                  return (
                    <tr className="pivot-row-subtotal" style={{ borderTop: '2px solid var(--border-strong)' }}>
                      <td className="pivot-row-label">{t('Total')}</td>
                      <td className="pivot-col-total">{fmtD(grand, decimals)}</td>
                      {effectivePivot.result.dimensions.map((d: any) => {
                        const v = leaves.reduce((s: number, a: any) => s + (a.by_dim?.[d.name] || 0), 0);
                        return <td key={d.name} className={v < 0 ? 'neg' : v === 0 ? 'zero' : ''}>
                          {v === 0 ? '-' : fmtD(v, decimals)}
                        </td>;
                      })}
                    </tr>
                  );
                })()}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* v1.9.63 — Combo view */}
      {viewMode === 'combo' && comboResult && (
        <ComboView result={comboResult} decimals={decimals} dimensions={accountingDims} />
      )}
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
                    <td title={a.name}><ArName name={a.name} fallback={a.account_name} source="Account" /></td>
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

function renderTbCells(row: {
  opening_debit: number; opening_credit: number;
  period_debit: number; period_credit: number;
  closing_debit: number; closing_credit: number;
}, decimals = 0) {
  const cell = (v: number) => v === 0 ? <td className="zero">-</td> : <td>{fmtD(v, decimals)}</td>;
  return [
    cell(row.opening_debit), cell(row.opening_credit),
    cell(row.period_debit), cell(row.period_credit),
    cell(row.closing_debit), cell(row.closing_credit),
  ];
}

function exportTbCsv(r: TrialBalanceResult) {
  const lines = ['Account,Code,Opening Dr,Opening Cr,Period Dr,Period Cr,Closing Dr,Closing Cr'];
  for (const a of r.result.accounts) {
    const indent = '  '.repeat(Math.max(0, a.depth));
    lines.push([
      `"${indent}${(a.label || '').replace(/"/g, '""')}"`,
      `"${a.code || ''}"`,
      String(a.opening_debit), String(a.opening_credit),
      String(a.period_debit), String(a.period_credit),
      String(a.closing_debit), String(a.closing_credit),
    ].join(','));
  }
  lines.push([
    'Total', '',
    String(r.result.totals.opening_debit), String(r.result.totals.opening_credit),
    String(r.result.totals.period_debit), String(r.result.totals.period_credit),
    String(r.result.totals.closing_debit), String(r.result.totals.closing_credit),
  ].join(','));
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `trial-balance-${r.filters.as_of_date}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function copyTbToClipboard(
  r: TrialBalanceResult | null,
  partyData: Record<string, TrialBalanceParty[]>,
  partyOpen: Set<string>,
) {
  if (!r) return;
  const lines = ['Account\tOpening Dr\tOpening Cr\tPeriod Dr\tPeriod Cr\tClosing Dr\tClosing Cr'];
  for (const a of r.result.accounts) {
    const indent = '\t'.repeat(0) + '  '.repeat(Math.max(0, a.depth));
    lines.push([
      `${indent}${a.label}`,
      String(a.opening_debit), String(a.opening_credit),
      String(a.period_debit), String(a.period_credit),
      String(a.closing_debit), String(a.closing_credit),
    ].join('\t'));
    if (partyOpen.has(a.name) && partyData[a.name]) {
      for (const p of partyData[a.name]) {
        lines.push([
          `${indent}    ${p.party_name}`,
          String(p.opening_debit), String(p.opening_credit),
          String(p.period_debit), String(p.period_credit),
          String(p.closing_debit), String(p.closing_credit),
        ].join('\t'));
      }
    }
  }
  navigator.clipboard.writeText(lines.join('\n'));
}

/** v2.55.0 — the trial balance as a portable document. Supersedes printTb(),
 *  which printed the literal strings `{t('Account')}` and `{t('Total')}` as
 *  labels and produced an unruled page. */
function buildTbDoc(r: any, partyData: Record<string, any[]>, partyOpen: Set<string>, decimals: number): ReportDoc {
  const cur = r.result.currency;
  const note = cur && cur.conversion_rate !== 1
    ? `Converted to ${cur.presentation_currency} @ ${cur.conversion_rate} (rate as of ${cur.as_of_date})`
    : undefined;
  const rows: DocRow[] = [];

  const cell = (v: any) => ({ v: Number(v || 0), text: fmtD(Number(v || 0), decimals), num: true as const });
  const figures = (a: any) => [
    cell(a.opening_debit), cell(a.opening_credit),
    cell(a.period_debit), cell(a.period_credit),
    cell(a.closing_debit), cell(a.closing_credit),
  ];

  for (const a of r.result.accounts) {
    rows.push({
      kind: a.is_group ? 'grp' : 'normal',
      cells: [
        { v: (a.code ? a.code + '  ' : '') + a.label, indent: Math.max(0, a.depth), bold: !!a.is_group },
        ...figures(a).map((c) => ({ ...c, bold: !!a.is_group })),
      ],
    });
    // Party breakdowns are exported exactly as they are shown — an expanded
    // receivables account carries its parties into the file, a collapsed one
    // does not, so the export always matches the screen it came from.
    if (a.has_parties && partyOpen.has(a.name) && partyData[a.name]) {
      for (const p of partyData[a.name]) {
        rows.push({
          kind: 'sub',
          cells: [
            { v: `${p.party_type}: ${p.party_name}`, indent: Math.max(0, a.depth) + 1 },
            ...figures(p),
          ],
        });
      }
    }
  }

  const tot = r.result.totals;
  rows.push({ kind: 'grand', cells: [{ v: 'Total', bold: true }, ...figures(tot).map((c) => ({ ...c, bold: true }))] });

  return {
    title: 'Trial Balance',
    company: r.filters.company,
    companyLabel: r.filters.company,
    period: `${r.filters.from_date || ''} → ${r.filters.as_of_date || r.filters.to_date || ''}`,
    columns: [
      { label: 'Account', width: 44 },
      { label: 'Opening Dr', num: true }, { label: 'Opening Cr', num: true },
      { label: 'Period Dr', num: true }, { label: 'Period Cr', num: true },
      { label: 'Closing Dr', num: true }, { label: 'Closing Cr', num: true },
    ],
    rows,
    fileBase: 'trial_balance',
    orientation: 'landscape',
    note,
  };
}

function escapeHtmlTb(s: string): string {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
