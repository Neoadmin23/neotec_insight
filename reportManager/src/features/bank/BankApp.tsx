import { useEffect, useRef, useState } from 'react';
import { api, uploadFile } from '../../utils/api';
import { t } from '../../utils/i18n';
import StatementImport from './StatementImport';
import Reconcile from './Reconcile';
import ReconcileReport from './ReconcileReport';

type Slip = {
  name: string; company?: string; bank?: string; direction?: string; amount?: number;
  currency?: string; fee?: number; vat?: number; total_amount?: number; status?: string;
  value_date?: string; counterparty_name?: string; counterparty_account?: string;
  bank_reference?: string; transaction_id?: string; purpose?: string; description?: string;
  raw_text?: string; extraction_confidence?: number; extraction_method?: string;
  party_type?: string; party?: string; source_account?: string;
  source_account_link?: string; suggested_account?: string; payment_entry?: string;
  direction_basis?: string;
};

const money = (v?: number, c?: string) =>
  (v == null ? '—' : new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v) + (c ? ' ' + c : ''));

// Payments (money out) read red; receipts (money in) read green.
const isIncoming = (d?: string) => (d || '').toLowerCase().startsWith('in');
const dirColor = (d?: string) => (isIncoming(d) ? '#15803d' : '#b91c1c');
const dirMark = (d?: string) => (isIncoming(d) ? '▾ ' : '▴ '); // ▾ in, ▴ out

const STATUS_COLOR: Record<string, string> = {
  Extracted: '#b8860b', Reviewed: '#2563eb', Posted: '#0d9488',
  Reconciled: '#16a34a', Rejected: '#b91c1c', Draft: '#9a948a',
};

export default function BankApp() {
  const [slips, setSlips] = useState<Slip[]>([]);
  const [sel, setSel] = useState<Slip | null>(null);
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const [note, setNote] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<'slips' | 'statement' | 'reconcile' | 'report'>('slips');

  const load = async () => {
    try { setSlips(await api.listSlips(30)); }
    catch (e: any) { setErr(e.message || String(e)); }
  };
  useEffect(() => { load(); }, []);

  const select = async (name: string) => {
    setErr('');
    try { setSel(await api.getSlip(name)); }
    catch (e: any) { setErr(e.message || String(e)); }
  };

  const onUpload = async (files: FileList | null) => {
    if (!files || !files.length) return;
    setErr(''); setNote(''); setBusy(t('Reading slip…'));
    try {
      const file_url = await uploadFile(files[0]);
      const res = await api.readSlip(file_url);
      await load();
      if (res.duplicate) setNote(t('This slip was already read — opening the existing one.') + (res.data?.bank_reference ? ` (${res.data.bank_reference})` : ''));
      await select(res.slip);
    } catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(''); if (fileRef.current) fileRef.current.value = ''; }
  };

  const stage = async (slip: Slip) => {
    setErr(''); setBusy(t('Staging draft Payment Entry…'));
    try {
      const r = await api.stageDraftPaymentEntry(slip.name);
      await load(); await select(slip.name);
      if (r.payment_entry) window.open(`/app/payment-entry/${encodeURIComponent(r.payment_entry)}`, '_blank');
    } catch (e: any) { setErr(e.message || String(e)); }
    finally { setBusy(''); }
  };

  return (
    <div className="ni-bank" style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>{t('Bank')}</h2>
          <div className="text-muted" style={{ fontSize: 13 }}>{t('Read a payment/transfer slip (PDF, image or Excel), review it, and stage a draft entry for approval.')}</div>
        </div>
        {mode === 'slips' && (
          <div>
            <input ref={fileRef} type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.xlsx,.xls" style={{ display: 'none' }}
                   onChange={(e) => onUpload(e.target.files)} />
            <button className="btn btn-primary" disabled={!!busy} onClick={() => fileRef.current?.click()}>
              {busy ? busy : '⬆ ' + t('Upload Slip')}
            </button>
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12, borderBottom: '1px solid #e6e0d4' }}>
        <button className={'btn btn-sm ' + (mode === 'slips' ? 'btn-primary' : 'btn-default')} onClick={() => setMode('slips')}>
          {t('Slip Reader')}
        </button>
        <button className={'btn btn-sm ' + (mode === 'statement' ? 'btn-primary' : 'btn-default')} onClick={() => setMode('statement')}>
          {t('Statement Import')}
        </button>
        <button className={'btn btn-sm ' + (mode === 'reconcile' ? 'btn-primary' : 'btn-default')} onClick={() => setMode('reconcile')}>
          {t('Reconcile')}
        </button>
        <button className={'btn btn-sm ' + (mode === 'report' ? 'btn-primary' : 'btn-default')} onClick={() => setMode('report')}>
          {t('Report')}
        </button>
      </div>

      {mode === 'report' ? <ReconcileReport /> : mode === 'reconcile' ? <Reconcile /> : mode === 'statement' ? <StatementImport /> : (
      <>
      {err && <div style={{ background: '#fdecea', color: '#b91c1c', padding: '8px 12px', borderRadius: 6, marginBottom: 10 }}>{err}</div>}
      {note && <div style={{ background: '#fff7e6', color: '#8a6d1b', padding: '8px 12px', borderRadius: 6, marginBottom: 10 }}>{note}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1fr', gap: 16 }}>
        <div>
          <table className="table" style={{ width: '100%', fontSize: 13 }}>
            <thead><tr>
              <th>{t('Bank')}</th><th>{t('Counterparty')}</th><th style={{ textAlign: 'right' }}>{t('Amount')}</th>
              <th>{t('Reference')}</th><th>{t('Status')}</th>
            </tr></thead>
            <tbody>
              {slips.map((s) => (
                <tr key={s.name} onClick={() => select(s.name)}
                    style={{ cursor: 'pointer', background: sel?.name === s.name ? '#f3efe7' : undefined }}>
                  <td>{s.bank || '—'}</td>
                  <td>{s.counterparty_name || '—'}</td>
                  <td style={{ textAlign: 'right', color: dirColor(s.direction), fontWeight: 600 }} title={s.direction}>{dirMark(s.direction)}{money(s.total_amount ?? s.amount, s.currency)}</td>
                  <td><code style={{ fontSize: 11 }}>{s.bank_reference || '—'}</code></td>
                  <td><span style={{ color: STATUS_COLOR[s.status || ''] || '#555', fontWeight: 600 }}>{s.status}</span></td>
                </tr>
              ))}
              {!slips.length && <tr><td colSpan={5} className="text-muted" style={{ padding: 16, textAlign: 'center' }}>{t('No slips yet — upload one to begin.')}</td></tr>}
            </tbody>
          </table>
        </div>

        <div>
          {!sel ? (
            <div className="text-muted" style={{ padding: 20, border: '1px dashed #d8d2c6', borderRadius: 8, textAlign: 'center' }}>
              {t('Select a slip to review.')}
            </div>
          ) : (
            <SlipReview slip={sel} busy={!!busy}
                        onStage={() => stage(sel)}
                        onParty={async (pt, p) => { await api.setSlipParty(sel.name, pt, p); await select(sel.name); }}
                        onAccounts={async (bank, acct) => { await api.setSlipAccounts(sel.name, bank, acct); await select(sel.name); }}
                        onDirection={async (d) => { await api.setSlipDirection(sel.name, d); await select(sel.name); }} />
          )}
        </div>
      </div>

      <div className="text-muted" style={{ marginTop: 14, fontSize: 12 }}>
        {t('Reconciliation (auto-match against bank statements) is the next milestone and will live here.')}
      </div>
      </>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: any }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid #f0ece3', gap: 12 }}>
      <span className="text-muted">{k}</span><span style={{ fontWeight: 600, textAlign: 'right' }}>{v ?? '—'}</span>
    </div>
  );
}

function Picker({ label, placeholder, current, search, render, onPick }: {
  label: string; placeholder: string; current?: string;
  search: (q: string) => Promise<any[]>; render: (r: any) => string;
  onPick: (value: string) => void;
}) {
  const [q, setQ] = useState('');
  const [rows, setRows] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const run = async (val: string) => { setQ(val); setOpen(true); try { setRows(await search(val)); } catch { setRows([]); } };
  return (
    <div style={{ marginTop: 10 }}>
      <div className="text-muted" style={{ fontSize: 12, marginBottom: 2 }}>{label}{current ? ` · ${current}` : ''}</div>
      <input className="form-control" placeholder={placeholder} value={q}
             onFocus={() => !rows.length && run('')} onChange={(e) => run(e.target.value)} />
      {open && rows.length > 0 && (
        <div style={{ maxHeight: 150, overflow: 'auto', border: '1px solid #eee', borderRadius: 6, marginTop: 2 }}>
          {rows.map((r) => (
            <div key={r.name} onClick={() => { onPick(r.name); setOpen(false); setQ(render(r)); }}
                 style={{ padding: '5px 8px', cursor: 'pointer', borderBottom: '1px solid #f3f3f3', fontSize: 13 }}>
              {render(r)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SlipReview({ slip, onStage, onParty, onAccounts, onDirection, busy }: {
  slip: Slip; busy: boolean; onStage: () => void;
  onParty: (pt: string, p: string) => void;
  onAccounts: (bank: string, acct: string) => void;
  onDirection: (d: string) => void;
}) {
  const [ptype, setPtype] = useState(slip.party_type || 'Supplier');
  const [pq, setPq] = useState(slip.counterparty_name || '');
  const [presults, setPresults] = useState<any[]>([]);
  const [showText, setShowText] = useState(false);
  const company = slip.company || '';

  const searchParty = async () => setPresults(await api.searchParties(ptype, pq));

  return (
    <div style={{ border: '1px solid #e6e0d4', borderRadius: 8, padding: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h3 style={{ margin: '0 0 6px' }}>{slip.bank || t('Slip')} · <span style={{ color: dirColor(slip.direction) }}>{dirMark(slip.direction)}{money(slip.total_amount ?? slip.amount, slip.currency)}</span></h3>
        <span style={{ fontSize: 12, color: '#8a857b' }}>{slip.extraction_method} · {Math.round(slip.extraction_confidence || 0)}%</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0', borderBottom: '1px solid #f0ece3', gap: 12 }}>
        <span className="text-muted">{t('Direction')}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: dirColor(slip.direction), fontWeight: 700 }}>{slip.direction || t('unconfirmed')}</span>
          <button className={'btn btn-xs ' + (isIncoming(slip.direction) ? 'btn-default' : 'btn-primary')} onClick={() => onDirection('Outgoing')} title={t('Payment')}>▴ {t('Out')}</button>
          <button className={'btn btn-xs ' + (isIncoming(slip.direction) ? 'btn-primary' : 'btn-default')} onClick={() => onDirection('Incoming')} title={t('Receipt')}>▾ {t('In')}</button>
        </span>
      </div>
      {slip.direction_basis && <div className="text-muted" style={{ fontSize: 11, padding: '2px 0' }}>{slip.direction_basis}</div>}
      <Row k={t('Counterparty')} v={slip.counterparty_name} />
      <Row k={t('Bank Reference')} v={slip.bank_reference} />
      <Row k={t('Value Date')} v={slip.value_date} />

      {(slip.description || slip.purpose) && (
        <div style={{ marginTop: 8, background: '#faf8f3', border: '1px solid #efe9dc', borderRadius: 6, padding: '8px 10px', fontSize: 13 }}>
          <div className="text-muted" style={{ fontSize: 11, marginBottom: 2 }}>{t('Description / Narration')}</div>
          {slip.purpose && <div><b>{slip.purpose}</b></div>}
          {slip.description && <div style={{ whiteSpace: 'pre-wrap' }}>{slip.description}</div>}
        </div>
      )}
      {slip.raw_text && (
        <div style={{ marginTop: 6 }}>
          <button className="btn btn-xs btn-default" onClick={() => setShowText(!showText)}>
            {showText ? t('Hide full slip text') : t('Show full slip text')}
          </button>
          {showText && <pre style={{ maxHeight: 180, overflow: 'auto', background: '#f7f5f0', padding: 8, borderRadius: 6, fontSize: 11, marginTop: 6, whiteSpace: 'pre-wrap' }}>{slip.raw_text}</pre>}
        </div>
      )}

      <Picker label={t('Bank account (source / paid-from)')} placeholder={t('Search bank account…')}
              current={slip.source_account_link}
              search={(q) => api.searchBankAccounts(company, q)}
              render={(r) => `${r.account_name || r.name}${r.bank ? ' · ' + r.bank : ''}${r.iban ? ' · ' + r.iban : ''}`}
              onPick={(v) => onAccounts(v, slip.suggested_account || '')} />

      <Picker label={t('Account to book against')} placeholder={t('Search account…')}
              current={slip.suggested_account}
              search={(q) => api.searchAccounts(company, q)}
              render={(r) => `${r.account_number ? r.account_number + ' · ' : ''}${r.account_name || r.name}${r.root_type ? ' · ' + r.root_type : ''}`}
              onPick={(v) => onAccounts(slip.source_account_link || '', v)} />

      <div style={{ marginTop: 12 }}>
        <div className="text-muted" style={{ fontSize: 12, marginBottom: 2 }}>{t('Party')}{slip.party ? ` · ${slip.party_type}: ${slip.party}` : ''}</div>
        <div style={{ display: 'flex', gap: 6 }}>
          <select value={ptype} onChange={(e) => setPtype(e.target.value)} className="form-control" style={{ maxWidth: 120 }}>
            <option>Supplier</option><option>Customer</option>
          </select>
          <input className="form-control" placeholder={t('Search name / tax id…')} value={pq}
                 onChange={(e) => setPq(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && searchParty()} />
          <button className="btn btn-default" onClick={searchParty}>{t('Search')}</button>
        </div>
        {presults.length > 0 && (
          <div style={{ maxHeight: 140, overflow: 'auto', border: '1px solid #eee', borderRadius: 6, marginTop: 4 }}>
            {presults.map((r) => (
              <div key={r.name} onClick={() => { onParty(ptype, r.name); setPresults([]); }}
                   style={{ padding: '5px 8px', cursor: 'pointer', borderBottom: '1px solid #f3f3f3', fontSize: 13 }}>
                <b>{r.supplier_name || r.customer_name || r.name}</b>
                <span className="text-muted"> · {r.name}{r.tax_id ? ' · ' + r.tax_id : ''}{r.default_currency ? ' · ' + r.default_currency : ''}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
        <button className="btn btn-primary" disabled={busy || !!slip.payment_entry} onClick={onStage}>
          {slip.payment_entry ? t('Draft created') : t('Stage Draft Payment Entry')}
        </button>
        {slip.payment_entry && (
          <a className="btn btn-default" href={`/app/payment-entry/${encodeURIComponent(slip.payment_entry)}`} target="_blank" rel="noreferrer">
            {t('Open')} {slip.payment_entry}
          </a>
        )}
      </div>
      <div className="text-muted" style={{ marginTop: 8, fontSize: 12 }}>
        {t('Nothing posts automatically — the draft is created unsubmitted for your review.')}
      </div>
    </div>
  );
}
