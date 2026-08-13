import { t } from './i18n';
export const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

/* ─── Fiscal-year-aware month labels (v1.9.59) ────────────────────────────
 *
 * Throughout Insight, month indexes (0..11) are FY-month indices — the Nth
 * month of the company's fiscal year, NOT calendar month. For a Jan-start
 * company (KSA) FY-month 0 = Jan; for an April-start company (India)
 * FY-month 0 = Apr. Use `monthLabel(fy_start_month, fy_month_idx)` to get
 * the right abbreviation.
 *
 * `fy_start_month` is 1..12 (calendar) — what the backend exposes as
 * `run.filters.fy_start_month`. Default of 1 keeps legacy callers working
 * (any view that hasn't been updated to read fy_start_month gets the same
 * labels it always did, since 1 → no rotation).
 */
/** v2.39.0 — expandable quarters: purely client-side. For granularities
 *  without a month tier, expanded quarters get their three months injected
 *  before the quarter column. Data already sits in each row's `monthly`, so
 *  no backend, no cache, no echo — display state only. */
export function injectExpandedQuarters(
  order: { tier: string; key: string; label: string; months: number[]; gran: string }[],
  expanded: number[],
  fyStartMonth?: number,
): { tier: string; key: string; label: string; months: number[]; gran: string }[] {
  if (!expanded.length) return order;
  const out: typeof order = [];
  for (const p of order) {
    const qIdx = /^q[0-3]$/.test(p.key) ? parseInt(p.key.slice(1)) : -1;
    if (qIdx >= 0 && expanded.includes(qIdx)) {
      for (const m of (Array.isArray(p.months) && p.months.length ? p.months : [qIdx * 3, qIdx * 3 + 1, qIdx * 3 + 2])) {
        out.push({ tier: 'month', key: `m${m}`, label: monthLabel(fyStartMonth, m), months: [m], gran: 'month' });
      }
    }
    out.push(p);
  }
  return out;
}

export function monthLabel(fyStartMonth: number | undefined, fyMonthIdx: number): string {
  const start = fyStartMonth && fyStartMonth >= 1 && fyStartMonth <= 12 ? fyStartMonth : 1;
  const calIdx = (start - 1 + fyMonthIdx) % 12;
  return t(MONTHS[calIdx]);
}

/* Build a per-company MONTHS array in FY order. Useful when a component
 * needs the whole 12-element sequence in fiscal order. KSA → identical to
 * MONTHS; India → ['Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec','Jan','Feb','Mar']. */
export function monthsInFyOrder(fyStartMonth: number | undefined): string[] {
  return Array.from({ length: 12 }, (_, i) => monthLabel(fyStartMonth, i));
}

/* Display label for a fiscal year, matching the regional convention.
 *   KSA (Jan-start):    "FY 2025"
 *   India (Apr-start):  "FY 2024-25"  ← FY label = year FY ends in
 *   Australia (Jul):    "FY 2024-25"  ← same convention
 * Use everywhere a fiscal year is displayed in headers, snapshots, prior-
 * year columns, etc. Falls back to "FY{N}" when fyStartMonth is missing. */
export function fmtFyLabel(fyStartMonth: number | undefined, fiscalYear: number): string {
  const start = fyStartMonth && fyStartMonth >= 1 && fyStartMonth <= 12 ? fyStartMonth : 1;
  if (start === 1) return t('FY') + ' ' + fiscalYear;
  return t('FY') + ' ' + (fiscalYear - 1) + '-' + String(fiscalYear).slice(-2);
}

export const FY_RANGE = [2022, 2023, 2024, 2025, 2026];

export const GRANULARITY_OPTIONS: { value: string; label: string }[] = [
  { value: 'month', label: 'Monthly only' },
  { value: 'quarter', label: 'Quarterly only' },
  { value: 'half', label: 'Half-yearly only' },
  { value: 'ytd', label: 'YTD only' },
  { value: 'month_quarter', label: 'Monthly + Quarterly' },
  { value: 'month_half', label: 'Monthly + Half-yearly' },
  { value: 'quarter_half', label: 'Quarterly + Half-yearly' },
  { value: 'month_quarter_half', label: 'Monthly + Quarterly + Half-yearly' },
  // v1.9.49 — interleaved Quarter / Quarter-YTD columns for board packs and
  // investment-holding financial statements. Renders as: Q1 (also YTD), Q2,
  // Q2 YTD, Q3, Q3 YTD, Q4, Q4 YTD — matching the conventional comparative
  // quarterly statement layout.
  { value: 'quarter_ytd', label: 'Quarterly + Quarter-YTD (interleaved)' },
];

/** v2.36.0 — digit grouping: 'western' 1,234,567 or 'indian' 12,34,567
 *  (lakh/crore). Chosen in the theme panel, persisted per user. */
export function numLocale(): string | undefined {
  try { return localStorage.getItem('ni-numfmt') === 'indian' ? 'en-IN' : undefined; } catch { return undefined; }
}

export function fmt0(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const n = Math.round(v);
  return (n < 0 ? '(' : '') + Math.abs(n).toLocaleString(numLocale()) + (n < 0 ? ')' : '');
}

/**
 * Decimal-aware number formatter. `decimals=0` matches fmt0 (rounded, the
 * default look). Higher values show that many fraction digits. Negatives in
 * parentheses, thousands separators always on.
 */
export function fmtD(v: number | null | undefined, decimals = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (decimals <= 0) return fmt0(v);
  const neg = v < 0;
  const s = Math.abs(v).toLocaleString(numLocale(), {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return (neg ? '(' : '') + s + (neg ? ')' : '');
}

export function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || !Number.isFinite(v)) return '—';
  return (v * 100).toFixed(1) + '%';
}

export function fmtPctGrowth(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || !Number.isFinite(v)) return '—';
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
}

export function fmtShort(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '';
  const a = Math.abs(v);
  const s = v < 0 ? '-' : '';
  if (a >= 1e6) return s + (a / 1e6).toFixed(1) + 'M';
  if (a >= 1e3) return s + (a / 1e3).toFixed(1) + 'k';
  return s + Math.round(a).toString();
}

export type PeriodTier = 'month' | 'quarter' | 'half' | 'ytd';
export interface Period { key: string; label: string; months: number[]; gran: PeriodTier; }
export interface PeriodGroup {
  key?: string; tier: PeriodTier; periods: Period[]; }

export function buildPeriodGroups(from: number, to: number, granularity: string, fyStartMonth?: number, selFrom?: number | null, selTo?: number | null): { groups: PeriodGroup[]; months: number[] } {
  const months: number[] = [];
  for (let m = from; m <= to; m++) months.push(m);
  const groups: PeriodGroup[] = [];

  const wantsMonth = granularity.includes('month');
  const wantsQuarter = granularity.includes('quarter');
  const wantsHalf = granularity.includes('half');
  const wantsYtd = granularity === 'ytd';

  // v1.9.49 — interleaved Quarter / Quarter-YTD layout. The first quarter
  // shows only its YTD column (since Q1 standalone == Q1-YTD); each
  // subsequent quarter shows the quarter standalone AND its cumulative YTD
  // side by side. This matches the conventional board-pack and investment-
  // holding comparative statement layout.
  // v2.38.0 — quarter frame: ALL FOUR quarter totals always present, the
  // user-selected months expanded in place inside their quarter(s):
  // Jan–Mar → Jan Feb Mar Q1 · Apr–Jun → Q1 Apr May Jun Q2 · etc.
  // Quarters entirely after the selection are flagged future (Actual column
  // renders Budget or blank per the header option). Hidden columns
  // (per-report, from the Columns ▾ popover) are filtered here so screen and
  // every export stay identical.
  if (granularity === 'quarter_frame') {
    // v2.38.1 — the fetch window is the full year; the USER's selection
    // arrives separately (selFrom/selTo) and controls month expansion and
    // future flags. Falling back to from/to kept the old bug at bay if a
    // caller forgets the args.
    const sf = (selFrom ?? from), st = (selTo ?? to);
    const allMonths: number[] = [];
    for (let q = 0; q < 4; q++) {
      const qm = [q * 3, q * 3 + 1, q * 3 + 2];
      const selected = qm.filter((m) => m >= sf && m <= st);
      for (const m of selected) {
        groups.push({ key: 'm' + m, label: monthLabel(fyStartMonth ?? 0, m), tier: 'month', months: [m] } as any);
      }
      const future = q * 3 > st;
      groups.push({ key: 'q' + q, label: 'Q' + (q + 1), tier: 'total', months: qm, future } as any);
      allMonths.push(...qm);
    }
    let out = groups;
    try {
      const hid: string[] = JSON.parse(localStorage.getItem('ni-hidecols') || '[]');
      if (hid.length) out = groups.filter((g) => !hid.includes(g.key));
    } catch { /* ignore */ }
    return { groups: out, months: allMonths };
  }

  if (granularity === 'quarter_ytd') {
    const qs: Record<number, number[]> = {};
    for (const m of months) (qs[Math.floor(m / 3)] = qs[Math.floor(m / 3)] || []).push(m);
    const sortedQs = Object.keys(qs).map((q) => parseInt(q)).sort((a, b) => a - b);
    const periods: Period[] = [];
    const ytdAccum: number[] = [];
    for (let i = 0; i < sortedQs.length; i++) {
      const q = sortedQs[i];
      const qMonths = qs[q];
      ytdAccum.push(...qMonths);
      if (i === 0) {
        // Q1 = Q1 YTD by definition; show one column labelled YTD only.
        periods.push({ key: 'q' + q + '_ytd', label: 'Q' + (q + 1) + ' YTD', months: [...ytdAccum], gran: 'ytd' });
      } else {
        periods.push({ key: 'q' + q, label: 'Q' + (q + 1), months: qMonths, gran: 'quarter' });
        periods.push({ key: 'q' + q + '_ytd', label: 'Q' + (q + 1) + ' YTD', months: [...ytdAccum], gran: 'ytd' });
      }
    }
    if (periods.length > 0) groups.push({ tier: 'quarter', periods });
    return { groups, months };
  }

  if (wantsMonth) {
    groups.push({
      tier: 'month',
      periods: months.map((m) => ({ key: 'm' + m, label: monthLabel(fyStartMonth, m), months: [m], gran: 'month' as PeriodTier })),
    });
  }
  if (wantsQuarter) {
    const qs: Record<number, number[]> = {};
    for (const m of months) (qs[Math.floor(m / 3)] = qs[Math.floor(m / 3)] || []).push(m);
    const periods: Period[] = Object.keys(qs)
      .map((q) => parseInt(q))
      .sort((a, b) => a - b)
      .map((q) => ({ key: 'q' + q, label: 'Q' + (q + 1), months: qs[q], gran: 'quarter' as PeriodTier }));
    if (periods.length > 0) groups.push({ tier: 'quarter', periods });
  }
  if (wantsHalf) {
    const hs: Record<number, number[]> = {};
    for (const m of months) {
      const h = m < 6 ? 0 : 1;
      (hs[h] = hs[h] || []).push(m);
    }
    const periods: Period[] = Object.keys(hs)
      .map((h) => parseInt(h))
      .sort((a, b) => a - b)
      .map((h) => ({ key: 'h' + h, label: h === 0 ? 'H1' : 'H2', months: hs[h], gran: 'half' as PeriodTier }));
    if (periods.length > 0) groups.push({ tier: 'half', periods });
  }
  if (wantsYtd) {
    groups.push({
      tier: 'ytd',
      periods: [{ key: 'ytd', label: 'YTD ' + monthLabel(fyStartMonth, from) + '–' + monthLabel(fyStartMonth, to), months, gran: 'ytd' }],
    });
  }
  return { groups, months };
}

export function aggregate(monthly: Record<number, number> | undefined, months: number[]): number {
  if (!monthly) return 0;
  let v = 0;
  for (const m of months) v += monthly[m] || 0;
  return v;
}

/**
 * Drop dimension columns that are entirely empty (zero for every account).
 * v1.9.12 — used by the TB/BS/P&L-Statement dimension views so columns like
 * "(Unassigned)" with no activity can be hidden. Pure, returns a new object.
 */
export function dropEmptyDimensions<T extends {
  accounts: { by_dim: Record<string, number> }[];
  dimensions: { name: string; label: string }[];
}>(result: T): T {
  if (!result || !result.dimensions) return result;
  const nonEmpty = new Set<string>();
  for (const a of result.accounts) {
    for (const d of result.dimensions) {
      if (Math.abs(a.by_dim?.[d.name] || 0) > 0.005) nonEmpty.add(d.name);
    }
  }
  const dims = result.dimensions.filter((d) => nonEmpty.has(d.name));
  if (dims.length === result.dimensions.length) return result;
  return {
    ...result,
    dimensions: dims,
    accounts: result.accounts.map((a) => {
      const by: Record<string, number> = {};
      for (const d of dims) by[d.name] = a.by_dim?.[d.name] || 0;
      return { ...a, by_dim: by };
    }),
  };
}

/* ─── Row styling (v1.9.18) ──────────────────────────────────────────────
 * Fixed palettes — tokens map to concrete values in ONE place so the
 * on-screen table and every exporter (Excel/PDF/Print) render identically.
 */
export const ROW_TEXT_COLORS: Record<string, { css: string; hex: string }> = {
  default: { css: 'inherit',  hex: '15141B' },
  muted:   { css: '#6e6a63', hex: '6E6A63' },
  blue:    { css: '#0c447c', hex: '0C447C' },
  green:   { css: '#0f6e56', hex: '0F6E56' },
  red:     { css: '#a32d2d', hex: 'A32D2D' },
};
export const ROW_BG_COLORS: Record<string, { css: string; hex: string | null }> = {
  none:   { css: 'transparent', hex: null },
  grey:   { css: '#f1efe8', hex: 'F1EFE8' },
  blue:   { css: '#e6f1fb', hex: 'E6F1FB' },
  yellow: { css: '#faeeda', hex: 'FAEEDA' },
};

export interface RowStyleLike {
  bold?: boolean; italic?: boolean;
  text_color?: string; bg_color?: string;
  border_top?: boolean; border_bottom?: boolean;
}

/** Resolve a RowStyle into inline CSS for the on-screen table. */
export function rowStyleToCss(style: RowStyleLike | null | undefined): Record<string, string | number> {
  if (!style) return {};
  const css: Record<string, string | number> = {};
  if (style.bold) css.fontWeight = 700;
  if (style.italic) css.fontStyle = 'italic';
  const tc = ROW_TEXT_COLORS[style.text_color || 'default'];
  if (tc && style.text_color && style.text_color !== 'default') css.color = tc.css;
  const bg = ROW_BG_COLORS[style.bg_color || 'none'];
  if (bg && style.bg_color && style.bg_color !== 'none') css.background = bg.css;
  if (style.border_top) css.borderTop = '2px solid var(--border-strong, #b8b4ac)';
  if (style.border_bottom) css.borderBottom = '2px solid var(--border-strong, #b8b4ac)';
  return css;
}
