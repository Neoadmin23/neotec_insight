import { useEffect, useMemo, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { ExportBar } from '../ExportBar';
import type { DocRow, ReportDoc } from '../../utils/reportdoc';
import { mergeByVoucher } from '../../utils/statementCore';

/* v3.4.0 — Statement of Account for one customer or supplier, from either
 *   • General Ledger — opening balance, every transaction, running balance, closing;
 *   • Ageing         — open invoices with age and bucket, unallocated payments, totals.
 * Field picker per source; Print / PDF / Excel / Image through ExportBar, so the
 * report's Layout (positions, paper, letterhead) applies to every output. */

type Source = 'gl' | 'ageing';
type Col = { key: string; label: string; num?: boolean };

// v3.4.1 — every field the GL engine returns per entry
const GL_COLS: Col[] = [
  { key: 'posting_date', label: 'Date' }, { key: 'voucher_type', label: 'Voucher type' },
  { key: 'voucher_no', label: 'Voucher no.' }, { key: 'account_label', label: 'Account' },
  { key: 'party_type', label: 'Party type' }, { key: 'party', label: 'Party' },
  { key: 'against', label: 'Against account' }, { key: 'against_voucher_type', label: 'Against voucher type' },
  { key: 'against_voucher', label: 'Against voucher' }, { key: 'cost_center', label: 'Cost centre' },
  { key: 'project', label: 'Project' }, { key: 'department', label: 'Department' },
  { key: 'finance_book', label: 'Finance book' }, { key: 'is_opening', label: 'Is opening' },
  { key: 'remarks', label: 'Remarks' }, { key: 'description', label: 'Description' },
  { key: 'account_currency', label: 'Account currency' },
  { key: 'debit', label: 'Debit', num: true }, { key: 'credit', label: 'Credit', num: true },
  { key: 'debit_in_account_currency', label: 'Debit (account currency)', num: true },
  { key: 'credit_in_account_currency', label: 'Credit (account currency)', num: true },
  { key: 'balance', label: 'Balance', num: true },
];
const AG_COLS: Col[] = [
  { key: 'date', label: 'Date' }, { key: 'bill', label: 'Invoice' }, { key: 'age', label: 'Age (days)', num: true },
  { key: 'slab', label: 'Bucket' }, { key: 'amount', label: 'Invoice amount', num: true },
  { key: 'paid', label: 'Paid / adjusted', num: true }, { key: 'remaining', label: 'Outstanding', num: true },
];
const DEFAULTS: Record<Source, string[]> = {
  gl: ['posting_date', 'voucher_no', 'against_voucher', 'remarks', 'debit', 'credit', 'balance'],
  ageing: ['date', 'bill', 'age', 'slab', 'amount', 'paid', 'remaining'],
};
const LS = (s: Source) => `ni-statement-cols:${s}`;
const today = () => new Date().toISOString().slice(0, 10);
const yearStart = () => `${new Date().getFullYear()}-01-01`;
const fmt = (v: any) => (v === null || v === undefined || v === '' ? '' : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

export default function StatementApp() {
  const [companies, setCompanies] = useState<any[]>([]);
  const [company, setCompany] = useState('');
  const [partyType, setPartyType] = useState<'Customer' | 'Supplier'>('Customer');
  const [query, setQuery] = useState('');
  const [parties, setParties] = useState<{ name: string; label: string }[]>([]);
  const [party, setParty] = useState('');
  const [source, setSource] = useState<Source>('gl');
  const [fromDate, setFromDate] = useState(yearStart());
  const [toDate, setToDate] = useState(today());
  const [slabs, setSlabs] = useState('30,60,90,120');
  const [cols, setCols] = useState<string[]>(() => JSON.parse(localStorage.getItem(LS('gl')) || 'null') || DEFAULTS.gl);
  const [picker, setPicker] = useState(false);
  const [oneLine, setOneLine] = useState(() => localStorage.getItem('ni-statement-oneline') !== '0');
  // v3.4.1 — account choice (blank = all receivable / payable accounts)
  const [accts, setAccts] = useState<{ name: string; label: string; type: string }[]>([]);
  const [acctSel, setAcctSel] = useState<string[]>([]);
  const [acctQ, setAcctQ] = useState('');
  const [acctOpen, setAcctOpen] = useState(false);
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.listCompanies().then((cs: any[]) => { setCompanies(cs || []); if (cs?.length) setCompany(cs[0].name); }).catch(() => {});
  }, []);
  useEffect(() => {
    const h = setTimeout(() => {
      api.listParties(partyType, query, company || null).then((p) => setParties(p || [])).catch(() => setParties([]));
    }, 250);
    return () => clearTimeout(h);
  }, [partyType, query, company]);
  useEffect(() => {
    setCols(JSON.parse(localStorage.getItem(LS(source)) || 'null') || DEFAULTS[source]);
    setData(null);
  }, [source]);
  useEffect(() => {
    if (!company) return;
    setAcctSel([]);
    api.accountTree(company).then((rows: any[]) => {
      const leaf = (rows || []).filter((r) => !r.is_group).map((r) => ({
        name: r.name, type: r.account_type || '',
        label: (r.account_number ? `${r.account_number} - ` : '') + (r.account_name || r.name),
      }));
      const rank = (a: { type: string }) => (a.type === 'Receivable' || a.type === 'Payable' ? 0 : 1);
      setAccts(leaf.sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label)));
    }).catch(() => setAccts([]));
  }, [company]);

  const all = source === 'gl' ? GL_COLS : AG_COLS;
  // v3.4.1 — the picker's order is the print order
  const shown = cols.map((k) => all.find((c) => c.key === k)).filter(Boolean) as Col[];
  const partyLabel = parties.find((p) => p.name === party)?.label || party;
  const companyLabel = companies.find((c) => c.name === company)?.label || company;

  function saveCols(next: string[]) {
    setCols(next);
    localStorage.setItem(LS(source), JSON.stringify(next));
  }
  function toggle(k: string) {
    saveCols(cols.includes(k) ? cols.filter((x) => x !== k) : [...cols, k]);
  }
  function move(k: string, d: -1 | 1) {
    const i = cols.indexOf(k), j = i + d;
    if (i < 0 || j < 0 || j >= cols.length) return;
    const next = [...cols];
    [next[i], next[j]] = [next[j], next[i]];
    saveCols(next);
  }

  async function run() {
    if (!party) { setErr(t('Choose a party.')); return; }
    setBusy(true); setErr('');
    try {
      if (source === 'gl') {
        const r = await api.generalLedger({
          company, accounts: acctSel, from_date: fromDate, to_date: toDate, with_description: 1,
          customer: partyType === 'Customer' ? [party] : null, supplier: partyType === 'Supplier' ? [party] : null,
        } as any);
        setData({ kind: 'gl', ...r });
      } else {
        const r = await api.billwise(company, partyType, [party], toDate, 'due', 'days', slabs);
        setData({ kind: 'ageing', ...r });
      }
    } catch (e: any) { setErr(String(e?.message || e)); setData(null); }
    finally { setBusy(false); }
  }

  /** Rows for screen and every export — one source of truth. */
  const rows = useMemo(() => {
    if (!data) return [] as { kind: string; cells: Record<string, any> }[];
    const out: { kind: string; cells: Record<string, any> }[] = [];
    if (data.kind === 'gl') {
      let deb = 0, cre = 0, open = 0, close = 0;
      for (const b of data.accounts || []) {
        open += Number(b.opening_raw) || 0;
        close += Number(b.closing_raw) || 0;
      }
      out.push({ kind: 'sub', cells: { remarks: t('Opening balance'), voucher_type: t('Opening balance'), balance: open } });
      for (const b of data.accounts || []) {
        for (const r of (oneLine ? mergeByVoucher(b.transactions || []) : (b.transactions || []))) {
          deb += Number(r.debit) || 0; cre += Number(r.credit) || 0;
          out.push({ kind: 'normal', cells: { ...r, balance: r.balance_raw } });
        }
      }
      out.push({ kind: 'tot', cells: { voucher_type: t('Total'), remarks: t('Total'), debit: deb, credit: cre } });
      out.push({ kind: 'grand', cells: { voucher_type: t('Closing balance'), remarks: t('Closing balance'), balance: close } });
    } else {
      const blk = (data.blocks || [])[0];
      if (blk) {
        let amt = 0, rem = 0;
        for (const b of blk.open_bills || []) {
          amt += Number(b.amount) || 0; rem += Number(b.remaining) || 0;
          out.push({ kind: 'normal', cells: { ...b, paid: (Number(b.amount) || 0) - (Number(b.remaining) || 0) } });
        }
        out.push({ kind: 'tot', cells: { bill: t('Open invoices'), date: t('Open invoices'), amount: amt, remaining: rem } });
        for (const u of blk.unallocated || []) {
          out.push({ kind: 'normal', cells: { date: u.date || u.posting_date, bill: `${u.voucher_no || ''} — ${t('unallocated payment')}`, remaining: -(Number(u.amount) || 0) } });
        }
        (data.labels || []).forEach((lab: any, i: number) => {
          out.push({ kind: 'sub', cells: { bill: typeof lab === 'string' ? lab : lab.en, date: typeof lab === 'string' ? lab : lab.en, remaining: blk.buckets?.[i] ?? 0 } });
        });
        out.push({ kind: 'grand', cells: { bill: t('Balance due'), date: t('Balance due'), remaining: blk.balance } });
      }
    }
    return out;
  }, [data, oneLine]);

  function getDoc(): ReportDoc | null {
    if (!data) return null;
    const first = shown.find((c) => !c.num)?.key;
    const docRows: DocRow[] = rows.map((r) => ({
      kind: r.kind as any,
      cells: shown.map((c) => {
        const v = r.cells[c.key] ?? (c.key === first && r.kind !== 'normal' ? (r.cells.remarks ?? r.cells.bill ?? r.cells.date) : undefined);
        return c.num ? { v: v === undefined || v === '' ? '' : Number(v) || 0, text: fmt(v), num: true, bold: r.kind !== 'normal' }
          : { v: v ?? '', bold: r.kind !== 'normal' };
      }),
    }));
    return {
      title: t('Statement of Account'),
      // v3.4.3 — the party goes directly above the table, not into the letterhead band
      party: { label: `${t(partyType)}: ${partyLabel}` },
      company, companyLabel,
      period: source === 'gl' ? `${fromDate} → ${toDate}` : `${t('As of')} ${toDate}`,
      columns: shown.map((c) => ({ label: t(c.label), num: c.num, width: c.key === 'remarks' || c.key === 'description' ? 34 : undefined })),
      rows: docRows,
      fileBase: `statement_${source}`,
      orientation: shown.length > 7 ? 'landscape' : 'portrait',
      note: source === 'ageing' ? t('Open items as at the date shown; payments are matched to the invoices they settle.') : undefined,
    };
  }

  return (
    <div className="gl-page">
      <div className="gl-filters card">
        <div className="gl-frow">
          <label><span className="flbl">{t('Company')}</span>
            <select value={company} onChange={(e) => setCompany(e.target.value)}>
              {companies.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
            </select></label>
          <label><span className="flbl">{t('Party type')}</span>
            <select value={partyType} onChange={(e) => { setPartyType(e.target.value as any); setParty(''); setData(null); }}>
              <option value="Customer">{t('Customer')}</option><option value="Supplier">{t('Supplier')}</option>
            </select></label>
          <label className="st-party"><span className="flbl">{t('Party')}</span>
            <input placeholder={t('Search…')} value={query} onChange={(e) => setQuery(e.target.value)} />
            <select value={party} onChange={(e) => { setParty(e.target.value); setData(null); }}>
              <option value="">{t('Choose…')}</option>
              {parties.map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}
            </select></label>
          <label><span className="flbl">{t('Statement from')}</span>
            <select value={source} onChange={(e) => setSource(e.target.value as Source)}>
              <option value="gl">{t('General Ledger (transactions)')}</option>
              <option value="ageing">{t('Ageing (open items)')}</option>
            </select></label>
          {source === 'gl' && (
            <div className="st-acct">
              <span className="flbl">{t('Accounts')}</span>
              <button type="button" className="bk-btn" onClick={() => setAcctOpen((v) => !v)}>
                {acctSel.length ? `${acctSel.length} ${t('selected')}`
                  : (partyType === 'Customer' ? t('All receivable accounts') : t('All payable accounts'))} ▾
              </button>
              {acctOpen && (
                <div className="st-acct-pop">
                  <input placeholder={t('Search accounts…')} value={acctQ} onChange={(e) => setAcctQ(e.target.value)} />
                  <button type="button" className="cp-link" onClick={() => setAcctSel([])}>{t('Clear — use all receivable / payable')}</button>
                  <div className="st-acct-list">
                    {accts.filter((a) => !acctQ || a.label.toLowerCase().includes(acctQ.toLowerCase())).slice(0, 200).map((a) => (
                      <label key={a.name}>
                        <input type="checkbox" checked={acctSel.includes(a.name)}
                          onChange={() => setAcctSel((s) => (s.includes(a.name) ? s.filter((x) => x !== a.name) : [...s, a.name]))} />
                        {a.label}{a.type ? <small> · {t(a.type)}</small> : null}
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
          {source === 'gl' && (
            <label><span className="flbl">{t('From date')}</span>
              <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} /></label>
          )}
          <label><span className="flbl">{source === 'gl' ? t('To date') : t('As of')}</span>
            <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} /></label>
          {source === 'ageing' && (
            <label><span className="flbl">{t('Slabs (days)')}</span>
              <input value={slabs} onChange={(e) => setSlabs(e.target.value)} /></label>
          )}
          {source === 'gl' && (
            <label className="st-oneline" title={t('Merge an invoice posted as several lines (e.g. revenue and VAT) into one line at the invoice total')}>
              <input type="checkbox" checked={oneLine}
                onChange={(e) => { setOneLine(e.target.checked); localStorage.setItem('ni-statement-oneline', e.target.checked ? '1' : '0'); }} />
              {' '}{t('One line per voucher')}
            </label>
          )}
          <button type="button" className="bk-btn bk-primary" disabled={busy} onClick={run}>{busy ? t('Loading…') : t('Generate')}</button>
        </div>
        <ExportBar reportKey="statement" reportLabel={t('Statement of Account')} company={company}
          companyLabel={companyLabel} disabled={!data} getDoc={getDoc}>
          <button type="button" onClick={() => setPicker((v) => !v)}>{t('Fields')}</button>
        </ExportBar>
        {picker && (
          <div className="st-picker">
            <div className="st-order">
              <div className="st-sub">{t('Print order')}</div>
              {shown.map((c, i) => (
                <div key={c.key} className="st-ord-row">
                  <span className="st-ord-n">{i + 1}</span>
                  <span className="st-ord-l">{t(c.label)}</span>
                  <button type="button" disabled={i === 0} onClick={() => move(c.key, -1)} title={t('Move left')}>↑</button>
                  <button type="button" disabled={i === shown.length - 1} onClick={() => move(c.key, 1)} title={t('Move right')}>↓</button>
                  <button type="button" onClick={() => toggle(c.key)} title={t('Remove')}>×</button>
                </div>
              ))}
            </div>
            <div className="st-avail">
              <div className="st-sub">{t('Add a field')}</div>
              {all.filter((c) => !cols.includes(c.key)).map((c) => (
                <button type="button" key={c.key} className="st-add" onClick={() => toggle(c.key)}>＋ {t(c.label)}</button>
              ))}
            </div>
          </div>
        )}
        {err && <div className="alloc-warn">{err}</div>}
      </div>
      {data && (
        <div className="card st-table">
          <h3>{t('Statement of Account')} — {partyLabel}</h3>
          <div className="cp-table-wrap">
            <table className="cp-table">
              <thead><tr>{shown.map((c) => <th key={c.key} className={c.num ? 'num' : ''}>{t(c.label)}</th>)}</tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className={'st-' + r.kind}>
                    {shown.map((c, j) => {
                      const v = r.cells[c.key] ?? (j === shown.findIndex((x) => !x.num) && r.kind !== 'normal' ? (r.cells.remarks ?? r.cells.bill ?? r.cells.date) : '');
                      return <td key={c.key} className={c.num ? 'num' : ''}>{c.num ? fmt(v) : String(v ?? '')}</td>;
                    })}
                  </tr>
                ))}
                {!rows.length && <tr><td colSpan={shown.length}>{t('No entries for this party and period.')}</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
