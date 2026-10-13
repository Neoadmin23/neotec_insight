import { useEffect, useState } from 'react';
import { t } from '../../utils/i18n';
import { api } from '../../utils/api';

/**
 * v2.88.12 — a rule with no head-count entries is not the same as a rule that
 * never had any. This looks for where they went (entries left pointing at a
 * rule name that no longer exists, or deleted entries still in the Deleted
 * Document log) and offers to bring them back to this rule.
 */
interface Props { rule: string; year: number; onClose: () => void; onDone: () => void; }

export function EntriesRecoveryModal({ rule, year, onClose, onDone }: Props) {
  const [d, setD] = useState<any>(null);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [snaps, setSnaps] = useState<any[] | null>(null);
  const load = () => {
    api.allocationEntriesDiagnostic(rule, year).then(setD)
      .catch((e: any) => setErr(e?.message || String(e)));
    // v2.89.0 — automatic snapshots that still hold this rule's figures.
    api.allocationSnapshotCandidates(rule, year)
      .then((r: any) => setSnaps(r?.rows || []))
      .catch(() => setSnaps([]));
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [rule, year]);

  async function run(key: string, fn: () => Promise<any>, ok: (r: any) => string) {
    setBusy(key); setErr(''); setMsg('');
    try { const r = await fn(); setMsg(ok(r)); await load(); onDone(); }
    catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(''); }
  }

  const state = d?.state;
  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel" role="alertdialog" style={{ maxWidth: 680 }}>
        <div className="theme-h">
          <h3>⚠ {t('No head-count entries are stored for')} {rule}</h3>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>
        <div className="cap-body">
          {!d && !err && <p className="bk-hint">{t('Looking for them…')}</p>}
          {err && <div className="alloc-warn">{err}</div>}
          {msg && <div className="alloc-ok">{msg}</div>}

          {d && state === 'ok' && <p>{t('This rule now has entries. Close this and press Reload.')}</p>}

          {!!snaps?.length && (
            <>
              <p><strong>{t('Automatic snapshots that hold figures for this rule:')}</strong>{' '}
                {t('Restore adds the entries that are missing; figures already on the site and frozen months are left as they are.')}</p>
              <table className="cap-table">
                <thead><tr><th>{t('Taken')}</th><th>{t('Type')}</th><th className="num">{year}</th><th className="num">{t('All years')}</th><th /></tr></thead>
                <tbody>
                  {snaps.slice(0, 12).map((s: any) => (
                    <tr key={s.name} title={s.label}>
                      <td>{s.creation}</td><td>{t(s.type)}{s.alert ? ' ⚠' : ''}</td>
                      <td className="num">{s.entries_year}</td><td className="num">{s.entries}</td>
                      <td style={{ display: 'flex', gap: 6 }}>
                        <button className="bk-btn" disabled={!!busy}
                          onClick={() => run('snapcheck-' + s.name,
                            () => api.allocationRestoreFromSnapshot(rule, s.name, null, false, true),
                            (r) => `${t('Check')}: ${r.inserted} ${t('would be added')}, ${r.kept} ${t('already present')}, ${r.skipped_frozen} ${t('frozen')}`
                              + (r.rule_restored ? ` · ${t('the rule itself would be restored')}` : ''))}>
                          {t('Check')}
                        </button>
                        <button className="bk-btn bk-primary" disabled={!!busy}
                          onClick={() => run('snap-' + s.name,
                            () => api.allocationRestoreFromSnapshot(rule, s.name, null, false, false),
                            (r) => `${r.inserted} ${t('restored')}, ${r.kept} ${t('already present')}`
                              + (r.skipped_frozen ? `, ${r.skipped_frozen} ${t('frozen left as is')}` : ''))}>
                          {busy === 'snap-' + s.name ? t('Restoring…') : t('Restore')}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {d && !!d.deleted?.length && (
            <>
              <p><strong>{t('Deleted entries found in the Deleted Document log:')}</strong></p>
              <table className="cap-table">
                <thead><tr><th>{t('Belonged to rule')}</th><th className="num">{t('Entries')}</th><th>{t('Deleted by')}</th><th>{t('When')}</th><th /></tr></thead>
                <tbody>
                  {d.deleted.map((g: any) => (
                    <tr key={g.rule}>
                      <td>{g.rule}</td><td className="num">{g.entries}</td><td>{g.owner}</td><td>{g.creation}</td>
                      <td>{g.rule === rule && (
                        <button className="bk-btn bk-primary" disabled={!!busy}
                          onClick={() => run('restore', () => api.allocationRestoreEntries(rule),
                            (r) => `${r.restored} ${t('restored')}${r.failed ? ` · ${r.failed} ${t('failed')}: ${(r.errors || []).join('; ')}` : ''}`)}>
                          {busy === 'restore' ? t('Restoring…') : t('Restore')}
                        </button>)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {d && !!d.orphans?.length && (
            <>
              <p><strong>{t('Entries whose rule no longer exists:')}</strong> {t('they can be moved onto this rule.')}</p>
              <table className="cap-table">
                <thead><tr><th>{t('Was attached to')}</th><th className="num">{t('Entries')}</th><th /></tr></thead>
                <tbody>
                  {d.orphans.map((o: any) => (
                    <tr key={o.rule}>
                      <td>{o.rule}</td><td className="num">{o.entries}</td>
                      <td><button className="bk-btn bk-primary" disabled={!!busy}
                        onClick={() => run('move', () => api.allocationReattachEntries(o.rule, rule),
                          (r) => `${r.moved} ${t('moved')}${r.skipped ? ` · ${r.skipped} ${t('skipped (already present)')}` : ''}`)}>
                        {busy === 'move' ? t('Moving…') : t('Move to this rule')}
                      </button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {d && !!d.other_rules?.length && (
            <p className="bk-hint">{t('Other rules still hold entries:')} {d.other_rules.map((o: any) => `${o.rule} (${o.entries})`).join(' · ')}. {t('Entries are not moved between existing rules automatically.')}</p>
          )}

          {d && state === 'none' && !snaps?.length && (
            <>
              <p>{t('Nothing was found: no entries under another rule name, and none in the Deleted Document log. The head-count figures are no longer in this site\'s database.')}</p>
              <p>{t('Ways back, in order of accuracy:')}</p>
              <ol>
                <li>{t('Restore a Frappe Cloud site backup from before they disappeared (or take the Insight Allocation Entry rows from it).')}</li>
                <li>{t('Backup → Snapshots: restore an automatic snapshot (taken daily and before every upgrade from v2.89.0 on).')}</li>
                <li>{t('Import an Insight configuration backup made with v2.88.11 or later (Backup → Import, Merge). Earlier backups do not contain the head counts.')}</li>
                <li>{t('Capture from HR, month by month. It counts from Employee joining and relieving dates, so it can differ from figures that were typed by hand.')}</li>
              </ol>
            </>
          )}

          <div className="bk-foot cap-foot">
            <button className="bk-btn" onClick={load} disabled={!!busy}>{t('Check again')}</button>
            <button className="bk-btn bk-primary" onClick={onClose}>{t('Close')}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
