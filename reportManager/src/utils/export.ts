import * as XLSX from 'xlsx';
import { jsPDF } from 'jspdf';
import autoTable from 'jspdf-autotable';
import type { RunResult } from '../types';
import { MONTHS, aggregate, buildPeriodGroups, monthLabel, ROW_TEXT_COLORS, ROW_BG_COLORS, numLocale, injectExpandedQuarters } from './format';
import { loadBrand, buildFrame, docTitle, bandRow, stripRow, borderTokens, tableCss } from './branddoc';
import type { RowStyleLike } from './format';
import type { LetterheadPayload } from './letterhead';

/* ─────────────────────────────────────────────────────────────────────────
 * Color tokens — one place so XLSX, PDF, and Print share the same palette
 * as the on-screen matrix. RGB tuples for jsPDF, hex strings for XLSX/HTML.
 * ───────────────────────────────────────────────────────────────────────── */
const C = {
  textPrimary: { rgb: [44, 44, 42]   as [number, number, number], hex: '2C2C2A' },
  textDanger:  { rgb: [163, 45, 45]  as [number, number, number], hex: 'A32D2D' },
  textSuccess: { rgb: [15, 110, 86]  as [number, number, number], hex: '0F6E56' },
  textWarning: { rgb: [133, 79, 11]  as [number, number, number], hex: '854F0B' },
  textInfo:    { rgb: [4, 44, 83]    as [number, number, number], hex: '042C53' },

  hdrTotalBg:    { rgb: [232, 232, 226] as [number, number, number], hex: 'E8E8E2' },
  hdrQuarterBg:  { rgb: [238, 237, 254] as [number, number, number], hex: 'EEEDFE' },
  hdrHalfBg:     { rgb: [250, 238, 218] as [number, number, number], hex: 'FAEEDA' },
  hdrYtdBg:      { rgb: [225, 245, 238] as [number, number, number], hex: 'E1F5EE' },

  hdrBudgetBg:   { rgb: [250, 238, 218] as [number, number, number], hex: 'FAEEDA' },
  hdrPriorBg:    { rgb: [225, 245, 238] as [number, number, number], hex: 'E1F5EE' },
  hdrDerivedBg:  { rgb: [230, 241, 251] as [number, number, number], hex: 'E6F1FB' },

  sectionBg:     { rgb: [241, 239, 232] as [number, number, number], hex: 'F1EFE8' },

  bodyQuarterTint: { rgb: [247, 246, 254] as [number, number, number], hex: 'F7F6FE' },
  bodyHalfTint:    { rgb: [253, 247, 235] as [number, number, number], hex: 'FDF7EB' },
  bodyYtdTint:     { rgb: [240, 250, 245] as [number, number, number], hex: 'F0FAF5' },

  bodyBudgetTint:  { rgb: [253, 247, 235] as [number, number, number], hex: 'FDF7EB' },
  bodyPriorTint:   { rgb: [240, 250, 245] as [number, number, number], hex: 'F0FAF5' },
  bodyDerivedTint: { rgb: [244, 249, 254] as [number, number, number], hex: 'F4F9FE' },

  white: { rgb: [255, 255, 255] as [number, number, number], hex: 'FFFFFF' },
};

type SubType = 'actual' | 'budget' | 'py' | 'derived';
type Tier = 'month' | 'quarter' | 'half' | 'ytd' | 'total';
type RowKind = 'section' | 'source' | 'formula';

interface CellMeta {
  text: string;
  raw: number | string | null;
  tier: Tier;
  sub: SubType;
  rowKind: RowKind;
  sign?: 'neg' | 'pos' | null;
  delta?: 'up' | 'down' | null;
  isLabel?: boolean;
  /** v1.9.18 — per-row user styling, attached to every cell of the row. */
  rowStyle?: RowStyleLike | null;
}

interface HeaderCellMeta {
  text: string;
  tier: Tier;
  sub?: SubType;
  level: 'top' | 'sub';
  colspan?: number;
}

interface StyledMatrix {
  headerTop: HeaderCellMeta[];
  headerSub: HeaderCellMeta[];
  body: CellMeta[][];
}

/* ─────────────────────────────────────────────────────────────────────────
 * Formatters — mirror the on-screen helpers exactly.
 * ───────────────────────────────────────────────────────────────────────── */
function fmt0(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const n = Math.round(v);
  return (n < 0 ? '(' : '') + Math.abs(n).toLocaleString(numLocale()) + (n < 0 ? ')' : '');
}
function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || !Number.isFinite(v)) return '—';
  return (v * 100).toFixed(1) + '%';
}
function fmtPctGrowth(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || !Number.isFinite(v)) return '—';
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%';
}

/** v2.32.0 — app-wide CSV header standard: every CSV opens with the same
 *  block the PDFs carry — Company, Report, Period, Generated — so no format
 *  is ever "naked" compared to another. */
export function csvHeader(company: string, title: string, period: string): string[] {
  const d = new Date();
  const dd = String(d.getDate()).padStart(2, '0') + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + d.getFullYear();
  return [`"${company}"`, `"${title}"`, `"${period}"`, `"Generated: ${dd}"`, ''];
}

/** v2.31.0 — export drill state: which source rows the user expanded on
 *  screen, plus their cached account rows. Exports mirror the screen. */
export interface ExportDrill {
  expanded: string[];
  drill: Record<string, { account: string; account_code: string; account_name: string; monthly: Record<number, number> }[]>;
}

/** The export header names the COMPANY, not the product (v2.31.0). */
function exportCompany(run: RunResult, letterhead?: LetterheadPayload): string {
  return (letterhead && letterhead.company_name)
    || ((run.filters as any).company as string)
    || run.filters.segment
    || 'Neotec Insight';
}

/** Subtitle tokens; the segment is dropped when it just repeats the company
 *  already shown in the title line. */
function exportSubtitle(run: RunResult, co: string): string {
  const f = run.filters;
  const parts = [
    f.fy_label || ('FY' + f.fiscal_year),
    `${monthLabel(f.fy_start_month, f.month_from)}\u2013${monthLabel(f.fy_start_month, f.month_to)}`,
  ];
  if (f.segment && f.segment.trim() !== (co || '').trim()) parts.push(f.segment);
  parts.push(f.comparison_mode === 'vs_budget' ? 'Actual vs Budget' : 'Actuals only');
  if (f.granularity) parts.push(f.granularity);
  return parts.join(' \u00b7 ');
}

/* ─────────────────────────────────────────────────────────────────────────
 * buildStyledMatrix — single source of truth for export structure & styles.
 * Builds the same matrix RunTab renders, but as data plus style hints.
 * ───────────────────────────────────────────────────────────────────────── */
function buildStyledMatrix(run: RunResult, show = { growth: true, pctrev: true, ach: true, varv: false }, drillOpt?: ExportDrill): StyledMatrix {
  const f = run.filters;
  const groups = run.period_groups && run.period_groups.length > 0
    ? run.period_groups
    : buildPeriodGroups(f.month_from, f.month_to, f.granularity || 'month_quarter', f.fy_start_month, (f as any).sel_from, (f as any).sel_to).groups;

  // Prefer the backend's interleaved period_order (Jan, Feb, Mar, Q1, ...) when
  // present. Falls back to flattening groups tier-by-tier for older runs.
  let flat: { tier: Tier; key: string; label: string; months: number[] }[] =
    ((run as any).period_order && (run as any).period_order.length > 0)
      ? (run as any).period_order
      : (() => {
          const acc: any[] = [];
          for (const g of groups) for (const p of g.periods) acc.push({ tier: g.tier as Tier, ...p });
          return acc;
        })();

  // v2.39.0 — expanded quarters print/export exactly as shown on screen
  try {
    const granX = String(f.granularity || '');
    if (!granX.includes('month') && granX !== 'quarter_frame') {
      const exp: number[] = JSON.parse(localStorage.getItem('ni-qexpand') || '[]');
      flat = injectExpandedQuarters(flat as any, exp, f.fy_start_month) as any;
    }
  } catch { /* */ }


  // Per-period sub-columns. All four optional metrics appear under each
  // period when their toggles are on, mirroring the on-screen matrix.
  const subs: SubType[] = ['actual'];
  const subLabels: string[] = ['Actual'];
  if (f.comparison_mode === 'vs_budget') { subs.push('budget'); subLabels.push('Budget'); }
  for (let i = 0; i < run.priors.length; i++) { subs.push('py'); subLabels.push(`FY${run.priors[i].fiscal_year}`); }
  if (show.varv && f.comparison_mode === 'vs_budget') { subs.push('derived'); subLabels.push('Variance'); }
  if (show.growth && run.priors.length > 0) { subs.push('derived'); subLabels.push('% Growth'); }
  if (show.ach && f.comparison_mode === 'vs_budget') { subs.push('derived'); subLabels.push('% Achieved'); }
  if (show.pctrev) { subs.push('derived'); subLabels.push('% Rev'); }

  // Trailing YTD-Total block — same sub-columns as per-period, computed over
  // the full month range. Skipped when granularity = 'ytd' (which already
  // contributes a YTD column inside period_order).
  const totSubs: SubType[] = ['actual'];
  const totLabels: string[] = ['Actual'];
  if (f.comparison_mode === 'vs_budget') { totSubs.push('budget'); totLabels.push('Budget'); }
  for (let i = 0; i < run.priors.length; i++) { totSubs.push('py'); totLabels.push(`FY${run.priors[i].fiscal_year}`); }
  if (show.varv && f.comparison_mode === 'vs_budget') { totSubs.push('derived'); totLabels.push('Variance'); }
  if (show.growth && run.priors.length > 0) { totSubs.push('derived'); totLabels.push('% Growth'); }
  if (show.ach && f.comparison_mode === 'vs_budget') { totSubs.push('derived'); totLabels.push('% Achieved'); }
  if (show.pctrev) { totSubs.push('derived'); totLabels.push('% Rev'); }

  // Suppress the trailing YTD Total block if period_order already contains a
  // YTD column (granularity = 'ytd') to avoid the duplicate.
  const ytdAlreadyInFlat = flat.some((p) => (p as any).tier === 'ytd');
  const trailingTotSubs = ytdAlreadyInFlat ? [] : totSubs;
  const trailingTotLabels = ytdAlreadyInFlat ? [] : totLabels;

  const headerTop: HeaderCellMeta[] = [{ text: 'Row', tier: 'total', level: 'top', colspan: 1 }];
  for (const p of flat) headerTop.push({ text: p.label, tier: p.tier, level: 'top', colspan: subs.length });
  if (trailingTotSubs.length > 0) {
    headerTop.push({ text: 'Yearly', tier: 'total', level: 'top', colspan: trailingTotSubs.length });
  }

  const headerSub: HeaderCellMeta[] = [{ text: '', tier: 'total', level: 'sub' }];
  for (const p of flat) {
    subs.forEach((s, i) => headerSub.push({ text: subLabels[i], tier: p.tier, sub: s, level: 'sub' }));
  }
  trailingTotSubs.forEach((s, i) => headerSub.push({ text: trailingTotLabels[i], tier: 'total', sub: s, level: 'sub' }));

  const revRow = run.current.rows.find((r) => r.key === 'total_revenue');
  const monthsAll: number[] = [];
  for (let m = f.month_from; m <= f.month_to; m++) monthsAll.push(m);

  const body: CellMeta[][] = [];

  run.current.rows.forEach((row, idx) => {
    const kind = row.kind as RowKind;
    // v2.85.0 — a row the screen hides must not appear in Excel, PDF, Print or
    // CSV either. The on-screen grid has skipped `hidden` rows since v2.62.1;
    // this loop never did, so a consolidated P&L printed its allocation rows
    // and its before-allocation line — with Actual 0 and a real Budget figure,
    // which reads as "budgeted and not spent" rather than "not applicable
    // here". The printed copy is the one that leaves the building.
    if ((row as any).hidden) return;
    if (kind === 'section') {
      const cells: CellMeta[] = [{ text: row.label, raw: row.label, tier: 'total', sub: 'actual', rowKind: 'section', isLabel: true }];
      const colCount = headerSub.length - 1;
      for (let i = 0; i < colCount; i++) cells.push({ text: '', raw: null, tier: 'total', sub: 'actual', rowKind: 'section' });
      const rs = (row as any).style as RowStyleLike | undefined;
      if (rs) for (const c of cells) c.rowStyle = rs;
      body.push(cells);
      return;
    }

    const bud = run.budget?.rows[idx];
    const pys = run.priors.map((p) => p.rows[idx]);
    const cells: CellMeta[] = [{ text: row.label, raw: row.label, tier: 'total', sub: 'actual', rowKind: kind, isLabel: true }];

    for (const p of flat) {
      const a = aggregate(row.monthly, p.months);
      const bv = bud ? aggregate(bud.monthly, p.months) : null;
      const pyAggs = pys.map((r) => aggregate(r.monthly, p.months));
      const rev = revRow ? aggregate(revRow.monthly, p.months) : 0;
      let priorPointer = 0;

      for (let i = 0; i < subs.length; i++) {
        const s = subs[i];
        const lbl = subLabels[i];
        if (s === 'actual') {
          if ((p as any).future) {
            // v2.38.0 — quarter-frame future quarters export exactly as shown.
            let opt = 'budget';
            try { opt = localStorage.getItem('ni-qframe-future') || 'budget'; } catch { /* */ }
            if (opt === 'blank') {
              cells.push({ text: '\u2014', raw: '', tier: p.tier, sub: 'actual', rowKind: kind });
            } else {
              const bvF = bud ? aggregate(bud.monthly as any, p.months) : 0;
              cells.push({ text: fmt0(bvF), raw: Math.round(bvF), tier: p.tier, sub: 'actual', rowKind: kind });
            }
          } else {
            cells.push({ text: fmt0(a), raw: Math.round(a), tier: p.tier, sub: 'actual', rowKind: kind, sign: a < 0 ? 'neg' : null });
          }
        } else if (s === 'budget') {
          cells.push({ text: fmt0(bv), raw: bv == null ? '' : Math.round(bv), tier: p.tier, sub: 'budget', rowKind: kind, sign: (bv ?? 0) < 0 ? 'neg' : null });
        } else if (s === 'py') {
          const v = pyAggs[priorPointer++];
          cells.push({ text: fmt0(v), raw: Math.round(v), tier: p.tier, sub: 'py', rowKind: kind, sign: v < 0 ? 'neg' : null });
        } else if (lbl === 'Variance') {
          const v = a - (bv || 0); const r = (bv || 1) === 0 ? 0 : v / Math.abs(bv as number);
          cells.push({ text: fmtPctGrowth(r), raw: r, tier: p.tier, sub: 'derived', rowKind: kind, delta: r >= 0 ? 'up' : 'down' });
        } else if (lbl === '% Growth') {
          const py = pyAggs[0] || 0; const g = py === 0 ? null : (a - py) / Math.abs(py);
          cells.push({ text: fmtPctGrowth(g), raw: g, tier: p.tier, sub: 'derived', rowKind: kind, delta: (g ?? 0) >= 0 ? 'up' : 'down' });
        } else if (lbl === '% Achieved') {
          const ach = (bv || 0) === 0 ? null : a / (bv as number);
          cells.push({ text: fmtPct(ach), raw: ach, tier: p.tier, sub: 'derived', rowKind: kind });
        } else if (lbl === '% Rev') {
          const pr = rev === 0 ? null : a / rev;
          cells.push({ text: fmtPct(pr), raw: pr, tier: p.tier, sub: 'derived', rowKind: kind });
        }
      }
    }

    const aT = aggregate(row.monthly, monthsAll);
    const bT = bud ? aggregate(bud.monthly, monthsAll) : null;
    const pyT = pys.map((r) => aggregate(r.monthly, monthsAll));
    const revT = revRow ? aggregate(revRow.monthly, monthsAll) : 0;
    let priorPointer = 0;
    for (let i = 0; i < trailingTotSubs.length; i++) {
      const s = trailingTotSubs[i];
      const lbl = trailingTotLabels[i];
      if (s === 'actual') {
        cells.push({ text: fmt0(aT), raw: Math.round(aT), tier: 'total', sub: 'actual', rowKind: kind, sign: aT < 0 ? 'neg' : null });
      } else if (s === 'budget') {
        cells.push({ text: fmt0(bT), raw: bT == null ? '' : Math.round(bT), tier: 'total', sub: 'budget', rowKind: kind });
      } else if (s === 'py') {
        const v = pyT[priorPointer++];
        cells.push({ text: fmt0(v), raw: Math.round(v), tier: 'total', sub: 'py', rowKind: kind });
      } else if (lbl === 'Variance') {
        const v = aT - (bT || 0); const r = (bT || 1) === 0 ? 0 : v / Math.abs(bT as number);
        cells.push({ text: fmtPctGrowth(r), raw: r, tier: 'total', sub: 'derived', rowKind: kind, delta: r >= 0 ? 'up' : 'down' });
      } else if (lbl === '% Growth') {
        const py = pyT[0] || 0; const g = py === 0 ? null : (aT - py) / Math.abs(py);
        cells.push({ text: fmtPctGrowth(g), raw: g, tier: 'total', sub: 'derived', rowKind: kind, delta: (g ?? 0) >= 0 ? 'up' : 'down' });
      } else if (lbl === '% Achieved') {
        const a = (bT || 0) === 0 ? null : aT / (bT as number);
        cells.push({ text: fmtPct(a), raw: a, tier: 'total', sub: 'derived', rowKind: kind });
      } else if (lbl === '% Rev') {
        const pr = revT === 0 ? null : aT / revT;
        cells.push({ text: fmtPct(pr), raw: pr, tier: 'total', sub: 'derived', rowKind: kind });
      }
    }
    const rs = (row as any).style as RowStyleLike | undefined;
    if (rs) for (const c of cells) c.rowStyle = rs;
    body.push(cells);

    // v2.31.0 — mirror the screen: a source row the user EXPANDED emits its
    // account drill rows (Actual columns only, like the grid); a collapsed
    // row emits nothing. Print/PDF/Excel/CSV all share this walk.
    if (kind === 'source' && drillOpt && drillOpt.expanded.includes(row.key)) {
      for (const acc of (drillOpt.drill[row.key] || [])) {
        const dLabel = `      ${acc.account_code ? acc.account_code + '  ' : ''}${acc.account_name || acc.account}`;
        const dCells: CellMeta[] = [{ text: dLabel, raw: dLabel, tier: 'total', sub: 'actual', rowKind: kind, isLabel: true }];
        const prev = (acc as any).monthly_prev as Record<number, number> | undefined;
        const cell = (sub: string, av: number, pv: number | null, tier: any) => {
          if (sub === 'actual') return { text: fmt0(av), raw: Math.round(av), tier, sub: 'actual' as any, rowKind: kind, sign: av < 0 ? ('neg' as any) : null };
          if (sub === 'py1' && pv !== null) return { text: fmt0(pv), raw: Math.round(pv), tier, sub: sub as any, rowKind: kind, sign: pv < 0 ? ('neg' as any) : null };
          if (sub === 'grw' && pv !== null && pv !== 0) {
            const g = (av - pv) / Math.abs(pv);
            return { text: (g >= 0 ? '+' : '') + (g * 100).toFixed(1) + '%', raw: g, tier, sub: sub as any, rowKind: kind, sign: g < 0 ? ('neg' as any) : null };
          }
          return { text: '', raw: '', tier, sub: sub as any, rowKind: kind };
        };
        for (const p of flat) {
          const av = aggregate(acc.monthly as any, p.months);
          const pv = prev ? aggregate(prev as any, p.months) : null;
          for (let i = 0; i < subs.length; i++) dCells.push(cell(subs[i], av, pv, p.tier) as any);
        }
        const avT = aggregate(acc.monthly as any, monthsAll);
        const pvT = prev ? aggregate(prev as any, monthsAll) : null;
        for (let i = 0; i < trailingTotSubs.length; i++) dCells.push(cell(trailingTotSubs[i], avT, pvT, 'total') as any);
        for (const c of dCells) (c as any).isDrill = true;
        body.push(dCells);
      }
    }
  });

  return { headerTop, headerSub, body };
}

/* ─────────────────────────────────────────────────────────────────────────
 * Color resolution
 * ───────────────────────────────────────────────────────────────────────── */
function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function bgRgbForBodyCell(c: CellMeta): [number, number, number] {
  // User row styling wins over automatic tinting.
  if (c.rowStyle?.bg_color && c.rowStyle.bg_color !== 'none') {
    const hex = ROW_BG_COLORS[c.rowStyle.bg_color]?.hex;
    if (hex) return hexToRgb(hex);
  }
  if (c.rowKind === 'section') return C.sectionBg.rgb;
  if (c.tier === 'quarter') return C.bodyQuarterTint.rgb;
  if (c.tier === 'half') return C.bodyHalfTint.rgb;
  if (c.tier === 'ytd' || c.tier === 'total') {
    if (c.sub === 'budget') return C.bodyBudgetTint.rgb;
    if (c.sub === 'py') return C.bodyPriorTint.rgb;
    if (c.sub === 'derived') return C.bodyDerivedTint.rgb;
    return C.bodyYtdTint.rgb;
  }
  if (c.sub === 'budget') return C.bodyBudgetTint.rgb;
  if (c.sub === 'py') return C.bodyPriorTint.rgb;
  if (c.sub === 'derived') return C.bodyDerivedTint.rgb;
  return C.white.rgb;
}
function bgHexForBodyCell(c: CellMeta): string {
  const [r, g, b] = bgRgbForBodyCell(c);
  return rgbToHex(r, g, b);
}
function fgRgbForBodyCell(c: CellMeta): [number, number, number] {
  // User text colour wins — but only on the label cell, so numeric colour
  // coding (negatives red, growth green) still works in the value columns.
  if (c.isLabel && c.rowStyle?.text_color && c.rowStyle.text_color !== 'default') {
    const hex = ROW_TEXT_COLORS[c.rowStyle.text_color]?.hex;
    if (hex) return hexToRgb(hex);
  }
  if (c.rowKind === 'section') return C.textPrimary.rgb;
  if (c.sign === 'neg') return C.textDanger.rgb;
  if (c.sub === 'derived' && c.delta === 'up') return C.textSuccess.rgb;
  if (c.sub === 'derived' && c.delta === 'down') return C.textDanger.rgb;
  if (c.sub === 'budget') return C.textWarning.rgb;
  if (c.sub === 'py') return C.textSuccess.rgb;
  if (c.sub === 'derived') return C.textInfo.rgb;
  return C.textPrimary.rgb;
}
function fgHexForBodyCell(c: CellMeta): string {
  const [r, g, b] = fgRgbForBodyCell(c);
  return rgbToHex(r, g, b);
}
function bgRgbForHeader(h: HeaderCellMeta): [number, number, number] {
  if (h.level === 'top') {
    if (h.tier === 'quarter') return C.hdrQuarterBg.rgb;
    if (h.tier === 'half') return C.hdrHalfBg.rgb;
    if (h.tier === 'ytd' || h.tier === 'total') return C.hdrYtdBg.rgb;
    return C.hdrTotalBg.rgb;
  }
  if (h.sub === 'budget') return C.hdrBudgetBg.rgb;
  if (h.sub === 'py') return C.hdrPriorBg.rgb;
  if (h.sub === 'derived') return C.hdrDerivedBg.rgb;
  if (h.tier === 'quarter') return C.bodyQuarterTint.rgb;
  if (h.tier === 'half') return C.bodyHalfTint.rgb;
  if (h.tier === 'ytd') return C.bodyYtdTint.rgb;
  return C.hdrTotalBg.rgb;
}
function bgHexForHeader(h: HeaderCellMeta): string {
  const [r, g, b] = bgRgbForHeader(h);
  return rgbToHex(r, g, b);
}

function rgbToHex(r: number, g: number, b: number): string {
  return [r, g, b].map((v) => v.toString(16).padStart(2, '0').toUpperCase()).join('');
}

/* ─────────────────────────────────────────────────────────────────────────
 * XLSX export — hand-authored Office Open XML zip to get cell colors.
 * (xlsx@0.18 community build doesn't write cell styles, hence the hand roll.)
 * ───────────────────────────────────────────────────────────────────────── */
export function exportXlsx(run: RunResult, fileName = 'neotec_insight_report.xlsx', letterhead?: LetterheadPayload, drillOpt?: ExportDrill) {
  const m = buildStyledMatrix(run, undefined, drillOpt);
  const blob = writeStyledXlsx(m, run, letterhead);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(a.href);
}

function writeStyledXlsx(m: StyledMatrix, run: RunResult, letterhead?: LetterheadPayload): Blob {
  const fills: string[] = ['FFFFFF', 'FFFFFF'];
  const fontHexes: string[] = ['2C2C2A'];
  const fillIdx = (hex: string) => {
    const i = fills.indexOf(hex);
    if (i >= 0) return i;
    fills.push(hex);
    return fills.length - 1;
  };
  const fontIdx = (hex: string, bold = false) => {
    const key = (bold ? 'B' : '') + hex;
    const i = fontHexes.indexOf(key);
    if (i >= 0) return i;
    fontHexes.push(key);
    return fontHexes.length - 1;
  };

  interface XfDef { fillId: number; fontId: number; horizontal?: 'left' | 'right' | 'center'; bold?: boolean }
  const xfs: XfDef[] = [{ fillId: 0, fontId: 0 }];
  const xfIdx = (def: XfDef) => {
    const sig = JSON.stringify(def);
    for (let i = 0; i < xfs.length; i++) if (JSON.stringify(xfs[i]) === sig) return i;
    xfs.push(def);
    return xfs.length - 1;
  };

  interface Cell { v: string | number; t: 's' | 'n'; s: number }
  const sheet: Cell[][] = [];
  const merges: { r1: number; c1: number; r2: number; c2: number }[] = [];

  // v1.9.53 — Letter Head as text rows at the top of the worksheet. We
  // do NOT embed the logo image: the xlsx writer in this codebase is a
  // minimal hand-rolled implementation, not a full SheetJS image-embedder.
  // Embedding images requires drawingML + media references, a significant
  // expansion. Text-only is honest and works in every spreadsheet app.
  if (letterhead && (letterhead.company_name || letterhead.address_lines.length > 0)) {
    if (letterhead.company_name) {
      sheet.push([{
        v: letterhead.company_name,
        t: 's',
        s: xfIdx({ fillId: 0, fontId: fontIdx(C.textPrimary.hex, true), horizontal: 'left', bold: true }),
      }]);
    }
    letterhead.address_lines.forEach((line) => {
      if (line) {
        sheet.push([{
          v: line,
          t: 's',
          s: xfIdx({ fillId: 0, fontId: fontIdx('5F5E5A'), horizontal: 'left' }),
        }]);
      }
    });
    const contact: string[] = [];
    if (letterhead.phone) contact.push('Tel: ' + letterhead.phone);
    if (letterhead.email) contact.push('Email: ' + letterhead.email);
    if (letterhead.website) contact.push('Web: ' + letterhead.website);
    if (contact.length > 0) {
      sheet.push([{
        v: contact.join(' · '),
        t: 's',
        s: xfIdx({ fillId: 0, fontId: fontIdx('5F5E5A'), horizontal: 'left' }),
      }]);
    }
    if (letterhead.tax_id) {
      sheet.push([{
        v: 'Tax ID / VAT: ' + letterhead.tax_id,
        t: 's',
        s: xfIdx({ fillId: 0, fontId: fontIdx('5F5E5A'), horizontal: 'left' }),
      }]);
    }
    sheet.push([]);  // blank separator before the report title
  }

  sheet.push([{
    v: `${exportCompany(run, letterhead)} — ${run.report.report_name}`,
    t: 's',
    s: xfIdx({ fillId: 0, fontId: fontIdx(C.textPrimary.hex, true), horizontal: 'left', bold: true }),
  }]);
  sheet.push([{
    v: exportSubtitle(run, exportCompany(run, letterhead)),
    t: 's',
    s: xfIdx({ fillId: 0, fontId: fontIdx('5F5E5A'), horizontal: 'left' }),
  }]);
  sheet.push([]);

  const hdrTopRow: Cell[] = [];
  let col = 0;
  m.headerTop.forEach((h) => {
    const fill = bgHexForHeader(h);
    const xf = xfIdx({ fillId: fillIdx(fill), fontId: fontIdx(C.textPrimary.hex, true), horizontal: 'center', bold: true });
    hdrTopRow.push({ v: h.text, t: 's', s: xf });
    if (h.colspan && h.colspan > 1) {
      merges.push({ r1: sheet.length, c1: col, r2: sheet.length, c2: col + h.colspan - 1 });
      for (let i = 1; i < h.colspan; i++) hdrTopRow.push({ v: '', t: 's', s: xf });
    }
    col += h.colspan || 1;
  });
  sheet.push(hdrTopRow);

  const hdrSubRow: Cell[] = m.headerSub.map((h) => ({
    v: h.text,
    t: 's',
    s: xfIdx({ fillId: fillIdx(bgHexForHeader(h)), fontId: fontIdx('444441'), horizontal: 'center' }),
  }));
  sheet.push(hdrSubRow);

  m.body.forEach((row) => {
    const out: Cell[] = row.map((c, i) => {
      const isLabel = i === 0;
      const hasUserBg = !!(c.rowStyle?.bg_color && c.rowStyle.bg_color !== 'none');
      const hasUserFg = !!(c.rowStyle?.text_color && c.rowStyle.text_color !== 'default');
      // Background: user fill wins everywhere; else section colour; else the
      // label stays white and value cells get their automatic tint.
      const bg = hasUserBg ? bgHexForBodyCell(c)
                : c.rowKind === 'section' ? C.sectionBg.hex
                : isLabel ? C.white.hex
                : bgHexForBodyCell(c);
      // Foreground: user text colour wins on the label; else existing logic.
      const fg = (isLabel && hasUserFg) ? fgHexForBodyCell(c)
                : c.rowKind === 'section' ? C.textPrimary.hex
                : isLabel && c.rowKind === 'formula' ? C.textInfo.hex
                : fgHexForBodyCell(c);
      const bold = c.rowKind === 'section' || c.rowKind === 'formula' || !!c.rowStyle?.bold;
      const xf = xfIdx({
        fillId: fillIdx(bg),
        fontId: fontIdx(fg, bold),
        horizontal: isLabel ? 'left' : 'right',
        bold,
      });
      const numeric = !isLabel && typeof c.raw === 'number' && c.sub !== 'derived';
      return numeric
        ? { v: c.raw as number, t: 'n', s: xf }
        : { v: c.text, t: 's', s: xf };
    });
    sheet.push(out);
  });

  return buildXlsxBlob(sheet, merges, fills, fontHexes, xfs);
}

function buildXlsxBlob(
  sheet: { v: string | number; t: 's' | 'n'; s: number }[][],
  merges: { r1: number; c1: number; r2: number; c2: number }[],
  fills: string[],
  fontHexes: string[],
  xfs: { fillId: number; fontId: number; horizontal?: string; bold?: boolean; noBorder?: boolean }[],
): Blob {
  const colLetter = (n: number): string => {
    let s = ''; n = n + 1;
    while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); }
    return s;
  };
  const escapeXml = (v: any): string => String(v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&apos;');

  let stylesXml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">';

  stylesXml += `<fonts count="${fontHexes.length}">`;
  fontHexes.forEach((f) => {
    const bold = f.startsWith('B');
    const hex = bold ? f.slice(1) : f;
    stylesXml += `<font><sz val="10"/>${bold ? '<b/>' : ''}<color rgb="FF${hex}"/><name val="Calibri"/></font>`;
  });
  stylesXml += '</fonts>';

  stylesXml += `<fills count="${fills.length}">`;
  fills.forEach((bg, i) => {
    if (i === 0) stylesXml += '<fill><patternFill patternType="none"/></fill>';
    else if (i === 1) stylesXml += '<fill><patternFill patternType="gray125"/></fill>';
    else stylesXml += `<fill><patternFill patternType="solid"><fgColor rgb="FF${bg}"/><bgColor indexed="64"/></patternFill></fill>`;
  });
  stylesXml += '</fills>';

  // v2.55.0 — a real border table. This used to declare one empty border and
  // point every style at it, so no workbook this app produced had ever
  // contained a single rule; "Excel borders are not printing" was literal.
  // borderId 1 boxes the cell, which is what a financial statement wants.
  const RULE_HEX = 'FFB9B4A6';
  stylesXml += '<borders count="2">'
    + '<border><left/><right/><top/><bottom/><diagonal/></border>'
    + `<border><left style="thin"><color rgb="${RULE_HEX}"/></left>`
    + `<right style="thin"><color rgb="${RULE_HEX}"/></right>`
    + `<top style="thin"><color rgb="${RULE_HEX}"/></top>`
    + `<bottom style="thin"><color rgb="${RULE_HEX}"/></bottom><diagonal/></border>`
    + '</borders>';
  stylesXml += '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>';

  stylesXml += `<cellXfs count="${xfs.length}">`;
  xfs.forEach((xf) => {
    const borderId = xf.noBorder ? 0 : 1;
    stylesXml += `<xf numFmtId="0" fontId="${xf.fontId}" fillId="${xf.fillId}" borderId="${borderId}" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">`
      + `<alignment horizontal="${xf.horizontal || 'general'}" vertical="center"/></xf>`;
  });
  stylesXml += '</cellXfs>';

  stylesXml += '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>';
  stylesXml += '</styleSheet>';

  let sheetXml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">';
  const maxCols = Math.max(...sheet.map((r) => r.length), 1);
  sheetXml += '<cols>';
  sheetXml += `<col min="1" max="1" width="36" customWidth="1"/>`;
  sheetXml += `<col min="2" max="${maxCols}" width="13" customWidth="1"/>`;
  sheetXml += '</cols>';
  sheetXml += '<sheetData>';
  sheet.forEach((row, rIdx) => {
    sheetXml += `<row r="${rIdx + 1}">`;
    row.forEach((c, cIdx) => {
      if (c.v === '' || c.v === null || c.v === undefined) return;
      const ref = `${colLetter(cIdx)}${rIdx + 1}`;
      if (c.t === 'n') {
        sheetXml += `<c r="${ref}" s="${c.s}" t="n"><v>${c.v}</v></c>`;
      } else {
        sheetXml += `<c r="${ref}" s="${c.s}" t="inlineStr"><is><t xml:space="preserve">${escapeXml(c.v)}</t></is></c>`;
      }
    });
    sheetXml += '</row>';
  });
  sheetXml += '</sheetData>';
  if (merges.length) {
    sheetXml += `<mergeCells count="${merges.length}">`;
    for (const mr of merges) {
      sheetXml += `<mergeCell ref="${colLetter(mr.c1)}${mr.r1 + 1}:${colLetter(mr.c2)}${mr.r2 + 1}"/>`;
    }
    sheetXml += '</mergeCells>';
  }
  // Excel does not print its own gridlines by default; switching it on means
  // even an unstyled block still reads as a table on paper.
  sheetXml += '<printOptions gridLines="1" horizontalCentered="0"/>';
  sheetXml += '<pageMargins left="0.4" right="0.4" top="0.7" bottom="0.7" header="0.3" footer="0.3"/>';
  sheetXml += '<pageSetup orientation="portrait" fitToWidth="1" fitToHeight="0" paperSize="9"/>';
  sheetXml += '</worksheet>';

  const workbookXml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    + '<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>';
  const workbookRels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    + '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    + '</Relationships>';
  const rootRels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    + '</Relationships>';
  const contentTypes = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    + '<Default Extension="xml" ContentType="application/xml"/>'
    + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    + '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    + '</Types>';

  const enc = new TextEncoder();
  const entries: { name: string; data: Uint8Array }[] = [
    { name: '[Content_Types].xml', data: enc.encode(contentTypes) },
    { name: '_rels/.rels', data: enc.encode(rootRels) },
    { name: 'xl/workbook.xml', data: enc.encode(workbookXml) },
    { name: 'xl/_rels/workbook.xml.rels', data: enc.encode(workbookRels) },
    { name: 'xl/styles.xml', data: enc.encode(stylesXml) },
    { name: 'xl/worksheets/sheet1.xml', data: enc.encode(sheetXml) },
  ];

  return new Blob([buildZip(entries)], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
}

/* Minimal stored-only ZIP writer. xlsx accepts uncompressed entries. */
function buildZip(entries: { name: string; data: Uint8Array }[]): Uint8Array {
  const localParts: Uint8Array[] = [];
  const centralParts: Uint8Array[] = [];
  let offset = 0;
  let cdSize = 0;

  for (const e of entries) {
    const nameBytes = new TextEncoder().encode(e.name);
    const crc = crc32(e.data);
    const size = e.data.length;

    const local = new Uint8Array(30 + nameBytes.length + size);
    const dv = new DataView(local.buffer);
    dv.setUint32(0, 0x04034b50, true);
    dv.setUint16(4, 20, true);
    dv.setUint16(6, 0, true);
    dv.setUint16(8, 0, true);
    dv.setUint16(10, 0, true);
    dv.setUint16(12, 0, true);
    dv.setUint32(14, crc, true);
    dv.setUint32(18, size, true);
    dv.setUint32(22, size, true);
    dv.setUint16(26, nameBytes.length, true);
    dv.setUint16(28, 0, true);
    local.set(nameBytes, 30);
    local.set(e.data, 30 + nameBytes.length);
    localParts.push(local);

    const central = new Uint8Array(46 + nameBytes.length);
    const cd = new DataView(central.buffer);
    cd.setUint32(0, 0x02014b50, true);
    cd.setUint16(4, 20, true);
    cd.setUint16(6, 20, true);
    cd.setUint16(8, 0, true);
    cd.setUint16(10, 0, true);
    cd.setUint16(12, 0, true);
    cd.setUint16(14, 0, true);
    cd.setUint32(16, crc, true);
    cd.setUint32(20, size, true);
    cd.setUint32(24, size, true);
    cd.setUint16(28, nameBytes.length, true);
    cd.setUint16(30, 0, true);
    cd.setUint16(32, 0, true);
    cd.setUint16(34, 0, true);
    cd.setUint16(36, 0, true);
    cd.setUint32(38, 0, true);
    cd.setUint32(42, offset, true);
    central.set(nameBytes, 46);
    centralParts.push(central);

    offset += local.length;
    cdSize += central.length;
  }

  const end = new Uint8Array(22);
  const ed = new DataView(end.buffer);
  ed.setUint32(0, 0x06054b50, true);
  ed.setUint16(4, 0, true);
  ed.setUint16(6, 0, true);
  ed.setUint16(8, entries.length, true);
  ed.setUint16(10, entries.length, true);
  ed.setUint32(12, cdSize, true);
  ed.setUint32(16, offset, true);
  ed.setUint16(20, 0, true);

  const totalLen = offset + cdSize + 22;
  const out = new Uint8Array(totalLen);
  let pos = 0;
  for (const p of localParts) { out.set(p, pos); pos += p.length; }
  for (const p of centralParts) { out.set(p, pos); pos += p.length; }
  out.set(end, pos);
  return out;
}

let CRC_TABLE: Uint32Array | null = null;
function crc32(data: Uint8Array): number {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
      CRC_TABLE[n] = c >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) crc = (CRC_TABLE[(crc ^ data[i]) & 0xff] ^ (crc >>> 8)) >>> 0;
  return (crc ^ 0xffffffff) >>> 0;
}

/* ─────────────────────────────────────────────────────────────────────────
 * PDF export — styled per-cell via autoTable's didParseCell hook.
 * ───────────────────────────────────────────────────────────────────────── */
export function exportPdf(run: RunResult, fileName = 'neotec_insight_report.pdf', letterhead?: LetterheadPayload, drillOpt?: ExportDrill) {
  const m = buildStyledMatrix(run, undefined, drillOpt);
  const doc = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a3' });

  // v1.9.53 — Letter Head: draw company name, address, contact, tax ID
  // at the top of the page. Pure text layout (jsPDF is text + raster), no
  // HTML rendering. Logo: if we have a data URL, embed it on the left of
  // the header block. Width capped at 80pt to avoid letterhead dominating.
  let cursorY = 40;
  if (letterhead && (letterhead.company_name || letterhead.address_lines.length > 0)) {
    let textX = 40;
    // Logo at top-left (best-effort — fails silently if URL is unreachable).
    if (letterhead.logo_url) {
      try {
        // jsPDF.addImage with URL is sync but throws on non-data URIs in
        // some browser combos. We wrap defensively.
        doc.addImage(letterhead.logo_url, 'PNG', 40, cursorY - 10, 80, 40, undefined, 'NONE');
        textX = 140;  // shift text to the right of the logo
      } catch {
        // Logo failed — proceed without it.
      }
    }
    if (letterhead.company_name) {
      doc.setFontSize(14);
      doc.setTextColor(...C.textPrimary.rgb);
      doc.text(letterhead.company_name, textX, cursorY);
      cursorY += 16;
    }
    doc.setFontSize(8);
    doc.setTextColor(95, 94, 90);
    letterhead.address_lines.forEach((line) => {
      if (line) { doc.text(line, textX, cursorY); cursorY += 11; }
    });
    const contact: string[] = [];
    if (letterhead.phone) contact.push('Tel: ' + letterhead.phone);
    if (letterhead.email) contact.push('Email: ' + letterhead.email);
    if (letterhead.website) contact.push('Web: ' + letterhead.website);
    if (contact.length > 0) { doc.text(contact.join(' · '), textX, cursorY); cursorY += 11; }
    if (letterhead.tax_id) { doc.text('Tax ID / VAT: ' + letterhead.tax_id, textX, cursorY); cursorY += 11; }
    // Separator line under the letterhead block.
    cursorY += 4;
    doc.setDrawColor(200, 200, 200);
    doc.line(40, cursorY, doc.internal.pageSize.getWidth() - 40, cursorY);
    cursorY += 18;
  }

  doc.setFontSize(14);
  doc.setTextColor(...C.textPrimary.rgb);
  doc.text(`${exportCompany(run, letterhead)} — ${run.report.report_name}`, 40, cursorY);
  cursorY += 16;
  doc.setFontSize(9);
  doc.text(exportSubtitle(run, exportCompany(run, letterhead)), 40, cursorY);
  cursorY += 18;

  const head: any[] = [
    m.headerTop.map((h) => ({
      content: h.text,
      colSpan: h.colspan || 1,
      styles: {
        fillColor: bgRgbForHeader(h),
        textColor: C.textPrimary.rgb,
        fontStyle: 'bold',
        halign: 'center',
      },
    })),
    m.headerSub.slice(1).map((h) => ({
      content: h.text,
      styles: {
        fillColor: bgRgbForHeader(h),
        textColor: C.textPrimary.rgb,
        halign: 'center',
        fontSize: 6,
      },
    })),
  ];

  const body: any[][] = m.body.map((row) => row.map((c, i) => ({
    content: c.text,
    styles: {
      fillColor: (c.rowStyle?.bg_color && c.rowStyle.bg_color !== 'none') ? bgRgbForBodyCell(c)
                 : c.rowKind === 'section' ? C.sectionBg.rgb
                 : i === 0 ? C.white.rgb
                 : bgRgbForBodyCell(c),
      textColor: (i === 0 && c.rowStyle?.text_color && c.rowStyle.text_color !== 'default') ? fgRgbForBodyCell(c)
                 : c.rowKind === 'section' ? C.textPrimary.rgb
                 : i === 0 && c.rowKind === 'formula' ? C.textInfo.rgb
                 : fgRgbForBodyCell(c),
      fontStyle: (() => {
        const b = c.rowKind === 'section' || c.rowKind === 'formula' || !!c.rowStyle?.bold;
        const it = !!c.rowStyle?.italic;
        return b && it ? 'bolditalic' : b ? 'bold' : it ? 'italic' : 'normal';
      })(),
      halign: i === 0 ? 'left' : 'right',
    },
  })));

  autoTable(doc, {
    startY: cursorY,
    head,
    body,
    theme: 'grid',
    styles: { fontSize: 7, cellPadding: 2, lineColor: [214, 213, 207], lineWidth: 0.3 },
    // v1.9.53 — footer + page number on every page. Footer text comes from
    // the Letter Head's footer_html if present; we strip tags for plain
    // text since jsPDF doesn't render HTML.
    didDrawPage: (data: any) => {
      const pageSize = doc.internal.pageSize;
      const pageWidth = pageSize.getWidth();
      const pageHeight = pageSize.getHeight();
      doc.setFontSize(7);
      doc.setTextColor(120, 120, 120);
      // Page number (right).
      const pageNum = (doc as any).internal.getNumberOfPages?.() || data.pageNumber || 1;
      doc.text(`Page ${data.pageNumber} of ${pageNum}`, pageWidth - 40, pageHeight - 20, { align: 'right' } as any);
      // Letter Head footer (left). Strip HTML for safe plain-text rendering.
      if (letterhead && letterhead.footer_html) {
        const txt = letterhead.footer_html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
        if (txt) {
          // Trim aggressively — footer is for a small line, not a paragraph.
          const trimmed = txt.length > 200 ? txt.slice(0, 197) + '…' : txt;
          doc.text(trimmed, 40, pageHeight - 20);
        }
      }
    },
  });
  doc.save(fileName);
}

/* ─────────────────────────────────────────────────────────────────────────
 * CSV — plain text. Colors are meaningless here; layout stays faithful.
 * ───────────────────────────────────────────────────────────────────────── */
export function exportCsv(run: RunResult, fileName = 'neotec_insight_report.csv', letterhead?: LetterheadPayload, drillOpt?: ExportDrill) {
  const m = buildStyledMatrix(run, undefined, drillOpt);
  const rows: string[] = [];

  // v1.9.53 — Letter Head as plain-text header lines. CSV can't embed
  // images or rich formatting, so we emit company name, address, contact
  // info, then a blank line. Won't corrupt the structured data because
  // these lines have no commas in their canonical form (we still csvEsc
  // each cell, just in case the company name has commas).
  if (letterhead && (letterhead.company_name || letterhead.address_lines.length > 0)) {
    if (letterhead.company_name) rows.push(csvEsc(letterhead.company_name));
    letterhead.address_lines.forEach((line) => { if (line) rows.push(csvEsc(line)); });
    const contact: string[] = [];
    if (letterhead.phone) contact.push('Tel: ' + letterhead.phone);
    if (letterhead.email) contact.push('Email: ' + letterhead.email);
    if (letterhead.website) contact.push('Web: ' + letterhead.website);
    if (contact.length > 0) rows.push(csvEsc(contact.join(' · ')));
    if (letterhead.tax_id) rows.push(csvEsc('Tax ID / VAT: ' + letterhead.tax_id));
    rows.push('');  // blank separator
  }

  const flatTop: string[] = [];
  m.headerTop.forEach((h) => {
    for (let i = 0; i < (h.colspan || 1); i++) flatTop.push(h.text);
  });
  rows.push(flatTop.map(csvEsc).join(','));
  rows.push(m.headerSub.map((h) => csvEsc(h.text)).join(','));

  m.body.forEach((row) => {
    rows.push(row.map((c) => csvEsc(typeof c.raw === 'number' ? c.raw : c.text)).join(','));
  });

  const blob = new Blob([rows.join('\n')], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(a.href);
}

function csvEsc(v: any): string {
  const s = String(v ?? '');
  return s.includes(',') || s.includes('"') || s.includes('\n')
    ? '"' + s.replace(/"/g, '""') + '"'
    : s;
}

/* ─────────────────────────────────────────────────────────────────────────
 * Print export — new window with the colored matrix, browser print dialog.
 * Forces print-color-adjust so backgrounds survive paper/PDF.
 * ───────────────────────────────────────────────────────────────────────── */
export function exportPrint(run: RunResult, letterhead?: LetterheadPayload, drillOpt?: ExportDrill) {
  // v2.38.6 — Print size option: 'auto' fits the font to the column count
  // (fewer columns → bigger, more readable print; the quarter-frame + hidden
  // columns combo prints large), or fixed compact/medium/large.
  let printSize = 'auto';
  try { printSize = localStorage.getItem('ni-print-size') || 'auto'; } catch { /* */ }
  const m = buildStyledMatrix(run, undefined, drillOpt);
  const win = window.open('', '_blank', 'width=1200,height=800');
  if (!win) {
    alert('Pop-ups blocked. Please allow pop-ups for this site so we can open the print view.');
    return;
  }

  const colCount = ((m as any).headerSub || (m as any).headerTop || []).length || 20;
  const fsFor = () => {
    if (printSize === 's') return { fs: 8, padV: 2, padH: 4 };
    if (printSize === 'm') return { fs: 11, padV: 4, padH: 7 };
    if (printSize === 'l') return { fs: 14, padV: 6, padH: 9 };
    // auto: A3-landscape usable ≈ 396mm; heuristic — scale font by column
    // budget, clamped to sane print sizes.
    const fs = Math.max(7, Math.min(15, Math.floor(340 / colCount)));
    return { fs, padV: fs >= 12 ? 6 : fs >= 9 ? 4 : 2, padH: fs >= 12 ? 9 : 6 };
  };
  const { fs, padV, padH } = fsFor();

  const css = `
    :root { --pfs: ${fs}px; --ppad: ${padV}px ${padH}px; }
    @page { size: A3 landscape; margin: 12mm; }
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      color: var(--th-ink, #2c2c2a); background: var(--th-paper, #fff); margin: 0; padding: 16px;
      -webkit-print-color-adjust: exact; print-color-adjust: exact;
    }
    h1 { font-size: 16px; margin: 0 0 4px; font-weight: 600; color: var(--th-accent, inherit); }
    .sub { font-size: 11px; color: var(--th-muted, #5f5e5a); margin-bottom: 12px; }
    table { border-collapse: collapse; font-size: var(--pfs, 10px); width: 100%; }
    th, td { padding: var(--ppad, 4px 6px); border: 0.5px solid var(--th-rule, #d6d5cf); white-space: nowrap; }
    th { text-align: center; font-weight: 600; }
    td.label { text-align: left; }
    td.num { text-align: right; font-variant-numeric: tabular-nums; }
    td.section { background: var(--th-group, #f1efe8) !important; font-weight: 600; }
    tr.formula td { font-weight: 600; }
    tr.formula td.label { color: var(--th-accent, #042c53); }
    /* v1.9.53 — Letter Head styling. Header sits at the top of every
     * printed page; footer at the bottom. We use 'position: running' for
     * @page margin boxes? Most browsers don't yet support that consistently,
     * so we just put the header above the table once. For paginated
     * printing across many pages, browsers will reflow the table but the
     * header HTML stays in its original position by design — most reports
     * fit on one page; multi-page exports show the header once at the top
     * (which matches how invoices print). */
    .lh-header { margin-bottom: 14px; padding-bottom: 12px; border-bottom: 1px solid #d6d5cf; }
    .lh-footer { margin-top: 14px; padding-top: 10px; border-top: 1px solid #d6d5cf; font-size: 9px; color: #5f5e5a; }
  `;

  const renderHeader = () => {
    const top = m.headerTop.map((h) =>
      `<th colspan="${h.colspan || 1}" style="background:#${bgHexForHeader(h)}">${escHtml(h.text)}</th>`,
    ).join('');
    const sub = m.headerSub.map((h) =>
      `<th style="background:#${bgHexForHeader(h)}; font-size:9px; font-weight:500">${escHtml(h.text)}</th>`,
    ).join('');
    return `<thead><tr>${top}</tr><tr>${sub}</tr></thead>`;
  };

  const renderBody = () => m.body.map((row) => {
    const isSection = row[0]?.rowKind === 'section';
    const isFormula = row[0]?.rowKind === 'formula';
    const rs = row[0]?.rowStyle as RowStyleLike | undefined;
    // Extra inline style applied to every cell of a user-styled row.
    const rowExtra = (() => {
      if (!rs) return '';
      let css = '';
      if (rs.bold) css += 'font-weight:700;';
      if (rs.italic) css += 'font-style:italic;';
      if (rs.border_top) css += 'border-top:2px solid #b8b4ac;';
      if (rs.border_bottom) css += 'border-bottom:2px solid #b8b4ac;';
      return css;
    })();
    const userBg = (rs?.bg_color && rs.bg_color !== 'none') ? ROW_BG_COLORS[rs.bg_color]?.hex : null;
    const userFg = (rs?.text_color && rs.text_color !== 'default') ? ROW_TEXT_COLORS[rs.text_color]?.hex : null;
    if (isSection) {
      const total = row.length;
      const bgCss = userBg ? `background:#${userBg};` : '';
      const fgCss = userFg ? `color:#${userFg};` : '';
      return `<tr><td class="section" colspan="${total}" style="${rowExtra}${bgCss}${fgCss}">${escHtml(row[0].text)}</td></tr>`;
    }
    const cells = row.map((c, i) => {
      if (i === 0) {
        const fg = userFg ? `color:#${userFg};` : (isFormula ? 'color:#042c53;font-weight:600;' : '');
        const bg = userBg ? `background:#${userBg};` : '';
        return `<td class="label" style="${rowExtra}${fg}${bg}">${escHtml(c.text)}</td>`;
      }
      const bg = bgHexForBodyCell(c);
      const fg = fgHexForBodyCell(c);
      return `<td class="num" style="background:#${bg}; color:#${fg};${rowExtra}${isFormula ? 'font-weight:600' : ''}">${escHtml(c.text)}</td>`;
    }).join('');
    return `<tr class="${isFormula ? 'formula' : ''}">${cells}</tr>`;
  }).join('');

  const lhHeader = letterhead && letterhead.header_html
    ? `<div class="lh-header">${letterhead.header_html}</div>`
    : '';
  const lhFooter = letterhead && letterhead.footer_html
    ? `<div class="lh-footer">${letterhead.footer_html}</div>`
    : '';

  // v2.48.0 — the app-wide Brand Kit frames this document too. Guarded:
  // any failure falls back to the original unbranded document untouched.
  let brandCss = ''; let brandHead = ''; let brandFoot = '';
  let brandDocTitle = run.report.report_name;   // v2.48.2 — Save-as-PDF filename carries the company
  try {
    const b = loadBrand((run as any).company || null);
    const fr = buildFrame(b, {
      title: run.report.report_name,
      companyLabel: exportCompany(run, letterhead),
      periodLabel: exportSubtitle(run, exportCompany(run, letterhead)),
      paperOverride: 'A3',  // reports keep their wide default unless brand narrows later
      orientationOverride: 'landscape',
    });
    brandCss = fr.css; brandHead = fr.headerHtml; brandFoot = fr.footerHtml;
    brandDocTitle = docTitle({ title: run.report.report_name, companyLabel: exportCompany(run, letterhead) });
  } catch { /* unbranded fallback */ }

  const html = `<!doctype html><html dir="ltr"><head><meta charset="utf-8"><title>${escHtml(brandDocTitle)}</title><style>${brandCss ? css.replace('@page { size: A3 landscape; margin: 12mm; }', '') + brandCss : css}</style></head>
  <body>
    ${brandHead ? '' : lhHeader}
    ${brandHead ? '' : `<h1>${escHtml(exportCompany(run, letterhead))} — ${escHtml(run.report.report_name)}</h1>
    <div class="sub">${escHtml(exportSubtitle(run, exportCompany(run, letterhead)))}</div>`}
    <table>${brandHead
      ? renderHeader().replace('<thead>', '<thead>' + bandRow(brandHead))
      : renderHeader()}${brandHead ? stripRow(brandFoot) : ''}<tbody>${renderBody()}</tbody></table>
    ${brandHead ? '' : lhFooter}
    <script>window.onload = () => { setTimeout(() => window.print(), 200); };</script>
  </body></html>`;

  win.document.open();
  win.document.write(html);
  win.document.close();
}

function escHtml(s: string): string {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* ─────────────────────────────────────────────────────────────────────────
 * Unchanged helpers — Map export plain, dashboard PDF stays canvas-based.
 * ───────────────────────────────────────────────────────────────────────── */
export function exportMapXlsx(rows: Array<{ account_code?: string; account_name?: string; flag?: string }>, fileName = 'neotec_insight_map.xlsx') {
  const wb = XLSX.utils.book_new();
  const out: any[][] = [[null, 'Chart of account', 'P&L Classification']];
  rows.forEach((r) => {
    out.push([null, `${r.account_code || ''} - ${r.account_name || ''}`, r.flag || '']);
  });
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(out), 'MAP');
  XLSX.writeFile(wb, fileName);
}

export async function exportDashboardPdf(title: string, subtitle: string, canvasSelectors: string, fileName = 'neotec_insight_dashboard.pdf', letterhead?: LetterheadPayload) {
  const doc = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4' });
  // v1.9.53 — Letter Head block at the top of the PDF.
  let cursorY = 40;
  if (letterhead && (letterhead.company_name || letterhead.address_lines.length > 0)) {
    let textX = 40;
    if (letterhead.logo_url) {
      try {
        doc.addImage(letterhead.logo_url, 'PNG', 40, cursorY - 10, 70, 36, undefined, 'NONE');
        textX = 125;
      } catch { /* logo failed silently */ }
    }
    if (letterhead.company_name) {
      doc.setFontSize(13); doc.setTextColor(28, 28, 28);
      doc.text(letterhead.company_name, textX, cursorY); cursorY += 14;
    }
    doc.setFontSize(8); doc.setTextColor(95, 94, 90);
    letterhead.address_lines.forEach((line) => { if (line) { doc.text(line, textX, cursorY); cursorY += 10; } });
    const contact: string[] = [];
    if (letterhead.phone) contact.push('Tel: ' + letterhead.phone);
    if (letterhead.email) contact.push('Email: ' + letterhead.email);
    if (letterhead.website) contact.push('Web: ' + letterhead.website);
    if (contact.length > 0) { doc.text(contact.join(' · '), textX, cursorY); cursorY += 10; }
    if (letterhead.tax_id) { doc.text('Tax ID / VAT: ' + letterhead.tax_id, textX, cursorY); cursorY += 10; }
    cursorY += 4;
    doc.setDrawColor(200, 200, 200);
    doc.line(40, cursorY, doc.internal.pageSize.getWidth() - 40, cursorY);
    cursorY += 14;
  }
  doc.setFontSize(14); doc.setTextColor(28, 28, 28);
  doc.text(title, 40, cursorY); cursorY += 14;
  doc.setFontSize(9); doc.setTextColor(95, 94, 90);
  doc.text(subtitle, 40, cursorY); cursorY += 18;
  let y = cursorY;
  const canvases = Array.from(document.querySelectorAll<HTMLCanvasElement>(canvasSelectors));
  for (const cv of canvases) {
    if (y > 460) {
      doc.addPage();
      y = 40;
    }
    try {
      const url = cv.toDataURL('image/png');
      doc.addImage(url, 'PNG', 40, y, 360, 200);
    } catch {}
    y += 220;
  }
  // Footer on every page.
  if (letterhead && letterhead.footer_html) {
    const txt = letterhead.footer_html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    if (txt) {
      const pages = (doc as any).internal.getNumberOfPages?.() || 1;
      const w = doc.internal.pageSize.getWidth();
      const h = doc.internal.pageSize.getHeight();
      const trimmed = txt.length > 200 ? txt.slice(0, 197) + '…' : txt;
      for (let p = 1; p <= pages; p++) {
        doc.setPage(p);
        doc.setFontSize(7); doc.setTextColor(120, 120, 120);
        doc.text(trimmed, 40, h - 20);
        doc.text(`Page ${p} of ${pages}`, w - 40, h - 20, { align: 'right' } as any);
      }
    }
  }
  doc.save(fileName);
}

/* ─────────────────────────────────────────────────────────────────────────
 * Trial Balance / Balance Sheet styled exports (v1.9).
 *
 * These reuse the low-level buildXlsxBlob() styled-grid writer so the
 * workbook carries cell colors and bold totals — matching the on-screen
 * look and the P&L export's quality.
 * ───────────────────────────────────────────────────────────────────────── */

interface GridCell {
  text: string | number;
  numeric?: boolean;
  bold?: boolean;
  bg?: string;     // hex, no FF prefix
  fg?: string;     // hex, no FF prefix
  align?: 'left' | 'right' | 'center';
}

/* Shared palette — kept consistent with the on-screen tables. */
const TB_COLORS = {
  headerBg: 'EFEEEA',
  groupBg: 'F1EFEA',
  totalBg: 'E4E9F0',
  sectionBg: '2A2731',
  sectionFg: 'FFFFFF',
  partyBg: 'F7F7F5',
  text: '15141B',
  muted: '6E6A63',
};

/* Turn a styled grid into an .xlsx blob via the existing low-level writer. */
function gridToXlsxBlob(grid: GridCell[][], merges: { r1: number; c1: number; r2: number; c2: number }[] = []): Blob {
  const fills: string[] = ['FFFFFF', 'FFFFFF'];
  const fontHexes: string[] = ['B' + TB_COLORS.text, TB_COLORS.text];  // 0 = bold default, 1 = normal
  const fillIdx = (hex: string) => {
    if (!hex) return 0;
    const i = fills.indexOf(hex);
    if (i >= 0) return i;
    fills.push(hex);
    return fills.length - 1;
  };
  const fontIdx = (hex: string, bold: boolean) => {
    const key = (bold ? 'B' : '') + (hex || TB_COLORS.text);
    const i = fontHexes.indexOf(key);
    if (i >= 0) return i;
    fontHexes.push(key);
    return fontHexes.length - 1;
  };
  interface XfDef { fillId: number; fontId: number; horizontal?: string; bold?: boolean; noBorder?: boolean }
  const xfs: XfDef[] = [{ fillId: 0, fontId: 1, noBorder: true }];
  const xfIdx = (def: XfDef) => {
    const sig = JSON.stringify(def);
    for (let i = 0; i < xfs.length; i++) if (JSON.stringify(xfs[i]) === sig) return i;
    xfs.push(def);
    return xfs.length - 1;
  };

  // The first rows of every grid are the title block — those are letterhead,
  // not data, so they stay unruled while the table below is boxed.
  const bandRows = grid.findIndex((r) => r.length > 1);
  const sheet: { v: string | number; t: 's' | 'n'; s: number }[][] = grid.map((row, ri) =>
    row.map((c) => {
      const s = xfIdx({
        fillId: fillIdx(c.bg || ''),
        fontId: fontIdx(c.fg || TB_COLORS.text, !!c.bold),
        horizontal: c.align || (c.numeric ? 'right' : 'left'),
        bold: c.bold,
        noBorder: bandRows >= 0 && ri < bandRows,
      });
      return c.numeric
        ? { v: typeof c.text === 'number' ? c.text : 0, t: 'n' as const, s }
        : { v: String(c.text), t: 's' as const, s };
    })
  );
  return buildXlsxBlob(sheet, merges, fills, fontHexes, xfs);
}

function downloadBlob(blob: Blob, fileName: string) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ── Trial Balance ─────────────────────────────────────────────────────── */

export function exportTrialBalanceXlsx(
  result: any,
  partyData: Record<string, any[]>,
  partyOpen: Set<string>,
  fileName = 'trial_balance.xlsx',
) {
  const grid: GridCell[][] = [];
  const num = (v: number): GridCell => ({ text: Math.round(v || 0), numeric: true });

  // Title + currency note.
  const cur = result?.result?.currency;
  const curNote = cur && cur.conversion_rate !== 1
    ? `  (converted to ${cur.presentation_currency} @ ${cur.conversion_rate} as of ${cur.as_of_date})`
    : '';
  grid.push([{ text: `Trial Balance — ${result?.filters?.company || ''}${curNote}`, bold: true }]);
  grid.push([{ text: `As of ${result?.filters?.as_of_date || ''}`, fg: TB_COLORS.muted }]);
  grid.push([{ text: '' }]);

  // Header rows.
  grid.push([
    { text: 'Account', bold: true, bg: TB_COLORS.headerBg },
    { text: 'Opening Debit', bold: true, bg: TB_COLORS.headerBg, align: 'right' },
    { text: 'Opening Credit', bold: true, bg: TB_COLORS.headerBg, align: 'right' },
    { text: 'Period Debit', bold: true, bg: TB_COLORS.headerBg, align: 'right' },
    { text: 'Period Credit', bold: true, bg: TB_COLORS.headerBg, align: 'right' },
    { text: 'Closing Debit', bold: true, bg: TB_COLORS.headerBg, align: 'right' },
    { text: 'Closing Credit', bold: true, bg: TB_COLORS.headerBg, align: 'right' },
  ]);

  for (const a of (result?.result?.accounts || [])) {
    const isGroup = !!a.is_group;
    const indent = '  '.repeat(Math.max(0, a.depth || 0));
    const label = `${indent}${a.code ? a.code + '  ' : ''}${a.label}`;
    const rowBg = isGroup ? TB_COLORS.groupBg : '';
    grid.push([
      { text: label, bold: isGroup, bg: rowBg },
      { ...num(a.opening_debit), bold: isGroup, bg: rowBg },
      { ...num(a.opening_credit), bold: isGroup, bg: rowBg },
      { ...num(a.period_debit), bold: isGroup, bg: rowBg },
      { ...num(a.period_credit), bold: isGroup, bg: rowBg },
      { ...num(a.closing_debit), bold: isGroup, bg: rowBg },
      { ...num(a.closing_credit), bold: isGroup, bg: rowBg },
    ]);
    // Party drill rows — only those the user has expanded on screen.
    if (a.has_parties && partyOpen.has(a.name) && partyData[a.name]) {
      for (const p of partyData[a.name]) {
        const plabel = `${indent}    ${p.party_type}: ${p.party_name}`;
        grid.push([
          { text: plabel, bg: TB_COLORS.partyBg, fg: TB_COLORS.muted },
          { ...num(p.opening_debit), bg: TB_COLORS.partyBg, fg: TB_COLORS.muted },
          { ...num(p.opening_credit), bg: TB_COLORS.partyBg, fg: TB_COLORS.muted },
          { ...num(p.period_debit), bg: TB_COLORS.partyBg, fg: TB_COLORS.muted },
          { ...num(p.period_credit), bg: TB_COLORS.partyBg, fg: TB_COLORS.muted },
          { ...num(p.closing_debit), bg: TB_COLORS.partyBg, fg: TB_COLORS.muted },
          { ...num(p.closing_credit), bg: TB_COLORS.partyBg, fg: TB_COLORS.muted },
        ]);
      }
    }
  }

  // Totals row.
  const t = result?.result?.totals || {};
  grid.push([
    { text: 'Total', bold: true, bg: TB_COLORS.totalBg },
    { ...num(t.opening_debit), bold: true, bg: TB_COLORS.totalBg },
    { ...num(t.opening_credit), bold: true, bg: TB_COLORS.totalBg },
    { ...num(t.period_debit), bold: true, bg: TB_COLORS.totalBg },
    { ...num(t.period_credit), bold: true, bg: TB_COLORS.totalBg },
    { ...num(t.closing_debit), bold: true, bg: TB_COLORS.totalBg },
    { ...num(t.closing_credit), bold: true, bg: TB_COLORS.totalBg },
  ]);

  downloadBlob(gridToXlsxBlob(grid), fileName);
}

/* ── Balance Sheet ─────────────────────────────────────────────────────── */

export function exportBalanceSheetXlsx(result: any, fileName = 'balance_sheet.xlsx') {
  const grid: GridCell[][] = [];
  const hasPrior = !!result?.filters?.prior_as_of_date;
  const num = (v: number | null | undefined): GridCell => ({ text: Math.round((v as number) || 0), numeric: true });

  const cur = result?.result?.currency;
  const curNote = cur && cur.conversion_rate !== 1
    ? `  (converted to ${cur.presentation_currency} @ ${cur.conversion_rate} as of ${cur.as_of_date})`
    : '';
  grid.push([{ text: `Balance Sheet — ${result?.filters?.company || ''}${curNote}`, bold: true }]);
  grid.push([{ text: `As of ${result?.filters?.as_of_date || ''}`, fg: TB_COLORS.muted }]);
  grid.push([{ text: '' }]);

  const header: GridCell[] = [
    { text: 'Account', bold: true, bg: TB_COLORS.headerBg },
    { text: result?.filters?.as_of_date || 'Current', bold: true, bg: TB_COLORS.headerBg, align: 'right' },
  ];
  if (hasPrior) header.push({ text: result.filters.prior_as_of_date, bold: true, bg: TB_COLORS.headerBg, align: 'right' });
  grid.push(header);

  const sections = result?.result?.sections || {};
  const accounts = result?.result?.accounts || [];

  const pushSection = (title: string, rootType: string, sectionKey: string) => {
    const secRow: GridCell[] = [{ text: title, bold: true, bg: TB_COLORS.sectionBg, fg: TB_COLORS.sectionFg }];
    secRow.push({ text: '', bg: TB_COLORS.sectionBg });
    if (hasPrior) secRow.push({ text: '', bg: TB_COLORS.sectionBg });
    grid.push(secRow);

    for (const a of accounts.filter((x: any) => x.root_type === rootType)) {
      const isGroup = !!a.is_group;
      const indent = '  '.repeat(Math.max(0, a.depth || 0));
      const rowBg = isGroup ? TB_COLORS.groupBg : '';
      const row: GridCell[] = [
        { text: `${indent}${a.code ? a.code + '  ' : ''}${a.label}`, bold: isGroup, bg: rowBg },
        { ...num(a.current), bold: isGroup, bg: rowBg },
      ];
      if (hasPrior) row.push({ ...num(a.prior), bold: isGroup, bg: rowBg });
      grid.push(row);
    }
    const sec = sections[sectionKey] || { current: 0, prior: 0 };
    const totRow: GridCell[] = [
      { text: `Total ${title}`, bold: true, bg: TB_COLORS.totalBg },
      { ...num(sec.current), bold: true, bg: TB_COLORS.totalBg },
    ];
    if (hasPrior) totRow.push({ ...num(sec.prior), bold: true, bg: TB_COLORS.totalBg });
    grid.push(totRow);
  };

  pushSection('Assets', 'Asset', 'asset');
  pushSection('Liabilities', 'Liability', 'liability');
  pushSection('Equity', 'Equity', 'equity');

  // Liabilities + Equity and Difference.
  const le = sections.lia_plus_eq || { current: 0, prior: 0 };
  const leRow: GridCell[] = [
    { text: 'Total Liabilities + Equity', bold: true, bg: TB_COLORS.totalBg },
    { ...num(le.current), bold: true, bg: TB_COLORS.totalBg },
  ];
  if (hasPrior) leRow.push({ ...num(le.prior), bold: true, bg: TB_COLORS.totalBg });
  grid.push(leRow);

  const diff = sections.diff || { current: 0, prior: 0 };
  const diffRow: GridCell[] = [
    { text: 'Difference (Assets - Liab & Eq)', bold: true },
    { ...num(diff.current), bold: true },
  ];
  if (hasPrior) diffRow.push({ ...num(diff.prior), bold: true });
  grid.push(diffRow);

  downloadBlob(gridToXlsxBlob(grid), fileName);
}

/* ── Styled print for TB / BS ──────────────────────────────────────────── */

export function printBalanceReport(title: string, headerHtml: string, bodyHtml: string, company?: string) {
  const w = window.open('', '_blank');
  if (!w) { alert('Pop-ups are blocked \u2014 allow them for this site to print.'); return; }
  const b = loadBrand(company || null);
  const bt = borderTokens(b);
  const fr = buildFrame(b, { title, companyLabel: company || '' }, (x) => x, { mode: 'flow' });
  w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${escHtml(title)}</title>
    <style>
      /* Without an explicit colour-adjust the print pipeline drops every
         background and hairline, which is why these statements printed as
         bare text with no rules at all. */
      * { -webkit-print-color-adjust: exact; print-color-adjust: exact; color-adjust: exact; box-sizing: border-box; }
      body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif;
             font-size: ${Number(b.bodySizePx || 11)}px; color: var(--th-ink, #15141b); margin: 0; padding: 18px; }
      ${fr.css}
      .sub { color: #6e6a63; font-size: 11px; margin: 8px 0 12px; }
      ${tableCss(b)}
      table { border-collapse: collapse; width: 100%; }
      th, td { padding: 4px 8px; text-align: right; border: ${bt.ruleW} solid var(--th-rule, #b9b4a6); }
      th:first-child, td:first-child { text-align: left; }
      thead th { background: var(--th-head-bg, #efeeea); color: var(--th-head-ink, #15141b);
                 font-weight: 600; border: ${bt.strongW} solid var(--th-strong, #333); }
      tr.grp td { background: var(--th-group, #f1efea); font-weight: 600; }
      tr.tot td { background: var(--th-total, #e4e9f0); font-weight: 700;
                  border-top: ${bt.strongW} solid var(--th-strong, #333);
                  border-bottom: ${bt.strongW} solid var(--th-strong, #333); }
      tr.sec td { background: var(--th-accent, #2a2731); color: #fff; font-weight: 600; }
      tr.party td { background: #f7f7f5; color: #6e6a63; }
      tbody tr { page-break-inside: avoid; }
      thead { display: table-header-group; }
      @media print { body { padding: 0; } }
    </style></head><body>
    ${fr.headerHtml}
    <div class="sub">${headerHtml}</div>
    ${bodyHtml}
    ${fr.footerHtml}
    <script>window.onload=function(){window.print();}<\/script>
    </body></html>`);
  w.document.close();
}

/* ── Profit & Loss Statement (CoA-based) styled Excel ──────────────────── */

export function exportPnlStatementXlsx(result: any, fileName = 'profit_and_loss.xlsx') {
  const grid: GridCell[][] = [];
  const num = (v: number): GridCell => ({ text: Math.round(v || 0), numeric: true });

  const cur = result?.result?.currency;
  const curNote = cur && cur.conversion_rate !== 1
    ? `  (converted to ${cur.presentation_currency} @ ${cur.conversion_rate})`
    : '';
  grid.push([{ text: `Profit & Loss Statement — ${result?.filters?.company || ''}${curNote}`, bold: true }]);
  grid.push([{ text: `${result?.filters?.from_date || ''} to ${result?.filters?.to_date || ''}`, fg: TB_COLORS.muted }]);
  grid.push([{ text: '' }]);

  grid.push([
    { text: 'Account', bold: true, bg: TB_COLORS.headerBg },
    { text: 'Amount', bold: true, bg: TB_COLORS.headerBg, align: 'right' },
  ]);

  const accounts = result?.result?.accounts || [];
  const summary = result?.result?.summary || {};

  const section = (title: string, rootType: string, total: number) => {
    grid.push([
      { text: title, bold: true, bg: TB_COLORS.sectionBg, fg: TB_COLORS.sectionFg },
      { text: '', bg: TB_COLORS.sectionBg },
    ]);
    for (const a of accounts.filter((x: any) => x.root_type === rootType)) {
      const isGroup = !!a.is_group;
      const indent = '  '.repeat(Math.max(0, a.depth || 0));
      const rowBg = isGroup ? TB_COLORS.groupBg : '';
      grid.push([
        { text: `${indent}${a.code ? a.code + '  ' : ''}${a.label}`, bold: isGroup, bg: rowBg },
        { ...num(a.amount), bold: isGroup, bg: rowBg },
      ]);
    }
    grid.push([
      { text: `Total ${title}`, bold: true, bg: TB_COLORS.totalBg },
      { ...num(total), bold: true, bg: TB_COLORS.totalBg },
    ]);
  };

  section('Income', 'Income', summary.total_income || 0);
  section('Expense', 'Expense', summary.total_expense || 0);

  // Net Profit / Loss — red text when a loss.
  const isLoss = !!summary.is_loss;
  const np = summary.net_profit || 0;
  grid.push([
    { text: isLoss ? 'Net Loss' : 'Net Profit', bold: true, bg: TB_COLORS.totalBg },
    {
      text: isLoss ? -Math.abs(np) : np,
      numeric: true, bold: true, bg: TB_COLORS.totalBg,
      fg: isLoss ? 'A02323' : '0F6E56',
    },
  ]);

  downloadBlob(gridToXlsxBlob(grid), fileName);
}

/* ── Generic dimension-pivot export (v1.9.12) ──────────────────────────────
 * TB, BS and P&L Statement all share the same dimension-pivot result shape:
 *   result.accounts:   [{ label, code, depth, is_group, by_dim, total }]
 *   result.dimensions: [{ name, label }]
 * These two functions export that shape to Excel and Print, so the export
 * buttons work in Dimension view, not just Period view.
 */
interface PivotExportRow {
  label: string; code?: string; depth?: number; is_group?: number | boolean;
  root_type?: string;
  by_dim: Record<string, number>; total: number;
}
interface PivotExportResult {
  accounts: PivotExportRow[];
  dimensions: { name: string; label: string }[];
}

function pivotGrid(
  result: PivotExportResult,
  decimals: number,
  totalsMode: 'grand' | 'balancesheet' = 'grand',
): GridCell[][] {
  const dims = result.dimensions;
  const grid: GridCell[][] = [];
  // Header. Note: header cells are TEXT — they must not be flagged numeric,
  // or the xlsx writer coerces the string label to 0. align:'right' keeps
  // them visually over their number columns.
  const head: GridCell[] = [
    { text: 'Account', bold: true, bg: TB_COLORS.headerBg },
    { text: 'Total', bold: true, bg: TB_COLORS.headerBg, align: 'right' },
  ];
  for (const d of dims) head.push({ text: d.label, bold: true, bg: TB_COLORS.headerBg, align: 'right' });
  grid.push(head);
  // Body.
  const round = (v: number) => {
    const f = Math.pow(10, decimals);
    return Math.round((v || 0) * f) / f;
  };
  for (const a of result.accounts) {
    const indent = '  '.repeat(Math.max(0, (a.depth || 1) - 1));
    const isGroup = !!a.is_group;
    const row: GridCell[] = [
      {
        text: indent + (a.code ? a.code + '  ' : '') + a.label,
        bold: isGroup, bg: isGroup ? TB_COLORS.groupBg : undefined,
      },
      { text: round(a.total), numeric: true, align: 'right', bold: isGroup, bg: isGroup ? TB_COLORS.groupBg : undefined },
    ];
    for (const d of dims) {
      row.push({
        text: round(a.by_dim?.[d.name] || 0), numeric: true, align: 'right',
        bold: isGroup, bg: isGroup ? TB_COLORS.groupBg : undefined,
      });
    }
    grid.push(row);
  }

  // Totals — leaf accounts only (group rows already contain their leaves).
  const leaves = result.accounts.filter((a) => !a.is_group);
  const mkTotRow = (label: string, rows: PivotExportRow[]): GridCell[] => {
    const r: GridCell[] = [
      { text: label, bold: true, bg: TB_COLORS.totalBg },
      { text: round(rows.reduce((s, a) => s + (a.total || 0), 0)), numeric: true, align: 'right', bold: true, bg: TB_COLORS.totalBg },
    ];
    for (const d of dims) {
      r.push({ text: round(rows.reduce((s, a) => s + (a.by_dim?.[d.name] || 0), 0)), numeric: true, align: 'right', bold: true, bg: TB_COLORS.totalBg });
    }
    return r;
  };

  if (totalsMode === 'balancesheet') {
    // Balance Sheet balances when Assets == Liabilities + Equity.
    const assets = leaves.filter((a) => (a.root_type || '') === 'Asset');
    const liabEq = leaves.filter((a) => ['Liability', 'Equity'].includes(a.root_type || ''));
    grid.push(mkTotRow('Total Assets', assets));
    grid.push(mkTotRow('Total Liabilities + Equity', liabEq));
    const diffTotal = assets.reduce((s, a) => s + (a.total || 0), 0)
      - liabEq.reduce((s, a) => s + (a.total || 0), 0);
    const colOff = dims.some((d) =>
      Math.abs(assets.reduce((s, a) => s + (a.by_dim?.[d.name] || 0), 0)
        - liabEq.reduce((s, a) => s + (a.by_dim?.[d.name] || 0), 0)) > 0.005);
    if (Math.abs(diffTotal) > 0.005 || colOff) {
      const diffRow: GridCell[] = [
        { text: 'Difference', bold: true, fg: 'A32D2D' },
        { text: round(diffTotal), numeric: true, align: 'right', bold: true, fg: 'A32D2D' },
      ];
      for (const d of dims) {
        const v = assets.reduce((s, a) => s + (a.by_dim?.[d.name] || 0), 0)
          - liabEq.reduce((s, a) => s + (a.by_dim?.[d.name] || 0), 0);
        diffRow.push({ text: round(v), numeric: true, align: 'right', bold: true, fg: 'A32D2D' });
      }
      grid.push(diffRow);
    }
  } else {
    grid.push(mkTotRow('Total', leaves));
  }
  return grid;
}

export function exportDimensionPivotXlsx(
  result: PivotExportResult,
  fileName = 'report_dimension.xlsx',
  decimals = 2,
  totalsMode: 'grand' | 'balancesheet' = 'grand',
) {
  const grid = pivotGrid(result, decimals, totalsMode);
  downloadBlob(gridToXlsxBlob(grid), fileName);
}

export function printDimensionPivot(
  title: string,
  headerHtml: string,
  result: PivotExportResult,
  decimals = 2,
  totalsMode: 'grand' | 'balancesheet' = 'grand',
) {
  const dims = result.dimensions;
  const nf = (v: number) => {
    const n = (v || 0).toLocaleString(undefined, {
      minimumFractionDigits: decimals, maximumFractionDigits: decimals,
    });
    return v < 0 ? `(${n.replace('-', '')})` : n;
  };
  let body = '<table><thead><tr><th>Account</th><th>Total</th>';
  for (const d of dims) body += `<th>${escHtml(d.label)}</th>`;
  body += '</tr></thead><tbody>';
  for (const a of result.accounts) {
    const cls = a.is_group ? ' class="grp"' : '';
    const pad = Math.max(0, (a.depth || 1) - 1) * 14;
    body += `<tr${cls}><td style="padding-left:${pad + 8}px">`
      + (a.code ? `<span style="color:#6e6a63">${escHtml(a.code)}</span> ` : '')
      + `${escHtml(a.label)}</td><td>${nf(a.total)}</td>`;
    for (const d of dims) body += `<td>${nf(a.by_dim?.[d.name] || 0)}</td>`;
    body += '</tr>';
  }
  const leaves = result.accounts.filter((a) => !a.is_group);
  const totRow = (label: string, rows: PivotExportRow[]) => {
    let r = `<tr class="tot"><td>${escHtml(label)}</td><td>${nf(rows.reduce((s, a) => s + (a.total || 0), 0))}</td>`;
    for (const d of dims) r += `<td>${nf(rows.reduce((s, a) => s + (a.by_dim?.[d.name] || 0), 0))}</td>`;
    return r + '</tr>';
  };
  if (totalsMode === 'balancesheet') {
    const assets = leaves.filter((a) => (a.root_type || '') === 'Asset');
    const liabEq = leaves.filter((a) => ['Liability', 'Equity'].includes(a.root_type || ''));
    body += totRow('Total Assets', assets);
    body += totRow('Total Liabilities + Equity', liabEq);
    const diff = assets.reduce((s, a) => s + (a.total || 0), 0) - liabEq.reduce((s, a) => s + (a.total || 0), 0);
    if (Math.abs(diff) > 0.005) {
      body += `<tr><td style="color:#a02323;font-weight:700">Difference</td>`
        + `<td style="color:#a02323;font-weight:700">${nf(diff)}</td>`;
      for (const d of dims) {
        const v = assets.reduce((s, a) => s + (a.by_dim?.[d.name] || 0), 0)
          - liabEq.reduce((s, a) => s + (a.by_dim?.[d.name] || 0), 0);
        body += `<td style="color:#a02323;font-weight:700">${Math.abs(v) > 0.005 ? nf(v) : '—'}</td>`;
      }
      body += '</tr>';
    }
  } else {
    body += totRow('Total', leaves);
  }
  body += '</tbody></table>';
  printBalanceReport(title, headerHtml, body);
}

/* ─── Management Pack (v1.9.25) ─────────────────────────────────────────────
 * Assembles the dashboard's sections into one bound, print-ready document —
 * a board-style management pack: cover, executive summary (KPIs), financial
 * ratios, liquidity, cash projection, and budget variance, in order.
 *
 * Takes the data the dashboard has already loaded — no re-fetch.
 */
interface MgmtPackData {
  company: string;
  fiscalYear: number;
  reportName: string;
  tiles: Array<{
    label: string; val: number; achievement: number | null;
    growth: number | null; rag: string;
  }>;
  ratios: any;       // get_financial_ratios payload, or null
  liquidity: any;    // get_liquidity payload, or null
  variance: Array<{
    label: string; actual: number; budget: number; gap: number; gapPct: number | null;
  }>;
  priorFy: number;
  varianceNotes?: Record<string, { commentary: string; modified?: string; modified_by?: string }>;
  trendBasis?: 'ytd' | 'rolling_12';
}

export function printManagementPack(d: MgmtPackData, letterhead?: LetterheadPayload) {
  const w = window.open('', '_blank');
  if (!w) return;

  const nf = (v: number, dec = 0) =>
    (v || 0).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
  const pct = (v: number | null) =>
    v == null || !isFinite(v) ? '—' : (v * 100).toFixed(1) + '%';
  const today = new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });

  // ── Executive summary — KPI tiles ──────────────────────────────────────
  const kpiRows = d.tiles.map((t) => `
    <tr>
      <td><span class="rag rag-${t.rag}"></span>${escHtml(t.label)}</td>
      <td>${nf(t.val)}</td>
      <td>${pct(t.achievement)}</td>
      <td>${t.growth == null ? '—' : (t.growth >= 0 ? '+' : '') + pct(t.growth)}</td>
    </tr>`).join('');

  // ── Ratios ─────────────────────────────────────────────────────────────
  let ratiosHtml = '<p class="muted">Ratios not available.</p>';
  if (d.ratios?.groups?.length) {
    ratiosHtml = d.ratios.groups.map((g: any) => {
      const cells = g.ratios.map((r: any) => {
        const val = r.value == null || !isFinite(r.value) ? '—'
          : r.format === 'pct' ? (r.value * 100).toFixed(1) + '%'
          : r.format === 'days' ? Math.round(r.value) + ' d'
          : r.value.toFixed(2) + '\u00d7';
        const bench = r.format === 'pct' ? (r.benchmark * 100).toFixed(0) + '%'
          : r.format === 'days' ? r.benchmark + ' d'
          : r.benchmark.toFixed(2) + '\u00d7';
        return `<tr><td>${escHtml(r.label)}</td><td>${val}</td>
          <td class="muted">${r.good === 'high' ? '\u2265' : '\u2264'} ${bench}</td></tr>`;
      }).join('');
      return `<h3>${escHtml(g.label)}</h3>
        <table><thead><tr><th>Ratio</th><th>Value</th><th>Benchmark</th></tr></thead>
        <tbody>${cells}</tbody></table>`;
    }).join('');
  }

  // ── Liquidity — cash months + receivables ageing ───────────────────────
  let liquidityHtml = '<p class="muted">Liquidity data not available.</p>';
  if (d.liquidity) {
    const cm = d.liquidity.cash_monthly || [];
    const MN = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const cashRows = cm.map((c: any) => `<tr>
      <td>${MN[c.month] || c.month}</td><td>${nf(c.opening)}</td>
      <td>${nf(c.inflow)}</td><td>${nf(c.outflow)}</td><td><b>${nf(c.closing)}</b></td>
    </tr>`).join('');
    const rec = d.liquidity.receivables || { buckets: [], not_due: 0, total: 0 };
    const ageRows = [`<tr><td>Not yet due</td><td>${nf(rec.not_due || 0)}</td></tr>`]
      .concat((rec.buckets || []).map((b: any) =>
        `<tr><td>${escHtml(b.label)} days</td><td>${nf(b.amount || 0)}</td></tr>`))
      .concat([`<tr class="tot"><td>Total</td><td>${nf(rec.total || 0)}</td></tr>`])
      .join('');
    liquidityHtml = `
      <h3>Cash movement</h3>
      ${cm.length ? `<table><thead><tr><th>Month</th><th>Opening</th><th>Cash In</th><th>Cash Out</th><th>Closing</th></tr></thead><tbody>${cashRows}</tbody></table>`
        : '<p class="muted">No Cash/Bank accounts found.</p>'}
      <h3>Receivables ageing</h3>
      <table><thead><tr><th>Bucket</th><th>Amount</th></tr></thead><tbody>${ageRows}</tbody></table>`;

    // Payables ageing.
    const pay = d.liquidity.payables;
    if (pay) {
      const payRows = [`<tr><td>Not yet due</td><td>${nf(pay.not_due || 0)}</td></tr>`]
        .concat((pay.buckets || []).map((b: any) =>
          `<tr><td>${escHtml(b.label)} days</td><td>${nf(b.amount || 0)}</td></tr>`))
        .concat([`<tr class="tot"><td>Total</td><td>${nf(pay.total || 0)}</td></tr>`])
        .join('');
      liquidityHtml += `<h3>Payables ageing</h3>
        <table><thead><tr><th>Bucket</th><th>Amount</th></tr></thead><tbody>${payRows}</tbody></table>`;
    }

    // Forward projection.
    if (d.liquidity.projection?.rows?.length) {
      const pr = d.liquidity.projection.rows.map((r: any) => `<tr>
        <td>${MN[r.month] || r.month} ${String(r.year).slice(2)}</td>
        <td>${nf(r.opening)}</td><td>${nf(r.expected_in)}</td>
        <td>${nf(r.expected_out)}</td><td><b>${nf(r.closing)}</b></td></tr>`).join('');
      liquidityHtml += `<h3>Forward cash projection</h3>
        <table><thead><tr><th>Month</th><th>Opening</th><th>Expected In</th><th>Expected Out</th><th>Projected Closing</th></tr></thead>
        <tbody>${pr}</tbody></table>`;
    }
  }

  // ── Variance ───────────────────────────────────────────────────────────
  const varRows = d.variance.map((v) => {
    const over = v.gap >= 0;
    const note = (d.varianceNotes || {})[(v as any).key];
    const noteHtml = note?.commentary ? `<tr class="commentary-row"><td colspan="5">
      <div class="commentary">${escHtml(note.commentary)}</div>
      ${note.modified_by ? `<div class="commentary-meta">— ${escHtml(note.modified_by)}</div>` : ''}
    </td></tr>` : '';
    return `<tr>
      <td>${escHtml(v.label)}</td><td>${nf(v.actual)}</td><td>${nf(v.budget)}</td>
      <td class="${over ? 'pos' : 'neg'}">${over ? '+' : ''}${nf(v.gap)}</td>
      <td class="${over ? 'pos' : 'neg'}">${v.gapPct == null ? '—' : (over ? '+' : '') + pct(v.gapPct)}</td>
    </tr>${noteHtml}`;
  }).join('');

  w.document.write(`<!doctype html><html><head><title>Management Pack — ${escHtml(d.company)}</title>
  <style>
    body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; font-size: 11px; color: #15141b; padding: 0; }
    .page { padding: 30px 34px; }
    .cover { padding: 120px 34px; text-align: center; }
    .cover h1 { font-size: 30px; margin: 0 0 8px; }
    .cover .co { font-size: 17px; color: #0c447c; margin-bottom: 4px; }
    .cover .meta { color: #6e6a63; font-size: 12px; margin-top: 30px; }
    h2 { font-size: 15px; border-bottom: 2px solid #0c447c; padding-bottom: 4px; margin: 0 0 12px; }
    h3 { font-size: 12px; margin: 14px 0 5px; color: #0c447c; }
    table { border-collapse: collapse; width: 100%; margin-bottom: 6px; }
    th, td { padding: 4px 8px; border-bottom: 0.5px solid #d9d6d0; text-align: right; }
    th:first-child, td:first-child { text-align: left; }
    thead th { background: #efeeea; font-weight: 600; }
    tr.tot td { background: #e4e9f0; font-weight: 700; }
    .muted { color: #6e6a63; }
    .pos { color: #0f6e56; font-weight: 600; }
    .neg { color: #a32d2d; font-weight: 600; }
    .commentary-row td {
      background: #f7f6f1; padding: 6px 12px; border-top: none;
      border-left: 3px solid #0c447c;
    }
    .commentary { font-style: italic; color: #15141b; line-height: 1.45; }
    .commentary-meta { font-size: 9px; color: #6e6a63; margin-top: 3px; }
    .rag { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
    .rag-green { background: #0f6e56; } .rag-amber { background: #b8860b; } .rag-red { background: #a32d2d; }
    .rag-none { background: #d9d6d0; }
    .section-note { color: #6e6a63; font-size: 10px; margin-top: 4px; }
    .lh-header { padding: 12px 0; border-bottom: 1px solid #d6d5cf; margin-bottom: 16px; }
    .lh-footer { margin-top: 18px; padding-top: 10px; border-top: 1px solid #d6d5cf; font-size: 9px; color: #5f5e5a; }
    @media print { .page, .cover { page-break-after: always; } }
  </style></head><body>

  ${letterhead && letterhead.header_html ? `<div class="lh-header">${letterhead.header_html}</div>` : ''}

  <div class="cover">
    <div class="co">${escHtml(d.company)}</div>
    <h1>Management Pack</h1>
    <div class="co">Fiscal Year ${d.fiscalYear}</div>
    <div class="meta">Source report: ${escHtml(d.reportName)}<br/>Generated ${today}</div>
  </div>

  <div class="page">
    <h2>1 · Executive Summary</h2>
    <table><thead><tr><th>Indicator</th><th>Actual</th><th>% of Budget</th><th>vs FY${d.priorFy}</th></tr></thead>
    <tbody>${kpiRows}</tbody></table>
    <div class="section-note">RAG status reflects performance against budget.${d.trendBasis === 'rolling_12' ? ' Trend basis: trailing 12 months (stitched across fiscal years).' : ' Trend basis: selected fiscal year so far.'}</div>
  </div>

  <div class="page">
    <h2>2 · Financial Ratios</h2>
    ${ratiosHtml}
  </div>

  <div class="page">
    <h2>3 · Liquidity & Cash</h2>
    ${liquidityHtml}
  </div>

  <div class="page">
    <h2>4 · Budget Variance</h2>
    <table><thead><tr><th>Line</th><th>Actual</th><th>Budget</th><th>Variance</th><th>%</th></tr></thead>
    <tbody>${varRows}</tbody></table>
    <div class="section-note">Lines ranked by the size of the gap to budget. Green = ahead of budget, red = behind.</div>
  </div>

  ${letterhead && letterhead.footer_html ? `<div class="lh-footer">${letterhead.footer_html}</div>` : ''}

  <script>window.onload=function(){window.print();}<\/script>
  </body></html>`);
  w.document.close();
}

/* ─── Coloured dashboard export to Excel (v1.9.27) ──────────────────────────
 * Exports the whole dashboard — KPI tiles, ratios, liquidity, ageing,
 * variance, projection — to a single styled .xlsx workbook, with the RAG
 * colours preserved as cell fills, exactly as colourful as on screen.
 */
const RAG_FILL: Record<string, string> = {
  green: 'D6EBE3',  // soft green
  amber: 'F4E7C8',  // soft amber
  red:   'F4D6D6',  // soft red
  none:  'FFFFFF',
};
const RAG_TEXT: Record<string, string> = {
  green: '0F6E56', amber: '8A6500', red: 'A32D2D', none: TB_COLORS.text,
};

export function exportDashboardXlsx(d: MgmtPackData, fileName = 'dashboard.xlsx', letterhead?: LetterheadPayload) {
  const grid: GridCell[][] = [];
  const blank = (): GridCell => ({ text: '' });
  const num = (v: number, fg?: string): GridCell =>
    ({ text: Math.round(v || 0), numeric: true, fg });
  const pct = (v: number | null): GridCell =>
    ({ text: v == null || !isFinite(v) ? '—' : (v * 100).toFixed(1) + '%', align: 'right' });
  const title = (t: string): GridCell => ({ text: t, bold: true, bg: TB_COLORS.headerBg });
  const hdr = (t: string): GridCell => ({ text: t, bold: true, bg: TB_COLORS.headerBg, align: 'right' });

  // v1.9.53 — Letter Head text rows at the top of the worksheet.
  if (letterhead && (letterhead.company_name || letterhead.address_lines.length > 0)) {
    if (letterhead.company_name) grid.push([{ text: letterhead.company_name, bold: true }]);
    letterhead.address_lines.forEach((line) => { if (line) grid.push([{ text: line, fg: TB_COLORS.muted }]); });
    const contact: string[] = [];
    if (letterhead.phone) contact.push('Tel: ' + letterhead.phone);
    if (letterhead.email) contact.push('Email: ' + letterhead.email);
    if (letterhead.website) contact.push('Web: ' + letterhead.website);
    if (contact.length > 0) grid.push([{ text: contact.join(' · '), fg: TB_COLORS.muted }]);
    if (letterhead.tax_id) grid.push([{ text: 'Tax ID / VAT: ' + letterhead.tax_id, fg: TB_COLORS.muted }]);
    grid.push([blank()]);
  }

  // ── Title ──────────────────────────────────────────────────────────────
  grid.push([{ text: `Management Dashboard — ${d.company}`, bold: true }]);
  grid.push([{ text: `Fiscal Year ${d.fiscalYear}  ·  ${d.reportName}`, fg: TB_COLORS.muted }]);
  grid.push([blank()]);

  // ── 1. Executive summary (KPI tiles) ───────────────────────────────────
  grid.push([title('EXECUTIVE SUMMARY')]);
  grid.push([hdr('Indicator'), hdr('Actual'), hdr('% of Budget'), hdr(`vs FY${d.priorFy}`), hdr('Status')]);
  for (const t of d.tiles) {
    grid.push([
      { text: t.label, bg: RAG_FILL[t.rag] || 'FFFFFF', fg: RAG_TEXT[t.rag] || TB_COLORS.text, bold: true },
      num(t.val),
      pct(t.achievement),
      { text: t.growth == null ? '—' : (t.growth >= 0 ? '+' : '') + (t.growth * 100).toFixed(1) + '%', align: 'right' },
      { text: (t.rag || 'none').toUpperCase(), bg: RAG_FILL[t.rag] || 'FFFFFF', fg: RAG_TEXT[t.rag] || TB_COLORS.text, align: 'center', bold: true },
    ]);
  }
  grid.push([blank()]);

  // ── 2. Financial ratios ────────────────────────────────────────────────
  if (d.ratios?.groups?.length) {
    grid.push([title('FINANCIAL RATIOS')]);
    for (const g of d.ratios.groups) {
      grid.push([{ text: g.label, bold: true, fg: '0C447C' }]);
      grid.push([hdr('Ratio'), hdr('Value'), hdr('Benchmark')]);
      for (const r of g.ratios) {
        const val = r.value == null || !isFinite(r.value) ? '—'
          : r.format === 'pct' ? (r.value * 100).toFixed(1) + '%'
          : r.format === 'days' ? Math.round(r.value) + ' d'
          : r.value.toFixed(2) + '\u00d7';
        const ratio = r.value == null ? null
          : (r.good === 'high' ? r.value / r.benchmark : r.benchmark / r.value);
        const rag = ratio == null ? 'none' : ratio >= 1 ? 'green' : ratio >= 0.8 ? 'amber' : 'red';
        const bench = r.format === 'pct' ? (r.benchmark * 100).toFixed(0) + '%'
          : r.format === 'days' ? r.benchmark + ' d'
          : r.benchmark.toFixed(2) + '\u00d7';
        grid.push([
          { text: r.label, bg: RAG_FILL[rag], fg: RAG_TEXT[rag] },
          { text: val, align: 'right', bg: RAG_FILL[rag], fg: RAG_TEXT[rag], bold: true },
          { text: (r.good === 'high' ? '\u2265 ' : '\u2264 ') + bench, align: 'right', fg: TB_COLORS.muted },
        ]);
      }
      grid.push([blank()]);
    }
  }

  // ── 3. Liquidity ───────────────────────────────────────────────────────
  if (d.liquidity) {
    const MN = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    grid.push([title('LIQUIDITY — CASH MOVEMENT')]);
    grid.push([hdr('Month'), hdr('Opening'), hdr('Cash In'), hdr('Cash Out'), hdr('Closing')]);
    for (const c of (d.liquidity.cash_monthly || [])) {
      grid.push([
        { text: MN[c.month] || String(c.month) },
        num(c.opening), num(c.inflow, '0F6E56'), num(c.outflow, 'A32D2D'),
        { text: Math.round(c.closing || 0), numeric: true, bold: true,
          fg: c.closing < 0 ? 'A32D2D' : TB_COLORS.text },
      ]);
    }
    grid.push([blank()]);

    // Receivables + payables ageing, side by side conceptually (stacked).
    const ageTable = (label: string, src: any) => {
      if (!src) return;
      grid.push([title(label)]);
      grid.push([hdr('Bucket'), hdr('Amount')]);
      grid.push([{ text: 'Not yet due' }, num(src.not_due || 0)]);
      for (const b of (src.buckets || [])) {
        grid.push([
          { text: b.label + ' days' },
          { text: Math.round(b.amount || 0), numeric: true, fg: b.amount > 0 ? 'A32D2D' : TB_COLORS.text },
        ]);
      }
      grid.push([{ text: 'Total', bold: true, bg: TB_COLORS.totalBg },
                 { text: Math.round(src.total || 0), numeric: true, bold: true, bg: TB_COLORS.totalBg }]);
      grid.push([blank()]);
    };
    ageTable('RECEIVABLES AGEING', d.liquidity.receivables);
    ageTable('PAYABLES AGEING', d.liquidity.payables);

    // Forward projection.
    if (d.liquidity.projection?.rows?.length) {
      grid.push([title(`FORWARD CASH PROJECTION — ${d.liquidity.projection.months} MONTHS`)]);
      grid.push([hdr('Month'), hdr('Opening'), hdr('Expected In'), hdr('Expected Out'), hdr('Projected Closing')]);
      for (const r of d.liquidity.projection.rows) {
        grid.push([
          { text: (MN[r.month] || r.month) + ' ' + String(r.year).slice(2) },
          num(r.opening), num(r.expected_in, '0F6E56'), num(r.expected_out, 'A32D2D'),
          { text: Math.round(r.closing || 0), numeric: true, bold: true,
            fg: r.closing < 0 ? 'A32D2D' : TB_COLORS.text },
        ]);
      }
      grid.push([blank()]);
    }
  }

  // ── 4. Variance ────────────────────────────────────────────────────────
  grid.push([title('BIGGEST VARIANCES VS BUDGET')]);
  grid.push([hdr('Line'), hdr('Actual'), hdr('Budget'), hdr('Variance'), hdr('%')]);
  for (const v of d.variance) {
    const over = v.gap >= 0;
    const fg = over ? '0F6E56' : 'A32D2D';
    grid.push([
      { text: v.label },
      num(v.actual), num(v.budget),
      { text: Math.round(v.gap || 0), numeric: true, fg, bold: true },
      { text: v.gapPct == null ? '—' : (over ? '+' : '') + (v.gapPct * 100).toFixed(1) + '%',
        align: 'right', fg, bold: true },
    ]);
  }

  downloadBlob(gridToXlsxBlob(grid), fileName);
}
