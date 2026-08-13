import { useEffect, useMemo, useRef, useState } from 'react';
import { arName } from '../utils/i18n';

/* ─── DimensionMultiSelect (v1.9.58) ─────────────────────────────────────
 *
 * Multi-select picker for dimension filter values (cost centers, projects,
 * departments, branches, custom Accounting Dimensions).
 *
 * Layout discipline:
 *   - Collapsed field renders at the same height as a native <select>
 *     (30px), so it doesn't disrupt the .filter-grid baseline alignment.
 *   - Chips render inline up to 2 selections; 3+ collapses to "+N more"
 *     so the field never grows taller than one line.
 *   - The dropdown opens as an absolutely-positioned popover anchored
 *     below the field — never pushes neighbouring cells around.
 *   - The popover has its own scroll for long option lists; the field
 *     itself stays compact.
 *   - Outside-click closes the popover.
 *
 * API contract:
 *   - value: string[] (empty array == no filter)
 *   - options: { name: string, label?: string }[]
 *   - onChange: (next: string[]) => void
 *
 * The component is intentionally controlled (no internal selection state)
 * so the parent component owns the source of truth and consistent
 * behaviour across re-mounts.
 */

export interface MultiSelectOption {
  name: string;
  label?: string;
}

interface Props {
  value: string[];
  options: MultiSelectOption[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  /** Optional id for testing / aria. */
  id?: string;
}

export function DimensionMultiSelect({
  value, options, onChange, placeholder = '— All —', disabled = false, id,
}: Props) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const rootRef = useRef<HTMLDivElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  // Outside-click closes the popover. Listen on document so any click
  // outside the picker chrome (which includes both the field button and
  // the popover) collapses it.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      const t = e.target as Node;
      if (rootRef.current && !rootRef.current.contains(t)
          && popoverRef.current && !popoverRef.current.contains(t)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  // Auto-focus the search input when the popover opens — keyboard users
  // can start typing immediately.
  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => searchRef.current?.focus());
    }
  }, [open]);

  // Escape closes the popover.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  const selectedSet = useMemo(() => new Set(value), [value]);

  const filteredOptions = useMemo(() => {
    if (!search.trim()) return options;
    const q = search.trim().toLowerCase();
    return options.filter((o) =>
      (o.label || o.name).toLowerCase().includes(q)
      || o.name.toLowerCase().includes(q)
    );
  }, [options, search]);

  // Resolve label for a selected name. If the option list has the entry,
  // use its label; otherwise fall back to the raw name (covers the case
  // where a previously-selected value no longer exists in options).
  function labelFor(name: string): string {
    return arName(name, options.find((o) => o.name === name)?.label || name);
  }

  function toggle(name: string) {
    if (selectedSet.has(name)) {
      onChange(value.filter((v) => v !== name));
    } else {
      onChange([...value, name]);
    }
  }

  function clearAll() {
    onChange([]);
  }

  function selectAllVisible() {
    const visible = filteredOptions.map((o) => o.name);
    const set = new Set([...value, ...visible]);
    onChange(Array.from(set));
  }

  // Chip rendering: at most 2 chips visible, then "+N more" — keeps the
  // collapsed field one line tall regardless of how many are selected.
  function renderChips() {
    if (value.length === 0) {
      return <span className="dms-placeholder">{placeholder}</span>;
    }
    const head = value.slice(0, 2);
    const extra = value.length - head.length;
    return (
      <>
        {head.map((v, i) => (
          <span key={v} className="dms-chip">
            {labelFor(v)}
            {i < head.length - 1 && <span className="dms-chip-sep">·</span>}
          </span>
        ))}
        {extra > 0 && <span className="dms-chip-more">+{extra} more</span>}
      </>
    );
  }

  return (
    <div className="dms-root" ref={rootRef} id={id}>
      <button
        type="button"
        className={'dms-field' + (disabled ? ' disabled' : '') + (open ? ' open' : '')}
        onClick={() => !disabled && setOpen((o) => !o)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="dms-chips">{renderChips()}</span>
        {value.length > 0 && !disabled && (
          <button
            type="button"
            className="dms-clear"
            onClick={(e) => { e.stopPropagation(); clearAll(); }}
            aria-label="Clear all selections"
            title="Clear all"
          >×</button>
        )}
        <span className="dms-caret" aria-hidden>▾</span>
      </button>

      {open && (
        <div className="dms-popover" ref={popoverRef} role="listbox">
          <div className="dms-search-row">
            <input
              ref={searchRef}
              type="text"
              className="dms-search"
              placeholder="Search…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="dms-actions">
            <button type="button" className="dms-act-btn" onClick={selectAllVisible}>
              Select {search ? 'visible' : 'all'}
            </button>
            <button type="button" className="dms-act-btn" onClick={clearAll}>
              Clear
            </button>
            <span className="dms-count">
              {value.length > 0 ? `${value.length} selected` : 'none selected'}
            </span>
          </div>
          <div className="dms-options" role="presentation">
            {filteredOptions.length === 0 && (
              <div className="dms-empty">No matches.</div>
            )}
            {filteredOptions.map((o) => {
              const checked = selectedSet.has(o.name);
              return (
                <label key={o.name} className={'dms-option' + (checked ? ' checked' : '')}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggle(o.name)}
                  />
                  <span className="dms-opt-label">{arName(o.name, o.label || o.name)}</span>
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
