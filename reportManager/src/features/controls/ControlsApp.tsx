import { useCallback, useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { deskPrefix } from '../../utils/desk';
import { docLink } from '../../utils/statementCore';

/* Insight Controls (v3.5.0) — continuous finance checks. Each issue is one
 * finding across daily runs: Open → Acknowledged → Fixed / Dismissed. */

const SEV = ['High', 'Medium', 'Low'];
const SEV_CLASS: Record<string, string> = { High: 'ctl-high', Medium: 'ctl-medium', Low: 'ctl-low' };
const STATUS = [['Active', 'Open & acknowledged'], ['Fixed', 'Fixed'], ['Dismissed', 'Dismissed'], ['', 'All']];
const money = (v: any) => (v === null || v === undefined || v === '' ? '' : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }));

function DocA({ dt, dn }: { dt?: string; dn?: string }) {
  const href = docLink(deskPrefix(), dt, dn);
  return href ? <a className="gl-doclink" href={href} target="_blank" rel="noopener noreferrer">{dn}</a> : <>{dn || ''}</>;
}

export default function ControlsApp() {
  const [companies, setCompanies] = useState<any[]>([]);
  const [company, setCompany] = useState('');
  const [status, setStatus] = useState('Active');
  const [severity, setSeverity] = useState('');
  const [data, setData] = useState<{ findings: any[]; counts: Record<string, number>; rules: any[] } | null>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [showRules, setShowRules] = useState(false);

  useEffect(() => { api.listCompanies().then((c: any[]) => { setCompanies(c || []); if (c?.length) setCompany(c[0].name); }).catch(() => {}); }, []);
  const load = useCallback(() => {
    if (!company) return;
    api.controlsFindings({ company, status, severity }).then((d) => { setData(d); setSel([]); })
      .catch((e: any) => setMsg(String(e?.message || e)));
  }, [company, status, severity]);
  useEffect(() => { load(); }, [load]);

  async function act(names: string[], st: string) {
    if (!names.length) return;
    const note = st === 'Dismissed' ? (prompt(t('Why is this acceptable? (kept with the finding)')) ?? null) : '';
    if (note === null) return;
    await api.controlsSetStatus(names, st, note || undefined);
    load();
  }
  async function runNow() {
    setBusy(true); setMsg('');
    try {
      const r = await api.controlsRun(company);
      setMsg(`${t('Run complete')}: ${r.new} ${t('new finding(s)')}${r.errors?.length ? ` · ${r.errors.length} ${t('check(s) failed — see Error Log')}` : ''}`);
      load();
    } catch (e: any) { setMsg(String(e?.message || e)); } finally { setBusy(false); }
  }

  const rows = data?.findings || [];
  return (
    <div className="ctl">
      <div className="ctl-bar">
        <select value={company} onChange={(e) => setCompany(e.target.value)}>
          {companies.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUS.map(([v, l]) => <option key={v} value={v}>{t(l)}</option>)}
        </select>
        <div className="ctl-sev">
          {SEV.map((s) => (
            <button type="button" key={s} className={'ctl-chip ' + SEV_CLASS[s] + (severity === s ? ' on' : '')}
              onClick={() => setSeverity(severity === s ? '' : s)}>{t(s)} · {data?.counts?.[s] || 0}</button>
          ))}
        </div>
        <button type="button" className="bk-btn bk-primary" disabled={busy || !company} onClick={runNow}>{busy ? t('Running…') : t('Run now')}</button>
        <button type="button" className="bk-btn" onClick={() => setShowRules((v) => !v)}>{t('Rules')}</button>
        {msg && <span className="bk-hint">{msg}</span>}
      </div>

      {showRules && data && <RulesPanel rules={data.rules} onSaved={load} />}

      {!!sel.length && (
        <div className="ctl-bulk">
          {sel.length} {t('selected')}:
          <button type="button" className="bk-btn" onClick={() => act(sel, 'Acknowledged')}>{t('Acknowledge')}</button>
          <button type="button" className="bk-btn" onClick={() => act(sel, 'Fixed')}>{t('Mark fixed')}</button>
          <button type="button" className="bk-btn" onClick={() => act(sel, 'Dismissed')}>{t('Dismiss…')}</button>
          <button type="button" className="bk-btn" onClick={() => act(sel, 'Open')}>{t('Reopen')}</button>
        </div>
      )}

      <div className="cp-table-wrap ctl-table">
        <table className="cp-table">
          <thead><tr>
            <th><input type="checkbox" checked={!!rows.length && sel.length === rows.length}
              onChange={(e) => setSel(e.target.checked ? rows.map((r) => r.name) : [])} /></th>
            <th>{t('Severity')}</th><th>{t('Finding')}</th><th>{t('Document')}</th><th>{t('Related')}</th>
            <th className="num">{t('Amount')}</th><th>{t('First seen')}</th><th>{t('Last seen')}</th><th>{t('Status')}</th><th />
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name} title={r.details || ''}>
                <td><input type="checkbox" checked={sel.includes(r.name)}
                  onChange={(e) => setSel((s) => (e.target.checked ? [...s, r.name] : s.filter((x) => x !== r.name)))} /></td>
                <td><span className={'ctl-chip ' + (SEV_CLASS[r.severity] || '')}>{t(r.severity)}</span></td>
                <td>{r.title}{r.note ? <div className="ctl-note">{r.note}</div> : null}</td>
                <td><DocA dt={r.reference_doctype} dn={r.reference_name} /></td>
                <td><DocA dt={r.related_doctype} dn={r.related_name} /></td>
                <td className="num">{money(r.amount)}</td>
                <td>{String(r.first_seen || '').slice(0, 10)}</td>
                <td>{String(r.last_seen || '').slice(0, 10)}</td>
                <td>{t(r.status)}</td>
                <td className="ctl-acts">
                  {r.status === 'Open' && <button type="button" className="cp-link" onClick={() => act([r.name], 'Acknowledged')}>{t('Acknowledge')}</button>}
                  {(r.status === 'Open' || r.status === 'Acknowledged') && <button type="button" className="cp-link" onClick={() => act([r.name], 'Dismissed')}>{t('Dismiss')}</button>}
                  {(r.status === 'Fixed' || r.status === 'Dismissed') && <button type="button" className="cp-link" onClick={() => act([r.name], 'Open')}>{t('Reopen')}</button>}
                </td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={10} className="bk-hint">{t('No findings here. Run now to check the books.')}</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RulesPanel({ rules, onSaved }: { rules: any[]; onSaved: () => void }) {
  const [edit, setEdit] = useState<Record<string, any>>({});
  const val = (r: any, k: string) => (edit[r.name]?.[k] ?? r[k]);
  const set = (r: any, k: string, v: any) => setEdit((e) => ({ ...e, [r.name]: { ...(e[r.name] || {}), [k]: v } }));
  return (
    <div className="card ctl-rules">
      <table className="cp-table">
        <thead><tr><th>{t('On')}</th><th>{t('Control')}</th><th>{t('Severity')}</th><th>{t('Threshold')}</th>
          <th>{t('Look back (days)')}</th><th /></tr></thead>
        <tbody>{rules.map((r) => (
          <tr key={r.name} title={r.description}>
            <td><input type="checkbox" checked={!!Number(val(r, 'enabled'))} onChange={(e) => set(r, 'enabled', e.target.checked ? 1 : 0)} /></td>
            <td>{r.rule_title}<div className="ctl-note">{r.threshold_label}</div></td>
            <td><select value={val(r, 'severity')} onChange={(e) => set(r, 'severity', e.target.value)}>
              {SEV.map((s) => <option key={s} value={s}>{t(s)}</option>)}</select></td>
            <td><input type="number" value={val(r, 'threshold')} onChange={(e) => set(r, 'threshold', e.target.value)} /></td>
            <td><input type="number" value={val(r, 'lookback_days')} onChange={(e) => set(r, 'lookback_days', e.target.value)} /></td>
            <td>{edit[r.name] && <button type="button" className="bk-btn bk-primary" onClick={async () => {
              await api.controlsSaveRule({ name: r.name, ...edit[r.name] });
              setEdit((e) => { const n = { ...e }; delete n[r.name]; return n; });
              onSaved();
            }}>{t('Save')}</button>}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
