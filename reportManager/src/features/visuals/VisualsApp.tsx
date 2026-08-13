import { useEffect, useMemo, useRef, useState } from 'react';
import { t } from '../../utils/i18n';
import Chart from 'chart.js/auto';
import type { RunSnapshot, Tile, ChartType, SavedDashboard } from '../../types';
import { aggregate, buildPeriodGroups, fmt0, fmtPct, fmtPctGrowth, fmtShort } from '../../utils/format';
import { exportDashboardPdf } from '../../utils/export';
import { api } from '../../utils/api';
import { LetterheadPickerModal, type LetterheadChoice } from '../LetterheadPickerModal';
import { fetchLetterhead } from '../../utils/letterhead';

interface Props {
  snapshots: RunSnapshot[];
  setSnapshots: (s: RunSnapshot[]) => void;
  reportName?: string;  // current report name — required to save a dashboard
}

const PALETTES: Record<string, string[]> = {
  brand: ['#185FA5', '#0F6E56', '#854F0B', '#3C3489', '#993C1D', '#A32D2D'],
  cool: ['#185FA5', '#378ADD', '#85B7EB', '#0F6E56', '#5DCAA5', '#3C3489'],
  warm: ['#854F0B', '#BA7517', '#EF9F27', '#993C1D', '#D85A30', '#A32D2D'],
  mono: ['#042C53', '#0C447C', '#185FA5', '#378ADD', '#85B7EB', '#B5D4F4'],
};

export function VisualsApp({ snapshots, setSnapshots, reportName }: Props) {
  const [activeRunId, setActiveRunId] = useState<string>('');
  const [tiles, setTiles] = useState<Tile[]>([]);
  const [building, setBuilding] = useState(false);

  // Saved-dashboards state (v1.7).
  const [savedDashboards, setSavedDashboards] = useState<SavedDashboard[]>([]);
  const [activeDashboardName, setActiveDashboardName] = useState<string>('');
  const [activeDashboardLabel, setActiveDashboardLabel] = useState<string>('');
  const [activeDashboardShared, setActiveDashboardShared] = useState<boolean>(false);
  const [isDirty, setIsDirty] = useState(false);
  const [saveDlgOpen, setSaveDlgOpen] = useState(false);
  const [saveAsMode, setSaveAsMode] = useState<'save' | 'save_as'>('save_as');
  const [statusMsg, setStatusMsg] = useState('');

  // v1.9.53 — Letter Head picker for PDF export.
  const [lhPickerOpen, setLhPickerOpen] = useState(false);
  async function completeExport(choice: LetterheadChoice) {
    setLhPickerOpen(false);
    if (!activeRun) return;
    const lh = choice.withoutLetterhead
      ? undefined
      : await fetchLetterhead(choice.name, undefined);
    exportDashboardPdf('Neotec Insight — Dashboard', activeRun.name, '#canvas-grid .tile canvas', undefined, lh);
  }

  // Initial dashboard list load — scoped to the current report when known.
  useEffect(() => {
    (async () => {
      try {
        const list = (await api.listDashboards(reportName)) as SavedDashboard[];
        setSavedDashboards(list);
      } catch {
        setSavedDashboards([]);
      }
    })();
  }, [reportName]);

  useEffect(() => {
    if (snapshots.length > 0 && !activeRunId) setActiveRunId(snapshots[snapshots.length - 1].id);
  }, [snapshots, activeRunId]);

  const activeRun = snapshots.find((s) => s.id === activeRunId);

  function markDirty() { setIsDirty(true); }
  function deleteTile(id: string) { setTiles(tiles.filter((t) => t.id !== id)); markDirty(); }
  function duplicateTile(id: string) {
    const t = tiles.find((x) => x.id === id); if (!t) return;
    setTiles([...tiles, { ...t, id: 'tile_' + Date.now().toString(36), title: t.title + ' (copy)' }]);
    markDirty();
  }

  async function loadDashboard(name: string) {
    if (!name) {
      setActiveDashboardName('');
      setActiveDashboardLabel('');
      setActiveDashboardShared(false);
      setTiles([]);
      setIsDirty(false);
      return;
    }
    if (isDirty && !confirm('You have unsaved changes. Load anyway?')) return;
    try {
      const d = await api.getDashboard(name);
      setActiveDashboardName(d.name);
      setActiveDashboardLabel(d.label);
      setActiveDashboardShared(!!d.is_shared);
      setTiles(d.tiles || []);
      setIsDirty(false);
      setStatusMsg(`Loaded "${d.label}".`);
    } catch (e: any) {
      setStatusMsg('Load failed: ' + (e?.message || 'unknown'));
    }
  }

  async function persistSave(label: string, isShared: boolean, updateExisting: boolean) {
    if (!reportName) { setStatusMsg('No report selected — pick a report on the Run tab first.'); return; }
    try {
      const payload: any = {
        name: updateExisting ? activeDashboardName : undefined,
        label,
        report: reportName,
        tiles,
        filters: activeRun ? activeRun.run.filters : {},
        is_shared: isShared ? 1 : 0,
      };
      const res = await api.saveDashboard(payload);
      setActiveDashboardName(res.name);
      setActiveDashboardLabel(res.label);
      setActiveDashboardShared(!!res.is_shared);
      setIsDirty(false);
      setStatusMsg(`Saved as "${res.label}".`);
      // Reload the list so the new/updated dashboard appears.
      try {
        const list = (await api.listDashboards(reportName)) as SavedDashboard[];
        setSavedDashboards(list);
      } catch {}
    } catch (e: any) {
      setStatusMsg('Save failed: ' + (e?.message || 'unknown'));
    }
  }

  async function deleteCurrentDashboard() {
    if (!activeDashboardName) return;
    if (!confirm(`Delete dashboard "${activeDashboardLabel}"? This cannot be undone.`)) return;
    try {
      await api.deleteDashboard(activeDashboardName);
      setStatusMsg(`Deleted "${activeDashboardLabel}".`);
      setActiveDashboardName('');
      setActiveDashboardLabel('');
      setActiveDashboardShared(false);
      setTiles([]);
      setIsDirty(false);
      try {
        const list = (await api.listDashboards(reportName)) as SavedDashboard[];
        setSavedDashboards(list);
      } catch {}
    } catch (e: any) {
      setStatusMsg('Delete failed: ' + (e?.message || 'unknown'));
    }
  }

  return (
    <div>
      <div className="visuals-toolbar">
        <div>
          <div className="strong">{t('Visuals workspace')}</div>
          <div className="muted">
            {activeDashboardLabel
              ? <>Editing <strong>{activeDashboardLabel}</strong>{activeDashboardShared ? ' · shared' : ' · private'}{isDirty ? ' · unsaved changes' : ''}</>
              : 'Untitled dashboard — save when you\'re happy with the layout.'}
          </div>
        </div>
        <div className="visuals-controls">
          <label style={{ minWidth: 200 }}><span className="flbl">{t('Saved dashboards')}</span>
            <select value={activeDashboardName} onChange={(e) => loadDashboard(e.target.value)}>
              <option value="">— New / unsaved —</option>
              {savedDashboards.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.label}{d.is_shared ? ' · shared' : ''}{!d.is_mine ? ` · by ${d.owner_user}` : ''}
                </option>
              ))}
            </select>
          </label>
          <label><span className="flbl">{t('Source run')}</span>
            <select value={activeRunId} onChange={(e) => { setActiveRunId(e.target.value); if (!activeDashboardName) setTiles([]); }}>
              {snapshots.length === 0
                ? <option>No runs yet — click Visualize on a report run</option>
                : snapshots.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <button onClick={() => setBuilding(true)} disabled={snapshots.length === 0}><i className="ti ti-plus" aria-hidden /> Add tile</button>
          <button
            onClick={() => { setSaveAsMode('save'); setSaveDlgOpen(true); }}
            disabled={!activeDashboardName || tiles.length === 0}
            title={activeDashboardName ? 'Save changes to the loaded dashboard' : 'Use Save as to give the dashboard a name first'}
          ><i className="ti ti-device-floppy" aria-hidden /> Save</button>
          <button
            onClick={() => { setSaveAsMode('save_as'); setSaveDlgOpen(true); }}
            disabled={tiles.length === 0}
          ><i className="ti ti-bookmark" aria-hidden /> Save as…</button>
          <button
            onClick={deleteCurrentDashboard}
            disabled={!activeDashboardName}
            title="Delete the loaded dashboard"
          ><i className="ti ti-trash" aria-hidden /> Delete</button>
          <button onClick={() => activeRun && setLhPickerOpen(true)}><i className="ti ti-file-text" aria-hidden /> PDF</button>
          <button onClick={() => window.print()}><i className="ti ti-printer" aria-hidden /> Print</button>
        </div>
      </div>

      {statusMsg && <div className="muted" style={{ padding: '6px 4px', fontSize: 11 }}>{statusMsg}</div>}

      {building && activeRun && (
        <TileBuilder
          run={activeRun}
          onCancel={() => setBuilding(false)}
          onCreate={(t) => { setTiles([...tiles, t]); setBuilding(false); markDirty(); }}
        />
      )}

      {tiles.length === 0 ? (
        <div className="canvas-empty">
          <i className="ti ti-chart-pie" aria-hidden style={{ fontSize: 22, display: 'block', marginBottom: 8 }} />
          {snapshots.length === 0
            ? 'Run a report and click Visualize to get started.'
            : 'Click "Add tile" to start building a dashboard.'}
        </div>
      ) : (
        <div id="canvas-grid" className="canvas-grid">
          {tiles.map((t) => <TileView key={t.id} tile={t} run={activeRun!} onDelete={() => deleteTile(t.id)} onDuplicate={() => duplicateTile(t.id)} />)}
        </div>
      )}

      {saveDlgOpen && (
        <SaveDashboardDialog
          mode={saveAsMode}
          currentLabel={activeDashboardLabel}
          currentShared={activeDashboardShared}
          onCancel={() => setSaveDlgOpen(false)}
          onSave={async (label, isShared) => {
            await persistSave(label, isShared, saveAsMode === 'save');
            setSaveDlgOpen(false);
          }}
        />
      )}

      {/* v1.9.53 — Letter Head picker for PDF export. */}
      <LetterheadPickerModal
        open={lhPickerOpen}
        company={undefined}
        actionLabel="Export Dashboard to PDF"
        onConfirm={completeExport}
        onCancel={() => setLhPickerOpen(false)}
      />
    </div>
  );
}

function SaveDashboardDialog({
  mode, currentLabel, currentShared, onCancel, onSave,
}: {
  mode: 'save' | 'save_as';
  currentLabel: string;
  currentShared: boolean;
  onCancel: () => void;
  onSave: (label: string, isShared: boolean) => void | Promise<void>;
}) {
  const [label, setLabel] = useState(mode === 'save_as' && currentLabel ? `${currentLabel} (copy)` : currentLabel);
  const [isShared, setIsShared] = useState(currentShared);
  const [busy, setBusy] = useState(false);
  return (
    <div className="modal-overlay">
      <div className="modal" style={{ maxWidth: 460 }}>
        <div className="modal-head">
          <div className="strong">{mode === 'save' ? 'Save dashboard' : 'Save as new dashboard'}</div>
          <button onClick={onCancel} aria-label="Close"><i className="ti ti-x" aria-hidden /></button>
        </div>
        <div className="modal-body">
          <label><span className="flbl">{t('Dashboard name')}</span>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              autoFocus
              placeholder="e.g. December review"
            />
          </label>
          <label style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 12, cursor: 'pointer', fontSize: 12 }}>
            <input
              type="checkbox"
              checked={isShared}
              onChange={(e) => setIsShared(e.target.checked)}
              style={{ width: 'auto', height: 'auto' }}
            />
            Share with all users (otherwise only you see this dashboard).
          </label>
        </div>
        <div className="modal-foot">
          <button onClick={onCancel}>{t('Cancel')}</button>
          <button
            className="primary-btn"
            disabled={busy || !label.trim()}
            onClick={async () => {
              setBusy(true);
              try { await onSave(label.trim(), isShared); }
              finally { setBusy(false); }
            }}
          ><i className="ti ti-check" aria-hidden /> {busy ? 'Saving…' : (mode === 'save' ? 'Save' : 'Create')}</button>
        </div>
      </div>
    </div>
  );
}

function TileBuilder({ run, onCancel, onCreate }: { run: RunSnapshot; onCancel: () => void; onCreate: (t: Tile) => void }) {
  const [title, setTitle] = useState('New chart');
  const [type, setType] = useState<ChartType>('bar');
  const [rowKeys, setRowKeys] = useState<string[]>([]);
  const [series, setSeries] = useState<Tile['series']>('actual');
  const [palette, setPalette] = useState<Tile['palette']>('brand');

  const candidates = run.rowDefs.filter((r) => r.kind !== 'section');

  function create() {
    if (rowKeys.length === 0) { alert('Pick at least one row.'); return; }
    onCreate({ id: 'tile_' + Date.now().toString(36), runId: run.id, title, type, rowKeys, series, palette });
  }

  return (
    <div className="card tile-builder">
      <div className="tile-builder-head">
        <div className="strong">{t('Configure new tile')}</div>
        <button onClick={onCancel}>{t('Cancel')}</button>
      </div>
      <div className="form-grid-5">
        <label><span className="flbl">{t('Title')}</span><input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <label><span className="flbl">{t('Chart type')}</span>
          <select value={type} onChange={(e) => setType(e.target.value as ChartType)}>
            <option value="bar">{t('Bar')}</option>
            <option value="line">{t('Line')}</option>
            <option value="area">{t('Area')}</option>
            <option value="stacked">{t('Stacked bar')}</option>
            <option value="grouped">{t('Grouped bar')}</option>
            <option value="hbar">{t('Horizontal bar')}</option>
            <option value="pie">{t('Pie')}</option>
            <option value="donut">{t('Donut')}</option>
            <option value="kpi">{t('KPI tile')}</option>
            <option value="table">{t('Mini table')}</option>
          </select>
        </label>
        <label><span className="flbl">{t('Rows')}</span>
          <select multiple value={rowKeys} onChange={(e) => setRowKeys(Array.from(e.target.selectedOptions).map((o) => o.value))} style={{ height: 84 }}>
            {candidates.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
        </label>
        <label><span className="flbl">{t('Series')}</span>
          <select value={series} onChange={(e) => setSeries(e.target.value as Tile['series'])}>
            <option value="actual">{t('Actual only')}</option>
            <option value="actual_budget">Actual vs Budget</option>
            <option value="actual_prior">Actual vs prior year(s)</option>
            <option value="actual_budget_prior">Actual + Budget + Prior</option>
          </select>
        </label>
        <label><span className="flbl">{t('Palette')}</span>
          <select value={palette} onChange={(e) => setPalette(e.target.value as Tile['palette'])}>
            <option value="brand">{t('Neotec brand')}</option>
            <option value="cool">{t('Cool')}</option>
            <option value="warm">{t('Warm')}</option>
            <option value="mono">{t('Mono blue')}</option>
          </select>
        </label>
      </div>
      <div style={{ marginTop: 10 }}><button onClick={create} className="primary-btn"><i className="ti ti-check" aria-hidden /> Add to canvas</button></div>
    </div>
  );
}

function TileView({ tile, run, onDelete, onDuplicate }: { tile: Tile; run: RunSnapshot; onDelete: () => void; onDuplicate: () => void }) {
  const cls = tile.type === 'table' ? 'tile full' : 'tile';
  return (
    <div className={cls}>
      <div className="tile-head">
        <div>
          <div className="tile-title">{tile.title}</div>
          <div className="tile-sub">{tile.type} · {tile.rowKeys.length} row{tile.rowKeys.length === 1 ? '' : 's'}</div>
        </div>
        <div className="tile-actions">
          <button onClick={onDuplicate} aria-label="Duplicate"><i className="ti ti-copy" aria-hidden /></button>
          <button onClick={onDelete} aria-label="Delete"><i className="ti ti-trash" aria-hidden /></button>
        </div>
      </div>
      <div className="tile-body">
        {tile.type === 'kpi' && <KpiTile tile={tile} run={run} />}
        {tile.type === 'table' && <MiniTable tile={tile} run={run} />}
        {tile.type !== 'kpi' && tile.type !== 'table' && <ChartTile tile={tile} run={run} />}
      </div>
    </div>
  );
}

function KpiTile({ tile, run }: { tile: Tile; run: RunSnapshot }) {
  const rk = tile.rowKeys[0]; if (!rk) return null;
  const row = run.run.current.rows.find((r) => r.key === rk); if (!row) return null;
  const monthsAll: number[] = [];
  for (let m = run.run.filters.month_from; m <= run.run.filters.month_to; m++) monthsAll.push(m);
  const val = aggregate(row.monthly, monthsAll);
  const bud = run.run.budget?.rows.find((r) => r.key === rk);
  const py = run.run.priors[0]?.rows.find((r) => r.key === rk);
  const budVal = bud ? aggregate(bud.monthly, monthsAll) : null;
  const pyVal = py ? aggregate(py.monthly, monthsAll) : null;
  return (
    <div className="kpi-tile">
      <div className="l">{row.label}</div>
      <div className="v">{fmt0(val)}</div>
      <div className="d">
        {budVal != null && budVal !== 0 && <span className={'delta ' + (val / budVal >= 1 ? 'up' : 'down')}>{fmtPct(val / budVal)} of budget</span>}
        {pyVal != null && pyVal !== 0 && <span className={'delta ' + ((val - pyVal) >= 0 ? 'up' : 'down')} style={{ marginLeft: 6 }}>{fmtPctGrowth((val - pyVal) / Math.abs(pyVal))} YoY</span>}
      </div>
    </div>
  );
}

function MiniTable({ tile, run }: { tile: Tile; run: RunSnapshot }) {
  const { groups } = buildPeriodGroups(run.run.filters.month_from, run.run.filters.month_to, 'month');
  const periods = groups[0]?.periods || [];
  const monthsAll = periods.flatMap((p) => p.months);
  return (
    <table className="mini-table">
      <thead><tr><th>{t('Row')}</th>{periods.map((p) => <th key={p.key}>{p.label}</th>)}<th>{t('YTD')}</th></tr></thead>
      <tbody>
        {tile.rowKeys.map((rk) => {
          const row = run.run.current.rows.find((r) => r.key === rk); if (!row) return null;
          return (
            <tr key={rk}>
              <td>{row.label}</td>
              {periods.map((p) => <td key={p.key}>{fmt0(aggregate(row.monthly, p.months))}</td>)}
              <td className="strong">{fmt0(aggregate(row.monthly, monthsAll))}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function ChartTile({ tile, run }: { tile: Tile; run: RunSnapshot }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const chartRef = useRef<Chart | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    if (chartRef.current) chartRef.current.destroy();
    const { groups } = buildPeriodGroups(run.run.filters.month_from, run.run.filters.month_to, 'month');
    const periods = groups[0]?.periods || [];
    const labels = periods.map((p) => p.label);
    const palette = PALETTES[tile.palette] || PALETTES.brand;
    const datasets: any[] = [];
    let colorIdx = 0;
    const sources: string[] =
      tile.series === 'actual' ? ['cur'] :
      tile.series === 'actual_budget' ? ['cur', 'budget'] :
      tile.series === 'actual_prior' ? ['cur', ...run.run.priors.map((_, i) => 'py' + (i + 1))] :
      ['cur', 'budget', ...run.run.priors.map((_, i) => 'py' + (i + 1))];
    for (const rk of tile.rowKeys) {
      const cur = run.run.current.rows.find((r) => r.key === rk); if (!cur) continue;
      for (const src of sources) {
        let monthly: Record<number, number> | undefined; let lbl = cur.label;
        if (src === 'cur') monthly = cur.monthly;
        else if (src === 'budget') { const b = run.run.budget?.rows.find((r) => r.key === rk); if (!b) continue; monthly = b.monthly; lbl += ' (Budget)'; }
        else if (src.startsWith('py')) { const i = parseInt(src.slice(2)) - 1; const py = run.run.priors[i]?.rows.find((r) => r.key === rk); if (!py) continue; monthly = py.monthly; lbl += ` (FY${run.run.priors[i].fiscal_year})`; }
        if (!monthly) continue;
        const data = periods.map((p) => aggregate(monthly!, p.months));
        const color = palette[colorIdx % palette.length]; colorIdx++;
        const ds: any = { label: lbl, data, backgroundColor: color, borderColor: color };
        if (tile.type === 'line' || tile.type === 'area') {
          ds.tension = 0.25; ds.fill = tile.type === 'area';
          ds.backgroundColor = tile.type === 'area' ? color + '33' : color; ds.borderWidth = 2;
          if (src === 'budget') ds.borderDash = [4, 4];
        }
        if (tile.type === 'pie' || tile.type === 'donut') ds.backgroundColor = data.map((_, i) => palette[i % palette.length]);
        datasets.push(ds);
      }
    }
    const chartType =
      tile.type === 'area' ? 'line' :
      tile.type === 'stacked' || tile.type === 'grouped' ? 'bar' :
      tile.type === 'hbar' ? 'bar' :
      tile.type === 'donut' ? 'doughnut' : (tile.type as any);

    chartRef.current = new Chart(ref.current, {
      type: chartType,
      data: { labels, datasets: tile.type === 'pie' || tile.type === 'donut' ? datasets.slice(0, 1) : datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        indexAxis: tile.type === 'hbar' ? 'y' : 'x',
        plugins: { legend: { display: true, labels: { font: { size: 10 }, boxWidth: 10, padding: 8 }, position: 'bottom' } },
        scales: tile.type === 'pie' || tile.type === 'donut' ? {} : {
          x: { stacked: tile.type === 'stacked', ticks: { font: { size: 9 }, autoSkip: false, maxRotation: 45 }, grid: { display: false } },
          y: { stacked: tile.type === 'stacked', ticks: { font: { size: 9 }, callback: (v: any) => fmtShort(v) }, grid: { color: 'rgba(0,0,0,0.05)' } },
        },
      },
    });
    return () => { chartRef.current?.destroy(); };
  }, [tile, run]);
  return <canvas ref={ref} role="img" aria-label={tile.title} />;
}
