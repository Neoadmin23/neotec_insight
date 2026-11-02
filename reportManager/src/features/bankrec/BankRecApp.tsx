import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { deskPrefix } from '../../utils/desk';
import { docLink } from '../../utils/statementCore';
import { FormatMapper } from './FormatMapper';

/* Bank reconciliation (v3.6.0): read a statement PDF, prove every balance,
 * create Bank Transactions, match (rules, then validated AI suggestions),
 * review, draft fee entries, reconcile through ERPNext's own tool. */

const money = (v: any) => (Number(v) ? Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '');
const ST: Record<string, string> = { Unmatched: 'br-un', Suggested: 'br-sug', Accepted: 'br-acc', Skipped: 'br-skip', Reconciled: 'br-rec' };

export default function BankRecApp() {
  const [accounts, setAccounts] = useState<any[]>([]);
  const [account, setAccount] = useState('');
  const [list, setList] = useState<any[]>([]);
  const [s, setS] = useState<any>(null);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');
  const [filter, setFilter] = useState('');
  const [sel, setSel] = useState<string[]>([]);
  // v3.6.2 — bank formats
  const [formats, setFormats] = useState<any[]>([]);
  const [format, setFormat] = useState('');
  const [mapper, setMapper] = useState(false);
  const loadFormats = (pick?: string) => api.bankrec('list_formats').then((f) => { setFormats(f || []); setFormat((cur) => pick || cur || f?.[0]?.name || ''); }).catch(() => {});
  useEffect(() => { loadFormats(); }, []);

  useEffect(() => { api.bankrec('bank_accounts').then((a) => { setAccounts(a || []); if (a?.length) setAccount(a[0].name); }).catch((e) => setMsg(String(e?.message || e))); }, []);
  useEffect(() => { if (account) api.bankrec('statements', { bank_account: account }).then(setList).catch(() => {}); }, [account, s?.status]);

  async function run(label: string, fn: () => Promise<any>) {
    setBusy(label); setMsg('');
    try {
      const r = await fn();
      if (r?.lines) setS(r);
      const extra = [r?.created !== undefined && `${r.created} ${t('transactions created')}`,
        r?.rule_matches !== undefined && `${r.rule_matches} ${t('rule matches')}, ${r.ai_matches} ${t('AI suggestions')}`,
        r?.reconciled !== undefined && `${r.reconciled} ${t('reconciled')}`, r?.drafts && `${r.drafts.length} ${t('draft entries')}`,
        r?.errors?.length && r.errors.join(' · ')].filter(Boolean).join(' · ');
      if (extra) setMsg(extra);
      setSel([]);
    } catch (e: any) { setMsg(String(e?.message || e)); } finally { setBusy(''); }
  }
  async function upload(f: File) {
    await run(t('Reading…'), async () => {
      const url = await api.uploadImage(f);
      return api.bankrec('create_statement', { bank_account: account, file_url: url, bank_format: format }, true);
    });
  }
  const lines = (s?.lines || []).filter((l: any) => !filter || l.match_status === filter);
  const act = (status: string) => run('…', () => api.bankrec('set_lines', { name: s.name, lines: JSON.stringify(sel), status }, true));

  return (
    <div className="br">
      <div className="br-bar">
        <select value={account} onChange={(e) => { setAccount(e.target.value); setS(null); }}>
          {accounts.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
        </select>
        <select value={format} onChange={(e) => setFormat(e.target.value)} title={t('Bank format')}>
          {formats.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
        </select>
        <button type="button" className="bk-btn" onClick={() => setMapper(true)}>{t('Bank formats…')}</button>
        <label className="bk-btn bk-primary">{busy === t('Reading…') ? busy : t('Upload statement PDF')}
          <input type="file" accept="application/pdf" hidden disabled={!account || !!busy}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ''; }} /></label>
        <select value={s?.name || ''} onChange={(e) => e.target.value && run('…', () => api.bankrec('summary', { name: e.target.value }))}>
          <option value="">{t('Open a statement…')}</option>
          {list.map((x) => <option key={x.name} value={x.name}>{x.from_date} → {x.to_date} · {t(x.status)}</option>)}
        </select>
        {msg && <span className="bk-hint">{msg}</span>}
      </div>
      {mapper && <FormatMapper onClose={() => setMapper(false)} onSaved={(n) => { loadFormats(n); setMsg(`${t('Saved format')}: ${n}`); }} />}
      {!accounts.length && <p className="bk-hint">{t('No company bank account: on a Bank Account tick “Is Company Account” and set its GL account.')}</p>}
      {s && (
        <>
          <div className="br-proof">
            <div><span>{t('Period')}</span><b>{s.from_date} → {s.to_date}</b></div>
            <div><span>{t('Opening')}</span><b>{money(s.opening)}</b></div>
            <div><span>{t('Credits')}</span><b>{money(s.credits)}</b></div>
            <div><span>{t('Debits')}</span><b>{money(s.debits)}</b></div>
            <div><span>{t('Closing')}</span><b>{money(s.closing)}</b></div>
            <div className={s.balance_errors ? 'br-bad' : 'br-good'}><span>{t('Balance proof')}</span>
              <b>{s.balance_errors ? `${s.balance_errors} ${t('line(s) break the chain')}` : `✓ ${s.lines.length} ${t('lines proven')}`}</b></div>
          </div>
          <div className="br-bar">
            <button type="button" className="bk-btn" disabled={!!busy || s.balance_errors > 0}
              onClick={() => run('…', () => api.bankrec('create_transactions', { name: s.name }, true))}>1 · {t('Create Bank Transactions')}</button>
            <button type="button" className="bk-btn" disabled={!!busy}
              onClick={() => run('…', () => api.bankrec('find_matches', { name: s.name, use_ai: 1 }, true))}>2 · {t('Find matches')}</button>
            <button type="button" className="bk-btn" disabled={!!busy}
              onClick={() => run('…', () => api.bankrec('set_lines', { name: s.name, threshold: 90 }, true))}>3 · {t('Accept all ≥ 90%')}</button>
            <button type="button" className="bk-btn bk-primary" disabled={!!busy}
              onClick={() => run('…', () => api.bankrec('reconcile', { name: s.name }, true))}>4 · {t('Reconcile accepted')}</button>
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">{t('All lines')} ({s.lines.length})</option>
              {Object.entries(s.counts || {}).map(([k, n]) => <option key={k} value={k}>{t(k)} ({String(n)})</option>)}
            </select>
          </div>
          {!!sel.length && (
            <div className="br-bar">
              {sel.length} {t('selected')}:
              <button type="button" className="bk-btn" onClick={() => act('Accepted')}>{t('Accept')}</button>
              <button type="button" className="bk-btn" onClick={() => act('Skipped')}>{t('Skip')}</button>
              <button type="button" className="bk-btn" onClick={() => act('Unmatched')}>{t('Reset')}</button>
              <button type="button" className="bk-btn" onClick={() => {
                const acc = prompt(t('Bank charges account (as in the chart of accounts)'), s.charges_account || '');
                if (acc) run('…', () => api.bankrec('draft_charges', { name: s.name, lines: JSON.stringify(sel), account: acc }, true));
              }}>{t('Draft entries (e.g. bank fees)')}</button>
            </div>
          )}
          <div className="cp-table-wrap br-table">
            <table className="cp-table">
              <thead><tr>
                <th><input type="checkbox" checked={!!lines.length && sel.length === lines.length}
                  onChange={(e) => setSel(e.target.checked ? lines.map((l: any) => l.name) : [])} /></th>
                <th>{t('Date')}</th><th>{t('Reference')}</th><th>{t('Details')}</th>
                <th className="num">{t('Credit')}</th><th className="num">{t('Debit')}</th><th className="num">{t('Balance')}</th>
                <th>{t('Matched entries')}</th><th className="num">%</th><th>{t('Status')}</th>
              </tr></thead>
              <tbody>{lines.map((l: any) => {
                const m: [string, string, number][] = l.match_json ? JSON.parse(l.match_json) : [];
                return (
                  <tr key={l.name} className={l.balance_ok ? '' : 'br-row-bad'}>
                    <td><input type="checkbox" checked={sel.includes(l.name)} onChange={(e) => setSel((x) => (e.target.checked ? [...x, l.name] : x.filter((y) => y !== l.name)))} /></td>
                    <td>{l.posting_date}</td>
                    <td>{l.reference}<div className="ctl-note">{l.txn_type}</div></td>
                    <td className="br-details" title={l.details}>{String(l.details || '').slice(0, 110)}</td>
                    <td className="num">{money(l.credit)}</td><td className="num">{money(l.debit)}</td><td className="num">{money(l.balance)}</td>
                    <td>{m.map(([dt, dn]) => <div key={dn}><a className="gl-doclink" href={docLink(deskPrefix(), dt, dn)} target="_blank" rel="noopener noreferrer">{dn}</a></div>)}
                      {l.match_source && <div className="ctl-note">{l.match_source}</div>}</td>
                    <td className="num">{l.confidence ? Math.round(l.confidence) : ''}</td>
                    <td><span className={'ctl-chip ' + (ST[l.match_status] || '')}>{t(l.match_status)}</span></td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
