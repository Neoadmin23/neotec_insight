import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { fmtD } from '../../utils/format';
import StudioChart from './StudioChart';
import DatasetWizard from './DatasetWizard';

interface DsMeasure { key: string; field: string; agg: string; label: string }
interface DsDim { field: string; label: string }
interface DsMeta { slug: string; title: string; base_doctype: string; description?: string }

/** Explore a semantic dataset: pick measures + one dimension, get an instant
 *  governed aggregate — every number defined once, on the dataset. */
export default function DatasetExplorer({ onDeleted }: { onDeleted?: () => void }) {
  const [list, setList] = useState<DsMeta[]>([]);
  const [slug, setSlug] = useState('');
  const [ds, setDs] = useState<any>(null);
  const [dim, setDim] = useState('');
  const [picked, setPicked] = useState<string[]>([]);
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [chartType, setChartType] = useState('bar');
  const [showWizard, setShowWizard] = useState(false);

  useEffect(() => { api.listDatasets().then(setList).catch(() => {}); }, []);

  async function pick(s: string) {
    setSlug(s); setDs(null); setResult(null); setErr(null);
    if (!s) return;
    try {
      const d = await api.loadDataset(s);
      setDs(d);
      setDim(d.config?.dimensions?.[0]?.field || '');
      setPicked((d.config?.measures || []).map((m: DsMeasure) => m.key));
    } catch (e: any) { setErr(String(e?.message || e)); }
  }

  async function run() {
    if (!slug) return;
    setBusy(true); setErr(null);
    try { setResult(await api.runDataset(slug, dim || null, picked)); }
    catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  async function remove() {
    if (!slug || !confirm(t('Delete this dataset?'))) return;
    await api.deleteDataset(slug).catch(() => {});
    setSlug(''); setDs(null); setResult(null);
    api.listDatasets().then(setList).catch(() => {});
    onDeleted && onDeleted();
  }

  const measures: DsMeasure[] = ds?.config?.measures || [];
  const dims: DsDim[] = ds?.config?.dimensions || [];

  // Adapt the dataset result to StudioChart's flat-rows shape.
  const chartResult = result && {
    rows: result.rows,
    columns: [{ field: 'key', label: result.dimension?.label || t('Total'), numeric: false }]
      .concat((result.measures || []).map((m: DsMeasure) => ({ field: m.key, label: m.label, numeric: true }))),
  };

  return (
    <div className="studio-card ds-explorer">
      <div className="studio-result-h" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong>{t('Datasets')}</strong>
        <select value={slug} onChange={(e) => pick(e.target.value)}>
          <option value="">{t('Pick a dataset…')}</option>
          {list.map((d) => <option key={d.slug} value={d.slug}>{d.title} · {d.base_doctype}</option>)}
        </select>
        <button className="studio-run" onClick={() => setShowWizard(true)}>+ {t('New dataset')}</button>
        {ds && <button className="studio-ghost" onClick={remove}>{t('Delete')}</button>}
        {list.length === 0 && <span className="studio-hint">{t('No datasets yet — create one with the wizard, or use “Save as Dataset” from the Builder.')}</span>}
      </div>

      {showWizard && (
        <DatasetWizard
          onClose={() => setShowWizard(false)}
          onCreated={(slug) => { api.listDatasets().then(setList).catch(() => {}); pick(slug); }}
        />
      )}
      {ds && (
        <>
          <div className="studio-frow" style={{ flexWrap: 'wrap', alignItems: 'center', padding: '8px 12px' }}>
            <span className="studio-lbl">{t('Measures')}</span>
            {measures.map((m) => (
              <label key={m.key} className={'studio-mchip' + (picked.includes(m.key) ? ' on' : '')}>
                <input type="checkbox" checked={picked.includes(m.key)}
                  onChange={() => setPicked((p) => p.includes(m.key) ? p.filter((x) => x !== m.key) : [...p, m.key])} />
                {m.label} <em className="ds-agg">{m.agg}</em>
              </label>
            ))}
            <span className="studio-lbl" style={{ marginInlineStart: 10 }}>{t('By')}</span>
            <select value={dim} onChange={(e) => setDim(e.target.value)}>
              <option value="">{t('Total only')}</option>
              {dims.map((d) => <option key={d.field} value={d.field}>{d.label}</option>)}
            </select>
            <select value={chartType} onChange={(e) => setChartType(e.target.value)}>
              {['bar', 'hbar', 'line', 'pie', 'donut'].map((x) => <option key={x} value={x}>{t(x)}</option>)}
            </select>
            <button className="studio-run" onClick={run} disabled={busy || !picked.length}>
              {busy ? t('Running…') : t('Explore')}
            </button>
          </div>

          {err && <div className="studio-err">{err}</div>}

          {result && (
            <div style={{ padding: '0 12px 12px' }}>
              {dim && <StudioChart result={chartResult} cfg={{ type: chartType, category: 'key', measures: picked }} height={280} />}
              <div className="studio-table-wrap" style={{ marginTop: 10 }}>
                <table className="studio-table">
                  <thead><tr>
                    <th>{result.dimension?.label || ''}</th>
                    {(result.measures || []).map((m: DsMeasure) => <th key={m.key} className="num">{m.label}</th>)}
                  </tr></thead>
                  <tbody>
                    {(result.rows || []).map((r: any) => (
                      <tr key={String(r.key)}>
                        <td>{String(r.key)}</td>
                        {(result.measures || []).map((m: DsMeasure) => <td key={m.key} className="num">{fmtD(r[m.key], 2)}</td>)}
                      </tr>
                    ))}
                    <tr className="studio-grand">
                      <td>{t('Total')}</td>
                      {(result.measures || []).map((m: DsMeasure) => <td key={m.key} className="num">{fmtD(result.totals?.[m.key], 2)}</td>)}
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
