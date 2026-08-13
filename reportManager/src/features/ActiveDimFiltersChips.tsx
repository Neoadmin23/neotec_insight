import { useDimFilters } from '../utils/dimFilters';

/* ─── Active dimension filter chips (v1.9.52) ─────────────────────────────
 *
 * A small inline indicator showing which custom Accounting Dimensions are
 * currently filtered. Renders nothing when no filters are active. Each chip
 * shows "Label: value ×" with a clear/remove action.
 *
 * Design intent: when a user sets "Region: GCC" in the Run tab and then
 * switches to the CFO Briefing, they should see at the top that the scope
 * is still applied. Avoids the silent-scope footgun where a stale filter
 * causes confusion ("why is my revenue so low?").
 */
export function ActiveDimFiltersChips() {
  const { filters, dimensions, setFilters } = useDimFilters();
  // v1.9.58 — values may be string or string[]. Both should display
  // meaningfully and survive serialisation. We collapse arrays for display
  // (e.g. "CC-A, CC-B, +1 more") and treat empties as inactive.
  const active = Object.entries(filters).filter(([, v]) => {
    if (Array.isArray(v)) return v.length > 0;
    return v && String(v).trim();
  });
  if (active.length === 0) return null;

  const labelFor = (fieldname: string) =>
    dimensions.find((d) => d.fieldname === fieldname)?.label || fieldname;

  function displayValue(v: string | string[]): string {
    if (Array.isArray(v)) {
      if (v.length === 0) return '';
      if (v.length <= 2) return v.join(', ');
      return `${v.slice(0, 2).join(', ')}, +${v.length - 2} more`;
    }
    return String(v);
  }

  const clear = (fieldname: string) =>
    setFilters((prev) => {
      const next = { ...prev };
      delete next[fieldname];
      return next;
    });

  const clearAll = () => setFilters({});

  return (
    <div className="dim-chips" role="status" aria-live="polite">
      <span className="dim-chips-prefix">
        <i className="ti ti-filter" aria-hidden /> Active filters:
      </span>
      {active.map(([fieldname, value]) => (
        <span key={fieldname} className="dim-chip">
          <span className="dim-chip-key">{labelFor(fieldname)}:</span>
          <span className="dim-chip-val">{displayValue(value)}</span>
          <button
            className="dim-chip-x"
            onClick={() => clear(fieldname)}
            aria-label={`Clear ${labelFor(fieldname)} filter`}
            title={`Clear ${labelFor(fieldname)} filter`}
          >×</button>
        </span>
      ))}
      {active.length > 1 && (
        <button className="dim-chips-clear-all" onClick={clearAll}>Clear all</button>
      )}
    </div>
  );
}
