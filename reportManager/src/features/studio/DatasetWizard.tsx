import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { fmtD } from '../../utils/format';

interface Field { fieldname: string; label: string; fieldtype: string; numeric?: boolean }
interface MeasureDef { field: string; agg: string; label: string; on: boolean }

/** Dataset wizard (v2.26.0) — model against the DocType meta directly:
 *  explicit aggregation per measure, named properly, only meaningful
 *  dimensions, deliberate base filters, and a LIVE preview on real data
 *  before anything is saved. */
export default function DatasetWizard({ onCreated, onClose }: {
  onCreated: (slug: string) => void; onClose: () => void;
}) {
  const [sources, setSources] = useState<any[]>([]);
  const [doctype, setDoctype] = useState('');
  const [fields, setFields] = useState<Field[]>([]);
  const [title, setTitle] = useState('');
  const [measures, setMeasures] = useState<MeasureDef[]>([]);
  const [dims, setDims] = useState<Record<string, boolean>>({});
  const [submittedOnly, setSubmittedOnly] = useState(true);
  const [prevDim, setPrevDim] = useState('');
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => { api.studioListSources('').then(setSources).catch(() => {}); }, []);

  function pickDoctype(dt: string) {
    setDoctype(dt); setFields([]); setMeasures([]); setDims({}); setPreview(null); setPrevDim(''); setErr('');
    if (!dt) return;
    api.studioListFields(dt).then((f: Field[]) => {
      setFields(f);
      setMeasures(f.filter((x) => x.numeric).slice(0, 20).map((x) => ({
        field: x.fieldname, agg: 'sum', label: x.label, on: false,
      })));
    }).catch((e: any) => setErr(String(e?.message || e)));
  }

  const buildConfig = () => ({
    measures: measures.filter((m) => m.on).map((m) => ({
      key: m.field, field: m.field, agg: m.agg, label: m.label.trim() || m.field,
    })),
    dimensions: fields.filter((f) => dims[f.fieldname]).map((f) => ({ field: f.fieldname, label: f.label })),
    filters: submittedOnly ? [{ field: 'docstatus', op: '=', value: '1' }] : [],
  });

  async function runPreview() {
    const cfg = buildConfig();
    if (!cfg.measures.length) { setErr(t('Tick at least one measure.')); return; }
    setBusy('preview'); setErr('');
    try { setPreview(await api.previewDataset(doctype, cfg, prevDim || null)); }
    catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(''); }
  }

  async function save() {
    const cfg = buildConfig();
    if (!title.trim()) { setErr(t('Give the dataset a title.')); return; }
    if (!cfg.measures.length) { setErr(t('Tick at least one measure.')); return; }
    setBusy('save'); setErr('');
    try {
      const r = await api.saveDataset({ title: title.trim(), base_doctype: doctype, config: cfg });
      onCreated(r.slug); onClose();
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(''); }
  }

  const updM = (i: number, patch: Partial<MeasureDef>) =>
    setMeasures((ms) => ms.map((m, k) => (k === i ? { ...m, ...patch } : m)));
  const dimFields = fields.filter((f) => !f.numeric && !['Text', 'Long Text', 'Code', 'HTML'].includes(f.fieldtype));
  const chosenDims = dimFields.filter((f) => dims[f.fieldname]);

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel" role="dialog" aria-label={t('New dataset')} style={{ width: 'min(860px, 100%)' }}>
        <div className="theme-h">
          <h3>◆ {t('New dataset')}</h3>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>

        <div className="studio-frow" style={{ gap: 8, marginBottom: 10 }}>
          <input placeholder={t('Dataset title — e.g. Sales Performance')} value={title}
            onChange={(e) => setTitle(e.target.value)} style={{ flex: 1 }} />
          <select value={doctype} onChange={(e) => pickDoctype(e.target.value)}>
            <option value="">{t('Base document…')}</option>
            {sources.map((s) => <option key={s.name} value={s.name}>{s.label}{s.is_child ? ` · ${t('lines of')} ${s.parent_doctype}` : ''}</option>)}
          </select>
          <label className="studio-cbx" style={{ display: 'flex', gap: 6, alignItems: 'center', whiteSpace: 'nowrap' }}>
            <input type="checkbox" checked={submittedOnly} onChange={(e) => setSubmittedOnly(e.target.checked)} /> {t('Submitted only')}
          </label>
        </div>

        {doctype && (
          <div className="dsw-cols">
            <div>
              <div className="theme-sec-title">{t('Measures — what gets aggregated')}</div>
              <div className="dsw-list">
                {measures.map((m, i) => (
                  <div key={m.field} className={'dsw-measure' + (m.on ? ' on' : '')}>
                    <input type="checkbox" checked={m.on} onChange={(e) => updM(i, { on: e.target.checked })} />
                    <input className="dsw-mlabel" value={m.label} disabled={!m.on}
                      onChange={(e) => updM(i, { label: e.target.value })} title={m.field} />
                    <select value={m.agg} disabled={!m.on} onChange={(e) => updM(i, { agg: e.target.value })}>
                      {['sum', 'avg', 'min', 'max', 'count'].map((a) => <option key={a} value={a}>{a}</option>)}
                    </select>
                  </div>
                ))}
                {measures.length === 0 && <div className="studio-hint">{t('No numeric fields on this document.')}</div>}
              </div>
            </div>
            <div>
              <div className="theme-sec-title">{t('Dimensions — what it can be split by')}</div>
              <div className="dsw-list">
                {dimFields.slice(0, 40).map((f) => (
                  <label key={f.fieldname} className={'studio-mchip' + (dims[f.fieldname] ? ' on' : '')} style={{ display: 'flex', margin: '2px 0' }}>
                    <input type="checkbox" checked={!!dims[f.fieldname]}
                      onChange={(e) => setDims((d) => ({ ...d, [f.fieldname]: e.target.checked }))} />
                    {f.label}
                  </label>
                ))}
              </div>
            </div>
          </div>
        )}

        {doctype && (
          <div className="studio-frow" style={{ gap: 8, marginTop: 12, alignItems: 'center' }}>
            <select value={prevDim} onChange={(e) => setPrevDim(e.target.value)}>
              <option value="">{t('Preview: total only')}</option>
              {chosenDims.map((f) => <option key={f.fieldname} value={f.fieldname}>{t('Preview by')} {f.label}</option>)}
            </select>
            <button className="studio-ghost" onClick={runPreview} disabled={busy !== ''}>
              {busy === 'preview' ? t('Running…') : t('Live preview')}
            </button>
            <span style={{ flex: 1 }} />
            <button className="studio-run" onClick={save} disabled={busy !== ''}>
              {busy === 'save' ? t('Saving…') : t('Create dataset')}
            </button>
          </div>
        )}

        {err && <div className="theme-err" style={{ marginTop: 8 }}>{err}</div>}

        {preview && (
          <div className="studio-table-wrap" style={{ marginTop: 10, maxHeight: 260, overflow: 'auto' }}>
            <table className="studio-table">
              <thead><tr>
                <th>{preview.dimension?.label || ''}</th>
                {(preview.measures || []).map((m: any) => <th key={m.key} className="num">{m.label} <em className="ds-agg">{m.agg}</em></th>)}
              </tr></thead>
              <tbody>
                {(preview.rows || []).map((r: any) => (
                  <tr key={String(r.key)}>
                    <td>{String(r.key)}</td>
                    {(preview.measures || []).map((m: any) => <td key={m.key} className="num">{fmtD(r[m.key], 2)}</td>)}
                  </tr>
                ))}
                <tr className="studio-grand">
                  <td>{t('Total')}</td>
                  {(preview.measures || []).map((m: any) => <td key={m.key} className="num">{fmtD(preview.totals?.[m.key], 2)}</td>)}
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
