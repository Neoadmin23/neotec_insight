import { createContext, useContext } from 'react';

/* ─── Access profile context (v1.9.45) ─────────────────────────────────────
 * Frontend reads the user's role tier once on app load and exposes it via
 * context. Components that show edit affordances (Save buttons, Rows tab,
 * Account map tab, Budget tab) read `canEdit` and hide themselves for users
 * without edit permission.
 *
 * Defence in depth: hiding the UI is half the protection. The backend
 * endpoints (save_report, save_budget_cells, save_mapping_rule) enforce
 * the same check server-side — a user can't bypass by calling the API
 * directly. UI hiding alone would not be safe.
 *
 * Default value: full access. This is the safe-on-error fallback — if the
 * access endpoint fails (e.g. older bench without the helper), users see
 * what they did before. The backend still enforces; this fallback never
 * grants real access, only the UI affordance to attempt it.
 */

export type RoleTier = 'admin' | 'cfo' | 'ceo' | 'group_viewer' | 'hr' | 'basic';

export interface AccessProfile {
  roleTier: RoleTier;
  canEdit: boolean;
  canSeeGroup: boolean;
  /** v2.84.0 — the user's only Insight access is the People workspace. Every
   *  other tab is hidden. The backend refuses the same requests independently
   *  (`_check_hr_only`), so this is the affordance, not the protection. */
  hrOnly: boolean;
  user: string;
}

export const DEFAULT_ACCESS: AccessProfile = {
  roleTier: 'admin',
  hrOnly: false,
  canEdit: true,
  canSeeGroup: true,
  user: '',
};

export const AccessContext = createContext<AccessProfile>(DEFAULT_ACCESS);

export function useAccess(): AccessProfile {
  return useContext(AccessContext);
}
