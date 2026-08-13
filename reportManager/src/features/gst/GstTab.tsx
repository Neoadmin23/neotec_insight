import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { fmtD } from '../../utils/format';
import { csvHeader } from '../../utils/export';

const HEADS = ['cgst', 'sgst', 'igst', 'cess'] as const;
const HLBL: Record<string, string> = { cgst: 'CGST', sgst: 'SGST / UTGST', igst: 'IGST', cess: 'Cess' };

function quarterStart() { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10); }
function todayISO() { return new Date().toISOString().slice(0, 10); }

/** India GST (v2.36.0) — GSTR-3B-style summary from GL tax heads: output tax
 *  vs ITC per head, net payable, taxable values from invoices, and a voucher
 *  drill per head. Registers live in Export Packs (GST Sales/Purchase). */
export default function GstTab() {
  const [companies, setCompanies] = useState<{ name: string; label: string }[]>([]);
  const [company, setCompany] = useState('');
  const [fromDate, setFromDate] = useState(quarterStart());
  const [toDate, setToDate] = useState(todayISO());
  const [data, setData] = useState<any>(null);
  const [drill, setDrill] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.dimensionOptions('company').then((r: any[]) => {
      const cs = (r || []).map((x) => ({ name: x.name, label: x.label || x.name }));
      setCompanies(cs); if (cs.length) { setCompany(cs[0].name); load(cs[0].name); }
    }).catch(() => load(''));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function load(c = company) {
    setLoading(true); setErr('');
    api.gstSummary(c || null, fromDate, toDate)
      .then(setData).catch((e: any) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  }

  function openDrill(side: 'output' | 'input', head: string) {
    api.gstHeadDrill(company || null, fromDate, toDate, side, head)
      .then((d) => setDrill({ ...d, side, head })).catch(() => {});
  }

  function exportCsv() {
    if (!data) return;
    const rows = [
      ...csvHeader(data.company || '', 'GST Summary (GSTR-3B style)', `${fromDate} to ${toDate}`),
      ['Head', 'Output Tax', 'ITC', 'Net'].join(','),
      ...HEADS.map((h) => [HLBL[h], data.output[h], data.itc[h], data.net[h]].join(',')),
      ['TOTAL', data.total_output, data.total_itc, data.net_payable].join(','),
    ];
    const blob = new Blob(['\ufeff' + rows.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `gst-summary-${fromDate}-to-${toDate}.csv`;
    a.click(); URL.revokeObjectURL(a.href);
  }

  const cur = data?.currency || 'INR';

  return (
    <div className="vat-wrap">
      <div className="vat-hero">
        <div>
          <h1>{t('GST Summary')} <span className="vat-badge">{t('India · GSTR-3B style')}</span></h1>
          <p>{t('Output tax and Input Tax Credit per head — CGST, SGST/UTGST, IGST, Cess — assembled from the GL tax accounts, with invoice taxable values and voucher drill.')}</p>
        </div>
      </div>

      <div className="vat-controls">
        <label><span className="flbl">{t('Company')}</span>
          <select value={company} onChange={(e) => setCompany(e.target.value)}>
            {companies.map((c) => <option key={c.name} value={c.name}>{c.label}</option>)}
          </select>
        </label>
        <label><span className="flbl">{t('From')}</span>
          <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} /></label>
        <label><span className="flbl">{t('To')}</span>
          <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} /></label>
        <button className="vat-run" onClick={() => load()} disabled={loading}>{loading ? t('Assembling…') : t('Generate')}</button>
        {data && <button className="vat-ghost" onClick={exportCsv}>{t('Export CSV')}</button>}
      </div>

      {err && <div className="studio-err">{err}</div>}

      {data && !loading && (
        <>
          <div className="vat-kpis">
            <div className="vat-kpi"><span className="vat-kpi-l">{t('Outward taxable value')}</span>
              <span className="vat-kpi-v">{fmtD(data.outward_taxable_value, 2)}</span></div>
            <div className="vat-kpi"><span className="vat-kpi-l">{t('Total output tax')}</span>
              <span className="vat-kpi-v">{fmtD(data.total_output, 2)}</span></div>
            <div className="vat-kpi"><span className="vat-kpi-l">{t('Total ITC')}</span>
              <span className="vat-kpi-v">{fmtD(data.total_itc, 2)}</span></div>
            <div className={'vat-kpi vat-kpi-net ' + (data.net_payable >= 0 ? 'pay' : 'refund')}>
              <span className="vat-kpi-l">{data.net_payable >= 0 ? t('Net GST payable') : t('Net credit')}</span>
              <span className="vat-kpi-v">{fmtD(Math.abs(data.net_payable), 2)} {cur}</span></div>
          </div>

          <table className="vat-form">
            <thead><tr>
              <th>{t('Head')}</th>
              <th className="vat-num">{t('Output Tax')}</th>
              <th className="vat-num">{t('Input Tax Credit')}</th>
              <th className="vat-num">{t('Net')}</th>
            </tr></thead>
            <tbody>
              {HEADS.map((h) => (
                <tr key={h}>
                  <td className="vat-box-label">{HLBL[h]}</td>
                  <td className="vat-num vat-vatcol">
                    <button className="vat-drill-btn" onClick={() => openDrill('output', h)}>{fmtD(data.output[h], 2)}</button>
                  </td>
                  <td className="vat-num vat-vatcol">
                    <button className="vat-drill-btn" onClick={() => openDrill('input', h)}>{fmtD(data.itc[h], 2)}</button>
                  </td>
                  <td className="vat-num z-strong">{fmtD(data.net[h], 2)}</td>
                </tr>
              ))}
              <tr className={'vat-grand ' + (data.net_payable >= 0 ? 'pay' : 'refund')}>
                <td>{t('TOTAL')}</td>
                <td className="vat-num">{fmtD(data.total_output, 2)}</td>
                <td className="vat-num">{fmtD(data.total_itc, 2)}</td>
                <td className="vat-num">{fmtD(data.net_payable, 2)}</td>
              </tr>
            </tbody>
          </table>

          {data.gstin && <div className="vat-accts"><strong>GSTIN:</strong> {data.gstin}</div>}
          <div className="vat-disclaimer">⚠ {data.note}</div>
        </>
      )}

      {drill && (
        <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) setDrill(null); }}>
          <div className="theme-panel" role="dialog" style={{ width: 'min(760px, 100%)' }}>
            <div className="theme-h">
              <h3>{drill.side === 'output' ? t('Output') : t('ITC')} · {HLBL[drill.head]} — {fmtD(drill.total, 2)} {cur}</h3>
              <button className="fh-x" onClick={() => setDrill(null)}>×</button>
            </div>
            <div style={{ maxHeight: '55vh', overflow: 'auto' }}>
              <table className="studio-table" style={{ width: '100%' }}>
                <thead><tr><th>{t('Date')}</th><th>{t('Voucher')}</th><th>{t('Party')}</th><th>{t('Account')}</th><th className="num">{t('Amount')}</th></tr></thead>
                <tbody>
                  {(drill.rows || []).map((r: any, i: number) => (
                    <tr key={i}>
                      <td>{r.posting_date}</td>
                      <td>{r.voucher_no} <span className="cls-root">{r.voucher_type}</span></td>
                      <td>{r.party}</td>
                      <td className="cls-type">{r.account}</td>
                      <td className={'num' + (r.amount < 0 ? ' cf-neg' : '')}>{fmtD(r.amount, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
