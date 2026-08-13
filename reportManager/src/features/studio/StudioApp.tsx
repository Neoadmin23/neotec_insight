import { useEffect, useMemo, useState } from 'react';
import * as XLSX from 'xlsx';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import StudioChart from './StudioChart';
import StudioDashboard from './StudioDashboard';
import DatasetExplorer from './DatasetExplorer';
import ScheduleModal from './ScheduleModal';

interface Field { fieldname: string; label: string; fieldtype: string; options?: string; numeric?: boolean; }
interface Source { name: string; label: string; module: string; }
interface Filter { field: string; op: string; value: string; value2?: string; ask?: boolean }
interface LinkField { link_field: string; label: string; target_doctype: string; }
interface Calc { key: string; label: string; formula: string; decimals: number; }
interface ColMeta { label?: string; decimals?: number; align?: string; color?: string; width?: number; }

const OPS = ['=', '!=', '>', '<', '>=', '<=', 'like', 'in', 'between', 'is set', 'is not set'];
const DATE_TYPES = ['Date', 'Datetime'];

function scrubKey(s: string): string {
  let k = (s || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  if (!k || /^[0-9]/.test(k)) k = 'c_' + k;
  return k;
}
function fmt(v: any, numeric?: boolean, decimals = 2): string {
  if (v == null || v === '') return '';
  if (numeric && (typeof v === 'number' || !isNaN(Number(v))))
    return Number(v).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return String(v);
}

export default function StudioApp() {
  const [sources, setSources] = useState<Source[]>([]);
  const [srcSearch, setSrcSearch] = useState('');
  const [doctype, setDoctype] = useState('');
  const [fields, setFields] = useState<Field[]>([]);
  const [cols, setCols] = useState<string[]>([]);
  const [filters, setFilters] = useState<Filter[]>([]);
  const [groupBy, setGroupBy] = useState('');
  const [limit, setLimit] = useState(500);

  const [fieldSearch, setFieldSearch] = useState('');
  const [selectedOnly, setSelectedOnly] = useState(false);
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [overIdx, setOverIdx] = useState<number | null>(null);

  const [linkFields, setLinkFields] = useState<LinkField[]>([]);
  const [openLink, setOpenLink] = useState('');
  const [linkTargetFields, setLinkTargetFields] = useState<Record<string, Field[]>>({});
  const [fieldVals, setFieldVals] = useState<Record<string, { value: string; label: string }[]>>({});

  const [calculated, setCalculated] = useState<Calc[]>([]);
  const [calcLabel, setCalcLabel] = useState('');
  const [calcFormula, setCalcFormula] = useState('');
  const [colMeta, setColMeta] = useState<Record<string, ColMeta>>({});
  const [colModes, setColModes] = useState<Record<string, string>>({});
  const [inspect, setInspect] = useState('');

  const [retOn, setRetOn] = useState(false);
  const [retField, setRetField] = useState('');
  const [retValue, setRetValue] = useState('1');

  // v2.23.0 — Time Intelligence (MTD/QTD/YTD/YoY/rolling-12M) + chart drill.
  const [tiOn, setTiOn] = useState(false);
  const [tiDateField, setTiDateField] = useState('');
  const [tiMeasures, setTiMeasures] = useState<string[]>([]);
  const [tiAsOf, setTiAsOf] = useState(new Date().toISOString().slice(0, 10));
  const [tiResult, setTiResult] = useState<any>(null);
  const [tiBusy, setTiBusy] = useState(false);
  const [drillKey, setDrillKey] = useState<string | null>(null);

  const [aiPrompt, setAiPrompt] = useState('');
  const [aiBusy, setAiBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<any[]>([]);
  const [title, setTitle] = useState('');

  useEffect(() => { api.studioListReports().then(setSaved).catch(() => {}); }, []);
  useEffect(() => {
    const h = setTimeout(() => { api.studioListSources(srcSearch).then(setSources).catch(() => {}); }, srcSearch ? 220 : 0);
    return () => clearTimeout(h);
  }, [srcSearch]);

  const fieldMap = useMemo(() => Object.fromEntries(fields.map((f) => [f.fieldname, f])) as Record<string, Field>, [fields]);
  const hasReturns = useMemo(() => fields.some((f) => f.fieldname === 'is_return'), [fields]);

  // Advanced pivot
  const [pivotOn, setPivotOn] = useState(false);
  const [pivotRow, setPivotRow] = useState('');
  const [pivotCol, setPivotCol] = useState('');
  const [pivotVal, setPivotVal] = useState('');
  const [pivotAgg, setPivotAgg] = useState('sum');
  const numericFields = useMemo(() => fields.filter((f) => f.numeric), [fields]);

  // Chart view ("convert report to graph")
  const [view, setView] = useState<'table' | 'chart'>('table');
  const [chartType, setChartType] = useState('bar');
  const [chartCategory, setChartCategory] = useState('');
  const [chartMeasures, setChartMeasures] = useState<string[]>([]);
  const CHART_TYPES = ['bar', 'hbar', 'stacked', 'line', 'area', 'pie', 'donut'];
  const [mode, setMode] = useState<'builder' | 'dashboard' | 'datasets'>('builder');
  const [showSchedule, setShowSchedule] = useState(false);
  const [promptOpen, setPromptOpen] = useState(false);
  // Print/PDF/Excel header — an ERPNext Letter Head chosen once, remembered.
  const [letterheads, setLetterheads] = useState<any[]>([]);
  const [letterHead, setLetterHead] = useState<string>(() => { try { return localStorage.getItem('ni-studio-lh') || ''; } catch { return ''; } });
  useEffect(() => { api.listLetterHeads().then(setLetterheads).catch(() => {}); }, []);
  useEffect(() => { try { localStorage.setItem('ni-studio-lh', letterHead); } catch { /* ignore */ } }, [letterHead]);
  const [dsSaving, setDsSaving] = useState(false);

  function pickDoctype(dt: string) {
    setDoctype(dt); setFields([]); setCols([]); setFilters([]); setGroupBy(''); setResult(null); setErr(null);
    setCalculated([]); setColMeta({}); setColModes({}); setLinkFields([]); setOpenLink(''); setLinkTargetFields({});
    setFieldSearch(''); setSelectedOnly(false); setRetOn(false); setRetField(''); setFieldVals({});
    setPivotOn(false); setPivotRow(''); setPivotCol(''); setPivotVal(''); setPivotAgg('sum');
    setDrillKey(null); setTiResult(null); setTiDateField(''); setTiMeasures([]);
    api.studioListFields(dt).then((f) => {
      setFields(f); setCols(f.slice(0, 6).map((x) => x.fieldname));
      if (f.some((x: Field) => x.fieldname === 'is_return')) setRetField('is_return');
    }).catch(() => {});
    api.studioListLinkFields(dt).then(setLinkFields).catch(() => {});
  }

  function toggleLink(lf: LinkField) {
    if (openLink === lf.link_field) { setOpenLink(''); return; }
    setOpenLink(lf.link_field);
    if (!linkTargetFields[lf.link_field])
      api.studioListFields(lf.target_doctype).then((f) => setLinkTargetFields((m) => ({ ...m, [lf.link_field]: f }))).catch(() => {});
  }
  function toggleCol(fn: string) { setCols((c) => c.includes(fn) ? c.filter((x) => x !== fn) : [...c, fn]); }
  function moveCol(from: number, to: number) {
    if (from === to) return;
    setCols((c) => { const a = [...c]; const [m] = a.splice(from, 1); a.splice(to, 0, m); return a; });
  }
  function ensureFieldVals(field: string) {
    if (!field || fieldVals[field] || !doctype) return;
    const f = fieldMap[field];
    if (f && (f.fieldtype === 'Link' || f.fieldtype === 'Select' || f.fieldtype === 'Check'))
      api.studioFieldValues(doctype, field).then((v) => setFieldVals((m) => ({ ...m, [field]: v }))).catch(() => {});
  }

  function addCalc() {
    if (!calcLabel.trim() || !calcFormula.trim()) return;
    const key = scrubKey(calcLabel);
    setCalculated((cs) => [...cs.filter((c) => c.key !== key), { key, label: calcLabel.trim(), formula: calcFormula.trim(), decimals: 2 }]);
    setCalcLabel(''); setCalcFormula('');
  }
  function delCalc(key: string) { setCalculated((cs) => cs.filter((c) => c.key !== key)); }

  async function saveAsDataset() {
    if (!doctype) return;
    const dsTitle = prompt(t('Dataset title (measures = your numeric columns, dimensions = your text columns):'), title || '');
    if (!dsTitle) return;
    const numeric = cols.filter((c) => fieldMap[c]?.numeric);
    const textual = cols.filter((c) => !fieldMap[c]?.numeric && !c.includes('.'));
    if (!numeric.length) { setErr(t('Pick at least one numeric column first — those become the dataset measures.')); return; }
    setDsSaving(true);
    try {
      await api.saveDataset({
        title: dsTitle, base_doctype: doctype,
        config: {
          measures: numeric.map((c) => ({ key: c, field: c, agg: 'sum', label: fieldMap[c]?.label || c })),
          dimensions: textual.map((c) => ({ field: c, label: fieldMap[c]?.label || c })),
          filters,
        },
      });
      setMode('datasets');
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setDsSaving(false); }
  }

  async function runTimeIntel() {
    if (!doctype || !tiDateField || tiMeasures.length === 0) return;
    setTiBusy(true); setErr(null);
    try {
      setTiResult(await api.studioTimeIntel({
        doctype, date_field: tiDateField, measures: tiMeasures,
        group_by: groupBy || null, as_of: tiAsOf, filters,
      }));
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setTiBusy(false); }
  }

  function buildConfig() {
    const fl = filters.map((f) => ({
      field: f.field, op: f.op,
      value: f.op === 'between' ? [f.value, f.value2 || ''] : f.value,
      ...(f.ask ? { ask: 1 } : {}),
    }));
    return {
      doctype, columns: cols, filters: fl, group_by: groupBy || null, limit, calculated, col_modes: colModes,
      returns: retOn && retField ? { field: retField, value: retValue } : null,
      pivot: pivotOn && pivotRow && (pivotVal || pivotAgg === 'count') ? { row: pivotRow, column: pivotCol, value: pivotVal, agg: pivotAgg } : null,
    };
  }

  async function run() {
    if (!doctype) { setErr(t('Pick a document first.')); return; }
    if (pivotOn && (!pivotRow || (!pivotVal && pivotAgg !== 'count'))) {
      setErr(t('For the pivot, choose Rows and a Value (Value is optional for Count). Columns are optional.'));
      return;
    }
    setLoading(true); setErr(null);
    setDrillKey(null);
    try { setResult(await api.studioRunQuery(buildConfig())); }
    catch (e: any) { setErr(String(e?.message || e)); } finally { setLoading(false); }
  }
  async function aiBuild() {
    if (!doctype) { setErr(t('Pick a document first.')); return; }
    if (!aiPrompt.trim()) return;
    setAiBusy(true); setErr(null);
    try {
      const cfg = await api.studioAiBuild(doctype, aiPrompt.trim());
      if (cfg.columns?.length) setCols(cfg.columns);
      setFilters((cfg.filters || []).map((f: any) => ({ field: f.field, op: f.op || '=', value: Array.isArray(f.value) ? (f.value[0] ?? '') : (f.value ?? ''), value2: Array.isArray(f.value) ? (f.value[1] ?? '') : '', ask: !!f.ask })));
      setGroupBy(cfg.group_by || '');
      setResult(await api.studioRunQuery({ ...buildConfig(), columns: cfg.columns?.length ? cfg.columns : cols, filters: cfg.filters || [], group_by: cfg.group_by || null }));
    } catch (e: any) { setErr(String(e?.message || e)); } finally { setAiBusy(false); }
  }

  function addFilter() { const f0 = fields[0]?.fieldname || ''; setFilters((f) => [...f, { field: f0, op: '=', value: '' }]); ensureFieldVals(f0); }
  function updFilter(i: number, patch: Partial<Filter>) {
    setFilters((f) => f.map((x, j) => j === i ? { ...x, ...patch } : x));
    if (patch.field) ensureFieldVals(patch.field);
  }
  function delFilter(i: number) { setFilters((f) => f.filter((_, j) => j !== i)); }
  function setMeta(key: string, patch: Partial<ColMeta>) { setColMeta((m) => ({ ...m, [key]: { ...m[key], ...patch } })); }

  async function save() {
    if (!title.trim()) { setErr(t('Give the report a title.')); return; }
    try { await api.studioSaveReport({ title: title.trim(), config: { ...buildConfig(), colMeta, _ui: { retOn, retField, retValue }, _chart: { view, type: chartType, category: chartCategory, measures: chartMeasures } } }); api.studioListReports().then(setSaved); }
    catch (e: any) { setErr(String(e?.message || e)); }
  }
  async function load(slug: string) {
    try {
      const r = await api.studioLoadReport(slug); const c = r.config || {};
      setTitle(r.title || '');
      if (c.doctype) { setDoctype(c.doctype); const f = await api.studioListFields(c.doctype); setFields(f); api.studioListLinkFields(c.doctype).then(setLinkFields).catch(() => {}); }
      setCols(c.columns || []);
      setFilters((c.filters || []).map((f: any) => ({ field: f.field, op: f.op, value: Array.isArray(f.value) ? (f.value[0] ?? '') : (f.value ?? ''), value2: Array.isArray(f.value) ? (f.value[1] ?? '') : '', ask: !!f.ask })));
      // Prompted report: parameters marked "Ask" open a prompt before first run.
      if ((c.filters || []).some((f: any) => f.ask)) setPromptOpen(true);
      setGroupBy(c.group_by || ''); setLimit(c.limit || 500); setCalculated(c.calculated || []); setColModes(c.col_modes || {}); setColMeta(c.colMeta || {});
      const ui = c._ui || {}; setRetOn(!!ui.retOn); setRetField(ui.retField || ''); setRetValue(ui.retValue || '1');
      setTimeout(run, 60);
    } catch (e: any) { setErr(String(e?.message || e)); }
  }

  const colDefs = result?.columns || [];
  const dec = (c: any) => colMeta[c.field]?.decimals ?? 2;
  const lbl = (c: any) => colMeta[c.field]?.label || c.label;
  const tdStyle = (c: any): React.CSSProperties => ({
    textAlign: (colMeta[c.field]?.align as any) || (c.numeric ? 'right' : 'left'),
    color: colMeta[c.field]?.color || undefined,
    width: colMeta[c.field]?.width ? colMeta[c.field]!.width + 'px' : undefined,
  });

  // Smart filter value control
  function valueInput(f: Filter, i: number) {
    if (['is set', 'is not set'].includes(f.op)) return null;
    const fld = fieldMap[f.field];
    const isDate = fld && DATE_TYPES.includes(fld.fieldtype);
    const vals = fieldVals[f.field];
    if (f.op === 'between') {
      const ty = isDate ? 'date' : 'text';
      return (
        <div className="studio-fval2">
          <input type={ty} value={f.value} onChange={(e) => updFilter(i, { value: e.target.value })} placeholder={t('from')} />
          <input type={ty} value={f.value2 || ''} onChange={(e) => updFilter(i, { value2: e.target.value })} placeholder={t('to')} />
        </div>
      );
    }
    if (isDate) return <input type="date" value={f.value} onChange={(e) => updFilter(i, { value: e.target.value })} />;
    if (vals && vals.length && f.op !== 'like') {
      return (
        <>
          <input list={`fv-${i}`} value={f.value} onChange={(e) => updFilter(i, { value: e.target.value })}
            placeholder={f.op === 'in' ? t('a,b,c') : t('value')} />
          <datalist id={`fv-${i}`}>{vals.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}</datalist>
        </>
      );
    }
    return <input value={f.value} onChange={(e) => updFilter(i, { value: e.target.value })} placeholder={f.op === 'in' ? t('a,b,c') : t('value')} />;
  }

  function exportPivot() {
    if (!result?.pivot) return;
    const p = result.pivot;
    const header = [p.row_label, ...p.columns, 'Total'];
    const aoa: any[][] = [[title || doctype], [`${p.agg} of ${p.value_label}`], header];
    p.rows.forEach((r: any) => aoa.push([r.key, ...p.columns.map((c: string) => r.cells[c] ?? 0), r.total]));
    aoa.push(['TOTAL', ...p.columns.map((c: string) => p.col_totals[c] ?? 0), p.grand_total]);
    const ws = XLSX.utils.aoa_to_sheet(aoa); const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Pivot'); XLSX.writeFile(wb, `studio_pivot_${doctype || 'report'}.xlsx`);
  }

  function exportExcel() {
    // Server-side styled workbook (v2.27.0): mirrors on-screen colours, group
    // bands, subtotals, dd-mm-yyyy dates, letterhead block.
    if (result && !result.pivot) {
      const url = '/api/method/neotec_insight.neotec_insight.api.studio.export_xlsx'
        + '?config=' + encodeURIComponent(JSON.stringify(buildConfig()))
        + '&title=' + encodeURIComponent(title || doctype || 'Report')
        + '&letter_head=' + encodeURIComponent(letterHead || '');
      window.open(url, '_blank');
      return;
    }
    exportExcelLocal();
  }

  function exportExcelLocal() {
    if (!result) return;
    const header = colDefs.map((c: any) => lbl(c));
    const aoa: any[][] = [[title || doctype], [], header];
    const push = (rows: any[]) => rows.forEach((r) => aoa.push(colDefs.map((c: any) => r[c.field] ?? '')));
    const subRow = (label: string, sub: any) => { const a = colDefs.map((c: any) => c.numeric ? (sub[c.field] ?? '') : ''); a[0] = label; return a; };
    if (result.groups) for (const g of result.groups) {
      aoa.push([`${result.group_label}: ${g.key} (${g.count})`]);
      if (result.split_returns) {
        aoa.push(['— Sales —']); push(g.sales_rows || []); aoa.push(subRow('Sales Subtotal', g.sales_subtotal || {}));
        if ((g.return_rows || []).length) { aoa.push(['— Returns —']); push(g.return_rows); aoa.push(subRow('Returns Subtotal', g.return_subtotal || {})); }
        aoa.push(subRow('Net Subtotal', g.net_subtotal || {}));
      } else { push(g.rows || []); aoa.push(subRow('Subtotal', g.subtotal || {})); }
      aoa.push([]);
    } else push(result.rows || []);
    aoa.push(subRow('TOTAL', result.grand_total || {}));
    const ws = XLSX.utils.aoa_to_sheet(aoa); const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Report'); XLSX.writeFile(wb, `studio_${doctype || 'report'}.xlsx`);
  }

  const fmtDMY = (v: any) => {
    const m = /^([0-9]{4})-([0-9]{2})-([0-9]{2})/.exec(String(v || ''));
    return m ? `${m[3]}-${m[2]}-${m[1]}` : String(v ?? '');
  };
  const isDateCol = (c: any) => c?.type === 'Date' || c?.type === 'Datetime';

  async function printReport() {
    if (!result) return;
    const esc = (s: any) => String(s ?? '').replace(/[&<>]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' } as any)[m]);
    const align = (c: any) => colMeta[c.field]?.align || (c.numeric ? 'right' : 'left');
    const th = colDefs.map((c: any) => `<th style="text-align:${align(c)}">${esc(lbl(c))}</th>`).join('');
    const rowH = (r: any) => `<tr>${colDefs.map((c: any) => `<td style="text-align:${align(c)};${colMeta[c.field]?.color ? 'color:' + colMeta[c.field]!.color : ''}">${esc(isDateCol(c) ? fmtDMY(r[c.field]) : fmt(r[c.field], c.numeric, dec(c)))}</td>`).join('')}</tr>`;
    const subH = (label: string, sub: any, cls: string) => `<tr class="${cls}">${colDefs.map((c: any, i: number) => `<td style="text-align:${c.numeric ? 'right' : 'left'}">${i === 0 ? label : c.numeric ? esc(fmt(sub[c.field], true, dec(c))) : ''}</td>`).join('')}</tr>`;
    let body = '';
    if (result.groups) for (const g of result.groups) {
      body += `<tr class="grp"><td colspan="${colDefs.length}">${esc(result.group_label)}: <b>${esc(g.key)}</b> (${g.count})</td></tr>`;
      if (result.split_returns) {
        body += `<tr class="band"><td colspan="${colDefs.length}">${t('Sales')}</td></tr>` + (g.sales_rows || []).map(rowH).join('') + subH(t('Sales Subtotal'), g.sales_subtotal || {}, 'sub');
        if ((g.return_rows || []).length) body += `<tr class="band ret"><td colspan="${colDefs.length}">${t('Returns')}</td></tr>` + g.return_rows.map(rowH).join('') + subH(t('Returns Subtotal'), g.return_subtotal || {}, 'sub ret');
        body += subH(t('Net Subtotal'), g.net_subtotal || {}, 'sub net');
      } else body += (g.rows || []).map(rowH).join('') + subH(t('Subtotal'), g.subtotal || {}, 'sub');
    } else body = (result.rows || []).map(rowH).join('');
    body += subH(t('TOTAL'), result.grand_total || {}, 'tot');
    // Letterhead: the ERPNext Letter Head chosen in the toolbar defines the
    // printed header/footer (Print → save as PDF keeps all colours).
    let lhHead = '', lhFoot = '';
    if (letterHead) {
      try {
        const lh = await api.getLetterhead(letterHead);
        lhHead = lh?.header_html || '';
        lhFoot = lh?.footer_html || '';
      } catch { /* print without letterhead */ }
    }
    const w = window.open('', '_blank'); if (!w) return;
    w.document.write(`<html><head><title>${esc(title || doctype)}</title><style>
      body{font-family:Calibri,Segoe UI,sans-serif;color:#2a2440;padding:24px;} h1{font-size:20px;margin:0 0 2px;}
      .meta{color:#888;font-size:12px;margin-bottom:14px;} table{width:100%;border-collapse:collapse;font-size:12px;}
      th,td{padding:5px 9px;border-bottom:.5px solid #e6e0d4;} thead th{background:#2a2440;color:#fff;}
      tr.grp td{background:#efeaf9;color:#6c5ce7;font-weight:700;} tr.band td{background:#f3f0fb;color:#6c5ce7;font-weight:700;font-size:10px;text-transform:uppercase;}
      tr.band.ret td{background:#fbeef0;color:#b3261e;} tr.sub td{background:#faf7f0;font-weight:700;color:#8a6d3b;}
      tr.sub.ret td{background:#fdf2f4;color:#b3261e;} tr.sub.net td{background:#eef7f4;color:#11816f;font-weight:800;}
      tr.tot td{background:#eef7f4;font-weight:800;border-top:2px solid #2a2440;}
      *{-webkit-print-color-adjust:exact;print-color-adjust:exact;}
      .lh-head{margin-bottom:14px;} .lh-foot{margin-top:18px;border-top:1px solid #e6e0d4;padding-top:8px;font-size:10px;color:#888;}
      @media print{@page{margin:14mm;}}</style></head><body>
      ${lhHead ? '<div class="lh-head">' + lhHead + '</div>' : ''}
      <h1>${esc(title || doctype)}</h1><div class="meta">${esc(doctype)} · ${result.row_count} ${t('rows')}${result.group_by ? ' · ' + t('grouped by') + ' ' + esc(result.group_label) : ''} · ${fmtDMY(new Date().toISOString().slice(0, 10))}</div>
      <table><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table>
      ${lhFoot ? '<div class="lh-foot">' + lhFoot + '</div>' : ''}
      <script>window.onload=function(){window.print();}<\/script></body></html>`);
    w.document.close();
  }

  // shared renderers
  const dataRow = (r: any, key: any) => <tr key={key}>{colDefs.map((c: any) => <td key={c.field} className={c.numeric ? 'num' : ''} style={tdStyle(c)}>{isDateCol(c) ? fmtDMY(r[c.field]) : fmt(r[c.field], c.numeric, dec(c))}</td>)}</tr>;
  const subRow = (label: string, sub: any, cls: string) => <tr className={cls}>{colDefs.map((c: any, ci: number) => <td key={c.field} className={c.numeric ? 'num' : ''} style={tdStyle(c)}>{ci === 0 ? label : c.numeric ? fmt(sub[c.field], true, dec(c)) : ''}</td>)}</tr>;

  const filteredFields = fields.filter((f) =>
    (!fieldSearch || f.label.toLowerCase().includes(fieldSearch.toLowerCase()) || f.fieldname.includes(fieldSearch.toLowerCase()))
    && (!selectedOnly || cols.includes(f.fieldname)));

  return (
    <div className="studio">
      <div className="studio-hero">
        <div className="studio-hero-glow" aria-hidden />
        <div className="studio-hero-in">
          <div className="studio-brand">
            <span className="studio-spark">✦</span>
            <div><h1>{t('Report Studio')}</h1><p>{t('Describe the report you want — or build it by hand. No code.')}</p></div>
            <div className="studio-modetabs">
              <button className={mode === 'builder' ? 'on' : ''} onClick={() => setMode('builder')}>{t('Builder')}</button>
              <button className={mode === 'dashboard' ? 'on' : ''} onClick={() => setMode('dashboard')}>{t('Dashboard')}</button>
              <button className={mode === 'datasets' ? 'on' : ''} onClick={() => setMode('datasets')}>{t('Datasets')}</button>
            </div>
          </div>
          {mode === 'builder' && (
          <div className="studio-ai">
            <select className="studio-ai-doc" value={doctype} onChange={(e) => pickDoctype(e.target.value)}>
              <option value="">{t('Pick a document…')}</option>
              {sources.map((s) => <option key={s.name} value={s.name}>{s.label}</option>)}
            </select>
            <input className="studio-ai-input" value={aiPrompt} onChange={(e) => setAiPrompt(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') aiBuild(); }}
              placeholder={t('e.g. this year\u2019s totals by customer, only paid invoices')} />
            <button className="studio-ai-go" onClick={aiBuild} disabled={aiBusy || !doctype}>{aiBusy ? t('Thinking…') : <>✦ {t('Build with AI')}</>}</button>
          </div>
          )}
        </div>
      </div>

      {showSchedule && <ScheduleModal reports={saved} onClose={() => setShowSchedule(false)} />}
      {promptOpen && (
        <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) setPromptOpen(false); }}>
          <div className="theme-panel" role="dialog" aria-label={t('Report parameters')} style={{ width: 'min(520px, 100%)' }}>
            <div className="theme-h"><h3>❯ {t('Report parameters')}</h3>
              <button className="fh-x" onClick={() => setPromptOpen(false)} aria-label={t('Close')}>×</button></div>
            <p className="theme-hint">{t('This report asks for the following values each time it runs:')}</p>
            <div className="sched-form" style={{ gridTemplateColumns: '1fr' }}>
              {filters.map((f, i) => f.ask ? (
                <label key={i}><span className="flbl">{(fieldMap[f.field]?.label || f.field)} ({f.op})</span>
                  {(() => {
                    const isDate = ['Date', 'Datetime'].includes(fieldMap[f.field]?.fieldtype || '');
                    const typ = isDate ? 'date' : 'text';
                    return f.op === 'between' ? (
                      <span style={{ display: 'flex', gap: 6 }}>
                        <input type={typ} value={f.value} onChange={(e) => updFilter(i, { value: e.target.value })} placeholder={t('From')} />
                        <input type={typ} value={f.value2 || ''} onChange={(e) => updFilter(i, { value2: e.target.value })} placeholder={t('To')} />
                      </span>
                    ) : (
                      <input type={typ} value={f.value} onChange={(e) => updFilter(i, { value: e.target.value })} />
                    );
                  })()}
                </label>
              ) : null)}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
              <button className="studio-run" onClick={() => { setPromptOpen(false); run(); }}>{t('Run report')}</button>
              <button className="studio-ghost" onClick={() => setPromptOpen(false)}>{t('Cancel')}</button>
            </div>
          </div>
        </div>
      )}
      {mode === 'datasets' ? (
        <div className="studio-body" style={{ display: 'block', padding: 16 }}>
          <DatasetExplorer />
        </div>
      ) : mode === 'dashboard' ? (
        <StudioDashboard onOpen={(slug) => { setMode('builder'); load(slug); }} />
      ) : (
      <div className="studio-body">
        <aside className="studio-rail">
          <div className="studio-card">
            <div className="studio-card-h">{t('Document')}</div>
            <input className="studio-search" value={srcSearch} onChange={(e) => setSrcSearch(e.target.value)} placeholder={t('Search documents…')} />
            <div className="studio-src-list">
              {sources.map((s) => (
                <button key={s.name} className={'studio-src' + (doctype === s.name ? ' on' : '')} onClick={() => pickDoctype(s.name)}>
                  <span>{s.label}</span><small>{s.module}</small>
                </button>
              ))}
            </div>
          </div>

          {doctype && (
            <div className="studio-card">
              <div className="studio-card-h">{t('Fields')} <small>{cols.length}/{fields.length}</small></div>
              <input className="studio-field-search" value={fieldSearch} onChange={(e) => setFieldSearch(e.target.value)} placeholder={t('Search fields…')} />
              <div className="studio-field-tools">
                <button className={'studio-mini' + (selectedOnly ? ' on' : '')} onClick={() => setSelectedOnly((s) => !s)}>{t('Selected only')}</button>
                {cols.length > 0 && <button className="studio-mini" onClick={() => setCols([])}>{t('Clear')}</button>}
              </div>
              <div className="studio-field-list">
                {filteredFields.map((f) => (
                  <label key={f.fieldname} className={'studio-field' + (cols.includes(f.fieldname) ? ' on' : '')}>
                    <input type="checkbox" checked={cols.includes(f.fieldname)} onChange={() => toggleCol(f.fieldname)} />
                    <span className="studio-field-lbl">{f.label}</span>
                    <span className={'studio-chip-t t-' + (f.numeric ? 'num' : 'txt')}>{f.fieldtype}</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {doctype && cols.length > 0 && (
            <div className="studio-card">
              <div className="studio-card-h">{t('Columns (drag to reorder)')}</div>
              <div className="studio-selcols">
                {cols.map((c, i) => {
                  const f = fieldMap[c];
                  const label = f ? f.label : (c.includes('.') ? c.split('.').join(' · ') : c);
                  return (
                    <div key={c} draggable
                      className={'studio-selcol' + (dragIdx === i ? ' drag' : '') + (overIdx === i ? ' over' : '')}
                      onDragStart={() => setDragIdx(i)}
                      onDragOver={(e) => { e.preventDefault(); setOverIdx(i); }}
                      onDragEnd={() => { if (dragIdx !== null && overIdx !== null) moveCol(dragIdx, overIdx); setDragIdx(null); setOverIdx(null); }}
                      onDrop={(e) => { e.preventDefault(); if (dragIdx !== null) moveCol(dragIdx, i); setDragIdx(null); setOverIdx(null); }}>
                      <span className="grip">⠿</span><span className="nm">{label}</span>
                      <button className="rm" onClick={() => toggleCol(c)} aria-label="remove">×</button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {doctype && linkFields.length > 0 && (
            <div className="studio-card">
              <div className="studio-card-h">{t('Linked documents')}</div>
              {linkFields.map((lf) => (
                <div key={lf.link_field} className="studio-link">
                  <button className={'studio-link-h' + (openLink === lf.link_field ? ' on' : '')} onClick={() => toggleLink(lf)}>
                    <span>{lf.label}</span><small>{lf.target_doctype}</small>
                  </button>
                  {openLink === lf.link_field && (
                    <div className="studio-link-fields">
                      {(linkTargetFields[lf.link_field] || []).map((tf) => {
                        const key = `${lf.link_field}.${tf.fieldname}`;
                        return (
                          <label key={key} className={'studio-field' + (cols.includes(key) ? ' on' : '')}>
                            <input type="checkbox" checked={cols.includes(key)} onChange={() => toggleCol(key)} />
                            <span className="studio-field-lbl">{tf.label}</span>
                            <span className={'studio-chip-t t-' + (tf.numeric ? 'num' : 'txt')}>{tf.fieldtype}</span>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </aside>

        <main className="studio-main">
          {doctype && (
            <div className="studio-controls studio-card">
              <div className="studio-ctl-row">
                <div className="studio-ctl"><span className="studio-lbl">{t('Group by')}</span>
                  <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)}>
                    <option value="">{t('None')}</option>
                    {fields.map((f) => <option key={f.fieldname} value={f.fieldname}>{f.label}</option>)}
                  </select>
                </div>
                <div className="studio-ctl"><span className="studio-lbl">{t('Row limit')}</span>
                  <select value={limit} onChange={(e) => setLimit(parseInt(e.target.value))}>
                    {[100, 500, 1000, 2000, 5000].map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </div>
                <div className="studio-ctl-actions">
                  <div className="studio-viewtog">
                    <button className={view === 'table' ? 'on' : ''} onClick={() => setView('table')}>{t('Table')}</button>
                    <button className={view === 'chart' ? 'on' : ''} onClick={() => setView('chart')}>{t('Chart')}</button>
                  </div>
                  <button className="studio-run" onClick={run} disabled={loading}>{loading ? t('Running…') : t('Run')}</button>
                  <button className="studio-ghost" onClick={exportExcel} disabled={!result}>{t('Excel')}</button>
                  <select value={letterHead} onChange={(e) => setLetterHead(e.target.value)} title={t('Letter Head used as the header on Print / PDF / Excel — manage designs in ERP under Letter Head')}>
                    <option value="">{t('No letterhead')}</option>
                    {letterheads.map((l: any) => <option key={l.name} value={l.name}>{l.label || l.name}</option>)}
                  </select>
                  <button className="studio-ghost" onClick={printReport} disabled={!result}>{t('Print / PDF')}</button>
                  <button className="studio-ghost" onClick={saveAsDataset} disabled={dsSaving || !doctype} title={t('Turn the current numeric columns into a governed semantic dataset')}>{dsSaving ? t('Saving…') : t('Save as Dataset')}</button>
                  <button className="studio-ghost" onClick={() => setShowSchedule(true)} title={t('Email / WhatsApp saved reports on a schedule')}>{t('Schedules')}</button>
                </div>
              </div>

              <div className="studio-filters">
                <div className="studio-filters-h"><span className="studio-lbl">{t('Filters')}</span>
                  <button className="studio-addf" onClick={addFilter}>+ {t('Add filter')}</button></div>
                {filters.length === 0 && <div className="studio-hint">{t('No filters — showing everything.')}</div>}
                {filters.map((f, i) => (
                  <div key={i} className="studio-frow">
                    <select value={f.field} onChange={(e) => updFilter(i, { field: e.target.value })}>
                      {fields.map((x) => <option key={x.fieldname} value={x.fieldname}>{x.label}</option>)}
                    </select>
                    <select value={f.op} onChange={(e) => updFilter(i, { op: e.target.value })}>{OPS.map((o) => <option key={o} value={o}>{o}</option>)}</select>
                    {valueInput(f, i)}
                    <label className="studio-ask" title={t('Prompt for this value when the saved report is opened — a run-time parameter')}>
                      <input type="checkbox" checked={!!f.ask} onChange={(e) => updFilter(i, { ask: e.target.checked })} /> {t('Ask')}
                    </label>
                    <button className="studio-delf" onClick={() => delFilter(i)} aria-label="remove">×</button>
                  </div>
                ))}
              </div>

              <div className="studio-calc">
                <label className="studio-cbx" style={{ display: 'flex', gap: 7, alignItems: 'center', fontSize: 12.5 }}>
                  <input type="checkbox" checked={tiOn} onChange={(e) => { setTiOn(e.target.checked); if (!e.target.checked) setTiResult(null); }} /> {t('Time intelligence (MTD / QTD / YTD / YoY / 12M)')}
                </label>
                {tiOn && (
                  <div className="studio-frow" style={{ marginTop: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    <select value={tiDateField} onChange={(e) => setTiDateField(e.target.value)}>
                      <option value="">{t('Date field…')}</option>
                      {fields.filter((f) => DATE_TYPES.includes(f.fieldtype)).map((f) => <option key={f.fieldname} value={f.fieldname}>{f.label}</option>)}
                    </select>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {fields.filter((f) => f.numeric).slice(0, 14).map((f) => (
                        <label key={f.fieldname} className={'studio-mchip' + (tiMeasures.includes(f.fieldname) ? ' on' : '')}>
                          <input type="checkbox" checked={tiMeasures.includes(f.fieldname)}
                            onChange={() => setTiMeasures((m) => m.includes(f.fieldname) ? m.filter((x) => x !== f.fieldname) : (m.length < 6 ? [...m, f.fieldname] : m))} />
                          {f.label}
                        </label>
                      ))}
                    </div>
                    <input type="date" value={tiAsOf} onChange={(e) => setTiAsOf(e.target.value)} title={t('As of')} />
                    <button className="studio-run" onClick={runTimeIntel} disabled={tiBusy || !tiDateField || !tiMeasures.length}>
                      {tiBusy ? t('Running…') : t('Analyze')}
                    </button>
                    <span className="studio-hint">{t('Windows respect the ERPNext Fiscal Year; group split uses the Group by above.')}</span>
                  </div>
                )}
              </div>

              <div className="studio-calc">
                <label className="studio-cbx" style={{ display: 'flex', gap: 7, alignItems: 'center', fontSize: 12.5 }}>
                  <input type="checkbox" checked={pivotOn} onChange={(e) => { setPivotOn(e.target.checked); setResult(null); }} /> {t('Advanced pivot (cross-tab)')}
                </label>
                {pivotOn && (
                  <div className="studio-frow" style={{ marginTop: 6, flexWrap: 'wrap' }}>
                    <select value={pivotRow} onChange={(e) => setPivotRow(e.target.value)}>
                      <option value="">{t('Rows…')}</option>
                      {fields.map((x) => <option key={x.fieldname} value={x.fieldname}>{x.label}</option>)}
                    </select>
                    <select value={pivotCol} onChange={(e) => setPivotCol(e.target.value)}>
                      <option value="">{t('Columns (optional)…')}</option>
                      {fields.map((x) => <option key={x.fieldname} value={x.fieldname}>{x.label}</option>)}
                    </select>
                    <select value={pivotVal} onChange={(e) => setPivotVal(e.target.value)}>
                      <option value="">{pivotAgg === 'count' ? t('Value (optional)…') : t('Value…')}</option>
                      {(pivotAgg === 'count' ? fields : numericFields).map((x) => <option key={x.fieldname} value={x.fieldname}>{x.label}</option>)}
                    </select>
                    <select value={pivotAgg} onChange={(e) => setPivotAgg(e.target.value)}>
                      {['sum', 'count', 'avg', 'min', 'max'].map((a) => <option key={a} value={a}>{t(a)}</option>)}
                    </select>
                  </div>
                )}
                <div className="studio-hint">{t('Pivot turns Rows × Columns into a matrix of the aggregated Value.')}</div>
              </div>

              {hasReturns && !pivotOn && (
                <div className="studio-calc">
                  <label className="studio-cbx" style={{ display: 'flex', gap: 7, alignItems: 'center', fontSize: 12.5, opacity: groupBy ? 1 : 0.55 }}>
                    <input type="checkbox" checked={retOn} disabled={!groupBy} onChange={(e) => setRetOn(e.target.checked)} /> {t('Separate Sales & Returns')}
                    {!groupBy && <span className="studio-hint">{t('— pick a Group by first; the split runs inside each group')}</span>}
                  </label>
                  {retOn && (
                    <div className="studio-frow" style={{ marginTop: 6 }}>
                      <select value={retField} onChange={(e) => setRetField(e.target.value)} style={{ flex: 1 }}>
                        <option value="">{t('Field that marks a return…')}</option>
                        {fields.map((x) => <option key={x.fieldname} value={x.fieldname}>{x.label}</option>)}
                      </select>
                      <input value={retValue} onChange={(e) => setRetValue(e.target.value)} placeholder={t('= value (e.g. 1 or Return)')} style={{ flex: 1 }} />
                    </div>
                  )}
                  <div className="studio-hint">{t('Returns are listed and subtotalled separately; group subtotal = Sales − Returns.')}</div>
                </div>
              )}

              <div className="studio-calc">
                <div className="studio-filters-h"><span className="studio-lbl">{t('Calculated columns')}</span></div>
                <div className="studio-frow">
                  <input value={calcLabel} onChange={(e) => setCalcLabel(e.target.value)} placeholder={t('Column name (e.g. Net)')} style={{ flex: 1 }} />
                  <input value={calcFormula} onChange={(e) => setCalcFormula(e.target.value)} placeholder={t('Formula e.g. qty * rate')} style={{ flex: 1.6 }} />
                  <button className="studio-addf" onClick={addCalc}>+ {t('Add')}</button>
                </div>
                {calculated.length > 0 && (
                  <div className="studio-calc-list">
                    {calculated.map((c) => (<span key={c.key} className="studio-calc-chip">{c.label} = <code>{c.formula}</code><button onClick={() => delCalc(c.key)} aria-label="remove">×</button></span>))}
                  </div>
                )}
                <div className="studio-hint">{t('Use field names; operators + - * / ; functions abs, round, min, max.')}</div>
              </div>

              <div className="studio-save">
                <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t('Report title to save…')} />
                <button className="studio-ghost" onClick={save}>{t('Save')}</button>
                {saved.length > 0 && (
                  <select className="studio-saved" value="" onChange={(e) => e.target.value && load(e.target.value)}>
                    <option value="">{t('Open saved…')}</option>
                    {saved.map((s) => <option key={s.slug} value={s.slug}>{s.title}</option>)}
                  </select>
                )}
              </div>
            </div>
          )}

          {err && <div className="studio-err">{err}</div>}
          {result && ((pivotOn && !result.pivot) || (!pivotOn && result.pivot)) && (
            <div className="studio-stale">↻ {t('Your settings changed — click Run to refresh the result.')}
              <button className="studio-run" style={{ marginInlineStart: 12, padding: '6px 16px' }} onClick={run}>{t('Run')}</button>
            </div>
          )}
          {!doctype && !err && (
            <div className="studio-empty">
              <div className="studio-empty-art" aria-hidden>✦</div>
              <h2>{t('Pick a document to begin')}</h2>
              {saved.length > 0 && (
                <div className="studio-empty-saved">
                  <div className="theme-sec-title">{t('Or open a saved report')}</div>
                  <div className="studio-saved-grid">
                    {saved.slice(0, 12).map((r: any) => (
                      <button key={r.slug} className="studio-saved-card" onClick={() => load(r.slug)}>
                        <span className="studio-saved-title">{r.title}</span>
                        <span className="studio-saved-dt">{r.doctype}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <p>{t('Choose any document on the left, then describe your report to the AI or pick fields yourself.')}</p>
            </div>
          )}

          {result && view === 'chart' && (
            <div className="studio-result studio-card">
              <div className="studio-chart-ctl">
                <div className="rf"><span className="studio-lbl">{t('Chart type')}</span>
                  <select value={chartType} onChange={(e) => setChartType(e.target.value)}>
                    {CHART_TYPES.map((ct) => <option key={ct} value={ct}>{t(ct)}</option>)}
                  </select>
                </div>
                {result.rows && !result.pivot && (
                  <div className="rf"><span className="studio-lbl">{t('Category')}</span>
                    <select value={chartCategory} onChange={(e) => setChartCategory(e.target.value)}>
                      <option value="">{t('auto')}</option>
                      {(result.columns || []).filter((c: any) => !c.numeric).map((c: any) => <option key={c.field} value={c.field}>{c.label}</option>)}
                    </select>
                  </div>
                )}
                {!result.pivot && (
                  <div className="rf"><span className="studio-lbl">{t('Measures')}</span>
                    <div className="studio-measures">
                      {(result.columns || []).filter((c: any) => c.numeric).map((c: any) => (
                        <label key={c.field} className={'studio-mchip' + (chartMeasures.includes(c.field) ? ' on' : '')}>
                          <input type="checkbox" checked={chartMeasures.includes(c.field)}
                            onChange={() => setChartMeasures((m) => m.includes(c.field) ? m.filter((x) => x !== c.field) : [...m, c.field])} />
                          {c.label}
                        </label>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              <StudioChart result={result} cfg={{ type: chartType, category: chartCategory, measures: chartMeasures }}
                onPick={(lbl) => { setDrillKey(lbl); setView('table'); }} />
              <div className="studio-hint" style={{ marginTop: 6 }}>{t('Tip: click any bar, point or slice to drill into its rows.')}</div>
            </div>
          )}

          {tiOn && tiResult && (
            <div className="studio-result studio-card">
              <div className="studio-result-h">
                <strong>{t('Time Intelligence')}</strong> · {t('as of')} {tiResult.as_of}
                {tiResult.group_by ? <> · {t('by')} <strong>{tiResult.group_label}</strong></> : null}
              </div>
              <div className="studio-table-wrap">
                <table className="studio-table">
                  <thead>
                    <tr>
                      {tiResult.group_by && <th>{tiResult.group_label}</th>}
                      <th>{t('Measure')}</th>
                      <th className="num">MTD</th><th className="num">MoM %</th>
                      <th className="num">QTD</th>
                      <th className="num">YTD</th><th className="num">{t('PY YTD')}</th><th className="num">YoY %</th>
                      <th className="num">{t('Rolling 12M')}</th><th className="num">Δ12M %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(tiResult.rows || []).map((r: any) => (tiResult.measures || []).map((m: any, mi: number) => {
                      const v = r[m.field] || {};
                      const pcell = (p: number | null) => p == null ? <td className="num">—</td>
                        : <td className={'num ' + (p >= 0 ? 'ti-pos' : 'ti-neg')}>{p >= 0 ? '+' : ''}{p}%</td>;
                      return (
                        <tr key={r.group + m.field}>
                          {tiResult.group_by && <td>{mi === 0 ? String(r.group) : ''}</td>}
                          <td>{m.label}</td>
                          <td className="num">{fmt(v.mtd, true)}</td>{pcell(v.mom_pct)}
                          <td className="num">{fmt(v.qtd, true)}</td>
                          <td className="num">{fmt(v.ytd, true)}</td>
                          <td className="num">{fmt(v.py_ytd, true)}</td>{pcell(v.yoy_pct)}
                          <td className="num">{fmt(v.r12, true)}</td>{pcell(v.r12_pct)}
                        </tr>
                      );
                    }))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {result && result.pivot && view === 'table' && (
            <div className="studio-result studio-card">
              <div className="studio-result-h">
                {t('Pivot')}: <strong>{result.pivot.row_label}</strong> × <strong>{result.pivot.col_label}</strong>
                {' · '}{t(result.pivot.agg)} {t('of')} <strong>{result.pivot.value_label}</strong>
                <button className="studio-ghost" style={{ marginInlineStart: 12, padding: '4px 12px' }} onClick={exportPivot}>{t('Excel')}</button>
              </div>
              <div className="studio-table-wrap">
                <table className="studio-table">
                  <thead><tr>
                    <th>{result.pivot.row_label}</th>
                    {result.pivot.columns.map((c: string) => <th key={c} className="num">{c}</th>)}
                    <th className="num">{result.pivot.single ? result.pivot.value_label : t('Total')}</th>
                  </tr></thead>
                  <tbody>
                    {result.pivot.rows.map((r: any) => (
                      <tr key={r.key}>
                        <td>{r.key}</td>
                        {result.pivot.columns.map((c: string) => <td key={c} className="num">{fmt(r.cells[c], true, 2)}</td>)}
                        <td className="num" style={{ fontWeight: 700 }}>{fmt(r.total, true, 2)}</td>
                      </tr>
                    ))}
                    <tr className="studio-total">
                      <td>{t('TOTAL')}</td>
                      {result.pivot.columns.map((c: string) => <td key={c} className="num">{fmt(result.pivot.col_totals[c], true, 2)}</td>)}
                      <td className="num">{fmt(result.pivot.grand_total, true, 2)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {result && !result.pivot && view === 'table' && (
            <div className="studio-result studio-card">
              {filters.length > 0 && (
                <div className="studio-refine">
                  {filters.map((f, i) => (
                    <div key={i} className="rf">
                      <span className="studio-lbl">{fieldMap[f.field]?.label || f.field} · {f.op}</span>
                      {valueInput(f, i)}
                    </div>
                  ))}
                  <button className="rf-apply" onClick={run}>{t('Apply')}</button>
                </div>
              )}
              <div className="studio-result-h">
                <strong>{result.row_count}</strong> {t('rows')}{result.group_by ? <> · {t('grouped by')} <strong>{result.group_label}</strong></> : null}
                {drillKey != null && (
                  <span className="studio-drillchip">
                    <i className="ti ti-filter" aria-hidden /> {t('Drilled')}: <b>{drillKey}</b>
                    <button onClick={() => setDrillKey(null)} aria-label={t('Clear drill')}>×</button>
                  </span>
                )}
                <span className="studio-tip"> · {t('click a column header to style it')}</span>
              </div>
              <div className="studio-table-wrap">
                <table className="studio-table">
                  <thead><tr>{colDefs.map((c: any) => (
                    <th key={c.field} className={c.numeric ? 'num' : ''} style={tdStyle(c)} onClick={() => setInspect(inspect === c.field ? '' : c.field)} title={t('Style / show-as')}>
                      {lbl(c)}{colModes[c.field] === 'running' ? ' ∑' : colModes[c.field] === 'pct_total' ? ' %' : ''}
                    </th>))}</tr></thead>
                  {result.groups ? result.groups.filter((g: any) => drillKey == null || String(g.key) === drillKey).map((g: any) => (
                    <tbody key={g.key} className="studio-grp">
                      <tr className="studio-grp-h"><td colSpan={colDefs.length}>{result.group_label}: <b>{String(g.key)}</b> <span className="studio-grp-n">{g.count}</span></td></tr>
                      {result.split_returns ? (<>
                        <tr className="studio-band"><td colSpan={colDefs.length}>{t('Sales')}</td></tr>
                        {(g.sales_rows || []).map((r: any, i: number) => dataRow(r, 's' + i))}
                        {subRow(t('Sales Subtotal'), g.sales_subtotal || {}, 'studio-sub')}
                        {(g.return_rows || []).length > 0 && (<>
                          <tr className="studio-band ret"><td colSpan={colDefs.length}>{t('Returns')}</td></tr>
                          {g.return_rows.map((r: any, i: number) => dataRow(r, 'r' + i))}
                          {subRow(t('Returns Subtotal'), g.return_subtotal || {}, 'studio-sub ret')}
                        </>)}
                        {subRow(t('Net Subtotal'), g.net_subtotal || {}, 'studio-sub net')}
                      </>) : (<>
                        {(g.rows || []).map((r: any, i: number) => dataRow(r, i))}
                        {subRow(t('Subtotal'), g.subtotal || {}, 'studio-sub')}
                      </>)}
                    </tbody>
                  )) : (<tbody>{(result.rows || [])
                    .filter((r: any) => {
                      if (drillKey == null) return true;
                      const cat = chartCategory || (colDefs.find((c: any) => !c.numeric) || {}).field;
                      return cat ? String(r[cat] ?? '—') === drillKey : true;
                    })
                    .map((r: any, i: number) => dataRow(r, i))}</tbody>)}
                  <tbody>{subRow(t('TOTAL'), result.grand_total || {}, 'studio-total')}</tbody>
                </table>
              </div>
            </div>
          )}
        </main>
      </div>
      )}

      {inspect && (() => {
        const c = colDefs.find((x: any) => x.field === inspect); if (!c) return null;
        const m = colMeta[inspect] || {};
        return (
          <div className="studio-inspect">
            <div className="studio-inspect-h">{t('Column')}: <b>{c.label}</b><button onClick={() => setInspect('')} aria-label="close">×</button></div>
            <label>{t('Label')}<input value={m.label ?? ''} onChange={(e) => setMeta(inspect, { label: e.target.value })} placeholder={c.label} /></label>
            <div className="studio-inspect-row">
              <label>{t('Align')}<select value={m.align || (c.numeric ? 'right' : 'left')} onChange={(e) => setMeta(inspect, { align: e.target.value })}><option value="left">left</option><option value="center">center</option><option value="right">right</option></select></label>
              {c.numeric && <label>{t('Decimals')}<select value={m.decimals ?? 2} onChange={(e) => setMeta(inspect, { decimals: parseInt(e.target.value) })}><option value={0}>0</option><option value={2}>2</option><option value={3}>3</option></select></label>}
            </div>
            <div className="studio-inspect-row">
              <label>{t('Width (px)')}<input type="number" value={m.width ?? ''} onChange={(e) => setMeta(inspect, { width: e.target.value ? parseInt(e.target.value) : undefined })} placeholder="auto" /></label>
              <label>{t('Color')}<input type="color" value={m.color || '#2a2440'} onChange={(e) => setMeta(inspect, { color: e.target.value })} /></label>
            </div>
            {c.numeric && (
              <label>{t('Show as')}<select value={colModes[inspect] || 'value'} onChange={(e) => { const v = e.target.value; setColModes((mm) => { const n = { ...mm }; if (v === 'value') delete n[inspect]; else n[inspect] = v; return n; }); }}>
                <option value="value">{t('Value')}</option><option value="running">{t('Running total')}</option><option value="pct_total">{t('% of total')}</option>
              </select></label>
            )}
            <button className="studio-run" style={{ width: '100%', marginTop: 8 }} onClick={() => { setInspect(''); run(); }}>{t('Apply & run')}</button>
          </div>
        );
      })()}
    </div>
  );
}
