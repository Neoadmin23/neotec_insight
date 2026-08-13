import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

const money = (v?: number) =>
  (v == null ? '—' : new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(v));

const SEV: Record<string, { bg: string; fg: string; label: string }> = {
  high: { bg: '#fdecea', fg: '#b91c1c', label: 'High' },
  medium: { bg: '#fff7e6', fg: '#b45309', label: 'Medium' },
  low: { bg: '#eef7ef', fg: '#15803d', label: 'Low' },
};

function Stat({ label, value, sub, color }: { label: string; value: any; sub?: string; color?: string }) {
  return (
    <div style={{ minWidth: 130 }}>
      <div className="text-muted" style={{ fontSize: 11 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color }}>{value}</div>
      {sub && <div className="text-muted" style={{ fontSize: 10 }}>{sub}</div>}
    </div>
  );
}

export function CfoBrief() {
  const [b, setB] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  const load = () => {
    setLoading(true); setErr('');
    api.morningBrief().then(setB).catch((e) => setErr(e.message || String(e))).finally(() => setLoading(false));
  };
  useEffect(() => { load(); }, []);

  if (loading) return <div className="text-muted" style={{ padding: 12 }}>{t('Preparing your brief…')}</div>;
  if (err) return <div style={{ background: '#fdecea', color: '#b91c1c', padding: '8px 12px', borderRadius: 6, marginBottom: 12 }}>{err}</div>;
  if (!b) return null;

  const m = b.metrics;
  const statusColor = b.status === 'high' ? '#b91c1c' : b.status === 'medium' ? '#b45309' : '#15803d';

  return (
    <div style={{ border: '1px solid #e6e0d4', borderLeft: `4px solid ${statusColor}`, borderRadius: 10, padding: 16, marginBottom: 16, background: '#fffdf9' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <div style={{ fontSize: 16, fontWeight: 700 }}>{t('What needs your attention')}</div>
        <div className="text-muted" style={{ fontSize: 11 }}>
          {m.company} · {m.as_of} · {b.high_count} {t('high')} / {b.headline_count} {t('total')}
          <button className="btn btn-xs btn-default" style={{ marginLeft: 8 }} onClick={load}>{t('Refresh')}</button>
        </div>
      </div>

      {b.narrative && (
        <div style={{ fontSize: 13.5, lineHeight: 1.5, marginBottom: 12, color: '#333' }}>{b.narrative}</div>
      )}

      {/* the numbers a CFO checks first */}
      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginBottom: 12, paddingBottom: 12, borderBottom: '1px solid #f0ece3' }}>
        <Stat label={t('Cash')} value={money(m.cash)} color={m.cash < 0 ? '#b91c1c' : undefined}
              sub={m.runway?.runway_months != null ? `~${m.runway.runway_months} ${t('mo runway')}` : undefined} />
        <Stat label={t('Overdue AR')} value={money(m.receivables?.overdue)} color={m.receivables?.overdue > 0 ? '#b45309' : '#15803d'}
              sub={m.receivables?.worst_days ? `${t('worst')} ${m.receivables.worst_days}d` : t('all current')} />
        <Stat label={t('Payables due ≤14d')} value={money(m.payables?.due_soon)} color={m.payables?.due_soon > 0 ? '#b45309' : undefined} />
        <Stat label={t('VAT payable')} value={money(m.vat?.net_payable)} color="#0d9488" />
        <Stat label={t('Revenue MTD')} value={money(m.revenue?.this_month)}
              sub={m.revenue?.mom_pct != null ? `${m.revenue.mom_pct >= 0 ? '▲' : '▼'} ${Math.abs(m.revenue.mom_pct)}% ${t('MoM')}` : undefined}
              color={m.revenue?.mom_pct != null ? (m.revenue.mom_pct >= 0 ? '#15803d' : '#b91c1c') : undefined} />
        <Stat label={t('Gross margin')} value={m.margin?.gross_margin_pct != null ? m.margin.gross_margin_pct + '%' : '—'}
              color={m.margin?.gross_margin_pct != null && m.margin.gross_margin_pct < 0 ? '#b91c1c' : undefined} />
        {m.payroll?.available && (
          <>
            <Stat label={t('Next payroll')} value={money(m.payroll.next_payroll)}
                  color={m.cash != null && m.payroll.next_payroll > m.cash ? '#b91c1c' : undefined}
                  sub={`${m.payroll.headcount} ${t('staff')}`} />
            <Stat label={t('People liabilities')} value={money(m.people_liabilities?.total)} color="#7c3aed"
                  sub={t('EOSB + leave + ticket + insurance')} />
          </>
        )}
      </div>

      {/* ranked attention list */}
      {b.alerts.length === 0 ? (
        <div style={{ color: '#15803d', fontWeight: 600 }}>✓ {t('Nothing urgent. The numbers look calm.')}</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {b.alerts.map((al: any, i: number) => {
            const sv = SEV[al.severity];
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, background: sv.bg, borderRadius: 8, padding: '6px 10px' }}>
                <span style={{ fontSize: 10, fontWeight: 700, color: '#fff', background: sv.fg, padding: '2px 7px', borderRadius: 4, marginTop: 1, whiteSpace: 'nowrap' }}>{t(sv.label)}</span>
                <div style={{ fontSize: 13 }}>
                  <span style={{ fontWeight: 600 }}>{al.title}</span>
                  <span className="text-muted"> · {al.area}</span>
                  <div className="text-muted" style={{ fontSize: 12 }}>→ {al.action}</div>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <div className="text-muted" style={{ fontSize: 10, marginTop: 10 }}>
        {t('Computed from posted entries (AR, AP, GL, VAT, bank reconciliation). The narrative is generated locally.')}
      </div>
    </div>
  );
}
