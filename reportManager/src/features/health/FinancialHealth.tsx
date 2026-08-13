import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import ClassificationStudio from './ClassificationStudio';
import { t } from '../../utils/i18n';
import FinancialHealthCharts from './FinancialHealthCharts';

const DOT: Record<string, string> = { green: '#1db954', amber: '#f4a52a', red: '#e0353a', na: '#c7c2b8' };
const money = (v: any) => v == null ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 });

function fmtVal(v: any, fmt: string): string {
  if (v == null) return '—';
  const n = Number(v);
  if (fmt === 'pct') return n.toFixed(1) + '%';
  if (fmt === 'x') return n.toFixed(2) + '×';
  if (fmt === 'days') return Math.round(n) + ' ' + t('days');
  if (fmt === 'money') return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return String(v);
}
function scoreColor(s: number | null): string {
  if (s == null) return '#c7c2b8';
  if (s >= 80) return '#1db954';
  if (s >= 70) return '#7bc043';
  if (s >= 60) return '#f4a52a';
  return '#e0353a';
}
function arrow(curr: any, prev: any, higherBetter = true): string {
  if (curr == null || prev == null) return '';
  if (Math.abs(curr - prev) < 1e-9) return '→';
  const up = curr > prev;
  const good = higherBetter ? up : !up;
  return (up ? '↑' : '↓') + (good ? '' : '');
}

export default function FinancialHealth() {
  const [companies, setCompanies] = useState<{ name: string; label?: string }[]>([]);
  const [company, setCompany] = useState('');
  const [showClassify, setShowClassify] = useState(false);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<'cards' | 'graphical'>('cards');
  const [fy, setFy] = useState('');
  const [ai, setAi] = useState<{ loading?: boolean; text?: string; error?: string } | null>(null);
  const [expanded, setExpanded] = useState<string>('');
  const [breakdowns, setBreakdowns] = useState<Record<string, any>>({});

  function runAi() {
    setAi({ loading: true });
    api.healthAiAnalysis(company || null, fy || null)
      .then((d) => setAi({ text: d?.text || '' }))
      .catch((e: any) => setAi({ error: String(e?.message || e) }));
  }

  // ── Account Setup Advisor ──
  const [setupOpen, setSetupOpen] = useState(false);
  const [setup, setSetup] = useState<any>(null);
  const [sel, setSel] = useState<Record<string, boolean>>({});
  const [applying, setApplying] = useState(false);
  const [applyMsg, setApplyMsg] = useState<string>('');

  function openSetup() {
    setSetupOpen(true); setSetup({ loading: true }); setApplyMsg('');
    api.scanAccountTypes(company || null).then((d) => {
      setSetup(d);
      const pre: Record<string, boolean> = {};
      (d.suggestions || []).forEach((s: any) => { pre[s.account] = s.applicable && !s.is_group && (s.priority === 'High' || s.priority === 'Medium'); });
      setSel(pre);
    }).catch((e: any) => setSetup({ error: String(e?.message || e) }));
  }
  function applySetup() {
    const changes = (setup?.suggestions || [])
      .filter((s: any) => sel[s.account] && s.applicable)
      .map((s: any) => ({ account: s.account, account_type: s.recommended_type }));
    if (!changes.length) { setApplyMsg(t('Select at least one applicable change.')); return; }
    setApplying(true); setApplyMsg('');
    api.applyAccountTypes(changes, company || null).then((r) => {
      setApplyMsg(t('Applied {0}, failed {1}.').replace('{0}', String(r.applied_count)).replace('{1}', String(r.failed_count)));
      openSetup();          // re-scan
      load(company, fy);    // recompute health with the new types
    }).catch((e: any) => setApplyMsg(String(e?.message || e))).finally(() => setApplying(false));
  }

  function toggleKpi(key: string) { setExpanded((e) => e === key ? '' : key); }

  // ── EBITDA add-back tagging ──
  const [abOpen, setAbOpen] = useState(false);
  const [ab, setAb] = useState<any>(null);
  const [abEdits, setAbEdits] = useState<Record<string, string>>({}); // account → category override
  const [abSaving, setAbSaving] = useState(false);
  const [abMsg, setAbMsg] = useState<string>('');

  function openAddbacks() {
    setAbOpen(true); setAb({ loading: true }); setAbEdits({}); setAbMsg('');
    api.listEbitdaAddbacks(company || null)
      .then((d) => setAb(d))
      .catch((e: any) => setAb({ error: String(e?.message || e) }));
  }
  function setAbEdit(account: string, current: string, value: string) {
    setAbEdits((m) => {
      const n = { ...m };
      if (value === current) delete n[account]; else n[account] = value;
      return n;
    });
  }
  function saveAddbacks() {
    const changes = Object.entries(abEdits).map(([account, category]) => ({ account, category }));
    if (!changes.length) { setAbMsg(t('No changes to save.')); return; }
    setAbSaving(true); setAbMsg('');
    api.saveEbitdaAddbacks(changes, company || null).then((r) => {
      setAbMsg(t('Saved {0}, removed {1}, failed {2}.')
        .replace('{0}', String(r.saved)).replace('{1}', String(r.removed)).replace('{2}', String(r.failed_count)));
      openAddbacks();       // reload
      load(company, fy);    // recompute health with the new add-backs
    }).catch((e: any) => setAbMsg(String(e?.message || e))).finally(() => setAbSaving(false));
  }

  function fetchBreakdown(bucket: string) {
    if (!bucket || breakdowns[bucket]) return;
    setBreakdowns((b) => ({ ...b, [bucket]: { loading: true } }));
    api.healthBreakdown(bucket, company || null)
      .then((d) => setBreakdowns((b) => ({ ...b, [bucket]: d })))
      .catch(() => setBreakdowns((b) => ({ ...b, [bucket]: { rows: [], total: 0 } })));
  }

  useEffect(() => {
    api.dimensionOptions('company').then((r: any[]) => setCompanies((r || []).map((x) => ({ name: x.name, label: x.label || x.name })))).catch(() => {});
    load('');
  }, []);

  function load(c: string, fyear?: string) {
    setLoading(true); setErr(null); setAi(null);
    api.financialHealth(c || null, fyear ?? fy ?? null).then((d) => {
      setData(d);
      if (d?.company && !company) setCompany(d.company);
      if (d?.fiscal_year) setFy(d.fiscal_year);
    }).catch((e: any) => setErr(String(e?.message || e))).finally(() => setLoading(false));
  }

  const sections = data?.sections || [];
  const trend = data?.trend || [];

  return (
    <div className="fh">
      <div className="fh-hero">
        <div className="fh-hero-glow" aria-hidden />
        <div className="fh-hero-in">
          <div className="fh-brand">
            <span className="fh-pulse">❤</span>
            <div>
              <h1>{t('Financial Health of the Firm')}</h1>
              <p>{t('From accounting data to a CEO-level verdict on the business.')}</p>
            </div>
          </div>
          <div className="fh-controls">
            <select value={company} onChange={(e) => { setCompany(e.target.value); setFy(''); load(e.target.value, ''); }}>
              {companies.length === 0 && <option value="">{data?.company || t('Company')}</option>}
              {companies.map((c) => <option key={c.name} value={c.name}>{c.label}</option>)}
            </select>
            {data?.fiscal_years?.length > 0 && (
              <select value={fy} onChange={(e) => { setFy(e.target.value); load(company, e.target.value); }} title={t('Fiscal Year')}>
                {data.fiscal_years.map((y: string) => <option key={y} value={y}>{y}</option>)}
              </select>
            )}
            {data?.period && <span className="fh-period">{data.period.label} · {data.period.from} → {data.period.to}</span>}
            <div className="fh-viewtog">
              <button className={view === 'cards' ? 'on' : ''} onClick={() => setView('cards')}>{t('Cards')}</button>
              <button className={view === 'graphical' ? 'on' : ''} onClick={() => setView('graphical')}>{t('Graphical')}</button>
            </div>
            <button className="fh-setup-btn" onClick={openSetup}>⚙ {t('Fix account types')}</button>
            <button className="fh-setup-btn" onClick={openAddbacks} title={t('Tag accounts as interest/depreciation add-backs for exact EBIT/EBITDA')}>∑ {t('EBITDA add-backs')}</button>
            <button className="fh-setup-btn" onClick={() => setShowClassify(true)} title={t('Tag COGS, Cash, Investing, Financing, Provisions and your own labels — one tag drives Health, Cash Flow and Zakat')}>🏷 {t('Classification')}</button>
          </div>
        </div>
      </div>

      {showClassify && (
        <ClassificationStudio company={company} onClose={() => setShowClassify(false)}
          onSaved={() => load(company, fy)} />
      )}
      {err && <div className="studio-err">{err}</div>}
      {loading && <div className="fh-loading">{t('Calculating financial health…')}</div>}

      {setupOpen && (
        <div className="fh-setup-overlay" onClick={(e) => { if (e.target === e.currentTarget) setSetupOpen(false); }}>
          <div className="fh-setup">
            <div className="fh-setup-h">
              <div>
                <h2>⚙ {t('Account Setup Advisor')}</h2>
                <p>{t('Scanned your Chart of Accounts and found type corrections that make the ratios accurate.')}</p>
              </div>
              <button className="fh-x" onClick={() => setSetupOpen(false)} aria-label="close">×</button>
            </div>
            {setup?.loading && <div className="fh-loading">{t('Scanning the account tree…')}</div>}
            {setup?.error && <div className="studio-err">{setup.error}</div>}
            {setup && !setup.loading && !setup.error && (
              <>
                {setup.suggestions.length === 0 && <div className="fh-setup-empty">✅ {t('No account-type issues found.')}</div>}
                {setup.suggestions.length > 0 && (
                  <>
                    {!setup.can_write && <div className="fh-warn" style={{ margin: '0 0 10px' }}>⚠ {t('You can review here, but you need write access to apply changes.')}</div>}
                    <div className="fh-setup-list">
                      <div className="fh-setup-row fh-setup-head">
                        <span /><span>{t('Account')}</span><span>{t('Current')}</span><span>→ {t('Recommended')}</span><span>{t('Priority')}</span>
                      </div>
                      {setup.suggestions.map((s: any) => (
                        <label key={s.account} className={'fh-setup-row' + (!s.applicable ? ' off' : '')}>
                          <input type="checkbox" disabled={!s.applicable || !setup.can_write}
                            checked={!!sel[s.account]} onChange={() => setSel((m) => ({ ...m, [s.account]: !m[s.account] }))} />
                          <span className="fh-setup-acc" title={s.reason}>{s.account}
                            {s.is_group && <em> · {t('group')}</em>}
                            <small>{s.reason}</small>
                          </span>
                          <span className="fh-setup-cur">{s.current_type || '(blank)'}</span>
                          <span className="fh-setup-rec">{s.recommended_type}</span>
                          <span className={'fh-pri fh-pri-' + s.priority.toLowerCase()}>{t(s.priority)}</span>
                        </label>
                      ))}
                    </div>
                    <div className="fh-setup-foot">
                      {applyMsg && <span className="fh-setup-msg">{applyMsg}</span>}
                      <button className="fh-ghost" onClick={() => setSetupOpen(false)}>{t('Close')}</button>
                      <button className="fh-ai-btn" onClick={applySetup} disabled={applying || !setup.can_write}>
                        {applying ? t('Applying…') : t('Apply selected')}
                      </button>
                    </div>
                  </>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {abOpen && (
        <div className="fh-setup-overlay" onClick={(e) => { if (e.target === e.currentTarget) setAbOpen(false); }}>
          <div className="fh-setup">
            <div className="fh-setup-h">
              <div>
                <h2>∑ {t('EBITDA add-backs')}</h2>
                <p>{t('Tag interest/financing and depreciation accounts so EBIT & EBITDA are exact — independent of account name or language.')}</p>
              </div>
              <button className="fh-x" onClick={() => setAbOpen(false)} aria-label="close">×</button>
            </div>
            {ab?.loading && <div className="fh-loading">{t('Reading the chart of accounts…')}</div>}
            {ab?.error && <div className="studio-err">{ab.error}</div>}
            {ab && !ab.loading && !ab.error && (
              <>
                {!ab.can_write && <div className="fh-warn" style={{ margin: '0 0 10px' }}>⚠ {t('You can review here, but you need write access to save tags.')}</div>}
                <div className="fh-ab-legend">
                  <span><i className="fh-src manual" /> {t('Tagged by you')}</span>
                  <span><i className="fh-src type" /> {t('account_type')}</span>
                  <span><i className="fh-src keyword" /> {t('Auto (name match)')}</span>
                  <span className="fh-ab-note">
                    {ab.interest_curated
                      ? t('Interest: using your tags (auto name-match off).')
                      : t('Interest: auto name-match active until you tag one.')}
                  </span>
                </div>
                <div className="fh-setup-list fh-ab-list">
                  <div className="fh-setup-row fh-setup-head">
                    <span>{t('Account')}</span><span>{t('Type')}</span><span>{t('Counted as')}</span><span>{t('Set')}</span>
                  </div>
                  {ab.accounts.map((a: any) => {
                    const pending = abEdits[a.account];
                    const current = a.tag || '';
                    const val = pending !== undefined ? pending : current;
                    const changed = pending !== undefined && pending !== current;
                    return (
                      <div key={a.account} className={'fh-setup-row fh-ab-row' + (changed ? ' changed' : '')}>
                        <span className="fh-setup-acc" title={a.account}>
                          {a.code && <code>{a.code}</code>} {a.name}
                        </span>
                        <span className="fh-ab-type">{a.account_type || '—'}</span>
                        <span className={'fh-ab-counted ' + (a.counted_as ? a.counted_as.toLowerCase() : 'none')}>
                          {a.counted_as
                            ? <><i className={'fh-src ' + (a.source || 'keyword')} /> {t(a.counted_as)}</>
                            : '—'}
                        </span>
                        <select
                          className="fh-ab-select"
                          value={val}
                          disabled={!ab.can_write}
                          onChange={(e) => setAbEdit(a.account, current, e.target.value)}
                        >
                          <option value="">{t('— none —')}</option>
                          <option value="Interest">{t('Interest')}</option>
                          <option value="Depreciation">{t('Depreciation')}</option>
                        </select>
                      </div>
                    );
                  })}
                </div>
                <div className="fh-setup-foot">
                  {abMsg && <span className="fh-setup-msg">{abMsg}</span>}
                  <button className="fh-ghost" onClick={() => setAbOpen(false)}>{t('Close')}</button>
                  <button className="fh-ai-btn" onClick={saveAddbacks} disabled={abSaving || !ab.can_write || Object.keys(abEdits).length === 0}>
                    {abSaving ? t('Saving…') : t('Save tags')}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {data && !loading && (
        <div className="fh-body">
          {/* Overall score */}
          <div className="fh-score-card">
            <div className="fh-gauge" style={{ ['--c' as any]: scoreColor(data.overall_score), ['--p' as any]: (data.overall_score || 0) + '%' }}>
              <div className="fh-gauge-in">
                <div className="fh-score-num">{data.overall_score ?? '—'}</div>
                <div className="fh-score-den">/ 100</div>
              </div>
            </div>
            <div className="fh-score-side">
              <div className="fh-class" style={{ color: scoreColor(data.overall_score) }}>{t(data.classification)}</div>
              <div className="fh-summary">{data.summary}</div>
              <button className="fh-ai-btn" onClick={runAi} disabled={ai?.loading}>
                ✨ {ai?.loading ? t('Analyzing…') : t('AI Deep Analysis')}
              </button>
            </div>
          </div>

          {ai && (ai.text || ai.error || ai.loading) && (
            <div className="fh-ai">
              <div className="fh-ai-h">✨ {t('AI Financial Analysis')}</div>
              {ai.loading && <div className="fh-ai-loading">{t('The AI is reviewing your ratios…')}</div>}
              {ai.error && <div className="fh-ai-err">{ai.error}</div>}
              {ai.text && <div className="fh-ai-body">{ai.text.split('\n').map((ln, i) => <p key={i}>{ln}</p>)}</div>}
            </div>
          )}

          {/* Section cards */}
          {view === 'cards' && (
          <div className="fh-grid">
            {sections.map((s: any) => (
              <div key={s.name} className="fh-sec">
                <div className="fh-sec-h">
                  <div>
                    <div className="fh-sec-name">{t(s.name)}</div>
                    <div className="fh-sec-q">{t(s.question)}</div>
                  </div>
                  <div className="fh-sec-score" style={{ background: scoreColor(s.score) }}>{s.score ?? '—'}</div>
                </div>
                {s.kpis.map((k: any) => {
                  const key = s.name + '|' + k.label;
                  const open = expanded === key;
                  return (
                    <div key={k.label} className={'fh-kpi-wrap' + (open ? ' open' : '')}>
                      <div className="fh-kpi" onClick={() => toggleKpi(key)} onDoubleClick={() => toggleKpi(key)} title={t('Click to see how this is calculated')}>
                        <span className="fh-dot" style={{ background: DOT[k.status] }} />
                        <span className="fh-kpi-l">{t(k.label)}</span>
                        <span className="fh-kpi-v">{fmtVal(k.value, k.fmt)}</span>
                        <span className="fh-caret">{open ? '▾' : '▸'}</span>
                      </div>
                      {k.warn && <div className="fh-warn">⚠ {t(k.warn)}</div>}
                      {open && k.detail && (
                        <div className="fh-evidence">
                          <div className="fh-formula">{t(k.detail.formula)}</div>
                          <div className="fh-parts">
                            {k.detail.parts.map((p: any, i: number) => (
                              <div key={i} className="fh-part">
                                <span className="fh-part-l">{t(p.label)}</span>
                                <span className="fh-part-v">{money(p.value)}</span>
                                {p.bucket && (
                                  <button className="fh-evi-btn" onClick={() => fetchBreakdown(p.bucket)}>{t('accounts')}</button>
                                )}
                                {p.bucket && breakdowns[p.bucket] && !breakdowns[p.bucket].loading && (
                                  <div className="fh-accts">
                                    {(breakdowns[p.bucket].rows || []).slice(0, 30).map((r: any) => (
                                      <div key={r.account} className="fh-acct"><span>{r.account}</span><span className="num">{money(r.value)}</span></div>
                                    ))}
                                    {(breakdowns[p.bucket].rows || []).length === 0 && <div className="fh-acct fh-acct-empty">{t('No posted accounts in this bucket.')}</div>}
                                    <div className="fh-acct fh-acct-total"><span>{t('Total')}</span><span className="num">{money(breakdowns[p.bucket].total)}</span></div>
                                  </div>
                                )}
                                {p.bucket && breakdowns[p.bucket]?.loading && <div className="fh-accts fh-acct-empty">{t('Loading…')}</div>}
                              </div>
                            ))}
                          </div>
                          {k.note && <div className="fh-evi-note">{t(k.note)}</div>}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}

            {/* Key Figures — fills the layout and gives the headline numbers */}
            <div className="fh-sec fh-info">
              <div className="fh-sec-h"><div>
                <div className="fh-sec-name">{t('Key Figures')}</div>
                <div className="fh-sec-q">{t('The period\u2019s headline numbers')}</div>
              </div></div>
              {[
                ['Revenue', 'revenue'], ['Gross Profit', 'gross_profit'], ['Net Income', 'net_income'],
                ['Total Assets', 'assets'], ['Current Assets', 'current_assets'],
                ['Total Liabilities', 'liabilities'], ['Equity', 'equity'],
                ['Cash & Bank', 'cash'], ['Inventory', 'inventory'],
                ['Receivables', 'receivables'], ['Payables', 'payables'],
              ].map(([label, key]) => (
                <div key={key} className="fh-fig">
                  <span className="fh-fig-l">{t(label)}</span>
                  <span className="fh-fig-v">{money(data.raw?.[key])}</span>
                </div>
              ))}
            </div>

            {/* How to read this — legend + healthy targets */}
            <div className="fh-sec fh-info">
              <div className="fh-sec-h"><div>
                <div className="fh-sec-name">{t('How to read this')}</div>
                <div className="fh-sec-q">{t('Status & healthy targets')}</div>
              </div></div>
              <div className="fh-legend">
                <span><i style={{ background: DOT.green }} /> {t('Healthy')}</span>
                <span><i style={{ background: DOT.amber }} /> {t('Watch')}</span>
                <span><i style={{ background: DOT.red }} /> {t('Needs attention')}</span>
                <span><i style={{ background: DOT.na }} /> {t('No data')}</span>
              </div>
              {[
                ['Current Ratio', '> 1.5'], ['Quick Ratio', '> 1.0'], ['Net Profit Margin', t('positive & growing')],
                ['DSO', '< 45 ' + t('days')], ['Debt-to-Equity', '< 1.5'], ['ROA', '> 5%'],
                ['ROE', '> 10%'], ['Interest Coverage', '> 3×'],
              ].map(([label, tgt]) => (
                <div key={label} className="fh-fig">
                  <span className="fh-fig-l">{t(label)}</span>
                  <span className="fh-fig-tgt">{tgt}</span>
                </div>
              ))}
            </div>
          </div>
          )}

          {view === 'graphical' && <FinancialHealthCharts data={data} />}

          {/* Trend */}
          {view === 'cards' && trend.length > 1 && (
            <div className="fh-trend">
              <div className="fh-trend-h">{t('Trend')} <small>({t('direction matters more than the number')})</small></div>
              <div className="fh-table-wrap">
                <table className="fh-table">
                  <thead><tr>
                    <th>{t('Ratio')}</th>
                    {trend.map((y: any) => <th key={y.year} className="num">{y.year}</th>)}
                  </tr></thead>
                  <tbody>
                    {[
                      { k: 'current_ratio', l: 'Current Ratio', f: 'x', hb: true },
                      { k: 'net_margin', l: 'Net Profit Margin', f: 'pct', hb: true },
                      { k: 'roe', l: 'Return on Equity (ROE)', f: 'pct', hb: true },
                      { k: 'dso', l: 'Days Sales Outstanding (DSO)', f: 'days', hb: false },
                      { k: 'debt_equity', l: 'Debt-to-Equity', f: 'x', hb: false },
                    ].map((row) => (
                      <tr key={row.k}>
                        <td>{t(row.l)}</td>
                        {trend.map((y: any, i: number) => (
                          <td key={y.year} className="num">
                            {fmtVal(y[row.k], row.f)}
                            {i === trend.length - 1 && <span className="fh-arrow">{arrow(y[row.k], trend[i - 1]?.[row.k], row.hb)}</span>}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="fh-note">
            {t('Ratios are computed from your General Ledger using ERP account types (root type for Assets/Liabilities/Equity/Income/Expense; account type for Receivable, Payable, Stock, Bank/Cash, Cost of Goods Sold). Current liabilities ≈ total liabilities, and interest/depreciation are detected by account name — verify classification for precise figures.')}
          </div>
        </div>
      )}
    </div>
  );
}
