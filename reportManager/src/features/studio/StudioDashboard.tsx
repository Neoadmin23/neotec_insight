import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import StudioChart from './StudioChart';

interface TileData { slug: string; title: string; result: any; type: string; cfg: any; fullCfg: any; }
interface CrossFilter { field: string; value: string; label: string }

const ORDER_KEY = 'studio_dash_order';
const loadOrder = (): string[] => { try { return JSON.parse(localStorage.getItem(ORDER_KEY) || '[]'); } catch { return []; } };
const saveOrder = (o: string[]) => { try { localStorage.setItem(ORDER_KEY, JSON.stringify(o)); } catch { /* ignore */ } };

const TYPES = ['bar', 'hbar', 'stacked', 'line', 'area', 'pie', 'donut'];

export default function StudioDashboard({ onOpen }: { onOpen?: (slug: string) => void }) {
  const [tiles, setTiles] = useState<TileData[]>([]);
  const [loading, setLoading] = useState(true);
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  // v2.24.0 — live cross-filtering. Clicking a chart category broadcasts a
  // filter to EVERY tile; the backend silently drops the filter on doctypes
  // that lack the field, so heterogeneous tiles coexist safely. Dashboard
  // date range broadcasts posting_date the same way.
  const [xFilters, setXFilters] = useState<CrossFilter[]>([]);
  const [dashFrom, setDashFrom] = useState('');
  const [dashTo, setDashTo] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => { build(); }, []);

  async function rerunAll(nextX: CrossFilter[], from = dashFrom, to = dashTo) {
    setRefreshing(true);
    try {
      const extra: any[] = nextX.map((f) => ({ field: f.field, op: '=', value: f.value }));
      if (from && to) extra.push({ field: 'posting_date', op: 'between', value: `${from},${to}` });
      const updated = await Promise.all(tiles.map(async (tile) => {
        try {
          const cfg = { ...tile.fullCfg, filters: [ ...(tile.fullCfg.filters || []), ...extra ] };
          const res = await api.studioRunQuery(cfg);
          return { ...tile, result: res };
        } catch { return tile; }
      }));
      setTiles(updated);
    } finally { setRefreshing(false); }
  }

  function crossFilter(tile: TileData, label: string) {
    // The clicked category maps to the tile's chart category or group-by field.
    const field = tile.cfg?.category || tile.fullCfg?.group_by;
    if (!field) return;
    const next = [...xFilters.filter((f) => f.field !== field), { field, value: label, label: `${field} = ${label}` }];
    setXFilters(next);
    rerunAll(next);
  }

  function clearFilter(field?: string) {
    const next = field ? xFilters.filter((f) => f.field !== field) : [];
    setXFilters(next);
    rerunAll(next);
  }

  async function build() {
    setLoading(true);
    try {
      const reports = await api.studioListReports();
      const order = loadOrder();
      reports.sort((a: any, b: any) => {
        const ia = order.indexOf(a.slug), ib = order.indexOf(b.slug);
        return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
      });
      const out: TileData[] = [];
      for (const r of reports) {
        try {
          const full = await api.studioLoadReport(r.slug);
          const cfg = full.config || {};
          const chart = cfg._chart || { type: 'bar' };
          const res = await api.studioRunQuery(cfg);
          out.push({ slug: r.slug, title: full.title || r.slug, result: res, type: chart.type || 'bar', cfg: chart, fullCfg: cfg });
        } catch { /* skip a broken report */ }
      }
      setTiles(out);
    } finally { setLoading(false); }
  }

  function setType(slug: string, type: string) {
    setTiles((ts) => ts.map((t2) => t2.slug === slug ? { ...t2, type, cfg: { ...t2.cfg, type } } : t2));
  }
  function reorder(from: number, to: number) {
    if (from === to) return;
    setTiles((ts) => {
      const a = [...ts]; const [m] = a.splice(from, 1); a.splice(to, 0, m);
      saveOrder(a.map((x) => x.slug));
      return a;
    });
  }

  if (loading) return <div className="studio-empty"><div className="studio-empty-art">✦</div><h2>{t('Building your dashboard…')}</h2></div>;
  if (!tiles.length) return (
    <div className="studio-empty">
      <div className="studio-empty-art" aria-hidden>✦</div>
      <h2>{t('No saved reports yet')}</h2>
      <p>{t('Build a report, choose a chart, give it a title and Save — it will appear here as a dashboard tile.')}</p>
    </div>
  );

  return (
    <div className="studio-dash">
      <div className="studio-dash-head">
        <span>{t('Drag to arrange · click any bar or slice to cross-filter every tile.')}</span>
        <span className="dash-range">
          <input type="date" value={dashFrom} onChange={(e) => setDashFrom(e.target.value)} />
          <span>→</span>
          <input type="date" value={dashTo} onChange={(e) => setDashTo(e.target.value)} />
          <button className="studio-ghost" onClick={() => rerunAll(xFilters)} disabled={refreshing}>{refreshing ? t('Applying…') : t('Apply')}</button>
        </span>
        <button className="studio-ghost" onClick={build}>{t('Refresh')}</button>
      </div>
      {xFilters.length > 0 && (
        <div className="dash-xchips">
          {xFilters.map((f) => (
            <span key={f.field} className="studio-drillchip">
              <i className="ti ti-filter" aria-hidden /> {f.label}
              <button onClick={() => clearFilter(f.field)} aria-label={t('Clear')}>×</button>
            </span>
          ))}
          <button className="studio-ghost" style={{ padding: '2px 10px' }} onClick={() => clearFilter()}>{t('Clear all')}</button>
        </div>
      )}
      <div className="studio-dash-grid">
        {tiles.map((tile, i) => (
          <div key={tile.slug} draggable
            className={'studio-tile' + (drag === i ? ' drag' : '') + (over === i ? ' over' : '')}
            onDragStart={() => setDrag(i)}
            onDragOver={(e) => { e.preventDefault(); setOver(i); }}
            onDragEnd={() => { if (drag !== null && over !== null) reorder(drag, over); setDrag(null); setOver(null); }}
            onDrop={(e) => { e.preventDefault(); if (drag !== null) reorder(drag, i); setDrag(null); setOver(null); }}>
            <div className="studio-tile-h">
              <span className="grip">⠿</span>
              <span className="studio-tile-title">{tile.title}</span>
              <select value={tile.type} onChange={(e) => setType(tile.slug, e.target.value)}>
                {TYPES.map((ty) => <option key={ty} value={ty}>{t(ty)}</option>)}
              </select>
              {onOpen && <button className="studio-tile-open" onClick={() => onOpen(tile.slug)} title={t('Open in builder')}>↗</button>}
            </div>
            <StudioChart result={tile.result} cfg={{ ...tile.cfg, type: tile.type }} height={260}
              onPick={(lbl) => crossFilter(tile, lbl)} />
          </div>
        ))}
      </div>
    </div>
  );
}
