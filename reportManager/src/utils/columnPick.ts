/* v3.7.5 — choose which columns go into Print / PDF / Excel / CSV / Image.
 * Pure: the first column (the row label) always stays; rows whose cells span
 * several columns (sections, gaps) are left as they are. The choice is kept
 * per report in this browser. */
import type { ReportDoc } from './reportdoc';

const KEY = (report: string) => `insight.exportCols.${report}`;

export function loadHidden(report: string): string[] {
  try { return JSON.parse(localStorage.getItem(KEY(report)) || '[]') || []; } catch { return []; }
}

export function saveHidden(report: string, hidden: string[]): void {
  try { localStorage.setItem(KEY(report), JSON.stringify(hidden)); } catch { /* private mode */ }
}

export function applyColumnPick(doc: ReportDoc, hidden: string[]): ReportDoc {
  if (!hidden?.length || !doc?.columns?.length) return doc;
  const hide = new Set(hidden);
  const keep = doc.columns.map((c, i) => i === 0 || !hide.has(c.label));
  if (keep.every(Boolean)) return doc;
  const n = doc.columns.length;
  const pick = <T,>(arr: T[]) => arr.filter((_x, i) => keep[i]);
  return {
    ...doc,
    columns: pick(doc.columns),
    rows: (doc.rows || []).map((r: any) => {
      const cells = r.cells || [];
      const plain = cells.length === n && !cells.some((c: any) => c && c.colSpan && c.colSpan > 1);
      return plain ? { ...r, cells: pick(cells) } : r;
    }),
  };
}
