import { useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { fmtD } from '../../utils/format';
import { csvHeader } from '../../utils/export';

/** Bill-wise Analysis (v2.32.0) — the classic per-party document statement:
 *  invoices and payments in date order, payments allocated FIFO with the
 *  AGAINST VOUCHER shown, per-bill remaining balance, cumulative balance,
 *  party subtotal, and the still-open bills aged into the user's slabs.
 *  Multiple parties print stacked one below the other — the multi-ledger
 *  concept adopted for bills. */
export default function BillwiseView({ company, side, asOf, basedOn, mode, slabs, treeSel }: {
  company: string; side: 'Customer' | 'Supplier'; asOf: string;
  basedOn: string; mode: string; slabs: string; treeSel?: string[] | null;
}) {
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<any[]>([]);
  const [picked, setPicked] = useState<{ name: string; label: string }[]>([]);
  const [hitsOpen, setHitsOpen] = useState(false);
  const [selOpen, setSelOpen] = useState(false);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const search = () => api.ageingListParties(company || null, side, query)
    .then((r) => { setHits(r); setHitsOpen(true); }).catch(() => {});
  const add = (h: any) => {
    if (!picked.some((p) => p.name === h.name)) setPicked((p) => [...p, { name: h.name, label: h.label || h.name }]);
  };

  async function run() {
    // Tree selection (shared with Summary) feeds Bill-wise too — chips add on top.
    const fromTree = (treeSel && treeSel.length && treeSel.length <= 20) ? treeSel : [];
    const names = Array.from(new Set([...fromTree, ...picked.map((p) => p.name)]));
    // Empty selection → backend picks the top 20 parties by outstanding.
    setLoading(true); setErr('');
    try { setData(await api.billwise(company || null, side, names, asOf, basedOn, mode, slabs)); }
    catch (e: any) { setErr(String(e?.message || e)); }
    finally { setLoading(false); }
  }

  const effCount = new Set([
    ...((treeSel && treeSel.length && treeSel.length <= 20) ? treeSel : []),
    ...picked.map((p) => p.name),
  ]).size;

  const amt = (v: number | null | undefined, strong = false) =>
    v == null ? '' : (v < 0 ? `(${fmtD(Math.abs(v), 2)})` : fmtD(v, 2)) + (strong ? '' : '');

  function exportCsv() {
    if (!data) return;
    const rows: string[] = [...csvHeader(data.company || '',
      `${side === 'Customer' ? 'AR' : 'AP'} Bill-wise Analysis`, `As of ${data.as_of}`)];
    for (const b of data.blocks) {
      rows.push(`"${b.label}"`);
      rows.push(['Doc Date', 'Doc Type', 'Doc No', 'Amount', 'Adjustment', 'Against Voucher', 'Bill Balance', 'Cumulative'].join(','));
      for (const r of b.rows) {
        rows.push([r.posting_date, `"${r.voucher_type || ''}"`, `"${r.voucher_no || ''}"`,
          r.amount ?? '', r.allocated ?? '', `"${r.against || ''}"`,
          r.bill_balance ?? '', r.cumulative ?? ''].join(','));
      }
      rows.push(`"Sub Total: ${b.label}",,,,,,,${b.balance}`);
      if (b.unallocated?.length) {
        rows.push(side === 'Customer'
          ? '"Unallocated receipts - not applied to any invoice"'
          : '"Unallocated payments - not applied to any purchase"');
        rows.push(['Date', 'Doc Type', 'Doc No', 'Amount'].join(','));
        for (const u of b.unallocated) rows.push([u.posting_date, `"${u.voucher_type}"`, `"${u.voucher_no}"`, u.amount].join(','));
        rows.push(`"Total unallocated",,,${b.unallocated_total}`);
      }
      rows.push('');
    }
    const blob = new Blob(['\ufeff' + rows.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${side === 'Customer' ? 'AR' : 'AP'}-billwise-${data.as_of}.csv`;
    a.click(); URL.revokeObjectURL(a.href);
  }

  function printReport() {
    if (!data) return;
    const esc = (x: any) => String(x ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;');
    const n = (v: any) => v == null ? '' : (v < 0 ? `(${fmtD(Math.abs(v), 2)})` : fmtD(v, 2));
    let body = '';
    for (const b of data.blocks) {
      body += `<h2>${esc(b.label)}</h2>
        <table><thead><tr><th>${t('Doc Date')}</th><th>${t('Doc No')}</th><th class="num">${t('Amount')}</th><th class="num">${t('Adjustment')}</th><th>${t('Against Voucher')}</th><th class="num">${t('Bill Balance')}</th><th class="num">${t('Cumulative')}</th></tr></thead><tbody>`;
      for (const r of b.rows) {
        body += `<tr><td>${esc(r.posting_date)}</td><td>${esc(r.voucher_no)}</td><td class="num">${n(r.amount)}</td><td class="num">${n(r.allocated)}</td><td>${esc(r.against)}</td><td class="num">${n(r.bill_balance)}</td><td class="num">${n(r.cumulative)}</td></tr>`;
      }
      body += `<tr class="sub"><td colspan="6">${t('Sub Total')}: ${esc(b.label)}</td><td class="num">${n(b.balance)}</td></tr></tbody></table>`;
      if (b.unallocated?.length) {
        const uh = side === 'Customer'
          ? t('Unallocated receipts — not applied to any invoice')
          : t('Unallocated payments — not applied to any purchase');
        body += `<div class="ua-h">${esc(uh)}</div><table class="ua"><thead><tr><th>${t('Date')}</th><th>${t('Doc Type')}</th><th>${t('Doc No')}</th><th class="num">${t('Amount')}</th></tr></thead><tbody>`;
        for (const u of b.unallocated) {
          body += `<tr><td>${esc(u.posting_date)}</td><td>${esc(u.voucher_type)}</td><td>${esc(u.voucher_no)}</td><td class="num">${n(u.amount)}</td></tr>`;
        }
        body += `<tr class="sub"><td colspan="3">${t('Total unallocated')}</td><td class="num">${n(b.unallocated_total)}</td></tr></tbody></table>`;
      }
      if (b.open_bills.length) {
        body += `<div class="ob-h">${t('Open bills (aged)')}</div><table class="ob"><thead><tr><th>${t('Bill')}</th><th>${t('Date')}</th><th class="num">${t('Amount')}</th><th class="num">${t('Remaining')}</th><th>${t('Age')}</th><th>${t('Slab')}</th></tr></thead><tbody>`;
        for (const o of b.open_bills) {
          body += `<tr><td>${esc(o.bill)}</td><td>${esc(o.date)}</td><td class="num">${n(o.amount)}</td><td class="num">${n(o.remaining)}</td><td>${esc(o.age)}</td><td>${esc(o.slab)}</td></tr>`;
        }
        body += '</tbody></table>';
      }
    }
    const w = window.open('', '_blank'); if (!w) return;
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${t('Bill-wise Analysis')}</title><style>
      *{-webkit-print-color-adjust:exact;print-color-adjust:exact;}
      body{font:12px/1.45 'Segoe UI',Arial,sans-serif;color:#2c2c2a;margin:24px;}
      h1{font-size:17px;margin:0 0 2px;} .meta{color:#888;font-size:10.5px;margin-bottom:14px;}
      h2{font-size:13.5px;margin:18px 0 6px;color:#16404d;border-bottom:2px solid #16404d;padding-bottom:3px;}
      table{border-collapse:collapse;width:100%;font-size:11px;} th{background:#16404d;color:#fff;text-align:start;padding:5px 8px;}
      td{border-bottom:1px solid #e6e0d4;padding:4px 8px;} .num{text-align:right;}
      tr.sub td{background:#eef7f4;font-weight:700;color:#11816F;}
      .ob-h{margin:8px 0 4px;font-size:11px;font-weight:600;color:#8a6d3b;}
      table.ob th{background:#8a6d3b;}
      .ua-h{margin:8px 0 4px;font-size:11px;font-weight:600;color:#b3261e;}
      table.ua th{background:#b3261e;} table.ua tr.sub td{background:#fdecea;color:#b3261e;}
      @media print{@page{margin:14mm;}}</style></head><body>
      <h1>${esc(data.company)} — ${side === 'Customer' ? t('AR') : t('AP')} ${t('Bill-wise Analysis')}</h1>
      <div class="meta">${t('As on')} ${esc(data.as_of)} · ${data.blocks.length} ${t('parties')}</div>
      ${body}
      <script>window.onload=function(){window.print();}<\/script></body></html>`);
    w.document.close();
  }

  return (
    <div>
      <div className="vat-controls" style={{ marginTop: 4 }}>
        <span className="navgrp" onClick={(e) => e.stopPropagation()}>
          <input placeholder={t('Search') + ' ' + (side === 'Customer' ? t('customer') : t('supplier')) + '…'}
            value={query} onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') search(); }} style={{ minWidth: 240 }} />
          {hitsOpen && hits.length > 0 && (
            <div className="navgrp-menu bw-hits">
              {hits.map((h) => {
                const on = picked.some((p) => p.name === h.name);
                return (
                  <button key={h.name} className={'bw-hit' + (on ? ' on' : '')}
                    onClick={() => on ? setPicked((x) => x.filter((y) => y.name !== h.name)) : add(h)}>
                    {on ? '✓ ' : '+ '}{h.label || h.name}
                  </button>
                );
              })}
              <div style={{ padding: '6px 10px' }}>
                <button className="studio-run" onClick={() => setHitsOpen(false)}>{t('Done')}</button>
              </div>
            </div>
          )}
        </span>
        <button className="vat-ghost" onClick={search}>{t('Search')}</button>
        {picked.length > 0 && (
          <span className="navgrp" onClick={(e) => e.stopPropagation()}>
            <button className="vat-ghost" onClick={() => setSelOpen((o) => !o)}>{t('Selected')} ({picked.length}) ▾</button>
            {selOpen && (
              <div className="navgrp-menu bw-hits">
                {picked.map((p) => (
                  <div key={p.name} className="bw-hit" style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span>{p.label}</span>
                    <button className="studio-ghost" onClick={() => setPicked((x) => x.filter((y) => y.name !== p.name))}>×</button>
                  </div>
                ))}
                <div style={{ padding: '6px 10px', display: 'flex', gap: 6 }}>
                  <button className="studio-ghost" onClick={() => setPicked([])}>{t('Clear all')}</button>
                  <button className="studio-run" onClick={() => setSelOpen(false)}>{t('Done')}</button>
                </div>
              </div>
            )}
          </span>
        )}
        <button className="vat-run" onClick={run} disabled={loading}>
          {loading ? t('Building…') : t('Bill-wise report') + (effCount ? ` (${effCount})` : ` (${t('Top 20')})`)}
        </button>
        {data && <button className="vat-ghost" onClick={printReport}>{t('Print / PDF')}</button>}
        {data && <button className="vat-ghost" onClick={exportCsv}>{t('Export CSV')}</button>}
        {effCount === 0 && (
          <span className="studio-hint">
            {t('No selection — the top 20 parties by outstanding balance will be shown. Narrow it with the tree or search.')}
          </span>
        )}
      </div>
      {err && <div className="studio-err">{err}</div>}

      {data && !loading && data.blocks.map((b: any) => (
        <div key={b.party} className="bw-block">
          <h3 className="bw-party">{b.label} <span className="cls-root">{t('Balance')}: {amt(b.balance)}</span></h3>
          <table className="vat-form bw-table">
            <thead><tr>
              <th>{t('Doc Date')}</th><th>{t('Doc No')}</th>
              <th className="vat-num">{t('Amount')}</th><th className="vat-num">{t('Adjustment')}</th>
              <th>{t('Against Voucher')}</th>
              <th className="vat-num">{t('Bill Balance')}</th><th className="vat-num">{t('Cumulative')}</th>
            </tr></thead>
            <tbody>
              {b.rows.map((r: any, i: number) => (
                <tr key={i}>
                  <td>{r.posting_date}</td>
                  <td>{r.voucher_no}</td>
                  <td className={'vat-num' + ((r.amount ?? 0) < 0 ? ' cf-neg' : '')}>{amt(r.amount)}</td>
                  <td className="vat-num">{amt(r.allocated)}</td>
                  <td className="cls-type">{r.against}</td>
                  <td className="vat-num">{amt(r.bill_balance)}</td>
                  <td className="vat-num z-strong">{amt(r.cumulative)}</td>
                </tr>
              ))}
              <tr className="vat-total-row">
                <td colSpan={6}>{t('Sub Total')}: {b.label}</td>
                <td className={'vat-num' + (b.balance < 0 ? ' cf-neg' : '')}>{amt(b.balance)}</td>
              </tr>
            </tbody>
          </table>
          {b.unallocated?.length > 0 && (
            <div className="bw-unalloc">
              <div className="bw-unalloc-h">
                {side === 'Customer'
                  ? t('Unallocated receipts — received from customer, not applied to any invoice')
                  : t('Unallocated payments — paid to supplier, not applied to any purchase')}
              </div>
              <table className="studio-table bw-unalloc-t">
                <thead><tr><th>{t('Date')}</th><th>{t('Doc Type')}</th><th>{t('Doc No')}</th><th className="num">{t('Amount')}</th></tr></thead>
                <tbody>
                  {b.unallocated.map((u: any, i: number) => (
                    <tr key={i}>
                      <td>{u.posting_date}</td><td>{u.voucher_type}</td><td>{u.voucher_no}</td>
                      <td className="num">{fmtD(u.amount, 2)}</td>
                    </tr>
                  ))}
                  <tr className="studio-grand">
                    <td colSpan={3}>{t('Total unallocated')}</td>
                    <td className="num">{fmtD(b.unallocated_total, 2)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}
          {b.open_bills.length > 0 && (
            <div className="bw-open">
              <div className="theme-sec-title">{t('Open bills (aged)')}</div>
              <table className="studio-table">
                <thead><tr><th>{t('Bill')}</th><th>{t('Date')}</th><th className="num">{t('Amount')}</th><th className="num">{t('Remaining')}</th><th>{t('Age')}</th><th>{t('Slab')}</th></tr></thead>
                <tbody>
                  {b.open_bills.map((o: any) => (
                    <tr key={o.bill + o.date}>
                      <td>{o.bill}</td><td>{o.date}</td>
                      <td className="num">{amt(o.amount)}</td>
                      <td className={'num' + (o.remaining < 0 ? ' cf-neg' : '')}>{amt(o.remaining)}</td>
                      <td>{o.age}</td><td>{o.slab}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
