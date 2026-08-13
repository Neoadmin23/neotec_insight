import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { fmtD } from '../../utils/format';

interface ZLine { account: string; label: string; amount: number }

function yearStart() { const d = new Date(); return new Date(d.getFullYear(), 0, 1).toISOString().slice(0, 10); }
function todayISO() { return new Date().toISOString().slice(0, 10); }

export default function ZakatTab() {
  const [companies, setCompanies] = useState<{ name: string; label: string }[]>([]);
  const [company, setCompany] = useState('');
  const [fromDate, setFromDate] = useState(yearStart());
  const [toDate, setToDate] = useState(todayISO());
  const [cal, setCal] = useState<'hijri' | 'gregorian'>('hijri');
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({ eq: false, fin: true, ded: true });

  useEffect(() => {
    api.dimensionOptions('company').then((r: any[]) => setCompanies((r || []).map((x) => ({ name: x.name, label: x.label || x.name })))).catch(() => {});
    load('', cal);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function load(c: string, calendar: string) {
    setLoading(true); setErr(null);
    api.zakatEstimate(c || company || null, fromDate, toDate, calendar)
      .then((d) => { setData(d); if (d?.company && !company) setCompany(d.company); })
      .catch((e: any) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  }

  const cur = data?.currency || 'SAR';
  const amt = (v: number, strong = false) => (
    <td className={'vat-num vat-vatcol' + (strong ? ' z-strong' : '') + (v < 0 ? ' cf-neg' : '')}>
      {v < 0 ? `(${fmtD(Math.abs(v), 2)})` : fmtD(v, 2)}
    </td>
  );
  const detail = (key: string, lines: ZLine[], sign = 1) => open[key] && (lines || []).map((l) => (
    <tr key={l.account}>
      <td className="vat-box-num" /><td className="vat-box-label cf-detail">{l.label}</td>
      {amt(sign * l.amount)}
    </tr>
  ));
  const toggler = (key: string, label: string, n: number) => (
    <button className="cf-sec-toggle" onClick={() => setOpen((s) => ({ ...s, [key]: !s[key] }))}>
      <i className={'ti ' + (open[key] ? 'ti-chevron-down' : 'ti-chevron-right')} aria-hidden /> {label}
      {n > 0 && <span className="cf-count">{n}</span>}
    </button>
  );

  const c = data?.components;

  return (
    <div className="vat-wrap">
      <div className="vat-hero">
        <div>
          <h1>{t('Zakat')} <span className="vat-badge">{t('Estimate')}</span></h1>
          <p>{t('Zakat base by the equity (indirect) method — equity, adjusted profit, long-term funding, less fixed assets & investments. A preparation estimate to review with your zakat advisor.')}</p>
        </div>
      </div>

      <div className="vat-controls">
        <label><span className="flbl">{t('Company')}</span>
          <select value={company} onChange={(e) => setCompany(e.target.value)}>
            {companies.length === 0 && <option value="">{data?.company || t('Company')}</option>}
            {companies.map((x) => <option key={x.name} value={x.name}>{x.label}</option>)}
          </select>
        </label>
        <label><span className="flbl">{t('Zakat year from')}</span>
          <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
        </label>
        <label><span className="flbl">{t('To')}</span>
          <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
        </label>
        <div className="vat-quick">
          <button className={cal === 'hijri' ? 'on' : ''} onClick={() => { setCal('hijri'); load(company, 'hijri'); }}>{t('Hijri')} 2.5%</button>
          <button className={cal === 'gregorian' ? 'on' : ''} onClick={() => { setCal('gregorian'); load(company, 'gregorian'); }}>{t('Gregorian')} 2.5777%</button>
        </div>
        <button className="vat-run" onClick={() => load(company, cal)} disabled={loading}>
          {loading ? t('Calculating…') : t('Estimate')}
        </button>
      </div>

      {err && <div className="studio-err">{err}</div>}

      {data && !loading && c && (
        <>
          <div className="vat-kpis">
            <div className="vat-kpi"><span className="vat-kpi-l">{t('Zakat base')}</span><span className="vat-kpi-v">{fmtD(data.base, 2)} {cur}</span></div>
            <div className="vat-kpi"><span className="vat-kpi-l">{t('Rate')}</span><span className="vat-kpi-v">{data.rate_pct}%</span></div>
            <div className="vat-kpi vat-kpi-net pay"><span className="vat-kpi-l">{t('Zakat due (estimate)')}</span><span className="vat-kpi-v">{fmtD(data.zakat_due, 2)} {cur}</span></div>
          </div>

          <table className="vat-form">
            <thead><tr><th style={{ width: 36 }} /><th>{t('Component')}</th><th className="vat-num">{t('Amount')} ({cur})</th></tr></thead>
            <tbody>
              <tr className="vat-section"><td colSpan={3}>{t('Additions to the base')}</td></tr>
              <tr><td className="vat-box-num" />
                <td className="vat-box-label">{toggler('eq', t('Equity (capital, reserves, retained earnings — excl. current profit)'), (c.equity_lines || []).length)}</td>
                {amt(c.equity_excl_profit)}
              </tr>
              {detail('eq', c.equity_lines)}
              <tr><td className="vat-box-num" /><td className="vat-box-label">{t('Adjusted net profit for the zakat year')}</td>{amt(c.net_profit)}</tr>
              <tr><td className="vat-box-num" />
                <td className="vat-box-label">{toggler('fin', t('Long-term funding & provisions'), (c.financing || []).length)}</td>
                {amt(c.financing_total)}
              </tr>
              {detail('fin', c.financing)}

              <tr className="vat-section"><td colSpan={3}>{t('Deductions from the base')}</td></tr>
              <tr><td className="vat-box-num" />
                <td className="vat-box-label">{toggler('ded', t('Net fixed assets, CWIP & long-term investments'), (c.deductions || []).length)}</td>
                {amt(-c.deductions_total)}
              </tr>
              {detail('ded', c.deductions, -1)}

              <tr className="vat-section"><td colSpan={3}>{t('Result')}</td></tr>
              {data.floored_at_profit && (
                <tr><td /><td className="vat-box-label">{t('Equity-method base (before profit floor)')}</td>{amt(data.base_raw)}</tr>
              )}
              <tr className="vat-total-row"><td /><td className="vat-box-label">
                {t('Zakat base')}{data.floored_at_profit ? ' — ' + t('floored at adjusted profit') : ''}
              </td>{amt(data.base, true)}</tr>
              <tr className="vat-grand pay"><td /><td className="vat-box-label">{t('Zakat due')} ({data.rate_pct}%)</td>{amt(data.zakat_due, true)}</tr>
            </tbody>
          </table>

          <div className="vat-disclaimer">
            ⚠ {t('Preparation estimate only — final zakat requires advisor adjustments (disallowed provisions, investments in zakat-paying entities, funding held under a full year). Expand each component to review the account classification.')}
          </div>
        </>
      )}
    </div>
  );
}
