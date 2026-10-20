import { useEffect, useRef, useState } from 'react';
import Chart from 'chart.js/auto';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

/* Analysis Pack (v2.97.0) — the most useful analyses of the GL, P&L and
 * balance sheet. Each analysis returns the same dataset shape (figures,
 * columns, rows, chart, notes); this screen only displays it. */

type Col = { key: string; label: string; type: string };
type Dataset = {
  key: string; title: string; figures: { label: string; value: number | null; format: string }[];
  columns: Col[]; rows: any[]; notes: string[];
  chart: null | { type: string; labels: string[]; series: { name: string; data: any[]; kinds?: string[] }[];
    line?: { name: string; data: number[] } };
  from_date?: string; to_date?: string; compare_from?: string; compare_to?: string; company?: string;
};

const BLUE = '#2563eb', GREEN = '#16a34a', RED = '#dc2626', GREY = '#64748b', AMBER = '#d97706';

function fmt(v: any, f: string) {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  if (f === 'percent') return `${n.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  if (f === 'days') return `${n.toLocaleString(undefined, { maximumFractionDigits: 0 })} ${t('days')}`;
  if (f === 'ratio') return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (f === 'number') return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function DatasetChart({ ds }: { ds: Dataset }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const c = ds.chart;
    if (!ref.current || !c) return;
    let cfg: any;
    if (c.type === 'waterfall') {
      const kinds = c.series[0].kinds || [];
      cfg = {
        type: 'bar',
        data: { labels: c.labels, datasets: [{
          label: c.series[0].name, data: c.series[0].data,
          backgroundColor: c.series[0].data.map((v: number[], i: number) =>
            kinds[i] === 'total' ? BLUE : (v[1] - v[0] >= 0 && i > 0 && c.series[0].data[i - 1][1] <= v[0] ? GREEN : RED)),
          borderSkipped: false,
        }] },
        options: { plugins: { legend: { display: false } }, responsive: true, maintainAspectRatio: false },
      };
    } else {
      const ds2: any[] = c.series.map((s, i) => ({ type: c.type === 'line' ? 'line' : 'bar', label: s.name, data: s.data,
        backgroundColor: [BLUE, GREEN, AMBER, RED][i % 4], borderColor: [BLUE, GREEN, AMBER, RED][i % 4], tension: 0.25,
        yAxisID: 'y' }));
      if (c.line) ds2.push({ type: 'line', label: c.line.name, data: c.line.data, borderColor: GREY, yAxisID: 'y1' });
      cfg = { type: c.type === 'line' ? 'line' : 'bar', data: { labels: c.labels, datasets: ds2 },
        options: { responsive: true, maintainAspectRatio: false,
          scales: c.line ? { y: {}, y1: { position: 'right', min: 0, max: 100, grid: { drawOnChartArea: false } } } : undefined } };
    }
    const chart = new Chart(ref.current, cfg);
    return () => chart.destroy();
  }, [ds]);
  if (!ds.chart) return null;
  return <div className="ap-chart"><canvas ref={ref} /></div>;
}

export default function AnalysisPack() {
  const [list, setList] = useState<{ key: string; title: string; description: string }[]>([]);
  const [key, setKey] = useState('profit_bridge');
  const [compare, setCompare] = useState('prior_year');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [ds, setDs] = useState<Dataset | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [view, setView] = useState<'chart' | 'table'>('chart');
  const seq = useRef(0);

  useEffect(() => { api.analysisList().then(setList).catch((e: any) => setErr(String(e?.message || e))); }, []);
  useEffect(() => {
    const n = ++seq.current;
    setBusy(true); setErr('');
    api.analysisRun(key, { compare, from_date: from, to_date: to })
      .then((d) => { if (n === seq.current) { setDs(d); setView(d.chart ? 'chart' : 'table'); } })
      .catch((e: any) => { if (n === seq.current) { setDs(null); setErr(String(e?.message || e)); } })
      .finally(() => { if (n === seq.current) setBusy(false); });
  }, [key, compare, from, to]);

  const csv = () => {
    if (!ds) return;
    const esc = (x: any) => { const s = x === null || x === undefined ? '' : String(x); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const body = [ds.columns.map((c) => esc(c.label)).join(','), ...ds.rows.map((r) => ds.columns.map((c) => esc(r[c.key])).join(','))].join('\n');
    const url = URL.createObjectURL(new Blob(['\ufeff' + body], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = `${ds.key}.csv`; a.click(); URL.revokeObjectURL(url);
  };

  return (
    <div className="ap">
      <div className="ap-bar">
        <label>{t('From')} <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>{t('To')} <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <label>{t('Compare with')}{' '}
          <select value={compare} onChange={(e) => setCompare(e.target.value)}>
            <option value="prior_year">{t('Same dates last year')}</option>
            <option value="prior_period">{t('The period before')}</option>
          </select>
        </label>
        {(from || to) && <button type="button" className="bk-btn" onClick={() => { setFrom(''); setTo(''); }}>{t('This fiscal year')}</button>}
        {ds && <span className="ap-period">{ds.company} · {ds.from_date} → {ds.to_date} · {t('vs')} {ds.compare_from} → {ds.compare_to}</span>}
      </div>
      <div className="ap-tiles">
        {list.map((a) => (
          <button type="button" key={a.key} className={'ap-tile' + (a.key === key ? ' on' : '')} onClick={() => setKey(a.key)} title={a.description}>
            <b>{a.title}</b><span>{a.description}</span>
          </button>
        ))}
      </div>
      {err && <div className="alloc-warn">{err}</div>}
      {busy && !ds && <p className="bk-hint">{t('Calculating…')}</p>}
      {ds && (
        <div className={'ap-card' + (busy ? ' ap-busy' : '')}>
          <div className="ap-head">
            <h3>{ds.title}</h3>
            <div className="ap-tools">
              {ds.chart && (
                <div className="cp-seg">
                  <button type="button" className={view === 'chart' ? 'on' : ''} onClick={() => setView('chart')}>{t('Chart')}</button>
                  <button type="button" className={view === 'table' ? 'on' : ''} onClick={() => setView('table')}>{t('Table')}</button>
                </div>
              )}
              <button type="button" className="cp-link" onClick={csv}>{t('Export CSV')}</button>
            </div>
          </div>
          {!!ds.figures.length && (
            <div className="ap-figs">
              {ds.figures.map((f) => <div key={f.label} className="ap-fig"><span>{f.label}</span><b>{fmt(f.value, f.format)}</b></div>)}
            </div>
          )}
          {view === 'chart' && ds.chart ? <DatasetChart ds={ds} /> : (
            <div className="cp-table-wrap ap-table">
              <table className="cp-table">
                <thead><tr>{ds.columns.map((c) => <th key={c.key} className={c.type === 'text' ? '' : 'num'}>{c.label}</th>)}</tr></thead>
                <tbody>{ds.rows.map((r, i) => (
                  <tr key={i}>{ds.columns.map((c) => <td key={c.key} className={c.type === 'text' ? '' : 'num'}>
                    {c.type === 'text' ? (r[c.key] ?? '—') : fmt(r[c.key], c.type === 'currency' ? 'currency' : c.type)}</td>)}</tr>
                ))}</tbody>
              </table>
            </div>
          )}
          {!ds.rows.length && <p className="bk-hint">{t('No data for this period.')}</p>}
          {ds.notes.map((n, i) => <p key={i} className="ap-note">{n}</p>)}
        </div>
      )}
    </div>
  );
}
