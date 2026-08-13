import { useRef, useState } from 'react';
import { api, uploadFile } from '../../utils/api';
import { t } from '../../utils/i18n';

const money = (v?: number) =>
  (v == null ? '—' : new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v));

export default function StatementImport() {
  const [fileUrl, setFileUrl] = useState('');
  const [preview, setPreview] = useState<any>(null);
  const [bankAccount, setBankAccount] = useState('');
  const [bankRows, setBankRows] = useState<any[]>([]);
  const [bankQ, setBankQ] = useState('');
  const [level, setLevel] = useState<'batch' | 'transaction'>('batch');
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const [result, setResult] = useState<any>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const onUpload = async (files: FileList | null) => {
    if (!files || !files.length) return;
    setErr(''); setResult(null); setPreview(null); setBusy(t('Parsing statement…'));
    try {
      const url = await uploadFile(files[0]);
      setFileUrl(url);
      const p = await api.previewStatement(url);
      setPreview(p);
      if (p.matched_bank_accounts?.length) setBankAccount(p.matched_bank_accounts[0].name);
    } catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(''); if (fileRef.current) fileRef.current.value = ''; }
  };

  const searchBank = async (q: string) => {
    setBankQ(q);
    try { setBankRows(await api.searchBankAccounts('', q)); } catch { setBankRows([]); }
  };

  const doImport = async () => {
    if (!bankAccount) { setErr(t('Pick the bank account these settle into.')); return; }
    setErr(''); setBusy(t('Creating bank transactions…'));
    try { setResult(await api.importStatement(fileUrl, bankAccount, level)); }
    catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(''); }
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <div className="text-muted" style={{ fontSize: 13 }}>
          {t('Upload a bank/merchant statement (CSV or Excel). Lines are grouped into settlement batches and imported as Bank Transactions.')}
        </div>
        <div>
          <input ref={fileRef} type="file" accept=".csv,.tsv,.xlsx,.xls" style={{ display: 'none' }} onChange={(e) => onUpload(e.target.files)} />
          <button className="btn btn-primary" disabled={!!busy} onClick={() => fileRef.current?.click()}>
            {busy ? busy : '⬆ ' + t('Upload Statement')}
          </button>
        </div>
      </div>

      {err && <div style={{ background: '#fdecea', color: '#b91c1c', padding: '8px 12px', borderRadius: 6, marginBottom: 10 }}>{err}</div>}

      {preview && preview.format === 'account' && (
        <div style={{ border: '1px solid #e6e0d4', borderRadius: 8, padding: 14 }}>
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginBottom: 10 }}>
            <Stat label={t('Lines')} value={preview.line_count} />
            <Stat label={t('Incoming')} value={`${preview.incoming_count} · ${money(preview.total_incoming)}`} color="#15803d" />
            <Stat label={t('Outgoing')} value={`${preview.outgoing_count} · ${money(preview.total_outgoing)}`} color="#b91c1c" />
            <Stat label={t('Bank fees')} value={money(preview.total_fees)} color="#b91c1c" />
            <Stat label={t('VAT on fees')} value={money(preview.total_vat)} color="#b91c1c" />
            {preview.with_invoice_ref > 0 && <Stat label={t('Cite an invoice')} value={preview.with_invoice_ref} />}
          </div>
          {typeof preview.balance_mismatch === 'number' && (
            <div style={{ fontSize: 12, marginBottom: 8, color: preview.balance_mismatch === 0 ? '#15803d' : '#b45309' }}>
              {preview.balance_mismatch === 0
                ? `✓ ${t('All lines reconcile against the running balance')} (${preview.balance_ok}).`
                : `⚠ ${preview.balance_mismatch} ${t('line(s) do not reconcile against the running balance — review before import')}.`}
            </div>
          )}
          <table className="table" style={{ width: '100%', fontSize: 13 }}>
            <thead><tr>
              <th>{t('Value Date')}</th><th>{t('Type')}</th><th>{t('Reference')}</th>
              <th style={{ textAlign: 'right' }}>{t('Amount')}</th><th>{t('Invoice')}</th>
            </tr></thead>
            <tbody>
              {preview.lines.map((l: any, i: number) => (
                <tr key={i}>
                  <td>{l.value_date}</td>
                  <td>{l.txn_type}</td>
                  <td><code style={{ fontSize: 11 }}>{l.reference || '—'}</code></td>
                  <td style={{ textAlign: 'right', color: l.direction === 'Incoming' ? '#15803d' : '#b91c1c', fontWeight: 600 }}>
                    {l.direction === 'Incoming' ? '▾ ' : '▴ '}{money(l.amount)}
                  </td>
                  <td>{l.invoice_ref || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <BankPicker preview={preview} bankAccount={bankAccount} setBankAccount={setBankAccount}
                      bankQ={bankQ} bankRows={bankRows} searchBank={searchBank} />
          <div style={{ marginTop: 12 }}>
            <button className="btn btn-primary" disabled={!!busy || !bankAccount} onClick={doImport}>
              {t('Import as Bank Transactions')}
            </button>
          </div>
          {result && (
            <div style={{ marginTop: 12, background: '#eef7ef', color: '#15803d', padding: '8px 12px', borderRadius: 6 }}>
              {t('Created')} {result.created_count} {t('bank transaction(s)')}{result.skipped ? ` · ${t('skipped')} ${result.skipped} ${t('(already imported)')}` : ''}.
              <span className="text-muted"> {t('Reconciliation matching is the next step.')}</span>
            </div>
          )}
        </div>
      )}

      {preview && preview.format !== 'account' && (
        <div style={{ border: '1px solid #e6e0d4', borderRadius: 8, padding: 14 }}>
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginBottom: 10 }}>
            <Stat label={t('Transactions')} value={preview.line_count} />
            <Stat label={t('Batches')} value={preview.batch_count} />
            <Stat label={t('Gross')} value={money(preview.total_gross)} />
            <Stat label={t('Fees')} value={money(preview.total_fees)} color="#b91c1c" />
            <Stat label={t('VAT')} value={money(preview.total_vat)} color="#b91c1c" />
            <Stat label={t('Net deposits')} value={money(preview.total_net)} color="#15803d" />
            {preview.account_number && <Stat label={t('Account')} value={preview.account_number} />}
          </div>

          <table className="table" style={{ width: '100%', fontSize: 13 }}>
            <thead><tr>
              <th>{t('Batch')}</th><th>{t('Posting Date')}</th><th>{t('Schemes')}</th>
              <th style={{ textAlign: 'right' }}>{t('Txns')}</th>
              <th style={{ textAlign: 'right' }}>{t('Gross')}</th>
              <th style={{ textAlign: 'right' }}>{t('Fees+VAT')}</th>
              <th style={{ textAlign: 'right' }}>{t('Net Deposit')}</th>
            </tr></thead>
            <tbody>
              {preview.batches.map((b: any, i: number) => (
                <tr key={i}>
                  <td><code style={{ fontSize: 11 }}>{b.batch_reference || '—'}</code></td>
                  <td>{b.posting_date}</td><td>{b.schemes}</td>
                  <td style={{ textAlign: 'right' }}>{b.count}</td>
                  <td style={{ textAlign: 'right' }}>{money(b.gross)}</td>
                  <td style={{ textAlign: 'right', color: '#b91c1c' }}>{money(b.fees + b.vat)}</td>
                  <td style={{ textAlign: 'right', color: '#15803d', fontWeight: 600 }}>{money(b.net)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* bank account + import */}
          <div style={{ marginTop: 12, borderTop: '1px solid #eee', paddingTop: 12 }}>
            <div className="text-muted" style={{ fontSize: 12, marginBottom: 2 }}>
              {t('Bank account these settle into')}{bankAccount ? ` · ${bankAccount}` : ''}
            </div>
            {preview.matched_bank_accounts?.length > 0 && (
              <div style={{ marginBottom: 6 }}>
                {preview.matched_bank_accounts.map((m: any) => (
                  <button key={m.name} className={'btn btn-xs ' + (bankAccount === m.name ? 'btn-primary' : 'btn-default')}
                          style={{ marginRight: 6 }} onClick={() => setBankAccount(m.name)}>
                    {m.account_name || m.name} · {m.bank}
                  </button>
                ))}
                <span className="text-muted" style={{ fontSize: 12 }}> {t('(matched by account number)')}</span>
              </div>
            )}
            <input className="form-control" placeholder={t('Search bank account…')} value={bankQ}
                   onChange={(e) => searchBank(e.target.value)} onFocus={() => !bankRows.length && searchBank('')} style={{ maxWidth: 420 }} />
            {bankRows.length > 0 && (
              <div style={{ maxHeight: 140, overflow: 'auto', border: '1px solid #eee', borderRadius: 6, marginTop: 4, maxWidth: 420 }}>
                {bankRows.map((r) => (
                  <div key={r.name} onClick={() => { setBankAccount(r.name); setBankRows([]); setBankQ(r.account_name || r.name); }}
                       style={{ padding: '5px 8px', cursor: 'pointer', borderBottom: '1px solid #f3f3f3', fontSize: 13 }}>
                    <b>{r.account_name || r.name}</b><span className="text-muted"> · {r.bank}{r.bank_account_no ? ' · ' + r.bank_account_no : ''}</span>
                  </div>
                ))}
              </div>
            )}

            <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 12 }}>
              <label style={{ fontSize: 13 }}>
                <input type="radio" checked={level === 'batch'} onChange={() => setLevel('batch')} /> {t('Per batch (deposit)')}
              </label>
              <label style={{ fontSize: 13 }}>
                <input type="radio" checked={level === 'transaction'} onChange={() => setLevel('transaction')} /> {t('Per transaction')}
              </label>
              <button className="btn btn-primary" disabled={!!busy || !bankAccount} onClick={doImport}>
                {t('Import as Bank Transactions')}
              </button>
            </div>
          </div>

          {result && (
            <div style={{ marginTop: 12, background: '#eef7ef', color: '#15803d', padding: '8px 12px', borderRadius: 6 }}>
              {t('Created')} {result.created_count} {t('bank transaction(s)')}{result.skipped ? ` · ${t('skipped')} ${result.skipped} ${t('(already imported)')}` : ''}.
              <span className="text-muted"> {t('Reconciliation matching against your entries comes next.')}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: any; color?: string }) {
  return (
    <div>
      <div className="text-muted" style={{ fontSize: 11 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color }}>{value}</div>
    </div>
  );
}

function BankPicker({ preview, bankAccount, setBankAccount, bankQ, bankRows, searchBank }: any) {
  return (
    <div style={{ marginTop: 12, borderTop: '1px solid #eee', paddingTop: 12 }}>
      <div className="text-muted" style={{ fontSize: 12, marginBottom: 2 }}>
        {t('Bank account this statement belongs to')}{bankAccount ? ` · ${bankAccount}` : ''}
      </div>
      {preview.matched_bank_accounts?.length > 0 && (
        <div style={{ marginBottom: 6 }}>
          {preview.matched_bank_accounts.map((m: any) => (
            <button key={m.name} className={'btn btn-xs ' + (bankAccount === m.name ? 'btn-primary' : 'btn-default')}
                    style={{ marginRight: 6 }} onClick={() => setBankAccount(m.name)}>
              {m.account_name || m.name} · {m.bank}
            </button>
          ))}
        </div>
      )}
      <input className="form-control" placeholder={t('Search bank account…')} value={bankQ}
             onChange={(e) => searchBank(e.target.value)} onFocus={() => !bankRows.length && searchBank('')} style={{ maxWidth: 420 }} />
      {bankRows.length > 0 && (
        <div style={{ maxHeight: 140, overflow: 'auto', border: '1px solid #eee', borderRadius: 6, marginTop: 4, maxWidth: 420 }}>
          {bankRows.map((r: any) => (
            <div key={r.name} onClick={() => { setBankAccount(r.name); searchBank(''); }}
                 style={{ padding: '5px 8px', cursor: 'pointer', borderBottom: '1px solid #f3f3f3', fontSize: 13 }}>
              <b>{r.account_name || r.name}</b><span className="text-muted"> · {r.bank}{r.bank_account_no ? ' · ' + r.bank_account_no : ''}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
