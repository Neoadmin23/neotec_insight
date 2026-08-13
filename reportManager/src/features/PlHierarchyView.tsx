import { useEffect, useState } from 'react';
import { t } from '../utils/i18n';
import { fmtD } from '../utils/format';

/* ─── PlHierarchyView (v1.9.99) ───────────────────────────────────────────
 * Renders report.pl_hierarchy: a multi-level P&L drill —
 *   Primary dimension (e.g. Cost Center)
 *     → Secondary dimension (e.g. Intercompany)
 *       → Section (Revenue / Cost of Sales / Operating Expenses)
 *         → Account
 * with Gross Profit, Net Profit and margins computed at every node, plus a
 * grand-total strip. Each level expands/collapses independently.
 */

interface AccountRow { account: string; code: string; label: string; amount: number; }
interface Section { section: string; label: string; amount: number; accounts: AccountRow[]; }
interface SecondaryNode {
  key: string; label: string;
  revenue: number; cogs: number; gross_profit: number; opex: number; net_profit: number;
  gross_margin: number; net_margin: number; sections: Section[];
}
interface PrimaryNode {
  key: string; label: string;
  revenue: number; cogs: number; gross_profit: number; opex: number; net_profit: number;
  gross_margin: number; net_margin: number; rev_share: number; children: SecondaryNode[];
}
interface PlHierResult {
  view: 'pl_hierarchy';
  primary_dim: { fieldname: string; label: string };
  secondary_dim: { fieldname: string; label: string } | null;
  period: { from: string; to: string };
  currency?: string;
  tree: PrimaryNode[];
  grand: {
    revenue: number; cogs: number; gross_profit: number; opex: number; net_profit: number;
    gross_margin: number; net_margin: number;
  };
}

const SECTION_SIGN: Record<string, number> = { revenue: 1, cogs: -1, opex: -1 };

export function PlHierarchyView({ data, decimals = 0, defaultExpand = false }: { data: PlHierResult; decimals?: number; defaultExpand?: boolean }) {
  const [openP, setOpenP] = useState<Set<string>>(() => new Set(data.tree.slice(0, 1).map((n) => n.key)));
  const [openS, setOpenS] = useState<Set<string>>(new Set());
  const [openSec, setOpenSec] = useState<Set<string>>(new Set());

  const tg = (set: Set<string>, setSet: (s: Set<string>) => void, k: string) => {
    const n = new Set(set); n.has(k) ? n.delete(k) : n.add(k); setSet(n);
  };
  const expandAll = () => {
    const p = new Set<string>(); const s = new Set<string>();
    for (const pn of data.tree) { p.add(pn.key); for (const sn of pn.children) s.add(pn.key + '||' + sn.key); }
    setOpenP(p); setOpenS(s);
  };
  const collapseAll = () => { setOpenP(new Set()); setOpenS(new Set()); setOpenSec(new Set()); };

  // v2.19 — react to the report header's Default Row Expand selector.
  useEffect(() => {
    if (defaultExpand) expandAll(); else collapseAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultExpand, data]);

  const num = (v: number) => fmtD(v, decimals);
  const cur = data.currency || '';
  const KPI_DEFS: Record<string, { label: string; field: string; marginField?: string; accent?: boolean; dark?: boolean }> = {
    revenue: { label: 'Revenue', field: 'revenue' },
    cogs: { label: 'Cost of Sales', field: 'cogs' },
    gross_profit: { label: 'Gross Profit', field: 'gross_profit', marginField: 'gross_margin', accent: true },
    opex: { label: 'Operating Expenses', field: 'opex' },
    net_profit: { label: 'Net Profit', field: 'net_profit', marginField: 'net_margin', dark: true },
  };
  const [kpiOrder, setKpiOrder] = useState<string[]>(() => {
    try { const v = JSON.parse(localStorage.getItem('ni-plh-kpis') || 'null'); if (Array.isArray(v) && v.length) return v; } catch { /* */ }
    return ['revenue', 'cogs', 'gross_profit', 'opex', 'net_profit'];
  });
  const [kpiOpen, setKpiOpen] = useState(false);
  const moveKpi = (k: string, d: number) => {
    const i = kpiOrder.indexOf(k); if (i < 0) return;
    const j = i + d; if (j < 0 || j >= kpiOrder.length) return;
    const next = [...kpiOrder]; [next[i], next[j]] = [next[j], next[i]];
    setKpiOrder(next); try { localStorage.setItem('ni-plh-kpis', JSON.stringify(next)); } catch { /* */ }
  };
  const margin = (v: number) => (v || v === 0 ? `${v}%` : '');

  return (
    <div className="plh">
      <div className="plh-bar">
        <div className="plh-title">
          {t('P&L Drill')} — {data.primary_dim.label}
          {data.secondary_dim ? ` › ${data.secondary_dim.label}` : ''}
          {(data as any).buckets_source && (
            <span className="vat-badge" style={{ marginInlineStart: 10, verticalAlign: 'middle' }}
              title={(data as any).buckets_source === 'definition'
                ? `Buckets from the report definition — ${(data as any).buckets_mapped?.revenue_accounts ?? 0} revenue / ${(data as any).buckets_mapped?.cogs_accounts ?? 0} cost accounts mapped`
                : 'Definition unavailable — account-type heuristics in use; totals may differ from Standard. Check Error Log.'}>
              {(data as any).buckets_source === 'definition' ? '✓ definition' : '⚠ heuristic'}
            </span>
          )}
        </div>
        <div className="plh-actions">
          <button onClick={expandAll}>{t('Expand all')}</button>
          <button onClick={collapseAll}>{t('Collapse all')}</button>
        </div>
      </div>

      {/* Grand total strip */}
      <div className="plh-grand">
        {/* v2.41.0 — KPI picker: cards chosen/ordered by the user, persisted */}
        {kpiOrder.map((k) => {
          const def = KPI_DEFS[k];
          if (!def) return null;
          return <Metric key={k} label={t(def.label)} value={num((data.grand as any)[def.field])}
            sub={def.marginField ? margin((data.grand as any)[def.marginField]) : undefined}
            accent={def.accent} strong={def.dark} />;
        })}
        <span className="navgrp" onClick={(e) => e.stopPropagation()}>
          <button className="vat-ghost" style={{ alignSelf: 'center' }} title={t('Choose KPI cards')}
            onClick={() => setKpiOpen((o) => !o)}>⚙</button>
          {kpiOpen && (
            <div className="navgrp-menu" style={{ minWidth: 240 }}>
              {Object.keys(KPI_DEFS).map((k) => (
                <label key={k} className="navgrp-item" style={{ display: 'flex', gap: 8 }}>
                  <input type="checkbox" checked={kpiOrder.includes(k)}
                    onChange={() => {
                      const next = kpiOrder.includes(k) ? kpiOrder.filter((x) => x !== k) : [...kpiOrder, k];
                      setKpiOrder(next); try { localStorage.setItem('ni-plh-kpis', JSON.stringify(next)); } catch { /* */ }
                    }} /> {t(KPI_DEFS[k].label)}
                  <span style={{ marginInlineStart: 'auto', display: 'flex', gap: 4 }}>
                    <button className="studio-ghost" onClick={(e) => { e.preventDefault(); moveKpi(k, -1); }}>↑</button>
                    <button className="studio-ghost" onClick={(e) => { e.preventDefault(); moveKpi(k, 1); }}>↓</button>
                  </span>
                </label>
              ))}
            </div>
          )}
        </span>

      </div>

      <div className="plh-table">
        <div className="plh-head">
          <span className="plh-c-label">{data.primary_dim.label} / {data.secondary_dim?.label || t('Account')}</span>
          <span className="plh-c-num">{t('Revenue')}</span>
          <span className="plh-c-num">{t('Gross Profit')}</span>
          <span className="plh-c-num">{t('Net Profit')}</span>
          <span className="plh-c-num">{t('Net %')}</span>
        </div>

        {data.tree.map((p) => {
          const pOpen = openP.has(p.key);
          return (
            <div key={p.key} className="plh-pblock">
              <div className="plh-row lvl-p" onClick={() => tg(openP, setOpenP, p.key)}>
                <span className="plh-c-label">
                  <span className="plh-caret">{pOpen ? '▾' : '▸'}</span>
                  <strong>{p.label}</strong>
                  <span className="plh-share">{p.rev_share}% {t('of revenue')}</span>
                </span>
                <span className="plh-c-num">{num(p.revenue)}</span>
                <span className="plh-c-num">{num(p.gross_profit)}</span>
                <span className={'plh-c-num ' + (p.net_profit < 0 ? 'neg' : 'pos')}>{num(p.net_profit)}</span>
                <span className="plh-c-num">{margin(p.net_margin)}</span>
              </div>

              {pOpen && p.children.map((s) => {
                const sKey = p.key + '||' + s.key;
                const sOpen = openS.has(sKey);
                return (
                  <div key={sKey} className="plh-sblock">
                    <div className="plh-row lvl-s" onClick={() => tg(openS, setOpenS, sKey)}>
                      <span className="plh-c-label" style={{ paddingInlineStart: 22 }}>
                        <span className="plh-caret">{sOpen ? '▾' : '▸'}</span>
                        {s.label}
                      </span>
                      <span className="plh-c-num">{num(s.revenue)}</span>
                      <span className="plh-c-num">{num(s.gross_profit)}</span>
                      <span className={'plh-c-num ' + (s.net_profit < 0 ? 'neg' : 'pos')}>{num(s.net_profit)}</span>
                      <span className="plh-c-num">{margin(s.net_margin)}</span>
                    </div>

                    {sOpen && (
                      <div className="plh-pl">
                        {s.sections.map((sec) => {
                          const secKey = sKey + '||' + sec.section;
                          const secOpen = openSec.has(secKey);
                          return (
                            <div key={secKey}>
                              <div className="plh-row lvl-sec" onClick={() => tg(openSec, setOpenSec, secKey)}>
                                <span className="plh-c-label" style={{ paddingInlineStart: 40 }}>
                                  <span className="plh-caret">{sec.accounts.length ? (secOpen ? '▾' : '▸') : ''}</span>
                                  {sec.label}
                                </span>
                                <span className="plh-c-num plh-secval" style={{ gridColumn: '2 / span 4' }}>
                                  {SECTION_SIGN[sec.section] < 0 && sec.amount ? `(${num(sec.amount)})` : num(sec.amount)} {cur}
                                </span>
                              </div>
                              {secOpen && sec.accounts.map((a) => (
                                <div key={a.account} className="plh-row lvl-acct">
                                  <span className="plh-c-label" style={{ paddingInlineStart: 60 }}>{a.label}</span>
                                  <span className="plh-c-num plh-secval" style={{ gridColumn: '2 / span 4' }}>
                                    {SECTION_SIGN[sec.section] < 0 && a.amount ? `(${num(a.amount)})` : num(a.amount)}
                                  </span>
                                </div>
                              ))}
                            </div>
                          );
                        })}
                        {/* calculated lines */}
                        <div className="plh-row lvl-calc">
                          <span className="plh-c-label" style={{ paddingInlineStart: 40 }}><strong>{t('Gross Profit')}</strong></span>
                          <span className="plh-c-num plh-secval" style={{ gridColumn: '2 / span 4' }}><strong>{num(s.gross_profit)}</strong> · {margin(s.gross_margin)}</span>
                        </div>
                        <div className="plh-row lvl-calc strong">
                          <span className="plh-c-label" style={{ paddingInlineStart: 40 }}><strong>{t('Net Profit')}</strong></span>
                          <span className="plh-c-num plh-secval" style={{ gridColumn: '2 / span 4' }}><strong>{num(s.net_profit)}</strong> · {margin(s.net_margin)}</span>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}

        <div className="plh-row lvl-grand">
          <span className="plh-c-label"><strong>{t('Grand Total')}</strong></span>
          <span className="plh-c-num"><strong>{num(data.grand.revenue)}</strong></span>
          <span className="plh-c-num"><strong>{num(data.grand.gross_profit)}</strong></span>
          <span className={'plh-c-num ' + (data.grand.net_profit < 0 ? 'neg' : 'pos')}><strong>{num(data.grand.net_profit)}</strong></span>
          <span className="plh-c-num"><strong>{margin(data.grand.net_margin)}</strong></span>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value, sub, accent, strong }: { label: string; value: string; sub?: string; accent?: boolean; strong?: boolean }) {
  return (
    <div className={'plh-metric' + (accent ? ' accent' : '') + (strong ? ' strong' : '')}>
      <div className="plh-m-label">{label}</div>
      <div className="plh-m-value">{value}</div>
      {sub ? <div className="plh-m-sub">{sub}</div> : null}
    </div>
  );
}
