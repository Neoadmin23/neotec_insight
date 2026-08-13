import { createContext, useContext } from 'react';

/* ─── Dimension filters context (v1.9.52) ─────────────────────────────────
 *
 * Custom Accounting Dimensions are discovered globally per bench but applied
 * per-view. This context shares the active filter map across workspaces
 * (Run / Dashboard / CFO Briefing / Group) so a user who sets "Region: GCC"
 * in the Run tab sees that scope respected in the Briefing too.
 *
 * Shape: { fieldname → selected value }. Empty value or missing key means
 * "no filter on this dimension".
 *
 * The context lives at the App root. Workspaces read it via useDimFilters()
 * and call setDimFilters() when the user changes a filter — but in practice
 * the canonical writer is the RunTab; other workspaces are mostly readers.
 * That keeps the mental model simple: edit filters where you read the data.
 *
 * NOT persisted — refreshing the page resets to empty. Persistence would
 * mean a per-user preference store; reasonable next step if users complain,
 * but not needed in v1.9.52.
 */

/* v1.9.58 — filter map values widened to string OR string[] for multi-
 * select. Empty string or empty array means "no filter on this dimension."
 * `compactDimFilters` drops empties and returns the multi-shaped payload.
 */
export type DimensionFiltersMap = Record<string, string | string[]>;

export interface DimensionFiltersContextValue {
  filters: DimensionFiltersMap;
  setFilters: (updater: DimensionFiltersMap | ((prev: DimensionFiltersMap) => DimensionFiltersMap)) => void;
  dimensions: Array<{ fieldname: string; label: string; document_type: string }>;
  setDimensions: (dims: Array<{ fieldname: string; label: string; document_type: string }>) => void;
}

export const DEFAULT_DIM_FILTERS_CTX: DimensionFiltersContextValue = {
  filters: {},
  setFilters: () => {},
  dimensions: [],
  setDimensions: () => {},
};

export const DimensionFiltersContext = createContext<DimensionFiltersContextValue>(DEFAULT_DIM_FILTERS_CTX);

export function useDimFilters(): DimensionFiltersContextValue {
  return useContext(DimensionFiltersContext);
}

/** Compact the filter map for an API call — drop empties.
 * v1.9.58 — values can be string or string[]; empties of either drop.
 * Returns undefined when the map is fully empty so callers can avoid
 * sending it at all. */
export function compactDimFilters(filters: DimensionFiltersMap): Record<string, string | string[]> | undefined {
  const out: Record<string, string | string[]> = {};
  for (const [k, v] of Object.entries(filters)) {
    if (Array.isArray(v)) {
      const cleaned = v.filter((s) => s && String(s).trim());
      if (cleaned.length > 0) out[k] = cleaned;
    } else if (v && String(v).trim()) {
      out[k] = v;
    }
  }
  return Object.keys(out).length > 0 ? out : undefined;
}
