import { useEffect, useMemo, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

interface TreeGroup { group: string; parties: { name: string; label: string }[] }

/** Party tree (v2.34.0) — customers/suppliers grouped by their Customer/
 *  Supplier Group, multi-select with group-level toggles and search. Used by
 *  BOTH the Summary (default: all selected) and Bill-wise views. `selected`
 *  = null means "all" (summary default). */
export default function PartyTreePicker({ side, selected, onChange }: {
  side: 'Customer' | 'Supplier';
  selected: string[] | null;
  onChange: (sel: string[] | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [tree, setTree] = useState<TreeGroup[]>([]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [q, setQ] = useState('');

  useEffect(() => { setTree([]); }, [side]);
  const load = () => { if (!tree.length) api.partyTree(side).then(setTree).catch(() => {}); };

  const allNames = useMemo(() => tree.flatMap((g) => g.parties.map((p) => p.name)), [tree]);
  const sel = selected === null ? new Set(allNames) : new Set(selected);
  const isAll = selected === null || (allNames.length > 0 && sel.size >= allNames.length);

  const emit = (next: Set<string>) => {
    onChange(allNames.length && next.size >= allNames.length ? null : Array.from(next));
  };
  const toggleParty = (name: string) => {
    const next = new Set(selected === null ? allNames : selected);
    next.has(name) ? next.delete(name) : next.add(name);
    emit(next);
  };
  const toggleGroup = (g: TreeGroup) => {
    const next = new Set(selected === null ? allNames : selected);
    const allOn = g.parties.every((p) => next.has(p.name));
    g.parties.forEach((p) => allOn ? next.delete(p.name) : next.add(p.name));
    emit(next);
  };

  const filtered = useMemo(() => {
    const qq = q.trim().toLowerCase();
    if (!qq) return tree;
    return tree.map((g) => ({ ...g, parties: g.parties.filter((p) => (p.label || p.name).toLowerCase().includes(qq)) }))
      .filter((g) => g.parties.length);
  }, [tree, q]);

  const label = isAll ? t('All parties') : `${sel.size} ${t('selected')}`;

  return (
    <span className="navgrp" onClick={(e) => e.stopPropagation()}>
      <button className="vat-ghost" onClick={() => { setOpen((o) => !o); load(); }} aria-expanded={open}>
        {side === 'Customer' ? t('Customers') : t('Suppliers')}: {label} ▾
      </button>
      {open && (
        <div className="navgrp-menu ptree" role="menu">
          <div style={{ display: 'flex', gap: 6, padding: '6px 10px' }}>
            <input placeholder={t('Search…')} value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1 }} />
            <button className="studio-ghost" onClick={() => onChange(null)}>{t('All')}</button>
            <button className="studio-ghost" onClick={() => onChange([])}>{t('None')}</button>
          </div>
          <div className="ptree-body">
            {filtered.map((g) => {
              const onCount = g.parties.filter((p) => sel.has(p.name)).length;
              const isCollapsed = collapsed.has(g.group);
              return (
                <div key={g.group}>
                  <div className="ptree-grp">
                    <button className="ptree-caret" onClick={() => setCollapsed((c) => { const n = new Set(c); n.has(g.group) ? n.delete(g.group) : n.add(g.group); return n; })}>
                      {isCollapsed ? '▸' : '▾'}
                    </button>
                    <label style={{ display: 'flex', gap: 6, alignItems: 'center', flex: 1, cursor: 'pointer' }}>
                      <input type="checkbox" checked={onCount === g.parties.length && g.parties.length > 0}
                        ref={(el) => { if (el) el.indeterminate = onCount > 0 && onCount < g.parties.length; }}
                        onChange={() => toggleGroup(g)} />
                      <b>{g.group}</b> <span className="cf-count">{onCount}/{g.parties.length}</span>
                    </label>
                  </div>
                  {!isCollapsed && g.parties.map((p) => (
                    <label key={p.name} className="ptree-item">
                      <input type="checkbox" checked={sel.has(p.name)} onChange={() => togglePartySafe(p.name)} />
                      {p.label || p.name}
                    </label>
                  ))}
                </div>
              );
            })}
            {tree.length === 0 && <div className="studio-hint" style={{ padding: 10 }}>{t('Loading…')}</div>}
          </div>
          <div style={{ padding: '6px 10px' }}>
            <button className="studio-run" onClick={() => setOpen(false)}>{t('Done')}</button>
          </div>
        </div>
      )}
    </span>
  );

  function togglePartySafe(name: string) { toggleParty(name); }
}
