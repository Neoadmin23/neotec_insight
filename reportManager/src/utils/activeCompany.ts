/* v2.55.0 — the active company, shared across the shell.
 *
 * Every report tab owns its own Company dropdown, which is right: a user can
 * run the ledger for one company while the briefing sits on another. But the
 * header brand block, the Brand Kit and the printed letterhead all need to
 * know which company is *currently* in view, so each tab publishes its
 * selection here and the shell subscribes.
 *
 * Deliberately tiny — no context provider, no re-render cascade. The header is
 * the only subscriber that matters and it re-renders on its own.
 */

const KEY = 'ni-active-company';

let current: string = (() => {
  try { return localStorage.getItem(KEY) || localStorage.getItem('ni-gl-lastco') || ''; }
  catch { return ''; }
})();

type Listener = (company: string) => void;
const listeners = new Set<Listener>();

export function getActiveCompany(): string { return current; }

/** Publish the company a tab has switched to. No-op when unchanged, so this is
 *  safe to call from a render-adjacent effect without looping. */
export function setActiveCompany(company: string | null | undefined) {
  const next = (company || '').trim();
  if (!next || next === current) return;
  current = next;
  try {
    localStorage.setItem(KEY, next);
    // Keep the legacy key in step: branddoc.loadBrand() still reads it when
    // no company is passed explicitly.
    localStorage.setItem('ni-gl-lastco', next);
  } catch { /* private mode — in-memory only */ }
  listeners.forEach((fn) => { try { fn(next); } catch { /* subscriber's problem */ } });
}

export function onActiveCompany(fn: Listener): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
