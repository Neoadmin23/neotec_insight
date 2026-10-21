import { useCallback, useEffect, useRef, useState } from 'react';
import Chart from 'chart.js/auto';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { AiDesign, type BoardDraft } from './AiDesign';
import {
  type CrossFilter, type Dataset, type Visual, type VisualType, applyCrossFilter, chartData, kpiValue, labelCol,
  nextY, numCols, pivot, placeVisual,
} from '../../utils/boardCore';

/* Dashboard designer (v2.99.0). A 12-column grid of visuals over Analysis Pack
 * analyses and Studio reports, with page filters and click-to-filter.
 * Layout and data logic live in utils/boardCore.ts (tested). */

const TYPES: VisualType[] = ['kpi', 'bar', 'line', 'area', 'pie', 'waterfall', 'table', 'pivot'];
const ROW_H = 80;
const PAL = ['#2563eb', '#16a34a', '#d97706', '#dc2626', '#7c3aed', '#0891b2', '#db2777', '#65a30d'];
type Filters = { company: string; from_date: string; to_date: string; cost_center: string };
type Slot = { ds?: Dataset; err?: string; loading?: boolean };
const EMPTY_F: Filters = { company: '', from_date: '', to_date: '', cost_center: '' };

const num = (v: any, f?: string) => {
  const n = Number(v);
  if (v === null || v === undefined || v === '' || !Number.isFinite(n)) return v ?? '—';
  return f === 'percent' ? `${n.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`
    : n.toLocaleString(undefined, { maximumFractionDigits: f === 'days' || f === 'number' ? 1 : 0 });
};

function ChartView({ v, ds, onPick }: { v: Visual; ds: Dataset; onPick: (label: string, key: string) => void }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const cd = chartData(ds, v.type);
    const kind = v.type === 'area' ? 'line' : v.type === 'waterfall' ? 'bar' : v.type;
    const pie = v.type === 'pie';
    const chart = new Chart(ref.current, {
      type: kind as any,
      data: { labels: cd.labels, datasets: cd.series.map((s: any, i: number) => ({
        label: s.name, data: s.data, fill: v.type === 'area', tension: 0.25,
        backgroundColor: pie ? cd.labels.map((_l, j) => PAL[j % PAL.length])
          : cd.waterfall ? s.data.map((_x: any, j: number) => (s.kinds?.[j] === 'total' ? PAL[0] : PAL[3]))
          : PAL[i % PAL.length] + (v.type === 'area' ? '55' : ''),
        borderColor: PAL[i % PAL.length], borderSkipped: false,
      })) },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: pie || cd.series.length > 1 } },
        onClick: (_e: any, els: any[]) => { if (els?.length && cd.labelKey) onPick(cd.labels[els[0].index], cd.labelKey); },
      },
    });
    return () => chart.destroy();
  }, [v.type, ds, onPick]);
  return <div className="bd-chart"><canvas ref={ref} /></div>;
}

function VisualBody({ v, ds, onPick, onOptions, editing }: {
  v: Visual; ds: Dataset; onPick: (label: string, key: string) => void;
  onOptions: (o: Visual['options']) => void; editing: boolean;
}) {
  if (!ds.rows.length && !ds.figures?.length) return <div className="bd-empty">{t('No data for these filters.')}</div>;
  if (v.type === 'kpi') {
    const k = kpiValue(ds);
    return <div className="bd-kpi"><span>{k.label}</span><b>{num(k.value, k.format)}</b></div>;
  }
  if (v.type === 'table') {
    const lc = labelCol(ds);
    return (
      <div className="bd-scroll"><table className="cp-table">
        <thead><tr>{ds.columns.map((c) => <th key={c.key} className={c.type === 'text' ? '' : 'num'}>{c.label}</th>)}</tr></thead>
        <tbody>{ds.rows.slice(0, 500).map((r, i) => (
          <tr key={i}>{ds.columns.map((c) => (
            <td key={c.key} className={c.type === 'text' ? '' : 'num'}>
              {c.key === lc?.key ? <button type="button" className="cp-link" onClick={() => onPick(String(r[c.key]), c.key)}>{String(r[c.key] ?? '—')}</button>
                : c.type === 'text' ? String(r[c.key] ?? '—') : num(r[c.key], c.type)}
            </td>))}</tr>
        ))}</tbody>
      </table></div>
    );
  }
  if (v.type === 'pivot') {
    const o = v.options || {};
    const p = pivot(ds, o.row, o.column, o.value);
    const textCols = ds.columns.filter((c) => !numCols(ds).includes(c));
    return (
      <div className="bd-scroll">
        {editing && (
          <div className="bd-pivot-opts">
            <select value={o.row || ''} onChange={(e) => onOptions({ ...o, row: e.target.value })}>
              <option value="">{t('Rows')}</option>{textCols.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select>
            <select value={o.column || ''} onChange={(e) => onOptions({ ...o, column: e.target.value })}>
              <option value="">{t('Columns: none')}</option>{textCols.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select>
            <select value={o.value || ''} onChange={(e) => onOptions({ ...o, value: e.target.value })}>
              <option value="">{t('Values')}</option>{numCols(ds).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select>
          </div>
        )}
        <table className="cp-table">
          <thead><tr><th />{p.colKeys.map((c) => <th key={c} className="num">{c}</th>)}<th className="num">{t('Total')}</th></tr></thead>
          <tbody>
            {p.rowKeys.map((r) => (
              <tr key={r}><td><button type="button" className="cp-link" onClick={() => onPick(r, o.row || labelCol(ds)?.key || '')}>{r}</button></td>
                {p.colKeys.map((c) => <td key={c} className="num">{num(p.cells[`${r}\u0000${c}`] || 0)}</td>)}
                <td className="num"><b>{num(p.rowTotals[r])}</b></td></tr>
            ))}
            <tr><td><b>{t('Total')}</b></td>{p.colKeys.map((c) => <td key={c} className="num"><b>{num(p.colTotals[c])}</b></td>)}
              <td className="num"><b>{num(p.total)}</b></td></tr>
          </tbody>
        </table>
      </div>
    );
  }
  return <ChartView v={v} ds={ds} onPick={onPick} />;
}

export default function BoardsApp() {
  const [boards, setBoards] = useState<any[]>([]);
  const [name, setName] = useState('');
  const [title, setTitle] = useState(t('New dashboard'));
  const [layout, setLayout] = useState<Visual[]>([]);
  const [shared, setShared] = useState('');
  const [canEdit, setCanEdit] = useState(true);
  const [editing, setEditing] = useState(true);
  const [filters, setFilters] = useState<Filters>(EMPTY_F);
  const [applied, setApplied] = useState<Filters>(EMPTY_F);
  const [data, setData] = useState<Record<string, Slot>>({});
  const [xf, setXf] = useState<CrossFilter | null>(null);
  const [companies, setCompanies] = useState<any[]>([]);
  const [sources, setSources] = useState<{ analyses: any[]; studio: any[] }>({ analyses: [], studio: [] });
  const [adding, setAdding] = useState(false);
  const [designing, setDesigning] = useState(false);
  const [notes, setNotes] = useState<string[]>([]);
  const [msg, setMsg] = useState('');
  const gridRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(1200);
  const drag = useRef<{ id: string; mode: 'move' | 'size'; sx: number; sy: number; v: Visual } | null>(null);
  const [ghost, setGhost] = useState<Visual | null>(null);

  useEffect(() => {
    api.boardsList().then(setBoards).catch(() => {});
    api.boardSources().then(setSources).catch(() => {});
    api.listCompanies().then((c: any) => setCompanies(Array.isArray(c) ? c : [])).catch(() => {});
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width));
    if (gridRef.current) ro.observe(gridRef.current);
    return () => ro.disconnect();
  }, []);

  const load = useCallback((vs: Visual[], f: Filters) => {
    vs.forEach((v) => {
      setData((d) => ({ ...d, [v.id]: { ...d[v.id], loading: true, err: undefined } }));
      api.boardRunVisual(v.source, f)
        .then((ds) => setData((d) => ({ ...d, [v.id]: { ds } })))
        .catch((e: any) => setData((d) => ({ ...d, [v.id]: { err: String(e?.message || e) } })));
    });
  }, []);

  async function openBoard(n: string) {
    if (!n) { setName(''); setTitle(t('New dashboard')); setLayout([]); setShared(''); setCanEdit(true); setEditing(true); setData({}); return; }
    const b = await api.boardGet(n);
    const f = { ...EMPTY_F, ...(b.filters || {}) };
    setName(b.name); setTitle(b.title); setLayout(b.layout || []); setShared(b.shared_roles || '');
    setCanEdit(!!b.can_edit); setEditing(false); setFilters(f); setApplied(f); setXf(null); setData({});
    load(b.layout || [], f);
  }

  async function save() {
    const r = await api.boardSave({ name, title, layout, filters: applied, shared_roles: shared });
    setName(r.name); setMsg(t('Saved')); setTimeout(() => setMsg(''), 2000);
    setBoards(await api.boardsList());
  }

  function addVisual(source: any, type: VisualType, vtitle: string) {
    const v: Visual = { id: Math.random().toString(36).slice(2, 10), title: vtitle, type, source,
      x: 0, y: nextY(layout), w: type === 'kpi' ? 3 : 6, h: type === 'kpi' ? 2 : 4, options: {} };
    setLayout((l) => [...l, v]);
    load([v], applied);
    setAdding(false);
  }

  const cw = width / 12;
  const onPointerDown = (e: React.PointerEvent, v: Visual, mode: 'move' | 'size') => {
    if (!editing) return;
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    drag.current = { id: v.id, mode, sx: e.clientX, sy: e.clientY, v };
    setGhost(v);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = Math.round((e.clientX - d.sx) / cw), dy = Math.round((e.clientY - d.sy) / ROW_H);
    setGhost(d.mode === 'move' ? { ...d.v, x: d.v.x + dx, y: d.v.y + dy } : { ...d.v, w: d.v.w + dx, h: d.v.h + dy });
  };
  const onPointerUp = () => {
    if (drag.current && ghost) setLayout((l) => placeVisual(l, ghost));
    drag.current = null; setGhost(null);
  };
  const pick = useCallback((id: string) => (label: string, key: string) =>
    setXf((cur) => (cur && cur.from === id && cur.value === label ? null : { key, value: label, from: id })), []);

  const shown = layout.map((v) => (ghost && ghost.id === v.id ? { ...ghost } : v));
  const height = Math.max(6, nextY(shown) + (editing ? 2 : 0)) * ROW_H;

  return (
    <div className="bd">
      <div className="bd-bar">
        <select value={name} onChange={(e) => openBoard(e.target.value)}>
          <option value="">{t('＋ New dashboard')}</option>
          {boards.map((b) => <option key={b.name} value={b.name}>{b.title}{b.mine ? '' : ` · ${b.owner}`}</option>)}
        </select>
        {editing ? <input className="bd-title" value={title} onChange={(e) => setTitle(e.target.value)} /> : <h3>{title}</h3>}
        {canEdit && (
          <>
            <button type="button" className="bk-btn" onClick={() => setEditing((x) => !x)}>{editing ? t('View') : t('Edit')}</button>
            {editing && <button type="button" className="bk-btn" onClick={() => setAdding(true)}>{t('＋ Visual')}</button>}
            <button type="button" className="bk-btn" onClick={() => setDesigning(true)}>✨ {t('Design with AI')}</button>
            <button type="button" className="bk-btn bk-primary" onClick={save} disabled={!layout.length}>{t('Save')}</button>
            <button type="button" className="bk-btn" onClick={() => { const r = prompt(t('Share with roles (comma-separated)'), shared); if (r !== null) setShared(r); }}>{t('Share')}</button>
            {name && <button type="button" className="bk-btn" onClick={async () => {
              if (!confirm(t('Delete this dashboard?'))) return;
              await api.boardDelete(name); setBoards(await api.boardsList()); openBoard('');
            }}>{t('Delete')}</button>}
          </>
        )}
        {msg && <span className="alloc-ok">{msg}</span>}
      </div>

      {!!notes.length && (
        <div className="alloc-warn">{t('AI draft — review, adjust and Save.')} {notes.join(' ')}
          <button type="button" className="cp-link" onClick={() => setNotes([])}> {t('Dismiss')}</button></div>
      )}
      <div className="bd-filters">
        <select value={filters.company} onChange={(e) => setFilters({ ...filters, company: e.target.value })}>
          <option value="">{t('Default company')}</option>
          {companies.map((c: any) => <option key={c.name} value={c.name}>{c.company_name || c.name}</option>)}
        </select>
        <label>{t('From')} <input type="date" value={filters.from_date} onChange={(e) => setFilters({ ...filters, from_date: e.target.value })} /></label>
        <label>{t('To')} <input type="date" value={filters.to_date} onChange={(e) => setFilters({ ...filters, to_date: e.target.value })} /></label>
        <input placeholder={t('Cost centre')} value={filters.cost_center} onChange={(e) => setFilters({ ...filters, cost_center: e.target.value })} />
        <button type="button" className="bk-btn bk-primary" onClick={() => { setApplied(filters); setXf(null); load(layout, filters); }}>{t('Apply')}</button>
        {xf && <span className="bd-chip">{t('Filtered')}: {xf.value} <button type="button" onClick={() => setXf(null)}>×</button></span>}
      </div>

      <div ref={gridRef} className={'bd-grid' + (editing ? ' editing' : '')} style={{ height }}
        onPointerMove={onPointerMove} onPointerUp={onPointerUp}>
        {!layout.length && <div className="bd-empty bd-start">{t('Add a visual to start: ＋ Visual.')}</div>}
        {shown.map((v) => {
          const slot = data[v.id] || {};
          const ds = slot.ds ? applyCrossFilter(slot.ds, xf, v.id) : undefined;
          return (
            <div key={v.id} className={'bd-visual' + (ghost?.id === v.id ? ' dragging' : '')}
              style={{ left: v.x * cw, top: v.y * ROW_H, width: v.w * cw - 10, height: v.h * ROW_H - 10 }}>
              <div className="bd-vh" onPointerDown={(e) => onPointerDown(e, layout.find((x) => x.id === v.id)!, 'move')}>
                <span>{v.title || ds?.title || ''}</span>
                {editing && (
                  <span className="bd-vtools" onPointerDown={(e) => e.stopPropagation()}>
                    <select value={v.type} onChange={(e) => setLayout((l) => l.map((x) => (x.id === v.id ? { ...x, type: e.target.value as VisualType } : x)))}>
                      {TYPES.map((tp) => <option key={tp} value={tp}>{tp}</option>)}
                    </select>
                    <button type="button" onClick={() => setLayout((l) => l.filter((x) => x.id !== v.id))}>×</button>
                  </span>
                )}
              </div>
              <div className="bd-vb">
                {slot.loading && !slot.ds && <div className="bd-empty">{t('Loading…')}</div>}
                {slot.err && <div className="bd-empty bd-err">{slot.err}</div>}
                {ds && <VisualBody v={v} ds={ds} editing={editing} onPick={pick(v.id)}
                  onOptions={(o) => setLayout((l) => l.map((x) => (x.id === v.id ? { ...x, options: o } : x)))} />}
              </div>
              {editing && <div className="bd-resize" onPointerDown={(e) => onPointerDown(e, layout.find((x) => x.id === v.id)!, 'size')} />}
            </div>
          );
        })}
      </div>

      {adding && <AddVisual sources={sources} onAdd={addVisual} onClose={() => setAdding(false)} />}
      {designing && <AiDesign onClose={() => setDesigning(false)} onBoard={(d: BoardDraft) => {
        setName(''); setTitle(d.title); setLayout(d.layout); setShared(''); setCanEdit(true); setEditing(true);
        setData({}); setXf(null); setNotes(d.notes || []); load(d.layout, applied);
      }} />}
    </div>
  );
}

function AddVisual({ sources, onAdd, onClose }: {
  sources: { analyses: any[]; studio: any[] }; onAdd: (s: any, t: VisualType, title: string) => void; onClose: () => void;
}) {
  const [src, setSrc] = useState('');
  const [type, setType] = useState<VisualType>('bar');
  const pickTitle = () => {
    const [kind, id] = src.split('|');
    return kind === 'analysis' ? sources.analyses.find((a) => a.key === id)?.title : sources.studio.find((s) => s.name === id)?.title;
  };
  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel" role="dialog" style={{ width: 'min(520px, 100%)' }}>
        <div className="theme-h"><h3>{t('Add a visual')}</h3><button type="button" className="cp-x" onClick={onClose}>×</button></div>
        <div className="studio-rel-form">
          <select value={src} onChange={(e) => setSrc(e.target.value)}>
            <option value="">{t('Choose data…')}</option>
            <optgroup label={t('Analysis Pack')}>{sources.analyses.map((a) => <option key={a.key} value={`analysis|${a.key}`}>{a.title}</option>)}</optgroup>
            <optgroup label={t('Studio reports')}>{sources.studio.map((s) => <option key={s.name} value={`studio|${s.name}`}>{s.title}</option>)}</optgroup>
          </select>
          <select value={type} onChange={(e) => setType(e.target.value as VisualType)}>
            {TYPES.map((tp) => <option key={tp} value={tp}>{tp}</option>)}
          </select>
          <button type="button" className="bk-btn bk-primary" disabled={!src} onClick={() => {
            const [kind, id] = src.split('|');
            onAdd(kind === 'analysis' ? { kind, key: id } : { kind, report: id }, type, pickTitle() || '');
          }}>{t('Add')}</button>
        </div>
      </div>
    </div>
  );
}
