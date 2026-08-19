import { t, arName, loadDimensionOptions } from '../../utils/i18n';
import GlDrillModal, { GlDrillArgs } from './GlDrillModal';
import { useEffect, useMemo, useState, useRef } from 'react';
import type {
  ReportDefinition, ReportSummary, RunResult, RunSnapshot,
  Granularity, ComparisonMode, BudgetBook,
  PivotResult, PivotBy,
  RowDrillResult, RowDrillAccount,
} from '../../types';
import { api } from '../../utils/api';
import GridErrorBoundary from '../../components/GridErrorBoundary';
import { loadBrand, buildFrame, bandRow, stripRow } from '../../utils/branddoc';
import BrandKitModal from '../shell/BrandKitModal';
import { setActiveCompany } from '../../utils/activeCompany';
import {
  MONTHS, FY_RANGE, GRANULARITY_OPTIONS,
  fmtD, fmtPct, fmtPctGrowth, aggregate, buildPeriodGroups, monthLabel, fmtFyLabel,
  injectExpandedQuarters, rowStyleToCss,
} from '../../utils/format';
import { exportCsv, exportPdf, exportPrint, exportXlsx } from '../../utils/export';

// v2.31.0 — the result grid (child component below) mirrors its live
// expand/collapse + drill cache here so the export handlers in the parent can
// reproduce the exact on-screen state in Excel/PDF/CSV/Print.
const gridStateRef: { expanded: Set<string>; drill: Record<string, any[]> } = { expanded: new Set(), drill: {} };
import { PivotMatrix } from '../pivot/PivotMatrix';
import { TAccountView } from './TAccountView';
import { useDimFilters, compactDimFilters } from '../../utils/dimFilters';
import { DimensionMultiSelect } from '../DimensionMultiSelect';
import { ComboView } from '../ComboView';
import { PlHierarchyView } from '../PlHierarchyView';
import { LetterheadPickerModal, type LetterheadChoice } from '../LetterheadPickerModal';
import { fetchLetterhead } from '../../utils/letterhead';
import { TrialBalanceView } from '../trialbalance/TrialBalanceView';
import { BalanceSheetView } from '../balancesheet/BalanceSheetView';
import { ProfitLossStatementView } from '../profitloss/ProfitLossStatementView';

interface Props {
  reports: ReportSummary[];
  selectedReport: string;
  setSelectedReport: (s: string) => void;
  report: ReportDefinition | null;
  onRunResult: (r: RunResult | null) => void;
  lastRun: RunResult | null;
  onPushSnapshot: (snap: RunSnapshot) => void;
}

const STATUS_LABEL: Record<string, string> = {
  draft: 'DRAFT',
  submitted: 'SUBMITTED',
  approved: 'APPROVED',
  locked: 'LOCKED',
};

export function RunTab({ reports, selectedReport, setSelectedReport, report, onRunResult, lastRun, onPushSnapshot }: Props) {
  const [fy, setFy] = useState(2026);
  const [from, setFrom] = useState(0);
  const [to, setTo] = useState(11);
  const [granularity, setGranularity] = useState<Granularity>('month_quarter');
  const [company, setCompany] = useState('');
  useEffect(() => { setActiveCompany(company); }, [company]);
  // v1.9.58 — native dimension filters are arrays (multi-select).
  // Backward compat: APIs accept either scalar or list; we always send list.
  const [costCenter, setCostCenter] = useState<string[]>([]);
  const [project, setProject] = useState<string[]>([]);
  const [department, setDepartment] = useState<string[]>([]);
  const [branch, setBranch] = useState<string[]>([]);
  const [comparisonMode, setComparisonMode] = useState<ComparisonMode>('vs_budget');
  const [priorYears, setPriorYears] = useState(1);
  const [compareToBook, setCompareToBook] = useState<string>(''); // empty = auto-resolve
  const [availableBooks, setAvailableBooks] = useState<BudgetBook[]>([]);
  const [showGrowth, setShowGrowth] = useState(true);
  const [showPctRev, setShowPctRev] = useState(true);
  const [showAch, setShowAch] = useState(true);
  const [showVar, setShowVar] = useState(false);
  // v2.34.1 — toggle for prior-year & growth on account drill rows
  const [qfFuture, setQfFuture] = useState<string>(() => { try { return localStorage.getItem('ni-qframe-future') || 'budget'; } catch { return 'budget'; } });
  // v2.39.0 — +/- on quarter headers (Quarterly only et al.): which quarters
  // are expanded into their months. Display state, persisted.
  const [qExpand, setQExpand] = useState<number[]>(() => { try { return JSON.parse(localStorage.getItem('ni-qexpand') || '[]'); } catch { return []; } });
  const toggleQExpand = (q: number) => {
    const next = qExpand.includes(q) ? qExpand.filter((x) => x !== q) : [...qExpand, q].sort();
    setQExpand(next); try { localStorage.setItem('ni-qexpand', JSON.stringify(next)); } catch { /* */ }
  };
  useEffect(() => { try { localStorage.setItem('ni-qframe-future', qfFuture); } catch { /* */ } }, [qfFuture]);
  const [hideCols, setHideCols] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem('ni-hidecols') || '[]'); } catch { return []; } });
  const [colsOpen, setColsOpen] = useState(false);
  function toggleHideCol(k: string) {
    const next = hideCols.includes(k) ? hideCols.filter((x) => x !== k) : [...hideCols, k];
    setHideCols(next);
    try { localStorage.setItem('ni-hidecols', JSON.stringify(next)); } catch { /* */ }
  }
  const [coverage, setCoverage] = useState<any>(null);
  const [coverageBusy, setCoverageBusy] = useState(false);
  async function runCoverage() {
    if (!selectedReport) return;
    setCoverageBusy(true);
    try { setCoverage(await api.coverageCheck(selectedReport, company || null, fy)); }
    catch (e: any) { alert(String(e?.message || e)); }
    finally { setCoverageBusy(false); }
  }
  const [showDrillCmp, setShowDrillCmp] = useState<boolean>(() => {
    try { return localStorage.getItem('ni-run-drillcmp') !== '0'; } catch { return true; }
  });
  useEffect(() => { try { localStorage.setItem('ni-run-drillcmp', showDrillCmp ? '1' : '0'); } catch { /* ignore */ } }, [showDrillCmp]);
  // Decimal precision for the P&L matrix (v1.9.3) — default from System Settings.
  const [decimals, setDecimals] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>('');

  // ─── Dimension Pivot view (v1.6) ──────────────────────────────────────
  // viewMode='period' (default) renders the existing Matrix with period columns.
  // viewMode='dimension' renders PivotMatrix with one column per dimension value.
  // viewMode='years' (v1.9.50) renders YearsMatrix — annual totals across multiple
  //   fiscal years, side by side. Used for IFRS-style "Comparative Income Statement"
  //   layouts (e.g. 2024 / 2023 / 2022 as columns). Frontend-only: fetches the
  //   same runReport endpoint N times and stitches.
  // viewMode='combo' (v1.9.63) renders ComboView — one row per (report-row ×
  //   dim1 × dim2) tuple. User picks two dimensions and a format (flat or
  //   hierarchy). Empty tuples are hidden by default.
  const [viewMode, setViewMode] = useState<'period' | 'dimension' | 'years' | 'combo'>('period');
  // v2.19 — on-screen row-expand control at the report header (Expanded/
  // Collapsed). Initialised from the report definition's default, but the user
  // decides live; drives both the period Matrix and the P&L Drill hierarchy.
  const [rowExpandMode, setRowExpandMode] = useState<'expanded' | 'collapsed'>('collapsed');
  useEffect(() => {
    setRowExpandMode((report as any)?.default_expand === 'Expanded' ? 'expanded' : 'collapsed');
  }, [selectedReport, (report as any)?.default_expand]);
  // v1.9.63 — combo mode state. Default to (cost_center, project) which are
  // the most common combinations and almost always present.
  const [comboDim1, setComboDim1] = useState<string>('cost_center');
  const [comboDim2, setComboDim2] = useState<string>('project');
  const [comboResult, setComboResult] = useState<any | null>(null);
  // v1.9.99 — combo layout: 'crosstab' (existing) or 'pldrill' (hierarchical P&L)
  const [comboMode, setComboMode] = useState<'crosstab' | 'pldrill'>('crosstab');
  const [plHier, setPlHier] = useState<any | null>(null);
  const [pivotBy, setPivotBy] = useState<PivotBy>('cost_center');
  const [pivotResult, setPivotResult] = useState<PivotResult | null>(null);
  const [pivotVisibleDims, setPivotVisibleDims] = useState<Set<string>>(new Set());
  const [pivotHideZero, setPivotHideZero] = useState(false);
  const [pivotTotalLast, setPivotTotalLast] = useState(false);
  const [pivotLoading, setPivotLoading] = useState(false);

  // v1.9.50 — Years view state. yearsRun holds an array of run results, one
  // per year, oldest → newest. yearsCount drives how many prior years to fetch
  // alongside the current FY (defaults to priorYears for consistency).
  const [yearsCount, setYearsCount] = useState(2);  // current + 2 priors = 3 years
  const [yearsRun, setYearsRun] = useState<{ year: number; result: RunResult }[] | null>(null);
  const [yearsDiag, setYearsDiag] = useState('');
  const [yearsLoading, setYearsLoading] = useState(false);
  // v1.9.97 — Integrity & Coverage audit (read-only).
  const [integrity, setIntegrity] = useState<any | null>(null);
  const [integrityLoading, setIntegrityLoading] = useState(false);
  const [integrityOpen, setIntegrityOpen] = useState(false);

  // Master data — loaded from ERP at mount and whenever the company changes.
  const [companies, setCompanies] = useState<any[]>([]);
  const [costCenters, setCostCenters] = useState<any[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [departments, setDepartments] = useState<any[]>([]);
  const [branches, setBranches] = useState<any[]>([]);
  const [fiscalYears, setFiscalYears] = useState<any[]>([]);
  // v1.9.59 — calendar month (1..12) the selected company's fiscal year
  // begins on. Drives the labels in the From/To month dropdowns BEFORE a
  // report has been run. After a run, lastRun.filters.fy_start_month is
  // the authoritative source.
  const [companyFyStartMonth, setCompanyFyStartMonth] = useState<number>(1);
  // v1.9.60 — reporting calendar selector for group reporting workflows.
  // `reportingCalendars` is the catalogue from the backend (Local + Group
  // Apr-Mar options when applicable). `calendarKey` is the active choice.
  // `fyOverride` is the resolved override month (null = use company's
  // configured calendar). When the user picks "Group", fyOverride=4 and
  // the engine treats this run as Apr-Mar regardless of company config.
  const [reportingCalendars, setReportingCalendars] = useState<Array<{ key: string; label: string; start_month: number; override: number | null }>>([]);
  const [calendarKey, setCalendarKey] = useState<string>('local');
  const fyOverride: number | null = (() => {
    const c = reportingCalendars.find((c) => c.key === calendarKey);
    return c ? c.override : null;
  })();
  // The effective FY start month for this run — drives From/To labels.
  const effectiveFyStartMonth = fyOverride ?? companyFyStartMonth;

  // v1.9.65 — Period mode. Two values:
  //   'fiscal_year' (default) — FY + month range drive date bounds
  //   'date_range' — explicit From/To dates drive bounds; FY is ignored
  // When in date_range mode, Years and (for TB/BS) multi-period view modes
  // are unavailable since they need an FY anchor to make sense.
  const [periodMode, setPeriodMode] = useState<'fiscal_year' | 'date_range'>('fiscal_year');
  // Sensible defaults — calendar year that's current. The user adjusts.
  const _today = new Date();
  const _yearStart = `${_today.getFullYear()}-01-01`;
  const _yearEnd = `${_today.getFullYear()}-12-31`;
  const [periodFromDate, setPeriodFromDate] = useState<string>(_yearStart);
  const [periodToDate, setPeriodToDate] = useState<string>(_yearEnd);

  // v1.9.66 — date-range quick presets. Computed from the company's effective
  // fiscal-year start month + today, so "YTD"/"Current year"/quarters respect
  // the company's calendar (Jan-Dec for KSA, Apr-Mar for India, etc.).
  const [datePreset, setDatePreset] = useState<string>('custom');
  function applyDatePreset(preset: string) {
    setDatePreset(preset);
    if (preset === 'custom') return;
    const fsm = effectiveFyStartMonth || 1;
    const today = new Date();
    const cy = today.getFullYear();
    const cm = today.getMonth() + 1;
    const pad = (n: number) => String(n).padStart(2, '0');
    const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const fyStartYear = cm >= fsm ? cy : cy - 1;
    const fyStart = new Date(fyStartYear, fsm - 1, 1);
    const fyEnd = new Date(fyStartYear + 1, fsm - 1, 0); // day before next FY start
    let from = fyStart;
    let to: Date = today;
    if (preset === 'ytd') { from = fyStart; to = today; }
    else if (preset === 'current_year') { from = fyStart; to = fyEnd; }
    else if (preset === 'prior_year') { from = new Date(fyStartYear - 1, fsm - 1, 1); to = new Date(fyStartYear, fsm - 1, 0); }
    else if (preset === 'this_month') { from = new Date(cy, cm - 1, 1); to = new Date(cy, cm, 0); }
    else if (preset === 'last_month') { from = new Date(cy, cm - 2, 1); to = new Date(cy, cm - 1, 0); }
    else if (preset === 't12m') { from = new Date(today.getFullYear(), today.getMonth() - 11, 1); to = today; }
    else if (preset === 'this_quarter' || preset === 'last_quarter') {
      const fyIdx = ((cm - fsm + 12) % 12);
      let qOff = Math.floor(fyIdx / 3) * 3;
      if (preset === 'last_quarter') qOff -= 3;
      from = new Date(fyStartYear, (fsm - 1) + qOff, 1);
      to = new Date(fyStartYear, (fsm - 1) + qOff + 3, 0);
    }
    setPeriodFromDate(iso(from));
    setPeriodToDate(iso(to));
  }
  // Switching to Date range while in Years mode falls back to Period — Years
  // compares fiscal years, so an arbitrary range has nothing to compare.
  // The reverse direction is handled on the tab itself.
  useEffect(() => {
    if (periodMode === 'date_range' && viewMode === 'years') {
      setViewMode('period');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [periodMode]);

  // v1.9.52 — custom Accounting Dimensions are app-global (shared with
  // Dashboard / CFO Briefing / Group). Discovery + filter state live in
  // DimensionFiltersContext. dimValues stays local since it's a lazy cache
  // tied to this view's interaction.
  const dimCtx = useDimFilters();
  const accountingDims = dimCtx.dimensions;
  const dimFilters = dimCtx.filters;
  const setDimFilters = (next: Record<string, string> | ((p: Record<string, string>) => Record<string, string>)) => dimCtx.setFilters(next);
  const [dimValues, setDimValues] = useState<Record<string, Array<{ name: string; label: string }>>>({});

  // First-mount load: kick off all five fetches in parallel. Don't wait for
  // companies to come back before loading dimensions — they aren't dependent,
  // and showing the user empty dropdowns while companies load is the worst UX.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      // Companies + FY years
      try {
        const [cs, fys] = await Promise.all([api.listCompanies(), api.listFiscalYears()]);
        if (cancelled) return;
        setCompanies(cs as any[]);
        if (cs && cs.length > 0 && !company) setCompany((cs as any[])[0].name);
        setFiscalYears(fys as any[]);
        if (Array.isArray(fys) && fys.length > 0) {
          const top = (fys as any[]).find((y) => y.year_int);
          if (top?.year_int) setFy(top.year_int);
        }
      } catch {
        // ERP probably not installed — leave dropdowns empty.
      }

      // Dimensions: load ALL of them (no company filter) on first mount.
      // The company-change watcher below will narrow them later.
      try {
        const [ccs, projs, depts] = await Promise.all([
          api.listCostCenters(''),
          api.listProjects(''),
          api.listDepartments(''),
        ]);
        if (cancelled) return;
        setCostCenters(ccs as any[]);
        setProjects(projs as any[]);
        setDepartments(depts as any[]);
      } catch {
        // No-op — leave empty.
      }

      // Branch is company-independent in ERP — load once on mount.
      try {
        const brs = await api.listBranches();
        if (!cancelled) setBranches(brs as any[]);
      } catch {
        // No-op.
      }

      // v1.9.52 — discover custom Accounting Dimensions configured on this
      // bench. Writes to global context so other workspaces see them too.
      // Skip the fetch entirely if already discovered (context persists
      // across workspace switches).
      if (accountingDims.length === 0) {
        try {
          const dims = await api.listAccountingDimensions();
          if (!cancelled) dimCtx.setDimensions((dims || []) as any[]);
        } catch {
          if (!cancelled) dimCtx.setDimensions([]);
        }
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When the user explicitly picks a different company, re-narrow the
  // dimension dropdowns. Only fires for real company changes, not the
  // initial empty-string state.
  useEffect(() => {
    if (!company) return;  // Skip the initial empty-string state.
    let cancelled = false;
    (async () => {
      try {
        const [ccs, projs, depts] = await Promise.all([
          api.listCostCenters(company),
          api.listProjects(company),
          api.listDepartments(company),
        ]);
        if (cancelled) return;
        setCostCenters(ccs as any[]);
        setProjects(projs as any[]);
        setDepartments(depts as any[]);
        // v1.9.58 — selections are arrays; drop entries that no longer belong.
        const ccNames = new Set((ccs as any[]).map((x) => x.name));
        const projNames = new Set((projs as any[]).map((x) => x.name));
        const deptNames = new Set((depts as any[]).map((x) => x.name));
        setCostCenter((prev) => prev.filter((v) => ccNames.has(v)));
        setProject((prev) => prev.filter((v) => projNames.has(v)));
        setDepartment((prev) => prev.filter((v) => deptNames.has(v)));
        // Default decimal precision from System Settings.
        try {
          const opts = await api.listReportFilterOptions(company);
          if (!cancelled && typeof opts?.float_precision === 'number') setDecimals(opts.float_precision);
          if (!cancelled && typeof opts?.fy_start_month === 'number') setCompanyFyStartMonth(opts.fy_start_month);
          if (!cancelled && Array.isArray(opts?.reporting_calendars)) setReportingCalendars(opts.reporting_calendars);
        } catch { /* keep current decimals */ }
      } catch {
        // Keep the previous values rather than wiping them.
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company]);

  useEffect(() => {
    if (!selectedReport) return;
    runReport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedReport]);

  // Auto-rerun when any data-affecting filter changes. Short debounce so it
  // feels instant. The Run button still works as an explicit refresh.
  useEffect(() => {
    if (!selectedReport) return;
    const t = setTimeout(() => { runReport(); }, 150);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    fy, from, to, granularity, company,
    costCenter, project, department, branch,
    comparisonMode, priorYears, compareToBook,
  ]);

  // v2.35.1 — auto-follow the cost-center filter: picking exactly ONE cost
  // center switches Compare-to to that centre's book (if it exists for the
  // FY); clearing it (or picking several) returns to the Total Company book.
  useEffect(() => {
    if (comparisonMode !== 'vs_budget' || !availableBooks.length) return;
    const total = availableBooks.find((b) => b.dimension_type === 'total');
    if (costCenter.length === 1) {
      const match = availableBooks.find((b) => b.dimension_type === 'cost_center' && b.dimension_value === costCenter[0]);
      if (match && compareToBook !== match.slug) setCompareToBook(match.slug);
    } else if (compareToBook && availableBooks.find((b) => b.slug === compareToBook)?.dimension_type === 'cost_center') {
      setCompareToBook(total?.slug || '');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [costCenter, availableBooks, comparisonMode]);

  // Reload the list of budget books whenever the report or FY changes — used
  // to populate the "Compare to" picker. Defaults to the Total Company book.
  useEffect(() => {
    if (!report?.name || comparisonMode !== 'vs_budget') return;
    let cancelled = false;
    (async () => {
      try {
        const books = (await api.listBudgetBooks(report.name!, fy)) as BudgetBook[];
        if (cancelled) return;
        setAvailableBooks(books);
        // If the user hasn't picked one, prefer Total Company as the default.
        if (!compareToBook) {
          const total = books.find((b) => b.dimension_type === 'total');
          if (total) setCompareToBook(total.slug);
        } else if (!books.find((b) => b.slug === compareToBook)) {
          // Picked book is no longer valid for this FY; fall back.
          const total = books.find((b) => b.dimension_type === 'total');
          setCompareToBook(total?.slug || '');
        }
      } catch {
        if (!cancelled) setAvailableBooks([]);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [report?.name, fy, comparisonMode]);

  // v1.9.53 — Letter Head picker. When the user clicks any export action,
  // we open the modal first; the chosen letterhead is then fetched and
  // passed into the corresponding export function.
  const [lhPickerOpen, setLhPickerOpen] = useState(false);
  const [lhPendingAction, setLhPendingAction] = useState<null | 'xlsx' | 'pdf' | 'csv' | 'print'>(null);
  // v2.55.0 — the Brand Kit is reachable from here too, not only the ledger.
  const [brandSetupOpen, setBrandSetupOpen] = useState(false);
  const [lhActionLabel, setLhActionLabel] = useState('');

  function initiateExport(action: 'xlsx' | 'pdf' | 'csv' | 'print') {
    if (!lastRun) return;
    setLhPendingAction(action);
    setLhActionLabel(
      action === 'xlsx' ? 'Export to Excel'
      : action === 'pdf' ? 'Export to PDF'
      : action === 'csv' ? 'Export to CSV'
      : 'Open Print Dialog'
    );
    setLhPickerOpen(true);
  }

  async function completeExport(choice: LetterheadChoice) {
    setLhPickerOpen(false);
    if (!lastRun || !lhPendingAction) return;
    const action = lhPendingAction;
    setLhPendingAction(null);
    // Fetch the letterhead payload — even when the user picked "Without
    // letterhead", we pass an empty payload (the export ignores it cleanly).
    const lh = choice.withoutLetterhead
      ? undefined
      : await fetchLetterhead(choice.name, lastRun.filters?.company || company);
    // v2.31.0 — exports mirror the on-screen expand/collapse state exactly:
    // expanded source rows carry their account drill into Excel/PDF/CSV/Print.
    const drillOpt = {
      expanded: Array.from(gridStateRef.expanded),
      drill: showDrillCmp ? gridStateRef.drill : Object.fromEntries(
        Object.entries(gridStateRef.drill).map(([k, accs]) => [k, (accs as any[]).map((a) => ({ ...a, monthly_prev: undefined }))])),
    };
    if (action === 'xlsx') exportXlsx(lastRun, undefined, lh, drillOpt);
    else if (action === 'pdf') exportPdf(lastRun, undefined, lh, drillOpt);
    else if (action === 'csv') exportCsv(lastRun, undefined, lh, drillOpt);
    else if (action === 'print') exportPrint(lastRun, lh, drillOpt);
  }

  function cancelExport() {
    setLhPickerOpen(false);
    setLhPendingAction(null);
  }

  // v2.39.5 — the dropdowns showed "Loading…" forever because values only
  // fetched on mouse-down. Eager-load each discovered dimension's values so
  // Business Division / Employee Cost Center are ready the moment the
  // filters render; the placeholder is honest immediately.
  useEffect(() => {
    for (const d of accountingDims) ensureDimValuesLoaded(d.fieldname);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountingDims]);

  async function ensureDimValuesLoaded(fieldname: string) {
    if (dimValues[fieldname]) return;  // already cached
    try {
      const vals = await api.listDimensionValues(fieldname);
      setDimValues((prev) => ({ ...prev, [fieldname]: vals || [] }));
    } catch {
      // Leave the cache empty for this dim — the dropdown will show the
      // "no values found" empty state.
      setDimValues((prev) => ({ ...prev, [fieldname]: [] }));
    }
  }

  // (compactDimFilters is imported from utils/dimFilters — works on any
  // DimensionFiltersMap, called with `dimFilters` from context.)

  async function runReport() {
    if (!selectedReport) return;
    setLoading(true);
    setError('');
    try {
      // The backend still has a `segment` param (legacy). Treat it as a free-form
      // tag derived from the active company selection so cached runs stay distinct.
      const segmentTag = company || 'total';
      const result = (await api.runReport({
        report: selectedReport,
        fiscal_year: fy,
        // In date_range mode the actual span is driven by the dates below;
        // request the full 0..11 FY window so the engine buckets the entire
        // range (the frontend then sums it into one Total column).
        month_from: periodMode === 'date_range' ? 0 : from,
        month_to: periodMode === 'date_range' ? 11 : to,
        segment: segmentTag,
        cost_center: costCenter.length ? costCenter : null,
        project: project.length ? project : null,
        department: department.length ? department : null,
        branch: branch.length ? branch : null,
        prior_years: priorYears,
        comparison_mode: comparisonMode,
        granularity,
        // v2.38.0 — quarter frame needs the whole year: quarter totals for
        // all four quarters render regardless of the selected window.
        ...(granularity === 'quarter_frame' ? { month_from: 0, month_to: 11, sel_from: from, sel_to: to } : {}),
        compare_to_book: comparisonMode === 'vs_budget' ? (compareToBook || null) : null,
        dimension_filters: compactDimFilters(dimFilters) || null,
        fy_start_month_override: fyOverride,
        period_mode: periodMode,
        period_from_date: periodMode === 'date_range' ? periodFromDate : null,
        period_to_date: periodMode === 'date_range' ? periodToDate : null,
      })) as RunResult;
      onRunResult(result);
    } catch (e: any) {
      setError(e?.message || 'Run failed.');
      onRunResult(null);
    } finally {
      setLoading(false);
    }
  }

  // v1.9.97 — read-only integrity & coverage audit for the active period.
  async function runIntegrity() {
    if (!selectedReport) return;
    setIntegrityLoading(true);
    setIntegrityOpen(true);
    try {
      const res = await api.reportIntegrity({
        report: selectedReport,
        fiscal_year: fy,
        month_from: periodMode === 'date_range' ? 0 : from,
        month_to: periodMode === 'date_range' ? 11 : to,
        period_mode: periodMode,
        period_from_date: periodMode === 'date_range' ? periodFromDate : null,
        period_to_date: periodMode === 'date_range' ? periodToDate : null,
        fy_start_month_override: fyOverride,
      });
      setIntegrity(res);
    } catch (e: any) {
      setIntegrity({ error: e?.message || 'Integrity check failed.' });
    } finally {
      setIntegrityLoading(false);
    }
  }

  /* v1.9.50 — Years view: fetch annual totals for [fy - yearsCount .. fy].
   * Each year is a full-year run (from=Jan, to=Dec) so the column shows the
   * year's annual total. Done in parallel for speed. Failures on individual
   * years are tolerated — that year shows as missing rather than failing the
   * whole view (e.g. FY before company creation will be empty). */
  async function runYears() {
    if (!selectedReport) return;
    setYearsLoading(true);
    setError('');
    try {
      const segmentTag = company || 'total';
      // v2.41.2 — Years now rides the PRIORS engine (one run, prior_years=N):
      // prior-year dates derive arithmetically, no Fiscal Year records needed.
      // The old per-year multi-fetch returned zeros on sites without those
      // records — the bug the v1.9.55 diagnostic comment was hunting.
      const yearList: number[] = [];
      for (let y = fy - yearsCount; y <= fy; y++) yearList.push(y);
      const dimSnapshot = compactDimFilters(dimFilters) || null;
      const settled = await Promise.allSettled([
        api.runReport({
          report: selectedReport,
          fiscal_year: fy,
          month_from: 0,
          month_to: 11,
          segment: segmentTag,
          cost_center: costCenter.length ? costCenter : null,
          project: project.length ? project : null,
          department: department.length ? department : null,
          branch: branch.length ? branch : null,
          prior_years: yearsCount,
          comparison_mode: 'actuals_only',
          granularity: 'month_quarter',  // v2.42.1 — byte-for-byte the PROVEN Period request
          compare_to_book: null,
          dimension_filters: null,
          fy_start_month_override: fyOverride,
        } as any) as Promise<RunResult>,
      ]);
      const out: { year: number; result: RunResult }[] = [];
      const failures: number[] = [];
      const first = settled[0];
      // v2.42.0 — self-diagnosis: capture what the payload ACTUALLY carried,
      // so a zero column comes with its own evidence on screen.
      let diag = '';
      if (first.status === 'fulfilled') {
        const base = first.value as any;
        out.push({ year: fy, result: base });
        const priors = (base.priors || []) as any[];
        for (let idx = 0; idx < yearsCount; idx++) {
          const p = priors[idx];
          if (p && Array.isArray(p.rows)) {
            // YearsMatrix reads result.current.rows — replace the nest it
            // actually consumes (top-level rows is ignored there).
            out.push({
              year: (p.fiscal_year ?? fy - 1 - idx),
              result: { ...base, rows: p.rows, current: { ...(base.current || {}), rows: p.rows } } as RunResult,
            });
          } else {
            failures.push(fy - 1 - idx);
          }
        }
        out.sort((a, b) => a.year - b.year);
        try {
          const pr = (base.priors || []) as any[];
          const sample = pr[0]?.rows?.find((r: any) => r.key === 'total_revenue') || pr[0]?.rows?.[0];
          const sampleSum = sample?.monthly ? Object.values(sample.monthly).reduce((a: number, b: any) => a + Number(b || 0), 0) : null;
          const dbg = pr[0]?.debug ? ` | dates ${pr[0].debug.from ?? '?'}→${pr[0].debug.to ?? '?'} abs=${pr[0].debug.rows_abs_sum ?? '?'}${pr[0].debug.error ? ' ERR:' + pr[0].debug.error : ''}` : '';
          // v2.42.3 — split the two remaining suspects: print a SOURCE row's
          // monthly exactly as received, plus a JS-side total across all rows.
          const src = pr[0]?.rows?.find((r: any) => r.kind === 'source' && r.monthly && Object.values(r.monthly).some((v: any) => Number(v))) || pr[0]?.rows?.find((r: any) => r.key === 'r_revenue');
          const srcSum = src?.monthly ? Object.values(src.monthly).reduce((a: number, b: any) => a + Number(b || 0), 0) : null;
          let jsAbs = 0;
          for (const r of (pr[0]?.rows || [])) for (const v of Object.values((r as any).monthly || {})) jsAbs += Math.abs(Number(v || 0));
          const dbg2 = ` || src '${src?.key ?? 'none'}' sum=${srcSum === null ? '—' : Math.round(srcSum as number).toLocaleString()} entries=${src?.monthly ? JSON.stringify(Object.entries(src.monthly).slice(0, 2)) : '—'} | jsAbs=${Math.round(jsAbs).toLocaleString()}`;
          diag = `payload: priors=${pr.length} | prior[0] fy=${pr[0]?.fiscal_year ?? '—'} rows=${pr[0]?.rows?.length ?? 0} | sample '${sample?.key ?? '—'}' 12m-sum=${sampleSum === null ? 'no monthly' : Math.round(sampleSum as number).toLocaleString()}${dbg}${dbg2}`;
        } catch (e: any) { diag = 'diag failed: ' + String(e?.message || e); }
      } else {
        yearList.forEach((y) => failures.push(y));
        diag = 'run failed: ' + String((first as any).reason?.message || (first as any).reason);
      }
      setYearsDiag(diag);
      setYearsRun(out);
      if (failures.length > 0 && out.length === 0) {
        setError(`Years view failed: no data could be loaded for any of FY${yearList.join(', FY')}.`);
      } else if (failures.length > 0) {
        setError(`Note: data unavailable for ${failures.map((y) => 'FY' + y).join(', ')}. Showing the years that loaded.`);
      }
    } catch (e: any) {
      setError(e?.message || 'Years view failed.');
      setYearsRun(null);
    } finally {
      setYearsLoading(false);
    }
  }

  async function runPivot() {
    if (!selectedReport) return;
    setPivotLoading(true);
    setError('');
    try {
      const result = (await api.runReportDimensionPivot({
        report: selectedReport,
        fiscal_year: fy,
        month_from: from,
        month_to: to,
        pivot_by: pivotBy,
        company: company || null,
      })) as PivotResult;
      setPivotResult(result);
      // On first load (or when the dim set changes), default to all visible.
      // On subsequent reloads with the same dim set, preserve the user's
      // chip selections.
      setPivotVisibleDims((prev) => {
        const newNames = new Set(result.dimensions.map((d) => d.name));
        const prevArray = Array.from(prev);
        const sameSet =
          prev.size === newNames.size &&
          prevArray.every((n) => newNames.has(n));
        if (sameSet && prev.size > 0) return prev;
        return newNames;
      });
    } catch (e: any) {
      setError(e?.message || 'Pivot run failed.');
      setPivotResult(null);
    } finally {
      setPivotLoading(false);
    }
  }

  // Auto-run pivot when in dimension view and key filters change.
  useEffect(() => {
    if (viewMode !== 'dimension') return;
    if (!selectedReport) return;
    const t = setTimeout(() => { runPivot(); }, 150);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewMode, pivotBy, fy, from, to, company, selectedReport]);

  // v1.9.50 — Auto-run years view when entering it or when key filters change.
  // Note we deliberately do NOT include `from`/`to` because Years view uses
  // full-year totals regardless of the period range — those controls are
  // disabled while in Years view.
  useEffect(() => {
    if (viewMode !== 'years') return;
    if (!selectedReport) return;
    const t = setTimeout(() => { runYears(); }, 150);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewMode, fy, yearsCount, company, costCenter, project, department, branch, selectedReport]);

  // v1.9.63 — auto-run combo when in combo view and inputs change.
  useEffect(() => {
    if (viewMode !== 'combo') return;
    if (!selectedReport) return;
    if (comboMode === 'pldrill') {
      if (!comboDim1) return;
      const t = setTimeout(() => { runPlHierarchy(); }, 200);
      return () => clearTimeout(t);
    }
    if (!comboDim1 || !comboDim2) return;
    if (comboDim1 === comboDim2) return;  // backend would reject
    const t = setTimeout(() => { runCombo(); }, 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewMode, comboMode, comboDim1, comboDim2, fy, from, to, company,
      costCenter, project, department, branch, fyOverride,
      periodMode, periodFromDate, periodToDate,
      JSON.stringify(dimFilters), selectedReport]);

  async function runPlHierarchy() {
    setLoading(true);
    setError('');
    try {
      const r = await api.plHierarchy({
        report: report?.name || null,  // v2.38.0 — definition-driven buckets
        company: company || null,
        fiscal_year: fy,
        month_from: from,
        month_to: to,
        primary_dim: comboDim1,
        secondary_dim: comboDim2 && comboDim2 !== comboDim1 ? comboDim2 : '',
        period_mode: periodMode,
        period_from_date: periodMode === 'date_range' ? periodFromDate : null,
        period_to_date: periodMode === 'date_range' ? periodToDate : null,
        fy_start_month_override: fyOverride,
        cost_center: costCenter.length ? costCenter : null,
        project: project.length ? project : null,
        finance_book: null,
        dimension_filters: compactDimFilters(dimFilters) || null,
      });
      setPlHier(r);
    } catch (e: any) {
      setError(e?.message || 'Failed to run P&L drill');
      setPlHier(null);
    } finally {
      setLoading(false);
    }
  }

  async function runCombo() {
    if (!selectedReport) return;
    setLoading(true);
    setError('');
    try {
      const r = await api.runComboReport({
        report: selectedReport,
        dim1: comboDim1,
        dim2: comboDim2,
        fiscal_year: fy,
        month_from: from,
        month_to: to,
        company: company || null,
        cost_center: costCenter.length ? costCenter : null,
        project: project.length ? project : null,
        department: department.length ? department : null,
        branch: branch.length ? branch : null,
        dimension_filters: compactDimFilters(dimFilters) || null,
        fy_start_month_override: fyOverride,
        period_mode: periodMode,
        period_from_date: periodMode === 'date_range' ? periodFromDate : null,
        period_to_date: periodMode === 'date_range' ? periodToDate : null,
      });
      setComboResult(r);
    } catch (e: any) {
      setError(e?.message || 'Failed to run combo view');
      setComboResult(null);
    } finally {
      setLoading(false);
    }
  }

  function visualize() {
    if (!lastRun || !report) return;
    const companyLabel = arName(lastRun.filters.segment, companies.find((c) => c.name === lastRun.filters.segment)?.label || lastRun.filters.segment || 'All');
    const snap: RunSnapshot = {
      id: 'run_' + Date.now().toString(36),
      name: `${companyLabel} · ${lastRun.filters.fy_label || ('FY' + lastRun.filters.fiscal_year)} · ${monthLabel(lastRun.filters.fy_start_month, lastRun.filters.month_from)}-${monthLabel(lastRun.filters.fy_start_month, lastRun.filters.month_to)}`,
      createdAt: Date.now(),
      run: lastRun,
      rowDefs: report.definition.rows.map((r) => ({ key: r.key, label: r.label, kind: r.kind })),
    };
    onPushSnapshot(snap);
  }

  const groups = useMemo(() => {
    if (!lastRun) return [];
    if (lastRun.period_groups && lastRun.period_groups.length > 0) return lastRun.period_groups;
    return buildPeriodGroups(lastRun.filters.month_from, lastRun.filters.month_to, lastRun.filters.granularity || 'month_quarter', lastRun.filters.fy_start_month, (lastRun.filters as any).sel_from, (lastRun.filters as any).sel_to).groups;
  }, [lastRun]);

  const monthsAll = useMemo(() => {
    if (!lastRun) return [];
    // v2.38.3 — under quarter_frame the fetch is the full year but the KPI
    // cards, YTD totals and drill totals must speak about the USER's window.
    const f: any = lastRun.filters;
    const mf = f.granularity === 'quarter_frame' && f.sel_from != null ? f.sel_from : f.month_from;
    const mt = f.granularity === 'quarter_frame' && f.sel_to != null ? f.sel_to : f.month_to;
    const out: number[] = [];
    for (let m = mf; m <= mt; m++) out.push(m);
    return out;
  }, [lastRun]);

  // Route to the right view based on the selected report's type. Trial
  // Balance and Balance Sheet have entirely different filter sets and
  // execution engines from the P&L matrix, so they get their own components.
  const reportType = (report?.report_type as string) || 'pnl';
  if (reportType === 'trial_balance') {
    return (
      <TrialBalanceView
        reports={reports}
        selectedReport={selectedReport}
        setSelectedReport={setSelectedReport}
        companies={companies}
        costCenters={costCenters}
        projects={projects}
        departments={departments}
        branches={branches}
        fiscalYears={fiscalYears}
      />
    );
  }
  if (reportType === 'balance_sheet') {
    return (
      <BalanceSheetView
        reports={reports}
        selectedReport={selectedReport}
        setSelectedReport={setSelectedReport}
        companies={companies}
        costCenters={costCenters}
        projects={projects}
        departments={departments}
        branches={branches}
      />
    );
  }
  if (reportType === 'pnl_statement') {
    return (
      <ProfitLossStatementView
        reports={reports}
        selectedReport={selectedReport}
        setSelectedReport={setSelectedReport}
        companies={companies}
        costCenters={costCenters}
        projects={projects}
        departments={departments}
        branches={branches}
        fiscalYears={fiscalYears}
      />
    );
  }

  return (
    <div>
      {/* v1.9.64 — View sub-tabs. Lifted out of the filter strip to fix
       *  overflow at narrow widths and give the four modes proper space.
       *  Conditional inputs (Outer/Inner dim, Comparative years) stay in
       *  the filter strip below — they're filters for the active mode. */}
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
          <i className="ti ti-layout-columns" aria-hidden /> {t('Dimension')}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === 'years'}
          className={'view-subtab' + (viewMode === 'years' ? ' is-active' : '')}
          /* v2.51.0 — Years needs fiscal years to compare, so it used to sit
             disabled under Date range. Greyed out it read as missing rather
             than blocked, and the reason was buried in a tooltip. Clicking it
             now moves Period mode back to Fiscal year, which both satisfies
             the dependency and demonstrates it. */
          onClick={() => { if (periodMode === 'date_range') setPeriodMode('fiscal_year'); setViewMode('years'); }}
          title={periodMode === 'date_range'
            ? t('Comparative annual totals — switches Period mode back to Fiscal year')
            : t('Comparative annual totals across multiple fiscal years')}
        >
          <i className="ti ti-calendar-stats" aria-hidden /> {t('Years')}
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
          <label><span className="flbl">{t('Report')}</span>
            <select value={selectedReport} onChange={(e) => setSelectedReport(e.target.value)}>
              {reports.map((r) => <option key={r.name} value={r.slug || r.name}>{r.report_name}</option>)}
            </select>
          </label>
          {/* v1.9.65 — Period mode selector. Drives whether the subsequent
            * controls are FY-based (Fiscal year + From/To months) or
            * date-based (From/To date pickers). */}
          <label>
            <span className="flbl" title="Choose between fiscal-year reporting and ad-hoc date range">
              {t('Period mode')}
            </span>
            <select value={periodMode} onChange={(e) => setPeriodMode(e.target.value as any)}>
              <option value="fiscal_year">{t('Fiscal year')}</option>
              <option value="date_range">{t('Date range')}</option>
            </select>
          </label>
          {periodMode === 'fiscal_year' && (
            <>
              <label><span className="flbl">{t('Fiscal year')}</span>
                <select value={fy} onChange={(e) => setFy(parseInt(e.target.value))}>
                  {fiscalYears.length > 0
                    ? fiscalYears.map((y) => (
                        <option key={y.name} value={y.year_int || y.name}>
                          {y.name}
                        </option>
                      ))
                    : FY_RANGE.map((y) => <option key={y} value={y}>FY{y}</option>)}
                </select>
              </label>
              {reportingCalendars.length > 1 && (
                <label>
                  <span className="flbl" title="Switch between local statutory reporting and group reporting calendar">
                    {t('Reporting calendar')}
                  </span>
                  <select value={calendarKey} onChange={(e) => setCalendarKey(e.target.value)}>
                    {reportingCalendars.map((c) => (
                      <option key={c.key} value={c.key}>{c.label}</option>
                    ))}
                  </select>
                </label>
              )}
              <label><span className="flbl">{t('From')}</span>
                <select value={from} onChange={(e) => setFrom(parseInt(e.target.value))} disabled={viewMode === 'years'}>
                  {Array.from({ length: 12 }, (_, i) => i).map((i) => (
                    <option key={i} value={i}>{monthLabel(effectiveFyStartMonth, i)}</option>
                  ))}
                </select>
              </label>
              <label><span className="flbl">{t('To')}</span>
                <select value={to} onChange={(e) => setTo(parseInt(e.target.value))} disabled={viewMode === 'years'}>
                  {Array.from({ length: 12 }, (_, i) => i).map((i) => (
                    <option key={i} value={i}>{monthLabel(effectiveFyStartMonth, i)}</option>
                  ))}
                </select>
              </label>
              <label className="gran-cell" style={{ gridColumn: 'span 2' }}><span className="flbl">{t('Granularity')}</span>
                <select value={granularity} onChange={(e) => setGranularity(e.target.value as Granularity)} disabled={viewMode === 'dimension' || viewMode === 'years'}>
                  {GRANULARITY_OPTIONS.map((g) => <option key={g.value} value={g.value}>{t(g.label)}</option>)}
                </select>
              </label>
              {granularity === 'quarter_frame' && (
                <>
                  <label><span className="flbl">{t('Future quarters')}</span>
                    <select value={qfFuture} onChange={(e) => setQfFuture(e.target.value)}>
                      <option value="budget">{t('Budget')}</option>
                      <option value="blank">{t('Blank')}</option>
                    </select>
                  </label>
                  <label><span className="flbl">{t('Columns')}</span>
                    <span className="navgrp" onClick={(e) => e.stopPropagation()}>
                      <button type="button" className="ghost-btn" onClick={() => setColsOpen((o) => !o)}>
                        {hideCols.length ? `${hideCols.length} ${t('hidden')}` : t('All shown')} ▾
                      </button>
                      {colsOpen && (
                        <div className="navgrp-menu" style={{ minWidth: 220 }}>
                          {['q0', 'q1', 'q2', 'q3'].map((k, i) => (
                            <label key={k} className="navgrp-item" style={{ display: 'flex', gap: 8 }}>
                              <input type="checkbox" checked={!hideCols.includes(k)} onChange={() => toggleHideCol(k)} /> Q{i + 1}
                            </label>
                          ))}
                          <div className="theme-hint" style={{ padding: '4px 10px' }}>{t('Hidden columns disappear from screen, Print, Excel, PDF and CSV alike.')}</div>
                        </div>
                      )}
                    </span>
                  </label>
                </>
              )}
            </>
          )}
          {periodMode === 'date_range' && (
            <>
              <label><span className="flbl">{t('Quick range')}</span>
                <select value={datePreset} onChange={(e) => applyDatePreset(e.target.value)}>
                  <option value="custom">{t('Custom')}</option>
                  <option value="ytd">{t('Year to date')}</option>
                  <option value="current_year">{t('Current year')}</option>
                  <option value="prior_year">{t('Prior year')}</option>
                  <option value="this_quarter">{t('This quarter')}</option>
                  <option value="last_quarter">{t('Last quarter')}</option>
                  <option value="this_month">{t('This month')}</option>
                  <option value="last_month">{t('Last month')}</option>
                  <option value="t12m">{t('Trailing 12 months')}</option>
                </select>
              </label>
              <label><span className="flbl">{t('From date')}</span>
                <input type="date" value={periodFromDate} onChange={(e) => { setPeriodFromDate(e.target.value); setDatePreset('custom'); }} />
              </label>
              <label><span className="flbl">{t('To date')}</span>
                <input type="date" value={periodToDate} onChange={(e) => { setPeriodToDate(e.target.value); setDatePreset('custom'); }} />
              </label>
            </>
          )}
          <label><span className="flbl">{t('Company')}</span>
            <select value={company} onChange={(e) => setCompany(e.target.value)}>
              {companies.length === 0 && <option value="">— No companies found —</option>}
              {companies.map((c) => (
                <option key={c.name} value={c.name}>
                  {arName(c.name, c.label)}{c.abbr ? ` (${c.abbr})` : ''}
                </option>
              ))}
            </select>
          </label>
          {viewMode === 'years' && (
            <label><span className="flbl">{t('Comparative years')}</span>
              <select value={yearsCount} onChange={(e) => setYearsCount(parseInt(e.target.value))}>
                <option value="1">Current + 1 prior</option>
                <option value="2">Current + 2 priors</option>
                <option value="3">Current + 3 priors</option>
                <option value="4">Current + 4 priors</option>
              </select>
            </label>
          )}
          {viewMode === 'combo' && (
            <>
              <label><span className="flbl">{t('Outer dim')}</span>
                <select value={comboDim1} onChange={(e) => setComboDim1(e.target.value)}>
                  <option value="cost_center">Cost Center</option>
                  <option value="project">Project</option>
                  <option value="department">Department</option>
                  <option value="branch">Branch</option>
                  {accountingDims.length > 0 && <option disabled>──── Custom ────</option>}
                  {accountingDims.map((dim) => (
                    <option key={dim.fieldname} value={dim.fieldname}>{dim.label}</option>
                  ))}
                </select>
              </label>
              <label><span className="flbl">{t('Inner dim')}</span>
                <select value={comboDim2} onChange={(e) => setComboDim2(e.target.value)}>
                  <option value="cost_center">Cost Center</option>
                  <option value="project">Project</option>
                  <option value="department">Department</option>
                  <option value="branch">Branch</option>
                  {accountingDims.length > 0 && <option disabled>──── Custom ────</option>}
                  {accountingDims.map((dim) => (
                    <option key={dim.fieldname} value={dim.fieldname}>{dim.label}</option>
                  ))}
                </select>
              </label>
            </>
          )}
          {/* v2.18 — Layout available in every view: Standard keeps the current
              view; the two pivot layouts switch into Combo. */}
          <label><span className="flbl">{t('Layout')}</span>
            <select value={viewMode === 'combo' ? comboMode : 'standard'} onChange={(e) => {
              const v = e.target.value;
              if (v === 'standard') { if (viewMode === 'combo') setViewMode('period'); return; }
              setComboMode(v as any);
              if (viewMode !== 'combo') setViewMode('combo');
            }}>
              <option value="standard">{t('Standard')}</option>
              <option value="crosstab">{t('Cross-tab')}</option>
              <option value="pldrill">{t('P&L Drill (with subtotals)')}</option>
            </select>
          </label>
          <label><span className="flbl">{t('Default Row Expand')}</span>
            <select value={rowExpandMode} onChange={(e) => setRowExpandMode(e.target.value as 'expanded' | 'collapsed')}>
              <option value="collapsed">{t('Collapsed')}</option>
              <option value="expanded">{t('Expanded')}</option>
            </select>
          </label>
          {viewMode === 'dimension' && (
            <label><span className="flbl">{t('Pivot by')}</span>
              <select value={pivotBy} onChange={(e) => setPivotBy(e.target.value as PivotBy)}>
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
        </div>
        {viewMode === 'period' && <div className="filter-grid">
          <label><span className="flbl">{t('Cost center')} {costCenters.length > 0 && <span className="muted" style={{ fontSize: 9 }}>({costCenters.length})</span>}</span>
            <DimensionMultiSelect
              value={costCenter}
              options={[{ name: '__BLANK__', label: t('(No value)') }, ...costCenters.map((c: any) => ({ name: c.name, label: c.label }))]}
              onChange={setCostCenter}
              placeholder="— All cost centers —"
            />
          </label>
          <label><span className="flbl">{t('Project')} {projects.length > 0 && <span className="muted" style={{ fontSize: 9 }}>({projects.length})</span>}</span>
            <DimensionMultiSelect
              value={project}
              options={[{ name: '__BLANK__', label: t('(No value)') }, ...projects.map((p: any) => ({ name: p.name, label: p.label }))]}
              onChange={setProject}
              placeholder="— All projects —"
            />
          </label>
          <label><span className="flbl">{t('Department')} {departments.length > 0 && <span className="muted" style={{ fontSize: 9 }}>({departments.length})</span>}</span>
            <DimensionMultiSelect
              value={department}
              options={[{ name: '__BLANK__', label: t('(No value)') }, ...departments.map((d: any) => ({ name: d.name, label: d.label }))]}
              onChange={setDepartment}
              placeholder="— All departments —"
            />
          </label>
          <label><span className="flbl">{t('Branch')} {branches.length > 0 && <span className="muted" style={{ fontSize: 9 }}>({branches.length})</span>}</span>
            <DimensionMultiSelect
              value={branch}
              options={[{ name: '__BLANK__', label: t('(No value)') }, ...branches.map((b: any) => ({ name: b.name, label: b.label }))]}
              onChange={setBranch}
              placeholder="— All branches —"
            />
          </label>
          {/* v1.9.52 — custom Accounting Dimensions discovered from this
           *  bench. Values are lazy-loaded on first dropdown open so we
           *  don't fire N extra queries on page mount.
           *  v1.9.58 — multi-select for custom dims too. State is shared
           *  across views via DimensionFiltersContext. */}
          {accountingDims.map((dim) => {
            const vals = dimValues[dim.fieldname];
            const loaded = vals !== undefined;
            const current = dimFilters[dim.fieldname];
            const currentArr: string[] = Array.isArray(current)
              ? current
              : current ? [current as string] : [];
            return (
              <label key={dim.fieldname}>
                <span className="flbl">
                  {dim.label}
                  {loaded && vals.length > 0 && (
                    <span className="muted" style={{ fontSize: 9 }}> ({vals.length})</span>
                  )}
                </span>
                <div onMouseDownCapture={() => ensureDimValuesLoaded(dim.fieldname)}>
                  <DimensionMultiSelect
                    value={currentArr}
                    options={(vals || []).map((v) => ({ name: v.name, label: v.label }))}
                    onChange={(next) => setDimFilters((prev) => ({ ...prev, [dim.fieldname]: next }))}
                    placeholder={loaded ? `— All ${dim.label.toLowerCase()} —` : 'Loading…'}
                  />
                </div>
              </label>
            );
          })}
          <label><span className="flbl">{t('Comparison')}</span>
            <select value={comparisonMode} onChange={(e) => setComparisonMode(e.target.value as ComparisonMode)}>
              <option value="actuals_only">{t('Actuals only')}</option>
              <option value="vs_budget">{t('Actual vs Budget')}</option>
            </select>
          </label>
          {comparisonMode === 'vs_budget' && (
            <label style={{ gridColumn: 'span 2' }}><span className="flbl">{t('Compare to (budget book)')}</span>
              {(lastRun as any)?.filters?.compare_to_book_resolved && (
                <span className="muted" style={{ display: 'block', fontSize: 10, marginTop: 2 }}>
                  {(() => {
                    const b = (lastRun as any).filters.compare_to_book_resolved;
                    const src = (lastRun as any).budget?.source;
                    if (src && typeof src === 'object' && src.rollup) return `Budget source: sum of ${src.books} ${src.rollup.replace('_', ' ')} book${src.books === 1 ? '' : 's'} (Total book is empty)`;
                    return `Budget source: ${b.label || b.slug || ''}`;
                  })()}
                </span>
              )}
              <select value={compareToBook} onChange={(e) => {
                const slug = e.target.value;
                setCompareToBook(slug);
                // v2.35.2 — the BOOK is the master of comparison scope:
                // picking a dimension book mirrors its value into the run
                // filters so actuals, drills and KPIs all match the budget's
                // scope; picking Total clears the mirrored dimensions.
                const b = availableBooks.find((x) => x.slug === slug);
                if (!b) return;
                if (b.dimension_type === 'cost_center') setCostCenter(b.dimension_value ? [b.dimension_value] : []);
                else if (b.dimension_type === 'project') setProject(b.dimension_value ? [b.dimension_value] : []);
                else if (b.dimension_type === 'department') setDepartment(b.dimension_value ? [b.dimension_value] : []);
                else if (b.dimension_type === 'total') { setCostCenter([]); setProject([]); setDepartment([]); }
              }} style={{ fontWeight: 500 }}>
                {availableBooks.length === 0 && <option value="">— Total (auto) —</option>}
                {availableBooks.map((b) => (
                  <option key={b.slug} value={b.slug}>
                    {b.label}  [{STATUS_LABEL[b.status] || b.status}]
                  </option>
                ))}
              </select>
            </label>
          )}
          <label><span className="flbl">{t('Prior years')}</span>
            <select value={priorYears} onChange={(e) => setPriorYears(parseInt(e.target.value))}>
              <option value={0}>None</option>
              <option value={1}>1</option>
              <option value={2}>2</option>
              <option value={3}>3</option>
              <option value={4}>4</option>
              <option value={5}>5 (max)</option>
            </select>
          </label>
        </div>}
        {viewMode === 'dimension' && pivotResult && (
          <PivotChipStrip
            result={pivotResult}
            visibleDims={pivotVisibleDims}
            setVisibleDims={setPivotVisibleDims}
          />
        )}
        {viewMode === 'period' && comparisonMode === 'vs_budget' && lastRun?.filters.compare_to_book_resolved && (
          <BookInfoNote book={lastRun.filters.compare_to_book_resolved} />
        )}
        {viewMode === 'period' && <div className="derived-row">
          <label className="chk"><input type="checkbox" checked={showGrowth} onChange={(e) => setShowGrowth(e.target.checked)} /> {t('% Growth')}</label>
          <label className="chk"><input type="checkbox" checked={showPctRev} onChange={(e) => setShowPctRev(e.target.checked)} /> {t('% of Revenue')}</label>
          <label className="chk"><input type="checkbox" checked={showAch} onChange={(e) => setShowAch(e.target.checked)} /> {t('% Achieved')}</label>
          <label className="chk"><input type="checkbox" checked={showVar} onChange={(e) => setShowVar(e.target.checked)} /> {t('Variance')}</label>
          <label className="chk" title={t('Show prior-year values and growth % on the account rows when a source row is expanded — applies on screen and in every export')}><input type="checkbox" checked={showDrillCmp} onChange={(e) => setShowDrillCmp(e.target.checked)} /> {t('Account comparisons')}</label>
          <label className="chk" title={t('Print text size. Auto fits the font to the number of visible columns — with quarter frame + hidden columns the print comes out large and readable; fixed sizes override.')}>
            {t('Print size')}
            <select defaultValue={(() => { try { return localStorage.getItem('ni-print-size') || 'auto'; } catch { return 'auto'; } })()}
              onChange={(e) => { try { localStorage.setItem('ni-print-size', e.target.value); } catch { /* */ } }}
              style={{ marginInlineStart: 4 }}>
              <option value="auto">{t('Auto (fit columns)')}</option>
              <option value="s">{t('Compact')}</option>
              <option value="m">{t('Medium')}</option>
              <option value="l">{t('Large')}</option>
            </select>
          </label>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginLeft: 8 }}>
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{t('Decimals')}</span>
            <select value={decimals} onChange={(e) => setDecimals(parseInt(e.target.value))} style={{ padding: '2px 6px' }}>
              {[0, 1, 2, 3, 4].map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </span>
        </div>}
        {viewMode === 'dimension' && <div className="derived-row">
          <label className="chk"><input type="checkbox" checked={pivotHideZero} onChange={(e) => setPivotHideZero(e.target.checked)} /> Hide all-zero columns</label>
          <label className="chk"><input type="checkbox" checked={pivotTotalLast} onChange={(e) => setPivotTotalLast(e.target.checked)} /> Total column last</label>
        </div>}
        <div className="action-row">
          {viewMode === 'period' ? (
            <>
              <button onClick={runReport} disabled={loading}>
                <i className="ti ti-refresh" aria-hidden /> {loading ? t('Running…') : t('Run')}
              </button>
              <button onClick={() => lastRun && initiateExport('xlsx')}><i className="ti ti-file-spreadsheet" aria-hidden /> {t('Excel')}</button>
              <button onClick={() => lastRun && initiateExport('pdf')}><i className="ti ti-file-text" aria-hidden /> {t('PDF')}</button>
              <button onClick={() => lastRun && initiateExport('print')}><i className="ti ti-printer" aria-hidden /> {t('Print')}</button>
              <button onClick={() => lastRun && initiateExport('csv')}><i className="ti ti-file-export" aria-hidden /> {t('CSV')}</button>
              <button className="ni-exp-gear" title={t('Print setup — letterhead, logo, borders, paper')}
                onClick={() => setBrandSetupOpen(true)}>
                <span aria-hidden>⚙</span><span className="ni-exp-gear-lbl">{t('Setup')}</span>
              </button>
              <button onClick={runIntegrity} disabled={integrityLoading || !selectedReport} title={t('Audit mapping coverage & integrity for this period')}>
                <i className="ti ti-shield-check" aria-hidden /> {integrityLoading ? t('Checking…') : t('Integrity')}
              </button>
              <button onClick={runCoverage} disabled={coverageBusy}
                title={t('Compare the chart of accounts (all Income/Expense) against this report\'s mapped accounts — unmapped accounts are the mismatch vs the native P&L')}>
                <i className="ti ti-list-check" aria-hidden /> {coverageBusy ? '…' : t('Coverage')}
              </button>
              <button onClick={visualize} className="primary-btn"><i className="ti ti-chart-pie" aria-hidden /> {t('Visualize')}</button>
              {lastRun?.performance && <span className="run-meta">{lastRun.performance.execution_ms}ms{lastRun.performance.cache_hit ? ' · cached' : ''}</span>}
            </>
          ) : (
            <>
              <button onClick={runPivot} disabled={pivotLoading}>
                <i className="ti ti-refresh" aria-hidden /> {pivotLoading ? t('Running…') : t('Run')}
              </button>
              <button onClick={() => pivotResult && exportPivotXlsx(pivotResult, pivotVisibleDims, pivotTotalLast)}><i className="ti ti-file-spreadsheet" aria-hidden /> {t('Excel')}</button>
              <button onClick={() => pivotResult && exportPivotPrint(pivotResult, pivotVisibleDims, pivotTotalLast)}><i className="ti ti-printer" aria-hidden /> {t('PDF / Print')}</button>
              <button onClick={() => pivotResult && exportPivotCsv(pivotResult, pivotVisibleDims, pivotTotalLast)}><i className="ti ti-file-export" aria-hidden /> {t('CSV')}</button>
              <button onClick={() => pivotResult && copyPivotToClipboard(pivotResult, pivotVisibleDims, pivotTotalLast)}><i className="ti ti-clipboard" aria-hidden /> {t('Copy')}</button>
              <button className="ni-exp-gear" title={t('Print setup — letterhead, logo, borders, paper')}
                onClick={() => setBrandSetupOpen(true)}>
                <span aria-hidden>⚙</span><span className="ni-exp-gear-lbl">{t('Setup')}</span>
              </button>
              
              {pivotResult?.performance && <span className="run-meta">{pivotResult.performance.execution_ms}ms{pivotResult.performance.cache_hit ? ' · cached' : ''}</span>}
            </>
          )}
        </div>
        {/* v1.9.62 — subtle progress bar during runs; replaces the
         *  jarring full-page spinner pattern. Sits above any error/meta. */}
        <div className={'loading-bar' + (loading ? ' is-loading' : '')} aria-hidden />
        {error && <div className="run-error">{error}</div>}
      </div>

      {integrityOpen && (
        <div className="integ-panel">
          <div className="integ-head">
            <div className="integ-title">
              <i className="ti ti-shield-check" aria-hidden /> {t('Integrity & Coverage')}
            </div>
            <button className="integ-close" onClick={() => setIntegrityOpen(false)} aria-label={t('Close')}>×</button>
          </div>
          {integrityLoading ? (
            <div className="integ-loading"><i className="ti ti-loader-2" aria-hidden /> {t('Auditing mappings and GL…')}</div>
          ) : integrity?.error ? (
            <div className="run-error">{integrity.error}</div>
          ) : integrity ? (
            <>
              <div className="integ-cov">
                {(() => {
                  const c = integrity.coverage || {};
                  const pct = c.coverage_pct ?? 100;
                  const tone = pct >= 99.5 ? 'ok' : pct >= 95 ? 'warn' : 'bad';
                  return (
                    <>
                      <div className={'integ-cov-ring ' + tone} style={{ ['--pct' as any]: pct }}>
                        <span>{pct}%</span>
                      </div>
                      <div className="integ-cov-text">
                        <strong>{c.covered_active_accounts ?? 0}</strong> of <strong>{c.total_active_accounts ?? 0}</strong> active{' '}
                        {(c.covered_root_types || []).join(' / ') || 'P&L'} accounts are mapped to a row.
                        {c.unmapped_active_accounts > 0 && (
                          <span className="integ-cov-miss"> {c.unmapped_active_accounts} with activity are missing from the statement.</span>
                        )}
                      </div>
                    </>
                  );
                })()}
              </div>

              {(integrity.findings || []).length === 0 ? (
                <div className="integ-clean"><i className="ti ti-circle-check" aria-hidden /> {t('No issues found for this period. Every active account is accounted for.')}</div>
              ) : (
                <div className="integ-findings">
                  {(integrity.findings || []).map((f: any) => (
                    <details key={f.id} className={'integ-finding sev-' + f.severity} open={f.severity === 'high'}>
                      <summary>
                        <span className={'integ-sev sev-' + f.severity}>{f.severity}</span>
                        <span className="integ-fname">{f.title}</span>
                        <span className="integ-fcount">{f.count}</span>
                      </summary>
                      <div className="integ-fdetail">{f.detail}</div>
                      <div className="integ-ffix"><i className="ti ti-bulb" aria-hidden /> {f.fix}</div>
                      <div className="integ-items">
                        {(f.items || []).slice(0, 50).map((it: any, i: number) => (
                          <div key={i} className="integ-item">
                            {it.code && <code>{it.code}</code>}
                            <span className="integ-item-name">{it.name || it.row_label || it.flag}</span>
                            {typeof it.amount === 'number' && <span className="integ-item-amt">{it.amount.toLocaleString()}</span>}
                            {it.flags && <span className="integ-item-meta">→ {it.flags.join(', ')}</span>}
                            {it.new_accounts && it.new_accounts.length > 0 && (
                              <span className="integ-item-meta">+{it.new_count}: {it.new_accounts.map((a: any) => a.code).join(', ')}</span>
                            )}
                            {it.mappings && <span className="integ-item-meta">{it.mappings} mapping(s)</span>}
                            {it.resolved_count != null && !it.new_accounts && <span className="integ-item-meta">{it.resolved_count} acct(s)</span>}
                          </div>
                        ))}
                        {(f.items || []).length > 50 && <div className="integ-more">+{f.items.length - 50} more…</div>}
                      </div>
                    </details>
                  ))}
                </div>
              )}
            </>
          ) : null}
        </div>
      )}

      {/* v1.9.48 — presentation format branch. T-account layout for
       *  Trading and P&L style statements; vertical (default) for everything
       *  else. Both branches use the same upstream report data — only the
       *  rendering changes. */}
      {viewMode === 'period' && lastRun && report?.presentation_format === 't_account' ? (
        <div style={{ position: 'relative' }}>
          {loading && (
            <div style={{
              position: 'absolute', inset: 0, zIndex: 5,
              background: 'rgba(247, 247, 245, 0.55)',
              display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
              paddingTop: 18, pointerEvents: 'none',
              backdropFilter: 'blur(0.5px)',
            }}>
              <span style={{
                fontSize: 11, padding: '4px 10px',
                background: 'var(--info-bg, #e6f1fb)',
                color: 'var(--info, #0c447c)',
                borderRadius: 12, fontWeight: 500,
              }}>
                <i className="ti ti-loader-2" aria-hidden /> Updating…
              </span>
            </div>
          )}
          <TAccountView run={lastRun} report={report} monthsAll={monthsAll} decimals={decimals} />
        </div>
      ) : (
        <>
      {coverage && (
        <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) setCoverage(null); }}>
          <div className="theme-panel" role="dialog" style={{ width: 'min(860px, 100%)' }}>
            <div className="theme-h">
              <h3>{t('Coverage check')} — {coverage.mapped}/{coverage.total_pl_accounts} {t('P&L accounts mapped')}</h3>
              <button className="fh-x" onClick={() => setCoverage(null)}>×</button>
            </div>
            {coverage.missing.length === 0 ? (
              <div className="studio-hint" style={{ padding: 16 }}>✓ {t('Every Income/Expense account is mapped — this report reconciles with the native P&L by construction.')}</div>
            ) : (
              <>
                <div className="studio-hint" style={{ padding: '4px 12px' }}>
                  {t('Unmapped')}: {coverage.missing.length} · {t('Income value missing')}: {fmtD(coverage.missing_value_income, 2)} · {t('Expense value missing')}: {fmtD(coverage.missing_value_expense, 2)}
                </div>
                <div style={{ maxHeight: '55vh', overflow: 'auto' }}>
                  <table className="studio-table" style={{ width: '100%' }}>
                    <thead><tr><th>{t('Account')}</th><th>{t('Type')}</th><th className="num">{t('FY value')}</th><th>{t('Assign to row')}</th></tr></thead>
                    <tbody>
                      {coverage.missing.map((a: any) => (
                        <tr key={a.account}>
                          <td>{(a.account_number ? a.account_number + ' · ' : '') + a.account_name}</td>
                          <td>{a.root_type}</td>
                          <td className={'num' + (a.amount < 0 ? ' cf-neg' : '')}>{fmtD(a.amount, 2)}</td>
                          <td>
                            <select defaultValue="" onChange={async (e) => {
                              const row = coverage.rows.find((r: any) => r.key === e.target.value);
                              if (!row) return;
                              try {
                                await api.saveAccountMapping({ report: selectedReport, account: a.account, flag: row.flag || row.key });
                                setCoverage({ ...coverage, mapped: coverage.mapped + 1, missing: coverage.missing.filter((x: any) => x.account !== a.account) });
                              } catch (err: any) { alert(String(err?.message || err)); }
                            }}>
                              <option value="">{t('— pick row —')}</option>
                              {coverage.rows.map((r: any) => <option key={r.key} value={r.key}>{r.label}</option>)}
                            </select>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="studio-hint" style={{ padding: '6px 12px' }}>{t('Assignments apply immediately — re-run the report to see balances reconcile.')}</div>
              </>
            )}
          </div>
        </div>
      )}
      {viewMode === 'period' && lastRun && <KpiRow run={lastRun} monthsAll={monthsAll} decimals={decimals} />}
      {viewMode === 'period' && lastRun && groups.length > 0 && (
        <div style={{ position: 'relative' }}>
          {loading && (
            <div style={{
              position: 'absolute', inset: 0, zIndex: 5,
              background: 'rgba(247, 247, 245, 0.55)',
              display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
              paddingTop: 18, pointerEvents: 'none',
              backdropFilter: 'blur(0.5px)',
            }}>
              <span style={{
                fontSize: 11, padding: '4px 10px',
                background: 'var(--info-bg, #e6f1fb)',
                color: 'var(--info, #0c447c)',
                borderRadius: 12, fontWeight: 500,
              }}>
                <i className="ti ti-loader-2" aria-hidden /> Updating…
              </span>
            </div>
          )}
          <GridErrorBoundary>
          <Matrix
            run={lastRun}
            groups={groups}
            monthsAll={monthsAll}
            show={{ growth: showGrowth, pctrev: showPctRev, ach: showAch, var: showVar, drillcmp: showDrillCmp, hideCols, qExpand, onToggleQ: toggleQExpand }}
            decimals={decimals}
            selectedReport={selectedReport}
            company={company}
            costCenter={costCenter}
            project={project}
            department={department}
            defaultExpand={rowExpandMode === 'expanded'}
          />
          </GridErrorBoundary>
        </div>
      )}
        </>
      )}
      {viewMode === 'dimension' && pivotResult && (
        <div style={{ position: 'relative' }}>
          {pivotLoading && (
            <div style={{
              position: 'absolute', inset: 0, zIndex: 5,
              background: 'rgba(247, 247, 245, 0.55)',
              display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
              paddingTop: 18, pointerEvents: 'none',
              backdropFilter: 'blur(0.5px)',
            }}>
              <span style={{
                fontSize: 11, padding: '4px 10px',
                background: 'var(--info-bg, #e6f1fb)',
                color: 'var(--info, #0c447c)',
                borderRadius: 12, fontWeight: 500,
              }}>
                <i className="ti ti-loader-2" aria-hidden /> Updating…
              </span>
            </div>
          )}
          <PivotMatrix
            result={pivotResult}
            visibleDims={pivotVisibleDims}
            setVisibleDims={setPivotVisibleDims}
            hideZero={pivotHideZero}
            totalLast={pivotTotalLast}
          />
        </div>
      )}
      {viewMode === 'dimension' && !pivotResult && !pivotLoading && (
        <div className="card" style={{ padding: 22, textAlign: 'center' }}>
          <div className="strong" style={{ marginBottom: 6 }}>No pivot data yet.</div>
          <div className="muted" style={{ fontSize: 12 }}>
            Pick a Pivot by dimension above and the matrix will populate automatically.
          </div>
        </div>
      )}

      {/* v1.9.50 — Years view: comparative annual totals across multiple
       *  fiscal years, side by side. Used for IFRS-style "Comparative Income
       *  Statement" layouts (e.g. 2024 / 2023 / 2022 as columns). */}
      {viewMode === 'years' && (
        <div style={{ position: 'relative' }}>
          {yearsLoading && (
            <div style={{
              position: 'absolute', inset: 0, zIndex: 5,
              background: 'rgba(247, 247, 245, 0.55)',
              display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
              paddingTop: 18, pointerEvents: 'none',
              backdropFilter: 'blur(0.5px)',
            }}>
              <span style={{
                fontSize: 11, padding: '4px 10px',
                background: 'var(--info-bg, #e6f1fb)',
                color: 'var(--info, #0c447c)',
                borderRadius: 12, fontWeight: 500,
              }}>
                <i className="ti ti-loader-2" aria-hidden /> Loading {yearsCount + 1} years…
              </span>
            </div>
          )}
          {yearsRun && yearsRun.length > 0 ? (
            <>
                            <YearsMatrix runs={yearsRun} decimals={decimals} report={report} company={company} />
            </>
          ) : !yearsLoading ? (
            <div className="card" style={{ padding: 22, textAlign: 'center' }}>
              <div className="strong" style={{ marginBottom: 6 }}>No years data yet.</div>
              <div className="muted" style={{ fontSize: 12 }}>
                The comparative view will populate automatically. Adjust the year or comparative count above to refresh.
              </div>
            </div>
          ) : null}
        </div>
      )}

      {/* v1.9.63 — Combo view: one row per (report row × dim1 × dim2) tuple.
       *  User picks two dimensions (outer / inner) in the filter strip, and
       *  selects flat vs hierarchy presentation in the toolbar. Empty
       *  tuples are hidden by default. The same component renders for P&L,
       *  Trial Balance, and Balance Sheet because the backend dispatcher
       *  returns a uniform shape. */}
      {viewMode === 'combo' && (
        <div>
          {comboMode === 'pldrill' ? (
            plHier ? (
              <PlHierarchyView data={plHier} decimals={decimals} defaultExpand={rowExpandMode === 'expanded'} />
            ) : !loading ? (
              <div className="empty-state">
                <div className="empty-state-icon"><i className="ti ti-binary-tree" aria-hidden /></div>
                <h3 className="empty-state-title">P&amp;L drill ready</h3>
                <p className="empty-state-body">
                  Pick a primary dimension (e.g. Cost Center) and an optional secondary
                  (e.g. Intercompany). Revenue, Gross Profit and Net Profit are computed
                  at each level. It refreshes automatically.
                </p>
              </div>
            ) : null
          ) : comboDim1 === comboDim2 ? (
            <div className="empty-state">
              <div className="empty-state-icon"><i className="ti ti-alert-triangle" aria-hidden /></div>
              <h3 className="empty-state-title">Pick two different dimensions</h3>
              <p className="empty-state-body">
                Combo view requires two different dimensions to combine. Set
                the Outer and Inner dimensions to distinct values.
              </p>
            </div>
          ) : comboResult ? (
            <ComboView
              result={comboResult}
              decimals={decimals}
              dimensions={accountingDims}
            />
          ) : !loading ? (
            <div className="empty-state">
              <div className="empty-state-icon"><i className="ti ti-table" aria-hidden /></div>
              <h3 className="empty-state-title">Combo view ready</h3>
              <p className="empty-state-body">
                The combo view will populate automatically. Adjust the dimensions
                or filters above to refresh.
              </p>
            </div>
          ) : null}
        </div>
      )}

      {brandSetupOpen && (
        <BrandKitModal company={company} companyLabel={company}
          onClose={() => setBrandSetupOpen(false)} />
      )}
      {/* v1.9.53 — Letter Head picker. Opens on any export/print action. */}
      <LetterheadPickerModal
        open={lhPickerOpen}
        report={selectedReport}
        company={lastRun?.filters?.company || company}
        actionLabel={lhActionLabel}
        onConfirm={completeExport}
        onCancel={cancelExport}
      />
    </div>
  );
}

function PivotChipStrip({
  result, visibleDims, setVisibleDims,
}: {
  result: PivotResult;
  visibleDims: Set<string>;
  setVisibleDims: (s: Set<string>) => void;
}) {
  const allOn = () => setVisibleDims(new Set(result.dimensions.map((d) => d.name)));
  const noneOn = () => setVisibleDims(new Set());
  const top5 = () => {
    const ranked = [...result.dimensions]
      .sort((a, b) => Math.abs(b.revenue) - Math.abs(a.revenue))
      .slice(0, 5).map((d) => d.name);
    setVisibleDims(new Set(ranked));
  };
  const toggle = (n: string) => {
    const next = new Set(visibleDims);
    if (next.has(n)) next.delete(n); else next.add(n);
    setVisibleDims(next);
  };
  if (result.dimensions.length === 0) {
    return (
      <div className="chip-strip" style={{ marginTop: 12 }}>
        <span className="chip-strip-label">Columns visible</span>
        <span className="muted" style={{ fontSize: 11 }}>
          No {result.filters.pivot_by.replace('_', ' ')} values found in ERP for this company.
        </span>
      </div>
    );
  }
  return (
    <div className="chip-strip" style={{ marginTop: 12 }}>
      <span className="chip-strip-label">Columns visible</span>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', flex: 1 }}>
        {result.dimensions.map((d) => (
          <div
            key={d.name}
            className={'pivot-chip' + (visibleDims.has(d.name) ? ' on' : '')}
            onClick={() => toggle(d.name)}
            title={d.company ? `${d.label} · ${d.company}` : d.label}
          >
            {d.label}
          </div>
        ))}
      </div>
      <div className="quick-btns" style={{ display: 'flex', gap: 6 }}>
        <button type="button" onClick={allOn}>All</button>
        <button type="button" onClick={top5}>Top 5 by revenue</button>
        <button type="button" onClick={noneOn}>Clear</button>
      </div>
    </div>
  );
}

function KpiRow({ run, monthsAll, decimals }: { run: RunResult; monthsAll: number[]; decimals: number }) {
  const get = (rk: string) => {
    const r = run.current.rows.find((x) => x.key === rk);
    return r ? aggregate(r.monthly, monthsAll) : 0;
  };
  const getB = (rk: string) => {
    if (!run.budget) return null;
    const r = run.budget.rows.find((x) => x.key === rk);
    return r ? aggregate(r.monthly, monthsAll) : null;
  };
  const getPY = (rk: string) => {
    if (!run.priors[0]) return null;
    const r = run.priors[0].rows.find((x) => x.key === rk);
    return r ? aggregate(r.monthly, monthsAll) : null;
  };
  const items = [
    { lbl: 'Revenue', val: get('total_revenue'), bud: getB('total_revenue'), py: getPY('total_revenue') },
    { lbl: 'Gross profit', val: get('gross_profit'), bud: getB('gross_profit'), py: getPY('gross_profit'), ofRev: get('total_revenue') },
    { lbl: 'EBITDA', val: get('ebitda'), bud: getB('ebitda'), ofRev: get('total_revenue') },
    { lbl: 'Net income', val: get('net_income'), bud: getB('net_income'), py: getPY('net_income'), ofRev: get('total_revenue') },
  ];
  return (
    <div className="kpi-row">
      {items.map((it) => (
        <div className="metric" key={it.lbl}>
          <div className="lbl">{t(it.lbl)}</div>
          <div className="val" style={{ color: it.val < 0 ? 'var(--color-text-danger)' : undefined }}>{fmtD(it.val, decimals)}</div>
          {it.bud != null && it.bud !== 0 && (
            <div className={'delta ' + (it.val / it.bud >= 1 ? 'up' : 'down')}>{fmtPct(it.val / it.bud)} {t('of budget')}</div>
          )}
          {it.py != null && it.py !== 0 && (
            <div className={'delta ' + ((it.val - it.py) >= 0 ? 'up' : 'down')}>{Math.abs(it.py) < 1 ? '—' : fmtPctGrowth((it.val - it.py) / Math.abs(it.py))} {t('vs')} {fmtFyLabel(run.filters.fy_start_month, run.filters.fiscal_year - 1)}</div>
          )}
          {it.ofRev != null && it.ofRev !== 0 && it.lbl !== 'Revenue' && (
            <div className="delta">{fmtPct(it.val / it.ofRev)} {t('of revenue')}</div>
          )}
        </div>
      ))}
    </div>
  );
}

function Matrix({
  run, groups, monthsAll, show, decimals,
  selectedReport, company, costCenter, project, department, defaultExpand,
}: {
  run: RunResult;
  groups: { tier: string; periods: { key: string; label: string; months: number[]; gran: string }[] }[];
  monthsAll: number[];
  show: { growth: boolean; pctrev: boolean; ach: boolean; var: boolean; drillcmp?: boolean; hideCols?: string[]; qExpand?: number[]; onToggleQ?: (q: number) => void };
  decimals: number;
  selectedReport: string;
  company: string;
  costCenter: string[];
  project: string[];
  department: string[];
  defaultExpand?: boolean;
}) {
  const f = run.filters;
  const revRow = run.current.rows.find((r) => r.key === 'total_revenue');

  // ─── Row drill state (v1.8.1) ──────────────────────────────────────
  // Per-row expand/collapse state. Drill data is lazy-fetched on first
  // expand; cached client-side so re-expanding is instant. Drill data is
  // dropped when filters change (we re-fetch with new filters).
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [drillData, setDrillData] = useState<Record<string, RowDrillAccount[]>>({});
  useEffect(() => { gridStateRef.expanded = expandedRows; gridStateRef.drill = drillData; }, [expandedRows, drillData]);
  const [drillLoading, setDrillLoading] = useState<Set<string>>(new Set());
  const [glDrill, setGlDrill] = useState<GlDrillArgs | null>(null);
  function openGl(rowKey: string, title: string, expected: number | null, account?: string | null) {
    setGlDrill({
      report: selectedReport, row_key: rowKey, account: account ?? null,
      fiscal_year: f.fiscal_year, month_from: f.month_from, month_to: f.month_to,
      cost_center: costCenter.length ? costCenter : null,
      project: project.length ? project : null,
      department: department.length ? department : null,
      company: company || null,
      period_mode: (f as any).period_mode || 'fiscal_year',
      period_from_date: (f as any).period_from_date ?? null,
      period_to_date: (f as any).period_to_date ?? null,
      title, expected, decimals,
    });
  }

  // Reset drill state when filters change. Comparing JSON-serialized snapshots
  // is a coarse but safe trigger.
  const filterFingerprint = JSON.stringify({ fy: f.fiscal_year, mf: f.month_from, mt: f.month_to, company, costCenter, project, department });
  useEffect(() => {
    setExpandedRows(new Set());
    setDrillData({});
  }, [filterFingerprint]);

  async function toggleExpand(rowKey: string) {
    if (expandedRows.has(rowKey)) {
      // Collapse: remove from set; keep cached data for next expand.
      setExpandedRows((s) => { const n = new Set(s); n.delete(rowKey); return n; });
      return;
    }
    if (drillData[rowKey]) {
      // Already cached — just expand.
      setExpandedRows((s) => { const n = new Set(s); n.add(rowKey); return n; });
      return;
    }
    // Fetch.
    setDrillLoading((s) => { const n = new Set(s); n.add(rowKey); return n; });
    try {
      const r = (await api.runReportRowDrill({
        report: selectedReport,
        row_key: rowKey,
        fiscal_year: f.fiscal_year,
        month_from: f.month_from,
        month_to: f.month_to,
        cost_center: costCenter.length ? costCenter : null,
        project: project.length ? project : null,
        department: department.length ? department : null,
        company: company || null,
        period_mode: (f as any).period_mode || 'fiscal_year',
        period_from_date: (f as any).period_from_date ?? null,
        period_to_date: (f as any).period_to_date ?? null,
      })) as RowDrillResult;
      setDrillData((d) => ({ ...d, [rowKey]: r.accounts || [] }));
      setExpandedRows((s) => { const n = new Set(s); n.add(rowKey); return n; });
    } catch (e: any) {
      // Surface the error to the user without crashing the matrix.
      console.error('Row drill failed', e);
      alert(`Could not load accounts for this row: ${e?.message || 'unknown error'}`);
    } finally {
      setDrillLoading((s) => { const n = new Set(s); n.delete(rowKey); return n; });
    }
  }

  // v1.9.92 / v2.19 — honour the on-screen "Default Row Expand" selector:
  // expand every source row (pre-fetching accounts) when set to Expanded, or
  // collapse them when set to Collapsed. Re-runs when the selector or filters
  // change.
  const autoExpandedRef = useRef<string>('');
  useEffect(() => {
    if (!run?.current?.rows?.length) return;
    const sig = filterFingerprint + ':' + (defaultExpand ? 'E' : 'C');
    if (autoExpandedRef.current === sig) return;
    autoExpandedRef.current = sig;
    if (defaultExpand) { expandAllRows(); } else { collapseAllRows(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultExpand, run, filterFingerprint]);

  // v2.18 — on-screen expand/collapse-all (overrides the definition default).
  // v2.31.0 — section-scoped expand/collapse: rows belong to the SECTION row
  // above them, so the section list maps to contiguous source-row key spans.
  const sectionMap: { label: string; keys: string[] }[] = (() => {
    const out: { label: string; keys: string[] }[] = [];
    let cur: { label: string; keys: string[] } | null = null;
    for (const r of run.current.rows as any[]) {
      if (r.kind === 'section') { cur = { label: r.label, keys: [] }; out.push(cur); }
      else if (r.kind === 'source' && !r.hidden && cur) cur.keys.push(r.key);
    }
    return out.filter((x) => x.keys.length);
  })();
  const [pickedSections, setPickedSections] = useState<Set<string>>(new Set());
  const [secMenuOpen, setSecMenuOpen] = useState(false);

  async function expandRows(sourceKeys: string[]) {
    if (!sourceKeys.length) return;
    const need = sourceKeys.filter((k) => !drillData[k]);
    setDrillLoading((s) => { const n = new Set(s); need.forEach((k) => n.add(k)); return n; });
    try {
      const results = await Promise.all(need.map((k) =>
        api.runReportRowDrill({
          report: selectedReport, row_key: k,
          fiscal_year: f.fiscal_year, month_from: f.month_from, month_to: f.month_to,
          cost_center: costCenter.length ? costCenter : null,
          project: project.length ? project : null,
          department: department.length ? department : null,
          company: company || null,
          period_mode: (f as any).period_mode || 'fiscal_year',
          period_from_date: (f as any).period_from_date ?? null,
          period_to_date: (f as any).period_to_date ?? null,
        }).then((r: any) => [k, r.accounts || []] as const).catch(() => [k, [] as any] as const),
      ));
      setDrillData((d) => { const n = { ...d }; results.forEach(([k, acc]) => { n[k] = acc; }); return n; });
      setExpandedRows((s) => { const n = new Set(s); sourceKeys.forEach((k) => n.add(k)); return n; });
    } finally {
      setDrillLoading((s) => { const n = new Set(s); need.forEach((k) => n.delete(k)); return n; });
    }
  }
  function expandAllRows() {
    return expandRows(run.current.rows.filter((r: any) => r.kind === 'source' && !r.hidden).map((r: any) => r.key));
  }
  function collapseAllRows() { setExpandedRows(new Set()); }
  function selectedSectionKeys(): string[] {
    return sectionMap.filter((sec) => pickedSections.has(sec.label)).flatMap((sec) => sec.keys);
  }
  function expandSelectedSections() { expandRows(selectedSectionKeys()); setSecMenuOpen(false); }
  function collapseSelectedSections() {
    const ks = new Set(selectedSectionKeys());
    setExpandedRows((s) => { const n = new Set(s); ks.forEach((k) => n.delete(k)); return n; });
    setSecMenuOpen(false);
  }

  // Per-period sub-columns. All four optional metrics (Variance, % Growth,
  // % Achieved, % Rev) appear under every period when their toggles are on —
  // that's how users want to see month-by-month variance and growth trends.
  const subs: string[] = (() => {
    const isDR = (f as any).period_mode === 'date_range';
    const s = ['actual'];
    if (!isDR && f.comparison_mode === 'vs_budget') s.push('budget');
    for (let i = 0; i < run.priors.length; i++) s.push('py' + (i + 1));
    if (!isDR && show.var && f.comparison_mode === 'vs_budget') s.push('var');
    if (show.growth && run.priors.length > 0) s.push('grw');
    if (!isDR && show.ach && f.comparison_mode === 'vs_budget') s.push('ach');
    if (show.pctrev) s.push('pcr');
    return s;
  })();

  // Trailing YTD-Total block — same column set as per-period, computed over
  // the full month range. Skipped when granularity = 'ytd' (which already
  // produces a YTD column in period_order).
  const totSubs: string[] = (() => {
    const s = ['actual'];
    if (f.comparison_mode === 'vs_budget') s.push('budget');
    for (let i = 0; i < run.priors.length; i++) s.push('py' + (i + 1));
    if (show.var && f.comparison_mode === 'vs_budget') s.push('var');
    if (show.growth && run.priors.length > 0) s.push('grw');
    if (show.ach && f.comparison_mode === 'vs_budget') s.push('ach');
    if (show.pctrev) s.push('pcr');
    return s;
  })();

  const subClass = (s: string) =>
    s === 'budget' ? 'col-budget' :
    s.startsWith('py') ? 'col-prior' :
    s === 'actual' ? '' : 'col-derived';

  const tierClass = (tier: string) => tier !== 'month' ? 'gran-' + tier : '';

  const labelFor = (s: string) => {
    if (s === 'actual') return t('Actual');
    if (s === 'budget') return t('Budget');
    if (s.startsWith('py')) return fmtFyLabel(run.filters.fy_start_month, run.priors[parseInt(s.slice(2)) - 1].fiscal_year);
    if (s === 'var') return t('Var');
    if (s === 'grw') return t('% Grw');
    if (s === 'ach') return t('% Ach');
    if (s === 'pcr') return t('% Rev');
    return s;
  };

  // v1.9.66 — DATE RANGE: the engine buckets a date span into FY-month
  // indices for storage, but the span is meant to be read as ONE total (its
  // own docstring says "sum the values"). So in date_range mode we render a
  // single Total column labelled with the actual dates — never fake months.
  const isDateRange = (run.filters as any).period_mode === 'date_range';
  const fmtRange = (a?: string, b?: string) => {
    const d = (x?: string) => {
      if (!x) return '';
      const dt = new Date(x + 'T00:00:00');
      return isNaN(dt.getTime()) ? x
        : dt.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' });
    };
    return d(a) + ' – ' + d(b);
  };

  // Prefer period_order from the run result — that's the interleaved
  // sequence (Jan, Feb, Mar, Q1, Apr, May, Jun, Q2, ...). Fall back to
  // flattening the groups tier-by-tier when running against an older
  // backend that doesn't ship period_order yet.
  const flat: { tier: string; key: string; label: string; months: number[]; gran: string }[] =
    isDateRange
      ? [{ tier: 'total', key: 'range', gran: 'range', months: Array.from({ length: 12 }, (_, i) => i),
           label: fmtRange((run.filters as any).period_from_date, (run.filters as any).period_to_date) }]
      : (() => {
          let base: any[];
          if ((run as any).period_order && (run as any).period_order.length > 0) {
            base = (run as any).period_order;
            if ((run.filters as any).granularity === 'quarter_frame' && show && (show as any).hideCols?.length) {
              base = base.filter((p: any) => !(show as any).hideCols.includes(p.key));
            }
          } else {
            base = [];
            for (const g of groups) for (const p of g.periods) base.push({ tier: g.tier, ...p });
          }
          // v2.39.0 — expandable quarters wherever months aren't already shown
          const gran = String((run.filters as any).granularity || '');
          if (!gran.includes('month') && gran !== 'quarter_frame') {
            base = injectExpandedQuarters(base, (show as any)?.qExpand || [], run.filters.fy_start_month);
          }
          return base;
        })();

  // Suppress the rightmost "YTD Total" trailing block when the interleaved
  // period_order already contains a YTD column (which happens for granularity
  // "ytd"). Otherwise the same total would render twice. Also suppressed in
  // date_range, where the single Total column already IS the total.
  const ytdAlreadyInFlat = flat.some((p) => p.tier === 'ytd');
  const trailingTotSubs = (isDateRange || ytdAlreadyInFlat) ? [] : totSubs;

  return (
    <div className="matrix-wrap">
      {glDrill && <GlDrillModal args={glDrill} onClose={() => setGlDrill(null)} />}
      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <span className="text-muted" style={{ fontSize: 12 }}>{t('Rows')}:</span>
        <span className="navgrp" onClick={(e) => e.stopPropagation()}>
          <button className="btn btn-xs btn-default" onClick={() => setSecMenuOpen((o) => !o)} aria-expanded={secMenuOpen}>
            {t('Sections')}{pickedSections.size ? ` (${pickedSections.size})` : ''} ▾
          </button>
          {secMenuOpen && (
            <div className="navgrp-menu" role="menu" style={{ insetInlineEnd: 0, insetInlineStart: 'auto', minWidth: 260 }}>
              <div className="navgrp-title">{t('Expand / collapse only these sections')}</div>
              {sectionMap.map((sec) => (
                <label key={sec.label} className="navgrp-item" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <input type="checkbox" checked={pickedSections.has(sec.label)}
                    onChange={() => setPickedSections((ps) => { const n = new Set(ps); n.has(sec.label) ? n.delete(sec.label) : n.add(sec.label); return n; })} />
                  {sec.label} <span className="cf-count">{sec.keys.length}</span>
                </label>
              ))}
              <div style={{ display: 'flex', gap: 6, padding: '8px 10px 4px' }}>
                <button className="btn btn-xs btn-default" onClick={expandSelectedSections} disabled={!pickedSections.size}>{t('Expand selected')}</button>
                <button className="btn btn-xs btn-default" onClick={collapseSelectedSections} disabled={!pickedSections.size}>{t('Collapse selected')}</button>
              </div>
            </div>
          )}
        </span>
        <button className="btn btn-xs btn-default" onClick={expandAllRows}>{t('Expand all')}</button>
        <button className="btn btn-xs btn-default" onClick={collapseAllRows}>{t('Collapse all')}</button>
      </div>
      <div className="matrix-scroll">
        <table className="matrix">
          <thead>
            <tr className="h1">
              <th rowSpan={2}>Row</th>
              {flat.map((p) => {
                const qIdx = /^q[0-3]$/.test(p.key) ? parseInt(p.key.slice(1)) : -1;
                const gran = String((run.filters as any).granularity || '');
                const expandable = qIdx >= 0 && !gran.includes('month') && gran !== 'quarter_frame' && (show as any)?.onToggleQ;
                const isOpen = expandable && ((show as any)?.qExpand || []).includes(qIdx);
                return (
                  <th key={p.key + '-h'} colSpan={subs.length} className={tierClass(p.tier)}>
                    {expandable && (
                      <button className="q-expander" title={isOpen ? t('Collapse months') : t('Expand months')}
                        onClick={() => (show as any).onToggleQ(qIdx)}>{isOpen ? '−' : '+'}</button>
                    )}
                    {p.label}
                  </th>
                );
              })}
              {trailingTotSubs.length > 0 && <th colSpan={trailingTotSubs.length}>Yearly</th>}
            </tr>
            <tr className="h2">
              {flat.flatMap((p) =>
                subs.map((s, i) => (
                  <th key={p.key + '-' + s + '-' + i} className={tierClass(p.tier) + ' ' + subClass(s)}>{labelFor(s)}</th>
                ))
              )}
              {trailingTotSubs.map((s, i) => (
                <th key={'tot-' + s + '-' + i} className={subClass(s)}>{labelFor(s)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {run.current.rows.map((row, idx) => {
              if ((row as any).hidden) return null;
              const rowCss = rowStyleToCss((row as any).style);
              if (row.kind === 'section') {
                const n = flat.length * subs.length + trailingTotSubs.length;
                return (
                  <tr key={row.key} className="r-section" style={rowCss}>
                    <td>{row.label}</td>
                    <td colSpan={n} />
                  </tr>
                );
              }
              // v2.79.1 — matched by KEY, not by array index.
              //
              // Index matching assumes the budget and prior-year row arrays are
              // the same length as the display rows and in the same order. Any
              // row handled on a different branch of the budget builder — the
              // allocation rows added in v2.79.0 — shifted every row after it by
              // one: the P&L showed January's budget in the February column,
              // February's in March, and dropped December's entirely. Silent,
              // because the figures were all plausible.
              //
              // Every other consumer of budget.rows already matches on key.
              const bud = run.budget?.rows.find((r: any) => r.key === row.key)
                          ?? run.budget?.rows[idx];
              const pys = run.priors.map((p) => p.rows.find((r: any) => r.key === row.key) ?? p.rows[idx]);
              const isFormula = row.kind === 'formula';
              const isSource = row.kind === 'source';
              const isExpanded = expandedRows.has(row.key);
              const isLoading = drillLoading.has(row.key);
              const accounts = drillData[row.key] || [];

              // v1.9.92 — a combined "Net Profit / Loss" formula row shows the
              // correct word for its sign: profit when positive, loss when negative.
              const _lbl = row.label || '';
              const _isNetRow = isFormula && (
                (/net/i.test(_lbl) && /profit/i.test(_lbl) && /loss/i.test(_lbl)) ||
                (/ربح/.test(_lbl) && /خسار/.test(_lbl))
              );
              const displayLabel = _isNetRow
                ? (aggregate(row.monthly, monthsAll) < 0 ? t('Net Loss') : t('Net Profit'))
                : _lbl;

              const parentRow = (
                <tr key={row.key} className={isFormula ? 'r-formula' : ''} style={rowCss}>
                  <td>
                    {isSource ? (
                      <button
                        className="row-drill-btn"
                        onClick={() => toggleExpand(row.key)}
                        aria-label={isExpanded ? 'Collapse accounts' : 'Show mapped accounts'}
                        title={isExpanded ? 'Hide mapped accounts' : 'Show mapped accounts'}
                      >{isLoading ? '⋯' : (isExpanded ? '−' : '+')}</button>
                    ) : <span className="row-drill-spacer" />}
                    <span style={{ marginLeft: 4 }}>{displayLabel}</span>
                    {isSource && (
                      <button
                        className="gl-verify-btn"
                        onClick={() => openGl(row.key, row.label, aggregate(row.monthly, monthsAll), null)}
                        title={t('Verify') + ' — ' + t('GL entries')}
                      >🔍</button>
                    )}
                    {isSource && (() => {
                      const meta = run.binding_meta?.[(row as any).flag];
                      if (!meta) return null;

                      // v2.76.1 — a row with nothing bound sums to zero in
                      // ~10ms and looks exactly like a row with genuinely no
                      // activity. That indistinguishability is what made a
                      // config gap read as "the report is broken." Flag it
                      // here, at the row that's actually affected, whether
                      // it was never mapped or the mapping was deleted —
                      // for EVERY source row, not only group bindings.
                      if (meta.resolved_count === 0) {
                        const why = !meta.has_binding
                          ? 'No accounts are assigned to this row.'
                          : meta.missing_count > 0
                            ? `The account${meta.missing_count === 1 ? '' : 's'} mapped to this row ` +
                              `${meta.missing_count === 1 ? 'no longer exists' : 'no longer exist'} in the chart of accounts.`
                            : 'The accounts mapped to this row resolve to nothing (an empty group, or the bound group was removed).';
                        return (
                          <span className="row-unbound-warn" title={`${why} This row will show 0.000 until it's remapped — that is a configuration gap, not a computed result. Open Map to fix.`}>
                            <i className="ti ti-alert-triangle" aria-hidden /> {t('Unmapped')}
                          </span>
                        );
                      }

                      // Stale accounts that still count in resolved_count's
                      // *history* but no longer contribute to the SQL query —
                      // this row shows a number, just a quietly smaller one
                      // than the mapping implies.
                      const missingBadge = meta.missing_count > 0 && (
                        <span className="row-unbound-warn row-unbound-warn--partial"
                          title={`${meta.missing_count} previously-mapped account${meta.missing_count === 1 ? '' : 's'} no longer exist in the chart of accounts and no longer contribute to this row's total. Open Map to clean up the mapping.`}>
                          <i className="ti ti-alert-triangle" aria-hidden /> {meta.missing_count}
                        </span>
                      );

                      if (!meta.is_group) return missingBadge || null;

                      const tip =
                        `Live group binding — resolves to ${meta.resolved_count} account${meta.resolved_count === 1 ? '' : 's'} at run time` +
                        (meta.group_codes && meta.group_codes.length ? ` (under ${meta.group_codes.join(', ')})` : '') +
                        '. New accounts added under the group are included automatically — no remap needed.';
                      const newTip = meta.new_count > 0
                        ? `${meta.new_count} account${meta.new_count === 1 ? '' : 's'} joined this row since it was set up: ` +
                          meta.new_accounts.map((a) => a.code).join(', ') + (meta.new_truncated ? ' …' : '')
                        : '';
                      return (
                        <>
                          <span className="row-livegroup" title={tip}>
                            <i className="ti ti-folder" aria-hidden /> {meta.resolved_count}
                            {meta.new_count > 0 && (
                              <span className="row-livegroup-new" title={newTip}>+{meta.new_count} new</span>
                            )}
                          </span>
                          {missingBadge}
                        </>
                      );
                    })()}
                  </td>
                  {flat.map((p) => {
                    const pm = Array.isArray((p as any).months) ? p.months : [];
                    const a = aggregate(row.monthly, pm);
                    const bv = bud ? aggregate(bud.monthly, pm) : null;
                    const pyAggs = pys.map((r) => aggregate(r.monthly, pm));
                    const rev = revRow ? aggregate(revRow.monthly, pm) : 0;
                    const tcls = tierClass(p.tier);
                    return subs.map((s, i) => {
                      const key = p.key + '-' + s + '-' + i;
                      if (s === 'actual') {
                        // v2.38.0 — future quarters in the quarter frame:
                        // Actual shows Budget (muted) or blank, per header.
                        if ((p as any).future) {
                          let qf = 'budget';
                          try { qf = localStorage.getItem('ni-qframe-future') || 'budget'; } catch { /* */ }
                          return qf === 'budget'
                            ? <td key={key} className={'col-budget ' + tcls} style={{ fontStyle: 'italic' }}>{fmtD(bv, decimals)}</td>
                            : <td key={key} className={tcls + ' zero'}>—</td>;
                        }
                        return <td key={key} className={tcls + ' ' + (a < 0 ? 'neg' : '')}>{fmtD(a, decimals)}</td>;
                      }
                      if (s === 'budget') return <td key={key} className={'col-budget ' + tcls + ' ' + (bv != null && bv < 0 ? 'neg' : '')}>{fmtD(bv, decimals)}</td>;
                      if (s.startsWith('py')) {
                        const v = pyAggs[parseInt(s.slice(2)) - 1];
                        return <td key={key} className={'col-prior ' + tcls + ' ' + (v < 0 ? 'neg' : '')}>{fmtD(v, decimals)}</td>;
                      }
                      if (s === 'var') {
                        const v = a - (bv || 0); const r = (bv || 1) === 0 ? 0 : v / Math.abs(bv as number);
                        return <td key={key} className={'col-derived ' + tcls + ' ' + (r >= 0 ? 'pos-delta' : 'neg-delta')}>{fmtPctGrowth(r)}</td>;
                      }
                      if (s === 'grw') {
                        const py = pyAggs[0] || 0; const g = py === 0 ? null : (a - py) / Math.abs(py);
                        return <td key={key} className={'col-derived ' + tcls + ' ' + ((g || 0) >= 0 ? 'pos-delta' : 'neg-delta')}>{fmtPctGrowth(g)}</td>;
                      }
                      if (s === 'ach') {
                        const ach = (bv || 0) === 0 ? null : a / (bv as number);
                        return <td key={key} className={'col-derived ' + tcls}>{fmtPct(ach)}</td>;
                      }
                      if (s === 'pcr') {
                        const pr = rev === 0 ? null : a / rev;
                        return <td key={key} className={'col-derived ' + tcls}>{fmtPct(pr)}</td>;
                      }
                      return null;
                    });
                  })}
                  {(() => {
                    const aT = aggregate(row.monthly, monthsAll);
                    const bT = bud ? aggregate(bud.monthly, monthsAll) : null;
                    const pyT = pys.map((r) => aggregate(r.monthly, monthsAll));
                    const revT = revRow ? aggregate(revRow.monthly, monthsAll) : 0;
                    return trailingTotSubs.map((s, i) => {
                      const key = 'tot-' + s + '-' + i;
                      if (s === 'actual') return <td key={key} className={aT < 0 ? 'neg' : ''} style={{ fontWeight: 500 }}>{fmtD(aT, decimals)}</td>;
                      if (s === 'budget') return <td key={key} className="col-budget" style={{ fontWeight: 500 }}>{fmtD(bT, decimals)}</td>;
                      if (s.startsWith('py')) {
                        const v = pyT[parseInt(s.slice(2)) - 1];
                        return <td key={key} className="col-prior" style={{ fontWeight: 500 }}>{fmtD(v, decimals)}</td>;
                      }
                      if (s === 'var') {
                        const v = aT - (bT || 0); const r = (bT || 1) === 0 ? 0 : v / Math.abs(bT as number);
                        return <td key={key} className={'col-derived ' + (r >= 0 ? 'pos-delta' : 'neg-delta')} style={{ fontWeight: 500 }}>{fmtPctGrowth(r)}</td>;
                      }
                      if (s === 'grw') {
                        const py = pyT[0] || 0; const g = py === 0 ? null : (aT - py) / Math.abs(py);
                        return <td key={key} className={'col-derived ' + ((g || 0) >= 0 ? 'pos-delta' : 'neg-delta')} style={{ fontWeight: 500 }}>{fmtPctGrowth(g)}</td>;
                      }
                      if (s === 'ach') {
                        const a = (bT || 0) === 0 ? null : aT / (bT as number);
                        return <td key={key} className="col-derived" style={{ fontWeight: 500 }}>{fmtPct(a)}</td>;
                      }
                      if (s === 'pcr') {
                        const pr = revT === 0 ? null : aT / revT;
                        return <td key={key} className="col-derived" style={{ fontWeight: 500 }}>{fmtPct(pr)}</td>;
                      }
                      return null;
                    });
                  })()}
                </tr>
              );

              // Drill rows under a source row. We render the same column set
              // so the eye scans cleanly down each column. Budget and PY are
              // empty for drill rows — those metrics are stored at the P&L
              // line level, not per chart-of-accounts leaf. Derived columns
              // (Var, %Grw, %Ach) are also empty because they depend on
              // Budget / PY. % Rev is computed against the same period
              // revenue total (the parent flag's denominator), giving a
              // meaningful "this account's share of revenue" for each leaf.
              if (!isSource || !isExpanded) {
                return parentRow;
              }
              const drillRows = accounts.length === 0 ? (
                <tr key={row.key + '__drill_empty'} className="r-drill-empty">
                  <td colSpan={flat.length * subs.length + trailingTotSubs.length + 1}>
                    No accounts mapped to this row. Use the Account map tab to bind accounts to the flag <code style={{ fontSize: 11 }}>{row.flag || row.label}</code>.
                  </td>
                </tr>
              ) : (
                accounts.map((acc) => (
                  <tr key={row.key + '__drill__' + acc.account} className="r-drill">
                    <td className="r-drill-label">
                      <span className="r-drill-indent" aria-hidden />
                      <span className="r-drill-code">{acc.account_code || '—'}</span>
                      <span className="r-drill-name">{acc.account_name}</span>
                      {acc.is_group_binding_leaf && (
                        <span
                          className="r-drill-group-pill"
                          title={`Via group binding: ${acc.parent_group}`}
                        >grp</span>
                      )}
                      <button
                        className="gl-verify-btn"
                        onClick={() => openGl(row.key, (acc.account_code ? acc.account_code + ' · ' : '') + acc.account_name, aggregate(acc.monthly, monthsAll), acc.account)}
                        title={t('Verify') + ' — ' + t('GL entries')}
                      >🔍</button>
                    </td>
                    {flat.map((p) => {
                      const a = aggregate(acc.monthly, p.months);
                      const prevA = (show as any).drillcmp !== false && acc.monthly_prev ? aggregate(acc.monthly_prev, p.months) : null;
                      const rev = revRow ? aggregate(revRow.monthly, p.months) : 0;
                      const tcls = tierClass(p.tier);
                      return subs.map((s, i) => {
                        const key = p.key + '-drill-' + s + '-' + i;
                        if (s === 'actual') return <td key={key} className={tcls + ' ' + (a < 0 ? 'neg' : '')}>{fmtD(a, decimals)}</td>;
                        if (s === 'pcr') {
                          const pr = rev === 0 ? null : a / rev;
                          return <td key={key} className={'col-derived ' + tcls}>{fmtPct(pr)}</td>;
                        }
                        // v2.34.0 — account rows carry real prior-year values
                        // and growth. Budget/%ACH stay row-level (budgets are
                        // not entered per account) → em-dash.
                        if (s === 'py1' && prevA !== null) {
                          return <td key={key} className={'col-prior ' + tcls + (prevA < 0 ? ' neg' : '')}>{fmtD(prevA, decimals)}</td>;
                        }
                        if (s === 'grw' && prevA !== null) {
                          const g = prevA === 0 ? null : (a - prevA) / Math.abs(prevA);
                          return <td key={key} className={'col-derived ' + tcls + ((g ?? 0) < 0 ? ' neg' : '')}>{fmtPctGrowth(g)}</td>;
                        }
                        return <td key={key} className={subClass(s) + ' ' + tcls + ' zero'}>—</td>;
                      });
                    })}
                    {(() => {
                      const aT = aggregate(acc.monthly, monthsAll);
                      const prevT = (show as any).drillcmp !== false && acc.monthly_prev ? aggregate(acc.monthly_prev, monthsAll) : null;
                      const revT = revRow ? aggregate(revRow.monthly, monthsAll) : 0;
                      return trailingTotSubs.map((s, i) => {
                        const key = 'tot-drill-' + s + '-' + i;
                        if (s === 'actual') return <td key={key} className={aT < 0 ? 'neg' : ''}>{fmtD(aT, decimals)}</td>;
                        if (s === 'pcr') {
                          const pr = revT === 0 ? null : aT / revT;
                          return <td key={key} className="col-derived">{fmtPct(pr)}</td>;
                        }
                        if (s === 'py1' && prevT !== null) {
                          return <td key={key} className={'col-prior' + (prevT < 0 ? ' neg' : '')}>{fmtD(prevT, decimals)}</td>;
                        }
                        if (s === 'grw' && prevT !== null) {
                          const g = prevT === 0 ? null : (aT - prevT) / Math.abs(prevT);
                          return <td key={key} className={'col-derived' + ((g ?? 0) < 0 ? ' neg' : '')}>{fmtPctGrowth(g)}</td>;
                        }
                        return <td key={key} className={subClass(s) + ' zero'}>—</td>;
                      });
                    })()}
                  </tr>
                ))
              );

              return (
                <>
                  {parentRow}
                  {drillRows}
                </>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function BookInfoNote({ book }: { book: NonNullable<RunResult['filters']['compare_to_book_resolved']> }) {
  // Approved books → blue info note. Draft / submitted → amber warning so the
  // user knows the comparison numbers aren't finalized.
  const isApproved = book.status === 'approved' || book.status === 'locked';
  const sev = isApproved ? 'info' : 'warning';
  const bg = isApproved ? '#e6f1fb' : '#faeeda';
  const fg = isApproved ? '#042c53' : '#854f0b';
  const dimText =
    book.dimension_type === 'total'
      ? 'no dimension filter — actuals shown across the whole company'
      : `${book.dimension_type.replace('_', ' ')} = ${book.dimension_value} (actuals are filtered to the same)`;
  return (
    <div className={`book-info-note book-info-${sev}`} style={{ background: bg, color: fg }}>
      <i className="ti ti-info-circle" aria-hidden />
      <span>
        <strong>{book.label}</strong> selected ({book.status.toUpperCase()}) — {dimText}.
      </span>
    </div>
  );
}

function buildPivotCsv(result: PivotResult, visible: Set<string>, totalLast: boolean): string {
  const dims = result.dimensions.filter((d) => visible.has(d.name));
  const headers = ['P&L line'];
  if (!totalLast) headers.push('Total');
  dims.forEach((d) => headers.push(d.label));
  if (totalLast) headers.push('Total');
  const lines = [headers.join(',')];
  for (const r of result.rows) {
    if (r.kind === 'section') {
      lines.push(`"${r.label}"${','.repeat(headers.length - 1)}`);
      continue;
    }
    const cells = [`"${r.label.replace(/"/g, '""')}"`];
    if (!totalLast) cells.push(String(Math.round(r.total)));
    dims.forEach((d) => cells.push(String(Math.round(r.by_dim[d.name] || 0))));
    if (totalLast) cells.push(String(Math.round(r.total)));
    lines.push(cells.join(','));
  }
  return lines.join('\n');
}

function exportPivotCsv(result: PivotResult, visible: Set<string>, totalLast: boolean) {
  const csv = buildPivotCsv(result, visible, totalLast);
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${result.report.slug}-pivot-${result.filters.pivot_by}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// v2.41.0 — the Combo pivot finally exports like everything else.
function exportPivotXlsx(result: PivotResult, visible: Set<string>, totalLast: boolean) {
  import('xlsx').then((XLSX) => {
    const dims = result.dimensions.filter((d) => visible.has(d.name));
    const head = ['P&L line', ...(!totalLast ? ['Total'] : []), ...dims.map((d) => d.label), ...(totalLast ? ['Total'] : [])];
    const aoa: any[][] = [
      [`${result.report.report_name || result.report.slug} — pivot by ${result.filters.pivot_by}`],
      [`FY ${result.filters.fiscal_year}`],
      [],
      head,
    ];
    for (const r of result.rows) {
      if (r.kind === 'section') { aoa.push([r.label]); continue; }
      aoa.push([r.label,
        ...(!totalLast ? [Math.round(r.total)] : []),
        ...dims.map((d) => Math.round(r.by_dim[d.name] || 0)),
        ...(totalLast ? [Math.round(r.total)] : [])]);
    }
    const ws = XLSX.utils.aoa_to_sheet(aoa);
    ws['!cols'] = [{ wch: 42 }, ...head.slice(1).map(() => ({ wch: 16 }))];
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Pivot');
    XLSX.writeFile(wb, `${result.report.slug}-pivot-${result.filters.pivot_by}.xlsx`);
  });
}

function exportPivotPrint(result: PivotResult, visible: Set<string>, totalLast: boolean) {
  const dims = result.dimensions.filter((d) => visible.has(d.name));
  const esc = (x: any) => String(x ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const n = (v: number) => { const r = Math.round(v); return (r < 0 ? '(' : '') + Math.abs(r).toLocaleString() + (r < 0 ? ')' : ''); };
  const heads = ['P&L line', ...(!totalLast ? ['Total'] : []), ...dims.map((d) => d.label), ...(totalLast ? ['Total'] : [])];
  const headRow = '<tr>' + heads.map((h, i) => `<th class="${i ? 'num' : ''}">${esc(h)}</th>`).join('') + '</tr>';
  let body = '';
  for (const r of result.rows) {
    if (r.kind === 'section') { body += `<tr class="sec"><td colspan="${heads.length}">${esc(r.label)}</td></tr>`; continue; }
    const cls = r.kind === 'formula' ? ' class="tot"' : '';
    const cells = [`<td>${esc(r.label)}</td>`];
    if (!totalLast) cells.push(`<td class="num">${n(r.total)}</td>`);
    dims.forEach((d) => cells.push(`<td class="num">${n(r.by_dim[d.name] || 0)}</td>`));
    if (totalLast) cells.push(`<td class="num">${n(r.total)}</td>`);
    body += `<tr${cls}>` + cells.join('') + '</tr>';
  }
  const w = window.open('', '_blank'); if (!w) { alert('Pop-ups blocked.'); return; }
  let fs = 11; try { const ps = localStorage.getItem('ni-print-size') || 'auto'; fs = ps === 's' ? 8 : ps === 'l' ? 14 : ps === 'm' ? 11 : Math.max(7, Math.min(14, Math.floor(300 / heads.length))); } catch { /* */ }
  // v2.48.0 — pivot wears the Brand Kit frame too (guarded fallback)
  let bCss = ''; let bHead = ''; let bFoot = '';
  try {
    const fr = buildFrame(loadBrand((result.filters as any).company || null), {
      title: result.report.report_name || result.report.slug,
      subtitle: 'pivot by ' + result.filters.pivot_by,
      companyLabel: (result.filters as any).company || '',   // v2.48.2 — the pivot letterhead was company-less
      periodLabel: 'FY ' + result.filters.fiscal_year,
      paperOverride: 'A3', orientationOverride: 'landscape',
    });
    bCss = fr.css; bHead = fr.headerHtml; bFoot = fr.footerHtml;
  } catch { /* */ }
  w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>Pivot</title><style>
    *{-webkit-print-color-adjust:exact;print-color-adjust:exact;box-sizing:border-box;}
    ${bCss ? '' : '@page{size:A3 landscape;margin:12mm;}'} ${bCss}
    *{-webkit-print-color-adjust:exact;print-color-adjust:exact;} body{font:${fs}px/1.45 'Segoe UI',Arial,sans-serif;color:var(--th-ink,#2c2c2a);margin:20px;}
    h1{font-size:${fs + 5}px;margin:0 0 2px;color:var(--th-accent,#16404d);} .meta{color:#888;font-size:${fs - 1}px;margin-bottom:12px;}
    table{border-collapse:collapse;width:100%;} th{background:var(--th-head-bg,#16404d);color:var(--th-head-ink,#fff);text-align:start;padding:5px 8px;}
    td{border-bottom:1px solid var(--th-rule,#e6e0d4);padding:4px 8px;} .num{text-align:right;white-space:nowrap;}
    tr.sec td{background:var(--th-group,#f0ece3);font-weight:700;color:var(--th-ink,#5a5346);}
    tr.tot td{background:var(--th-total,#eef7f4);font-weight:700;color:var(--th-accent,#11816F);}
  </style></head><body>
  ${bHead ? '' : `<h1>${esc(result.report.report_name || result.report.slug)} — ${esc('pivot by ' + result.filters.pivot_by)}</h1>
  <div class="meta">FY ${esc(result.filters.fiscal_year)} · ${dims.length} ${esc('columns')}</div>`}
  <table><thead>${bHead ? bandRow(bHead, heads.length) : ''}${headRow}</thead>${bHead ? stripRow(bFoot, heads.length) : ''}<tbody>${body}</tbody></table>
  <script>window.onload=function(){setTimeout(function(){window.print();},200);}<\/script></body></html>`);
  w.document.close();
}

function copyPivotToClipboard(result: PivotResult, visible: Set<string>, totalLast: boolean) {
  // Tab-separated for paste-into-Excel
  const dims = result.dimensions.filter((d) => visible.has(d.name));
  const headers = ['P&L line'];
  if (!totalLast) headers.push('Total');
  dims.forEach((d) => headers.push(d.label));
  if (totalLast) headers.push('Total');
  const lines = [headers.join('\t')];
  for (const r of result.rows) {
    if (r.kind === 'section') {
      lines.push(r.label);
      continue;
    }
    const cells = [r.label];
    if (!totalLast) cells.push(String(Math.round(r.total)));
    dims.forEach((d) => cells.push(String(Math.round(r.by_dim[d.name] || 0))));
    if (totalLast) cells.push(String(Math.round(r.total)));
    lines.push(cells.join('\t'));
  }
  navigator.clipboard.writeText(lines.join('\n')).then(
    () => { /* could toast */ },
    () => { alert('Copy failed — please use CSV instead.'); }
  );
}

/* ─── YearsMatrix (v1.9.50) ───────────────────────────────────────────────
 *
 * Renders annual P&L totals across multiple fiscal years, side by side —
 * the canonical IFRS "Comparative Income Statement" layout. Each row label
 * appears once on the left; columns are years (oldest → newest, as convention).
 *
 * Design notes:
 *   - Row order: takes the row order from the MOST RECENT year's run (since
 *     a report's row definition can evolve; the most recent definition is
 *     the canonical structure for display).
 *   - Cells: full-year totals from each year's run. Empty cells show '—'
 *     rather than '0' to distinguish "no data" from "zero balance".
 *   - Section rows render as headers spanning all year columns.
 *   - Formula and bold rows preserve their style from the row definition.
 *
 * IMPORTANT: This view shows annual TOTALS only. Period-level filters (from/to
 * months, granularity) don't apply — each column is one full fiscal year.
 */
function YearsMatrix({ runs, decimals, report, company }: {
  runs: { year: number; result: RunResult }[];
  decimals: number;
  report: ReportDefinition | null;
  company: string;
}) {
  if (runs.length === 0) return null;
  // Sort oldest → newest as the conventional comparative layout.
  const ordered = [...runs].sort((a, b) => a.year - b.year);
  const years = ordered.map((r) => r.year);
  const mostRecent = ordered[ordered.length - 1];

  // Use the most recent run's rows as the canonical row order. If a row
  // exists in a prior year but not the latest, it WILL still render below —
  // we append it at the end to avoid silently dropping legacy data.
  const canonicalRows = mostRecent.result.current?.rows || [];
  const canonicalKeys = new Set(canonicalRows.map((r) => r.key));
  const orphanRows: typeof canonicalRows = [];
  const seenOrphan = new Set<string>();
  for (const run of ordered) {
    for (const r of (run.result.current?.rows || [])) {
      if (!canonicalKeys.has(r.key) && !seenOrphan.has(r.key)) {
        orphanRows.push(r);
        seenOrphan.add(r.key);
      }
    }
  }
  const allRows = [...canonicalRows, ...orphanRows];

  // Pre-index each year's rows by key for fast lookup.
  const yearMaps = ordered.map((y) => {
    const m: Record<string, any> = {};
    for (const r of (y.result.current?.rows || [])) m[r.key] = r;
    return m;
  });

  // Sum a row's monthly values for a given year. Returns null if the row
  // isn't present in that year (so we can render '—' instead of '0').
  const cellValue = (rowKey: string, yearIdx: number): number | null => {
    const row = yearMaps[yearIdx][rowKey];
    if (!row || !row.monthly) return null;
    let v = 0;
    for (let m = 0; m < 12; m++) v += Number(row.monthly[m] || 0);
    return v;
  };

  const currency = mostRecent.result.filters?.company_currency || '';
  const companyLabel = company || mostRecent.result.filters?.company || '';

  return (
    <div className="years-matrix-wrap">
      <div className="years-matrix-head">
        <div className="ym-co">{companyLabel}</div>
        <div className="ym-title">{report?.report_name || 'Comparative Income Statement'}</div>
        <div className="ym-period">For the years ended December 31</div>
      </div>
      <div className="years-matrix-scroll">
        <table className="years-matrix">
          <thead>
            <tr>
              <th className="ym-row-label" />
              {years.map((y) => (
                <th key={y} className="ym-year-col">
                  <div className="ym-year-num">{y}</div>
                  {currency && <div className="ym-year-cur">{currency}</div>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {allRows.map((r) => {
              const isSection = r.kind === 'section';
              const isFormula = r.kind === 'formula';
              const styleCss = rowStyleToCss(r.style);
              if (isSection) {
                return (
                  <tr key={r.key} className="ym-section">
                    <td colSpan={years.length + 1} style={styleCss}>{r.label}</td>
                  </tr>
                );
              }
              return (
                <tr key={r.key} className={isFormula ? 'ym-formula' : ''}>
                  <td className="ym-row-label" style={styleCss}>{r.label}</td>
                  {years.map((y, i) => {
                    const v = cellValue(r.key, i);
                    return (
                      <td key={y} className={'num' + (v != null && v < 0 ? ' is-neg' : '')} style={styleCss}>
                        {v == null ? '—' : fmtD(v, decimals)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="years-matrix-footnote">
        Comparative annual totals. Each column is one full fiscal year (January–December).
        Row order follows the most recent year's report definition; rows present only in earlier years are appended at the bottom.
        Cells reading <strong>—</strong> indicate the row didn't exist in that year's report definition.
      </div>
    </div>
  );
}
