import { useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { PrintBar } from '../PrintBar';

const money = (v?: number) =>
  (v == null ? '—' : new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v));

function esc(s: string) {
  return (s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));
}

function Card({ label, value, color, sub }: { label: string; value: any; color?: string; sub?: string }) {
  return (
    <div style={{ border: '1px solid #e6e0d4', borderRadius: 10, padding: '12px 16px', minWidth: 160 }}>
      <div className="text-muted" style={{ fontSize: 12 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
      {sub && <div className="text-muted" style={{ fontSize: 11 }}>{sub}</div>}
    </div>
  );
}

export default function ReconcileReport() {
  const [bankAccount, setBankAccount] = useState('');
  const [bankQ, setBankQ] = useState('');
  const [bankRows, setBankRows] = useState<any[]>([]);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [s, setS] = useState<any>(null);
  const [rep, setRep] = useState<any>(null);
  const [bridge, setBridge] = useState<any>(null);
  const [stmtBal, setStmtBal] = useState('');
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const buildBody = () => {
    if (!rep) return '';
    const period = rep.from_date ? `${rep.from_date} → ${rep.to_date}` : t('All dates');
    const bridgeHtml = bridge ? `
      <table class="bridge"><tbody>
        <tr class="b-strong"><td>${esc(t('Balance as per Books (General Ledger)'))}</td><td class="bnum">${money(bridge.book_balance)}</td></tr>
        <tr><td>${esc(t('Add: Payments issued but not yet presented'))}</td><td class="bnum">${money(bridge.outstanding_payments)}</td></tr>
        <tr><td>${esc(t('Less: Deposits in transit'))}</td><td class="bnum">${money(-bridge.deposits_in_transit)}</td></tr>
        <tr><td>${esc(t('Add: Bank credits not yet in books'))}</td><td class="bnum">${money(bridge.bank_credits_unbooked)}</td></tr>
        <tr><td>${esc(t('Less: Bank charges/debits not yet in books'))}</td><td class="bnum">${money(-bridge.bank_charges_unbooked)}</td></tr>
        <tr class="b-strong"><td>${esc(t('Expected balance as per Bank Statement'))}</td><td class="bnum">${money(bridge.expected_bank_balance)}</td></tr>
      </tbody></table>` : '';
    const cols = ['#', t('Value Date'), t('Bank Reference'), t('Amount'), t('Document Type'),
      t('Document No'), t('Document Date'), t('Cleared Date'), t('Party'), t('Status')];
    const body = rep.rows.map((r: any, i: number) => `
      <tr>
        <td>${i + 1}</td><td>${esc(r.value_date)}</td><td>${esc(r.bank_reference || '—')}</td>
        <td class="num ${r.direction === 'Incoming' ? 'in' : 'out'}">${money(r.amount)}</td>
        <td>${esc(r.voucher_type || '—')}</td><td><b>${esc(r.voucher_no || '—')}</b></td>
        <td>${esc(r.voucher_date || '—')}</td><td>${esc(r.cleared_date || '—')}</td>
        <td>${esc(r.party || '—')}</td>
        <td class="${r.reconciled ? 'in' : 'out'}">${r.reconciled ? esc(t('Reconciled')) : esc(t('Open'))}</td>
      </tr>`).join('');
    return `
      <style>
        .num{text-align:right}.in{color:#15803d}.out{color:#b91c1c}
        .bridge{width:60%;border-collapse:collapse;font-size:12px;margin-bottom:14px}
        .bridge td{padding:4px 8px;border-bottom:1px solid #eee}
        .bridge .b-strong td{font-weight:700;border-top:1px solid #333;border-bottom:2px solid #333}
        .bridge .bnum{text-align:right;font-variant-numeric:tabular-nums}
        tfoot td{font-weight:700;border-top:2px solid #333}
      </style>
      <div style="font-size:11px;margin-bottom:8px;color:#555">${esc(rep.bank_account)} · ${esc(period)} · ${esc(t('Reconciled'))}: ${rep.reconciled_count}/${rep.row_count} · ${money(rep.reconciled_value)}</div>
      ${bridgeHtml}
      <table><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead>
        <tbody>${body}</tbody>
        <tfoot><tr><td colspan="3">${esc(t('Reconciled value'))}</td><td class="num">${money(rep.reconciled_value)}</td><td colspan="6"></td></tr></tfoot>
      </table>`;
  };

  const searchBank = async (q: string) => {
    setBankQ(q);
    try { setBankRows(await api.searchBankAccounts('', q)); } catch { setBankRows([]); }
  };
  const run = async () => {
    if (!bankAccount) { setErr(t('Pick a bank account first.')); return; }
    setErr(''); setBusy(t('Loading…'));
    try {
      const [sum, report, br] = await Promise.all([
        api.reconciliationSummary(bankAccount, from, to),
        api.reconciliationReport(bankAccount, from, to, 1),
        api.reconciliationBridge(bankAccount, to || ''),
      ]);
      setS(sum); setRep(report); setBridge(br);
    } catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(''); }
  };

  return (
    <div>
      <div className="text-muted" style={{ fontSize: 13, marginBottom: 10 }}>
        {t('Bank reconciliation status — reconciled coverage, recoverable input VAT on bank charges, and incoming/outgoing for the period. These figures feed the CEO/CFO views.')}
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: 12 }}>
        <div style={{ position: 'relative' }}>
          <div className="text-muted" style={{ fontSize: 12 }}>{t('Bank account')}</div>
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
        <div><div className="text-muted" style={{ fontSize: 12 }}>{t('From')}</div><input type="date" className="form-control" value={from} onChange={(e) => setFrom(e.target.value)} /></div>
        <div><div className="text-muted" style={{ fontSize: 12 }}>{t('To')}</div><input type="date" className="form-control" value={to} onChange={(e) => setTo(e.target.value)} /></div>
        <button className="btn btn-primary" disabled={!!busy} onClick={run}>{busy || t('Run')}</button>
      </div>

      {err && <div style={{ background: '#fdecea', color: '#b91c1c', padding: '8px 12px', borderRadius: 6, marginBottom: 10 }}>{err}</div>}

      {s && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <Card label={t('Reconciled')} value={`${s.reconciled_pct}%`} color="#15803d"
                sub={`${s.reconciled_count} ${t('of')} ${s.reconciled_count + s.open_count} · ${money(s.reconciled_value)}`} />
          <Card label={t('Open / unreconciled')} value={s.open_count} color={s.open_count ? '#b45309' : '#15803d'} sub={money(s.open_value)} />
          <Card label={t('Bank charges')} value={money(s.bank_charges)} color="#b91c1c" />
          <Card label={t('Recoverable input VAT')} value={money(s.recoverable_input_vat)} color="#0d9488" sub={t('on bank charges (ZATCA)')} />
          <Card label={t('Incoming')} value={money(s.incoming_value)} color="#15803d" />
          <Card label={t('Outgoing')} value={money(s.outgoing_value)} color="#b91c1c" />
        </div>
      )}

      {bridge && (() => {
        const diff = stmtBal !== '' ? Math.round((parseFloat(stmtBal) - bridge.expected_bank_balance) * 100) / 100 : null;
        const Row = ({ label, val, op, strong }: any) => (
          <tr style={{ borderBottom: strong ? '2px solid #333' : '1px solid #f0ece3' }}>
            <td style={{ padding: '6px 10px', fontWeight: strong ? 700 : 400 }}>{op ? <span className="text-muted" style={{ marginInlineEnd: 6 }}>{op}</span> : ''}{label}</td>
            <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: strong ? 700 : 400, fontVariantNumeric: 'tabular-nums' }}>{money(val)}</td>
          </tr>
        );
        return (
          <div style={{ marginTop: 18, border: '1px solid #e6e0d4', borderRadius: 10, padding: 16, maxWidth: 560 }}>
            <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 2 }}>{t('Balance bridge (book → bank)')}</div>
            <div className="text-muted" style={{ fontSize: 11, marginBottom: 10 }}>{bridge.bank_account} · {t('as of')} {bridge.as_of}</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <tbody>
                <Row label={t('Balance as per Books (General Ledger)')} val={bridge.book_balance} strong />
                <Row label={t('Payments issued but not yet presented')} val={bridge.outstanding_payments} op="Add:" />
                <Row label={t('Deposits in transit (not yet credited)')} val={-bridge.deposits_in_transit} op="Less:" />
                <Row label={t('Bank credits not yet in books')} val={bridge.bank_credits_unbooked} op="Add:" />
                <Row label={t('Bank charges/debits not yet in books')} val={-bridge.bank_charges_unbooked} op="Less:" />
                <Row label={t('Expected balance as per Bank Statement')} val={bridge.expected_bank_balance} strong />
              </tbody>
            </table>
            <div style={{ marginTop: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <div>
                <div className="text-muted" style={{ fontSize: 12 }}>{t('Actual bank statement closing balance')}</div>
                <input className="form-control" type="number" placeholder={t('enter to verify')} value={stmtBal}
                       onChange={(e) => setStmtBal(e.target.value)} style={{ width: 200 }} />
              </div>
              {diff != null && (
                <div style={{ padding: '6px 12px', borderRadius: 8, fontWeight: 700,
                              background: Math.abs(diff) < 0.01 ? '#eef7ef' : '#fdecea',
                              color: Math.abs(diff) < 0.01 ? '#15803d' : '#b91c1c' }}>
                  {t('Difference')}: {money(diff)} {Math.abs(diff) < 0.01 ? '✓' : '⚠'}
                </div>
              )}
            </div>
            <div className="text-muted" style={{ fontSize: 11, marginTop: 8 }}>
              {bridge.fully_reconciled
                ? t('No timing differences — books and bank agree.')
                : t('The adjustments above are the open timing differences. When all are cleared, expected balance equals the book balance.')}
            </div>
          </div>
        );
      })()}

      {rep && (
        <div style={{ marginTop: 20, border: '1px solid #e6e0d4', borderRadius: 10, padding: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10, flexWrap: 'wrap', gap: 8 }}>
            <div>
              <div style={{ fontSize: 16, fontWeight: 700 }}>{t('Bank Reconciliation Statement')}</div>
              <div className="text-muted" style={{ fontSize: 12 }}>
                {rep.bank_account} · {rep.company}{rep.from_date ? ` · ${rep.from_date} → ${rep.to_date}` : ''}
              </div>
            </div>
            <div className="text-muted" style={{ fontSize: 12, textAlign: 'right' }}>
              {t('Reconciled')}: <b>{rep.reconciled_count}</b> / {rep.row_count} · {money(rep.reconciled_value)}
              <div style={{ marginTop: 4, display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center', flexWrap: 'wrap' }}>
                <PrintBar title={t('Bank Reconciliation Statement')} defaultOrientation="landscape" getBody={buildBody}
                          meta={`${rep.bank_account} · ${rep.from_date ? rep.from_date + ' → ' + rep.to_date : t('All dates')}`} />
                <button className="btn btn-xs btn-default"
                        onClick={async () => { try { const r = await api.backfillClearance(bankAccount); setErr(''); alert(t('Stamped cleared dates on') + ' ' + r.stamped + ' ' + t('vouchers')); } catch (e: any) { setErr(e.message || String(e)); } }}>
                  {t('Sync cleared dates to vouchers')}
                </button>
              </div>
            </div>
          </div>

          <div style={{ overflowX: 'auto' }}>
            <table className="table" style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #d8d2c6', textAlign: 'left' }}>
                  <th style={{ padding: '6px 8px' }}>#</th>
                  <th style={{ padding: '6px 8px' }}>{t('Value Date')}</th>
                  <th style={{ padding: '6px 8px' }}>{t('Bank Reference')}</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>{t('Amount')}</th>
                  <th style={{ padding: '6px 8px' }}>{t('Document Type')}</th>
                  <th style={{ padding: '6px 8px' }}>{t('Document No')}</th>
                  <th style={{ padding: '6px 8px' }}>{t('Document Date')}</th>
                  <th style={{ padding: '6px 8px' }}>{t('Cleared Date')}</th>
                  <th style={{ padding: '6px 8px' }}>{t('Party')}</th>
                  <th style={{ padding: '6px 8px' }}>{t('Status')}</th>
                </tr>
              </thead>
              <tbody>
                {rep.rows.map((r: any, i: number) => (
                  <tr key={i} style={{ borderBottom: '1px solid #f0ece3', background: r.reconciled ? undefined : '#fdfbf6' }}>
                    <td style={{ padding: '5px 8px', color: '#9a948a' }}>{i + 1}</td>
                    <td style={{ padding: '5px 8px' }}>{r.value_date}</td>
                    <td style={{ padding: '5px 8px' }}><code style={{ fontSize: 11 }}>{r.bank_reference || '—'}</code></td>
                    <td style={{ padding: '5px 8px', textAlign: 'right', color: r.direction === 'Incoming' ? '#15803d' : '#b91c1c', fontWeight: 600 }}>
                      {r.direction === 'Incoming' ? '▾ ' : '▴ '}{money(r.amount)}
                    </td>
                    <td style={{ padding: '5px 8px' }}>{r.voucher_type ? (r.voucher_type === 'Payment Entry' ? t('Payment Entry') : t('Journal Entry')) : '—'}</td>
                    <td style={{ padding: '5px 8px' }}><b>{r.voucher_no || '—'}</b></td>
                    <td style={{ padding: '5px 8px' }}>{r.voucher_date || '—'}</td>
                    <td style={{ padding: '5px 8px' }}>{r.cleared_date || '—'}</td>
                    <td style={{ padding: '5px 8px' }}>{r.party || '—'}</td>
                    <td style={{ padding: '5px 8px' }}>
                      {r.reconciled
                        ? <span style={{ color: '#15803d', fontWeight: 600 }}>{t('Reconciled')}</span>
                        : <span style={{ color: '#b45309', fontWeight: 600 }}>{t('Open')}</span>}
                    </td>
                  </tr>
                ))}
                {!rep.rows.length && <tr><td colSpan={10} className="text-muted" style={{ padding: 16, textAlign: 'center' }}>{t('No transactions in this range.')}</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
