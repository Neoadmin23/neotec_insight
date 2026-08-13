import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { fmtD } from '../../utils/format';
import { csvHeader } from '../../utils/export';
import BillwiseView from './BillwiseView';
import PartyTreePicker from './PartyTreePicker';

const LS = {
  excl: 'ni-ageing-excl-',
  slabsDays: 'ni-ageing-slabs-days',
  slabsMonths: 'ni-ageing-slabs-months',
  topN: 'ni-ageing-topn',
  mode: 'ni-ageing-mode',
  basedOn: 'ni-ageing-basedon',
  alloc: 'ni-ageing-alloc',
};
const get = (k: string, d: string) => { try { return localStorage.getItem(k) || d; } catch { return d; } };
const put = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } };

function todayISO() { return new Date().toISOString().slice(0, 10); }

/** AR / AP Ageing (v2.30.0) — GL-based FIFO ageing with user-defined slabs
 *  (days or calendar months, persisted until changed) and Top-N parties. */
export default function AgeingTab() {
  const [companies, setCompanies] = useState<{ name: string; label: string }[]>([]);
  const [company, setCompany] = useState('');
  const [asOf, setAsOf] = useState(todayISO());
  const [side, setSide] = useState<'Customer' | 'Supplier'>('Customer');
  const [mode, setMode] = useState<'days' | 'months'>(() => get(LS.mode, 'days') as any);
  const [basedOn, setBasedOn] = useState<'due' | 'posting'>(() => get(LS.basedOn, 'due') as any);
  const [alloc, setAlloc] = useState<'actual' | 'fifo'>(() => get(LS.alloc, 'actual') as any);
  const [slabs, setSlabs] = useState(() => get(LS.slabsDays, '30,60,90,120'));
  const [topN, setTopN] = useState(() => get(LS.topN, '10'));
  const [view, setView] = useState<'summary' | 'billwise'>('summary');
  const [excl, setExcl] = useState<string[]>(() => { try { return JSON.parse(get(LS.excl + 'Customer', '[]')); } catch { return []; } });
  const [exclOpen, setExclOpen] = useState(false);
  const [exclQuery, setExclQuery] = useState('');
  const [exclHits, setExclHits] = useState<any[]>([]);
  // null = all parties (summary default); array = explicit selection
  const [treeSel, setTreeSel] = useState<string[] | null>(null);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.dimensionOptions('company').then((r: any[]) => {
      const cs = (r || []).map((x) => ({ name: x.name, label: x.label || x.name }));
      setCompanies(cs);
      const c = cs[0]?.name || '';
      setCompany(c);
      run(c, side, mode, slabs, basedOn, topN);
    }).catch(() => run('', side, mode, slabs, basedOn, topN));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function switchMode(m: 'days' | 'months') {
    const s = get(m === 'days' ? LS.slabsDays : LS.slabsMonths, m === 'days' ? '30,60,90,120' : '1,2,3,6');
    setMode(m); setSlabs(s); put(LS.mode, m);
    run(company, side, m, s, basedOn, topN);
  }

  function loadExcl(s: 'Customer' | 'Supplier'): string[] {
    let e: string[] = [];
    try { e = JSON.parse(get(LS.excl + s, '[]')); } catch { /* ignore */ }
    setExcl(e); return e;
  }
  // Disputed / legal-hold parties stay OFF the report — per side, persisted.
  function toggleExcl(party: string) {
    const next = excl.includes(party) ? excl.filter((x) => x !== party) : [...excl, party];
    setExcl(next); put(LS.excl + side, JSON.stringify(next));
    run(company, side, mode, slabs, basedOn, topN, next);
  }

  function run(c = company, s = side, m = mode, sl = slabs, b = basedOn, tn = topN, ex = excl, al = alloc, inc = treeSel) {
    setLoading(true); setErr(null);
    // The slabs the user typed persist until they change them again.
    put(m === 'days' ? LS.slabsDays : LS.slabsMonths, sl);
    put(LS.topN, tn); put(LS.basedOn, b); put(LS.alloc, al);
    api.arApAgeing(c || null, asOf, s, b, m, sl, parseInt(tn) || 0, ex, al, inc || [])
      .then((d) => { setData(d); if (d?.company && !company) setCompany(d.company); })
      .catch((e: any) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  }

  function exportCsv() {
    if (!data) return;
    const labels = data.labels.map((l: any) => l.en);
    const head = ['#', 'Party', 'Invoiced', 'Paid', 'Outstanding', ...labels, 'Total Due'];
    const hdr = csvHeader(data.company || '', `${side === 'Customer' ? 'AR' : 'AP'} Ageing`, `As of ${asOf}`);
    const line = (r: any, i: number | string) =>
      [i, `"${r.label}"`, r.invoiced, r.paid, r.outstanding, ...r.buckets, r.outstanding].join(',');
    const rows = [...hdr, head.join(',')]
      .concat(data.rows.map((r: any, i: number) => line(r, i + 1)))
      .concat(data.others ? [line(data.others, '')] : [])
      .concat([line({ ...data.total, label: 'TOTAL' }, '')]);
    const blob = new Blob(['\ufeff' + rows.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${side === 'Customer' ? 'AR' : 'AP'}-ageing-${asOf}.csv`;
    a.click(); URL.revokeObjectURL(a.href);
  }

  const cur = data?.currency || 'SAR';
  const amt = (v: number, strong = false) => (
    <td className={'vat-num vat-vatcol' + (strong ? ' z-strong' : '') + (v < 0 ? ' cf-neg' : '')}>
      {v === 0 ? '—' : v < 0 ? `(${fmtD(Math.abs(v), 2)})` : fmtD(v, 2)}
    </td>
  );
  const rowCells = (r: any) => (
    <>
      <td className="vat-box-label">{r.label}</td>
      {amt(r.invoiced)}{amt(r.paid)}{amt(r.outstanding, true)}
      {r.buckets.map((b: number, i: number) => <span key={i} style={{ display: 'contents' }}>{amt(b)}</span>)}
      {amt(r.outstanding, true)}
    </>
  );

  return (
    <div className="vat-wrap">
      <div className="vat-hero">
        <div>
          <h1>{side === 'Customer' ? t('AR Ageing') : t('AP Ageing')} <span className="vat-badge">{alloc === 'actual' ? t('Actual allocation') : t('FIFO')}</span></h1>
          <p>{t('Receivables and payables aged into your own slabs — days or calendar months — with payments allocated to the oldest items first and advances shown as negatives.')}</p>
        </div>
      </div>

      <div className="vat-controls">
        <div className="vat-quick">
          <button className={side === 'Customer' ? 'on' : ''} onClick={() => { const e = loadExcl('Customer'); setSide('Customer'); setTreeSel(null); run(company, 'Customer', undefined, undefined, undefined, undefined, e, undefined, null); }}>{t('Receivables (AR)')}</button>
          <button className={side === 'Supplier' ? 'on' : ''} onClick={() => { const e = loadExcl('Supplier'); setSide('Supplier'); setTreeSel(null); run(company, 'Supplier', undefined, undefined, undefined, undefined, e, undefined, null); }}>{t('Payables (AP)')}</button>
        </div>
        <label><span className="flbl">{t('Company')}</span>
          <select value={company} onChange={(e) => setCompany(e.target.value)}>
            {companies.map((c) => <option key={c.name} value={c.name}>{c.label}</option>)}
          </select>
        </label>
        <label><span className="flbl">{t('As of')}</span>
          <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>
        <label><span className="flbl">{t('Ageing by')}</span>
          <select value={mode} onChange={(e) => switchMode(e.target.value as any)}>
            <option value="days">{t('Days')}</option>
            <option value="months">{t('Months')}</option>
          </select>
        </label>
        <label><span className="flbl">{t('Slabs')} ({mode === 'days' ? t('days') : t('months')})</span>
          <input value={slabs} style={{ width: 140 }} title={t('Comma-separated boundaries — e.g. 30,60,90,120. Remembered until you change them.')}
            onChange={(e) => setSlabs(e.target.value)} />
        </label>
        <label><span className="flbl">{t('Allocation')}</span>
          <select value={alloc} onChange={(e) => setAlloc(e.target.value as any)}
            title={t('Actual: each payment settles the invoice it was applied to (Payment Entry references) — matches Bill-wise. FIFO: oldest-first estimate for ledgers without references.')}>
            <option value="actual">{t('Actual (as linked)')}</option>
            <option value="fifo">{t('FIFO (estimate)')}</option>
          </select>
        </label>
        <label><span className="flbl">{t('Based on')}</span>
          <select value={basedOn} onChange={(e) => setBasedOn(e.target.value as any)}>
            <option value="due">{t('Due date')}</option>
            <option value="posting">{t('Posting date')}</option>
          </select>
        </label>
        <label><span className="flbl">{t('Top')}</span>
          <select value={topN} onChange={(e) => setTopN(e.target.value)}>
            <option value="5">{t('Top 5')}</option>
            <option value="10">{t('Top 10')}</option>
            <option value="20">{t('Top 20')}</option>
            <option value="0">{t('All parties')}</option>
          </select>
        </label>
        <div className="vat-quick">
          <button className={view === 'summary' ? 'on' : ''} onClick={() => setView('summary')}>{t('Summary')}</button>
          <button className={view === 'billwise' ? 'on' : ''} onClick={() => setView('billwise')}>{t('Bill-wise')}</button>
        </div>
        <PartyTreePicker side={side} selected={treeSel} onChange={setTreeSel} />
        {view === 'summary' && (
          <span className="navgrp" onClick={(e) => e.stopPropagation()}>
            <button className="vat-ghost" onClick={() => setExclOpen((o) => !o)}>
              {t('Excluded')}{excl.length ? ` (${excl.length})` : ''} ▾
            </button>
            {exclOpen && (
              <div className="navgrp-menu" style={{ minWidth: 320 }}>
                <div className="navgrp-title">{t('Parties kept off the report (dispute / legal hold)')}</div>
                {excl.map((p) => (
                  <div key={p} className="navgrp-item" style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span>{p}</span>
                    <button className="studio-ghost" onClick={() => toggleExcl(p)}>{t('Restore')}</button>
                  </div>
                ))}
                <div style={{ padding: '6px 10px' }}>
                  <input placeholder={t('Search party to exclude…')} value={exclQuery} style={{ width: '100%' }}
                    onChange={(e) => setExclQuery(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') api.ageingListParties(company || null, side, exclQuery).then(setExclHits); }} />
                  {exclHits.map((h) => (
                    <div key={h.name} className="navgrp-item" style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span>{h.label || h.name}</span>
                      <button className="studio-ghost" onClick={() => { toggleExcl(h.name); setExclHits([]); setExclQuery(''); }}>{t('Exclude')}</button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </span>
        )}
        <button className="vat-run" onClick={() => run()} disabled={loading}>{loading ? t('Calculating…') : t('Generate')}</button>
        {data && <button className="vat-ghost" onClick={exportCsv}>{t('Export CSV')}</button>}
      </div>

      {err && <div className="studio-err">{err}</div>}

      {view === 'billwise' && (
        <BillwiseView company={company} side={side} asOf={asOf} basedOn={basedOn} mode={mode} slabs={slabs} treeSel={treeSel} />
      )}

      {view === 'summary' && data && !loading && (
        <>
          <div className="vat-kpis">
            {data.labels.map((l: any, i: number) => (
              <div className="vat-kpi" key={l.key}>
                <span className="vat-kpi-l">{l.en}</span>
                <span className="vat-kpi-v">{fmtD(data.total.buckets[i], 2)}</span>
              </div>
            ))}
            <div className={'vat-kpi vat-kpi-net ' + (data.total.outstanding >= 0 ? 'pay' : 'refund')}>
              <span className="vat-kpi-l">{t('Total outstanding')}</span>
              <span className="vat-kpi-v">{fmtD(data.total.outstanding, 2)} {cur}</span>
            </div>
          </div>

          <table className="vat-form">
            <thead>
              <tr>
                <th>{side === 'Customer' ? t('Customer') : t('Supplier')}</th>
                <th className="vat-num">{t('Invoiced')}</th>
                <th className="vat-num">{t('Paid')}</th>
                <th className="vat-num">{t('Outstanding')}</th>
                {data.labels.map((l: any) => <th key={l.key} className="vat-num">{l.en}</th>)}
                <th className="vat-num">{t('Total Due')}</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r: any) => <tr key={r.party}>{rowCells(r)}</tr>)}
              {data.others && <tr style={{ fontStyle: 'italic' }}>{rowCells(data.others)}</tr>}
              <tr className="vat-grand pay">{rowCells({ ...data.total, label: t('TOTAL') })}</tr>
            </tbody>
          </table>

          <div className="vat-disclaimer">
            ⚠ {(data.notes || []).join(' ')}
          </div>
        </>
      )}
    </div>
  );
}
