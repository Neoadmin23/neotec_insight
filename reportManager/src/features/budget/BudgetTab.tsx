import { useEffect, useMemo, useState } from 'react';
import { t } from '../../utils/i18n';
import type { ReportDefinition, BudgetBook, DimensionType, BookStatus } from '../../types';
import { api } from '../../utils/api';
import * as XLSX from 'xlsx';
import { MONTHS, FY_RANGE } from '../../utils/format';

interface Props { report: ReportDefinition }

const DIM_LABEL: Record<DimensionType, string> = {
  total: 'Total Company',
  cost_center: 'Cost Center',
  department: 'Department',
  project: 'Project',
  custom: 'Accounting Dimension',
};

const STATUS_LABEL: Record<BookStatus, string> = {
  draft: 'Draft',
  submitted: 'Submitted',
  approved: 'Approved',
  locked: 'Locked',
};

export function BudgetTab({ report }: Props) {
  const [fy, setFy] = useState(2026);
  const [books, setBooks] = useState<BudgetBook[]>([]);
  const [activeSlug, setActiveSlug] = useState<string>('');
  const [cells, setCells] = useState<Record<string, Record<number, number>>>({});
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [bookLoading, setBookLoading] = useState(false);

  // v1.9.56 — Derive from prior year + Copy to another fiscal year.
  const [deriveOpen, setDeriveOpen] = useState(false);
  const [copyOpen, setCopyOpen] = useState(false);

  const rows = report.definition.rows;
  const sourceRows = useMemo(() => rows.filter((r) => r.kind === 'source'), [rows]);
  const activeBook = books.find((b) => b.slug === activeSlug);
  const primaryAxis = (report as any).primary_budget_axis || 'cost_center';

  async function reloadBooks(preserveActive = true) {
    if (!report.name) return;
    try {
      const list = (await api.listBudgetBooks(report.name, fy)) as BudgetBook[];
      setBooks(list);
      if (list.length === 0) {
        setActiveSlug('');
        setCells({});
        return;
      }
      const keepCurrent = preserveActive && list.find((b) => b.slug === activeSlug);
      if (!keepCurrent) {
        // Default to Total book if present, else first.
        const total = list.find((b) => b.dimension_type === 'total');
        setActiveSlug((total || list[0]).slug);
      }
    } catch (e: any) {
      setStatus('Could not load books: ' + (e?.message || 'unknown'));
    }
  }

  async function reloadActiveBook() {
    if (!activeSlug) {
      setCells({});
      return;
    }
    setBookLoading(true);
    try {
      const r = await api.getBudgetBook(activeSlug);
      setCells(r.cells || {});
      setDirty(false);
      setStatus('');
    } catch (e: any) {
      setStatus('Could not load cells: ' + (e?.message || 'unknown'));
    } finally {
      setBookLoading(false);
    }
  }

  useEffect(() => { reloadBooks(false); /* eslint-disable-next-line */ }, [report.name, fy]);
  useEffect(() => { reloadActiveBook(); /* eslint-disable-next-line */ }, [activeSlug]);

  function setCell(rowKey: string, month: number, raw: string) {
    const n = parseFloat(raw.replace(/,/g, '')) || 0;
    setCells((prev) => {
      const next = { ...prev };
      next[rowKey] = { ...(next[rowKey] || {}) };
      next[rowKey][month] = n;
      return next;
    });
    setDirty(true);
  }

  async function save() {
    if (!activeBook) return;
    if (!activeBook.can_edit) {
      setStatus('This book is ' + STATUS_LABEL[activeBook.status] + ' — read-only.');
      return;
    }
    const payload: any[] = [];
    sourceRows.forEach((r) => {
      for (let m = 0; m < 12; m++) {
        const v = cells[r.key]?.[m];
        if (v === undefined) continue;
        payload.push({ row_key: r.key, month: m, amount: v });
      }
    });
    try {
      const res = await api.saveBudgetCells(activeBook.name, payload);
      setStatus(`Saved ${res.written} cell${res.written === 1 ? '' : 's'}${res.deleted ? `, removed ${res.deleted} zeros` : ''}.`);
      setDirty(false);
    } catch (e: any) {
      setStatus('Save failed: ' + (e?.message || 'unknown'));
    }
  }

  function distribute() {
    if (!activeBook?.can_edit) return;
    setCells((prev) => {
      const next: typeof prev = {};
      sourceRows.forEach((r) => {
        const annual = Object.values(prev[r.key] || {}).reduce((a, b) => a + b, 0);
        next[r.key] = {};
        if (annual === 0) return;
        const monthly = Math.round(annual / 12);
        for (let m = 0; m < 12; m++) next[r.key][m] = monthly;
      });
      return next;
    });
    setDirty(true);
  }

  async function rollUp() {
    if (!report.name) return;
    if (!confirm(`Roll up all ${DIM_LABEL[primaryAxis as DimensionType] || primaryAxis} books for FY${fy} into the Total Company book? This overwrites the Total book's cells.`)) return;
    try {
      const r = await api.rollupToTotal(report.name, fy);
      setStatus(`Rolled up ${r.contributing_books} ${r.primary_axis} book${r.contributing_books === 1 ? '' : 's'} → ${r.cells_written} cells in the Total book.`);
      await reloadBooks(false);
      setActiveSlug(r.book.slug);
    } catch (e: any) {
      setStatus('Roll-up failed: ' + (e?.message || 'unknown'));
    }
  }

  async function changeStatus(next: BookStatus) {
    if (!activeBook) return;
    try {
      await api.updateBudgetBook(activeBook.name, { status: next });
      setStatus(`Status changed to ${STATUS_LABEL[next]}.`);
      reloadBooks(true);
    } catch (e: any) {
      setStatus('Status change failed: ' + (e?.message || 'unknown'));
    }
  }

  async function deleteBook() {
    if (!activeBook) return;
    if (!confirm(`Delete book "${activeBook.label}" and all its cells? This cannot be undone.`)) return;
    try {
      await api.deleteBudgetBook(activeBook.name);
      setActiveSlug('');
      await reloadBooks(false);
      setStatus('Book deleted.');
    } catch (e: any) {
      setStatus('Delete failed: ' + (e?.message || 'unknown'));
    }
  }

  return (
    <div>
      <div className="book-strip">
        <div className="book-strip-top">
          <label><span className="flbl">Fiscal year</span>
            <select value={fy} onChange={(e) => setFy(parseInt(e.target.value))}>
              {FY_RANGE.map((y) => <option key={y} value={y}>FY{y}</option>)}
            </select>
          </label>
          <label style={{ flex: 1, minWidth: 280 }}>
            <span className="flbl">Active book</span>
            <select value={activeSlug} onChange={(e) => { if (dirty && !confirm('Unsaved changes will be lost.')) return; setActiveSlug(e.target.value); }}>
              {books.length === 0 && <option value="">No books yet — create one</option>}
              {books.map((b) => (
                <option key={b.slug} value={b.slug}>
                  {b.label}  [{STATUS_LABEL[b.status].toUpperCase()}]
                </option>
              ))}
            </select>
          </label>
          <button onClick={() => setCreateOpen(true)} className="primary-btn"><i className="ti ti-plus" aria-hidden /> New book</button>
          <select className="ghost-btn" title="Dimension for Template / Import"
            defaultValue={(() => { try { return localStorage.getItem('ni-budget-impdim') || 'cost_center'; } catch { return 'cost_center'; } })()}
            onChange={(e) => { try { localStorage.setItem('ni-budget-impdim', e.target.value); } catch { /* */ } }}>
            <option value="cost_center">Cost Center</option>
            <option value="department">Department</option>
            <option value="project">Project</option>
          </select>
          <button className="ghost-btn" onClick={() => {
            let d = 'cost_center'; try { d = localStorage.getItem('ni-budget-impdim') || 'cost_center'; } catch { /* */ }
            const url = '/api/method/neotec_insight.neotec_insight.api.budget_import.budget_import_template'
              + `?report=${encodeURIComponent(report.name!)}&fiscal_year=${fy}&dimension_type=${d}`;
            window.open(url, '_blank');
          }} title="Download the bulk template: every cost center × every source row × 12 months">⬇ Template</button>
          <button className="ghost-btn" title="Export this book's grid — all rows, months, annual, formulas computed — as Excel"
            onClick={() => {
              import('xlsx').then((XLSX) => {
                const head = ['Row', 'Key', ...MONTHS, 'Annual'];
                const aoa: any[][] = [
                  [`${activeBook?.label || ''} — FY${fy}`],
                  [(report as any).report_name || report.name],
                  [],
                  head,
                ];
                for (const r of rows) {
                  if (r.kind === 'section') { aoa.push([r.label]); continue; }
                  if (r.kind === 'formula') {
                    const vals = Array.from({ length: 12 }, (_x, m) => formulaTotalRaw(rows, cells, r.formula || '', m));
                    aoa.push([r.label, r.key, ...vals.map((v) => Math.round(v * 100) / 100), Math.round(vals.reduce((a, b) => a + b, 0) * 100) / 100]);
                    continue;
                  }
                  const vals = Array.from({ length: 12 }, (_x, m) => cells[r.key]?.[m] || 0);
                  aoa.push([r.label, r.key, ...vals, vals.reduce((a, b) => a + b, 0)]);
                }
                const ws = XLSX.utils.aoa_to_sheet(aoa);
                ws['!cols'] = [{ wch: 40 }, { wch: 18 }, ...Array.from({ length: 13 }, () => ({ wch: 13 }))];
                const wb = XLSX.utils.book_new();
                XLSX.utils.book_append_sheet(wb, ws, 'Budget');
                XLSX.writeFile(wb, `budget-${activeBook?.slug || 'book'}-fy${fy}.xlsx`);
              });
            }}>⬇ Excel</button>
          <label className="ghost-btn" style={{ cursor: 'pointer' }} title="Upload the filled template — creates/updates one DRAFT book per cost center with all cells">
            ⬆ Import
            <input type="file" accept=".xlsx,.xls" style={{ display: 'none' }} onChange={async (e) => {
              const f = e.target.files?.[0]; if (!f) return;
              try {
                const wb = XLSX.read(await f.arrayBuffer());
                const ws = wb.Sheets[wb.SheetNames[0]];
                const arr: any[][] = XLSX.utils.sheet_to_json(ws, { header: 1 });
                const hi = arr.findIndex((r) => r[0] === 'Dimension Value');
                if (hi < 0) { alert('Header row not found — use the downloaded template.'); return; }
                const rows = arr.slice(hi + 1).filter((r) => r[0] && r[1]).map((r) => ({
                  value: String(r[0]).trim(), row_key: String(r[1]).trim(),
                  months: Object.fromEntries(Array.from({ length: 12 }, (_x, i) => [String(i), Number(r[3 + i] || 0)])),
                }));
                if (!rows.length) { alert('No data rows found.'); return; }
                let dimSel = 'cost_center'; try { dimSel = localStorage.getItem('ni-budget-impdim') || 'cost_center'; } catch { /* */ }
                const res = await api.budgetImport({ report: report.name, fiscal_year: fy, dimension_type: dimSel, rows });
                alert(`Imported: ${res.books_touched} books (${res.books_created} new), ${res.cells_written} cells.` + (res.warnings?.length ? `\nWarnings:\n` + res.warnings.join('\n') : ''));
                reloadBooks(true);
              } catch (err: any) { alert(String(err?.message || err)); }
              finally { e.target.value = ''; }
            }} />
          </label>
          {primaryAxis !== 'none' && (
            <button onClick={rollUp} title={`Sum all ${DIM_LABEL[primaryAxis as DimensionType] || primaryAxis} books into Total`}>
              <i className="ti ti-refresh" aria-hidden /> Roll up to Total
            </button>
          )}
        </div>
        {activeBook && (
          <div className="book-meta-grid">
            <div className="meta-card"><div className="meta-l">Dimension</div><div className="meta-v">{DIM_LABEL[activeBook.dimension_type]}</div></div>
            <div className="meta-card"><div className="meta-l">Value</div><div className="meta-v">{activeBook.dimension_value || '—'}</div></div>
            <div className="meta-card"><div className="meta-l">Owner</div><div className="meta-v">{activeBook.owner_user || 'Unassigned'}</div></div>
            <div className={'meta-card meta-status meta-status-' + activeBook.status}>
              <div className="meta-l">Status</div>
              <div className="meta-v">{STATUS_LABEL[activeBook.status]}</div>
            </div>
          </div>
        )}
        {activeBook && (
          <div className="book-actions-row">
            {activeBook.status === 'draft' && activeBook.can_edit && <button onClick={() => changeStatus('submitted')}>Submit for approval</button>}
            {activeBook.status === 'submitted' && <button onClick={() => changeStatus('approved')}>Approve</button>}
            {activeBook.status === 'submitted' && <button onClick={() => changeStatus('draft')}>Send back to draft</button>}
            {(activeBook.status === 'approved' || activeBook.status === 'locked') && activeBook.can_edit && <button onClick={() => changeStatus('draft')}>Unlock to edit</button>}
            <button onClick={distribute} disabled={!activeBook.can_edit}><i className="ti ti-equal" aria-hidden /> Distribute annual ÷ 12</button>
            <button
              onClick={() => setDeriveOpen(true)}
              disabled={!activeBook.can_edit}
              title="Generate cells from a prior year's actuals, with configurable growth %"
            >
              <i className="ti ti-wand" aria-hidden /> Derive from prior year
            </button>
            <button
              onClick={() => setCopyOpen(true)}
              title="Duplicate this book to a different fiscal year (copies all cells)"
            >
              <i className="ti ti-copy" aria-hidden /> Copy to another year
            </button>
            <button onClick={save} disabled={!dirty || !activeBook.can_edit} className="primary-btn"><i className="ti ti-device-floppy" aria-hidden /> Save {dirty ? '(unsaved)' : ''}</button>
            <span style={{ flex: 1 }} />
            <button onClick={deleteBook} title="Delete this book"><i className="ti ti-trash" aria-hidden /></button>
            {status && <span className="run-meta">{status}</span>}
          </div>
        )}
      </div>

      {books.length === 0 && !bookLoading && (
        <div className="card" style={{ padding: 22, textAlign: 'center' }}>
          <div className="strong" style={{ marginBottom: 6 }}>No budget books for FY{fy} yet.</div>
          <div className="muted" style={{ marginBottom: 14, fontSize: 12 }}>
            Create a book per cost center, project, or department you want to budget against — or just one Total Company book if you don't slice by dimension.
          </div>
          <button onClick={() => setCreateOpen(true)} className="primary-btn"><i className="ti ti-plus" aria-hidden /> Create first book</button>
        </div>
      )}

      {activeBook && (
        <div className="card" style={{ overflow: 'auto', padding: 0, marginTop: 10 }}>
          <table className="bud-grid">
            <thead>
              <tr>
                <th>Row</th>
                {MONTHS.map((m) => <th key={m}>{m}</th>)}
                <th className="bud-annual">Annual</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                if (r.kind === 'section') {
                  return <tr key={r.key} className="bud-section"><td colSpan={14}>{r.label}</td></tr>;
                }
                if (r.kind === 'formula') {
                  // Compute formula totals from the current draft state so the user gets live feedback.
                  return (
                    <tr key={r.key} className="bud-formula">
                      <td>{r.label} <code className="fp">{r.formula}</code></td>
                      {MONTHS.map((_, m) => <td key={m} className="bud-readonly">{formulaTotal(rows, cells, r.formula || '', m)}</td>)}
                      <td className="bud-annual bud-readonly">
                        {(() => {
                          let sum = 0;
                          for (let m = 0; m < 12; m++) sum += formulaTotalRaw(rows, cells, r.formula || '', m);
                          return sum.toLocaleString();
                        })()}
                      </td>
                    </tr>
                  );
                }
                const annual = MONTHS.reduce((acc, _, m) => acc + (cells[r.key]?.[m] || 0), 0);
                return (
                  <tr key={r.key}>
                    <td>{r.label}</td>
                    {MONTHS.map((_, m) => (
                      <td key={m}>
                        <input
                          type="text"
                          inputMode="numeric"
                          value={cells[r.key]?.[m] ?? 0}
                          onChange={(e) => setCell(r.key, m, e.target.value)}
                          disabled={!activeBook?.can_edit || bookLoading}
                          style={{ width: 78, textAlign: 'right', padding: '2px 4px', fontVariantNumeric: 'tabular-nums', fontSize: 11 }}
                        />
                      </td>
                    ))}
                    <td className="bud-annual">{annual.toLocaleString()}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {createOpen && (
        <CreateBookDialog
          report={report}
          initialFy={fy}
          onClose={() => setCreateOpen(false)}
          onCreated={async (slug) => {
            setCreateOpen(false);
            await reloadBooks(false);
            setActiveSlug(slug);
          }}
        />
      )}

      {/* v1.9.56 — Derive from prior year dialog */}
      {deriveOpen && activeBook && (
        <DeriveBudgetDialog
          book={activeBook}
          rows={rows}
          onClose={() => setDeriveOpen(false)}
          onDone={async () => {
            setDeriveOpen(false);
            await reloadActiveBook();
            setStatus('Derive complete.');
          }}
        />
      )}

      {/* v1.9.56 — Copy book to another fiscal year */}
      {copyOpen && activeBook && (
        <CopyBudgetDialog
          book={activeBook}
          onClose={() => setCopyOpen(false)}
          onDone={async (newName) => {
            setCopyOpen(false);
            await reloadBooks(false);
            setStatus(`Copied to ${newName}.`);
          }}
        />
      )}
    </div>
  );
}

function formulaTotal(rows: any[], cells: Record<string, Record<number, number>>, expr: string, month: number): string {
  const v = formulaTotalRaw(rows, cells, expr, month);
  return v.toLocaleString();
}

function formulaTotalRaw(rows: any[], cells: Record<string, Record<number, number>>, expr: string, month: number): number {
  // Lightweight client-side evaluator — for display only, the server is authoritative.
  // Supports +, -, *, /, parens, and references to row keys.
  const memo: Record<string, number> = {};
  const resolveRow = (key: string): number => {
    if (memo[key] !== undefined) return memo[key];
    const row = rows.find((r) => r.key === key);
    if (!row) return 0;
    if (row.kind === 'source') {
      memo[key] = cells[key]?.[month] || 0;
      return memo[key];
    }
    if (row.kind === 'formula') {
      memo[key] = evalSimple(row.formula || '0', resolveRow);
      return memo[key];
    }
    memo[key] = 0;
    return 0;
  };
  try {
    return Math.round(evalSimple(expr, resolveRow));
  } catch {
    return 0;
  }
}

function evalSimple(expr: string, resolve: (key: string) => number): number {
  // Replace identifiers with their numeric values, then `new Function` on the rest.
  // Whitelist: identifiers, digits, dots, parens, math operators, whitespace.
  if (!/^[\s\w+\-*/().,]+$/.test(expr)) return 0;
  const replaced = expr.replace(/[A-Za-z_][A-Za-z0-9_]*/g, (m) => String(resolve(m)));
  // eslint-disable-next-line no-new-func
  const v = Function('"use strict"; return (' + replaced + ');')();
  return typeof v === 'number' && isFinite(v) ? v : 0;
}

function CreateBookDialog({ report, initialFy, onClose, onCreated }: {
  report: ReportDefinition;
  initialFy: number;
  onClose: () => void;
  onCreated: (slug: string) => void;
}) {
  const [fy, setFy] = useState(initialFy);
  const [dimType, setDimType] = useState<DimensionType>('cost_center');
  const [dimValue, setDimValue] = useState('');
  const [labelIsCustom, setLabelIsCustom] = useState(false);
  const [customLabel, setCustomLabel] = useState('');
  const [ownerUser, setOwnerUser] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // Dimension-value options: load from the appropriate ERP master based
  // on the picked dimension type. Books are report-scoped, so we pull the
  // master for whichever company is set on the report (if any).
  const [valueOptions, setValueOptions] = useState<{ name: string; label: string }[]>([]);
  const [valueLoading, setValueLoading] = useState(false);
  // v2.35.0 — custom Accounting Dimension books (Branch, Business Division…)
  const [acctDims, setAcctDims] = useState<{ fieldname: string; label: string }[]>([]);
  const [customField, setCustomField] = useState('');
  useEffect(() => {
    if (dimType === 'custom' && acctDims.length === 0) {
      api.listAccountingDimensions().then((d: any[]) => setAcctDims(d || [])).catch(() => {});
    }
  }, [dimType]); // eslint-disable-line react-hooks/exhaustive-deps
  const reportCompany = (report as any).company || undefined;

  useEffect(() => {
    if (dimType === 'total') { setValueOptions([]); return; }
    let cancelled = false;
    setValueLoading(true);
    (async () => {
      try {
        let opts: any[] = [];
        if (dimType === 'cost_center') opts = (await api.listCostCenters(reportCompany)) as any[];
        else if (dimType === 'project') opts = (await api.listProjects(reportCompany)) as any[];
        else if (dimType === 'department') opts = (await api.listDepartments(reportCompany)) as any[];
        else if (dimType === 'custom') opts = customField ? (await api.listDimensionValues(customField)) as any[] : [];
        if (cancelled) return;
        setValueOptions(opts.map((o) => ({ name: o.name, label: o.label || o.name })));
        // Clear stale picks when the dim type changes.
        setDimValue('');
      } catch {
        if (!cancelled) setValueOptions([]);
      } finally {
        if (!cancelled) setValueLoading(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dimType]);

  const autoLabel = useMemo(() => {
    if (dimType === 'total') return `FY${fy} · Total Company`;
    return `FY${fy} · ${DIM_LABEL[dimType]}: ${dimValue || '(set value)'}`;
  }, [fy, dimType, dimValue]);

  const finalLabel = labelIsCustom ? customLabel : autoLabel;

  async function submit() {
    if (dimType === 'custom' && !customField) { setError('Pick the accounting dimension.'); return; }
    if (dimType !== 'total' && !dimValue.trim()) { setError('Dimension value is required.'); return; }
    if (labelIsCustom && !customLabel.trim()) { setError('Custom label cannot be empty.'); return; }
    setBusy(true); setError('');
    try {
      const book = await api.createBudgetBook({
        report: report.name,
        fiscal_year: fy,
        dimension_type: dimType,
        dimension_value: dimType === 'total' ? null : dimValue.trim(),
        custom_dimension_fieldname: dimType === 'custom' ? customField : null,
        label: finalLabel,
        label_is_custom: labelIsCustom ? 1 : 0,
        owner_user: ownerUser.trim() || null,
      });
      onCreated(book.slug);
    } catch (e: any) {
      setError(e?.message || 'Create failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal" style={{ maxWidth: 560 }}>
        <div className="modal-head">
          <div className="strong">New budget book</div>
          <button onClick={onClose} aria-label="Close"><i className="ti ti-x" aria-hidden /></button>
        </div>
        <div className="modal-body">
          <div className="muted" style={{ marginBottom: 12, fontSize: 11 }}>
            A book holds the budget numbers for one dimension value. Pick the dimension you're budgeting against, then the value.
          </div>
          <div className="form-grid-2">
            <label><span className="flbl">Fiscal year</span>
              <select value={fy} onChange={(e) => setFy(parseInt(e.target.value))}>
                {FY_RANGE.map((y) => <option key={y} value={y}>FY{y}</option>)}
              </select>
            </label>
            <label><span className="flbl">Dimension</span>
              <select value={dimType} onChange={(e) => setDimType(e.target.value as DimensionType)}>
                <option value="cost_center">Cost Center</option>
                <option value="department">Department</option>
                <option value="project">Project</option>
                <option value="total">Total Company (no dimension)</option>
                <option value="custom">Custom (Accounting Dimension)</option>
              </select>
            </label>
          </div>
          {dimType === 'custom' && (
            <label><span className="flbl">Accounting dimension</span>
              <select value={customField} onChange={(e) => setCustomField(e.target.value)}>
                <option value="">{acctDims.length ? '— Pick a dimension —' : 'Loading…'}</option>
                {acctDims.map((d) => <option key={d.fieldname} value={d.fieldname}>{d.label}</option>)}
              </select>
            </label>
          )}
          {dimType !== 'total' && (
            <label><span className="flbl">
              {DIM_LABEL[dimType]} value
              {valueOptions.length > 0 && <span className="muted" style={{ fontSize: 9, marginLeft: 4 }}>({valueOptions.length})</span>}
            </span>
              <select value={dimValue} onChange={(e) => setDimValue(e.target.value)} disabled={valueLoading}>
                <option value="">
                  {valueLoading
                    ? 'Loading…'
                    : valueOptions.length === 0
                      ? `— No ${DIM_LABEL[dimType].toLowerCase()}s found in ERP —`
                      : `— Pick a ${DIM_LABEL[dimType].toLowerCase()} —`}
                </option>
                {valueOptions.map((o) => (
                  <option key={o.name} value={o.name}>{o.label}</option>
                ))}
              </select>
            </label>
          )}
          <label><span className="flbl">Owner (optional)</span>
            <input type="text" value={ownerUser} onChange={(e) => setOwnerUser(e.target.value)} placeholder="user@example.com" />
          </label>

          <div style={{ background: 'var(--surface-2, #f7f7f5)', border: '0.5px solid var(--border)', padding: '10px 12px', borderRadius: 'var(--radius-md)', marginTop: 12 }}>
            <div className="flbl">Label (auto-generated)</div>
            <div style={{ fontWeight: 500, fontSize: 13, marginTop: 4 }}>{finalLabel}</div>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 8, fontSize: 11, cursor: 'pointer' }}>
              <input type="checkbox" checked={labelIsCustom} onChange={(e) => { setLabelIsCustom(e.target.checked); if (e.target.checked && !customLabel) setCustomLabel(autoLabel); }} style={{ width: 'auto', height: 'auto' }} />
              Customize label
            </label>
            {labelIsCustom && (
              <input type="text" value={customLabel} onChange={(e) => setCustomLabel(e.target.value)} placeholder="e.g. FY26 · NISA-MSSP · Stretch with Beacon" style={{ marginTop: 6 }} />
            )}
          </div>

          {error && <div className="import-result is-error" style={{ marginTop: 10 }}>{error}</div>}
        </div>
        <div className="modal-foot">
          <button onClick={onClose}>Cancel</button>
          <button onClick={submit} disabled={busy} className="primary-btn">
            <i className="ti ti-check" aria-hidden /> {busy ? 'Creating…' : 'Create book'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Derive Budget dialog (v1.9.56) ──────────────────────────────────────
 * Lets the admin generate Insight Budget Cell documents from a basis year's
 * actuals, scaled by a default growth % with optional per-section and
 * per-source-row overrides. Honours the active book's dimension scope.
 *
 * Two-phase: Preview (computes what would be created) → Generate (commits).
 * The preview lets the admin sanity-check before writing N cells.
 */
function DeriveBudgetDialog({
  book, rows, onClose, onDone,
}: {
  book: BudgetBook;
  rows: any[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [basisOffset, setBasisOffset] = useState<number>((book as any).derive_basis_offset || 1);
  const [defaultGrowth, setDefaultGrowth] = useState<number>((book as any).derive_default_growth_pct ?? 10);
  // overrides: row_key → growth%. Section keys cascade.
  const [overrides, setOverrides] = useState<Record<string, number>>(() => {
    const raw = (book as any).derive_overrides_json;
    if (!raw) return {};
    try { return JSON.parse(raw); } catch { return {}; }
  });
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<any>(null);
  const [error, setError] = useState('');

  function setRowGrowth(key: string, val: string) {
    if (val === '' || val === '-') {
      // Treat blank as "remove the override" — let the cascade fall through.
      const next = { ...overrides };
      delete next[key];
      setOverrides(next);
      return;
    }
    const n = parseFloat(val);
    if (!isFinite(n)) return;
    setOverrides({ ...overrides, [key]: n });
  }

  async function doPreview() {
    setBusy(true); setError('');
    try {
      const r = await api.deriveBudgetCells({
        book: book.name,
        basis_offset: basisOffset,
        default_growth_pct: defaultGrowth,
        row_overrides: overrides,
        preview: 1,
      });
      setPreview(r);
    } catch (e: any) {
      setError(String(e?.message || 'Preview failed.'));
    } finally {
      setBusy(false);
    }
  }

  async function doGenerate() {
    setBusy(true); setError('');
    try {
      await api.deriveBudgetCells({
        book: book.name,
        basis_offset: basisOffset,
        default_growth_pct: defaultGrowth,
        row_overrides: overrides,
        preview: 0,
      });
      onDone();
    } catch (e: any) {
      setError(String(e?.message || 'Generate failed.'));
      setBusy(false);
    }
  }

  return (
    <div className="lh-modal-backdrop">
      <div className="lh-modal-card" style={{ maxWidth: 880, maxHeight: '85vh', overflow: 'auto' }}>
        <div className="lh-modal-title">
          <i className="ti ti-wand" aria-hidden /> Derive Budget from Prior Year
        </div>

        <div className="lh-modal-body">
          <div className="form-grid-3">
            <label>
              <span className="flbl">Basis Year</span>
              <select value={basisOffset} onChange={(e) => setBasisOffset(parseInt(e.target.value))}>
                <option value={1}>FY-1 (most recent prior year)</option>
                <option value={2}>FY-2</option>
                <option value={3}>FY-3</option>
                <option value={4}>FY-4</option>
                <option value={5}>FY-5 (oldest supported)</option>
              </select>
            </label>
            <label>
              <span className="flbl">Default Growth %</span>
              <input
                type="number"
                step="0.1"
                value={defaultGrowth}
                onChange={(e) => setDefaultGrowth(parseFloat(e.target.value || '0'))}
              />
            </label>
            <div /* spacer */ />
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 4, lineHeight: 1.55 }}>
            Reads actuals from <strong>FY{(book.fiscal_year || 0) - basisOffset}</strong> with this book's
            dimension scope, multiplies each value by <strong>(1 + growth ÷ 100)</strong>, and creates
            real Insight Budget Cell documents. Existing cells on matching (row, month) tuples are
            replaced; cells on rows the basis year had zero for are preserved.
          </div>

          <div style={{ marginTop: 16 }}>
            <div className="strong" style={{ fontSize: 12, marginBottom: 4 }}>
              Per-section / per-row growth overrides
            </div>
            <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
              Leave blank to inherit. Section overrides cascade to all source rows below them
              (until the next section). Source-row overrides win over section overrides.
            </div>
            <table className="rows-table" style={{ width: '100%', fontSize: 12 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left' }}>Row</th>
                  <th style={{ textAlign: 'right', width: 120 }}>Growth %</th>
                </tr>
              </thead>
              <tbody>
                {rows.filter((r) => r.kind === 'section' || r.kind === 'source').map((r) => (
                  <tr key={r.key}>
                    <td style={{
                      paddingLeft: r.kind === 'source' ? 22 : 6,
                      fontWeight: r.kind === 'section' ? 700 : 400,
                      color: r.kind === 'section' ? 'var(--text, #15141b)' : 'inherit',
                      textTransform: r.kind === 'section' ? 'uppercase' : 'none',
                      fontSize: r.kind === 'section' ? 11 : 12,
                    }}>{r.label}</td>
                    <td style={{ textAlign: 'right' }}>
                      <input
                        type="number"
                        step="0.1"
                        value={overrides[r.key] ?? ''}
                        placeholder="(inherit)"
                        onChange={(e) => setRowGrowth(r.key, e.target.value)}
                        style={{ width: 90, textAlign: 'right' }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {error && <div className="run-error" style={{ marginTop: 12 }}>{error}</div>}

          {preview && (
            <div style={{ marginTop: 16, padding: 12, background: '#f7f6f1', borderRadius: 6 }}>
              <div className="strong" style={{ marginBottom: 6 }}>Preview</div>
              <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
                Would create <strong>{preview.preview_cells?.length || 0} cells</strong> from
                FY{preview.basis_year} actuals. Effective growth per row:
              </div>
              <table className="rows-table" style={{ width: '100%', fontSize: 11 }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left' }}>Row</th>
                    <th style={{ textAlign: 'right' }}>Growth %</th>
                    <th style={{ textAlign: 'left' }}>{t('Source')}</th>
                  </tr>
                </thead>
                <tbody>
                  {(preview.growth_summary || []).map((g: any) => (
                    <tr key={g.row_key}>
                      <td style={{ paddingLeft: 12 }}>{g.label}</td>
                      <td style={{ textAlign: 'right' }}>{g.growth_pct.toFixed(1)}%</td>
                      <td>
                        <span className="muted" style={{ fontSize: 10 }}>
                          {g.source === 'row_override' ? 'row override'
                            : g.source === 'section_override' ? 'section override'
                            : 'book default'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="lh-modal-foot">
          <button onClick={onClose} className="lh-modal-cancel">Cancel</button>
          <button onClick={doPreview} disabled={busy} className="lh-modal-skip">
            <i className="ti ti-eye" aria-hidden /> {busy && !preview ? 'Computing…' : 'Preview'}
          </button>
          <button
            onClick={doGenerate}
            className="primary-btn"
            disabled={busy || !preview}
            title={!preview ? 'Click Preview first to see what will be created.' : 'Create the cells.'}
          >
            <i className="ti ti-wand" aria-hidden /> {busy && preview ? 'Generating…' : 'Generate cells'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Copy Budget dialog (v1.9.56) ────────────────────────────────────────
 * Duplicates the current book to a new fiscal year. Same scope, same cells,
 * new FY. Blocks if a book already exists for the target (report, FY, scope).
 */
function CopyBudgetDialog({
  book, onClose, onDone,
}: {
  book: BudgetBook;
  onClose: () => void;
  onDone: (newName: string) => void;
}) {
  const [targetFy, setTargetFy] = useState<number>((book.fiscal_year || 0) + 1);
  const [targetLabel, setTargetLabel] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function doCopy() {
    setBusy(true); setError('');
    try {
      const r = await api.copyBudgetBook(book.name, targetFy, targetLabel || undefined);
      onDone(r.name);
    } catch (e: any) {
      setError(String(e?.message || 'Copy failed.'));
      setBusy(false);
    }
  }

  return (
    <div className="lh-modal-backdrop">
      <div className="lh-modal-card">
        <div className="lh-modal-title">
          <i className="ti ti-copy" aria-hidden /> Copy Budget Book
        </div>
        <div className="lh-modal-body">
          <div className="muted" style={{ fontSize: 12, marginBottom: 12, lineHeight: 1.55 }}>
            Duplicates <strong>{book.label}</strong> to a new fiscal year. Same dimension scope,
            same cells. The new book starts in <em>draft</em> status — you can edit its cells freely.
          </div>
          <div className="form-grid-3">
            <label>
              <span className="flbl">Target FY</span>
              <input
                type="number"
                value={targetFy}
                onChange={(e) => setTargetFy(parseInt(e.target.value || '0'))}
              />
            </label>
            <label style={{ gridColumn: 'span 2' }}>
              <span className="flbl">New book label (optional)</span>
              <input
                value={targetLabel}
                placeholder={`${book.label} — FY${targetFy}`}
                onChange={(e) => setTargetLabel(e.target.value)}
              />
            </label>
          </div>
          {error && <div className="run-error" style={{ marginTop: 12 }}>{error}</div>}
        </div>
        <div className="lh-modal-foot">
          <button onClick={onClose} className="lh-modal-cancel">Cancel</button>
          <button onClick={doCopy} className="primary-btn" disabled={busy || !targetFy || targetFy === book.fiscal_year}>
            <i className="ti ti-copy" aria-hidden /> {busy ? 'Copying…' : 'Copy book'}
          </button>
        </div>
      </div>
    </div>
  );
}
