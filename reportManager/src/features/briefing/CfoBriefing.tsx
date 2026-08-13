import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { t } from '../../utils/i18n';
import { api } from '../../utils/api';
import { useDimFilters, compactDimFilters } from '../../utils/dimFilters';
import { ActiveDimFiltersChips } from '../ActiveDimFiltersChips';
import { CfoBrief } from './CfoBrief';

/* ─── CFO Briefing tab (v1.9.40) ────────────────────────────────────────────
 * A finance leader's primary view, structured around the questions a CFO
 * actually asks on a Tuesday morning. NOT a feature dump — a synthesis.
 *
 * This release ships Sections 1 (Solvency) + 2 (Profit & growth). Sections
 * 3-5 (Fragility, Variance, Actions) come in subsequent releases.
 *
 * Honest-synthesis discipline (applied throughout):
 *   - Only reference data the response actually contains.
 *   - If a comparison is unavailable, say nothing about it — don't fabricate.
 *   - Use qualifiers when the underlying numbers are noisy.
 * Every sentence below is auditable against the response payload.
 */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function fmt(v: number | null | undefined, decimals = 0): string {
  if (v == null || !isFinite(v)) return '—';
  return v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function fmtShort(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'M';
  if (abs >= 1_000) return (v / 1_000).toFixed(0) + 'K';
  return v.toFixed(0);
}

function pct(v: number | null | undefined, decimals = 1): string {
  if (v == null || !isFinite(v)) return '—';
  return (v * 100).toFixed(decimals) + '%';
}

export function CfoBriefing() {
  const [reports, setReports] = useState<Array<{ name: string; report_name: string; slug: string }>>([]);
  const [report, setReport] = useState<string>('');
  const [fy, setFy] = useState<number>(new Date().getFullYear());
  const [run, setRun] = useState<any>(null);
  const [liquidity, setLiquidity] = useState<any>(null);
  const [fragility, setFragility] = useState<any>(null);
  const [varianceNotes, setVarianceNotes] = useState<Record<string, { commentary: string; modified: string; modified_by: string }>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>('');

  // v1.9.52 — honour any custom Accounting Dimension filters the user set
  // in the Run tab. The Briefing reads-only the shared scope; users don't
  // edit filters from here (would be a confusing UX duplication).
  const { filters: dimFilters, dimensions: activeDims } = useDimFilters();

  // Load reports list once.
  useEffect(() => {
    let cancelled = false;
    api.listReports()
      .then((rs) => {
        if (cancelled) return;
        const list = (rs || []) as any[];
        setReports(list);
        if (list.length && !report) setReport(list[0].slug || list[0].name);
      })
      .catch(() => { /* leave empty */ });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When report/fy changes, load the report run + liquidity in parallel.
  useEffect(() => {
    if (!report || !fy) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    setRun(null);
    setLiquidity(null);
    setFragility(null);
    setVarianceNotes({});

    const dimSnap = compactDimFilters(dimFilters);

    const runPromise = api.runReport({
      report, fiscal_year: fy, month_from: 0, month_to: 11,
      segment: 'total', prior_years: 1, comparison_mode: 'vs_budget',
      dimension_filters: dimSnap || null,
    });

    runPromise
      .then((r: any) => {
        if (cancelled) return;
        setRun(r);
        const company = r?.filters?.company;
        if (company) {
          api.getLiquidity(company, fy, 6, 'committed', 'realistic', undefined, undefined, dimSnap || null)
            .then((l) => { if (!cancelled) setLiquidity(l); })
            .catch(() => { if (!cancelled) setLiquidity(null); });
          api.getFragilityRadar(company, fy, 5)
            .then((f) => { if (!cancelled) setFragility(f); })
            .catch(() => { if (!cancelled) setFragility(null); });
        }
        // Variance commentary is per-report, not per-company.
        api.listVarianceNotes(report, fy)
          .then((notes) => {
            if (cancelled) return;
            const map: Record<string, any> = {};
            for (const n of (notes || [])) map[n.row_key] = n;
            setVarianceNotes(map);
          })
          .catch(() => { if (!cancelled) setVarianceNotes({}); });
      })
      .catch((e: any) => {
        if (cancelled) return;
        setError(String(e?.message || e || 'Failed to load.'));
      })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [report, fy, dimFilters]);

  return (
    <div className="dash-shell briefing-shell">
      <CfoBrief />
      {/* Header */}
      <div className="dash-head">
        <div>
          <h2 className="dash-title">CFO Briefing</h2>
          <span className="dash-sub">The financial situation, on one screen, structured around the questions a CFO asks first.</span>
          <ActiveDimFiltersChips />
        </div>
        <div className="dash-controls">
          <label>
            <span className="flbl">{t('Report')}</span>
            <select value={report} onChange={(e) => setReport(e.target.value)}>
              {reports.map((r) => (
                <option key={r.name} value={r.slug || r.name}>{r.report_name}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="flbl">{t('Fiscal Year')}</span>
            <input type="number" value={fy} onChange={(e) => setFy(Number(e.target.value) || fy)} style={{ width: 84 }} />
          </label>
        </div>
      </div>

      {loading && <div className="dash-empty">Loading the briefing…</div>}
      {error && <div className="dash-empty"><h2>{t('Briefing unavailable')}</h2><p>{error}</p></div>}

      {!loading && !error && run && (
        <>
          <SolvencySection run={run} liquidity={liquidity} />
          <ProfitSection run={run} />
          <FragilitySection fragility={fragility} currency={run?.filters?.company_currency || ''} />
          <VarianceSection run={run} varianceNotes={varianceNotes} currency={run?.filters?.company_currency || ''} />
          <ActionsSection
            run={run}
            liquidity={liquidity}
            fragility={fragility}
            varianceNotes={varianceNotes}
            currency={run?.filters?.company_currency || ''}
          />
          <TrendSection report={report} fy={fy} currency={run?.filters?.company_currency || ''} />
          <BriefingFooterNote />
        </>
      )}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────────
 * SECTION 1 — Solvency at a glance
 * Answers: "Am I solvent? How long can this last?"
 */
function SolvencySection({ run, liquidity }: { run: any; liquidity: any }) {
  const currency = liquidity?.currency || run?.filters?.company_currency || '';
  const currentCash = Number(liquidity?.current_cash || 0);
  const proj = liquidity?.projection;
  const projRows = (proj?.rows || []) as any[];
  const closings = projRows.map((r) => Number(r.closing || 0));
  const minProjected = closings.length ? Math.min(...closings) : currentCash;
  const minIdx = closings.length ? closings.indexOf(minProjected) : -1;
  const goesNegative = minProjected < 0;

  // Trailing-3-month burn rate from the projection's expected_out.
  // Honest: we use up to the first 3 months of projected outflow as proxy
  // for "current burn" — that's the data we have. If projection is empty,
  // we don't compute runway at all.
  const burnRate = useMemo(() => {
    if (!projRows.length) return null;
    const firstThree = projRows.slice(0, Math.min(3, projRows.length));
    const totalOut = firstThree.reduce((s, r) => s + Number(r.expected_out || 0), 0);
    const totalIn = firstThree.reduce((s, r) => s + Number(r.expected_in || 0), 0);
    const netBurn = totalOut - totalIn;
    if (netBurn <= 0) return 0; // cash positive — not burning
    return netBurn / firstThree.length; // monthly net burn
  }, [projRows]);

  const runwayMonths = useMemo(() => {
    if (burnRate == null || burnRate === 0) return null;
    if (currentCash <= 0) return 0;
    return currentCash / burnRate;
  }, [burnRate, currentCash]);

  // Status: derived from the projection's worst point AND the runway.
  // Conservative rule of thumb a real CFO would use.
  let status: 'green' | 'amber' | 'red';
  if (goesNegative) status = 'red';
  // v1.9.43 — cash today reading as zero is a setup/data issue we should
  // not paint as Green. Amber is the honest call: we don't know enough.
  else if (currentCash <= 0) status = 'amber';
  else if (runwayMonths != null && runwayMonths < 6) status = 'red';
  else if (runwayMonths != null && runwayMonths < 12) status = 'amber';
  else status = 'green';

  // Headline sentence — honest synthesis.
  const headline = buildSolvencyHeadline({
    currentCash, currency, goesNegative, minProjected, minIdx, projRows, runwayMonths, burnRate,
  });

  return (
    <section className="briefing-section">
      <SectionHeader
        number={1}
        title="Am I solvent?"
        subtitle="Cash today, cash in the projection window, and runway at the current burn rate."
        statusPill={status}
      />

      <div className="briefing-headline">{headline}</div>

      <div className="briefing-grid solvency-grid">
        <KpiCell
          label="Cash today"
          value={fmt(currentCash)}
          unit={currency}
          tone={currentCash > 0 ? 'neutral' : 'bad'}
        />
        <KpiCell
          label="Projected low point"
          value={projRows.length ? fmt(minProjected) : '—'}
          unit={currency}
          sub={projRows.length && minIdx >= 0 ? `${MONTHS[projRows[minIdx].month]} ${String(projRows[minIdx].year).slice(2)}` : ''}
          tone={goesNegative ? 'bad' : 'neutral'}
        />
        <KpiCell
          label="Runway"
          value={runwayMonths == null ? '—' : (runwayMonths > 60 ? '60+' : runwayMonths.toFixed(1))}
          unit={runwayMonths == null ? '' : 'months'}
          sub={
            currentCash <= 0
              ? 'no cash balance to calculate from'
              : burnRate == null
                ? ''
                : (burnRate === 0 ? 'cash-positive operating' : `~${fmtShort(burnRate)}/mo net burn`)
          }
          tone={status === 'red' ? 'bad' : status === 'amber' ? 'warn' : 'good'}
        />
      </div>

      {projRows.length > 0 && <SolvencyChart rows={projRows} currentCash={currentCash} />}
    </section>
  );
}

function buildSolvencyHeadline(p: {
  currentCash: number; currency: string;
  goesNegative: boolean; minProjected: number; minIdx: number; projRows: any[];
  runwayMonths: number | null; burnRate: number | null;
}): React.JSX.Element {
  const { currentCash, currency, goesNegative, minProjected, minIdx, projRows, runwayMonths, burnRate } = p;

  // No projection at all? Just state cash. Don't fabricate.
  if (projRows.length === 0) {
    return <>Cash position: <strong>{currency} {fmt(currentCash)}</strong>. Projection unavailable for runway estimate.</>;
  }

  // v1.9.43 — honest handling of "cash today is zero or unknown". This often
  // means the bench has no accounts tagged as Bank/Cash (a setup issue, not
  // a real cash position), or the cash balance is genuinely depleted. Either
  // way, "cash-positive operating" is the wrong message — it implies the
  // business is healthy when we don't actually know the starting cash.
  if (currentCash <= 0) {
    return (
      <>
        <span className="briefing-warn-mark">⚠</span>{' '}
        <strong>Cash today reads as {fmt(currentCash)} {currency}.</strong> Either the cash position is genuinely depleted,
        or no accounts in ERP are tagged with <em>{t('Account Type')}</em> Bank or Cash — Insight reads the balance
        from those tags. Review the chart of accounts before relying on the figures below.
      </>
    );
  }

  // Cash goes negative — the only thing that matters is the warning.
  if (goesNegative) {
    const monthLabel = `${MONTHS[projRows[minIdx].month]} ${String(projRows[minIdx].year).slice(2)}`;
    return (
      <>
        <span className="briefing-warn-mark">⚠</span> Projection turns negative in <strong>{monthLabel}</strong>.
        Low point: <strong>{fmt(minProjected)} {currency}</strong>. Review collection assumptions or accelerate receivables.
      </>
    );
  }

  // Cash positive operating — burn is zero or negative. Honest version: don't
  // claim a runway figure that's misleading; state the operating reality.
  if (burnRate === 0) {
    return <>Cash-positive operating: <strong>{fmt(currentCash)} {currency}</strong> today, projection stays positive across the window.</>;
  }

  // Healthy case — state cash, runway estimate as approximate, qualifier word.
  if (runwayMonths != null && runwayMonths >= 12) {
    return <>Cash position is healthy: <strong>{fmt(currentCash)} {currency}</strong> today, around <strong>{runwayMonths > 60 ? '60+' : runwayMonths.toFixed(0)} months</strong> of runway at the current burn rate.</>;
  }

  // Amber — honest framing.
  if (runwayMonths != null && runwayMonths >= 6) {
    return <>Cash position is adequate but tightening: <strong>{fmt(currentCash)} {currency}</strong> today, approximately <strong>{runwayMonths.toFixed(1)} months</strong> of runway. Worth monitoring.</>;
  }

  // Red — short runway.
  if (runwayMonths != null) {
    return <><span className="briefing-warn-mark">⚠</span> Short runway: <strong>{fmt(currentCash)} {currency}</strong> today covers approximately <strong>{runwayMonths.toFixed(1)} months</strong> at the current burn rate. Action needed.</>;
  }

  // Fallback — runway not computable, state what we have.
  return <>Cash position: <strong>{fmt(currentCash)} {currency}</strong>. Projection stays positive across the window.</>;
}

function SolvencyChart({ rows, currentCash }: { rows: any[]; currentCash: number }) {
  if (!rows.length) return null;
  const W = 760, H = 160, PAD_L = 56, PAD_R = 14, PAD_T = 10, PAD_B = 24;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  // Include "current cash" as the first point on the line.
  const series: Array<{ label: string; closing: number; idx: number }> = [{ label: 'Now', closing: currentCash, idx: -1 }];
  rows.forEach((r, i) => series.push({
    label: MONTHS[r.month] + ' ' + String(r.year).slice(2),
    closing: Number(r.closing || 0),
    idx: i,
  }));
  const ys = series.map((s) => s.closing);
  const minVal = Math.min(...ys, 0);
  const maxVal = Math.max(...ys, 0);
  const span = (maxVal - minVal) || 1;
  const stepX = innerW / Math.max(1, series.length - 1);
  const y = (v: number) => PAD_T + innerH - ((v - minVal) / span) * innerH;
  const pts = series.map((s, i) => [PAD_L + i * stepX, y(s.closing)] as [number, number]);
  const path = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const zeroY = y(0);
  const hasZero = minVal < 0;
  return (
    <svg className="dash-chart" viewBox={`0 0 ${W} ${H}`} width="100%" style={{ marginTop: 12 }}>
      <line x1={PAD_L} y1={y(minVal)} x2={W - PAD_R} y2={y(minVal)} stroke="#ece9e3" strokeWidth="1" />
      <line x1={PAD_L} y1={y(maxVal)} x2={W - PAD_R} y2={y(maxVal)} stroke="#ece9e3" strokeWidth="1" />
      <text x={PAD_L - 6} y={y(maxVal) + 4} textAnchor="end" fontSize="10" fill="#6e6a63">{fmtShort(maxVal)}</text>
      <text x={PAD_L - 6} y={y(minVal) + 4} textAnchor="end" fontSize="10" fill="#6e6a63">{fmtShort(minVal)}</text>
      {hasZero && <line x1={PAD_L} y1={zeroY} x2={W - PAD_R} y2={zeroY} stroke="#a32d2d" strokeWidth="1" strokeDasharray="3 3" />}
      <path d={path} fill="none" stroke="#0c447c" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      {pts.map((p, i) => (
        <g key={i}>
          <circle cx={p[0]} cy={p[1]} r="3" fill={i === 0 ? '#6e6a63' : '#0c447c'} />
          <text x={p[0]} y={H - 8} textAnchor="middle" fontSize="9.5" fill="#6e6a63">{series[i].label}</text>
        </g>
      ))}
    </svg>
  );
}

/* ────────────────────────────────────────────────────────────────────────────
 * SECTION 2 — Profit & growth
 * Answers: "Am I making money, and is the trend right?"
 */
function ProfitSection({ run }: { run: any }) {
  const currency = run?.filters?.company_currency || '';
  const months = [0,1,2,3,4,5,6,7,8,9,10,11];

  const rowsByKey = useMemo(() => {
    const map: Record<string, any> = {};
    for (const r of (run?.current?.rows || [])) map[r.key] = r;
    return map;
  }, [run]);
  const priorRowsByKey = useMemo(() => {
    const map: Record<string, any> = {};
    const first = run?.priors?.[0]?.rows || [];
    for (const r of first) map[r.key] = r;
    return map;
  }, [run]);
  const budgetByKey = useMemo(() => {
    const map: Record<string, any> = {};
    for (const r of (run?.budget?.rows || [])) map[r.key] = r;
    return map;
  }, [run]);

  function sumMonthly(monthlyMap: any): number {
    if (!monthlyMap) return 0;
    let total = 0;
    for (const m of months) total += Number(monthlyMap[m] || 0);
    return total;
  }

  const KPI_DEFS: Array<{ key: string; label: string }> = [
    { key: 'total_revenue', label: 'Revenue' },
    { key: 'gross_profit', label: 'Gross Profit' },
    { key: 'ebitda', label: 'EBITDA' },
    { key: 'net_income', label: 'Net Income' },
  ];

  const tiles = KPI_DEFS.map((d) => {
    const cur = rowsByKey[d.key];
    const py = priorRowsByKey[d.key];
    const bud = budgetByKey[d.key];
    const val = cur ? sumMonthly(cur.monthly) : 0;
    const pyVal = py ? sumMonthly(py.monthly) : null;
    const budVal = bud ? sumMonthly(bud.monthly) : null;
    const yoy = pyVal != null && pyVal !== 0 ? (val - pyVal) / Math.abs(pyVal) : null;
    const vsBudget = budVal != null && budVal !== 0 ? (val - budVal) / Math.abs(budVal) : null;
    const series = cur ? months.map((m) => Number(cur.monthly?.[m] || 0)) : [];
    return { ...d, val, pyVal, budVal, yoy, vsBudget, series };
  });

  // Section-level status — green/amber/red driven by net income vs plan.
  const ni = tiles.find((t) => t.key === 'net_income');
  let status: 'green' | 'amber' | 'red';
  if (ni && ni.val < 0) status = 'red';
  else if (ni?.vsBudget != null && ni.vsBudget < -0.1) status = 'amber';
  else if (ni?.vsBudget != null && ni.vsBudget < -0.2) status = 'red';
  else status = 'green';

  // Section headline — derived from the strongest signal we have.
  const headline = buildProfitHeadline(tiles, currency);

  return (
    <section className="briefing-section">
      <SectionHeader
        number={2}
        title="Am I profitable, and is the trend right?"
        subtitle="Revenue through to net income, with year-on-year and vs-plan context where the data supports it."
        statusPill={status}
      />

      <div className="briefing-headline">{headline}</div>

      <div className="briefing-grid profit-grid">
        {tiles.map((t) => <ProfitTile key={t.key} tile={t} currency={currency} />)}
      </div>
    </section>
  );
}

function buildProfitHeadline(tiles: any[], currency: string): React.JSX.Element {
  const rev = tiles.find((t) => t.key === 'total_revenue');
  const ni = tiles.find((t) => t.key === 'net_income');
  if (!rev || !ni) return <>Profit & loss is being computed — KPI rows not yet defined for this report.</>;

  // Build only with what we have. Two key facts: revenue level, NI position.
  const parts: React.JSX.Element[] = [];

  // Revenue framing.
  if (rev.yoy != null && Math.abs(rev.yoy) >= 0.02) {
    parts.push(<span key="rev-yoy">Revenue {rev.yoy >= 0 ? 'up' : 'down'} <strong>{pct(Math.abs(rev.yoy), 0)}</strong> year-on-year</span>);
  } else if (rev.yoy != null) {
    parts.push(<span key="rev-flat">Revenue broadly flat year-on-year</span>);
  } else {
    parts.push(<span key="rev-level">Revenue at <strong>{fmtShort(rev.val)} {currency}</strong></span>);
  }
  if (rev.vsBudget != null && Math.abs(rev.vsBudget) >= 0.02) {
    parts.push(<span key="rev-bud">, {rev.vsBudget >= 0 ? 'ahead of' : 'behind'} plan by <strong>{pct(Math.abs(rev.vsBudget), 0)}</strong></span>);
  }

  // Net income framing.
  if (ni.val < 0) {
    parts.push(<span key="ni-loss">. Net result is a loss of <strong>{fmtShort(Math.abs(ni.val))} {currency}</strong></span>);
  } else if (ni.vsBudget != null && Math.abs(ni.vsBudget) >= 0.05) {
    parts.push(<span key="ni-vbud">. Net income {ni.vsBudget >= 0 ? 'ahead of' : 'behind'} plan by <strong>{pct(Math.abs(ni.vsBudget), 0)}</strong></span>);
  } else {
    parts.push(<span key="ni-level">. Net income: <strong>{fmtShort(ni.val)} {currency}</strong></span>);
  }
  parts.push(<span key="period">.</span>);

  return <>{parts}</>;
}

function ProfitTile({ tile, currency }: { tile: any; currency: string }) {
  return (
    <div className="briefing-tile">
      <div className="briefing-tile-head">
        <span className="briefing-tile-lbl">{tile.label}</span>
        <span className="briefing-tile-cur">{currency}</span>
      </div>
      <div className={'briefing-tile-val' + (tile.val < 0 ? ' is-neg' : '')}>{fmt(tile.val)}</div>
      <div className="briefing-tile-deltas">
        {tile.yoy != null && (
          <span className={'briefing-delta ' + (tile.yoy >= 0 ? 'pos' : 'neg')}>
            {tile.yoy >= 0 ? '▲' : '▼'} {pct(Math.abs(tile.yoy), 0)} Y/Y
          </span>
        )}
        {tile.vsBudget != null && (
          <span className={'briefing-delta ' + (tile.vsBudget >= 0 ? 'pos' : 'neg')}>
            {tile.vsBudget >= 0 ? '+' : ''}{pct(tile.vsBudget, 0)} vs plan
          </span>
        )}
      </div>
      <Sparkline data={tile.series} />
    </div>
  );
}

function Sparkline({ data }: { data: number[] }) {
  if (!data || data.length === 0) return null;
  const min = Math.min(...data, 0);
  const max = Math.max(...data, 0);
  const span = (max - min) || 1;
  const W = 200, H = 32;
  const stepX = W / Math.max(1, data.length - 1);
  const pts = data.map((v, i) => [i * stepX, H - ((v - min) / span) * H] as [number, number]);
  const path = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ marginTop: 4 }}>
      <path d={path} fill="none" stroke="#0c447c" strokeWidth="1.5" />
    </svg>
  );
}

/* ────────────────────────────────────────────────────────────────────────────
 * Common components
 */
function SectionHeader({ number, title, subtitle, statusPill }: {
  number: number; title: string; subtitle: string;
  statusPill: 'green' | 'amber' | 'red';
}) {
  const LBL = { green: 'Healthy', amber: 'Watch', red: 'Attention' };
  return (
    <div className="briefing-section-head">
      <div>
        <div className="briefing-section-q">
          <span className="briefing-section-num">{number}.</span>
          <h3 className="briefing-section-title">{title}</h3>
        </div>
        <div className="briefing-section-sub">{subtitle}</div>
      </div>
      <div className={'briefing-pill briefing-pill-' + statusPill}>{LBL[statusPill]}</div>
    </div>
  );
}

function KpiCell({ label, value, unit, sub, tone }: {
  label: string; value: string; unit?: string; sub?: string;
  tone: 'good' | 'warn' | 'bad' | 'neutral';
}) {
  return (
    <div className={'briefing-kpi briefing-kpi-' + tone}>
      <div className="briefing-kpi-lbl">{label}</div>
      <div className="briefing-kpi-val">
        {value}
        {unit && <span className="briefing-kpi-unit"> {unit}</span>}
      </div>
      {sub && <div className="briefing-kpi-sub">{sub}</div>}
    </div>
  );
}

function BriefingFooterNote() {
  return (
    <div className="briefing-footer">
      <strong>About this view.</strong> The CFO Briefing answers the five questions a finance leader asks first.
      Sections 1 (Solvency), 2 (Profit & growth), 3 (Fragility radar), 4 (What broke from plan),
      5 (Things to look at), and 6 (Multi-year trend) are all live. <strong>"Things to look at" surfaces triggers for human review — never strategic recommendations.</strong>
      Every synthesis sentence is derived strictly from the report data — when a comparison isn't available, the briefing says nothing rather than fabricating one.
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────────
 * SECTION 3 — Fragility radar (v1.9.41)
 * Answers: "Where is the business fragile? Where am I exposed?"
 *
 * Four concentration metrics, each with a status pill, a one-sentence
 * synthesis, and a top-N detail table. Branch metric appears only when
 * branch data is tagged on sales invoices.
 */
function FragilitySection({ fragility, currency }: { fragility: any; currency: string }) {
  if (!fragility) {
    return (
      <section className="briefing-section">
        <SectionHeader
          number={3}
          title="Where is the business fragile?"
          subtitle="Customer, supplier, ageing, and (where available) branch concentration. Loading…"
          statusPill="green"
        />
      </section>
    );
  }
  const blocks = fragility.blocks || [];
  // Section-level status: worst of all blocks.
  const order: Record<string, number> = { green: 0, amber: 1, red: 2 };
  let worst: 'green' | 'amber' | 'red' = 'green';
  for (const b of blocks) {
    if (order[b.status] > order[worst]) worst = b.status;
  }
  // Section headline: a one-paragraph summary referencing only the data
  // present. Lead with whichever block is worst.
  const headline = buildFragilityHeadline(blocks);
  return (
    <section className="briefing-section">
      <SectionHeader
        number={3}
        title="Where is the business fragile?"
        subtitle="Customer, supplier, ageing, and branch concentration — where a single counterparty or category could hurt cash."
        statusPill={worst}
      />
      <div className="briefing-headline">{headline}</div>

      <div className="fragility-grid">
        {blocks.map((b: any, idx: number) => (
          <FragilityBlockCard key={idx} block={b} currency={currency} />
        ))}
      </div>
    </section>
  );
}

function buildFragilityHeadline(blocks: any[]): React.JSX.Element {
  if (!blocks.length) return <>No concentration data available for this report.</>;
  // Sort by severity, take the most serious as the lead.
  const order: Record<string, number> = { red: 0, amber: 1, green: 2 };
  const sorted = [...blocks].sort((a, b) => order[a.status] - order[b.status]);
  const worst = sorted[0];
  const others = sorted.slice(1);
  // If everything is green, single calming sentence.
  if (worst.status === 'green' && others.every((b) => b.status === 'green')) {
    return <>Concentration is within the comfort zone across all measured dimensions. No single counterparty or category is large enough to materially threaten the business.</>;
  }
  // Otherwise, lead with the worst.
  return (
    <>
      <strong>{worst.metric}</strong> is the most prominent fragility: {worst.headline.toLowerCase()}
      {others.filter((b) => b.status !== 'green').length > 0 && (
        <> Other concerns: {others.filter((b) => b.status !== 'green').map((b) => b.metric.toLowerCase()).join(', ')}.</>
      )}
    </>
  );
}

function FragilityBlockCard({ block, currency }: { block: any; currency: string }) {
  const details = block.details || {};
  return (
    <div className={'fragility-card fragility-' + block.status}>
      <div className="fragility-card-head">
        <span className="fragility-card-metric">{block.metric}</span>
        <span className={'briefing-pill briefing-pill-' + block.status}>
          {block.status === 'green' ? 'Healthy' : block.status === 'amber' ? 'Watch' : 'Attention'}
        </span>
      </div>
      <div className="fragility-card-headline">{block.headline}</div>
      {block.metric === 'Receivables ageing skew' ? (
        <AgeingSkewDetail details={details} currency={currency} />
      ) : (
        <ConcentrationDetail details={details} currency={currency} />
      )}
      <div className="fragility-card-explainer" title={details.threshold_explainer}>
        <i className="ti ti-info-circle" aria-hidden /> Thresholds
      </div>
    </div>
  );
}

function ConcentrationDetail({ details, currency }: { details: any; currency: string }) {
  const topN = details.top_n || [];
  const total = details.total || 0;
  const top3 = details.top_3_share || 0;
  const hhi = details.hhi || 0;
  return (
    <>
      <div className="fragility-metrics-row">
        <div className="fragility-mini">
          <div className="fragility-mini-lbl">{t('Top 3')}</div>
          <div className="fragility-mini-val">{top3.toFixed(0)}%</div>
        </div>
        <div className="fragility-mini">
          <div className="fragility-mini-lbl">{t('HHI')}</div>
          <div className="fragility-mini-val">{Math.round(hhi).toLocaleString()}</div>
          <div className="fragility-mini-sub">{hhi < 1500 ? 'low' : hhi < 2500 ? 'moderate' : 'high'}</div>
        </div>
        <div className="fragility-mini">
          <div className="fragility-mini-lbl">{t('Total')}</div>
          <div className="fragility-mini-val">{fmtShort(total)}</div>
          <div className="fragility-mini-sub">{currency}</div>
        </div>
      </div>
      <table className="fragility-toptable">
        <thead>
          <tr><th>#</th><th>{t('Name')}</th><th className="num">{t('Amount')}</th><th className="num">{t('Share')}</th></tr>
        </thead>
        <tbody>
          {topN.map((r: any, i: number) => (
            <tr key={i}>
              <td className="num">{i + 1}</td>
              <td>{r.name}</td>
              <td className="num">{fmtShort(r.amount)}</td>
              <td className="num">{r.share.toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function AgeingSkewDetail({ details, currency }: { details: any; currency: string }) {
  const buckets = details.buckets || {};
  const total = details.total_outstanding || 0;
  if (total === 0) {
    return <div className="dash-sub">No outstanding receivables.</div>;
  }
  const ORDER = [
    { key: 'not_due', label: 'Not yet due', color: '#0c447c' },
    { key: '0_30', label: '0-30 days', color: '#6e9d4f' },
    { key: '30_60', label: '30-60 days', color: '#c9a227' },
    { key: '60_90', label: '60-90 days', color: '#d97706' },
    { key: '90_180', label: '90-180 days', color: '#b03a3a' },
    { key: '180_plus', label: '180+ days', color: '#7a1f1f' },
  ];
  let cum = 0;
  return (
    <>
      <div className="fragility-metrics-row">
        <div className="fragility-mini">
          <div className="fragility-mini-lbl">In 90+ days</div>
          <div className="fragility-mini-val">{(details.aged_90plus_share * 100).toFixed(0)}%</div>
          <div className="fragility-mini-sub">{fmtShort(details.aged_90plus_amount)} {currency}</div>
        </div>
        <div className="fragility-mini">
          <div className="fragility-mini-lbl">{t('Total AR')}</div>
          <div className="fragility-mini-val">{fmtShort(total)}</div>
          <div className="fragility-mini-sub">{currency}</div>
        </div>
      </div>
      <svg viewBox="0 0 100 8" preserveAspectRatio="none" width="100%" height="22" style={{ marginTop: 8, borderRadius: 3 }}>
        {ORDER.map((b) => {
          const amt = buckets[b.key] || 0;
          const w = (amt / total) * 100;
          const rect = <rect key={b.key} x={cum} y="0" width={w} height="8" fill={b.color} />;
          cum += w;
          return rect;
        })}
      </svg>
      <div className="fragility-ageing-legend">
        {ORDER.filter((b) => (buckets[b.key] || 0) > 0).map((b) => (
          <span key={b.key}>
            <span className="leg-sw" style={{ background: b.color }} />
            {b.label}: {fmtShort(buckets[b.key])}
          </span>
        ))}
      </div>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────────────────
 * SECTION 4 — What broke from plan (v1.9.42)
 * Answers: "What moved away from plan, in which direction, and why?"
 *
 * Differences from the dashboard variance panel:
 *   1. Ranks by *adverse* impact (not absolute magnitude) — a revenue
 *      shortfall ranks above a marketing overspend of the same dollar size.
 *   2. Shows variance commentary inline, not behind a click.
 *   3. Flags rows that need commentary but don't have one (a quiet nudge).
 *   4. Section headline summarises the *pattern*, not just the worst row.
 */

function VarianceSection({ run, varianceNotes, currency }: { run: any; varianceNotes: Record<string, any>; currency: string }) {
  const variance = useMemo(() => buildVarianceList(run), [run]);

  // Status: how bad is the worst variance, as a share of revenue?
  const status = computeVarianceStatus(variance, run);
  const headline = buildVarianceHeadline(variance, currency);

  return (
    <section className="briefing-section">
      <SectionHeader
        number={4}
        title="What broke from plan?"
        subtitle="Variances ranked by adverse impact — revenue shortfalls outrank expense beats of the same magnitude."
        statusPill={status}
      />
      <div className="briefing-headline">{headline}</div>

      {variance.length === 0 ? (
        <div className="dash-sub">No variance data — budget book may not be configured for this report.</div>
      ) : (
        <div className="variance-list">
          {variance.slice(0, 6).map((v) => {
            const note = varianceNotes[v.key];
            return <VarianceRow key={v.key} v={v} note={note} currency={currency} />;
          })}
        </div>
      )}
    </section>
  );
}

/* Build the ranked variance list.
 *
 * Important: the existing dashboard ranks by |gap| alone. We rank by
 * "adverse impact" — a revenue shortfall is adverse; a revenue beat is
 * favourable. For expense rows, the inverse: overspend is adverse, underspend
 * favourable. We classify rows by their "kind" in the report definition
 * (revenue/expense) when available, otherwise use a heuristic on the row key.
 */
function buildVarianceList(run: any): Array<{
  key: string;
  label: string;
  actual: number;
  budget: number;
  gap: number;
  gapPct: number | null;
  adverseImpact: number;  // positive = adverse (bad); negative = favourable
  isAdverse: boolean;
  kind: 'revenue' | 'expense' | 'unknown';
}> {
  if (!run?.budget?.rows?.length) return [];
  const months = [0,1,2,3,4,5,6,7,8,9,10,11];
  const sumMonthly = (m: any) => {
    if (!m) return 0;
    let t = 0;
    for (const i of months) t += Number(m[i] || 0);
    return t;
  };
  const budgetByKey: Record<string, any> = {};
  for (const r of run.budget.rows) budgetByKey[r.key] = r;
  const actualByKey: Record<string, any> = {};
  for (const r of (run.current?.rows || [])) actualByKey[r.key] = r;

  const result = [];
  for (const r of run.current?.rows || []) {
    const bud = budgetByKey[r.key];
    if (!bud) continue;
    const actual = sumMonthly(r.monthly);
    const budget = sumMonthly(bud.monthly);
    if (budget === 0 && actual === 0) continue;
    const gap = actual - budget;
    const gapPct = budget !== 0 ? gap / Math.abs(budget) : null;

    // Classify the row's kind.
    const kind = classifyRowKind(r.key, r.label);

    // Adverse impact: revenue-side, negative gap = bad (adverse).
    // Expense-side, positive gap = bad (adverse).
    let adverseImpact: number;
    if (kind === 'revenue') adverseImpact = -gap;        // shortfall = positive adverse
    else if (kind === 'expense') adverseImpact = gap;    // overspend = positive adverse
    else adverseImpact = Math.abs(gap) * 0.5;            // unknown: dampen, rank lower

    result.push({
      key: r.key,
      label: r.label || r.key,
      actual, budget, gap, gapPct, adverseImpact,
      isAdverse: adverseImpact > 0,
      kind,
    });
  }

  // Sort: most adverse first (positive = bad), then favourable last.
  result.sort((a, b) => b.adverseImpact - a.adverseImpact);
  return result;
}

/** Heuristic classification of a row's kind by its key/label.
 *  Conservative: only classifies when reasonably sure; otherwise unknown.
 */
function classifyRowKind(key: string, label: string): 'revenue' | 'expense' | 'unknown' {
  const k = (key || '').toLowerCase();
  const l = (label || '').toLowerCase();
  const text = k + ' ' + l;
  const REV_HINTS = ['revenue', 'sales', 'income', 'turnover', 'gross_profit', 'profit', 'ebitda', 'net_income', 'margin'];
  const EXP_HINTS = ['expense', 'cost', 'cogs', 'opex', 'salary', 'rent', 'utilities', 'overhead', 'depreciation', 'amortization', 'interest', 'tax'];
  // Profit-line rows: treat as revenue-side (a shortfall is adverse).
  for (const h of REV_HINTS) if (text.includes(h)) return 'revenue';
  for (const h of EXP_HINTS) if (text.includes(h)) return 'expense';
  return 'unknown';
}

function computeVarianceStatus(variance: any[], run: any): 'green' | 'amber' | 'red' {
  if (!variance.length) return 'green';
  // Revenue total as the denominator for "material" — if absent, use the
  // largest absolute budget value among the rows we have.
  const revRow = variance.find((v) => v.kind === 'revenue' && /revenue|sales|turnover/.test((v.label || '').toLowerCase()));
  const denom = revRow ? Math.abs(revRow.budget) : Math.max(...variance.map((v) => Math.abs(v.budget)));
  if (!denom) return 'green';
  const worst = variance[0];
  if (!worst.isAdverse) return 'green';
  const severityPct = worst.adverseImpact / denom;
  if (severityPct < 0.03) return 'green';     // <3% — broadly on plan
  if (severityPct < 0.10) return 'amber';     // 3-10% — meaningful
  return 'red';                               // >10% — material
}

function buildVarianceHeadline(variance: any[], currency: string): React.JSX.Element {
  if (!variance.length) {
    return <>Budget comparison unavailable — no budget book is configured for this report.</>;
  }
  const adverse = variance.filter((v) => v.isAdverse);
  if (adverse.length === 0) {
    return <>Performance is broadly in line with plan — no material adverse variances to explain. Top movers below are favourable or near zero.</>;
  }
  const worst = adverse[0];
  const others = adverse.slice(1, 3);
  return (
    <>
      Largest adverse variance: <strong>{worst.label}</strong> is {fmtShort(worst.adverseImpact)} {currency}{' '}
      {worst.kind === 'revenue' ? 'short of plan' : 'over plan'}
      {others.length > 0 && (
        <>. Other adverse movers: {others.map((v) => v.label).join(', ')}</>
      )}
      .
    </>
  );
}

function VarianceRow({ v, note, currency }: { v: any; note: any; currency: string }) {
  const isAdverse = v.isAdverse;
  return (
    <div className={'variance-row variance-row-' + (isAdverse ? 'adverse' : 'favourable')}>
      <div className="variance-row-top">
        <div className="variance-row-label">
          <span className="variance-row-name">{v.label}</span>
          <span className={'variance-row-kind variance-kind-' + v.kind}>
            {v.kind === 'revenue' ? 'income side' : v.kind === 'expense' ? 'cost side' : ''}
          </span>
        </div>
        <div className="variance-row-numbers">
          <div className="variance-num-cell">
            <div className="variance-num-lbl">{t('Actual')}</div>
            <div className="variance-num-val">{fmtShort(v.actual)}</div>
          </div>
          <div className="variance-num-cell">
            <div className="variance-num-lbl">{t('Budget')}</div>
            <div className="variance-num-val">{fmtShort(v.budget)}</div>
          </div>
          <div className="variance-num-cell">
            <div className="variance-num-lbl">{t('Gap')}</div>
            <div className={'variance-num-val ' + (isAdverse ? 'is-bad' : 'is-good')}>
              {v.gap >= 0 ? '+' : ''}{fmtShort(v.gap)}
            </div>
          </div>
          <div className="variance-num-cell">
            <div className="variance-num-lbl">%</div>
            <div className={'variance-num-val ' + (isAdverse ? 'is-bad' : 'is-good')}>
              {v.gapPct == null ? '—' : (v.gap >= 0 ? '+' : '') + (v.gapPct * 100).toFixed(0) + '%'}
            </div>
          </div>
        </div>
      </div>
      {note?.commentary ? (
        <div className="variance-commentary">
          <div className="variance-commentary-text">{note.commentary}</div>
          <div className="variance-commentary-meta">
            {note.modified_by && <>— {note.modified_by}</>}
          </div>
        </div>
      ) : isAdverse && Math.abs(v.gap) > 0 ? (
        <div className="variance-needs-note">
          <i className="ti ti-message-2" aria-hidden /> No commentary yet. Add an explanation on the Dashboard's variance panel.
        </div>
      ) : null}
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────────
 * SECTION 5 — Things to look at (v1.9.46)
 *
 * Derives data-grounded triggers from the other sections' data. Deliberate
 * framing choices:
 *   - Called "Things to look at", not "Actions". This is a list of TRIGGERS
 *     for human review, not strategic recommendations.
 *   - Each item: what was detected, why it matters, suggested first step.
 *     First steps are always "review the X panel" — never strategic advice
 *     like "cut spending" or "raise prices".
 *   - Capped at 8 items. A list of 20 = a list of 0 (overwhelming, ignored).
 *   - Empty when nothing is wrong — no fabricated busy-work.
 *   - Severity drives sort order: high > medium > low.
 */

type ActionItem = {
  id: string;
  severity: 'high' | 'medium' | 'low';
  category: 'solvency' | 'profit' | 'fragility' | 'variance';
  title: string;
  detail: string;
  firstStep: string;
};

function ActionsSection({ run, liquidity, fragility, varianceNotes, currency }: {
  run: any; liquidity: any; fragility: any; varianceNotes: Record<string, any>; currency: string;
}) {
  const items = useMemo(
    () => buildActionItems({ run, liquidity, fragility, varianceNotes, currency }),
    [run, liquidity, fragility, varianceNotes, currency]
  );

  // Section status: worst item drives the pill.
  const ord: Record<string, number> = { high: 2, medium: 1, low: 0 };
  const worst = items.reduce((w, it) => (ord[it.severity] > ord[w] ? it.severity : w), 'low' as 'high' | 'medium' | 'low');
  const status: 'green' | 'amber' | 'red' =
    items.length === 0 ? 'green' :
    worst === 'high' ? 'red' :
    worst === 'medium' ? 'amber' : 'green';

  return (
    <section className="briefing-section">
      <SectionHeader
        number={5}
        title="Things to look at"
        subtitle="Data-grounded triggers for human review — not strategic recommendations. Each item points to a panel where you can dig deeper."
        statusPill={status}
      />
      {items.length === 0 ? (
        <div className="briefing-headline">
          No flags detected — performance, liquidity, and concentration are all within healthy ranges. Nothing in the data calls for immediate attention.
        </div>
      ) : (
        <>
          <div className="briefing-headline">
            <strong>{items.length} item{items.length === 1 ? '' : 's'}</strong> to review{' '}
            {items.filter((i) => i.severity === 'high').length > 0 && (
              <>— including <strong>{items.filter((i) => i.severity === 'high').length} high-priority</strong></>
            )}. Listed worst-first, capped at 8.
          </div>
          <div className="actions-list">
            {items.slice(0, 8).map((it) => <ActionRow key={it.id} item={it} />)}
          </div>
        </>
      )}
    </section>
  );
}

function ActionRow({ item }: { item: ActionItem }) {
  const SEV_LBL = { high: 'High', medium: 'Medium', low: 'Low' };
  const CAT_ICON = {
    solvency: 'ti-coin', profit: 'ti-trending-up',
    fragility: 'ti-radar', variance: 'ti-arrows-diff',
  };
  return (
    <div className={'action-row action-' + item.severity}>
      <div className="action-icon">
        <i className={'ti ' + CAT_ICON[item.category]} aria-hidden />
      </div>
      <div className="action-body">
        <div className="action-top">
          <span className="action-title">{item.title}</span>
          <span className={'action-sev action-sev-' + item.severity}>{SEV_LBL[item.severity]}</span>
        </div>
        <div className="action-detail">{item.detail}</div>
        <div className="action-step">
          <i className="ti ti-arrow-right" aria-hidden /> {item.firstStep}
        </div>
      </div>
    </div>
  );
}

/** Trigger logic — auditable, single function so every rule is in one place. */
function buildActionItems(p: {
  run: any; liquidity: any; fragility: any; varianceNotes: Record<string, any>; currency: string;
}): ActionItem[] {
  const { run, liquidity, fragility, varianceNotes, currency } = p;
  const items: ActionItem[] = [];

  // ── Solvency triggers ─────────────────────────────────────────────────
  const currentCash = Number(liquidity?.current_cash || 0);
  const projRows = (liquidity?.projection?.rows || []) as any[];
  const closings = projRows.map((r) => Number(r.closing || 0));
  const minProjected = closings.length ? Math.min(...closings) : null;
  const minIdx = closings.length && minProjected != null ? closings.indexOf(minProjected) : -1;

  if (minProjected != null && minProjected < 0 && minIdx >= 0) {
    const row = projRows[minIdx];
    const monthLabel = `${MONTHS[row.month]} ${String(row.year).slice(2)}`;
    items.push({
      id: 'solvency-negative',
      severity: 'high',
      category: 'solvency',
      title: 'Cash projection turns negative',
      detail: `In ${monthLabel}, projected closing cash is ${fmt(minProjected)} ${currency}. The current trajectory of inflows and outflows does not cover obligations.`,
      firstStep: 'Open the Dashboard → Forward cash projection. Check the Collection assumptions and Payment assumptions panels.',
    });
  }

  if (currentCash <= 0 && liquidity != null) {
    items.push({
      id: 'solvency-cash-zero',
      severity: 'medium',
      category: 'solvency',
      title: 'Cash today reads as zero',
      detail: 'Either the cash position is genuinely depleted, or no accounts in ERP are tagged with Account Type "Bank" or "Cash". Insight reads the balance from those tags.',
      firstStep: 'Open ERP → Chart of Accounts. Verify that bank/cash accounts have the correct Account Type.',
    });
  }

  // ── Variance triggers ─────────────────────────────────────────────────
  const variance = buildVarianceList(run);
  const adverse = variance.filter((v) => v.isAdverse);
  // Find a revenue denominator for "material" sizing.
  const revRow = variance.find((v) => v.kind === 'revenue' && /revenue|sales|turnover/.test((v.label || '').toLowerCase()));
  const revDenom = revRow ? Math.abs(revRow.budget) : 0;

  const materialAdverse = adverse.filter((v) => revDenom > 0 && v.adverseImpact / revDenom >= 0.10);
  for (const v of materialAdverse.slice(0, 3)) {
    const hasNote = varianceNotes[v.key]?.commentary;
    items.push({
      id: 'variance-' + v.key,
      severity: 'high',
      category: 'variance',
      title: `${v.label} is materially off plan`,
      detail: `${v.kind === 'revenue' ? 'Shortfall' : 'Overspend'} of ${fmtShort(v.adverseImpact)} ${currency} (${revDenom > 0 ? ((v.adverseImpact / revDenom) * 100).toFixed(0) + '% of revenue' : 'meaningful by absolute size'}).${hasNote ? '' : ' No commentary recorded yet.'}`,
      firstStep: hasNote
        ? 'Open the CFO Briefing → "What broke from plan" panel above to read the explanation in context.'
        : 'Open the Dashboard → variance panel and add a commentary note explaining the cause.',
    });
  }

  // Mid-tier: adverse without commentary, smaller but worth a note.
  const adverseNoNote = adverse.filter((v) => !varianceNotes[v.key]?.commentary && (revDenom === 0 || v.adverseImpact / revDenom < 0.10) && v.adverseImpact > 0);
  if (adverseNoNote.length >= 3) {
    items.push({
      id: 'variance-needs-notes',
      severity: 'low',
      category: 'variance',
      title: `${adverseNoNote.length} adverse variances have no commentary`,
      detail: 'Variances without an explanation are hard to act on later — and they look bad in a management pack.',
      firstStep: 'Open the Dashboard → variance panel. Hover the lines and add a one-sentence note on each.',
    });
  }

  // ── Profit triggers ───────────────────────────────────────────────────
  const ni = variance.find((v) => v.key === 'net_income');
  if (ni && ni.actual < 0) {
    items.push({
      id: 'profit-loss',
      severity: 'high',
      category: 'profit',
      title: 'Net result is a loss',
      detail: `Net income for the period is ${fmt(ni.actual)} ${currency}. The cumulative cost base exceeds the income generated.`,
      firstStep: 'Open the CFO Briefing → "What broke from plan" panel above to identify which lines drove the loss.',
    });
  } else if (ni && ni.gapPct != null && ni.gapPct < -0.10) {
    items.push({
      id: 'profit-behind-plan',
      severity: 'medium',
      category: 'profit',
      title: 'Net income is meaningfully behind plan',
      detail: `Net income is ${fmtShort(Math.abs(ni.gap))} ${currency} behind plan (${Math.abs((ni.gapPct || 0) * 100).toFixed(0)}%).`,
      firstStep: 'Open the CFO Briefing → "What broke from plan" panel above for the breakdown.',
    });
  }

  // ── Fragility triggers ────────────────────────────────────────────────
  // Pull each block from the fragility radar response. Red status = a real
  // concentration concern; we don't flag amber as an action item (those are
  // already visible in Section 3 — no need to repeat as a "thing to look at").
  for (const block of (fragility?.blocks || [])) {
    if (block.status !== 'red') continue;
    if (block.metric === 'Customer concentration') {
      items.push({
        id: 'fragility-customer',
        severity: 'high',
        category: 'fragility',
        title: 'Customer concentration is high',
        detail: `${block.headline} Losing one of the top customers would materially affect the business.`,
        firstStep: 'Open the CFO Briefing → "Where is the business fragile?" panel above. Identify the top 3 by name and assess retention risk.',
      });
    } else if (block.metric === 'Supplier concentration') {
      items.push({
        id: 'fragility-supplier',
        severity: 'high',
        category: 'fragility',
        title: 'Supplier concentration is high',
        detail: `${block.headline} Supply disruption from a single supplier would directly affect operations.`,
        firstStep: 'Open the CFO Briefing → "Where is the business fragile?" panel above. Identify the top suppliers and assess supply-chain continuity.',
      });
    } else if (block.metric === 'Receivables ageing skew') {
      items.push({
        id: 'fragility-ageing',
        severity: 'high',
        category: 'fragility',
        title: 'A large share of AR is significantly overdue',
        detail: `${block.headline}`,
        firstStep: 'Open the Dashboard → Receivables ageing block. Identify the customers in the 90+ buckets and begin collection action.',
      });
    } else if (block.metric === 'Branch concentration') {
      items.push({
        id: 'fragility-branch',
        severity: 'medium',
        category: 'fragility',
        title: 'Revenue is concentrated in one branch',
        detail: `${block.headline}`,
        firstStep: 'Open the CFO Briefing → "Where is the business fragile?" panel above. Consider geographic-risk implications.',
      });
    }
  }

  // Sort by severity: high → medium → low. Stable within each tier.
  const order = { high: 0, medium: 1, low: 2 };
  items.sort((a, b) => order[a.severity] - order[b.severity]);
  return items;
}

/* ────────────────────────────────────────────────────────────────────────────
 * SECTION 6 — Multi-year trend (v1.9.47)
 * Answers: "Are we growing? Are margins compressing? Which part is driving it?"
 *
 * Inspired by the CFI dashboard template but scoped honestly:
 *   - P&L rows only (revenue, gross profit, EBITDA, net income, margin %).
 *   - Historical actuals only — no forecast extension.
 *   - User picks metric + breakdown (Total or By Branch).
 *   - Synthesis sentence states only what the data supports.
 *
 * Performance note: lazy-loaded. We DON'T fetch on briefing mount because
 * the trend endpoint loops the engine N times — that would add 2-3 seconds
 * to the briefing's first paint. Instead we fetch only when this section
 * scrolls into view OR when the user changes a control.
 */

const TREND_METRICS = [
  { key: 'total_revenue', label: 'Revenue', isRatio: false },
  { key: 'gross_profit', label: 'Gross Profit', isRatio: false },
  { key: 'ebitda', label: 'EBITDA', isRatio: false },
  { key: 'net_income', label: 'Net Income', isRatio: false },
  { key: 'gross_margin_pct', label: 'Gross Margin %', isRatio: true },
] as const;

function TrendSection({ report, fy, currency }: { report: string; fy: number; currency: string }) {
  const [years, setYears] = useState<5 | 3 | 7>(5);
  const [metric, setMetric] = useState<string>('total_revenue');
  const [breakdown, setBreakdown] = useState<'total' | 'branch'>('total');
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [hasFetched, setHasFetched] = useState(false);
  // v1.9.52 — honour active custom-dim filters when fetching the trend.
  const { filters: dimFilters } = useDimFilters();

  const sectionRef = useRef<HTMLElement | null>(null);

  // Lazy load on scroll-into-view OR on user interaction.
  useEffect(() => {
    if (hasFetched || !sectionRef.current) return;
    const el = sectionRef.current;
    const observer = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) {
        setHasFetched(true);
        observer.disconnect();
      }
    }, { threshold: 0.1 });
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasFetched]);

  useEffect(() => {
    if (!hasFetched || !report || !fy) return;
    let cancelled = false;
    setLoading(true);
    api.getMultiYearTrend(report, fy, years, breakdown, compactDimFilters(dimFilters) || null)
      .then((r) => { if (!cancelled) setData(r); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [hasFetched, report, fy, years, breakdown, dimFilters]);

  // Look up the selected metric's row in the response.
  const metricMeta = TREND_METRICS.find((m) => m.key === metric);
  const row = (data?.rows || []).find((r: any) => r.key === metric);
  const yearList: number[] = data?.years || [];
  const hasBranches = breakdown === 'branch' && data?.branches && Object.keys(data.branches).length > 0;

  // Section-level status: based on whether revenue is trending up or down.
  const revRow = (data?.rows || []).find((r: any) => r.key === 'total_revenue');
  const status: 'green' | 'amber' | 'red' = useMemo(() => {
    if (!revRow || revRow.values?.length < 2) return 'green';
    const first = revRow.values[0];
    const last = revRow.values[revRow.values.length - 1];
    if (first === 0) return 'green';
    const change = (last - first) / Math.abs(first);
    if (change < -0.10) return 'red';   // shrinking by more than 10% over the window
    if (change < 0.02) return 'amber';  // broadly flat (-2% to +2%)
    return 'green';
  }, [revRow]);

  return (
    <section ref={sectionRef as any} className="briefing-section">
      <SectionHeader
        number={6}
        title="Are we growing?"
        subtitle="Multi-year trend across the headline P&L lines. Historical actuals only — no forecast."
        statusPill={status}
      />

      {!hasFetched ? (
        <div className="dash-sub" style={{ padding: '8px 0' }}>Scroll into view to load…</div>
      ) : loading ? (
        <div className="dash-sub" style={{ padding: '8px 0' }}>Computing {years}-year trend…</div>
      ) : !data || !row ? (
        <div className="dash-sub" style={{ padding: '8px 0' }}>No multi-year data available for this report.</div>
      ) : (
        <>
          {data.synthesis && (
            <div className="briefing-headline">{data.synthesis}</div>
          )}

          <div className="trend-controls">
            <div className="trend-control-group">
              <span className="flbl">{t('Metric')}</span>
              <div className="trend-segm">
                {TREND_METRICS.map((m) => (
                  <button
                    key={m.key}
                    className={'trend-segm-btn' + (metric === m.key ? ' on' : '')}
                    onClick={() => setMetric(m.key)}
                  >{m.label}</button>
                ))}
              </div>
            </div>
            <div className="trend-control-group">
              <span className="flbl">{t('Years')}</span>
              <div className="trend-segm">
                {[3, 5, 7].map((n) => (
                  <button
                    key={n}
                    className={'trend-segm-btn' + (years === n ? ' on' : '')}
                    onClick={() => setYears(n as 5 | 3 | 7)}
                  >{n}Y</button>
                ))}
              </div>
            </div>
            <div className="trend-control-group">
              <span className="flbl">{t('Breakdown')}</span>
              <div className="trend-segm">
                <button
                  className={'trend-segm-btn' + (breakdown === 'total' ? ' on' : '')}
                  onClick={() => setBreakdown('total')}
                >{t('Total')}</button>
                <button
                  className={'trend-segm-btn' + (breakdown === 'branch' ? ' on' : '')}
                  onClick={() => setBreakdown('branch')}
                  title="Requires GL Entries to be tagged with the branch dimension"
                >{t('By Branch')}</button>
              </div>
            </div>
          </div>

          {breakdown === 'branch' && !hasBranches ? (
            <div className="dash-sub" style={{ padding: '8px 0' }}>
              No branch-tagged GL data available for this report and year range. Switch back to Total or tag transactions with branch in ERP.
            </div>
          ) : (
            <TrendChart
              row={row}
              years={yearList}
              currency={metricMeta?.isRatio ? '' : currency}
              isRatio={!!metricMeta?.isRatio}
              branches={hasBranches ? data.branches : null}
              metric={metric}
              metricLabel={metricMeta?.label || metric}
            />
          )}

          <TrendDataTable row={row} years={yearList} currency={currency} isRatio={!!metricMeta?.isRatio} />
        </>
      )}
    </section>
  );
}

function TrendChart({ row, years, currency, isRatio, branches, metric, metricLabel }: {
  row: any;
  years: number[];
  currency: string;
  isRatio: boolean;
  branches: Record<string, any> | null;
  metric: string;
  metricLabel: string;
}) {
  const values: number[] = row.values || [];
  if (!values.length) return null;

  const W = 760, H = 240, PAD_L = 64, PAD_R = 16, PAD_T = 14, PAD_B = 36;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;

  // If we have branches, the chart shows N branch lines plus the total faintly.
  const branchEntries: Array<[string, number[]]> = branches
    ? Object.entries(branches).map(([br, rows]) => [br, (rows[metric] || []) as number[]])
    : [];

  // Compute Y range across all series.
  const allVals: number[] = [...values];
  for (const [, v] of branchEntries) allVals.push(...v);
  const minVal = Math.min(...allVals, 0);
  const maxVal = Math.max(...allVals, 0);
  const span = (maxVal - minVal) || 1;
  const stepX = innerW / Math.max(1, years.length - 1);
  const y = (v: number) => PAD_T + innerH - ((v - minVal) / span) * innerH;
  const x = (i: number) => PAD_L + i * stepX;

  // Branch palette — deterministic by index so the same branch keeps the same colour across renders.
  const BRANCH_COLORS = ['#6e9d4f', '#d97706', '#b03a3a', '#0c447c', '#7a4d10', '#2d5a2d', '#7a1f1f', '#3a3a7a'];

  const fmtAxis = (v: number) => isRatio ? `${(v * 100).toFixed(0)}%` : fmtShort(v);
  const ticks = [minVal, (minVal + maxVal) / 2, maxVal];

  const totalPath = values.map((v, i) => (i === 0 ? 'M' : 'L') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' ');

  return (
    <div className="trend-chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" className="dash-chart">
        {/* Y ticks */}
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={PAD_L} y1={y(t)} x2={W - PAD_R} y2={y(t)} stroke="#ece9e3" strokeWidth="1" />
            <text x={PAD_L - 6} y={y(t) + 4} textAnchor="end" fontSize="10" fill="#6e6a63">{fmtAxis(t)}</text>
          </g>
        ))}
        {/* Branch lines (semi-transparent) */}
        {branchEntries.map(([br, v], idx) => {
          const path = v.map((val, i) => (i === 0 ? 'M' : 'L') + x(i).toFixed(1) + ' ' + y(val).toFixed(1)).join(' ');
          const color = BRANCH_COLORS[idx % BRANCH_COLORS.length];
          return (
            <g key={br}>
              <path d={path} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.85" />
              {v.map((val, i) => <circle key={i} cx={x(i)} cy={y(val)} r="2.5" fill={color} />)}
            </g>
          );
        })}
        {/* Total line — always on top, prominent */}
        {!branches && (
          <path d={totalPath} fill="none" stroke="#0c447c" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        )}
        {!branches && values.map((v, i) => <circle key={i} cx={x(i)} cy={y(v)} r="3.5" fill="#0c447c" />)}
        {/* X axis labels */}
        {years.map((yr, i) => (
          <text key={yr} x={x(i)} y={H - 16} textAnchor="middle" fontSize="10.5" fill="#6e6a63">FY{yr}</text>
        ))}
        {/* Chart title */}
        <text x={PAD_L} y={12} fontSize="10" fill="#6e6a63">{metricLabel}{currency ? ' (' + currency + ')' : ''}</text>
      </svg>
      {branches && branchEntries.length > 0 && (
        <div className="trend-legend">
          {branchEntries.map(([br], idx) => (
            <span key={br} className="trend-legend-item">
              <span className="leg-sw" style={{ background: BRANCH_COLORS[idx % BRANCH_COLORS.length] }} />
              {br}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function TrendDataTable({ row, years, currency, isRatio }: {
  row: any; years: number[]; currency: string; isRatio: boolean;
}) {
  const values: number[] = row?.values || [];
  const yoy: (number | null)[] = row?.yoy || [];
  if (!values.length) return null;
  const fmtCell = (v: number) => isRatio ? `${(v * 100).toFixed(1)}%` : fmt(v);
  return (
    <table className="dash-variance-table" style={{ marginTop: 12 }}>
      <thead>
        <tr>
          <th>{t('Year')}</th>
          <th className="num">{isRatio ? 'Margin' : currency}</th>
          <th className="num">{t('Y/Y change')}</th>
        </tr>
      </thead>
      <tbody>
        {years.map((y, i) => {
          const v = values[i] || 0;
          const yy = yoy[i];
          return (
            <tr key={y}>
              <td>FY{y}</td>
              <td className="num">{fmtCell(v)}</td>
              <td className={'num ' + (yy != null && yy > 0 ? 'var-over' : yy != null && yy < 0 ? 'var-under' : '')}>
                {yy == null ? '—' : (yy > 0 ? '+' : '') + (isRatio ? `${(yy * 100).toFixed(1)}pp` : `${(yy * 100).toFixed(0)}%`)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
