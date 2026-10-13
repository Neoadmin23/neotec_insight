import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

/* Backup → Recover (v2.89.5)
 * Report versions — earlier versions of a report from its version history,
 *   deleted copies and snapshots; the one still holding the rows allocation
 *   rules read their pool from is listed first.
 * Deleted records — anything Insight deleted (allocation entries, account
 *   mappings, rules, budgets …), restorable by selection. */
export function RecoverPanel() {
  const [reports, setReports] = useState<any[]>([]);
  const [report, setReport] = useState('');
  const [vers, setVers] = useState<any>(null);
  const [summary, setSummary] = useState<any[] | null>(null);
  const [dt, setDt] = useState('');
  const [list, setList] = useState<any[] | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [replace, setReplace] = useState(false);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => {
    api.listReports().then((r: any[]) => {
      setReports(r || []);
      const pl = (r || []).find((x: any) => (x.slug || x.name) === 'consolidated-p-and-l') || (r || [])[0];
      if (pl) setReport(pl.name);
    }).catch(() => {});
    api.recoveryDeletedSummary().then(setSummary).catch((e: any) => setErr(String(e?.message || e)));
  }, []);
  useEffect(() => {
    if (!report) return;
    setVers(null);
    api.recoveryReportVersions(report).then(setVers).catch((e: any) => setErr(String(e?.message || e)));
  }, [report]);
  useEffect(() => {
    if (!dt) return;
    setList(null); setSel(new Set());
    api.recoveryDeletedList(dt).then(setList).catch((e: any) => setErr(String(e?.message || e)));
  }, [dt]);

  async function act(key: string, fn: () => Promise<string>) {
    setBusy(key); setErr(''); setMsg('');
    try { const m = await fn(); if (m) setMsg(m); } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(''); }
  }

  const Sum = ({ s }: { s: any }) => (
    <div style={{ fontSize: 12, color: '#4a463f' }}>
      {s.rows} {t('rows')}
      {s.allocation_rows?.length ? ` · ${t('allocation rows')}: ${s.allocation_rows.join(', ')}` : ` · ${t('no allocation rows')}`}
      {s.has_pool_rows?.length ? <span style={{ color: '#1f7a4a' }}> · {t('has pool row')} {s.has_pool_rows.join(', ')}</span> : null}
      {s.missing_pool_rows?.length ? <span style={{ color: '#b3261e' }}> · {t('missing pool row')} {s.missing_pool_rows.join(', ')}</span> : null}
      {s.last_formula && <div style={{ fontFamily: 'monospace', fontSize: 11 }}>{s.last_formula}</div>}
    </div>
  );

  return (
    <>
      <section>
        <div className="flbl" style={{ marginBottom: 6 }}>{t('Report versions')}</div>
        <select value={report} onChange={(e) => setReport(e.target.value)} style={{ minWidth: 280, marginBottom: 8 }}>
          {reports.map((r: any) => <option key={r.name} value={r.name}>{r.report_name || r.name}</option>)}
        </select>
        {vers?.current && (
          <div style={{ padding: 8, border: '1px solid var(--border,#eee)', borderRadius: 8, marginBottom: 8 }}>
            <b>{t('Now')}</b>{vers.current.seed_source === 'app-preset' ? ` — ${t('the stock preset, created')} ${vers.current.created}` : ''}
            <Sum s={vers.current.summary} />
          </div>
        )}
        {vers && !vers.versions.length && <p className="bk-hint">{t('No earlier versions were found for this report.')}</p>}
        {vers?.versions.map((v: any, i: number) => (
          <div key={v.id} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '8px 0', borderBottom: '1px solid var(--border,#eee)' }}>
            <div style={{ flex: 1 }}>
              <b>{i === 0 ? '★ ' : ''}{v.when}</b> · {t(v.source)}{v.who ? ` · ${v.who}` : ''}
              <Sum s={v.summary} />
            </div>
            <button className="bk-btn bk-primary" disabled={!!busy}
              onClick={() => act('r' + v.id, async () => {
                if (!confirm(t('Put this version back over the current report? The current version is kept in a snapshot first.'))) return '';
                const r = await api.recoveryRestoreReport(report, v.id);
                setVers(await api.recoveryReportVersions(report));
                return `${t('Restored')}: ${r.summary.rows} ${t('rows')} (${t(r.from)}, ${r.when}). ${t('Reload the page to see it.')}`;
              })}>{busy === 'r' + v.id ? t('Restoring…') : t('Restore')}</button>
          </div>
        ))}
      </section>

      <section style={{ borderTop: '1px solid var(--border,#eee)', paddingTop: 14 }}>
        <div className="flbl" style={{ marginBottom: 6 }}>{t('Deleted records')}</div>
        {summary && !summary.length && <p className="bk-hint">{t('Nothing deleted.')}</p>}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
          {summary?.map((s: any) => (
            <button key={s.doctype} className={'bk-btn' + (dt === s.doctype ? ' bk-primary' : '')}
              onClick={() => setDt(s.doctype)} title={`${s.first} → ${s.last}`}>
              {s.doctype.replace('Insight ', '')} · {s.count}
            </button>
          ))}
        </div>
        {list && (
          <>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 6 }}>
              <label style={{ fontSize: 12.5 }}>
                <input type="checkbox" checked={list.length > 0 && sel.size === list.length}
                  onChange={() => setSel(sel.size === list.length ? new Set() : new Set(list.map((r: any) => r.name)))} /> {t('All')}
              </label>
              <label style={{ fontSize: 12.5 }}>
                <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />{' '}
                {t('Write over a record that exists again under the same name')}
              </label>
              <button className="bk-btn bk-primary" disabled={!sel.size || !!busy} style={{ marginInlineStart: 'auto' }}
                onClick={() => act('del', async () => {
                  const r = await api.recoveryRestoreDeleted([...sel], replace ? 'replace' : 'skip');
                  setList(await api.recoveryDeletedList(dt));
                  setSummary(await api.recoveryDeletedSummary());
                  return `${r.restored} ${t('restored')} · ${r.replaced} ${t('written over')} · ${r.skipped} ${t('skipped')}`
                    + (r.errors?.length ? ` · ${r.errors.length} ${t('errors')}: ${r.errors.slice(0, 3).join('; ')}` : '');
                })}>{t('Restore selected')} ({sel.size})</button>
            </div>
            <table className="cap-table" style={{ width: '100%' }}>
              <thead><tr><th /><th>{t('Deleted')}</th><th>{t('Record')}</th><th>{t('By')}</th><th /></tr></thead>
              <tbody>
                {list.map((r: any) => (
                  <tr key={r.name}>
                    <td><input type="checkbox" checked={sel.has(r.name)} onChange={() => setSel((s) => {
                      const n = new Set(s); if (n.has(r.name)) n.delete(r.name); else n.add(r.name); return n; })} /></td>
                    <td style={{ whiteSpace: 'nowrap' }}>{r.when}</td><td>{r.title}</td><td>{r.who}</td>
                    <td>{r.exists_now ? <span title={t('A record with this name exists again')}>⚠</span> : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>
      {msg && <div className="gl-recon ok" style={{ margin: 0 }}>{msg}</div>}
      {err && <div className="gl-msg gl-err" style={{ padding: 8, textAlign: 'left' }}>{err}</div>}
    </>
  );
}
