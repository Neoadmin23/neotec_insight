import { useEffect, useRef, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { PrintBar } from '../PrintBar';

const money = (v?: number) =>
  (v == null ? '—' : new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(v));
const signed = (v: number) => (v >= 0 ? '+' : '−') + money(Math.abs(v));

const PERIODS: Array<[string, string]> = [
  ['this_month', 'This month'], ['last_month', 'Last month'],
  ['this_quarter', 'This quarter'], ['last_quarter', 'Last quarter'],
  ['half_year', 'Half-year'], ['ytd', 'Year to date'], ['last_12m', 'Last 12 months'],
];

function Card({ label, value, color, sub, onClick, active }: any) {
  return (
    <div onClick={onClick} style={{
      border: '1px solid ' + (active ? '#7c3aed' : '#e6e0d4'), borderRadius: 10, padding: '12px 16px',
      minWidth: 150, cursor: onClick ? 'pointer' : 'default', background: active ? '#faf5ff' : undefined,
    }}>
      <div className="text-muted" style={{ fontSize: 12 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
      {sub && <div className="text-muted" style={{ fontSize: 11 }}>{sub}</div>}
    </div>
  );
}

function SourcePicker({ label, value, opts, onChange }: any) {
  const [kind, val] = (value || ':').split(/:(.*)/);
  const set = (k: string, v: string) => onChange(v === '' && k !== 'fixed' ? '' : `${k}:${v}`);
  return (
    <div style={{ minWidth: 240 }}>
      <div className="text-muted" style={{ fontSize: 12 }}>{label}</div>
      <div style={{ display: 'flex', gap: 4 }}>
        <select className="form-control" value={kind || 'field'} style={{ width: 110 }}
                onChange={(e) => set(e.target.value, '')}>
          <option value="field">Employee field</option>
          <option value="component">Salary component</option>
          <option value="fixed">Fixed / employee</option>
        </select>
        {kind === 'fixed' ? (
          <input className="form-control" type="number" placeholder="0" value={val || ''} onChange={(e) => set('fixed', e.target.value)} />
        ) : kind === 'component' ? (
          <select className="form-control" value={val || ''} onChange={(e) => set('component', e.target.value)}>
            <option value="">—</option>
            {opts.components.map((c: string) => <option key={c} value={c}>{c}</option>)}
          </select>
        ) : (
          <select className="form-control" value={val || ''} onChange={(e) => set('field', e.target.value)}>
            <option value="">—</option>
            {opts.employee_fields.map((f: any) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        )}
      </div>
    </div>
  );
}

export default function HrApp() {
  const [period, setPeriod] = useState('this_month');
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [basis, setBasis] = useState<'processed' | 'defined'>('processed');
  const [eosb, setEosb] = useState<any>(null);
  const [showEosb, setShowEosb] = useState(false);
  const [showProvCfg, setShowProvCfg] = useState(false);
  const [opts, setOpts] = useState<any>({ employee_fields: [], components: [] });
  const [cfg, setCfg] = useState<any>({ vacation_days: 0, ticket_source: '', insurance_source: '' });
  const contentRef = useRef<HTMLDivElement>(null);

  const load = (p: string) => {
    setLoading(true); setErr('');
    api.hrSummary('', '', p).then(setD).catch((e) => setErr(e.message || String(e))).finally(() => setLoading(false));
  };
  useEffect(() => { load(period); }, [period]);
  useEffect(() => {
    api.getProvisionConfig().then(setCfg).catch(() => {});
    api.provisionFieldOptions().then(setOpts).catch(() => {});
  }, []);

  const saveCfg = async (next: any) => {
    setCfg(next);
    try { await api.setProvisionConfig(Number(next.vacation_days) || 0, next.ticket_source, next.insurance_source); load(period); }
    catch (e: any) { setErr(e.message || String(e)); }
  };

  const openEosb = async () => {
    setShowEosb(!showEosb);
    if (!eosb) { try { setEosb(await api.eosbBreakdown()); } catch (e: any) { setErr(e.message || String(e)); } }
  };

  if (loading && !d) return <div className="text-muted" style={{ padding: 20 }}>{t('Loading people & payroll…')}</div>;
  if (err) return <div style={{ background: '#fdecea', color: '#b91c1c', padding: '10px 14px', borderRadius: 8, margin: 16 }}>{err}</div>;
  if (!d) return null;
  const p = d.payroll || {};
  const sal = d.salary || {};

  return (
    <div style={{ padding: 16 }} ref={contentRef}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h2 style={{ margin: '0 0 2px' }}>{t('People & Payroll')}</h2>
          <div className="text-muted" style={{ fontSize: 13 }}>
            {d.company} · {d.as_of} · {t('Salaries, accruals and end-of-service feed the CFO brief.')}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
          <div>
            <div className="text-muted" style={{ fontSize: 12 }}>{t('Period')}</div>
            <select className="form-control" value={period} onChange={(e) => setPeriod(e.target.value)}>
              {PERIODS.map(([v, l]) => <option key={v} value={v}>{t(l)}</option>)}
            </select>
            <div className="text-muted" style={{ fontSize: 11, marginTop: 2 }}>{d.from_date} → {d.to_date} · {d.months} {t('mo')}</div>
          </div>
          <PrintBar title={t('People & Payroll')} targetRef={contentRef}
                    meta={`${d.company} · ${d.period_label} · ${d.as_of}`} />
        </div>
      </div>

      {!p.available ? (
        <div style={{ background: '#fff7e6', color: '#92400e', padding: '12px 16px', borderRadius: 8, marginTop: 14 }}>
          {t('No HR data found. Install/enable Frappe HR and run payroll to populate this view.')}
        </div>
      ) : (
        <>
          {/* snapshot */}
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '16px 0' }}>
            <Card label={t('Headcount')} value={p.headcount} sub={p.saudi_pct != null ? `${p.saudi_pct}% ${t('Saudi')}` : t('point-in-time')} />
            <Card label={t('Defined monthly (master)')} value={money(sal.defined_monthly)} color="#1d4ed8" sub={t('Salary Structure Assignment')} />
            <Card label={t('Next payroll')} value={money(p.next_payroll)} color="#b45309" sub={t('committed')} />
            <Card label={t('Accrued unpaid salary')} value={money(p.accrued_unpaid)} color={p.accrued_unpaid > 0 ? '#b45309' : '#15803d'} />
            <Card label={t('EOSB provision')} value={money(p.eosb_liability)} color="#7c3aed" sub={t('tap for slab detail ▾')} onClick={openEosb} active={showEosb} />
          </div>

          {/* EOSB slab-wise detail */}
          {showEosb && (
            <div style={{ border: '1px solid #e6e0d4', borderRadius: 10, padding: 14, marginBottom: 18 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
                <div style={{ fontWeight: 700 }}>{t('EOSB provision — slab-wise')}</div>
                {eosb && (
                  <div className="text-muted" style={{ fontSize: 12 }}>
                    {t('First 5 yrs (½ mo/yr)')}: <b>{money(eosb.slab1_total)}</b> · {t('Beyond 5 yrs (1 mo/yr)')}: <b>{money(eosb.slab2_total)}</b> · {t('Total')}: <b style={{ color: '#7c3aed' }}>{money(eosb.total)}</b>
                  </div>
                )}
              </div>
              {!eosb ? <div className="text-muted">{t('Loading…')}</div> : (
                <div style={{ overflowX: 'auto' }}>
                  <table className="table" style={{ width: '100%', fontSize: 12 }}>
                    <thead><tr style={{ textAlign: 'left', borderBottom: '2px solid #d8d2c6' }}>
                      <th style={{ padding: '5px 8px' }}>{t('Employee')}</th>
                      <th style={{ padding: '5px 8px' }}>{t('Joined')}</th>
                      <th style={{ padding: '5px 8px', textAlign: 'right' }}>{t('Years')}</th>
                      <th style={{ padding: '5px 8px', textAlign: 'right' }}>{t('Base wage')}</th>
                      <th style={{ padding: '5px 8px', textAlign: 'right' }}>{t('½ mo × first 5y')}</th>
                      <th style={{ padding: '5px 8px', textAlign: 'right' }}>{t('1 mo × beyond 5y')}</th>
                      <th style={{ padding: '5px 8px', textAlign: 'right' }}>{t('EOSB')}</th>
                    </tr></thead>
                    <tbody>
                      {eosb.rows.map((r: any) => (
                        <tr key={r.employee} style={{ borderBottom: '1px solid #f0ece3' }}>
                          <td style={{ padding: '4px 8px' }}>{r.employee_name}</td>
                          <td style={{ padding: '4px 8px' }}>{r.doj}</td>
                          <td style={{ padding: '4px 8px', textAlign: 'right' }}>{r.years}</td>
                          <td style={{ padding: '4px 8px', textAlign: 'right' }}>{money(r.wage)}</td>
                          <td style={{ padding: '4px 8px', textAlign: 'right' }}>{money(r.slab1)}</td>
                          <td style={{ padding: '4px 8px', textAlign: 'right' }}>{money(r.slab2)}</td>
                          <td style={{ padding: '4px 8px', textAlign: 'right', fontWeight: 700, color: '#7c3aed' }}>{money(r.total)}</td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot><tr style={{ borderTop: '2px solid #333', fontWeight: 700 }}>
                      <td style={{ padding: '5px 8px' }} colSpan={4}>{eosb.count} {t('employees')}</td>
                      <td style={{ padding: '5px 8px', textAlign: 'right' }}>{money(eosb.slab1_total)}</td>
                      <td style={{ padding: '5px 8px', textAlign: 'right' }}>{money(eosb.slab2_total)}</td>
                      <td style={{ padding: '5px 8px', textAlign: 'right', color: '#7c3aed' }}>{money(eosb.total)}</td>
                    </tr></tfoot>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* people provisions */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <div style={{ fontWeight: 700 }}>{t('People provisions & liabilities')}</div>
            <button className="btn btn-xs btn-default" onClick={() => setShowProvCfg(!showProvCfg)}>{t('Configure sources')}</button>
          </div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
            <Card label={t('EOSB provision')} value={money(p.eosb_liability)} color="#7c3aed" sub={t('Saudi end-of-service')} />
            <Card label={t('Annual vacation provision')} value={money(d.provisions?.vacation)} color="#0d9488" sub={d.provisions?.vacation_basis} />
            <Card label={t('Annual ticket provision')} value={d.provisions?.ticket_configured ? money(d.provisions?.ticket) : t('not set')}
                  color={d.provisions?.ticket_configured ? '#b45309' : '#9a948a'} sub={d.provisions?.ticket_configured ? d.provisions?.ticket_basis : t('configure source →')} />
            <Card label={t('Insurance provision')} value={d.provisions?.insurance_configured ? money(d.provisions?.insurance) : t('not set')}
                  color={d.provisions?.insurance_configured ? '#b45309' : '#9a948a'} sub={d.provisions?.insurance_configured ? d.provisions?.insurance_basis : t('configure source →')} />
          </div>
          {showProvCfg && (
            <div style={{ background: '#faf8f3', border: '1px solid #efe9dc', borderRadius: 8, padding: 12, marginBottom: 14, display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div style={{ minWidth: 150 }}>
                <div className="text-muted" style={{ fontSize: 12 }}>{t('Vacation days/year (0 = auto 21/30)')}</div>
                <input className="form-control" type="number" value={cfg.vacation_days || 0}
                       onChange={(e) => setCfg({ ...cfg, vacation_days: e.target.value })}
                       onBlur={() => saveCfg(cfg)} style={{ width: 150 }} />
              </div>
              <SourcePicker label={t('Annual ticket source')} value={cfg.ticket_source} opts={opts}
                            onChange={(v: string) => saveCfg({ ...cfg, ticket_source: v })} />
              <SourcePicker label={t('Insurance source')} value={cfg.insurance_source} opts={opts}
                            onChange={(v: string) => saveCfg({ ...cfg, insurance_source: v })} />
              <span className="text-muted" style={{ fontSize: 11, maxWidth: 280 }}>
                {t('Ticket/Insurance can read an Employee numeric field, a Salary Component (annualised ×12), or a fixed amount per employee. Saved automatically.')}
              </span>
            </div>
          )}

          {/* defined vs processed (budget vs actual) for the period */}
          <div style={{ fontWeight: 700, marginBottom: 6 }}>{t('Defined vs processed')} · {d.period_label}</div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
            <Card label={`${t('Defined (master)')} · ${d.months} ${t('mo')}`} value={money(sal.defined_for_period)} color="#1d4ed8"
                  active={basis === 'defined'} onClick={() => setBasis('defined')} sub={t('committed per contracts')} />
            <Card label={t('Processed (actual)')} value={money(sal.processed)} color="#b91c1c"
                  active={basis === 'processed'} onClick={() => setBasis('processed')} sub={`${sal.slip_count} ${t('slips')} · ${t('net')} ${money(sal.net_paid)}`} />
            <Card label={t('Additional salary')} value={money(sal.additional)} color={sal.additional >= 0 ? '#b45309' : '#15803d'} sub={t('ad-hoc earnings/deductions')} />
            {sal.slip_count > 0 ? (
              <Card label={t('Variance (actual − defined)')} value={signed(sal.variance)}
                    color={sal.variance > 0 ? '#b91c1c' : sal.variance < 0 ? '#15803d' : undefined}
                    sub={sal.variance > 0 ? t('paid above master') : sal.variance < 0 ? t('paid below master') : t('on plan')} />
            ) : (
              <Card label={t('Variance (actual − defined)')} value="—" sub={t('payroll not processed yet')} />
            )}
          </div>
          <div className="text-muted" style={{ fontSize: 11, marginBottom: 18 }}>
            {sal.slip_count === 0
              ? t('No payroll has been processed for this period yet — the variance will appear once salary slips are submitted. (Pick a completed period like “Last month” to compare.)')
              : sal.variance > 0
                ? t('Processed payroll is above the master total — check additional salary, overtime, or off-cycle pay.')
                : sal.variance < 0
                  ? t('Processed payroll is below the master total — likely LOP/deductions, mid-period joiners/leavers, or runs not yet processed.')
                  : t('Processed payroll matches the master commitment for this period.')}
          </div>

          {d.departments?.length > 0 && (
            <div style={{ marginBottom: 18 }}>
              <div style={{ fontWeight: 700, marginBottom: 6 }}>{t('Headcount by department')}</div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {d.departments.map((x: any, i: number) => (
                  <span key={i} style={{ background: '#f3efe6', borderRadius: 6, padding: '4px 10px', fontSize: 13 }}>
                    {x.department} · <b>{x.count}</b>
                  </span>
                ))}
              </div>
            </div>
          )}

          {d.recent_runs?.length > 0 && (
            <div>
              <div style={{ fontWeight: 700, marginBottom: 6 }}>{t('Recent payroll runs')}</div>
              <table className="table" style={{ width: '100%', fontSize: 13, maxWidth: 900 }}>
                <thead><tr style={{ textAlign: 'left', borderBottom: '2px solid #d8d2c6' }}>
                  <th style={{ padding: '5px 8px' }}>{t('Month')}</th>
                  <th style={{ padding: '5px 8px' }}>{t('Payroll run')}</th>
                  <th style={{ padding: '5px 8px' }}>{t('For')}</th>
                  <th style={{ padding: '5px 8px' }}>{t('Posting')}</th>
                  <th style={{ padding: '5px 8px', textAlign: 'right' }}>{t('Employees')}</th>
                  <th style={{ padding: '5px 8px' }}>{t('Status')}</th>
                </tr></thead>
                <tbody>
                  {d.recent_runs.map((r: any) => {
                    const dt = r.start_date ? new Date(r.start_date + 'T00:00:00') : null;
                    const monthLabel = dt ? dt.toLocaleDateString(undefined, { month: 'short', year: 'numeric' }) : '—';
                    const scope = [r.branch, r.department].filter(Boolean).join(' · ');
                    return (
                      <tr key={r.name} style={{ borderBottom: '1px solid #f0ece3' }}>
                        <td style={{ padding: '5px 8px', fontWeight: 600 }}>{monthLabel}</td>
                        <td style={{ padding: '5px 8px' }}><code style={{ fontSize: 11 }}>{r.name}</code></td>
                        <td style={{ padding: '5px 8px' }}>{scope || (r.payroll_frequency ? t(r.payroll_frequency) : t('All employees'))}</td>
                        <td style={{ padding: '5px 8px' }}>{r.posting_date || '—'}</td>
                        <td style={{ padding: '5px 8px', textAlign: 'right' }}>{r.number_of_employees ?? '—'}</td>
                        <td style={{ padding: '5px 8px' }}>{r.status || '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      <div className="text-muted" style={{ fontSize: 11, marginTop: 16 }}>
        {t('EOSB is provisioned per Saudi Labour Law (½ month/yr for the first 5 years, 1 month/yr thereafter) on each active employee\'s latest base wage. Defined salary is the active Salary Structure Assignment; processed is actual submitted slips.')}
      </div>
    </div>
  );
}
