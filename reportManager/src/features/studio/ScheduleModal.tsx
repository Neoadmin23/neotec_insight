import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

interface Sched {
  name?: string; report: string; enabled: number; frequency: string; weekday: string;
  day_of_month: number; recipients: string; whatsapp_numbers: string;
  file_format: string; subject: string; last_run?: string; last_status?: string;
}

const BLANK: Sched = {
  report: '', enabled: 1, frequency: 'Daily', weekday: 'Sunday', day_of_month: 1,
  recipients: '', whatsapp_numbers: '', file_format: 'XLSX', subject: '',
};

export default function ScheduleModal({ reports, onClose }: { reports: any[]; onClose: () => void }) {
  const [list, setList] = useState<Sched[]>([]);
  const [edit, setEdit] = useState<Sched | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const refresh = () => api.listSchedules().then(setList).catch(() => {});
  useEffect(() => { refresh(); }, []);

  async function save() {
    if (!edit?.report) { setMsg(t('Pick a report.')); return; }
    setBusy(true); setMsg('');
    try { await api.saveSchedule(edit); setEdit(null); refresh(); }
    catch (e: any) { setMsg(String(e?.message || e)); }
    finally { setBusy(false); }
  }
  async function remove(name?: string) {
    if (!name || !confirm(t('Delete this schedule?'))) return;
    await api.deleteSchedule(name).catch(() => {});
    refresh();
  }
  async function runNow(name?: string) {
    if (!name) return;
    setBusy(true); setMsg('');
    try { await api.runScheduleNow(name); setMsg(t('Dispatched — check recipients (and Last Status after refresh).')); refresh(); }
    catch (e: any) { setMsg(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  const upd = (patch: Partial<Sched>) => setEdit((e) => e ? { ...e, ...patch } : e);

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel" role="dialog" aria-label={t('Report schedules')} style={{ width: 'min(760px, 100%)' }}>
        <div className="theme-h">
          <h3>⏱ {t('Report schedules')}</h3>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>
        <p className="theme-hint">{t('Saved Studio reports delivered automatically — email attachment (XLSX/CSV) and an optional WhatsApp summary. WhatsApp needs whatsapp_token / whatsapp_phone_id in site config.')}</p>

        {!edit && (
          <>
            <table className="studio-table" style={{ width: '100%' }}>
              <thead><tr><th>{t('Report')}</th><th>{t('Frequency')}</th><th>{t('Recipients')}</th><th>{t('Last run')}</th><th /></tr></thead>
              <tbody>
                {list.map((s) => (
                  <tr key={s.name} style={{ opacity: s.enabled ? 1 : 0.45 }}>
                    <td>{s.report}</td>
                    <td>{t(s.frequency)}{s.frequency === 'Weekly' ? ` · ${t(s.weekday)}` : s.frequency === 'Monthly' ? ` · ${t('day')} ${s.day_of_month}` : ''}</td>
                    <td className="sched-recip">{[s.recipients, s.whatsapp_numbers && `📱 ${s.whatsapp_numbers}`].filter(Boolean).join(' · ') || '—'}</td>
                    <td title={s.last_status || ''}>{s.last_run ? String(s.last_run).slice(0, 16) : '—'}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button className="studio-ghost" onClick={() => setEdit(s)}>{t('Edit')}</button>{' '}
                      <button className="studio-ghost" onClick={() => runNow(s.name)} disabled={busy}>{t('Run now')}</button>{' '}
                      <button className="studio-ghost" onClick={() => remove(s.name)}>✕</button>
                    </td>
                  </tr>
                ))}
                {list.length === 0 && <tr><td colSpan={5} className="studio-hint">{t('No schedules yet.')}</td></tr>}
              </tbody>
            </table>
            <div style={{ marginTop: 12 }}>
              <button className="studio-run" onClick={() => setEdit({ ...BLANK, report: reports[0]?.slug || '' })}>+ {t('New schedule')}</button>
            </div>
          </>
        )}

        {edit && (
          <div className="sched-form">
            <label><span className="flbl">{t('Report')}</span>
              <select value={edit.report} onChange={(e) => upd({ report: e.target.value })}>
                <option value="">{t('Pick a saved report…')}</option>
                {reports.map((r) => <option key={r.slug} value={r.slug}>{r.title}</option>)}
              </select>
            </label>
            <label><span className="flbl">{t('Frequency')}</span>
              <select value={edit.frequency} onChange={(e) => upd({ frequency: e.target.value })}>
                {['Daily', 'Weekly', 'Monthly'].map((f) => <option key={f} value={f}>{t(f)}</option>)}
              </select>
            </label>
            {edit.frequency === 'Weekly' && (
              <label><span className="flbl">{t('Weekday')}</span>
                <select value={edit.weekday} onChange={(e) => upd({ weekday: e.target.value })}>
                  {['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'].map((d) => <option key={d} value={d}>{t(d)}</option>)}
                </select>
              </label>
            )}
            {edit.frequency === 'Monthly' && (
              <label><span className="flbl">{t('Day of month')}</span>
                <input type="number" min={1} max={31} value={edit.day_of_month}
                  onChange={(e) => upd({ day_of_month: parseInt(e.target.value) || 1 })} />
              </label>
            )}
            <label><span className="flbl">{t('Email recipients')}</span>
              <input value={edit.recipients} placeholder="cfo@company.com, ceo@company.com"
                onChange={(e) => upd({ recipients: e.target.value })} />
            </label>
            <label><span className="flbl">{t('WhatsApp numbers')}</span>
              <input value={edit.whatsapp_numbers} placeholder="+9665XXXXXXXX"
                onChange={(e) => upd({ whatsapp_numbers: e.target.value })} />
            </label>
            <label><span className="flbl">{t('Format')}</span>
              <select value={edit.file_format} onChange={(e) => upd({ file_format: e.target.value })}>
                <option value="XLSX">XLSX</option><option value="CSV">CSV</option>
              </select>
            </label>
            <label><span className="flbl">{t('Subject')}</span>
              <input value={edit.subject} placeholder={t('Optional — defaults to report title + date')}
                onChange={(e) => upd({ subject: e.target.value })} />
            </label>
            <label className="studio-cbx" style={{ display: 'flex', gap: 7, alignItems: 'center' }}>
              <input type="checkbox" checked={!!edit.enabled} onChange={(e) => upd({ enabled: e.target.checked ? 1 : 0 })} /> {t('Enabled')}
            </label>
            <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
              <button className="studio-run" onClick={save} disabled={busy}>{busy ? t('Saving…') : t('Save schedule')}</button>
              <button className="studio-ghost" onClick={() => setEdit(null)}>{t('Cancel')}</button>
            </div>
          </div>
        )}

        {msg && <div className="theme-hint" style={{ marginTop: 10 }}>{msg}</div>}
      </div>
    </div>
  );
}
