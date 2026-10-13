import { useEffect, useRef, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { RecoverPanel } from './RecoverPanel';

/* Backup (v2.89.0)
 *
 * Export / Import — one JSON file with everything Insight holds: setup AND
 *   data (allocation head counts, VAT adjustments, payment orders, bank
 *   slips, cash-flow classifications). The list of areas is discovered on
 *   the server from the installed doctypes, so a feature added later is in
 *   the backup without this screen changing.
 * Snapshots — automatic copies (daily, before every upgrade, before every
 *   restore, before bulk changes), restorable in one click. A snapshot whose
 *   record counts fell sharply against the previous one carries an alert.
 * Coverage — what is backed up, and what is deliberately not, with reasons.
 */
type Area = { label: string; doctypes: string[]; kind?: string; auto?: boolean };
type Tab = 'file' | 'snapshots' | 'recover' | 'coverage';

function download(bundle: any, base: string) {
  const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${base}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function RestoreSummary({ s }: { s: any }) {
  if (!s) return null;
  const sum = (o: any) => Object.values(o || {}).reduce((a: number, n: any) => a + (n || 0), 0);
  const failed = s.rolled_back;
  return (
    <div className={failed ? 'gl-msg gl-err' : 'gl-recon ok'} style={{ margin: 0, textAlign: 'left', padding: 8 }}>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>
        {failed
          ? '✗ ' + t('Restore failed — nothing was changed (rolled back).')
          : '✓ ' + t('Restore complete') + (s.from_version ? ` (v${s.from_version})` : '')}
      </div>
      {!failed && (
        <div style={{ fontSize: 12, fontWeight: 400 }}>
          {t('Inserted')} {sum(s.inserted)} · {t('Updated')} {sum(s.updated)} · {t('Deleted')} {sum(s.deleted)}
        </div>
      )}
      {!!s.skipped_unknown?.length && (
        <div style={{ fontSize: 11.5, fontWeight: 400 }}>
          {t('Not on this site, skipped')}: {s.skipped_unknown.join(', ')}
        </div>
      )}
      {s.pre_restore_snapshot && (
        <div style={{ fontSize: 11.5, fontWeight: 400 }}>
          {t('The state before this restore was saved as snapshot')} {s.pre_restore_snapshot}.
        </div>
      )}
      {!!s.errors?.length && (
        <details style={{ marginTop: 6, fontWeight: 400 }} open={failed}>
          <summary style={{ cursor: 'pointer' }}>{s.errors.length} {t('errors')}</summary>
          <ul style={{ fontSize: 11, margin: '6px 0 0', paddingLeft: 18 }}>
            {s.errors.map((e: string, i: number) => <li key={i}>{e}</li>)}
          </ul>
        </details>
      )}
      {!failed && <div style={{ fontSize: 11.5, fontWeight: 400, marginTop: 6 }}>{t('Reload the page to see the restored data.')}</div>}
    </div>
  );
}

export function ConfigBackupModal({ onClose, initialTab }: { onClose: () => void; initialTab?: Tab }) {
  const [tab, setTab] = useState<Tab>(initialTab || 'file');
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [summary, setSummary] = useState<any>(null);
  const [pending, setPending] = useState<any>(null);
  const [mode, setMode] = useState<'merge' | 'replace'>('merge');
  const [areas, setAreas] = useState<Area[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [snaps, setSnaps] = useState<{ ready: boolean; rows: any[] } | null>(null);
  const [compare, setCompare] = useState<any>(null);
  const [coverage, setCoverage] = useState<any>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const allDt = areas.flatMap((a) => a.doctypes);

  useEffect(() => {
    api.configAreas().then((rows) => {
      setAreas(rows || []);
      setSelected(new Set((rows || []).flatMap((a) => a.doctypes)));
    }).catch((e: any) => setErr(String(e?.message || e)));
    api.configSectionCounts().then(setCounts).catch(() => {});
  }, []);

  const loadSnaps = () => api.snapshotList().then(setSnaps)
    .catch((e: any) => setErr(String(e?.message || e)));
  useEffect(() => {
    if (tab === 'snapshots' && !snaps) loadSnaps();
    if (tab === 'coverage' && !coverage) api.configBackupCoverage().then(setCoverage)
      .catch((e: any) => setErr(String(e?.message || e)));
    // eslint-disable-next-line
  }, [tab]);

  const clear = () => { setErr(null); setMsg(null); setSummary(null); };
  async function act(key: string, fn: () => Promise<void>) {
    setBusy(key); clear();
    try { await fn(); } catch (e: any) { setErr(String(e?.message || e)); } finally { setBusy(''); }
  }

  const areaSelected = (a: Area) => a.doctypes.every((d) => selected.has(d));
  const areaCount = (a: Area) => a.doctypes.reduce((s, d) => s + (counts[d] || 0), 0);
  const toggleArea = (a: Area) => setSelected((s) => {
    const n = new Set(s); const on = a.doctypes.every((d) => n.has(d));
    a.doctypes.forEach((d) => (on ? n.delete(d) : n.add(d)));
    return n;
  });
  const allOn = allDt.length > 0 && selected.size === allDt.length;
  const toggleAll = () => setSelected(allOn ? new Set() : new Set(allDt));
  const setOnlyConfig = () => setSelected(new Set(areas.filter((a) => a.kind !== 'data').flatMap((a) => a.doctypes)));

  const doExport = () => act('export', async () => {
    const picked = [...selected];
    const bundle = await api.exportConfiguration(picked.length === allDt.length ? undefined : picked);
    download(bundle, `neotec_insight_backup_${new Date().toISOString().slice(0, 10)}`);
    const total = Object.values(bundle.counts || {}).reduce((s: number, n: any) => s + (n || 0), 0);
    setMsg(`${t('Exported')} — ${total} ${t('records')} (v${bundle.version || '?'}).`
      + (bundle.errors?.length ? ` ${t('Some areas could not be read')}: ${bundle.errors.join('; ')}` : ''));
  });

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    clear();
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result));
        if (!parsed || !parsed.data) throw new Error(t('Not a valid backup file.'));
        setPending(parsed);
      } catch (e2: any) { setErr(String(e2?.message || e2)); }
    };
    reader.readAsText(f);
  }

  const doImport = () => act('import', async () => {
    if (!pending) return;
    if (mode === 'replace' && !confirm(t('Replace: for every area in this file, records on this site that are not in the file are deleted. A snapshot is taken first. Continue?'))) return;
    const res = await api.importConfiguration(pending, mode);
    setSummary(res);
    if (!res?.rolled_back) { setPending(null); if (fileRef.current) fileRef.current.value = ''; }
  });

  const pendingTotal = pending
    ? Object.values(pending.counts || {}).reduce((s: number, n: any) => s + (n || 0), 0) : 0;

  const tabBtn = (k: Tab, label: string) => (
    <button role="tab" aria-selected={tab === k} onClick={() => { setTab(k); clear(); }}
      className={'view-subtab' + (tab === k ? ' is-active' : '')}>{label}</button>
  );

  return (
    <div className="gl-scrim" onClick={onClose}>
      <div className="gl-modal" style={{ width: 760, maxWidth: '96vw' }} onClick={(e) => e.stopPropagation()}>
        <div className="gl-head">
          <div>
            <div className="gl-title">{t('Backup')}</div>
            <div className="gl-sub">{t('Everything Insight holds — setup and data — with automatic snapshots')}</div>
          </div>
          <button className="gl-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>

        <nav className="view-subtabs" role="tablist" style={{ padding: '0 18px' }}>
          {tabBtn('file', t('Export / Import'))}
          {tabBtn('snapshots', t('Snapshots'))}
          {tabBtn('recover', t('Recover'))}
          {tabBtn('coverage', t('Coverage'))}
        </nav>

        <div style={{ padding: '14px 18px', display: 'flex', flexDirection: 'column', gap: 16, maxHeight: '70vh', overflow: 'auto' }}>
          {tab === 'file' && (
            <>
              <section>
                <div className="flbl" style={{ marginBottom: 6 }}>{t('Export')}</div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6, gap: 10 }}>
                  <label style={{ fontSize: 12.5, fontWeight: 600, display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
                    <input type="checkbox" checked={allOn} ref={(el) => { if (el) el.indeterminate = !allOn && selected.size > 0; }} onChange={toggleAll} />
                    {t('All areas')}
                  </label>
                  <button className="bk-btn" type="button" onClick={setOnlyConfig}>{t('Setup only (no data)')}</button>
                  <span style={{ fontSize: 11, color: '#9a948a', marginInlineStart: 'auto' }}>{selected.size}/{allDt.length} {t('selected')}</span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 14px', marginBottom: 10,
                              border: '1px solid var(--border,#eee)', borderRadius: 8, padding: '8px 10px' }}>
                  {areas.map((a) => (
                    <label key={a.label} style={{ fontSize: 12.5, display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
                      <input type="checkbox" checked={areaSelected(a)} onChange={() => toggleArea(a)} />
                      <span>{t(a.label)}</span>
                      {a.auto && <span title={t('Found automatically — not yet given a label')} style={{ fontSize: 10, color: '#8a6d1f' }}>{t('auto')}</span>}
                      <span style={{ fontSize: 11, color: '#9a948a', marginInlineStart: 'auto' }}>{areaCount(a)}</span>
                    </label>
                  ))}
                </div>
                <button className="btn-primary gl-open" onClick={doExport} disabled={!!busy || selected.size === 0} style={{ border: 0 }}>
                  {busy === 'export' ? t('Working…') : allOn ? t('Export everything') : `${t('Export selected')} (${selected.size})`}
                </button>
              </section>

              <section style={{ borderTop: '1px solid var(--border,#eee)', paddingTop: 14 }}>
                <div className="flbl" style={{ marginBottom: 6 }}>{t('Import / Restore')}</div>
                <div style={{ display: 'flex', gap: 16, fontSize: 12.5, marginBottom: 8 }}>
                  <label style={{ display: 'flex', gap: 6 }}>
                    <input type="radio" checked={mode === 'merge'} onChange={() => setMode('merge')} />
                    <span><b>{t('Merge')}</b> — {t('add what is missing, update what matches, delete nothing')}</span>
                  </label>
                  <label style={{ display: 'flex', gap: 6 }}>
                    <input type="radio" checked={mode === 'replace'} onChange={() => setMode('replace')} />
                    <span><b>{t('Replace')}</b> — {t('make each area in the file exactly match it')}</span>
                  </label>
                </div>
                <p style={{ fontSize: 12, color: '#6b675f', margin: '0 0 8px' }}>
                  {t('A snapshot is taken before every restore, and a restore that hits any error changes nothing. Areas not in the file are never touched.')}
                </p>
                <input ref={fileRef} type="file" accept="application/json,.json" onChange={onPickFile} />
                {pending && (
                  <div style={{ marginTop: 10 }}>
                    <div style={{ fontSize: 12.5, marginBottom: 8 }}>
                      {t('Ready to restore')}: <b>{pendingTotal}</b> {t('records')}
                      {pending.version ? ` · v${pending.version}` : ''}
                      {pending.site ? ` · ${t('from')} ${pending.site}` : ''}
                    </div>
                    <button className="btn-primary gl-open" onClick={doImport} disabled={!!busy}
                      style={{ border: 0, background: mode === 'replace' ? '#b3261e' : undefined }}>
                      {busy === 'import' ? t('Restoring…') : mode === 'merge' ? t('Restore (merge)') : t('Restore (replace)')}
                    </button>
                  </div>
                )}
              </section>
            </>
          )}

          {tab === 'snapshots' && (
            <section>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                <p style={{ fontSize: 12.5, color: '#6b675f', margin: 0, flex: 1 }}>
                  {t('Taken automatically every day (when something changed), before every upgrade, before every restore and before bulk changes. ⚠ marks a snapshot whose record counts fell sharply.')}
                </p>
                <button className="bk-btn bk-primary" disabled={!!busy || !snaps?.ready}
                  onClick={() => act('take', async () => {
                    const r = await api.snapshotTake(prompt(t('Label (optional)')) || '');
                    setMsg(`${t('Snapshot taken')}: ${r.name} · ${r.records} ${t('records')}`);
                    await loadSnaps();
                  })}>{busy === 'take' ? t('Working…') : t('Take snapshot now')}</button>
              </div>
              {snaps && !snaps.ready && <div className="gl-msg gl-err">{t('The snapshot store is not installed yet. Run Migrate Database.')}</div>}
              {snaps?.ready && !snaps.rows.length && <p className="bk-hint">{t('No snapshots yet.')}</p>}
              {!!snaps?.rows.length && (
                <table className="cap-table" style={{ width: '100%' }}>
                  <thead><tr><th>{t('Taken')}</th><th>{t('Type')}</th><th>{t('Label')}</th><th>{t('Version')}</th><th className="num">{t('Records')}</th><th /></tr></thead>
                  <tbody>
                    {snaps.rows.map((s: any) => (
                      <tr key={s.name} title={s.alert || s.name}>
                        <td style={{ whiteSpace: 'nowrap' }}>{s.creation}</td>
                        <td>{t(s.snapshot_type)}{s.scoped ? ` (${t('partial')})` : ''}{s.pinned ? ' 📌' : ''}</td>
                        <td>{s.alert ? <span style={{ color: '#b3261e' }}>⚠ {s.alert.split('\n')[0]}</span> : s.label}</td>
                        <td>{s.app_version}</td>
                        <td className="num">{s.record_count}</td>
                        <td style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                          <button className="bk-btn" disabled={!!busy} onClick={() => act('cmp', async () => setCompare(await api.snapshotCompare(s.name)))}>{t('Compare')}</button>
                          <button className="bk-btn" disabled={!!busy} onClick={() => act('dl', async () => download(await api.snapshotBundle(s.name), `neotec_insight_${s.name}`))}>{t('Download')}</button>
                          <button className="bk-btn bk-primary" disabled={!!busy}
                            onClick={() => act('restore', async () => {
                              const m = confirm(t('Merge this snapshot into the site? (OK = merge: add missing, update matching, delete nothing. Cancel = choose replace instead.)'))
                                ? 'merge'
                                : (confirm(t('Replace: every area in the snapshot is made to match it exactly; newer records in those areas are deleted. Continue?')) ? 'replace' : null);
                              if (!m) return;
                              setSummary(await api.snapshotRestore(s.name, m));
                              await loadSnaps();
                            })}>{t('Restore')}</button>
                          <button className="bk-btn" disabled={!!busy} onClick={() => act('pin', async () => { await api.snapshotPin(s.name, !s.pinned); await loadSnaps(); })}>{s.pinned ? t('Unpin') : t('Pin')}</button>
                          <button className="bk-btn" disabled={!!busy} onClick={() => act('del', async () => {
                            if (!confirm(t('Delete this snapshot permanently?'))) return;
                            await api.snapshotDelete(s.name); await loadSnaps();
                          })}>×</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {compare && (
                <div style={{ marginTop: 12 }}>
                  <div className="flbl">{t('Snapshot')} {compare.name} {t('compared with now')}</div>
                  <table className="cap-table">
                    <thead><tr><th>{t('Area')}</th><th className="num">{t('Snapshot')}</th><th className="num">{t('Now')}</th><th className="num">±</th></tr></thead>
                    <tbody>
                      {compare.rows.filter((r: any) => r.snapshot || r.now).map((r: any) => (
                        <tr key={r.doctype} style={r.diff < 0 ? { color: '#b3261e' } : undefined}>
                          <td>{r.doctype}</td><td className="num">{r.snapshot}</td><td className="num">{r.now}</td>
                          <td className="num">{r.diff > 0 ? '+' : ''}{r.diff}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}

          {tab === 'recover' && <RecoverPanel />}

          {tab === 'coverage' && coverage && (
            <section>
              <p style={{ fontSize: 12.5, color: '#6b675f', margin: '0 0 8px' }}>
                {t('Every Insight doctype installed on this site is backed up unless listed as excluded below. Doctypes marked “auto” are included automatically.')}
              </p>
              <table className="cap-table" style={{ width: '100%' }}>
                <thead><tr><th>{t('Doctype')}</th><th>{t('Area')}</th><th>{t('Kind')}</th><th className="num">{t('Records')}</th></tr></thead>
                <tbody>
                  {(coverage.plan || []).map((p: any) => (
                    <tr key={p.doctype}>
                      <td>{p.doctype}</td><td>{t(p.area)}{p.auto ? ` (${t('auto')})` : ''}</td>
                      <td>{t(p.kind || 'config')}</td><td className="num">{counts[p.doctype] ?? ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="flbl" style={{ marginTop: 12 }}>{t('Not backed up')}</div>
              <ul style={{ fontSize: 12, paddingLeft: 18 }}>
                {Object.entries(coverage.excluded_reasons || {}).map(([k, v]: any) => <li key={k}><b>{k}</b> — {v}</li>)}
              </ul>
            </section>
          )}

          {msg && <div className="gl-recon ok" style={{ margin: 0 }}>{msg}</div>}
          {err && <div className="gl-msg gl-err" style={{ padding: 8, textAlign: 'left' }}>{err}</div>}
          <RestoreSummary s={summary} />
        </div>
      </div>
    </div>
  );
}
