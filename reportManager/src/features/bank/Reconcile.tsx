import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

const money = (v?: number) =>
  (v == null ? '—' : new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v));

const PASS_LABEL: Record<string, string> = {
  reference: 'Reference', invoice: 'Invoice', amount_date: 'Amount + date', bank_charge: 'Bank charge', none: 'No match',
};
const PASS_COLOR: Record<string, string> = {
  reference: '#15803d', invoice: '#0d9488', amount_date: '#b45309', bank_charge: '#7c3aed', none: '#9a948a',
};
const WINDOWS: Record<string, [number, number]> = { Tight: [1, 3], Standard: [3, 7], Wide: [7, 14] };

function AcctPicker({ label, company, value, onPick }: { label: string; company: string; value?: string; onPick: (v: string) => void }) {
  const [q, setQ] = useState(value || '');
  const [rows, setRows] = useState<any[]>([]);
  const run = async (val: string) => { setQ(val); try { setRows(await api.searchAccounts(company, val)); } catch { setRows([]); } };
  return (
    <div style={{ position: 'relative' }}>
      <div className="text-muted" style={{ fontSize: 12 }}>{label}{value ? ` · ${value}` : ''}</div>
      <input className="form-control" placeholder={t('Search account…')} value={q} style={{ width: 260 }}
             onChange={(e) => run(e.target.value)} onFocus={() => !rows.length && run('')} />
      {rows.length > 0 && (
        <div style={{ position: 'absolute', zIndex: 6, background: '#fff', width: 260, maxHeight: 150, overflow: 'auto', border: '1px solid #ddd', borderRadius: 6 }}>
          {rows.map((r) => (
            <div key={r.name} onClick={() => { onPick(r.name); setRows([]); setQ(r.name); }}
                 style={{ padding: '5px 8px', cursor: 'pointer', borderBottom: '1px solid #f3f3f3', fontSize: 12 }}>
              {r.account_number ? r.account_number + ' · ' : ''}{r.account_name || r.name}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Reconcile() {
  const [bankAccount, setBankAccount] = useState('');
  const [bankQ, setBankQ] = useState('');
  const [bankRows, setBankRows] = useState<any[]>([]);
  const [win, setWin] = useState<'Tight' | 'Standard' | 'Wide'>('Standard');
  const [data, setData] = useState<any>(null);
  const [view, setView] = useState<'open' | 'reconciled'>('open');
  const [reconciled, setReconciled] = useState<any>(null);
  const [chargesAcct, setChargesAcct] = useState('');
  const [vatAcct, setVatAcct] = useState('');
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => {
    api.getReconcileSettings().then((s) => {
      setChargesAcct(s.bank_charges_account || ''); setVatAcct(s.input_vat_account || '');
    }).catch(() => {});
  }, []);

  const searchBank = async (q: string) => {
    setBankQ(q);
    try { setBankRows(await api.searchBankAccounts('', q)); } catch { setBankRows([]); }
  };

  const run = async () => {
    if (!bankAccount) { setErr(t('Pick a bank account first.')); return; }
    setErr(''); setBusy(t('Finding matches…')); setView('open');
    try { const [wb, wa] = WINDOWS[win]; setData(await api.findMatches(bankAccount, '', '', wb, wa)); }
    catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(''); }
  };

  const loadReconciled = async () => {
    setErr(''); setBusy(t('Loading…')); setView('reconciled');
    try { setReconciled(await api.listReconciled(bankAccount)); }
    catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(''); }
  };

  const drop = (name: string) => setData((d: any) => ({ ...d, transactions: d.transactions.filter((x: any) => x.name !== name) }));

  const confirm = async (txn: any, cand: any) => {
    setErr('');
    try {
      if (cand.pass === 'bank_charge') {
        if (!chargesAcct) { setErr(t('Pick a Bank Charges account first (below).')); return; }
        await api.bookBankCharge(txn.name, chargesAcct, vatAcct || undefined, cand.fee, cand.vat);
      } else {
        await api.confirmMatch(txn.name, cand.voucher_type, cand.voucher_name);
      }
      drop(txn.name);
    } catch (e: any) { setErr(e.message || String(e)); }
  };

  const undo = async (name: string) => {
    setErr('');
    try { await api.unmatch(name); setReconciled((r: any) => ({ ...r, transactions: r.transactions.filter((x: any) => x.name !== name) })); }
    catch (e: any) { setErr(e.message || String(e)); }
  };

  const company = data?.company || '';

  return (
    <div>
      <div className="text-muted" style={{ fontSize: 13, marginBottom: 10 }}>
        {t('Match imported bank transactions to Payment Entries and Journal Entries. Nothing is reconciled until you confirm.')}
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 12 }}>
        <div style={{ position: 'relative' }}>
          <div className="text-muted" style={{ fontSize: 12 }}>{t('Bank account')}{bankAccount ? ` · ${bankAccount}` : ''}</div>
          <input className="form-control" placeholder={t('Search bank account…')} value={bankQ} style={{ width: 300 }}
                 onChange={(e) => searchBank(e.target.value)} onFocus={() => !bankRows.length && searchBank('')} />
          {bankRows.length > 0 && (
            <div style={{ position: 'absolute', zIndex: 5, background: '#fff', width: 300, maxHeight: 160, overflow: 'auto', border: '1px solid #ddd', borderRadius: 6 }}>
              {bankRows.map((r) => (
                <div key={r.name} onClick={() => { setBankAccount(r.name); setBankRows([]); setBankQ(r.account_name || r.name); }}
                     style={{ padding: '5px 8px', cursor: 'pointer', borderBottom: '1px solid #f3f3f3', fontSize: 13 }}>
                  <b>{r.account_name || r.name}</b><span className="text-muted"> · {r.bank}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div>
          <div className="text-muted" style={{ fontSize: 12 }}>{t('Match window')}</div>
          <select className="form-control" value={win} onChange={(e) => setWin(e.target.value as any)}>
            <option value="Tight">{t('Tight (−1 / +3 days)')}</option>
            <option value="Standard">{t('Standard (−3 / +7 days)')}</option>
            <option value="Wide">{t('Wide (−7 / +14 days)')}</option>
          </select>
        </div>
        <button className="btn btn-primary" disabled={!!busy} onClick={run}>{busy || t('Find Matches')}</button>
        {bankAccount && <button className="btn btn-default" disabled={!!busy} onClick={loadReconciled}>{t('View Reconciled')}</button>}
      </div>

      {/* charge accounts (remembered) */}
      {company && (
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', marginBottom: 12, flexWrap: 'wrap', background: '#faf8f3', border: '1px solid #efe9dc', borderRadius: 8, padding: 10 }}>
          <AcctPicker label={t('Bank Charges account')} company={company} value={chargesAcct}
                      onPick={(v) => { setChargesAcct(v); api.setReconcileSettings(v, vatAcct).catch(() => {}); }} />
          <AcctPicker label={t('Input VAT account (recoverable)')} company={company} value={vatAcct}
                      onPick={(v) => { setVatAcct(v); api.setReconcileSettings(chargesAcct, v).catch(() => {}); }} />
          <span className="text-muted" style={{ fontSize: 11 }}>{t('Used when booking bank fees. Remembered for next time.')}</span>
        </div>
      )}

      {err && <div style={{ background: '#fdecea', color: '#b91c1c', padding: '8px 12px', borderRadius: 6, marginBottom: 10 }}>{err}</div>}

      {view === 'open' && data && (
        <>
          <div style={{ display: 'flex', gap: 16, marginBottom: 10, fontSize: 13, flexWrap: 'wrap' }}>
            <span>{t('Unreconciled')}: <b>{data.transaction_count}</b></span>
            <span style={{ color: PASS_COLOR.reference }}>{t('Reference')}: <b>{data.pass_counts.reference}</b></span>
            <span style={{ color: PASS_COLOR.invoice }}>{t('Invoice')}: <b>{data.pass_counts.invoice}</b></span>
            <span style={{ color: PASS_COLOR.amount_date }}>{t('Amount+date')}: <b>{data.pass_counts.amount_date}</b></span>
            <span style={{ color: PASS_COLOR.none }}>{t('No match')}: <b>{data.pass_counts.none}</b></span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {data.transactions.map((txn: any) => (
              <div key={txn.name} style={{ border: '1px solid #e6e0d4', borderRadius: 8, padding: 10 }}>
                <div style={{ fontSize: 13 }}>
                  <span style={{ color: txn.direction === 'Incoming' ? '#15803d' : '#b91c1c', fontWeight: 700 }}>
                    {txn.direction === 'Incoming' ? '▾ ' : '▴ '}{money(txn.amount)}
                  </span>
                  <span className="text-muted"> · {txn.date} · </span><code style={{ fontSize: 11 }}>{txn.reference || '—'}</code>
                  {txn.draft && <span style={{ fontSize: 10, background: '#fef3c7', color: '#92400e', padding: '1px 5px', borderRadius: 4, marginLeft: 6 }}>{t('draft')}</span>}
                  <div className="text-muted" style={{ fontSize: 11 }}>{txn.description}</div>
                </div>
                <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {txn.candidates.length === 0 && <div className="text-muted" style={{ fontSize: 12 }}>{t('No candidate entries found — book it manually or widen the window.')}</div>}
                  {txn.candidates.map((c: any, i: number) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, background: i === 0 ? '#f7f5f0' : undefined, padding: '4px 6px', borderRadius: 6 }}>
                      <div style={{ fontSize: 12 }}>
                        <span style={{ fontSize: 10, color: '#fff', background: PASS_COLOR[c.pass], padding: '1px 6px', borderRadius: 4 }}>{t(PASS_LABEL[c.pass])}</span>
                        {c.pass === 'bank_charge'
                          ? <span style={{ marginLeft: 6 }}>{t('Fee')} <b>{money(c.fee)}</b> + {t('VAT')} <b>{money(c.vat)}</b></span>
                          : <span style={{ marginLeft: 6 }}>{c.voucher_type === 'Payment Entry' ? 'PE' : 'JE'} <b>{c.voucher_name}</b><span className="text-muted"> · {money(c.amount)}{c.amount_matches ? '' : ' ⚠'} · {c.date}{c.party ? ' · ' + c.party : ''}</span></span>}
                        <div className="text-muted" style={{ fontSize: 11 }}>{c.why}</div>
                      </div>
                      <button className="btn btn-xs btn-primary" onClick={() => confirm(txn, c)}>{c.pass === 'bank_charge' ? t('Book charge') : t('Confirm')}</button>
                    </div>
                  ))}
                </div>
              </div>
            ))}
            {!data.transactions.length && <div className="text-muted" style={{ padding: 16, textAlign: 'center' }}>{t('All clear — nothing left to reconcile in this range.')}</div>}
          </div>
        </>
      )}

      {view === 'reconciled' && reconciled && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div className="text-muted" style={{ fontSize: 13 }}>{t('Reconciled')}: <b>{reconciled.count}</b></div>
          {reconciled.transactions.map((txn: any) => (
            <div key={txn.name} style={{ border: '1px solid #e6e0d4', borderRadius: 8, padding: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ fontSize: 12 }}>
                <span style={{ color: txn.direction === 'Incoming' ? '#15803d' : '#b91c1c', fontWeight: 700 }}>
                  {txn.direction === 'Incoming' ? '▾ ' : '▴ '}{money(txn.amount)}
                </span>
                <span className="text-muted"> · {txn.date} · </span><code style={{ fontSize: 11 }}>{txn.reference || '—'}</code>
                <span className="text-muted"> → </span>
                {txn.vouchers.map((v: any, i: number) => (
                  <span key={i}>{v.type === 'Payment Entry' ? 'PE' : 'JE'} <b>{v.name}</b> ({money(v.amount)}){i < txn.vouchers.length - 1 ? ', ' : ''}</span>
                ))}
              </div>
              <button className="btn btn-xs btn-default" onClick={() => undo(txn.name)}>{t('Unmatch')}</button>
            </div>
          ))}
          {!reconciled.transactions.length && <div className="text-muted" style={{ padding: 16, textAlign: 'center' }}>{t('Nothing reconciled yet.')}</div>}
        </div>
      )}
    </div>
  );
}
