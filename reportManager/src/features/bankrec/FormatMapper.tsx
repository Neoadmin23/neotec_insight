import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

/* v3.6.2 — Bank Format Mapper: teach Insight a bank's statement layout from a
 * sample PDF. Suggests the header line, column mapping, date format and line
 * order; Test runs the real reader + balance proof; Save only when it proves. */

const COLS: [string, string, boolean][] = [
  ['date', 'Date', true], ['details', 'Details', true], ['reference', 'Reference', false], ['cheque', 'Cheque no.', false],
  ['type', 'Transaction type', false], ['credit', 'Credit / deposit', true], ['debit', 'Debit / withdrawal', true], ['balance', 'Balance', true],
];
const money = (v: any) => (Number(v) ? Number(v).toLocaleString(undefined, { minimumFractionDigits: 2 }) : '');

export function FormatMapper({ onClose, onSaved }: { onClose: () => void; onSaved: (name: string) => void }) {
  const [formats, setFormats] = useState<any[]>([]);
  const [fileUrl, setFileUrl] = useState('');
  const [info, setInfo] = useState<any>(null);
  const [cand, setCand] = useState(0);
  const [spec, setSpec] = useState<any>({ format_name: '', bank_name: '', columns: {}, date_format: '%d/%m/%Y', order: 'auto', footer_markers: 'Page', slack: 10 });
  const [test, setTest] = useState<any>(null);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');

  useEffect(() => { api.bankrec('list_formats').then(setFormats).catch(() => {}); }, []);
  const go = async (label: string, fn: () => Promise<any>) => {
    setBusy(label); setMsg('');
    try { return await fn(); } catch (e: any) { setMsg(String(e?.message || e)); return null; } finally { setBusy(''); }
  };
  async function upload(f: File) {
    const r = await go(t('Reading…'), async () => {
      const url = await api.uploadImage(f);
      setFileUrl(url);
      return api.bankrec('inspect_pdf', { file_url: url }, true);
    });
    if (!r) return;
    setInfo(r); setCand(0); setTest(null);
    const c = r.candidates?.[0];
    setSpec((s: any) => ({ ...s, columns: { ...(c?.suggested || {}), ...(Object.keys(s.columns).length && s._loaded ? s.columns : {}) },
      date_format: s._loaded ? s.date_format : (r.date_format || s.date_format) }));
    if (!r.candidates?.length) setMsg(t('No column header line was recognised: pick the columns by typing their header words below.'));
  }
  function pickCandidate(i: number) {
    setCand(i);
    setSpec((s: any) => ({ ...s, columns: { ...(info?.candidates?.[i]?.suggested || {}) } }));
    setTest(null);
  }
  async function loadFormat(name: string) {
    if (!name) return;
    const f = await go('…', () => api.bankrec('get_format', { name }));
    if (f) setSpec({ ...f, order: f.order || 'auto', _loaded: true });
  }
  const labels: string[] = info?.candidates?.[cand]?.labels || [];
  const doTest = () => go(t('Testing…'), () => api.bankrec('test_format', { file_url: fileUrl, spec: JSON.stringify(spec) }, true)).then((r) => r && setTest(r));
  const doSave = () => go(t('Saving…'), () => api.bankrec('save_format', { file_url: fileUrl, spec: JSON.stringify(spec) }, true))
    .then((r) => { if (r) { onSaved(r.name); onClose(); } });

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel fm-panel" role="dialog" aria-label={t('Bank format mapper')}>
        <div className="theme-h"><h3>{t('Bank format mapper')}</h3><button type="button" className="cp-x" onClick={onClose}>×</button></div>
        <div className="br-bar">
          <label className="bk-btn bk-primary">{busy === t('Reading…') ? busy : t('1 · Upload a sample statement PDF')}
            <input type="file" accept="application/pdf" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ''; }} /></label>
          <select defaultValue="" onChange={(e) => loadFormat(e.target.value)}>
            <option value="">{t('…or edit an existing format')}</option>
            {formats.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
          </select>
          {info && <span className="bk-hint">{info.pages} {t('page(s)')} · {info.dates_found} {t('dates found')}</span>}
        </div>
        {msg && <div className="alloc-warn">{msg}</div>}

        {!!info?.candidates?.length && (
          <>
            <h4 className="pl-h">{t('2 · Column header line')}</h4>
            {info.candidates.map((c: any, i: number) => (
              <label key={i} className={'fm-cand' + (i === cand ? ' on' : '')}>
                <input type="radio" checked={i === cand} onChange={() => pickCandidate(i)} />
                {c.words.map((w: any, j: number) => <span key={j} className="fm-chip">{w.text}</span>)}
              </label>
            ))}
          </>
        )}

        <h4 className="pl-h">{t('3 · Which header word starts each column')}</h4>
        <div className="pl-grid">
          {COLS.map(([k, lbl, req]) => (
            <label key={k}>{t(lbl)}{req ? ' *' : ''}
              {labels.length ? (
                <select value={spec.columns[k] || ''} onChange={(e) => setSpec({ ...spec, columns: { ...spec.columns, [k]: e.target.value } })}>
                  <option value="">{req ? t('— choose —') : t('— not on this statement —')}</option>
                  {labels.map((l) => <option key={l} value={l}>{l.replace(/#(\d+)$/, ' (#$1)')}</option>)}
                </select>
              ) : (
                <input value={spec.columns[k] || ''} placeholder={t('header word')}
                  onChange={(e) => setSpec({ ...spec, columns: { ...spec.columns, [k]: e.target.value } })} />
              )}
            </label>
          ))}
        </div>

        <h4 className="pl-h">{t('4 · Dates, order and page footer')}</h4>
        <div className="pl-grid">
          <label>{t('Date format')}
            <select value={spec.date_format} onChange={(e) => setSpec({ ...spec, date_format: e.target.value })}>
              {(info?.date_formats || ['%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%y', '%d-%b-%Y', '%m/%d/%Y', '%d.%m.%Y']).map((f: string) => (
                <option key={f} value={f}>{f.replace('%d', 'DD').replace('%m', 'MM').replace('%Y', 'YYYY').replace('%y', 'YY').replace('%b', 'Mon')}</option>))}
            </select></label>
          <label>{t('Lines are listed')}
            <select value={spec.order} onChange={(e) => setSpec({ ...spec, order: e.target.value })}>
              <option value="auto">{t('Detect from the balances')}</option>
              <option value="newest_first">{t('Newest first')}</option>
              <option value="oldest_first">{t('Oldest first')}</option>
            </select></label>
          <label>{t('Ignore words starting with (one per line)')}
            <textarea rows={2} value={spec.footer_markers} onChange={(e) => setSpec({ ...spec, footer_markers: e.target.value })} /></label>
          <label>{t('Column tolerance (pt)')}
            <input type="number" value={spec.slack} onChange={(e) => setSpec({ ...spec, slack: Number(e.target.value) || 10 })} /></label>
        </div>

        <div className="br-bar" style={{ marginTop: 12 }}>
          <button type="button" className="bk-btn" disabled={!fileUrl || !!busy} onClick={doTest}>{busy === t('Testing…') ? busy : t('5 · Test on the sample')}</button>
          {test && (
            <span className={test.ok ? 'fm-ok' : 'fm-bad'}>
              {test.ok ? `✓ ${test.lines} ${t('lines read, every balance proven')} (${t(test.order === 'newest_first' ? 'newest first' : 'oldest first')})`
                : test.lines ? `${test.bad} ${t('of')} ${test.lines} ${t('lines do not prove — adjust the mapping')}` : t('No lines read — check the date column and date format')}
            </span>
          )}
        </div>
        {test?.lines > 0 && (
          <>
            <div className="br-proof" style={{ marginTop: 8 }}>
              <div><span>{t('Opening')}</span><b>{money(test.opening)}</b></div>
              <div><span>{t('Credits')}</span><b>{money(test.credits)}</b></div>
              <div><span>{t('Debits')}</span><b>{money(test.debits)}</b></div>
              <div><span>{t('Closing')}</span><b>{money(test.closing)}</b></div>
            </div>
            <div className="cp-table-wrap fm-prev"><table className="cp-table">
              <thead><tr><th>{t('Date')}</th><th>{t('Reference')}</th><th>{t('Details')}</th><th className="num">{t('Credit')}</th>
                <th className="num">{t('Debit')}</th><th className="num">{t('Balance')}</th></tr></thead>
              <tbody>{test.preview.map((r: any, i: number) => (
                <tr key={i} className={r.ok ? '' : 'br-row-bad'}><td>{r.date}</td><td>{r.reference}</td><td className="br-details">{r.details}</td>
                  <td className="num">{money(r.credit)}</td><td className="num">{money(r.debit)}</td><td className="num">{money(r.balance)}</td></tr>))}</tbody>
            </table></div>
          </>
        )}
        <h4 className="pl-h">{t('6 · Save')}</h4>
        <div className="br-bar">
          <input placeholder={t('Format name, e.g. Al Rajhi — Business Statement')} value={spec.format_name} style={{ minWidth: 280 }}
            onChange={(e) => setSpec({ ...spec, format_name: e.target.value })} />
          <input placeholder={t('Bank')} value={spec.bank_name} onChange={(e) => setSpec({ ...spec, bank_name: e.target.value })} />
          <button type="button" className="bk-btn bk-primary" disabled={!test?.ok || !spec.format_name || !!busy} onClick={doSave}
            title={t('Saved only when the sample proves')}>{t('Save format')}</button>
        </div>
      </div>
    </div>
  );
}
