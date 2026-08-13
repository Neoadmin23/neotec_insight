import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t, arName } from '../../utils/i18n';
import { fmtD } from '../../utils/format';

export interface GlDrillArgs {
  report: string;
  row_key: string;
  account?: string | null;
  fiscal_year: number;
  month_from: number;
  month_to: number;
  cost_center?: string | string[] | null;
  project?: string | string[] | null;
  department?: string | string[] | null;
  branch?: string | string[] | null;
  company?: string | null;
  period_mode?: 'fiscal_year' | 'date_range';
  period_from_date?: string | null;
  period_to_date?: string | null;
  /** Human label shown in the header (row or account name). */
  title?: string;
  /** The value displayed in the report, for a reconcile check. */
  expected?: number | null;
  decimals?: number;
}

interface GlEntry {
  posting_date: string; account: string; debit: number; credit: number;
  voucher_type: string; voucher_no: string; against?: string;
  party_type?: string; party?: string; cost_center?: string;
  project?: string; department?: string; remarks?: string;
}

export default function GlDrillModal({ args, onClose }: { args: GlDrillArgs; onClose: () => void }) {
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [data, setData] = useState<any>(null);
  const dec = args.decimals ?? 2;

  useEffect(() => {
    let alive = true;
    setLoading(true); setErr(null);
    api.glDrillEntries({
      report: args.report, row_key: args.row_key, account: args.account ?? null,
      fiscal_year: args.fiscal_year, month_from: args.month_from, month_to: args.month_to,
      cost_center: args.cost_center ?? null, project: args.project ?? null,
      department: args.department ?? null, branch: args.branch ?? null,
      company: args.company ?? null, period_mode: args.period_mode || 'fiscal_year',
      period_from_date: args.period_from_date ?? null, period_to_date: args.period_to_date ?? null,
      limit: 1000,
    }).then((r) => { if (alive) { setData(r); setLoading(false); } })
      .catch((e) => { if (alive) { setErr(String(e?.message || e || 'Error')); setLoading(false); } });
    return () => { alive = false; };
  }, [args]);

  const entries: GlEntry[] = (data?.entries as GlEntry[]) || [];
  const total = data?.total ?? 0;
  const reconciles = args.expected != null && data
    ? Math.abs((args.expected || 0) - (total || 0)) < 0.005 : null;

  return (
    <div className="gl-scrim" onClick={onClose}>
      <div className="gl-modal" onClick={(e) => e.stopPropagation()}>
        <div className="gl-head">
          <div>
            <div className="gl-title">{t('GL entries')}</div>
            <div className="gl-sub">{args.title || ''}</div>
          </div>
          <button className="gl-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>

        {loading && <div className="gl-msg">{t('Loading…')}</div>}
        {err && <div className="gl-msg gl-err">{err}</div>}

        {!loading && !err && data && (
          <>
            <div className="gl-summary">
              <div className="gl-sum-row">
                <span>{t('Date range')}</span>
                <b>{data.filters?.date_from} → {data.filters?.date_to}</b>
              </div>
              {data.filters?.scope_dimension && (
                <div className="gl-sum-row">
                  <span>{t('Scope')}</span>
                  <b>{data.filters.scope_dimension}: {(data.filters.scope_values || []).join(', ')}</b>
                </div>
              )}
              <div className="gl-sum-row">
                <span>{t('Accounts')}</span>
                <b>{(data.filters?.accounts || []).length}</b>
              </div>
              <div className="gl-sum-row">
                <span>{t('Entries')}</span>
                <b>{data.count}{data.shown < data.count ? ` (${t('showing')} ${data.shown})` : ''}</b>
              </div>
              <div className="gl-sum-row gl-total">
                <span>{t('Net total (credit − debit)')}</span>
                <b>{fmtD(total, dec)}</b>
              </div>
              {reconciles != null && (
                <div className={'gl-recon ' + (reconciles ? 'ok' : 'bad')}>
                  {reconciles
                    ? `✓ ${t('Matches the report value')} (${fmtD(args.expected || 0, dec)})`
                    : `⚠ ${t('Differs from report value')}: ${fmtD(args.expected || 0, dec)}`}
                </div>
              )}
            </div>

            <div className="gl-actions">
              {data.gl_report_url && (
                <a className="gl-open" href={data.gl_report_url} target="_blank" rel="noreferrer">
                  {t('Open in ERP GL')} ↗
                </a>
              )}
            </div>

            <div className="gl-table-wrap">
              <table className="gl-table">
                <thead>
                  <tr>
                    <th>{t('Date')}</th><th>{t('Account')}</th><th>{t('Voucher')}</th>
                    <th>{t('Against')}</th><th className="num">{t('Debit')}</th>
                    <th className="num">{t('Credit')}</th><th>{t('Department')}</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.length === 0 && (
                    <tr><td colSpan={7} className="gl-empty">{t('No entries')}</td></tr>
                  )}
                  {entries.map((e, i) => (
                    <tr key={i}>
                      <td>{e.posting_date}</td>
                      <td title={e.account}>{arName(e.account, e.account)}</td>
                      <td>{e.voucher_no}</td>
                      <td title={e.against || ''}>{(e.party || e.against || '').toString().slice(0, 28)}</td>
                      <td className="num">{e.debit ? fmtD(e.debit, dec) : ''}</td>
                      <td className="num">{e.credit ? fmtD(e.credit, dec) : ''}</td>
                      <td>{arName(e.department || '', e.department || '')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
