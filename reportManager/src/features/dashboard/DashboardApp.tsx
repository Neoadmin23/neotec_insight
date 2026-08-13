import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { t } from '../../utils/i18n';
import { api } from '../../utils/api';
import { fmtD, fmtPct, fmtPctGrowth, aggregate, FY_RANGE, MONTHS } from '../../utils/format';
import { printManagementPack, exportDashboardXlsx } from '../../utils/export';
import { useDimFilters, compactDimFilters } from '../../utils/dimFilters';
import { ActiveDimFiltersChips } from '../ActiveDimFiltersChips';
import { LetterheadPickerModal, type LetterheadChoice } from '../LetterheadPickerModal';
import { fetchLetterhead } from '../../utils/letterhead';

/* ─── CEO Dashboard (v1.9.19) ───────────────────────────────────────────────
 * A one-screen company-health view. Pick a report + fiscal year; it runs the
 * existing run_report engine and renders a fixed set of KPI tiles — Revenue,
 * Gross Profit, EBITDA, Net Income — each with vs-budget, vs-prior-year, a
 * trend arrow and a RAG (red/amber/green) status dot.
 *
 * No new backend: this is a presentational layer over run_report.
 */

interface KpiTile {
  label: string;
  rowKey: string;
  /** show "% of revenue" line (not meaningful for Revenue itself) */
  ofRevenue: boolean;
}

// Fixed KPI set — resolved by row key, the stable identifiers the engine uses.
const KPI_TILES: KpiTile[] = [
  { label: 'Revenue', rowKey: 'total_revenue', ofRevenue: false },
  { label: 'Gross Profit', rowKey: 'gross_profit', ofRevenue: true },
  { label: 'EBITDA', rowKey: 'ebitda', ofRevenue: true },
  { label: 'Net Income', rowKey: 'net_income', ofRevenue: true },
];

type Rag = 'green' | 'amber' | 'red' | 'none';

/** RAG from achievement vs budget: >=100% green, 90-100% amber, <90% red. */
function ragForAchievement(ratio: number | null): Rag {
  if (ratio == null || !isFinite(ratio)) return 'none';
  if (ratio >= 1) return 'green';
  if (ratio >= 0.9) return 'amber';
  return 'red';
}

/** Tiny inline SVG sparkline — 12 monthly points, no axes. */
function Sparkline({ data }: { data: number[] }) {
  const w = 132, h = 30, pad = 2;
  if (!data || data.length === 0) return null;
  const min = Math.min(...data, 0);
  const max = Math.max(...data, 0);
  const span = max - min || 1;
  const stepX = (w - pad * 2) / Math.max(1, data.length - 1);
  const pts = data.map((v, i) => {
    const x = pad + i * stepX;
    const y = h - pad - ((v - min) / span) * (h - pad * 2);
    return [x, y] as [number, number];
  });
  const path = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const zeroY = h - pad - ((0 - min) / span) * (h - pad * 2);
  const last = pts[pts.length - 1];
  const rising = data[data.length - 1] >= data[0];
  const stroke = rising ? '#0f6e56' : '#a32d2d';
  return (
    <svg className="dash-spark" viewBox={`0 0 ${w} ${h}`} width={w} height={h} aria-hidden>
      {min < 0 && max > 0 && (
        <line x1={pad} y1={zeroY} x2={w - pad} y2={zeroY} stroke="#d9d6d0" strokeWidth="1" strokeDasharray="2 2" />
      )}
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last[0]} cy={last[1]} r="2.4" fill={stroke} />
    </svg>
  );
}

export function DashboardApp() {
  const [reports, setReports] = useState<any[]>([]);
  const [report, setReport] = useState('');
  const [fy, setFy] = useState<number>(new Date().getFullYear());
  const [run, setRun] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Liquidity block — fetched once the run resolves the company.
  const [liquidity, setLiquidity] = useState<any>(null);

  // v1.9.52 — honour active custom Accounting Dimensions filters set in the
  // Run tab. Dashboard reads them; the Run tab is the writer.
  const { filters: dimFilters, dimensions: activeDims } = useDimFilters();

  // v1.9.53 — Letter Head picker for Management Pack + Excel exports.
  const [lhPickerOpen, setLhPickerOpen] = useState(false);
  const [lhPendingAction, setLhPendingAction] = useState<null | 'pack' | 'xlsx'>(null);
  function initiateExport(action: 'pack' | 'xlsx') {
    if (!run) return;
    setLhPendingAction(action);
    setLhPickerOpen(true);
  }
  async function completeExport(choice: LetterheadChoice) {
    setLhPickerOpen(false);
    const action = lhPendingAction;
    setLhPendingAction(null);
    if (!run || !action) return;
    const lh = choice.withoutLetterhead
      ? undefined
      : await fetchLetterhead(choice.name, run.filters?.company);
    if (action === 'pack') {
      printManagementPack(buildPackData(), lh);
    } else if (action === 'xlsx') {
      exportDashboardXlsx(buildPackData(),
        `dashboard_${(run.filters?.company || 'company').replace(/\s+/g, '_')}_FY${fy}.xlsx`,
        lh);
    }
  }
  function cancelExport() {
    setLhPickerOpen(false);
    setLhPendingAction(null);
  }
  // Financial ratios — same company/FY trigger.
  const [ratios, setRatios] = useState<any>(null);
  // Forward-projection horizon — 3 / 6 / 9 / 12 months.
  const [projMonths, setProjMonths] = useState<number>(6);
  // Forward-projection expense baseline — committed only, prior-year same
  // period, or trailing-3-month average. v1.9.28.
  const [projBaseline, setProjBaseline] = useState<string>('committed');
  // Collection-likelihood — best_case keeps today's behaviour; realistic
  // haircuts receivables by ageing bucket. v1.9.29.
  const [collMode, setCollMode] = useState<string>('best_case');
  // User-tunable schedule overrides — empty = defaults from backend.
  const [collSchedule, setCollSchedule] = useState<Record<string, { pct: number; weights: number[] }> | undefined>(undefined);
  // Payment-timing schedule overrides (v1.9.30) — payables, no haircut, only timing.
  const [paySchedule, setPaySchedule] = useState<Record<string, { weights: number[] }> | undefined>(undefined);
  // v1.9.32 — chart/table view mode for variance (other blocks own their own state).
  const [varianceView, setVarianceView] = useState<'table' | 'chart'>('table');
  // v1.9.36 — variance commentary: per-row notes keyed by row_key.
  const [varianceNotes, setVarianceNotes] = useState<Record<string, { name: string; commentary: string; modified: string; modified_by: string }>>({});
  // v1.9.37 — sparkline trend basis: 'ytd' (current behaviour) or 'rolling_12'.
  const [trendBasis, setTrendBasis] = useState<'ytd' | 'rolling_12'>('ytd');
  // Rolling-12 series keyed by row_key, plus the month labels for tooltips.
  const [rolling12, setRolling12] = useState<{ rows: Record<string, number[]>; months: Array<{ label: string }> } | null>(null);
  const [openVarianceEditor, setOpenVarianceEditor] = useState<string | null>(null);
  // Debounced auto-save for variance commentary — saves 1s after typing stops.
  const noteSaveTimers = useRef<Record<string, number | undefined>>({});
  function saveVarianceNote(rowKey: string, text: string) {
    // Update local state immediately so the textarea stays responsive.
    setVarianceNotes((prev) => {
      const next = { ...prev };
      if (text.trim()) {
        const existing = next[rowKey];
        next[rowKey] = {
          name: existing?.name || '',
          commentary: text,
          modified: existing?.modified || new Date().toISOString(),
          modified_by: existing?.modified_by || '',
        };
      } else {
        delete next[rowKey];
      }
      return next;
    });
    // Debounce the backend save by 1 second.
    const timers = noteSaveTimers.current;
    if (timers[rowKey]) window.clearTimeout(timers[rowKey]);
    timers[rowKey] = window.setTimeout(() => {
      const reportSlug = run?.report?.slug || run?.report?.name || report;
      api.saveVarianceNote(reportSlug, rowKey, fy, text)
        .then((res: any) => {
          if (!res || res.deleted) return;
          // Refresh local note metadata (author/modified) from server.
          setVarianceNotes((prev) => ({
            ...prev,
            [rowKey]: {
              name: res.name,
              commentary: res.commentary,
              modified: res.modified,
              modified_by: res.modified_by,
            },
          }));
        })
        .catch(() => { /* leave local state; user can retry by typing */ });
    }, 1000) as unknown as number;
  }

  // Load the report list once.
  useEffect(() => {
    api.listReports()
      .then((rs) => {
        setReports(rs || []);
        // Default to the first definition-based (pnl) report.
        const first = (rs || []).find((r: any) => !r.report_type || r.report_type === 'pnl') || (rs || [])[0];
        if (first) setReport(first.slug || first.name);
      })
      .catch(() => setReports([]));
  }, []);

  // Run the report whenever report or FY changes.
  useEffect(() => {
    if (!report) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    setLiquidity(null);
    setRatios(null);
    setVarianceNotes({});
    api.runReport({
      report,
      fiscal_year: fy,
      month_from: 0,
      month_to: 11,
      segment: 'total',
      prior_years: 1,
      comparison_mode: 'vs_budget',
      granularity: 'month',
      dimension_filters: compactDimFilters(dimFilters) || null,
    })
      .then((r) => { if (!cancelled) setRun(r); })
      .catch((e) => { if (!cancelled) setError(e?.message || 'Could not load the dashboard.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [report, fy, dimFilters]);

  // Fetch the liquidity block once the run payload resolves the company.
  // run.filters.company is the report's actual company (segment is just the
  // dimension token — often 'total' — and must NOT be used as the company).
  // Re-fetches when the projection horizon changes.
  useEffect(() => {
    const company = run?.filters?.company;
    if (!company || typeof company !== 'string') return;
    let cancelled = false;
    api.getLiquidity(company, fy, projMonths, projBaseline, collMode, collSchedule, paySchedule, compactDimFilters(dimFilters) || null)
      .then((l) => { if (!cancelled) setLiquidity(l); })
      .catch(() => { if (!cancelled) setLiquidity(null); });
    api.getFinancialRatios(company, fy)
      .then((r) => { if (!cancelled) setRatios(r); })
      .catch(() => { if (!cancelled) setRatios(null); });
    // v1.9.36 — load any variance commentary for this report+year.
    api.listVarianceNotes(report, fy)
      .then((notes) => {
        if (cancelled) return;
        const map: Record<string, any> = {};
        for (const n of (notes || [])) map[n.row_key] = n;
        setVarianceNotes(map);
      })
      .catch(() => { if (!cancelled) setVarianceNotes({}); });
    // v1.9.37 — load rolling-12 only when that basis is selected, to keep
    // the dashboard fast on first paint.
    if (trendBasis === 'rolling_12') {
      api.getRolling12(report, fy)
        .then((r) => {
          if (cancelled) return;
          const byKey: Record<string, number[]> = {};
          for (const row of (r?.rows || [])) byKey[row.key] = row.series || [];
          setRolling12({ rows: byKey, months: r?.months || [] });
        })
        .catch(() => { if (!cancelled) setRolling12(null); });
    } else {
      setRolling12(null);
    }
    return () => { cancelled = true; };
  }, [run, fy, projMonths, projBaseline, collMode, collSchedule, paySchedule, trendBasis, dimFilters]);

  const monthsAll = useMemo(() => Array.from({ length: 12 }, (_, i) => i), []);

  // Resolve each KPI tile's numbers from the run payload.
  const tiles = useMemo(() => {
    if (!run) return [];
    const findRow = (rows: any[] | undefined, key: string) =>
      rows?.find((x: any) => x.key === key);
    return KPI_TILES.map((t) => {
      const cur = findRow(run.current?.rows, t.rowKey);
      const bud = findRow(run.budget?.rows, t.rowKey);
      const py = findRow(run.priors?.[0]?.rows, t.rowKey);
      const val = cur ? aggregate(cur.monthly, monthsAll) : 0;
      const budV = bud ? aggregate(bud.monthly, monthsAll) : null;
      const pyV = py ? aggregate(py.monthly, monthsAll) : null;
      const revRow = findRow(run.current?.rows, 'total_revenue');
      const revV = revRow ? aggregate(revRow.monthly, monthsAll) : 0;
      const achievement = budV && budV !== 0 ? val / budV : null;
      const growth = pyV && pyV !== 0 ? (val - pyV) / Math.abs(pyV) : null;
      // Sparkline series — fiscal-YTD (12 current-FY monthly points) by
      // default; switches to the stitched trailing-12 when 'Rolling-12' is on
      // AND the backend has loaded rolling-12 data for this report+FY.
      let series: number[];
      if (trendBasis === 'rolling_12' && rolling12?.rows?.[t.rowKey]) {
        series = rolling12.rows[t.rowKey];
      } else {
        series = monthsAll.map((m) => Number(cur?.monthly?.[m] || 0));
      }
      return {
        ...t, val, budV, pyV, revV, achievement, growth, series,
        rag: ragForAchievement(achievement),
      };
    });
  }, [run, monthsAll, trendBasis, rolling12]);

  // Ranked variance — every source/formula row's actual-vs-budget gap, sorted
  // by the largest shortfall first. Surfaces "where did we miss plan."
  const variance = useMemo(() => {
    if (!run || !run.budget) return [];
    const budByKey: Record<string, any> = {};
    for (const r of run.budget.rows || []) budByKey[r.key] = r;
    const out: Array<{
      key: string; label: string; actual: number; budget: number;
      gap: number; gapPct: number | null;
    }> = [];
    for (const r of run.current?.rows || []) {
      if (r.kind === 'section') continue;
      const b = budByKey[r.key];
      if (!b) continue;
      const actual = aggregate(r.monthly, monthsAll);
      const budget = aggregate(b.monthly, monthsAll);
      if (budget === 0 && actual === 0) continue;
      const gap = actual - budget;
      const gapPct = budget !== 0 ? gap / Math.abs(budget) : null;
      out.push({ key: r.key, label: r.label, actual, budget, gap, gapPct });
    }
    // Largest absolute gap first. Keep up to 15; the view decides how many show.
    out.sort((a, b) => Math.abs(b.gap) - Math.abs(a.gap));
    return out.slice(0, 15);
  }, [run, monthsAll]);

  const priorFy = fy - 1;
  // v1.9.23 — CEO vs CFO view. CEO: health-first, concise. CFO: liquidity and
  // variance first, deeper variance list.
  const [view, setView] = useState<'ceo' | 'cfo'>('ceo');
  const varianceShown = view === 'cfo' ? variance : variance.slice(0, 8);

  // Assemble the shared data object for both exports (pack PDF + colour Excel).
  function buildPackData() {
    return {
      company: run?.filters?.company || '',
      fiscalYear: fy,
      reportName: run?.report?.report_name || report,
      tiles: tiles.map((t) => ({
        label: t.label, val: t.val,
        achievement: t.achievement, growth: t.growth, rag: t.rag,
      })),
      ratios,
      liquidity,
      variance,
      priorFy,
      varianceNotes,
      trendBasis,
    };
  }

  return (
    <div className="dash">
      <div className="dash-head">
        <div>
          <h2 className="dash-title">{view === 'cfo' ? 'CFO View' : 'Company Health'}</h2>
          <div className="dash-sub">
            {view === 'cfo'
              ? 'Liquidity, cash projection and variance — the operating detail.'
              : 'A one-screen view of where the business stands this year.'}
          </div>
          <ActiveDimFiltersChips />
        </div>
        <div className="dash-controls">
          <div className="dash-viewtoggle">
            <button
              className={'dash-vt-btn' + (view === 'ceo' ? ' on' : '')}
              onClick={() => setView('ceo')}
            >{t('CEO')}</button>
            <button
              className={'dash-vt-btn' + (view === 'cfo' ? ' on' : '')}
              onClick={() => setView('cfo')}
            >{t('CFO')}</button>
          </div>
          <div className="dash-viewtoggle" title="Sparkline trend basis on the KPI tiles">
            <button
              className={'dash-vt-btn' + (trendBasis === 'ytd' ? ' on' : '')}
              onClick={() => setTrendBasis('ytd')}
              title="Sparkline shows the selected fiscal year so far (Jan→Dec of the chosen FY)"
            >{t('YTD')}</button>
            <button
              className={'dash-vt-btn' + (trendBasis === 'rolling_12' ? ' on' : '')}
              onClick={() => setTrendBasis('rolling_12')}
              title="Sparkline shows the trailing 12 months ending at the latest data — stitched across fiscal years"
            >R-12</button>
          </div>
          <label>
            <span className="flbl">{t('Report')}</span>
            <select value={report} onChange={(e) => setReport(e.target.value)}>
              {reports
                .filter((r) => !r.report_type || r.report_type === 'pnl')
                .map((r) => (
                  <option key={r.slug || r.name} value={r.slug || r.name}>
                    {r.report_name || r.name}
                  </option>
                ))}
            </select>
          </label>
          <label>
            <span className="flbl">{t('Fiscal Year')}</span>
            <select value={fy} onChange={(e) => setFy(Number(e.target.value))}>
              {FY_RANGE.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          </label>
          <button
            className="dash-pack-btn"
            disabled={!run || loading}
            title="Generate a bound, print-ready management pack"
            onClick={() => { if (!run) return; initiateExport('pack'); }}
          >
            <i className="ti ti-file-text" aria-hidden /> Management Pack
          </button>
          <button
            className="dash-pack-btn dash-xlsx-btn"
            disabled={!run || loading}
            title="Export the whole dashboard to a colour Excel workbook"
            onClick={() => { if (!run) return; initiateExport('xlsx'); }}
          >
            <i className="ti ti-file-spreadsheet" aria-hidden /> Export Excel
          </button>
        </div>
      </div>

      {error && <div className="run-error">{error}</div>}
      {loading && <div className="dash-loading">Loading company health…</div>}

      {!loading && !error && run && (
        <>
          <div className="dash-tiles">
            {tiles.map((t) => (
              <div className={'dash-tile rag-' + t.rag} key={t.rowKey}>
                <div className="dash-tile-head">
                  <span className="dash-tile-label">{t.label}</span>
                  {t.rag !== 'none' && <span className={'dash-rag rag-dot-' + t.rag} title={'Status: ' + t.rag} />}
                </div>
                <div className="dash-tile-val" style={{ color: t.val < 0 ? 'var(--neg, #a32d2d)' : undefined }}>
                  {fmtD(t.val, 0)}
                </div>
                <Sparkline data={t.series} />
                <div className="dash-tile-metrics">
                  {t.achievement != null && (
                    <div className={'dash-metric ' + (t.achievement >= 1 ? 'up' : 'down')}>
                      <i className={'ti ti-' + (t.achievement >= 1 ? 'arrow-up-right' : 'arrow-down-right')} aria-hidden />
                      {fmtPct(t.achievement)} of budget
                    </div>
                  )}
                  {t.growth != null && (
                    <div className={'dash-metric ' + (t.growth >= 0 ? 'up' : 'down')}>
                      <i className={'ti ti-' + (t.growth >= 0 ? 'trending-up' : 'trending-down')} aria-hidden />
                      {fmtPctGrowth(t.growth)} vs FY{priorFy}
                    </div>
                  )}
                  {t.ofRevenue && t.revV !== 0 && (
                    <div className="dash-metric muted">
                      {fmtPct(t.val / t.revV)} of revenue
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>

          <div className="dash-legend">
            <span><span className="rag-dot-green dash-rag" /> On or above budget</span>
            <span><span className="rag-dot-amber dash-rag" /> Within 10% of budget</span>
            <span><span className="rag-dot-red dash-rag" /> More than 10% below budget</span>
          </div>

          {(() => {
            const variancePanel = varianceShown.length > 0 ? (
              <div className="dash-variance" key="variance">
                <div className="dash-variance-head dash-block-head">
                  <div>
                    <h3 className="dash-variance-title">{t('Biggest variances vs budget')}</h3>
                    <span className="dash-sub">The lines that moved furthest from plan — largest gap first.</span>
                  </div>
                  <ViewModeToggle mode={varianceView} onChange={setVarianceView} />
                </div>
                {varianceView === 'chart' ? (
                  <VarianceChart rows={varianceShown} />
                ) : (
                  <table className="dash-variance-table">
                    <thead>
                      <tr>
                        <th>{t('Line')}</th>
                        <th className="num">{t('Actual')}</th>
                        <th className="num">{t('Budget')}</th>
                        <th className="num">Variance</th>
                        <th className="num">%</th>
                      </tr>
                    </thead>
                    <tbody>
                      {varianceShown.map((v) => {
                        const over = v.gap >= 0;
                        const note = varianceNotes[v.key];
                        const hasNote = !!note?.commentary;
                        const isOpen = openVarianceEditor === v.key;
                        return (
                          <Fragment key={v.key}>
                            <tr>
                              <td>
                                <div className="var-label">
                                  <span>{v.label}</span>
                                  <button
                                    className={'var-note-btn' + (hasNote ? ' has-note' : '')}
                                    onClick={() => setOpenVarianceEditor(isOpen ? null : v.key)}
                                    title={hasNote ? 'Edit commentary' : 'Add commentary'}
                                  >
                                    <i className={'ti ti-' + (hasNote ? 'message-circle-2' : 'message-plus')} aria-hidden />
                                  </button>
                                </div>
                              </td>
                              <td className="num">{fmtD(v.actual, 0)}</td>
                              <td className="num">{fmtD(v.budget, 0)}</td>
                              <td className={'num ' + (over ? 'var-over' : 'var-under')}>
                                {over ? '+' : ''}{fmtD(v.gap, 0)}
                              </td>
                              <td className={'num ' + (over ? 'var-over' : 'var-under')}>
                                {v.gapPct == null ? '—' : (over ? '+' : '') + fmtPct(v.gapPct)}
                              </td>
                            </tr>
                            {(hasNote || isOpen) && (
                              <tr className="var-note-row">
                                <td colSpan={5}>
                                  <VarianceNoteEditor
                                    rowKey={v.key}
                                    note={note}
                                    open={isOpen}
                                    onChange={(text) => saveVarianceNote(v.key, text)}
                                    onClose={() => setOpenVarianceEditor(null)}
                                  />
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                )}
                <div className="dash-sub" style={{ marginTop: 8 }}>
                  Green = ahead of budget · Red = behind budget. Showing the top {varianceShown.length} by gap size.
                </div>
              </div>
            ) : null;

            const liquidityPanel = liquidity
              ? <LiquidityBlock liquidity={liquidity} projMonths={projMonths} onProjMonths={setProjMonths} projBaseline={projBaseline} onProjBaseline={setProjBaseline} collMode={collMode} onCollMode={setCollMode} collSchedule={collSchedule} onCollSchedule={setCollSchedule} paySchedule={paySchedule} onPaySchedule={setPaySchedule} key="liquidity" />
              : null;

            const ratiosPanel = ratios
              ? <RatiosBlock ratios={ratios} key="ratios" />
              : null;

            // v1.9.39 — sensitivity stress test. Renders only when liquidity
            // is loaded (sensitivity stress-tests the projection, which lives
            // inside liquidity).
            const sensitivityPanel = liquidity
              ? <SensitivityBlock
                  company={run?.filters?.company || ''}
                  fy={fy}
                  projMonths={projMonths}
                  projBaseline={projBaseline}
                  collMode={collMode}
                  currency={liquidity?.currency || ''}
                  key="sensitivity"
                />
              : null;

            // CFO view leads with ratios + liquidity (the operating health);
            // CEO view leads with variance (the plan story), ratios last.
            return view === 'cfo'
              ? <>{ratiosPanel}{liquidityPanel}{sensitivityPanel}{variancePanel}</>
              : <>{variancePanel}{liquidityPanel}{sensitivityPanel}{ratiosPanel}</>;
          })()}
        </>
      )}

      {/* v1.9.53 — Letter Head picker. Opens on Management Pack / Excel export. */}
      <LetterheadPickerModal
        open={lhPickerOpen}
        report={report}
        company={run?.filters?.company}
        actionLabel={lhPendingAction === 'pack' ? 'Generate Management Pack' : 'Export to Excel'}
        onConfirm={completeExport}
        onCancel={cancelExport}
      />
    </div>
  );
}

/* ─── Liquidity block (v1.9.21) ─────────────────────────────────────────────
 * Cash movement month-by-month + receivables ageing. The liquidity core a
 * Big-Four management pack leads with.
 */
function LiquidityBlock({ liquidity, projMonths, onProjMonths, projBaseline, onProjBaseline, collMode, onCollMode, collSchedule, onCollSchedule, paySchedule, onPaySchedule }: {
  liquidity: any;
  projMonths: number;
  onProjMonths: (m: number) => void;
  projBaseline: string;
  onProjBaseline: (b: string) => void;
  collMode: string;
  onCollMode: (m: string) => void;
  collSchedule: Record<string, { pct: number; weights: number[] }> | undefined;
  onCollSchedule: (s: Record<string, { pct: number; weights: number[] }> | undefined) => void;
  paySchedule: Record<string, { weights: number[] }> | undefined;
  onPaySchedule: (s: Record<string, { weights: number[] }> | undefined) => void;
}) {
  const cash = liquidity.cash_monthly || [];
  // v1.9.32 — chart/table view per ageing block, local to LiquidityBlock.
  const [recView, setRecView] = useState<'table' | 'chart'>('table');
  const [payView, setPayView] = useState<'table' | 'chart'>('table');
  // v1.9.33 — chart style for the ageing blocks (when chart mode is on).
  const [recStyle, setRecStyle] = useState<'bar' | 'donut' | 'pie'>('bar');
  const [payStyle, setPayStyle] = useState<'bar' | 'donut' | 'pie'>('bar');
  const rec = liquidity.receivables || { total: 0, not_due: 0, buckets: [] };
  const closingSeries = cash.map((c: any) => Number(c.closing || 0));
  const latestClosing = cash.length ? cash[cash.length - 1].closing : liquidity.cash_opening || 0;
  const totalInflow = cash.reduce((s: number, c: any) => s + (c.inflow || 0), 0);
  const totalOutflow = cash.reduce((s: number, c: any) => s + (c.outflow || 0), 0);
  const recTotal = rec.total || 0;
  const overdue = (rec.buckets || []).reduce((s: number, b: any) => s + (b.amount || 0), 0);
  const pay = liquidity.payables || null;
  const payTotal = pay ? (pay.total || 0) : 0;

  return (
    <div className="dash-liquidity">
      <div className="dash-variance-head">
        <h3 className="dash-variance-title">{t('Liquidity')}</h3>
        <span className="dash-sub">
          Cash movement and what customers owe — the working-capital picture.
        </span>
      </div>

      {/* Cash summary tiles */}
      <div className="dash-liq-summary">
        <div className="dash-liq-stat">
          <span className="dash-liq-stat-lbl">{t('Cash on hand')}</span>
          <span className="dash-liq-stat-val" style={{ color: latestClosing < 0 ? 'var(--neg,#a32d2d)' : undefined }}>
            {fmtD(latestClosing, 0)}
          </span>
          <Sparkline data={closingSeries} />
        </div>
        <div className="dash-liq-stat">
          <span className="dash-liq-stat-lbl">Cash in (year)</span>
          <span className="dash-liq-stat-val up-text">{fmtD(totalInflow, 0)}</span>
        </div>
        <div className="dash-liq-stat">
          <span className="dash-liq-stat-lbl">Cash out (year)</span>
          <span className="dash-liq-stat-val down-text">{fmtD(totalOutflow, 0)}</span>
        </div>
        <div className="dash-liq-stat">
          <span className="dash-liq-stat-lbl">Receivables outstanding</span>
          <span className="dash-liq-stat-val">{fmtD(recTotal, 0)}</span>
          {recTotal > 0 && (
            <span className="dash-sub">{fmtPct(overdue / recTotal)} overdue</span>
          )}
        </div>
        {pay && (
          <div className="dash-liq-stat">
            <span className="dash-liq-stat-lbl">Payables outstanding</span>
            <span className="dash-liq-stat-val">{fmtD(payTotal, 0)}</span>
            <span className="dash-sub">
              Net position {fmtD(recTotal - payTotal, 0)}
            </span>
          </div>
        )}
      </div>

      {/* Month-by-month cash table */}
      {cash.length > 0 && (
        <table className="dash-variance-table dash-liq-table">
          <thead>
            <tr>
              <th>{t('Month')}</th>
              <th className="num">{t('Opening')}</th>
              <th className="num">{t('Cash In')}</th>
              <th className="num">{t('Cash Out')}</th>
              <th className="num">{t('Closing')}</th>
            </tr>
          </thead>
          <tbody>
            {cash.map((c: any) => (
              <tr key={c.month}>
                <td>{MONTHS[typeof c.cal_month === 'number' ? c.cal_month : c.month]}</td>
                <td className="num">{fmtD(c.opening, 0)}</td>
                <td className="num up-text">{fmtD(c.inflow, 0)}</td>
                <td className="num down-text">{fmtD(c.outflow, 0)}</td>
                <td className="num" style={{ fontWeight: 600, color: c.closing < 0 ? 'var(--neg,#a32d2d)' : undefined }}>
                  {fmtD(c.closing, 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {cash.length === 0 && (
        <div className="dash-sub" style={{ padding: '8px 0' }}>
          No Cash or Bank accounts found for this company, so cash movement can't be shown.
        </div>
      )}

      {/* Receivables ageing */}
      <div className="dash-liq-ageing-head dash-block-head">
        <span>Receivables ageing — by days past due</span>
        <div className="dash-block-head-tools">
          {recView === 'chart' && (
            <ChartStyleSelector
              value={recStyle}
              onChange={setRecStyle}
              options={[
                { key: 'bar', label: 'Stacked bar', icon: 'layout-rows' },
                { key: 'donut', label: 'Donut', icon: 'chart-donut' },
                { key: 'pie', label: 'Pie', icon: 'chart-pie' },
              ]}
            />
          )}
          <ViewModeToggle mode={recView} onChange={setRecView} />
        </div>
      </div>
      {recView === 'chart' ? (
        recStyle === 'bar'
          ? <AgeingChart notDue={rec.not_due || 0} buckets={rec.buckets || []} label="receivables" />
          : <AgeingPie notDue={rec.not_due || 0} buckets={rec.buckets || []} hole={recStyle === 'donut' ? 0.55 : 0} />
      ) : (
        <div className="dash-liq-ageing">
          <div className="dash-liq-age-cell">
            <span className="dash-liq-age-lbl">{t('Not yet due')}</span>
            <span className="dash-liq-age-val">{fmtD(rec.not_due || 0, 0)}</span>
          </div>
          {(rec.buckets || []).map((b: any) => (
            <div className="dash-liq-age-cell" key={b.key}>
              <span className="dash-liq-age-lbl">{b.label} days</span>
              <span className={'dash-liq-age-val' + (b.amount > 0 ? ' is-overdue' : '')}>
                {fmtD(b.amount || 0, 0)}
              </span>
            </div>
          ))}
          <div className="dash-liq-age-cell is-total">
            <span className="dash-liq-age-lbl">{t('Total')}</span>
            <span className="dash-liq-age-val">{fmtD(recTotal, 0)}</span>
          </div>
        </div>
      )}

      {pay && (
        <>
          <div className="dash-liq-ageing-head dash-block-head">
            <span>Payables ageing — by days past due</span>
            <div className="dash-block-head-tools">
              {payView === 'chart' && (
                <ChartStyleSelector
                  value={payStyle}
                  onChange={setPayStyle}
                  options={[
                    { key: 'bar', label: 'Stacked bar', icon: 'layout-rows' },
                    { key: 'donut', label: 'Donut', icon: 'chart-donut' },
                    { key: 'pie', label: 'Pie', icon: 'chart-pie' },
                  ]}
                />
              )}
              <ViewModeToggle mode={payView} onChange={setPayView} />
            </div>
          </div>
          {payView === 'chart' ? (
            payStyle === 'bar'
              ? <AgeingChart notDue={pay.not_due || 0} buckets={pay.buckets || []} label="payables" />
              : <AgeingPie notDue={pay.not_due || 0} buckets={pay.buckets || []} hole={payStyle === 'donut' ? 0.55 : 0} />
          ) : (
            <div className="dash-liq-ageing">
              <div className="dash-liq-age-cell">
                <span className="dash-liq-age-lbl">{t('Not yet due')}</span>
                <span className="dash-liq-age-val">{fmtD(pay.not_due || 0, 0)}</span>
              </div>
              {(pay.buckets || []).map((b: any) => (
                <div className="dash-liq-age-cell" key={b.key}>
                  <span className="dash-liq-age-lbl">{b.label} days</span>
                  <span className={'dash-liq-age-val' + (b.amount > 0 ? ' is-overdue' : '')}>
                    {fmtD(b.amount || 0, 0)}
                  </span>
                </div>
              ))}
              <div className="dash-liq-age-cell is-total">
                <span className="dash-liq-age-lbl">{t('Total')}</span>
                <span className="dash-liq-age-val">{fmtD(payTotal, 0)}</span>
              </div>
            </div>
          )}
        </>
      )}

      <div className="dash-sub" style={{ marginTop: 8 }}>
        Cash is the movement across {liquidity.cash_accounts_count} Bank/Cash account(s).
        Receivables is what customers owe; payables is what you owe suppliers — each bucketed by days past due date.
      </div>

      {liquidity.projection && liquidity.projection.rows?.length > 0 && (
        <ProjectionView projection={liquidity.projection} projMonths={projMonths} onProjMonths={onProjMonths} projBaseline={projBaseline} onProjBaseline={onProjBaseline} collMode={collMode} onCollMode={onCollMode} collSchedule={collSchedule} onCollSchedule={onCollSchedule} paySchedule={paySchedule} onPaySchedule={onPaySchedule} />
      )}
    </div>
  );
}

/* ─── Forward cash projection (v1.9.22) ─────────────────────────────────────
 * Projects cash for the next 6 months: current cash + expected receivable
 * collections − known payables, both placed in the month of their due date.
 * A committed-items (AR/AP) forecast — honest about what it does and doesn't
 * include.
 */
function ProjectionView({ projection, projMonths, onProjMonths, projBaseline, onProjBaseline, collMode, onCollMode, collSchedule, onCollSchedule, paySchedule, onPaySchedule }: {
  projection: any;
  projMonths: number;
  onProjMonths: (m: number) => void;
  projBaseline: string;
  onProjBaseline: (b: string) => void;
  collMode: string;
  onCollMode: (m: string) => void;
  collSchedule: Record<string, { pct: number; weights: number[] }> | undefined;
  onCollSchedule: (s: Record<string, { pct: number; weights: number[] }> | undefined) => void;
  paySchedule: Record<string, { weights: number[] }> | undefined;
  onPaySchedule: (s: Record<string, { weights: number[] }> | undefined) => void;
}) {
  const rows = projection.rows || [];
  // v1.9.32 — chart/table view of the projection.
  const [projView, setProjView] = useState<'table' | 'chart'>('table');
  // v1.9.33 — chart style: line or bar (both legit for time series).
  const [projStyle, setProjStyle] = useState<'line' | 'bar'>('line');
  const closings = rows.map((r: any) => Number(r.closing || 0));
  const lowest = closings.length ? Math.min(...closings) : 0;
  const lowestIdx = closings.indexOf(lowest);
  const endCash = closings.length ? closings[closings.length - 1] : 0;
  const startCash = projection.current_cash || 0;
  const goesNegative = lowest < 0;
  // Did any baseline expense actually layer in? Show breakdown only if so.
  const hasBaseline = rows.some((r: any) => (r.baseline_out || 0) > 0);

  const monthLabel = (r: any) => MONTHS[r.month] + ' ' + String(r.year).slice(2);

  return (
    <div className="dash-projection">
      <div className="dash-variance-head dash-proj-head" style={{ marginTop: 18 }}>
        <div>
          <div className="dash-block-head" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h3 className="dash-variance-title" style={{ margin: 0 }}>Forward cash projection — next {projection.months} months</h3>
            <div className="dash-block-head-tools">
              {projView === 'chart' && (
                <ChartStyleSelector
                  value={projStyle}
                  onChange={setProjStyle}
                  options={[
                    { key: 'line', label: 'Line', icon: 'chart-line' },
                    { key: 'bar', label: 'Bar', icon: 'chart-bar' },
                  ]}
                />
              )}
              <ViewModeToggle mode={projView} onChange={setProjView} />
            </div>
          </div>
          <span className="dash-sub">
            Where cash is heading, based on receivable collections and payables due
            {projBaseline !== 'committed' && (projBaseline === 'prior_year' ? ', plus prior-year expenses' : ', plus a 3-month average expense baseline')}.
          </span>
        </div>
        <div className="dash-proj-controls">
          <div className="dash-proj-horizon">
            <span className="flbl">{t('Horizon')}</span>
            <div className="dash-proj-hbtns">
              {[3, 6, 9, 12].map((m) => (
                <button
                  key={m}
                  className={'dash-proj-hbtn' + (projMonths === m ? ' on' : '')}
                  onClick={() => onProjMonths(m)}
                >{m}m</button>
              ))}
            </div>
          </div>
          <div className="dash-proj-horizon">
            <span className="flbl">{t('Expense baseline')}</span>
            <div className="dash-proj-hbtns">
              <button
                className={'dash-proj-hbtn' + (projBaseline === 'committed' ? ' on' : '')}
                onClick={() => onProjBaseline('committed')}
                title="Use only invoices that already exist — most conservative"
              >{t('Committed only')}</button>
              <button
                className={'dash-proj-hbtn' + (projBaseline === 'prior_year' ? ' on' : '')}
                onClick={() => onProjBaseline('prior_year')}
                title="Add same-period prior-year expenses on top of committed payables — best for seasonal businesses"
              >Prior year</button>
              <button
                className={'dash-proj-hbtn' + (projBaseline === 'trailing_3m' ? ' on' : '')}
                onClick={() => onProjBaseline('trailing_3m')}
                title="Add the trailing 3-month average expense on top of committed payables — best when current run-rate matters more than seasonality"
              >Trailing 3m</button>
            </div>
          </div>
          <div className="dash-proj-horizon">
            <span className="flbl">{t('Collections')}</span>
            <div className="dash-proj-hbtns">
              <button
                className={'dash-proj-hbtn' + (collMode === 'best_case' ? ' on' : '')}
                onClick={() => onCollMode('best_case')}
                title="Assume 100% of receivables collect on their due date. The most optimistic — useful as a 'best case'."
              >{t('Best case')}</button>
              <button
                className={'dash-proj-hbtn' + (collMode === 'realistic' ? ' on' : '')}
                onClick={() => onCollMode('realistic')}
                title="Haircut by ageing bucket — the older the overdue, the less likely it collects and the longer it takes."
              >Realistic</button>
            </div>
          </div>
        </div>
      </div>

      {collMode === 'realistic' && (
        <CollectionAssumptionsPanel
          schedule={collSchedule}
          onChange={onCollSchedule}
          projMonths={projMonths}
        />
      )}

      {collMode === 'realistic' && (
        <PaymentAssumptionsPanel
          schedule={paySchedule}
          onChange={onPaySchedule}
          projMonths={projMonths}
        />
      )}

      {/* Headline: projected low point — the number a CFO watches. */}
      <div className={'dash-proj-headline' + (goesNegative ? ' is-warning' : '')}>
        {goesNegative ? (
          <>
            <i className="ti ti-alert-triangle" aria-hidden />
            <span>
              Projected cash dips to <strong>{fmtD(lowest, 0)}</strong> in {monthLabel(rows[lowestIdx])} —
              a shortfall on committed items.
            </span>
          </>
        ) : (
          <>
            <i className="ti ti-circle-check" aria-hidden />
            <span>
              Projected cash stays positive — low point <strong>{fmtD(lowest, 0)}</strong> in {monthLabel(rows[lowestIdx])}.
            </span>
          </>
        )}
      </div>

      {projView === 'chart' ? (
        projStyle === 'bar' ? <ProjectionBars rows={rows} /> : <ProjectionChart rows={rows} />
      ) : (
      <table className="dash-variance-table dash-liq-table">
        <thead>
          <tr>
            <th>{t('Month')}</th>
            <th className="num">{t('Opening')}</th>
            <th className="num">{t('Expected In')}</th>
            <th className="num">{t('Expected Out')}</th>
            <th className="num">Projected Closing</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r: any, i: number) => (
            <tr key={i}>
              <td>{monthLabel(r)}</td>
              <td className="num">{fmtD(r.opening, 0)}</td>
              <td className="num up-text">{fmtD(r.expected_in, 0)}</td>
              <td className="num down-text">
                {fmtD(r.expected_out, 0)}
                {hasBaseline && (r.baseline_out || 0) > 0 && (
                  <div className="dash-proj-subline">
                    {fmtD(r.committed_out || 0, 0)} committed
                    {' + '}
                    {fmtD(r.baseline_out || 0, 0)} baseline
                  </div>
                )}
              </td>
              <td className="num" style={{ fontWeight: 600, color: r.closing < 0 ? 'var(--neg,#a32d2d)' : undefined }}>
                {fmtD(r.closing, 0)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      )}

      <div className="dash-proj-foot">
        <span>Starting cash: <strong>{fmtD(startCash, 0)}</strong></span>
        <span>Projected in {projection.months} months: <strong style={{ color: endCash < 0 ? 'var(--neg,#a32d2d)' : undefined }}>{fmtD(endCash, 0)}</strong></span>
        <span>Payables outstanding: <strong>{fmtD(projection.payables_total || 0, 0)}</strong></span>
        {collMode === 'realistic' && projection.uncollectible_estimate > 0 && (
          <span style={{ color: 'var(--neg, #a32d2d)' }}>
            Doubtful (excluded from forecast): <strong>{fmtD(projection.uncollectible_estimate, 0)}</strong>
          </span>
        )}
      </div>
      <div className="dash-sub" style={{ marginTop: 6 }}>
        {projBaseline === 'committed' && (
          <>Committed-items forecast: includes outstanding customer invoices (expected in) and supplier
            invoices (expected out), each placed in the month they fall due. Overdue items are assumed to
            settle in the first month. <strong>Does not include uncommitted future spend</strong> — pick a baseline above to add a recurring-expense estimate.</>
        )}
        {projBaseline === 'prior_year' && (
          <>Forecast with a <strong>prior-year</strong> expense baseline: committed inflows and payables remain as invoiced, plus the actual expense from the same calendar month one year earlier — net of any payables already invoiced to avoid double-counting. Best for seasonal businesses. Inflow remains committed-only (no projected revenue).</>
        )}
        {projBaseline === 'trailing_3m' && (
          <>Forecast with a <strong>trailing 3-month average</strong> expense baseline: committed inflows and payables remain as invoiced, plus the average actual expense from the last 3 completed months — net of any payables already invoiced. Best when current run-rate matters more than seasonality. Inflow remains committed-only (no projected revenue).</>
        )}
        {collMode === 'realistic' && (
          <div style={{ marginTop: 4 }}>
            <strong>Realistic mode:</strong> receivables are haircut by ageing bucket and spread over the months ahead — the older the overdue, the lower the collection rate and the longer the tail. Doubtful amounts are excluded from the forecast and shown above. Payables are timed by ageing bucket too — older overdue pays sooner (no haircut: you still owe the money) — and not all of it lands in month 1. Tune both schedules to match your real-world experience.
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── Financial ratios (v1.9.24) ────────────────────────────────────────────
 * Profitability / liquidity / efficiency ratios — the language management
 * and boards speak in. Each ratio shows its value and a RAG status against a
 * healthy benchmark.
 */
function fmtRatio(value: number | null, format: string): string {
  if (value == null || !isFinite(value)) return '—';
  if (format === 'pct') return (value * 100).toFixed(1) + '%';
  if (format === 'days') return Math.round(value) + ' d';
  return value.toFixed(2) + '×';
}

/** RAG for a ratio vs its benchmark. 'good'='high' means above benchmark is
 *  healthy; 'low' means below benchmark is healthy (DSO, etc.). */
function ragForRatio(value: number | null, benchmark: number, good: string): string {
  if (value == null || !isFinite(value)) return 'none';
  const ratio = good === 'high' ? value / benchmark : benchmark / value;
  if (ratio >= 1) return 'green';
  if (ratio >= 0.8) return 'amber';
  return 'red';
}

function RatiosBlock({ ratios }: { ratios: any }) {
  const groups = ratios.groups || [];
  return (
    <div className="dash-ratios">
      <div className="dash-variance-head">
        <h3 className="dash-variance-title">{t('Financial ratios')}</h3>
        <span className="dash-sub">
          Profitability, liquidity and efficiency — measured against healthy benchmarks.
        </span>
      </div>

      {groups.map((g: any) => (
        <div className="dash-ratio-group" key={g.key}>
          <div className="dash-ratio-group-lbl">{g.label}</div>
          <div className="dash-ratio-cells">
            {g.ratios.map((r: any) => {
              const rag = ragForRatio(r.value, r.benchmark, r.good);
              return (
                <div className={'dash-ratio-cell rag-' + rag} key={r.key}>
                  <div className="dash-ratio-top">
                    <span className="dash-ratio-lbl">{r.label}</span>
                    {rag !== 'none' && <span className={'dash-rag rag-dot-' + rag} />}
                  </div>
                  <div className="dash-ratio-val">{fmtRatio(r.value, r.format)}</div>
                  <div className="dash-ratio-bench">
                    target {r.good === 'high' ? '≥' : '≤'} {fmtRatio(r.benchmark, r.format)}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
      <div className="dash-sub" style={{ marginTop: 8 }}>
        Computed from the general ledger for the fiscal year. Benchmarks are general healthy
        guidelines — your industry targets may differ.
      </div>
    </div>
  );
}

/* ─── Collection assumptions editor (v1.9.29) ───────────────────────────────
 * Lets the user override the receivable collection-likelihood schedule used
 * by the realistic projection — collection % and month-spread per ageing
 * bucket. Defaults match the backend's sensible Big-Four-style values.
 */
const DEFAULT_COLL_SCHEDULE: Record<string, { pct: number; weights: number[]; label: string }> = {
  not_due:    { pct: 1.00, weights: [0.5, 0.5],                        label: 'Not yet due' },
  b0_30:      { pct: 0.90, weights: [0.6, 0.4],                        label: '0–30 days overdue' },
  b30_60:     { pct: 0.75, weights: [0.4, 0.4, 0.2],                   label: '30–60 days overdue' },
  b60_90:     { pct: 0.60, weights: [0.3, 0.3, 0.2, 0.2],              label: '60–90 days overdue' },
  b90_180:    { pct: 0.40, weights: [0.2, 0.2, 0.2, 0.2, 0.2],         label: '90–180 days overdue' },
  b180_plus:  { pct: 0.20, weights: [0.15, 0.15, 0.15, 0.15, 0.2, 0.2], label: '180+ days overdue' },
};

function CollectionAssumptionsPanel({ schedule, onChange, projMonths }: {
  schedule: Record<string, { pct: number; weights: number[] }> | undefined;
  onChange: (s: Record<string, { pct: number; weights: number[] }> | undefined) => void;
  projMonths: number;
}) {
  const [open, setOpen] = useState(false);
  // Compose the working values — user overrides on top of defaults.
  const eff: Record<string, { pct: number; weights: number[]; label: string }> = {};
  for (const k of Object.keys(DEFAULT_COLL_SCHEDULE)) {
    const d = DEFAULT_COLL_SCHEDULE[k];
    const u = schedule?.[k];
    eff[k] = {
      label: d.label,
      pct: u?.pct != null ? u.pct : d.pct,
      weights: u?.weights && u.weights.length ? u.weights : d.weights,
    };
  }

  function updateBucket(k: string, patch: Partial<{ pct: number; weights: number[] }>) {
    const next = { ...(schedule || {}) };
    next[k] = { ...(eff[k]), ...patch };
    // Drop unchanged-from-default entries so the payload stays small/clean.
    const cleaned: Record<string, { pct: number; weights: number[] }> = {};
    for (const kk of Object.keys(next)) {
      const v = next[kk];
      const d = DEFAULT_COLL_SCHEDULE[kk];
      if (!d) continue;
      const pctChanged = Math.abs(v.pct - d.pct) > 1e-9;
      const weightsChanged = JSON.stringify(v.weights) !== JSON.stringify(d.weights);
      if (pctChanged || weightsChanged) cleaned[kk] = v;
    }
    onChange(Object.keys(cleaned).length ? cleaned : undefined);
  }

  const isCustomised = schedule && Object.keys(schedule).length > 0;

  return (
    <div className="coll-panel">
      <button
        className="coll-panel-toggle"
        onClick={() => setOpen((o) => !o)}
      >
        <i className={'ti ti-chevron-' + (open ? 'down' : 'right')} aria-hidden />
        Collection assumptions
        {isCustomised && <span className="coll-panel-tag">{t('Customised')}</span>}
        <span className="dash-sub">
          {open ? 'Collapse' : 'Expand'} to tune collection % and month spread per ageing bucket
        </span>
      </button>

      {open && (
        <div className="coll-panel-body">
          <div className="coll-panel-hint">
            <i className="ti ti-info-circle" aria-hidden />
            <span>
              The defaults are general industry guidelines — <strong>tune them to match
              your actual collection experience</strong>. A B2B consulting business
              collecting from corporate clients usually has higher recovery on aged
              receivables than retail; adjust upward. A business with chronic late-payers
              should adjust downward.
            </span>
          </div>
          <table className="coll-table">
            <thead>
              <tr>
                <th>{t('Ageing bucket')}</th>
                <th className="num">{t('Collection %')}</th>
                <th>Month spread (relative weights)</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(eff).map(([k, v]) => (
                <tr key={k}>
                  <td>{v.label}</td>
                  <td className="num">
                    <input
                      type="number"
                      step="5"
                      min="0"
                      max="100"
                      value={Math.round(v.pct * 100)}
                      onChange={(e) => {
                        const pct = Math.max(0, Math.min(100, Number(e.target.value) || 0)) / 100;
                        updateBucket(k, { pct });
                      }}
                      className="coll-input"
                    />%
                  </td>
                  <td>
                    <div className="coll-weights">
                      {v.weights.map((w, i) => (
                        <input
                          key={i}
                          type="number"
                          step="0.05"
                          min="0"
                          value={w}
                          onChange={(e) => {
                            const nw = [...v.weights];
                            nw[i] = Math.max(0, Number(e.target.value) || 0);
                            updateBucket(k, { weights: nw });
                          }}
                          className="coll-input coll-input-weight"
                          title={'Weight for month ' + (i + 1)}
                        />
                      ))}
                      <button
                        className="coll-w-btn"
                        title="Add a month to the spread"
                        onClick={() => updateBucket(k, { weights: [...v.weights, 0.1] })}
                      >+</button>
                      {v.weights.length > 1 && (
                        <button
                          className="coll-w-btn"
                          title="Remove the last month from the spread"
                          onClick={() => updateBucket(k, { weights: v.weights.slice(0, -1) })}
                        >−</button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="dash-sub" style={{ marginTop: 6 }}>
            Weights are relative — they normalise to 100% across the months you specify.
            Weights beyond your chosen horizon ({projMonths}m) collapse into the final month so
            the realistic estimate stays whole within the window.
          </div>
          {isCustomised && (
            <button
              className="coll-reset"
              onClick={() => onChange(undefined)}
            >
              <i className="ti ti-rotate-clockwise" aria-hidden /> Reset to defaults
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Payment-timing assumptions editor (v1.9.30) ───────────────────────────
 * The payables mirror of CollectionAssumptionsPanel — but without a haircut
 * (you still owe the money). Lets the user tune the payment-timing spread
 * per ageing bucket. Older overdue pays sooner by default (supplier pressure).
 */
const DEFAULT_PAY_SCHEDULE: Record<string, { weights: number[]; label: string }> = {
  not_due:    { weights: [0.5, 0.5],                          label: 'Not yet due' },
  b0_30:      { weights: [0.7, 0.3],                          label: '0–30 days overdue' },
  b30_60:     { weights: [0.6, 0.3, 0.1],                     label: '30–60 days overdue' },
  b60_90:     { weights: [0.5, 0.3, 0.2],                     label: '60–90 days overdue' },
  b90_180:    { weights: [0.3, 0.25, 0.2, 0.15, 0.1],         label: '90–180 days overdue' },
  b180_plus:  { weights: [0.2, 0.2, 0.2, 0.15, 0.15, 0.1],    label: '180+ days overdue' },
};

function PaymentAssumptionsPanel({ schedule, onChange, projMonths }: {
  schedule: Record<string, { weights: number[] }> | undefined;
  onChange: (s: Record<string, { weights: number[] }> | undefined) => void;
  projMonths: number;
}) {
  const [open, setOpen] = useState(false);
  const eff: Record<string, { weights: number[]; label: string }> = {};
  for (const k of Object.keys(DEFAULT_PAY_SCHEDULE)) {
    const d = DEFAULT_PAY_SCHEDULE[k];
    const u = schedule?.[k];
    eff[k] = {
      label: d.label,
      weights: u?.weights && u.weights.length ? u.weights : d.weights,
    };
  }

  function updateBucket(k: string, patch: Partial<{ weights: number[] }>) {
    const next = { ...(schedule || {}) };
    next[k] = { ...(eff[k]), ...patch };
    const cleaned: Record<string, { weights: number[] }> = {};
    for (const kk of Object.keys(next)) {
      const v = next[kk];
      const d = DEFAULT_PAY_SCHEDULE[kk];
      if (!d) continue;
      if (JSON.stringify(v.weights) !== JSON.stringify(d.weights)) cleaned[kk] = v;
    }
    onChange(Object.keys(cleaned).length ? cleaned : undefined);
  }

  const isCustomised = schedule && Object.keys(schedule).length > 0;

  return (
    <div className="coll-panel">
      <button className="coll-panel-toggle" onClick={() => setOpen((o) => !o)}>
        <i className={'ti ti-chevron-' + (open ? 'down' : 'right')} aria-hidden />
        Payment assumptions
        {isCustomised && <span className="coll-panel-tag">{t('Customised')}</span>}
        <span className="dash-sub">
          {open ? 'Collapse' : 'Expand'} to tune the month-spread of payables payment per ageing bucket
        </span>
      </button>

      {open && (
        <div className="coll-panel-body">
          <div className="coll-panel-hint">
            <i className="ti ti-info-circle" aria-hidden />
            <span>
              <strong>Tune these for your business.</strong> Defaults spread very-aged
              payables (90+ days) over several months because in practice they
              dribble out rather than clearing in one. If you actually clear old
              debts quickly, shorten the spread. If you stretch suppliers longer,
              lengthen it.
            </span>
          </div>
          <table className="coll-table">
            <thead>
              <tr>
                <th>{t('Ageing bucket')}</th>
                <th>Month spread (relative weights)</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(eff).map(([k, v]) => (
                <tr key={k}>
                  <td>{v.label}</td>
                  <td>
                    <div className="coll-weights">
                      {v.weights.map((w, i) => (
                        <input
                          key={i}
                          type="number"
                          step="0.05"
                          min="0"
                          value={w}
                          onChange={(e) => {
                            const nw = [...v.weights];
                            nw[i] = Math.max(0, Number(e.target.value) || 0);
                            updateBucket(k, { weights: nw });
                          }}
                          className="coll-input coll-input-weight"
                          title={'Weight for month ' + (i + 1)}
                        />
                      ))}
                      <button className="coll-w-btn" title="Add a month" onClick={() => updateBucket(k, { weights: [...v.weights, 0.1] })}>+</button>
                      {v.weights.length > 1 && (
                        <button className="coll-w-btn" title="Remove the last month" onClick={() => updateBucket(k, { weights: v.weights.slice(0, -1) })}>−</button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="dash-sub" style={{ marginTop: 6 }}>
            Weights are relative — they normalise to 100% across the months you specify. <strong>No haircut on payables: 100% pays.</strong> Weights beyond your chosen horizon ({projMonths}m) collapse into the final month.
          </div>
          {isCustomised && (
            <button className="coll-reset" onClick={() => onChange(undefined)}>
              <i className="ti ti-rotate-clockwise" aria-hidden /> Reset to defaults
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Chart components (v1.9.32) ────────────────────────────────────────────
 * Lightweight inline SVG charts for the dashboard blocks where a visual is
 * genuinely clearer than the table — variance, ageing, projection. No chart
 * library: hand-drawn SVG, native scaling, no runtime overhead.
 */

function fmtShort(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'M';
  if (abs >= 1_000) return (v / 1_000).toFixed(0) + 'K';
  return v.toFixed(0);
}

/** Small two-state toggle — Table / Chart — used on each chart-capable block. */
function ViewModeToggle({ mode, onChange }: { mode: 'table' | 'chart'; onChange: (m: 'table' | 'chart') => void }) {
  return (
    <div className="dash-vmt">
      <button className={'dash-vmt-btn' + (mode === 'table' ? ' on' : '')} onClick={() => onChange('table')} title="Table view">
        <i className="ti ti-table" aria-hidden />
      </button>
      <button className={'dash-vmt-btn' + (mode === 'chart' ? ' on' : '')} onClick={() => onChange('chart')} title="Chart view">
        <i className="ti ti-chart-bar" aria-hidden />
      </button>
    </div>
  );
}

/** Horizontal bar chart of biggest variances. Green = ahead of budget, red = behind. */
function VarianceChart({ rows }: { rows: Array<{ key: string; label: string; gap: number; gapPct: number | null }> }) {
  if (!rows.length) return null;
  const W = 720, RH = 26, P = 4;
  const H = rows.length * RH + P * 2;
  const labelW = 160, valueW = 90;
  const barW = W - labelW - valueW - P * 2;
  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.gap))) || 1;
  const zeroX = labelW + barW / 2;
  return (
    <svg className="dash-chart" viewBox={`0 0 ${W} ${H}`} width="100%" preserveAspectRatio="xMidYMid meet">
      <line x1={zeroX} y1={P} x2={zeroX} y2={H - P} stroke="#d9d6d0" strokeWidth="1" />
      {rows.map((r, i) => {
        const y = P + i * RH;
        const len = (Math.abs(r.gap) / maxAbs) * (barW / 2);
        const over = r.gap >= 0;
        const x1 = over ? zeroX : zeroX - len;
        const fill = over ? '#0f6e56' : '#a32d2d';
        return (
          <g key={r.key}>
            <text x={labelW - 6} y={y + RH / 2 + 4} textAnchor="end" fontSize="11" fill="#15141b">
              {r.label.length > 28 ? r.label.slice(0, 27) + '…' : r.label}
            </text>
            <rect x={x1} y={y + 5} width={len} height={RH - 10} fill={fill} rx="2" />
            <text x={W - P} y={y + RH / 2 + 4} textAnchor="end" fontSize="11" fill={fill} fontWeight="600">
              {(over ? '+' : '') + fmtShort(r.gap)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** Ageing bar chart — one row, segmented by bucket. Reads bucket distribution at a glance. */
function AgeingChart({ notDue, buckets, label }: {
  notDue: number;
  buckets: Array<{ key: string; label: string; amount: number }>;
  label: string;
}) {
  const all = [{ key: 'not_due', label: 'Not due', amount: notDue }, ...buckets];
  const total = all.reduce((s, b) => s + (b.amount || 0), 0);
  if (total <= 0) return <div className="dash-sub" style={{ padding: '8px 0' }}>No {label} to chart.</div>;
  // Cool blue for "not due," progressively warmer reds as overdue grows.
  const COLORS: Record<string, string> = {
    not_due:   '#0c447c',
    b0_30:     '#6e9d4f',
    b30_60:    '#c9a227',
    b60_90:    '#d97706',
    b90_180:   '#b03a3a',
    b180_plus: '#7a1f1f',
  };
  let x = 0;
  return (
    <div className="dash-ageing-chart">
      <svg viewBox="0 0 100 8" preserveAspectRatio="none" width="100%" height="22">
        {all.map((b) => {
          const w = (b.amount / total) * 100;
          const rect = <rect key={b.key} x={x} y="0" width={w} height="8" fill={COLORS[b.key] || '#999'} />;
          x += w;
          return rect;
        })}
      </svg>
      <div className="dash-ageing-legend">
        {all.filter((b) => b.amount > 0).map((b) => (
          <span key={b.key} className="dash-ageing-leg-item">
            <span className="dash-ageing-leg-sw" style={{ background: COLORS[b.key] }} />
            {b.label}: {fmtShort(b.amount)} ({((b.amount / total) * 100).toFixed(0)}%)
          </span>
        ))}
      </div>
    </div>
  );
}

/** Projection line chart — projected closing cash over time, with a zero baseline. */
function ProjectionChart({ rows }: { rows: Array<{ month: number; year: number; closing: number }> }) {
  if (!rows.length) return null;
  const W = 720, H = 220, PAD_L = 56, PAD_R = 20, PAD_T = 16, PAD_B = 28;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  const closings = rows.map((r) => r.closing);
  const minVal = Math.min(...closings, 0);
  const maxVal = Math.max(...closings, 0);
  const span = (maxVal - minVal) || 1;
  const stepX = innerW / Math.max(1, rows.length - 1);
  const y = (v: number) => PAD_T + innerH - ((v - minVal) / span) * innerH;
  const x = (i: number) => PAD_L + i * stepX;
  const pts = rows.map((r, i) => [x(i), y(r.closing)] as [number, number]);
  const path = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const areaPath = path + ` L ${pts[pts.length - 1][0]} ${y(minVal)} L ${pts[0][0]} ${y(minVal)} Z`;
  const zeroY = y(0);
  const hasZero = minVal < 0 && maxVal > 0;
  // Y-axis ticks — three sensible levels.
  const ticks = [minVal, (minVal + maxVal) / 2, maxVal];
  return (
    <svg className="dash-chart" viewBox={`0 0 ${W} ${H}`} width="100%">
      {/* Grid + ticks */}
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={PAD_L} y1={y(t)} x2={W - PAD_R} y2={y(t)} stroke="#ece9e3" strokeWidth="1" />
          <text x={PAD_L - 6} y={y(t) + 4} textAnchor="end" fontSize="10" fill="#6e6a63">{fmtShort(t)}</text>
        </g>
      ))}
      {hasZero && (
        <line x1={PAD_L} y1={zeroY} x2={W - PAD_R} y2={zeroY} stroke="#a32d2d" strokeWidth="1" strokeDasharray="3 3" />
      )}
      {/* Area under line */}
      <path d={areaPath} fill="#0c447c" fillOpacity="0.08" />
      {/* Line */}
      <path d={path} fill="none" stroke="#0c447c" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      {/* Points */}
      {pts.map((p, i) => (
        <g key={i}>
          <circle cx={p[0]} cy={p[1]} r="3.2" fill="#0c447c" />
          <text x={p[0]} y={H - 10} textAnchor="middle" fontSize="10" fill="#6e6a63">
            {MONTHS[rows[i].month]} {String(rows[i].year).slice(2)}
          </text>
        </g>
      ))}
    </svg>
  );
}

/* ─── Chart-style alternatives (v1.9.33) ────────────────────────────────────
 * Defensible style choice: only where multiple chart types legitimately
 * answer the same question. Ageing blocks get donut/pie alternatives;
 * projection gets a bar alternative. Variance stays bar-only by design.
 */

/** Small chart-style selector — Bar / Donut / Pie or Line / Bar. */
function ChartStyleSelector<T extends string>({ value, options, onChange }: {
  value: T;
  options: Array<{ key: T; label: string; icon: string }>;
  onChange: (v: T) => void;
}) {
  return (
    <div className="dash-cstyle">
      {options.map((opt) => (
        <button
          key={opt.key}
          className={'dash-cstyle-btn' + (value === opt.key ? ' on' : '')}
          onClick={() => onChange(opt.key)}
          title={opt.label}
        >
          <i className={'ti ti-' + opt.icon} aria-hidden />
        </button>
      ))}
    </div>
  );
}

const AGEING_PALETTE: Record<string, string> = {
  not_due:   '#0c447c',
  b0_30:     '#6e9d4f',
  b30_60:    '#c9a227',
  b60_90:    '#d97706',
  b90_180:   '#b03a3a',
  b180_plus: '#7a1f1f',
};

/** Donut / Pie chart for ageing composition. `hole` controls donut vs pie. */
function AgeingPie({ notDue, buckets, hole }: {
  notDue: number;
  buckets: Array<{ key: string; label: string; amount: number }>;
  hole: number;       // 0 = pie, 0.55 = donut
}) {
  const all = [{ key: 'not_due', label: 'Not due', amount: notDue }, ...buckets]
    .filter((b) => (b.amount || 0) > 0);
  const total = all.reduce((s, b) => s + (b.amount || 0), 0);
  if (total <= 0) {
    return <div className="dash-sub" style={{ padding: '8px 0' }}>Nothing to chart.</div>;
  }

  const size = 200;
  const cx = size / 2, cy = size / 2;
  const rOuter = 90;
  const rInner = hole > 0 ? rOuter * hole : 0;

  // Generate slice paths.
  let angleStart = -Math.PI / 2;       // start at 12 o'clock
  const slices = all.map((b) => {
    const frac = b.amount / total;
    const angleEnd = angleStart + frac * Math.PI * 2;
    const path = arcPath(cx, cy, rOuter, rInner, angleStart, angleEnd);
    const midAngle = (angleStart + angleEnd) / 2;
    const labelR = (rOuter + rInner) / 2 || rOuter * 0.6;
    const lx = cx + Math.cos(midAngle) * labelR;
    const ly = cy + Math.sin(midAngle) * labelR;
    const out = { key: b.key, label: b.label, amount: b.amount, frac, path, lx, ly, color: AGEING_PALETTE[b.key] || '#999' };
    angleStart = angleEnd;
    return out;
  });

  return (
    <div className="dash-pie-wrap">
      <svg viewBox={`0 0 ${size} ${size}`} width="220" height="220" className="dash-pie">
        {slices.map((s) => (
          <g key={s.key}>
            <path d={s.path} fill={s.color} stroke="#fff" strokeWidth="1" />
            {s.frac >= 0.06 && (
              <text x={s.lx} y={s.ly} textAnchor="middle" fontSize="10" fill="#fff" fontWeight="600">
                {(s.frac * 100).toFixed(0)}%
              </text>
            )}
          </g>
        ))}
        {hole > 0 && (
          <g>
            <text x={cx} y={cy - 4} textAnchor="middle" fontSize="11" fill="#6e6a63">{t('Total')}</text>
            <text x={cx} y={cy + 12} textAnchor="middle" fontSize="13" fontWeight="700" fill="#15141b">
              {fmtShort(total)}
            </text>
          </g>
        )}
      </svg>
      <div className="dash-pie-legend">
        {slices.map((s) => (
          <div key={s.key} className="dash-pie-leg-row">
            <span className="dash-pie-leg-sw" style={{ background: s.color }} />
            <span className="dash-pie-leg-lbl">{s.label}</span>
            <span className="dash-pie-leg-amt">{fmtShort(s.amount)}</span>
            <span className="dash-pie-leg-pct">{(s.frac * 100).toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** SVG path for an annular sector (works for pie when rInner=0). */
function arcPath(cx: number, cy: number, rOuter: number, rInner: number, a0: number, a1: number): string {
  const large = (a1 - a0) > Math.PI ? 1 : 0;
  const x0 = cx + Math.cos(a0) * rOuter, y0 = cy + Math.sin(a0) * rOuter;
  const x1 = cx + Math.cos(a1) * rOuter, y1 = cy + Math.sin(a1) * rOuter;
  if (rInner <= 0) {
    return `M ${cx} ${cy} L ${x0} ${y0} A ${rOuter} ${rOuter} 0 ${large} 1 ${x1} ${y1} Z`;
  }
  const ix0 = cx + Math.cos(a0) * rInner, iy0 = cy + Math.sin(a0) * rInner;
  const ix1 = cx + Math.cos(a1) * rInner, iy1 = cy + Math.sin(a1) * rInner;
  return `M ${x0} ${y0} A ${rOuter} ${rOuter} 0 ${large} 1 ${x1} ${y1} L ${ix1} ${iy1} A ${rInner} ${rInner} 0 ${large} 0 ${ix0} ${iy0} Z`;
}

/** Column chart for projection — one bar per month, height = closing cash. */
function ProjectionBars({ rows }: { rows: Array<{ month: number; year: number; closing: number }> }) {
  if (!rows.length) return null;
  const W = 720, H = 220, PAD_L = 56, PAD_R = 20, PAD_T = 16, PAD_B = 28;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  const closings = rows.map((r) => r.closing);
  const minVal = Math.min(...closings, 0);
  const maxVal = Math.max(...closings, 0);
  const span = (maxVal - minVal) || 1;
  const colW = innerW / rows.length;
  const barW = Math.max(20, colW * 0.6);
  const y = (v: number) => PAD_T + innerH - ((v - minVal) / span) * innerH;
  const zeroY = y(0);
  const ticks = [minVal, (minVal + maxVal) / 2, maxVal];
  return (
    <svg className="dash-chart" viewBox={`0 0 ${W} ${H}`} width="100%">
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={PAD_L} y1={y(t)} x2={W - PAD_R} y2={y(t)} stroke="#ece9e3" strokeWidth="1" />
          <text x={PAD_L - 6} y={y(t) + 4} textAnchor="end" fontSize="10" fill="#6e6a63">{fmtShort(t)}</text>
        </g>
      ))}
      {minVal < 0 && maxVal > 0 && (
        <line x1={PAD_L} y1={zeroY} x2={W - PAD_R} y2={zeroY} stroke="#a32d2d" strokeWidth="1" strokeDasharray="3 3" />
      )}
      {rows.map((r, i) => {
        const cx = PAD_L + i * colW + colW / 2;
        const top = r.closing >= 0 ? y(r.closing) : zeroY;
        const h = Math.abs(y(r.closing) - zeroY);
        const fill = r.closing >= 0 ? '#0c447c' : '#a32d2d';
        return (
          <g key={i}>
            <rect x={cx - barW / 2} y={top} width={barW} height={h} fill={fill} rx="2" />
            <text x={cx} y={H - 10} textAnchor="middle" fontSize="10" fill="#6e6a63">
              {MONTHS[r.month]} {String(r.year).slice(2)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ─── Variance commentary editor (v1.9.36) ──────────────────────────────────
 * Inline textarea under a variance row. Auto-saves 1s after typing stops.
 * Shows author and last-modified date — a management pack reader needs to
 * know who wrote the explanation and when.
 */
function VarianceNoteEditor({ rowKey, note, open, onChange, onClose }: {
  rowKey: string;
  note: { commentary: string; modified: string; modified_by: string } | undefined;
  open: boolean;
  onChange: (text: string) => void;
  onClose: () => void;
}) {
  const text = note?.commentary || '';
  if (!open) {
    // Closed: render the note read-only (caller only renders the row at all
    // when there's a note to show OR the editor is open).
    if (!text) return null;
    return (
      <div className="var-note-display">
        <div className="var-note-text">{text}</div>
        {(note?.modified_by || note?.modified) && (
          <div className="var-note-meta">
            {note?.modified_by && <span>— {note.modified_by}</span>}
            {note?.modified && <span className="muted">{formatNoteDate(note.modified)}</span>}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="var-note-edit">
      <textarea
        autoFocus
        defaultValue={text}
        placeholder="Why did this line move from plan? e.g. 'Q3 trade show brought forward from FY25, plus brand-refresh agreed by the board in October.'"
        onChange={(e) => onChange(e.target.value)}
        className="var-note-textarea"
        rows={2}
      />
      <div className="var-note-edit-foot">
        <span className="dash-sub">Auto-saves shortly after you stop typing</span>
        <button className="var-note-close" onClick={onClose} title="Close editor">
          <i className="ti ti-check" aria-hidden /> Done
        </button>
      </div>
    </div>
  );
}

function formatNoteDate(raw: string): string {
  if (!raw) return '';
  try {
    const d = new Date(raw.replace(' ', 'T'));
    if (isNaN(d.getTime())) return '';
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  } catch {
    return '';
  }
}

/* ─── Sensitivity / stress test (v1.9.39) ──────────────────────────────────
 * Three sliders that perturb the existing projection: collection speed,
 * revenue level, cost inflation. Live cash chart updates as you move them.
 * Also runs a tornado on demand — shows which lever matters most.
 *
 * Honest scope: not persisted. Exploratory analysis only — slider values
 * reset on remount. If a CFO wants to commit a stressed scenario, that's
 * a budget revision (different feature, not this one).
 */

function SensitivityBlock({ company, fy, projMonths, projBaseline, collMode, currency }: {
  company: string;
  fy: number;
  projMonths: number;
  projBaseline: string;
  collMode: string;
  currency: string;
}) {
  const [days, setDays] = useState(0);          // -30..+30, default 0 (base case)
  const [revPct, setRevPct] = useState(0);      // -20..+10
  const [costPct, setCostPct] = useState(0);    // 0..+15
  const [stressed, setStressed] = useState<any>(null);
  const [tornado, setTornado] = useState<any>(null);
  const [tornadoLoading, setTornadoLoading] = useState(false);

  const isBaseCase = days === 0 && revPct === 0 && costPct === 0;

  // Run the stressed scenario when any slider changes (debounced).
  useEffect(() => {
    if (!company || !fy) return;
    const t = setTimeout(() => {
      api.getSensitivityScenario({
        company, fiscal_year: fy,
        projection_months: projMonths, projection_baseline: projBaseline,
        collection_mode: collMode,
        stress_collection_days: days,
        stress_revenue_pct: revPct,
        stress_cost_pct: costPct,
      })
        .then((r) => setStressed(r))
        .catch(() => setStressed(null));
    }, 250);
    return () => clearTimeout(t);
  }, [company, fy, projMonths, projBaseline, collMode, days, revPct, costPct]);

  function runTornado() {
    if (!company || !fy) return;
    setTornadoLoading(true);
    api.getSensitivityTornado({
      company, fiscal_year: fy,
      projection_months: projMonths, projection_baseline: projBaseline,
      collection_mode: collMode,
    })
      .then((r) => setTornado(r))
      .catch(() => setTornado(null))
      .finally(() => setTornadoLoading(false));
  }

  function resetSliders() { setDays(0); setRevPct(0); setCostPct(0); }

  const projRows = (stressed?.projection?.rows || []) as any[];
  const summary = stressed?.projection?.stressed_summary;
  const baseRows = (stressed?.projection?.rows || []) as any[];
  const lowPoint = summary?.low_point || 0;
  const goesNegative = !!summary?.goes_negative;
  const ending = summary?.ending_cash || 0;
  const baseEnding = (() => {
    // The endpoint returns delta_vs_base_ending — use that to derive base.
    const d = summary?.delta_vs_base_ending || 0;
    return ending - d;
  })();

  return (
    <div className="dash-card sensitivity-block">
      <div className="dash-block-head">
        <div>
          <h3 className="dash-variance-title">Stress test — what could break cash</h3>
          <span className="dash-sub">
            Move the sliders to see how the projection responds. Sensitivity is exploratory — values aren't saved.
          </span>
        </div>
        {!isBaseCase && (
          <button className="sens-reset" onClick={resetSliders}>
            <i className="ti ti-rotate-clockwise" aria-hidden /> Reset to base case
          </button>
        )}
      </div>

      {/* Sliders */}
      <div className="sens-sliders">
        <SensSlider
          label="Collection delay"
          value={days}
          min={-30} max={30} step={5}
          format={(v) => (v === 0 ? 'On time' : (v > 0 ? '+' : '') + v + ' days')}
          help="Customers paying later (or earlier) than expected. +20 days = a slowdown."
          onChange={setDays}
        />
        <SensSlider
          label="Revenue level"
          value={revPct}
          min={-20} max={10} step={1}
          format={(v) => (v === 0 ? 'On plan' : (v > 0 ? '+' : '') + v + '%')}
          help="Revenue below or above plan. −10% = sales 10% softer than expected."
          onChange={setRevPct}
          downsideEmphasis
        />
        <SensSlider
          label="Cost inflation"
          value={costPct}
          min={0} max={15} step={1}
          format={(v) => (v === 0 ? 'No inflation' : '+' + v + '%')}
          help="Supplier price rises, wage inflation, etc. +5% = all outflows 5% higher."
          onChange={setCostPct}
        />
      </div>

      {/* Headline summary — the "what broke" line */}
      {stressed && (
        <div className={'sens-headline ' + (goesNegative ? 'is-warning' : (isBaseCase ? '' : (ending < baseEnding ? 'is-worse' : 'is-better')))}>
          {isBaseCase ? (
            <>Base case: cash ends at <strong>{fmtD(ending, 0)}</strong>, low point <strong>{fmtD(lowPoint, 0)}</strong>.</>
          ) : goesNegative ? (
            <>⚠ Cash goes negative in this scenario. Low point: <strong>{fmtD(lowPoint, 0)}</strong> in month {(summary?.low_point_month_idx || 0) + 1}.</>
          ) : ending < baseEnding ? (
            <>Cash ends at <strong>{fmtD(ending, 0)}</strong> — {fmtD(Math.abs(ending - baseEnding), 0)} below base case. Stays positive.</>
          ) : (
            <>Cash ends at <strong>{fmtD(ending, 0)}</strong> — {fmtD(ending - baseEnding, 0)} better than base case.</>
          )}
        </div>
      )}

      {/* Stressed projection chart */}
      {projRows.length > 0 && (
        <StressedProjectionChart rows={projRows} baseEnding={baseEnding} />
      )}

      {/* Tornado section */}
      <div className="sens-tornado-section">
        <div className="dash-block-head" style={{ marginTop: 16 }}>
          <div>
            <h4 className="dash-variance-title" style={{ fontSize: 13 }}>Tornado — which lever matters most?</h4>
            <span className="dash-sub">
              Standardised mild / moderate / severe stress for each lever. The longest bar is what to worry about first.
            </span>
          </div>
          {!tornado && (
            <button className="dash-run-btn" onClick={runTornado} disabled={tornadoLoading}>
              {tornadoLoading ? 'Running…' : 'Run tornado'}
            </button>
          )}
        </div>
        {tornado && <TornadoChart data={tornado} />}
      </div>
    </div>
  );
}

function SensSlider({ label, value, min, max, step, format, help, onChange, downsideEmphasis }: {
  label: string;
  value: number;
  min: number; max: number; step: number;
  format: (v: number) => string;
  help: string;
  onChange: (v: number) => void;
  downsideEmphasis?: boolean;
}) {
  const isStressed = value !== 0 && (!downsideEmphasis || value < 0);
  return (
    <div className="sens-slider">
      <div className="sens-slider-head">
        <span className="sens-slider-lbl">{label}</span>
        <span className={'sens-slider-val' + (isStressed ? ' is-stressed' : '')}>{format(value)}</span>
      </div>
      <input
        type="range"
        min={min} max={max} step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="sens-slider-input"
      />
      <div className="sens-slider-help">{help}</div>
    </div>
  );
}

function StressedProjectionChart({ rows, baseEnding }: { rows: any[]; baseEnding: number }) {
  if (!rows.length) return null;
  const W = 720, H = 200, PAD_L = 56, PAD_R = 20, PAD_T = 14, PAD_B = 24;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  const closings = rows.map((r) => Number(r.closing || 0));
  const minVal = Math.min(...closings, 0, baseEnding);
  const maxVal = Math.max(...closings, 0, baseEnding);
  const span = (maxVal - minVal) || 1;
  const stepX = innerW / Math.max(1, rows.length - 1);
  const y = (v: number) => PAD_T + innerH - ((v - minVal) / span) * innerH;
  const pts = closings.map((c, i) => [PAD_L + i * stepX, y(c)] as [number, number]);
  const path = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const ticks = [minVal, (minVal + maxVal) / 2, maxVal];
  const zeroY = y(0);
  const hasZero = minVal < 0 && maxVal > 0;
  return (
    <svg className="dash-chart" viewBox={`0 0 ${W} ${H}`} width="100%">
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={PAD_L} y1={y(t)} x2={W - PAD_R} y2={y(t)} stroke="#ece9e3" strokeWidth="1" />
          <text x={PAD_L - 6} y={y(t) + 4} textAnchor="end" fontSize="10" fill="#6e6a63">{fmtShort(t)}</text>
        </g>
      ))}
      {hasZero && <line x1={PAD_L} y1={zeroY} x2={W - PAD_R} y2={zeroY} stroke="#a32d2d" strokeWidth="1" strokeDasharray="3 3" />}
      <path d={path} fill="none" stroke="#0c447c" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r="3" fill="#0c447c" />)}
    </svg>
  );
}

function TornadoChart({ data }: { data: any }) {
  const levers = data?.levers || [];
  if (!levers.length) return null;

  // Max absolute delta across all scenarios — defines the chart's full width.
  let maxAbs = 0;
  for (const lev of levers) for (const s of (lev.scenarios || [])) maxAbs = Math.max(maxAbs, Math.abs(s.delta));
  if (maxAbs === 0) maxAbs = 1;

  const W = 720, RH = 30, P = 6;
  const labelW = 200;
  const barAreaW = W - labelW - 80 - P;
  const H = levers.length * RH + P * 2 + 18;
  const cx = labelW + barAreaW / 2;

  return (
    <div className="sens-tornado">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%">
        <line x1={cx} y1={P} x2={cx} y2={H - 14} stroke="#a32d2d" strokeWidth="1" strokeDasharray="3 3" />
        <text x={cx} y={H - 2} textAnchor="middle" fontSize="10" fill="#6e6a63">Base case (no stress)</text>
        {levers.map((lev: any, i: number) => {
          const y = P + i * RH + 4;
          const severe = lev.scenarios[2];
          const moderate = lev.scenarios[1];
          const mild = lev.scenarios[0];
          const direction = (severe.delta || 0) < 0 ? -1 : 1;
          const wSevere = (Math.abs(severe.delta) / maxAbs) * (barAreaW / 2);
          const wMod = (Math.abs(moderate.delta) / maxAbs) * (barAreaW / 2);
          const wMild = (Math.abs(mild.delta) / maxAbs) * (barAreaW / 2);
          const x0 = direction < 0 ? cx - wSevere : cx;
          return (
            <g key={lev.lever}>
              <text x={labelW - 8} y={y + 14} textAnchor="end" fontSize="11" fill="#15141b">
                {lev.label}
              </text>
              {/* Severe band */}
              <rect x={x0} y={y + 4} width={wSevere} height={RH - 12} fill="#a32d2d" opacity="0.7" />
              {/* Moderate band (overlaid) */}
              <rect x={direction < 0 ? cx - wMod : cx} y={y + 4} width={wMod} height={RH - 12} fill="#d97706" opacity="0.85" />
              {/* Mild band (innermost) */}
              <rect x={direction < 0 ? cx - wMild : cx} y={y + 4} width={wMild} height={RH - 12} fill="#c9a227" />
              <text x={direction < 0 ? cx - wSevere - 6 : cx + wSevere + 6} y={y + 16}
                    textAnchor={direction < 0 ? 'end' : 'start'} fontSize="10" fill="#a32d2d" fontWeight="600">
                {fmtShort(severe.delta)}{severe.goes_negative ? ' ⚠' : ''}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="sens-tornado-legend">
        <span><span className="leg-sw" style={{ background: '#c9a227' }} /> Mild</span>
        <span><span className="leg-sw" style={{ background: '#d97706' }} /> Moderate</span>
        <span><span className="leg-sw" style={{ background: '#a32d2d' }} /> Severe</span>
        <span style={{ marginLeft: 12 }}>⚠ = scenario takes cash negative</span>
      </div>
      <div className="dash-sub" style={{ marginTop: 6 }}>
        Levers ranked by impact magnitude. <strong>{levers[0]?.label}</strong> moves cash the most — focus your attention here.
      </div>
    </div>
  );
}
