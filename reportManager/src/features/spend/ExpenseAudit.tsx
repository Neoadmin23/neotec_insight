import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

/* Expense Audit (v3.7.0) — company spending from imported bank statements.
 * Step 1 clean · Step 2 first look. Every number is computed on the server. */

const money = (v: any) => Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const firstOfYear = () => `${new Date().getFullYear()}-01-01`;
const today = () => new Date().toISOString().slice(0, 10);

export default function ExpenseAudit() {
  const [companies, setCompanies] = useState<any[]>([]);
  const [company, setCompany] = useState('');
  const [from, setFrom] = useState(firstOfYear());
  const [to, setTo] = useState(today());
  const [d, setD] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [open, setOpen] = useState('');

  useEffect(() => { api.listCompanies().then((c: any[]) => { setCompanies(c || []); if (c?.length) setCompany(c[0].name); }).catch(() => {}); }, []);
  async function run() {
    setBusy(true); setErr('');
    try { setD(await api.spendAudit(company, from, to)); } catch (e: any) { setErr(String(e?.message || e)); setD(null); }
    finally { setBusy(false); }
  }
  function csv() {
    const head = ['date', 'account', 'merchant', 'via', 'centre', 'kind', 'direction', 'amount', 'currency'];
    const esc = (x: any) => { const s = String(x ?? ''); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const body = [head.join(','), ...d.rows.map((r: any) => head.map((k) => esc(r[k])).join(','))].join('\n');
    const url = URL.createObjectURL(new Blob(['\ufeff' + body], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = `expense-audit-${d.from_date}-${d.to_date}.csv`; a.click(); URL.revokeObjectURL(url);
  }
  const maxC = d?.centres?.[0]?.total || 1;

  return (
    <div className="ea">
      <div className="br-bar">
        <select value={company} onChange={(e) => setCompany(e.target.value)}>
          {companies.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
        </select>
        <label>{t('From')} <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>{t('To')} <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <button type="button" className="bk-btn bk-primary" disabled={busy || !company} onClick={run}>{busy ? t('Auditing…') : t('Run audit')}</button>
        {d && <button type="button" className="bk-btn" onClick={csv}>{t('Download cleaned table (CSV)')}</button>}
        {d && <span className="bk-hint">{d.lines} {t('lines')} · {d.from_date} → {d.to_date}</span>}
      </div>
      <p className="bk-hint">{t('Company bank and card statements imported in Operations → Reconcile. Every figure is computed from the statement lines.')}</p>
      {err && <div className="alloc-warn">{err}</div>}
      {d && (
        <>
          <h3 className="ea-h">{t('Step 1 · Clean')}</h3>
          <div className="cp-table-wrap"><table className="cp-table">
            <thead><tr><th>{t('Account')}</th><th>{t('Bank')}</th><th>{t('Currency')}</th><th>{t('First')}</th><th>{t('Last')}</th><th className="num">{t('Lines')}</th></tr></thead>
            <tbody>{d.accounts.map((a: any) => <tr key={a.account}><td>{a.account}</td><td>{a.bank}</td><td>{a.currency}</td><td>{a.first}</td><td>{a.last}</td><td className="num">{a.count}</td></tr>)}</tbody>
          </table></div>
          <p className="bk-hint">
            {t('Moved between your own accounts (removed from spending and income)')}: <b>{d.internal.count}</b> · {money(d.internal.total)}
            {d.card_payments.length > 0 && <> · <span className="fm-bad">{d.card_payments.length} {t('card payment(s) with no card statement imported')}</span></>}
          </p>

          <h3 className="ea-h">{t('Step 2 · First look')}</h3>
          <div className="br-proof">
            <div><span>{t('Money in')}</span><b>{money(d.money_in)}</b></div>
            <div><span>{t('Money out')}</span><b>{money(d.money_out)}</b></div>
            <div className={d.left < 0 ? 'br-bad' : 'br-good'}><span>{t('Left')}</span><b>{money(d.left)}</b></div>
          </div>
          <div className="cp-table-wrap"><table className="cp-table">
            <thead><tr><th>{t('Month')}</th><th className="num">{t('In')}</th><th className="num">{t('Out')}</th><th className="num">{t('Left')}</th></tr></thead>
            <tbody>{d.monthly.map((m: any) => <tr key={m.month}><td>{m.month}</td><td className="num">{money(m.in)}</td><td className="num">{money(m.out)}</td>
              <td className="num" style={{ color: m.left < 0 ? '#b91c1c' : undefined }}>{money(m.left)}</td></tr>)}</tbody>
          </table></div>

          <h4 className="pl-h">{t('Cost centres — biggest first')}</h4>
          {d.centres.map((c: any) => (
            <div key={c.centre} className="ea-centre">
              <button type="button" className="ea-row" onClick={() => setOpen(open === c.centre ? '' : c.centre)}>
                <span className="ea-name">{t(c.centre)} <small>({c.count})</small></span>
                <span className="ea-bar"><i style={{ width: `${Math.max(2, (c.total / maxC) * 100)}%` }} /></span>
                <b>{money(c.total)}</b>
              </button>
              {open === c.centre && (
                <ul className="ea-merchants">{c.merchants.map((m: any) => <li key={m.merchant}><span>{m.merchant}</span><b>{money(m.total)}</b></li>)}</ul>
              )}
            </div>
          ))}

          <h4 className="pl-h">{t('Recurring charges')}</h4>
          {d.recurring.length ? (
            <div className="cp-table-wrap"><table className="cp-table">
              <thead><tr><th>{t('Payee')}</th><th>{t('Every')}</th><th className="num">{t('Price')}</th><th className="num">{t('Per year')}</th>
                <th>{t('Last')}</th><th>{t('Next renewal')}</th><th>{t('Note')}</th></tr></thead>
              <tbody>{d.recurring.map((r: any, i: number) => <tr key={i}><td>{r.merchant}{r.via ? ` · ${t('via')} ${r.via}` : ''}</td><td>{t(r.period)}</td>
                <td className="num">{money(r.price)}</td><td className="num">{money(r.per_year)}</td><td>{r.last}</td><td>{r.next}</td>
                <td>{r.price_rise ? `${t('price up')} ${money(r.price_rise)}` : ''}</td></tr>)}</tbody>
            </table></div>
          ) : <p className="bk-hint">{t('No repeating charges yet — recurring charges show once at least two periods of statements are imported.')}</p>}

          {d.middlemen.length > 0 && (
            <>
              <h4 className="pl-h">{t('Charges billed through a middleman (Apple, Google, PayPal, Amazon…)')}</h4>
              <ul className="ea-merchants">{d.middlemen.map((m: any, i: number) => (
                <li key={i}><span>{m.via} · {t('day')} {m.day} · {m.count}×</span><b>{money(m.amount)}</b></li>))}</ul>
            </>
          )}
        </>
      )}
    </div>
  );
}
