import { useEffect, useState } from 'react';
import { jsPDF } from 'jspdf';
import autoTable from 'jspdf-autotable';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import { fmtD } from '../../utils/format';
import { csvHeader } from '../../utils/export';
import VatAdjustments from './VatAdjustments';

// `box` is a string as well as a number: the government line is numbered '1.2'
// and its number comes from the GTPL rule, because ZATCA has renumbered this
// return before.
type Line = { box: number | string; label: string; amount: number; adjustment: number; vat: number; zero_vat?: boolean; system?: boolean };

function todayISO() { return new Date().toISOString().slice(0, 10); }
function monthStart() { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10); }

export default function VatReturn() {
  const [companies, setCompanies] = useState<{ name: string; label: string }[]>([]);
  const [company, setCompany] = useState('');
  const [fromDate, setFromDate] = useState(monthStart());
  const [toDate, setToDate] = useState(todayISO());
  const [data, setData] = useState<any>(null);
  const [showAdj, setShowAdj] = useState(false);
  const [drillBusy, setDrillBusy] = useState('');

  // v2.32.0 — checkbox in the drill: unticking an invoice EXCLUDES it from
  // this return (a governed VAT Adjustment with a mandatory reason); ticking
  // an excluded one re-includes it. The return, packs and audit trail follow.
  async function drillExclude(r: any, dt: string) {
    const reason = prompt(t('Reason for excluding this invoice from the return (audit trail):'),
      t('Not paid in the period — VAT deferred to payment quarter'));
    if (!reason) return;
    setDrillBusy(r.name);
    try {
      await api.saveVatAdjustment({
        company: company || data?.company || null, from_date: fromDate, to_date: toDate,
        voucher_type: dt, voucher_no: r.name, action: 'Exclude', reason,
      });
      await refreshDrill(); load(company);
    } catch (e: any) { alert(String(e?.message || e)); }
    finally { setDrillBusy(''); }
  }
  async function drillReinclude(r: any, dt: string) {
    setDrillBusy(r.name);
    try {
      await api.clearVatAdjustment(company || data?.company || null, fromDate, toDate, dt, r.name);
      await refreshDrill(); load(company);
    } catch (e: any) { alert(String(e?.message || e)); }
    finally { setDrillBusy(''); }
  }
  async function refreshDrill() {
    if (!drill) return;
    const d = await api.vatBoxDrill(company || data?.company || null, fromDate, toDate, drill.box);
    setDrill({ ...d, box: drill.box });
  }
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [drill, setDrill] = useState<{ box: number | string; rows: any[]; doctype: string } | null>(null);
  // Manual ZATCA fields
  const [box14, setBox14] = useState(0); // corrections from previous period
  const [box15, setBox15] = useState(0); // credit carried forward

  useEffect(() => {
    api.dimensionOptions('company').then((r: any[]) => setCompanies((r || []).map((x) => ({ name: x.name, label: x.label || x.name })))).catch(() => {});
    load('');
  }, []);

  function load(c: string) {
    setLoading(true); setErr(null); setDrill(null);
    api.vatReturn(c || company || null, fromDate, toDate)
      .then((d) => { setData(d); if (d?.company && !company) setCompany(d.company); })
      .catch((e: any) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  }

  function quickPeriod(kind: 'this_month' | 'last_month' | 'this_quarter') {
    const now = new Date();
    let f: Date, tt: Date;
    if (kind === 'this_month') { f = new Date(now.getFullYear(), now.getMonth(), 1); tt = new Date(now.getFullYear(), now.getMonth() + 1, 0); }
    else if (kind === 'last_month') { f = new Date(now.getFullYear(), now.getMonth() - 1, 1); tt = new Date(now.getFullYear(), now.getMonth(), 0); }
    else { const q = Math.floor(now.getMonth() / 3); f = new Date(now.getFullYear(), q * 3, 1); tt = new Date(now.getFullYear(), q * 3 + 3, 0); }
    setFromDate(f.toISOString().slice(0, 10)); setToDate(tt.toISOString().slice(0, 10));
  }

  function openDrill(box: number | string) {
    api.vatBoxDrill(company || null, fromDate, toDate, box)
      .then((d) => setDrill({ box, rows: d.rows || [], doctype: d.doctype }))
      .catch(() => {});
  }

  const cur = data?.currency || 'SAR';
  const salesLines: Line[] = data?.sales_lines || [];
  const purchLines: Line[] = data?.purchase_lines || [];
  const box6: Line | null = data?.box6 || null;
  const box12: Line | null = data?.box12 || null;
  const box13 = data ? data.net.box13 : 0;
  const box16 = data ? box13 + box14 - box15 : 0;

  function exportCsv() {
    if (!data) return;
    const rows: string[] = [
      ...csvHeader(data.company || '', 'VAT Return — ZATCA 16-box', `${fromDate} to ${toDate}`),
      ['Box', 'Description', 'Amount', 'Adjustment', 'VAT Amount'].join(','),
    ];
    const push = (l: Line) => rows.push([l.box, `"${l.label}"`, l.amount.toFixed(2), l.adjustment.toFixed(2), l.zero_vat ? '0.00' : l.vat.toFixed(2)].join(','));
    rows.push('"VAT on Sales",,,,');
    salesLines.forEach(push); if (box6) push(box6 as any);
    rows.push('"VAT on Purchases",,,,');
    purchLines.forEach(push); if (box12) push(box12 as any);
    rows.push(`13,"Total VAT due for period",,,${box13.toFixed(2)}`);
    rows.push(`14,"Corrections from previous period",,,${box14.toFixed(2)}`);
    rows.push(`15,"VAT credit carried forward",,,${box15.toFixed(2)}`);
    rows.push(`16,"Net VAT due (or reclaimable)",,,${box16.toFixed(2)}`);
    const blob = new Blob([rows.join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob); const a = document.createElement('a');
    a.href = url; a.download = `vat-return-${fromDate}-to-${toDate}.csv`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // Official ZATCA VAT return box labels (English presentation of the form).
  const ZATCA_LABELS: Record<number, string> = {
    1: 'Standard rated sales',
    2: 'Private healthcare / private education to citizens',
    3: 'Zero-rated domestic sales',
    4: 'Exports',
    5: 'Exempt sales',
    6: 'Total sales',
    7: 'Standard rated domestic purchases',
    8: 'Imports subject to VAT (paid at customs)',
    9: 'Imports subject to VAT (reverse charge mechanism)',
    10: 'Zero-rated purchases',
    11: 'Exempt purchases',
    12: 'Total purchases',
    13: 'Total VAT due for the period',
    14: 'Corrections from previous period (between SAR \u00B15,000)',
    15: 'VAT credit carried forward from previous period(s)',
    16: 'Net VAT due (or reclaimable) for the period',
  };

  // Produce the return laid out exactly like the ZATCA VAT return form:
  // # | Description | Amount (SAR) | Adjustment (SAR) | VAT Amount (SAR), grouped
  // into VAT on Sales (boxes 1–6) and VAT on Purchases (boxes 7–12), then the
  // net settlement boxes 13–16 (VAT column only).
  function exportForm() {
    if (!data) return;
    const NAVY: [number, number, number] = [22, 64, 77];
    const TOTAL: [number, number, number] = [232, 232, 226];
    const NET: [number, number, number] = [225, 245, 238];
    const n2 = (v: number) => Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    const doc = new jsPDF({ unit: 'pt', format: 'a4' });
    const pageW = doc.internal.pageSize.getWidth();

    // v2.34.0 — jsPDF's built-in fonts cannot render Arabic (the company name
    // printed as mojibake). The browser CAN — it shapes and orders Arabic
    // perfectly on a canvas — so the company heading is drawn to a hidden
    // canvas and embedded as a crisp 3x image. Works for any script.
    const textImage = (text: string, px: number, bold = false, color = '#16404d') => {
      const scale = 3;
      const c = document.createElement('canvas');
      const g = c.getContext('2d')!;
      const font = `${bold ? '700 ' : ''}${px * scale}px 'Segoe UI', Tahoma, Arial, sans-serif`;
      g.font = font;
      const w = Math.ceil(g.measureText(text).width) + 8 * scale;
      c.width = w; c.height = Math.ceil(px * scale * 1.5);
      const g2 = c.getContext('2d')!;
      g2.font = font; g2.fillStyle = color; g2.textBaseline = 'middle';
      g2.direction = /[\u0600-\u06FF]/.test(text) ? 'rtl' : 'ltr';
      g2.fillText(text, g2.direction === 'rtl' ? w - 4 * scale : 4 * scale, c.height / 2);
      return { url: c.toDataURL('image/png'), w: w / scale, h: c.height / scale };
    };

    doc.setFont('helvetica', 'bold'); doc.setFontSize(15); doc.setTextColor(22, 64, 77);
    doc.text('VAT Return', pageW / 2, 42, { align: 'center' });
    // Company heading — Arabic-safe image, centered
    if (data.company) {
      const img = textImage(String(data.company), 13, true);
      doc.addImage(img.url, 'PNG', (pageW - img.w) / 2, 48, img.w, img.h);
    }
    doc.setFont('helvetica', 'normal'); doc.setFontSize(9.5); doc.setTextColor(120, 120, 120);
    doc.text('Kingdom of Saudi Arabia \u2014 ZATCA (16-box VAT return)', pageW / 2, 76, { align: 'center' });

    doc.setTextColor(44, 44, 42); doc.setFontSize(9.5);
    doc.text(`VAT Registration No.: ${data.tax_id || '\u2014'}`, 40, 98);
    doc.text(`Tax Period: ${fromDate}  to  ${toDate}`, pageW - 40, 84, { align: 'right' });
    doc.text(`Currency: ${cur}`, pageW - 40, 98, { align: 'right' });

    const body: any[] = [];
    const section = (label: string) => body.push([{ content: label, colSpan: 5, styles: { fillColor: NAVY, textColor: [255, 255, 255], fontStyle: 'bold', halign: 'left' } }]);
    const row = (box: number | string, amount: number | null, adj: number | null, vat: number | null, fill?: [number, number, number], bold?: boolean, label?: string) => {
      const base = fill ? { fillColor: fill } : {};
      const right = (v: number | null) => ({ content: v === null ? '' : n2(v), styles: { halign: 'right', fontStyle: bold ? 'bold' : 'normal', ...base } });
      body.push([
        { content: String(box), styles: { halign: 'center', ...base } },
        { content: label ?? ZATCA_LABELS[Number(box)] ?? '', styles: { fontStyle: bold ? 'bold' : 'normal', ...base } },
        right(amount), right(adj), right(vat),
      ]);
    };
    // Driven by the lines the API returned, NOT by a hardcoded [1,2,3,4,5]. The
    // return grew a box: standard-rated government sales (1.2) appear whenever a
    // GTPL rule is active. A fixed list would have dropped that line from the PDF
    // while box 6 still counted it — a printed return that does not foot, and the
    // kind of error nobody finds until ZATCA does.
    section('VAT on Sales');
    salesLines.forEach((l) => row(l.box, l.amount, l.adjustment, l.zero_vat ? 0 : l.vat, undefined, false, l.label));
    if (box6) row(6, box6.amount, box6.adjustment, box6.vat, TOTAL, true);

    section('VAT on Purchases');
    purchLines.forEach((l) => row(l.box, l.amount, l.adjustment, l.zero_vat ? 0 : l.vat, undefined, false, l.label));
    if (box12) row(12, box12.amount, box12.adjustment, box12.vat, TOTAL, true);

    row(13, null, null, box13);
    row(14, null, null, box14);
    row(15, null, null, box15);
    row(16, null, null, box16, NET, true);

    autoTable(doc, {
      startY: 116,
      head: [[
        { content: '#', styles: { halign: 'center' } },
        'Description',
        { content: 'Amount (SAR)', styles: { halign: 'right' } },
        { content: 'Adjustment (SAR)', styles: { halign: 'right' } },
        { content: 'VAT Amount (SAR)', styles: { halign: 'right' } },
      ]],
      body,
      theme: 'grid',
      styles: { font: 'helvetica', fontSize: 8.5, cellPadding: 4, lineColor: [225, 222, 214], lineWidth: 0.5, textColor: [44, 44, 42] },
      headStyles: { fillColor: NAVY, textColor: [255, 255, 255], fontStyle: 'bold', fontSize: 8.5 },
      columnStyles: { 0: { cellWidth: 26 }, 1: { cellWidth: 'auto' }, 2: { cellWidth: 92 }, 3: { cellWidth: 92 }, 4: { cellWidth: 98 } },
      margin: { left: 40, right: 40 },
    });

    const endY = ((doc as any).lastAutoTable?.finalY || 116) + 18;
    doc.setFontSize(7.5); doc.setTextColor(120, 120, 120);
    doc.text(
      'Assembled by Neotec Insight from posted invoices and the GL tax accounts for the selected period. This is a filing-preparation aid \u2014 reconcile against your records before submitting on the ZATCA Fatoora portal. Boxes 14 and 15 are entered manually.',
      40, endY, { maxWidth: pageW - 80 });
    doc.text(`Generated: ${new Date().toISOString().slice(0, 19).replace('T', ' ')}`, 40, endY + 26);

    doc.save(`zatca-vat-return-${fromDate}-to-${toDate}.pdf`);
  }

  const lineRow = (l: Line) => (
    <tr key={l.box} className={l.system ? 'vat-total-row' : ''}>
      <td className="vat-box-num">{l.box}</td>
      <td className="vat-box-label">
        <button className="vat-drill-link" onClick={() => openDrill(l.box)} title={t('Show source invoices')}>{t(l.label)}</button>
      </td>
      <td className="vat-num">{fmtD(l.amount, 2)}</td>
      <td className="vat-num vat-muted">{fmtD(l.adjustment, 2)}</td>
      <td className="vat-num vat-vatcol">{l.zero_vat ? '0.00' : fmtD(l.vat, 2)}</td>
    </tr>
  );

  return (
    <div className="vat-wrap">
      {showAdj && (
        <VatAdjustments
          company={company || data?.company || ''}
          fromDate={fromDate}
          toDate={toDate}
          onClose={() => { setShowAdj(false); load(company); }}
        />
      )}
      <div className="vat-hero">
        <div>
          <h1>{t('VAT Return')} <span className="vat-badge">ZATCA</span></h1>
          <p>{t('Saudi VAT return (16 boxes) assembled from your invoices and GL for the selected period.')}</p>
        </div>
      </div>

      <div className="vat-controls">
        <label><span className="flbl">{t('Company')}</span>
          <select value={company} onChange={(e) => { setCompany(e.target.value); }}>
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
          <button onClick={() => quickPeriod('last_month')}>{t('Last month')}</button>
          <button onClick={() => quickPeriod('this_quarter')}>{t('This quarter')}</button>
        </div>
        <button className="vat-ghost" onClick={() => setShowAdj(true)} title={t('Include out-of-period invoices (e.g. government invoices paid this quarter) or defer unpaid ones — with a mandatory reason')}>
          ⇄ {t('Adjustments')}{(data?.adjustments && (data.adjustments.included.length + data.adjustments.excluded.length) > 0) ? ` (${data.adjustments.included.length + data.adjustments.excluded.length})` : ''}
        </button>
        <button className="vat-run" onClick={() => load(company)} disabled={loading}>
          {loading ? t('Calculating…') : t('Generate')}
        </button>
        {data && <button className="vat-ghost" onClick={exportForm}>{t('Export VAT Form (PDF)')}</button>}
        {data && <button className="vat-ghost" onClick={exportCsv}>{t('Export CSV')}</button>}
      </div>

      {err && <div className="studio-err">{err}</div>}

      {data && !loading && (
        <>
          {/* KPI summary */}
          <div className="vat-kpis">
            <div className="vat-kpi"><span className="vat-kpi-l">{t('Output VAT (sales)')}</span><span className="vat-kpi-v">{fmtD(box6?.vat || 0, 2)} {cur}</span></div>
            <div className="vat-kpi"><span className="vat-kpi-l">{t('Input VAT (purchases)')}</span><span className="vat-kpi-v">{fmtD(box12?.vat || 0, 2)} {cur}</span></div>
            <div className={'vat-kpi vat-kpi-net ' + (box16 >= 0 ? 'pay' : 'refund')}>
              <span className="vat-kpi-l">{box16 >= 0 ? t('Net VAT payable') : t('Net VAT reclaimable')}</span>
              <span className="vat-kpi-v">{fmtD(Math.abs(box16), 2)} {cur}</span>
            </div>
          </div>

          <table className="vat-form">
            <thead>
              <tr>
                <th>#</th><th>{t('Description')}</th>
                <th className="vat-num">{t('Amount')} ({cur})</th>
                <th className="vat-num">{t('Adjustment')}</th>
                <th className="vat-num">{t('VAT Amount')} ({cur})</th>
              </tr>
            </thead>
            <tbody>
              <tr className="vat-section"><td colSpan={5}>{t('VAT on Sales (Output VAT)')}</td></tr>
              {salesLines.map(lineRow)}
              {box6 && lineRow(box6)}

              <tr className="vat-section"><td colSpan={5}>{t('VAT on Purchases (Input VAT)')}</td></tr>
              {purchLines.map(lineRow)}
              {box12 && lineRow(box12)}

              <tr className="vat-section"><td colSpan={5}>{t('Net VAT Due')}</td></tr>
              <tr className="vat-total-row">
                <td className="vat-box-num">13</td>
                <td className="vat-box-label">{t('Total VAT due for the period')}</td>
                <td colSpan={2}></td>
                <td className="vat-num vat-vatcol">{fmtD(box13, 2)}</td>
              </tr>
              <tr>
                <td className="vat-box-num">14</td>
                <td className="vat-box-label">{t('Corrections from previous period')} <small>(±5,000)</small></td>
                <td colSpan={2}></td>
                <td className="vat-num"><input type="number" className="vat-edit" value={box14} onChange={(e) => setBox14(Number(e.target.value) || 0)} /></td>
              </tr>
              <tr>
                <td className="vat-box-num">15</td>
                <td className="vat-box-label">{t('VAT credit carried forward')}</td>
                <td colSpan={2}></td>
                <td className="vat-num"><input type="number" className="vat-edit" value={box15} onChange={(e) => setBox15(Number(e.target.value) || 0)} /></td>
              </tr>
              <tr className={'vat-grand ' + (box16 >= 0 ? 'pay' : 'refund')}>
                <td className="vat-box-num">16</td>
                <td className="vat-box-label">{box16 >= 0 ? t('Net VAT due to ZATCA') : t('Net VAT reclaimable')}</td>
                <td colSpan={2}></td>
                <td className="vat-num vat-vatcol">{fmtD(box16, 2)}</td>
              </tr>
            </tbody>
          </table>

          {/* Non-invoice VAT clubbed into boxes 1 / 7 (transparency) */}
          {(Math.abs(data?.non_invoice?.input?.total || 0) >= 0.01 || Math.abs(data?.non_invoice?.output?.total || 0) >= 0.01) && (
            <div className="vat-accts">
              <strong>{t('Non-invoice VAT included')}:</strong>{' '}
              {Math.abs(data?.non_invoice?.input?.total || 0) >= 0.01 && (
                <>{t('Box 7')} +{fmtD(data.non_invoice.input.total, 2)} {cur} ({Object.entries(data.non_invoice.input.sources || {}).map(([k, v]) => `${t(k)}: ${fmtD(v as number, 2)}`).join(', ')})</>
              )}
              {Math.abs(data?.non_invoice?.input?.total || 0) >= 0.01 && Math.abs(data?.non_invoice?.output?.total || 0) >= 0.01 && ' · '}
              {Math.abs(data?.non_invoice?.output?.total || 0) >= 0.01 && (
                <>{t('Box 1')} +{fmtD(data.non_invoice.output.total, 2)} {cur} ({Object.entries(data.non_invoice.output.sources || {}).map(([k, v]) => `${t(k)}: ${fmtD(v as number, 2)}`).join(', ')})</>
              )}
            </div>
          )}

          {data?.adjustments && (data.adjustments.included.length > 0 || data.adjustments.excluded.length > 0) && (
            <div className="vat-accts">
              <strong>{t('Period adjustments')}:</strong>{' '}
              {data.adjustments.included.length > 0 && <>{data.adjustments.included.length} {t('included from other periods')} (+{fmtD(data.adjustments.included.reduce((a: number, x: any) => a + x.vat, 0), 2)} {cur})</>}
              {data.adjustments.included.length > 0 && data.adjustments.excluded.length > 0 && ' · '}
              {data.adjustments.excluded.length > 0 && <>{data.adjustments.excluded.length} {t('deferred out')} (−{fmtD(data.adjustments.excluded.reduce((a: number, x: any) => a + x.vat, 0), 2)} {cur})</>}
              {' · '}{t('net VAT effect')}: {fmtD(data.adjustments.net_vat_effect, 2)} {cur}
            </div>
          )}

          {/* VAT accounts used (transparency) */}
          <div className="vat-accts">
            <strong>{t('VAT accounts used')}:</strong>{' '}
            {t('Output')}: {(data.accounts.output_vat || []).join(', ') || '—'}{' · '}
            {t('Input')}: {(data.accounts.input_vat || []).join(', ') || '—'}
          </div>

          <div className="vat-disclaimer">
            ⚠ {t('This is a preparation aid. Verify every box against your source documents before filing on the ZATCA Fatoora portal. Category splits are inferred from each invoice\'s Tax Category (or VAT rate); confirm zero-rated, exempt, export and import classifications.')}
          </div>
        </>
      )}

      {drill && (
        <div className="vat-drill-overlay" onClick={(e) => { if (e.target === e.currentTarget) setDrill(null); }}>
          <div className="vat-drill">
            <div className="vat-drill-h">
              <h3>{t('Box')} {drill.box} — {drill.doctype} ({drill.rows.length})</h3>
              <button className="fh-x" onClick={() => setDrill(null)}>×</button>
            </div>
            <div className="vat-drill-body">
              <table className="vat-drill-table">
                <thead><tr><th style={{ width: 30 }} title={t('Counted in this return — untick to exclude')}>✓</th><th>{t('Document')}</th><th>{t('Date')}</th><th>{t('Party')}</th><th>{t('Tax Category')}</th><th className="vat-num">{t('Net')}</th><th className="vat-num">{t('VAT')}</th></tr></thead>
                <tbody>
                  {drill.rows.map((r) => (
                    <tr key={r.name} className={r._adj === 'in' ? 'vat-row-included' : ''}>
                      <td>
                        {['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'].includes(String(drill.box)) && !r.doctype ? (
                          <input type="checkbox" checked disabled={drillBusy === r.name}
                            title={r._adj === 'in' ? t('Included from another period — untick to remove') : t('Untick to exclude from this return')}
                            onChange={() => r._adj === 'in'
                              ? drillReinclude(r, drill.doctype)
                              : drillExclude(r, drill.doctype)} />
                        ) : null}
                      </td>
                      <td>
                        <a
                          className="vat-doc-link"
                          href={`/app/${((r.doctype || drill.doctype) || '').toLowerCase().replace(/\s+/g, '-')}/${encodeURIComponent(r.name)}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={t('Open document')}
                        >{r.name}</a>
                      </td>
                      <td>{r.posting_date}</td>
                      <td>{r.customer || r.supplier || ''}</td>
                      <td>{r.tax_category || '—'}</td>
                      <td className="vat-num">{fmtD(r.base_net_total, 2)}</td>
                      <td className="vat-num">{fmtD(r.base_total_taxes_and_charges, 2)}</td>
                    </tr>
                  ))}
                  {drill.rows.length === 0 && <tr><td colSpan={7} className="vat-muted" style={{ padding: 16 }}>{t('No documents in this box for the period.')}</td></tr>}
                </tbody>
              </table>
              {(drill as any).excluded?.length > 0 && (
                <>
                  <div className="vat-excl-head">{t('Excluded from this return — VAT not payable this period')}</div>
                  <table className="vat-drill-table vat-excl">
                    <tbody>
                      {(drill as any).excluded.map((r: any) => (
                        <tr key={r.name}>
                          <td style={{ width: 30 }}>
                            <input type="checkbox" checked={false} disabled={drillBusy === r.name}
                              title={t('Tick to re-include in this return')}
                              onChange={() => drillReinclude(r, drill.doctype)} />
                          </td>
                          <td>{r.name}</td>
                          <td>{r.posting_date}</td>
                          <td>{r.customer || r.supplier || ''}</td>
                          <td className="vat-excl-reason" title={r._adj_reason}>{r._adj_reason || ''}</td>
                          <td className="vat-num">{fmtD(r.base_net_total, 2)}</td>
                          <td className="vat-num">{fmtD(r.base_total_taxes_and_charges, 2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
