import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { fmtD } from '../../utils/format';
import { csvHeader } from '../../utils/export';

interface CfLine { account: string; label: string; amount: number }

function monthStart() { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10); }
function todayISO() { return new Date().toISOString().slice(0, 10); }

export default function CashFlowTab() {
  const [companies, setCompanies] = useState<{ name: string; label: string }[]>([]);
  const [company, setCompany] = useState('');
  const [fromDate, setFromDate] = useState(monthStart());
  const [toDate, setToDate] = useState(todayISO());
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [openSec, setOpenSec] = useState<Record<string, boolean>>({ wc: true, inv: true, fin: true });

  useEffect(() => {
    api.dimensionOptions('company').then((r: any[]) => setCompanies((r || []).map((x) => ({ name: x.name, label: x.label || x.name })))).catch(() => {});
    load('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function load(c: string) {
    setLoading(true); setErr(null);
    api.cashFlow(c || company || null, fromDate, toDate)
      .then((d) => { setData(d); if (d?.company && !company) setCompany(d.company); })
      .catch((e: any) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  }

  function quickPeriod(kind: 'this_month' | 'this_quarter' | 'ytd') {
    const now = new Date();
    let f: Date;
    if (kind === 'this_month') f = new Date(now.getFullYear(), now.getMonth(), 1);
    else if (kind === 'this_quarter') { const q = Math.floor(now.getMonth() / 3); f = new Date(now.getFullYear(), q * 3, 1); }
    else f = new Date(now.getFullYear(), 0, 1);
    setFromDate(f.toISOString().slice(0, 10)); setToDate(todayISO());
  }

  function exportCsv() {
    if (!data) return;
    const rows: string[] = [
      ...csvHeader(data.company || '', 'Statement of Cash Flows (Indirect)', `${fromDate} to ${toDate}`),
      ['Section', 'Line', 'Amount'].join(','),
    ];
    const push = (sec: string, label: string, amt: number) => rows.push([`"${sec}"`, `"${label}"`, amt.toFixed(2)].join(','));
    push('Operating', 'Net profit', data.operating.net_profit);
    push('Operating', 'Depreciation & amortisation add-back', data.operating.depreciation_addback);
    (data.operating.working_capital || []).forEach((l: CfLine) => push('Operating', l.label, l.amount));
    push('Operating', 'NET CASH FROM OPERATING', data.operating.total);
    (data.investing.lines || []).forEach((l: CfLine) => push('Investing', l.label, l.amount));
    push('Investing', 'NET CASH FROM INVESTING', data.investing.total);
    (data.financing.lines || []).forEach((l: CfLine) => push('Financing', l.label, l.amount));
    push('Financing', 'NET CASH FROM FINANCING', data.financing.total);
    push('Cash', 'Net change in cash', data.net_change);
    if (Math.abs(data.unclassified) >= 0.01) push('Cash', 'Unclassified movement', data.unclassified);
    push('Cash', 'Opening cash', data.cash.opening);
    push('Cash', 'Closing cash', data.cash.closing);
    const blob = new Blob(['\ufeff' + rows.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `cash-flow-${fromDate}-to-${toDate}.csv`;
    a.click(); URL.revokeObjectURL(a.href);
  }

  const cur = data?.currency || 'SAR';
  const amt = (v: number) => (
    <td className={'vat-num vat-vatcol ' + (v < 0 ? 'cf-neg' : '')}>{v < 0 ? `(${fmtD(Math.abs(v), 2)})` : fmtD(v, 2)}</td>
  );
  const lineRows = (secKey: string, lines: CfLine[]) => openSec[secKey] && (lines || []).map((l) => (
    <tr key={l.account}>
      <td className="vat-box-num" />
      <td className="vat-box-label cf-detail">{l.label}</td>
      {amt(l.amount)}
    </tr>
  ));
  const secHeader = (secKey: string, label: string, count: number) => (
    <tr className="vat-section">
      <td colSpan={3}>
        <button className="cf-sec-toggle" onClick={() => setOpenSec((s) => ({ ...s, [secKey]: !s[secKey] }))}>
          <i className={'ti ' + (openSec[secKey] ? 'ti-chevron-down' : 'ti-chevron-right')} aria-hidden /> {label}
          {count > 0 && <span className="cf-count">{count}</span>}
        </button>
      </td>
    </tr>
  );

  return (
    <div className="vat-wrap">
      <div className="vat-hero">
        <div>
          <h1>{t('Cash Flow')} <span className="vat-badge">{t('Indirect')}</span></h1>
          <p>{t('Statement of cash flows built from the General Ledger — operating, investing and financing, reconciled to your actual bank & cash movement.')}</p>
        </div>
      </div>

      <div className="vat-controls">
        <label><span className="flbl">{t('Company')}</span>
          <select value={company} onChange={(e) => setCompany(e.target.value)}>
            {companies.length === 0 && <option value="">{data?.company || t('Company')}</option>}
            {companies.map((c) => <option key={c.name} value={c.name}>{c.label}</option>)}
          </select>
        </label>
        <label><span className="flbl">{t('From')}</span>
          <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
        </label>
        <label><span className="flbl">{t('To')}</span>
          <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
        </label>
        <div className="vat-quick">
          <button onClick={() => quickPeriod('this_month')}>{t('This month')}</button>
          <button onClick={() => quickPeriod('this_quarter')}>{t('This quarter')}</button>
          <button onClick={() => quickPeriod('ytd')}>{t('YTD')}</button>
        </div>
        <button className="vat-run" onClick={() => load(company)} disabled={loading}>
          {loading ? t('Calculating…') : t('Generate')}
        </button>
        {data && <button className="vat-ghost" onClick={exportCsv}>{t('Export CSV')}</button>}
      </div>

      {err && <div className="studio-err">{err}</div>}

      {data && !loading && (
        <>
          <div className="vat-kpis">
            <div className="vat-kpi"><span className="vat-kpi-l">{t('Operating cash flow')}</span><span className="vat-kpi-v">{fmtD(data.operating.total, 2)} {cur}</span></div>
            <div className="vat-kpi"><span className="vat-kpi-l">{t('Investing')}</span><span className="vat-kpi-v">{fmtD(data.investing.total, 2)} {cur}</span></div>
            <div className="vat-kpi"><span className="vat-kpi-l">{t('Financing')}</span><span className="vat-kpi-v">{fmtD(data.financing.total, 2)} {cur}</span></div>
            <div className={'vat-kpi vat-kpi-net ' + (data.net_change >= 0 ? 'refund' : 'pay')}>
              <span className="vat-kpi-l">{t('Net change in cash')}</span>
              <span className="vat-kpi-v">{fmtD(data.net_change, 2)} {cur}</span>
            </div>
          </div>

          <table className="vat-form">
            <thead>
              <tr><th style={{ width: 36 }} /><th>{t('Description')}</th><th className="vat-num">{t('Amount')} ({cur})</th></tr>
            </thead>
            <tbody>
              <tr className="vat-section"><td colSpan={3}>{t('Cash flow from operating activities')}</td></tr>
              <tr><td className="vat-box-num" /><td className="vat-box-label">{t('Net profit for the period')}</td>{amt(data.operating.net_profit)}</tr>
              <tr><td className="vat-box-num" /><td className="vat-box-label">{t('Depreciation & amortisation (add-back)')}</td>{amt(data.operating.depreciation_addback)}</tr>
              {secHeader('wc', t('Changes in working capital'), (data.operating.working_capital || []).length)}
              {lineRows('wc', data.operating.working_capital)}
              <tr className="vat-total-row"><td /><td className="vat-box-label">{t('Net cash from operating activities')}</td>{amt(data.operating.total)}</tr>

              {secHeader('inv', t('Cash flow from investing activities'), (data.investing.lines || []).length)}
              {lineRows('inv', data.investing.lines)}
              <tr className="vat-total-row"><td /><td className="vat-box-label">{t('Net cash from investing activities')}</td>{amt(data.investing.total)}</tr>

              {secHeader('fin', t('Cash flow from financing activities'), (data.financing.lines || []).length)}
              {lineRows('fin', data.financing.lines)}
              <tr className="vat-total-row"><td /><td className="vat-box-label">{t('Net cash from financing activities')}</td>{amt(data.financing.total)}</tr>

              <tr className="vat-section"><td colSpan={3}>{t('Reconciliation to cash')}</td></tr>
              <tr className="vat-total-row"><td /><td className="vat-box-label">{t('Net change in cash & equivalents')}</td>{amt(data.net_change)}</tr>
              {Math.abs(data.unclassified) >= 0.01 && (
                <tr><td /><td className="vat-box-label">{t('Unclassified movement (opening entries / rounding)')}</td>{amt(data.unclassified)}</tr>
              )}
              <tr><td /><td className="vat-box-label">{t('Cash & equivalents — opening')}</td>{amt(data.cash.opening)}</tr>
              <tr className={'vat-grand ' + (data.cash.closing >= data.cash.opening ? 'refund' : 'pay')}>
                <td /><td className="vat-box-label">{t('Cash & equivalents — closing')}</td>{amt(data.cash.closing)}
              </tr>
            </tbody>
          </table>

          <div className="vat-accts">
            <strong>{t('Cash accounts')}:</strong> {(data.cash.accounts || []).join(', ') || '—'}
          </div>
          <div className="vat-disclaimer">
            ⚠ {t('Classification of working-capital, investing and financing lines is inferred from account types and names. Review before external reporting.')}
          </div>
        </>
      )}
    </div>
  );
}
