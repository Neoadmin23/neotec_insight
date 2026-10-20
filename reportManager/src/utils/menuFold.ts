/* v2.97.1 — fold the old top-level Briefing / Dashboard / Analysis sections
 * into Performance. Pure (no React), so it is tested on its own.
 *
 * v2.97.0 broke this: the description of the OLD Analysis section gained the
 * new 'insights' tab, so a saved menu in the old layout no longer matched,
 * folding was skipped, and the new tab was put in a fresh Performance section
 * next to the old ones. Now:
 *   - the old layout is described exactly as it shipped;
 *   - a section still in that layout folds even when a Performance section
 *     already exists (the state v2.97.0 left behind);
 *   - a section someone changed by hand (other tabs added) is left alone;
 *   - hidden flags travel with their tabs; tabs take Performance's order. */

export interface FoldTab { ws: string; hidden?: unknown }
export interface FoldSection<T extends FoldTab = FoldTab> { key: string; label: string; tabs: T[] }

export const LEGACY_PERF: Record<string, string[]> = {
  briefing: ['briefing'],
  dashboard: ['dashboard'],
  analysis: ['health', 'group'],
};
// Tabs a legacy section may also hold without counting as hand-made.
const LEGACY_EXTRA: Record<string, string[]> = { analysis: ['insights', 'classification'] };

function untouchedLegacy(sec: FoldSection): boolean {
  const base = LEGACY_PERF[sec.key];
  if (!base) return false;
  const ok = new Set([...base, ...(LEGACY_EXTRA[sec.key] || [])]);
  const have = (sec.tabs || []).map((t) => t.ws);
  return have.every((w) => ok.has(w));
}

export function foldLegacyPerformance<T extends FoldTab, S extends FoldSection<T>>(
  sections: S[], perfKey: string, perfLabel: string, perfOrder: string[],
): S[] {
  const legacy = sections.filter((s) => untouchedLegacy(s));
  if (!legacy.length) return sections;
  const perf = sections.find((s) => s.key === perfKey);
  const seen = new Set<string>();
  const tabs: T[] = [];
  for (const t of [...(perf?.tabs || []), ...legacy.flatMap((s) => s.tabs || [])]) {
    if (seen.has(t.ws)) continue;
    seen.add(t.ws);
    tabs.push(t);
  }
  const rank = (w: string) => { const i = perfOrder.indexOf(w); return i < 0 ? perfOrder.length : i; };
  tabs.sort((a, b) => rank(a.ws) - rank(b.ws));
  const merged = { ...(perf || {}), key: perfKey, label: perf?.label || perfLabel, tabs } as S;
  const folded = new Set<string>([perfKey, ...legacy.map((s) => s.key)]);
  const out: S[] = [];
  let placed = false;
  for (const sec of sections) {
    if (!folded.has(sec.key)) { out.push(sec); continue; }
    if (!placed) { out.push(merged); placed = true; }
  }
  return out;
}
