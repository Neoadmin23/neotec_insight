import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { fmtD } from '../../utils/format';

/** Period adjustments (v2.29.0) — the accountant's green/red Excel rows as a
 *  governed feature: include an out-of-period invoice (paid this quarter) or
 *  defer an in-period one (unpaid), each with a mandatory reason. The return,
 *  drill-downs and Export Pack registers all obey; packs render included rows
 *  green and deferred rows red with an automated reconciliation footer. */
export default function VatAdjustments({ company, fromDate, toDate, onClose }: {
  company: string; fromDate: string; toDate: string; onClose: () => void;
}) {
  const [list, setList] = useState<any[]>([]);
  const [vtype, setVtype] = useState<'Sales Invoice' | 'Purchase Invoice'>('Sales Invoice');
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<any[]>([]);
  const [picked, setPicked] = useState<any>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const refresh = () => api.listVatAdjustments(company || null, fromDate, toDate).then(setList).catch(() => {});
  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, [company, fromDate, toDate]);

  async function search() {
    setMsg(''); setPicked(null);
    try { setHits(await api.vatFindVouchers(company || null, vtype, query)); }
    catch (e: any) { setMsg(String(e?.message || e)); }
  }

  const inPeriod = (d: string) => fromDate <= String(d) && String(d) <= toDate;

  async function save() {
    if (!picked) return;
    if (!reason.trim()) { setMsg(t('A reason is required — it is the audit trail.')); return; }
    setBusy(true); setMsg('');
    try {
      await api.saveVatAdjustment({
        company: company || null, from_date: fromDate, to_date: toDate,
        voucher_type: vtype, voucher_no: picked.name,
        action: inPeriod(picked.posting_date) ? 'Exclude' : 'Include',
        reason: reason.trim(),
      });
      setPicked(null); setReason(''); setHits([]); setQuery('');
      refresh();
    } catch (e: any) { setMsg(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  async function remove(name: string) {
    await api.deleteVatAdjustment(name).catch(() => {});
    refresh();
  }

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel" role="dialog" aria-label={t('VAT period adjustments')} style={{ width: 'min(780px, 100%)' }}>
        <div className="theme-h">
          <h3>⇄ {t('VAT period adjustments')} <span className="vat-badge">{fromDate} → {toDate}</span></h3>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>
        <p className="theme-hint">
          {t('An invoice dated OUTSIDE the period is INCLUDED (e.g. a government invoice whose VAT falls due when paid — paid this quarter). An invoice dated INSIDE the period is EXCLUDED / deferred to its payment quarter. Every adjustment needs a reason and is kept as the audit trail; registers show included rows in green and deferred rows in red with an automatic reconciliation.')}
        </p>

        <div className="studio-frow" style={{ gap: 8 }}>
          <select value={vtype} onChange={(e) => { setVtype(e.target.value as any); setHits([]); setPicked(null); }}>
            <option value="Sales Invoice">{t('Sales Invoice')}</option>
            <option value="Purchase Invoice">{t('Purchase Invoice')}</option>
          </select>
          <input placeholder={t('Invoice number…')} value={query} style={{ flex: 1 }}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') search(); }} />
          <button className="studio-ghost" onClick={search}>{t('Search')}</button>
        </div>

        {hits.length > 0 && !picked && (
          <table className="studio-table" style={{ width: '100%', marginTop: 8 }}>
            <thead><tr><th>{t('Invoice')}</th><th>{t('Date')}</th><th>{t('Party')}</th><th className="num">{t('VAT')}</th><th /></tr></thead>
            <tbody>
              {hits.map((h) => (
                <tr key={h.name}>
                  <td>{h.name}</td>
                  <td>{String(h.posting_date)}</td>
                  <td>{h.party}</td>
                  <td className="num">{fmtD(h.base_total_taxes_and_charges, 2)}</td>
                  <td>
                    <button className="studio-ghost" onClick={() => { setPicked(h); setMsg(''); }}>
                      {inPeriod(h.posting_date) ? t('Defer out') : t('Include in period')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {picked && (
          <div className="studio-card" style={{ padding: 12, marginTop: 10 }}>
            <div style={{ marginBottom: 8 }}>
              <span className={'cls-badge' + (inPeriod(picked.posting_date) ? '' : '')}>
                {inPeriod(picked.posting_date) ? t('EXCLUDE (defer)') : t('INCLUDE (add)')}
              </span>{' '}
              <b>{picked.name}</b> · {String(picked.posting_date)} · {picked.party} · {t('VAT')} {fmtD(picked.base_total_taxes_and_charges, 2)}
            </div>
            <input placeholder={t('Reason — e.g. “SWA invoice 2025, paid in February 2026 — VAT due on payment”')}
              value={reason} onChange={(e) => setReason(e.target.value)} style={{ width: '100%' }} />
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <button className="studio-run" onClick={save} disabled={busy}>{busy ? t('Saving…') : t('Save adjustment')}</button>
              <button className="studio-ghost" onClick={() => setPicked(null)}>{t('Cancel')}</button>
            </div>
          </div>
        )}

        <div className="theme-sec-title" style={{ marginTop: 16 }}>{t('Adjustments for this period')}</div>
        <table className="studio-table" style={{ width: '100%' }}>
          <thead><tr><th>{t('Action')}</th><th>{t('Invoice')}</th><th>{t('Reason')}</th><th>{t('By')}</th><th /></tr></thead>
          <tbody>
            {list.map((a) => (
              <tr key={a.name}>
                <td><span className="cls-badge" style={a.action === 'Exclude' ? { background: 'var(--neg-bg)', borderColor: 'var(--neg)', color: 'var(--neg)' } : {}}>{t(a.action)}</span></td>
                <td>{a.voucher_no} <span className="cls-root">{t(a.voucher_type)}</span></td>
                <td className="sched-recip" title={a.reason}>{a.reason}</td>
                <td className="cls-type">{a.owner}</td>
                <td><button className="studio-ghost" onClick={() => remove(a.name)}>✕</button></td>
              </tr>
            ))}
            {list.length === 0 && <tr><td colSpan={5} className="studio-hint">{t('No adjustments for this period.')}</td></tr>}
          </tbody>
        </table>

        {msg && <div className="theme-err" style={{ marginTop: 8 }}>{msg}</div>}
      </div>
    </div>
  );
}
