import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

/* v2.89.3 — "Where did my allocation go?" in one click.
 * Walks the chain the Report depends on — rule, stored figures (this year,
 * other years, impossible years, other companies, excluded cost centres),
 * the pool, the computed result, snapshots and deleted entries — and offers
 * the fix for each broken link. */
interface Props {
  rule: string; year: number; company: string | null;
  onClose: () => void; onChanged: () => void; onOpenRecovery: () => void; onOpenPaste: () => void;
}

const fmt = (v: number) => (v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
/** Row label with its January and year figures, so the pool row is recognisable by its numbers. */
export function candLabel(r: any) {
  return `${r.label} — Jan ${fmt(r.jan)} · YTD ${fmt(r.ytd)}  (${r.key})`;
}

export function DiagnoseModal({ rule, year, company, onClose, onChanged, onOpenRecovery, onOpenPaste }: Props) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');
  const [pick, setPick] = useState('');

  const load = () => { setErr(''); api.allocationDiagnose(rule, year, company).then(setD)
    .catch((e: any) => setErr(e?.message || String(e))); };
  useEffect(load, [rule, year, company]);

  async function act(key: string, fn: () => Promise<string>) {
    setBusy(key); setErr(''); setMsg('');
    try { setMsg(await fn()); load(); onChanged(); }
    catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(''); }
  }

  const icon = (ok: boolean | null) => ok === true ? '✓' : ok === false ? '✗' : 'ℹ';
  const colour = (ok: boolean | null) => ok === true ? '#1f7a4a' : ok === false ? '#b3261e' : '#6b675f';

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel" role="dialog" style={{ width: 'min(820px, 100%)' }}>
        <div className="theme-h">
          <h3>{t('Diagnose')} · {rule} · {year}</h3>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>
        <div>
          {!d && !err && <p>{t('Checking…')}</p>}
          {d?.checks?.map((c: any) => (
            <div key={c.key} style={{ display: 'flex', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--border,#eee)' }}>
              <div style={{ color: colour(c.ok), fontWeight: 700, width: 16 }}>{icon(c.ok)}</div>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600 }}>{c.title}</div>
                {c.detail && <div style={{ fontSize: 12.5, color: '#4a463f' }}>{c.detail}</div>}
                <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                  {c.fix === 'move_year' && (c.years || []).map((y: number) => (
                    <button key={y} className="bk-btn bk-primary" disabled={!!busy}
                      onClick={() => act('mv' + y, async () => {
                        if (!confirm(t('Move the figures stored under') + ` ${y} ` + t('to') + ` ${year}? `
                          + t('Months already filled in the target are kept. A snapshot is taken first.'))) return '';
                        const r = await api.allocationMoveYear(rule, y, year, false);
                        return `${r.moved} ${t('moved')} · ${r.kept_target} ${t('already filled, kept')}`;
                      })}>{t('Move')} {y} → {year}</button>
                  ))}
                  {c.fix === 'pick_row' && !!c.candidates?.length && (
                    <>
                      <div style={{ fontSize: 12, color: '#6b675f', width: '100%' }}>
                        {t('Rows with figures are listed first. Pick the one whose January figure matches the cost you expect to spread.')}
                      </div>
                      <select value={pick} onChange={(e) => setPick(e.target.value)} style={{ minWidth: 260, maxWidth: '100%' }}>
                        <option value="">{t('Choose the row that holds the pool…')}</option>
                        {c.candidates.map((r: any) => <option key={r.key} value={r.key}>{candLabel(r)}</option>)}
                      </select>
                      <button className="bk-btn bk-primary" disabled={!pick || !!busy}
                        onClick={() => act('row', async () => {
                          const r = await api.allocationSetPoolRow(rule, pick);
                          return `${t('Pool row set to')} ${r.label}`;
                        })}>{t('Use this row')}</button>
                    </>
                  )}
                  {c.fix === 'restore_report' && (
                    <button className="bk-btn bk-primary" disabled={!!busy}
                      onClick={() => act('rr', async () => {
                        if (!confirm(t('Put this earlier version of the report back? The current version is kept in a snapshot first.'))) return '';
                        const r = await api.recoveryRestoreReport(c.report, c.version_id);
                        return `${t('Report restored from')} ${r.from} · ${r.when} · ${r.summary.rows} ${t('rows')}`;
                      })}>{t('Restore this version')}</button>
                  )}
                  {c.fix === 'snapshots' && (
                    <button className="bk-btn" onClick={() => { onClose(); onOpenRecovery(); }}>{t('Find my data')}</button>
                  )}
                  {c.fix === 'paste' && (
                    <button className="bk-btn" onClick={() => { onClose(); onOpenPaste(); }}>{t('Paste figures')}</button>
                  )}
                </div>
              </div>
            </div>
          ))}
          {msg && <div className="alloc-ok" style={{ marginTop: 10 }}>{msg}</div>}
          {err && <div className="ni-exp-err" style={{ marginTop: 10 }}>{err}</div>}
        </div>
        <div style={{ paddingTop: 10, display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button className="bk-btn" onClick={load} disabled={!!busy}>{t('Check again')}</button>
          <button className="bk-btn" onClick={onClose}>{t('Close')}</button>
        </div>
      </div>
    </div>
  );
}
