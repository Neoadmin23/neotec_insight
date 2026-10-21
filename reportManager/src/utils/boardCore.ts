/* Dashboard designer — pure logic (v2.99.0), tested in scripts/test-board.mjs. */

export type VisualType = 'kpi' | 'bar' | 'line' | 'area' | 'pie' | 'waterfall' | 'table' | 'pivot';
export interface Col { key: string; label: string; type: string }
export interface Dataset {
  key: string; title: string; figures: { label: string; value: number | null; format: string }[];
  columns: Col[]; rows: Record<string, any>[]; notes: string[];
  chart: null | { type: string; labels: string[]; series: { name: string; data: any[]; kinds?: string[] }[] };
}
export interface Visual {
  id: string; title: string; type: VisualType; source: { kind: 'analysis' | 'studio'; key?: string; report?: string };
  x: number; y: number; w: number; h: number; options?: { row?: string; column?: string; value?: string };
}
export const COLS = 12;
const NUM = new Set(['currency', 'number', 'percent', 'int']);

export const isNum = (c: Col) => NUM.has(c.type);
export const labelCol = (d: Dataset) => d.columns.find((c) => !isNum(c));
export const numCols = (d: Dataset) => d.columns.filter(isNum);

/* ── layout ───────────────────────────────────────────────────────────── */
const overlap = (a: Visual, b: Visual) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

export function clampVisual(v: Visual): Visual {
  const w = Math.max(2, Math.min(COLS, Math.round(v.w)));
  const h = Math.max(2, Math.min(12, Math.round(v.h)));
  return { ...v, w, h, x: Math.max(0, Math.min(COLS - w, Math.round(v.x))), y: Math.max(0, Math.round(v.y)) };
}

/** Place `moved` and push anything it now overlaps further down, repeatedly. */
export function placeVisual(layout: Visual[], moved: Visual): Visual[] {
  const m = clampVisual(moved);
  const out = layout.map((v) => (v.id === m.id ? m : v));
  for (let guard = 0; guard < 200; guard++) {
    let changed = false;
    const sorted = [...out].sort((a, b) => a.y - b.y || a.x - b.x);
    for (const a of sorted) {
      for (const b of sorted) {
        if (a === b || !overlap(a, b)) continue;
        const keep = a.id === m.id ? a : b.id === m.id ? b : a.y <= b.y ? a : b;
        const push = keep === a ? b : a;
        push.y = keep.y + keep.h;
        changed = true;
      }
    }
    if (!changed) break;
  }
  return out;
}

/** First free row below everything — where a new visual goes. */
export const nextY = (layout: Visual[]) => layout.reduce((m, v) => Math.max(m, v.y + v.h), 0);

/* ── data ─────────────────────────────────────────────────────────────── */
export interface CrossFilter { key: string; value: any; from: string }

/** Rows of a visual with the page's click-to-filter applied (if it has that column). */
export function applyCrossFilter(d: Dataset, f: CrossFilter | null, visualId: string): Dataset {
  if (!f || f.from === visualId || !d.columns.some((c) => c.key === f.key)) return d;
  return { ...d, rows: d.rows.filter((r) => String(r[f.key]) === String(f.value)) };
}

export function kpiValue(d: Dataset): { label: string; value: number | null; format: string } {
  if (d.figures?.length) return d.figures[0];
  const n = numCols(d)[0];
  if (!n) return { label: d.title, value: d.rows.length, format: 'number' };
  return { label: n.label, value: d.rows.reduce((s, r) => s + (Number(r[n.key]) || 0), 0), format: n.type };
}

export function pivot(d: Dataset, row?: string, column?: string, value?: string) {
  const r = row || labelCol(d)?.key;
  const v = value || numCols(d)[0]?.key;
  if (!r || !v) return { rowKeys: [] as string[], colKeys: [] as string[], cells: {} as Record<string, number>, rowTotals: {} as Record<string, number>, colTotals: {} as Record<string, number>, total: 0 };
  const cells: Record<string, number> = {}, rowTotals: Record<string, number> = {}, colTotals: Record<string, number> = {};
  const rowKeys: string[] = [], colKeys: string[] = [];
  let total = 0;
  for (const rec of d.rows) {
    const rk = String(rec[r] ?? '—'), ck = column ? String(rec[column] ?? '—') : 'Total';
    const n = Number(rec[v]) || 0;
    if (!rowKeys.includes(rk)) rowKeys.push(rk);
    if (!colKeys.includes(ck)) colKeys.push(ck);
    cells[`${rk}\u0000${ck}`] = (cells[`${rk}\u0000${ck}`] || 0) + n;
    rowTotals[rk] = (rowTotals[rk] || 0) + n;
    colTotals[ck] = (colTotals[ck] || 0) + n;
    total += n;
  }
  return { rowKeys, colKeys, cells, rowTotals, colTotals, total };
}

/** Chart.js-ready labels and series for bar/line/area/pie/waterfall. */
export function chartData(d: Dataset, type: VisualType) {
  if (type === 'waterfall' && d.chart?.type === 'waterfall') {
    return { labels: d.chart.labels, series: d.chart.series, waterfall: true, labelKey: labelCol(d)?.key || '' };
  }
  const lc = labelCol(d);
  const nums = type === 'pie' ? numCols(d).slice(0, 1) : numCols(d).slice(0, 4);
  const rows = type === 'pie' ? d.rows.slice(0, 12) : d.rows.slice(0, 60);
  return {
    labels: rows.map((r) => String(lc ? r[lc.key] ?? '—' : '')),
    series: nums.map((c) => ({ name: c.label, data: rows.map((r) => Number(r[c.key]) || 0) })),
    waterfall: false, labelKey: lc?.key || '',
  };
}
