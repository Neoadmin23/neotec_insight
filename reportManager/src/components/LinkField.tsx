import { useEffect, useRef, useState } from 'react';
import { api } from '../utils/api';
import { t } from '../utils/i18n';

/** Link picker with tree navigation (v2.72.0).
 *
 *  Tree doctypes are navigable AND searchable. A chart of accounts is best
 *  drilled when you know roughly where a thing lives and best searched when you
 *  know its name; forcing either one alone makes the other case tedious.
 *
 *  Group nodes are branches, never selections by default — an account that
 *  carries no balance cannot be a VAT control account, and letting someone
 *  pick one only produces a setting that silently resolves to nothing.
 *
 *  v2.87.4 — `allowGroupSelection` lifts that for callers that have a real
 *  use for a group (Cash Flow Forecast's account binding: bind a whole
 *  account-tree branch, resolved live to its current leaf accounts every
 *  time the report runs, rather than picking leaves one at a time). Off by
 *  default — every existing caller's behaviour is unchanged. */

type Opt = { value: string; label: string; code?: string; meta?: string; is_group?: boolean };

export default function LinkField({
  doctype, company, value, onChange, placeholder, disabled, allowGroupSelection,
}: {
  doctype: 'Account' | 'Customer' | 'Customer Group' | 'Sales Invoice' | 'Cost Center';
  company?: string | null;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
  allowGroupSelection?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [opts, setOpts] = useState<Opt[]>([]);
  const [tree, setTree] = useState(false);
  const [searched, setSearched] = useState(false);
  const [trail, setTrail] = useState<{ value: string; label: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  function fetchOpts(parent: string | null, q: string) {
    setLoading(true);
    api.linkOptions(doctype, company ?? null, parent, q)
      .then((r: any) => { setOpts(r?.options || []); setTree(!!r?.tree); setSearched(!!r?.searched); })
      .catch(() => setOpts([]))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (!open) return;
    const id = setTimeout(() => fetchOpts(trail.length ? trail[trail.length - 1].value : null, query), 220);
    return () => clearTimeout(id);
  }, [open, query, trail, doctype, company]);

  useEffect(() => {
    function away(e: MouseEvent) {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', away);
    return () => document.removeEventListener('mousedown', away);
  }, []);

  function pick(o: Opt) {
    if (o.is_group) { setTrail([...trail, { value: o.value, label: o.label }]); setQuery(''); return; }
    onChange(o.value); setOpen(false); setQuery('');
  }

  function selectGroup(o: Opt, e: React.MouseEvent) {
    e.stopPropagation();
    onChange(o.value); setOpen(false); setQuery('');
  }

  return (
    <div className="lf" ref={box}>
      <div className="lf-control">
        <input
          className="lf-input"
          value={open ? query : value}
          disabled={disabled}
          placeholder={value || placeholder || t('Search…')}
          onFocus={() => { setOpen(true); setQuery(''); setTrail([]); }}
          onChange={(e) => setQuery(e.target.value)}
        />
        {value && !disabled &&
          <button className="lf-clear" title={t('Clear')}
            onClick={() => { onChange(''); setQuery(''); }}>×</button>}
      </div>

      {open && (
        <div className="lf-menu">
          {tree && !searched && (
            <div className="lf-crumbs">
              <button onClick={() => setTrail([])}>{t('All')}</button>
              {trail.map((c, i) => (
                <span key={c.value}>
                  <span className="lf-sep">/</span>
                  <button onClick={() => setTrail(trail.slice(0, i + 1))}>{c.label}</button>
                </span>
              ))}
            </div>
          )}
          {loading && <div className="lf-note">{t('Loading…')}</div>}
          {!loading && opts.length === 0 && <div className="lf-note">{t('Nothing found.')}</div>}
          {!loading && opts.map((o) => (
            <button key={o.value} className={o.is_group ? 'lf-opt is-group' : 'lf-opt'}
              onClick={() => pick(o)}
              title={o.is_group ? t('Group — open to see what is inside') : o.value}>
              <span className="lf-main">
                {o.code && <span className="cls-num">{o.code}</span>}
                {o.label}
                {o.is_group && <span className="lf-arrow">›</span>}
              </span>
              {o.is_group && allowGroupSelection && (
                <span className="lf-use-group" onClick={(e) => selectGroup(o, e)}
                  title={t('Bind the whole group — resolved live to its current leaf accounts every run')}>
                  {t('Use group')}
                </span>
              )}
              {o.meta && <span className="lf-meta">{o.meta}</span>}
            </button>
          ))}
          {tree && !searched &&
            <div className="lf-note">{t('Type to search the whole tree, or open a group to drill in.')}</div>}
        </div>
      )}
    </div>
  );
}
