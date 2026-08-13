import { useEffect, useRef, useState } from 'react';
import { api } from '../../utils/api';

interface QuickLink {
  label: string;
  url: string;
  icon?: string;
  open_in_new_tab?: number;
}

/**
 * Header dropdown that lets the user leave Insight and go back into the
 * ERP desk. "Back to ERP Desk" is a fixed primary item (same-tab). Below
 * it, a user-configurable list of links comes from the Insight Quick Link
 * DocType — these open in a new tab by default so Insight stays open.
 */
export function BackToErpMenu() {
  const [open, setOpen] = useState(false);
  const [links, setLinks] = useState<QuickLink[]>([]);
  const [loaded, setLoaded] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Load the configurable links once, lazily on first open.
  useEffect(() => {
    if (!open || loaded) return;
    let cancelled = false;
    api.listQuickLinks()
      .then((rows) => { if (!cancelled) { setLinks(rows || []); setLoaded(true); } })
      .catch(() => { if (!cancelled) { setLinks([]); setLoaded(true); } });
    return () => { cancelled = true; };
  }, [open, loaded]);

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  function go(url: string, newTab: boolean) {
    setOpen(false);
    if (newTab) window.open(url, '_blank', 'noopener,noreferrer');
    else window.location.href = url;
  }

  return (
    <div className="erp-menu" ref={wrapRef}>
      <button
        className="ws-btn erp-menu-trigger"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Go back to the ERP desk"
      >
        <i className="ti ti-arrow-back-up" aria-hidden /> ERP
        <i className="ti ti-chevron-down erp-menu-caret" aria-hidden />
      </button>

      {open && (
        <div className="erp-menu-pop" role="menu">
          {/* Fixed primary item — same tab, you're leaving Insight. */}
          <button className="erp-menu-item is-primary" role="menuitem" onClick={() => go('/app', false)}>
            <i className="ti ti-home-2" aria-hidden />
            <span>Back to ERP Desk</span>
          </button>

          {(links.length > 0 || !loaded) && <div className="erp-menu-sep" />}

          {!loaded && <div className="erp-menu-empty">Loading links…</div>}

          {loaded && links.map((lnk) => (
            <button
              key={lnk.label}
              className="erp-menu-item"
              role="menuitem"
              onClick={() => go(lnk.url, lnk.open_in_new_tab !== 0)}
              title={lnk.url}
            >
              <i className={'ti ti-' + (lnk.icon || 'external-link')} aria-hidden />
              <span>{lnk.label}</span>
              {lnk.open_in_new_tab !== 0 && (
                <i className="ti ti-external-link erp-menu-newtab" aria-hidden />
              )}
            </button>
          ))}

          {loaded && links.length === 0 && (
            <div className="erp-menu-empty">
              No quick links yet. Add them in ERP under “Insight Quick Link”.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
