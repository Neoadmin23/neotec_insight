import { useEffect, useState } from 'react';
import { t } from '../../utils/i18n';
import type { DefinitionRow, ReportDefinition, RowStyle, TAccountSide } from '../../types';
import { api } from '../../utils/api';
import { ROW_TEXT_COLORS, ROW_BG_COLORS } from '../../utils/format';

interface Props { report: ReportDefinition; onChange: (r: ReportDefinition) => void; }

/** Compute, for every row, its 1-based position WITHIN its section.
 *  Section headers themselves get a section-ordinal; rows under a section
 *  count 1,2,3… and reset at the next section header. Returns a map
 *  rowKey -> { sectionLabel, posInSection }. */
function computeSectionNumbering(rows: DefinitionRow[]): Record<string, { section: string; pos: number }> {
  const out: Record<string, { section: string; pos: number }> = {};
  let currentSection = '(no section)';
  let sectionOrdinal = 0;
  let posInSection = 0;
  for (const r of rows) {
    if (r.kind === 'section') {
      sectionOrdinal += 1;
      currentSection = r.label || `Section ${sectionOrdinal}`;
      posInSection = 0;
      out[r.key] = { section: currentSection, pos: sectionOrdinal };
    } else {
      posInSection += 1;
      out[r.key] = { section: currentSection, pos: posInSection };
    }
  }
  return out;
}

export function RowsTab({ report, onChange }: Props) {
  const [rows, setRows] = useState<DefinitionRow[]>(report.definition.rows);
  const [selected, setSelected] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState('');
  // Insert-position dialog (v1.9.8).
  const [insertDlg, setInsertDlg] = useState<null | 'section' | 'source' | 'formula' | 'allocation'>(null);

  useEffect(() => { setRows(report.definition.rows); }, [report.definition.rows]);

  function update(rs: DefinitionRow[]) { setRows(rs); setDirty(true); }

  function makeRow(kind: 'section' | 'source' | 'formula' | 'allocation'): DefinitionRow {
    const prefix = kind === 'section' ? 'sec_' : kind === 'source' ? 'src_'
      : kind === 'allocation' ? 'alc_' : 'fml_';
    const key = prefix + Math.random().toString(36).slice(2, 7);
    return kind === 'source' ? { key, kind, label: 'New source', accounts: [], sign: 'normal' }
      : kind === 'formula' ? { key, kind, label: 'New formula', formula: '' }
      // v2.61.0 — an allocation row draws its monthly figures from an
      // Allocation Rule and behaves like a source row everywhere else, so
      // formulas can reference it by key with no special syntax.
      : kind === 'allocation' ? { key, kind, label: 'New allocation', allocation_rule: '',
                                  sign: 'normal', show_when: 'cost_center' }
      : { key, kind, label: 'New section' };
  }

  /** Insert a new row at a chosen index. afterKey = '' means top / start. */
  function insertRow(kind: 'section' | 'source' | 'formula' | 'allocation', afterKey: string) {
    const row = makeRow(kind);
    const next = [...rows];
    if (!afterKey) {
      next.unshift(row);
    } else {
      const idx = next.findIndex((r) => r.key === afterKey);
      next.splice(idx < 0 ? next.length : idx + 1, 0, row);
    }
    update(next);
    setSelected(row.key);
    setInsertDlg(null);
  }


  async function save() {
    setStatus('Saving…');
    try {
      const result = await api.saveReport({
        ...report,
        definition: { ...report.definition, rows },
      });
      onChange(result as ReportDefinition);
      setDirty(false);
      setStatus('Saved.');
      setTimeout(() => setStatus(''), 1500);
    } catch (e: any) {
      setStatus('Save failed: ' + e.message);
    }
  }

  const sel = rows.find((r) => r.key === selected) || null;
  const numbering = computeSectionNumbering(rows);

  return (
    <div>
      <div className="rows-add-group">
        <div className="rows-add-label">
          <i className="ti ti-plus" aria-hidden /> Add new row:
        </div>
        <button onClick={() => setInsertDlg('section')} className="add-row-btn add-row-section">
          <span className="plus-glyph">+</span> {t('Section')}
        </button>
        <button onClick={() => setInsertDlg('source')} className="add-row-btn add-row-source">
          <span className="plus-glyph">+</span> {t('Source')}
        </button>
        <button onClick={() => setInsertDlg('allocation')} className="add-row-btn add-row-allocation">
          <span className="add-row-plus">+</span> {t('Allocation')}
        </button>
        <button onClick={() => setInsertDlg('formula')} className="add-row-btn add-row-formula">
          <span className="plus-glyph">+</span> {t('Formula')}
        </button>
        <span style={{ flex: 1 }} />
        <button onClick={save} disabled={!dirty} className="primary-btn">
          <i className="ti ti-device-floppy" aria-hidden /> {t('Save changes')}
        </button>
        {status && <span className="run-meta" style={{ marginLeft: 8 }}>{status}</span>}
      </div>

      {/* v1.9.48 — Presentation format. Vertical (default) is the existing
       *  top-to-bottom statement. T-Account is the traditional two-column
       *  Trading and P&L layout. T-Account requires each row to declare a
       *  t_side classification; rows without one are omitted from that view. */}
      <div className="rows-format-bar">
        <span className="rows-format-lbl">
          <i className="ti ti-layout-2" aria-hidden /> {t('Presentation format')}
        </span>
        <label className="rows-format-radio">
          <input
            type="radio"
            name="pres-fmt"
            checked={(report.presentation_format || 'vertical') === 'vertical'}
            onChange={() => { onChange({ ...report, presentation_format: 'vertical' }); setDirty(true); }}
          />
          <span><strong>{t('Vertical')}</strong> — classical top-to-bottom statement (default).</span>
        </label>
        <label className="rows-format-radio">
          <input
            type="radio"
            name="pres-fmt"
            checked={report.presentation_format === 't_account'}
            onChange={() => { onChange({ ...report, presentation_format: 't_account' }); setDirty(true); }}
          />
          <span><strong>{t('T-Account')}</strong> — two-column Trading and Profit &amp; Loss layout. Set each row's <em>T-side</em> below.</span>
          {report.presentation_format !== 't_account' && (
            <button className="btn btn-xs btn-default" style={{ marginInlineStart: 12 }}
              title={t('Clone this report as a ready T-format Trading & P&L — rows auto-classified (Revenue → Trading credit, COGS → Trading debit, expenses → P&L debit, GP/NP balancers). Fully editable after.')}
              onClick={async () => {
                try {
                  const r = await api.createTaccountVariant(report.name!);
                  alert(`${t('Created')}: ${r.report_name}\n${r.rows_classified}/${r.rows_total} ${t('rows auto-classified. Open it from the report selector and fine-tune any T-side.')}`);
                  location.reload();
                } catch (e: any) { alert(String(e?.message || e)); }
              }}>
              ⚡ {t('Create T-format copy of this report')}
            </button>
          )}
        </label>
      </div>
      <div className="rows-add-help">
        <span><strong>{t('Section')}</strong> — a non-data header used to group rows visually.</span>
        <span><strong>{t('Source')}</strong> — a data row that pulls actuals from GL Entry. Bind accounts to it on the Account map tab.</span>
        <span><strong>{t('Formula')}</strong> — a computed row like <code className="fp">total_revenue - total_cogs</code>.</span>
      </div>
      <div className="rows-grid">
        <div className="card">
          <div className="row-list">
            {rows.map((r) => {
              const num = numbering[r.key];
              return (
              <div key={r.key} className={'row-card' + (selected === r.key ? ' is-selected' : '') + (r.kind === 'section' ? ' is-section' : '')} onClick={() => setSelected(r.key)}>
                <span className={'kind-badge kind-' + r.kind}>{r.kind}</span>
                <span>{r.label}</span>
                <span className="acc-list">
                  {r.kind === 'source' ? `${(r.accounts || []).length} accounts` :
                   r.kind === 'formula' ? <code className="fp">{r.formula}</code> : ''}
                </span>
                <span className="row-pos" title={r.kind === 'section' ? 'Section' : `${num?.section} · row ${num?.pos}`}>
                  {r.kind === 'section'
                    ? `§${num?.pos ?? ''}`
                    : `${num?.pos ?? ''}`}
                </span>
              </div>
              );
            })}
          </div>
        </div>
        <div className="card">
          {sel ? <RowEditor key={sel.key} row={sel} all={rows} report={report} onChange={(updated) => {
            const idx = rows.findIndex((r) => r.key === sel.key);
            const next = [...rows]; next[idx] = updated; update(next);
          }} onRenameKey={(newKey) => {
            // v2.31.0 — renaming a row key rewrites every formula that
            // references it, so nothing breaks. Keys are how formulas chain
            // (formula → formula works: rows evaluate top-to-bottom).
            const old = sel.key;
            if (!newKey || newKey === old || rows.some((r) => r.key === newKey)) return;
            const rx = new RegExp('\\b' + old.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\b', 'g');
            const next = rows.map((r) => {
              const nr: any = r.key === old ? { ...r, key: newKey } : { ...r };
              if (nr.kind === 'formula' && nr.formula) nr.formula = String(nr.formula).replace(rx, newKey);
              return nr;
            });
            update(next); setSelected(newKey);
          }} onDelete={() => { update(rows.filter((r) => r.key !== sel.key)); setSelected(null); }}
          onMove={(dir) => {
            const idx = rows.findIndex((r) => r.key === sel.key);
            const newIdx = idx + (dir === 'up' ? -1 : 1);
            if (newIdx < 0 || newIdx >= rows.length) return;
            const next = [...rows]; [next[idx], next[newIdx]] = [next[newIdx], next[idx]];
            update(next);
          }} /> : <div className="muted">{t('Select a row to edit.')}</div>}
        </div>
      </div>

      {insertDlg && (
        <InsertRowDialog
          kind={insertDlg}
          rows={rows}
          numbering={numbering}
          onCancel={() => setInsertDlg(null)}
          onInsert={(afterKey) => insertRow(insertDlg, afterKey)}
        />
      )}
    </div>
  );
}

/* ── Insert-row dialog (v1.9.8) ────────────────────────────────────────────
 * When adding a Source/Formula row, the user picks which section it goes into
 * and after which row. When adding a Section, they pick which existing section
 * it follows (or the top). New rows no longer always land at the bottom.
 */
function InsertRowDialog({
  kind, rows, numbering, onCancel, onInsert,
}: {
  kind: 'section' | 'source' | 'formula' | 'allocation';
  rows: DefinitionRow[];
  numbering: Record<string, { section: string; pos: number }>;
  onCancel: () => void;
  onInsert: (afterKey: string) => void;
}) {
  const sections = rows.filter((r) => r.kind === 'section');

  // For a Section: choose which section it follows ('' = very top).
  // For Source/Formula: choose a target section, then a row within it.
  const [sectionKey, setSectionKey] = useState<string>(sections[0]?.key || '');
  const [afterKey, setAfterKey] = useState<string>('');

  // Rows belonging to the chosen section (everything from that section header
  // up to — but not including — the next section header).
  function rowsInSection(secKey: string): DefinitionRow[] {
    const startIdx = rows.findIndex((r) => r.key === secKey);
    if (startIdx < 0) return [];
    const out: DefinitionRow[] = [];
    for (let i = startIdx + 1; i < rows.length; i++) {
      if (rows[i].kind === 'section') break;
      out.push(rows[i]);
    }
    return out;
  }

  const sectionRows = kind !== 'section' ? rowsInSection(sectionKey) : [];

  function confirm() {
    if (kind === 'section') {
      // afterKey here is the chosen section to follow ('' = top). But a new
      // section must land AFTER all rows of the preceding section, so we
      // resolve to the last row of that section (or the section header itself
      // if it has no rows).
      if (!afterKey) { onInsert(''); return; }
      const inSec = rowsInSection(afterKey);
      onInsert(inSec.length ? inSec[inSec.length - 1].key : afterKey);
    } else {
      // afterKey '' = start of the chosen section → insert right after the
      // section header itself.
      onInsert(afterKey || sectionKey);
    }
  }

  const kindLabel = kind === 'section' ? 'Section' : kind === 'source' ? 'Source row'
    : kind === 'allocation' ? 'Allocation row' : 'Formula row';

  return (
    <div className="ni-modal-backdrop" onClick={onCancel}>
      <div className="ni-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 460 }}>
        <h3 style={{ marginTop: 0 }}>Add {kindLabel}</h3>

        {kind === 'section' ? (
          <label className="ni-field">
            <span className="flbl">Place after section</span>
            <select value={afterKey} onChange={(e) => setAfterKey(e.target.value)}>
              <option value="">— At the very top —</option>
              {sections.map((s) => (
                <option key={s.key} value={s.key}>{s.label}</option>
              ))}
            </select>
          </label>
        ) : (
          <>
            <label className="ni-field">
              <span className="flbl">Into section</span>
              <select value={sectionKey} onChange={(e) => { setSectionKey(e.target.value); setAfterKey(''); }}>
                {sections.length === 0 && <option value="">— No sections yet —</option>}
                {sections.map((s) => (
                  <option key={s.key} value={s.key}>{s.label}</option>
                ))}
              </select>
            </label>
            <label className="ni-field">
              <span className="flbl">Place after</span>
              <select value={afterKey} onChange={(e) => setAfterKey(e.target.value)}>
                <option value="">— At the start of the section —</option>
                {sectionRows.map((r) => (
                  <option key={r.key} value={r.key}>
                    {numbering[r.key]?.pos}. {r.label}
                  </option>
                ))}
              </select>
            </label>
            {sections.length === 0 && (
              <p className="muted" style={{ fontSize: 11 }}>
                No sections exist yet — the row will be added at the top. Add a Section first to organise rows.
              </p>
            )}
          </>
        )}

        <div className="ni-modal-actions">
          <button onClick={onCancel}>Cancel</button>
          <button className="primary-btn" onClick={confirm}>Add here</button>
        </div>
      </div>
    </div>
  );
}

function RowEditor({ row, all, report, onChange, onRenameKey, onDelete, onMove }: {
  row: DefinitionRow;
  all: DefinitionRow[];
  report: ReportDefinition;
  onChange: (r: DefinitionRow) => void;
  onRenameKey: (newKey: string) => void;
  onDelete: () => void;
  onMove: (dir: 'up' | 'down') => void;
}) {
  const [boundAccounts, setBoundAccounts] = useState<any[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [refreshNonce, setRefreshNonce] = useState(0);

  // Effective flag for this source row: explicit flag, else label.
  const effectiveFlag = (row.flag || row.label || '').trim();

  useEffect(() => {
    if (row.kind !== 'source' || !report.name || !effectiveFlag) {
      setBoundAccounts([]);
      return;
    }
    let cancelled = false;
    api.listAccountsForFlag(report.name, effectiveFlag)
      .then((rows) => { if (!cancelled) setBoundAccounts(rows as any[]); })
      .catch(() => { if (!cancelled) setBoundAccounts([]); });
    return () => { cancelled = true; };
  }, [row.kind, report.name, effectiveFlag, refreshNonce]);

  async function unbindAccount(mappingName: string) {
    // Delete this specific binding by its mapping name — so removing an
    // account from THIS row leaves its bindings to other rows intact.
    if (!mappingName) return;
    try {
      await api.deleteAccountMapping(report.name!, mappingName);
      setRefreshNonce((n) => n + 1);
    } catch (e: any) {
      alert('Could not unbind: ' + (e?.message || 'unknown'));
    }
  }

  return (
    <div>
      <div className="form-grid-3">
        <label><span className="flbl">Label</span><input value={row.label} onChange={(e) => onChange({ ...row, label: e.target.value })} /></label>
        {row.kind !== 'section' && (
          <label><span className="flbl">Key</span>
            <input
              defaultValue={row.key}
              style={{ fontFamily: 'var(--font-mono, monospace)' }}
              onBlur={(e) => {
                const v = e.target.value.trim();
                if (!v || v === row.key) { e.target.value = row.key; return; }
                if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(v) || all.some((r) => r.key === v)) {
                  e.target.value = row.key;  // invalid or duplicate — revert
                  return;
                }
                onRenameKey(v);
              }}
              onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
            />
            <span className="muted" style={{ fontSize: 11 }}>
              Reference this row in other formulas by this key — e.g. sum several formula totals: <code className="fp">total_admin + total_ga + total_selling</code>. Renaming updates every formula that uses it.
            </span>
          </label>
        )}
        <label className="row-hidden-toggle" style={{ display:'inline-flex', alignItems:'center', gap:6, marginLeft:10, fontSize:12 }}><input type="checkbox" checked={!!row.hidden} onChange={(e) => onChange({ ...row, hidden: e.target.checked })} /> {t('Hide from display (still counts in totals)')}</label>
        <label><span className="flbl">Key</span><input value={row.key} disabled /></label>
        <label><span className="flbl">Kind</span><input value={row.kind} disabled /></label>
        {/* v2.76.0 — visibility on any row kind. The case it exists for: with
            credit-back on, an allocation leaves the company total unchanged, so
            a consolidated run prints "before allocation" and "after allocation"
            as the same figure. Correct, and it reads as a mistake.
            Leaving this alone changes nothing — the per-kind default in
            execution.py is what an untouched report already does. */}
        {row.kind !== 'allocation' && (
          <label><span className="flbl">Show</span>
            <select value={(row as any).show_when || 'always'}
              onChange={(e) => onChange({ ...row, show_when: e.target.value } as DefinitionRow)}>
              <option value="always">Always</option>
              <option value="cost_center">Only when a cost centre is selected — still counts in formulas</option>
              <option value="cost_center_exclude">Only when a cost centre is selected — and excluded from formulas</option>
            </select>
            <span className="muted" style={{ fontSize: 11 }}>
              {(row as any).show_when === 'cost_center_exclude'
                ? 'When hidden, this row contributes 0 to every formula that references it — totals change.'
                : (row as any).show_when === 'cost_center'
                  ? 'Hidden rows still feed formulas — only the display is suppressed, totals are unchanged.'
                  : 'Always shown, always counted.'}
            </span>
          </label>
        )}
      </div>

      {/* v1.9.48 — T-Account classification. Shown only when the report's
       *  presentation_format is 't_account'. Empty value means "exclude
       *  this row from the T-account view" (the renderer skips it). */}
      {report.presentation_format === 't_account' && (
        <TAccountRowEditor row={row} allRows={all} onChange={onChange} />
      )}
      {row.kind === 'allocation' && (
        <AllocationRowEditor row={row} onChange={onChange} />
      )}
      {row.kind === 'source' && (
        <div>
          <div className="form-grid-3">
            <label><span className="flbl">Flag</span><input value={row.flag || ''} onChange={(e) => onChange({ ...row, flag: e.target.value })} placeholder="Same as label by default" /></label>
            <label><span className="flbl">Sign</span>
              <select value={row.sign || 'normal'} onChange={(e) => onChange({ ...row, sign: e.target.value as any })}>
                <option value="normal">Normal</option>
                <option value="invert">Invert</option>
              </select>
            </label>
            <div />
          </div>

          <RowScopeEditor
            report={report}
            scope={row.dimension_scope || null}
            onChange={(scope) => onChange({ ...row, dimension_scope: scope })}
          />

          <div className="row-accounts">
            <div className="row-accounts-head">
              <div>
                <div className="strong" style={{ fontSize: 12 }}>Bound accounts ({boundAccounts.length})</div>
                <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                  Accounts whose flag is <code className="fp">{effectiveFlag || '—'}</code>. Adding a group binds every leaf under it.
                </div>
              </div>
              <button
                className="primary-btn"
                disabled={!effectiveFlag}
                onClick={() => setPickerOpen(true)}
              >
                <i className="ti ti-plus" aria-hidden /> Add accounts
              </button>
            </div>
            {boundAccounts.length === 0 ? (
              <div className="muted" style={{ padding: '10px 0', fontSize: 11 }}>
                No accounts bound to this row yet. Click <strong>Add accounts</strong> to pick from your chart of accounts. You can pick individual accounts or a group account (which expands to all leaves under it).
              </div>
            ) : (
              <div className="acc-chips">
                {boundAccounts.map((a) => (
                  <span className="acc-chip" key={a.name}>
                    <code className="fp">{a.account_code || ''}</code>
                    <span>{a.account_name || a.account}</span>
                    <button className="chip-x" aria-label="Unbind" onClick={() => unbindAccount(a.name)}>×</button>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
      {row.kind === 'formula' && (
        <div>
          <label><span className="flbl">{t('Formula')}</span>
            <input value={row.formula || ''} onChange={(e) => onChange({ ...row, formula: e.target.value })} style={{ fontFamily: 'var(--font-mono, monospace)' }} />
          </label>
          <div className="muted" style={{ marginTop: 6 }}>
            Available row keys: {all.filter((r) => r.kind !== 'section' && r.key !== row.key).map((r) => <code key={r.key} className="fp" style={{ marginRight: 4 }}>{r.key}</code>)}
          </div>
        </div>
      )}
      <RowStyleEditor
        style={row.style || null}
        onChange={(style) => onChange({ ...row, style })}
      />
      <div className="row-actions">
        <button onClick={onDelete}><i className="ti ti-trash" aria-hidden /> Delete row</button>
        <button onClick={() => onMove('up')} aria-label="Move up"><i className="ti ti-arrow-up" aria-hidden /></button>
        <button onClick={() => onMove('down')} aria-label="Move down"><i className="ti ti-arrow-down" aria-hidden /></button>
      </div>

      {pickerOpen && (
        <AccountPicker
          report={report}
          flag={effectiveFlag}
          currentRowKey={row.key}
          allRows={all}
          onClose={() => setPickerOpen(false)}
          onDone={() => { setPickerOpen(false); setRefreshNonce((n) => n + 1); }}
        />
      )}
    </div>
  );
}

function AccountPicker({ report, flag, currentRowKey, allRows, onClose, onDone }: {
  report: ReportDefinition;
  flag: string;
  currentRowKey: string;
  allRows: DefinitionRow[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [search, setSearch] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const [picked, setPicked] = useState<Map<string, any>>(new Map());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<any>(null);

  // Source rows this picker can bind to. Each row's flag is what mappings key
  // on (explicit flag, else label). The current row is pre-selected.
  const sourceRows = allRows
    .filter((r) => r.kind === 'source')
    .map((r) => ({ key: r.key, label: r.label, flag: (r.flag || r.label || '').trim() }))
    .filter((r) => r.flag);
  const [targetRowKeys, setTargetRowKeys] = useState<Set<string>>(new Set([currentRowKey]));

  function toggleTargetRow(key: string) {
    setTargetRowKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  // "Copy accounts from another row" — preloads `picked` with that row's
  // currently-bound accounts, so the user can re-bind them elsewhere fast.
  const [copyBusy, setCopyBusy] = useState(false);
  async function copyFromRow(srcFlag: string) {
    if (!srcFlag || !report.name) return;
    setCopyBusy(true); setError('');
    try {
      const accs = await api.listAccountsForFlag(report.name, srcFlag);
      const m = new Map(picked);
      for (const a of accs as any[]) {
        m.set(a.account, {
          name: a.account, account_number: a.account_code,
          account_name: a.account_name, is_group: 0,
        });
      }
      setPicked(m);
    } catch (e: any) {
      setError('Could not copy from row: ' + (e?.message || ''));
    } finally {
      setCopyBusy(false);
    }
  }

  useEffect(() => {
    if (!report.name) return;
    const t = setTimeout(async () => {
      try {
        const r = await api.listAvailableAccounts(report.name!, search, 60, 1);
        setResults(r as any[]);
      } catch (e: any) {
        setError(e?.message || 'Search failed.');
      }
    }, 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, report.name]);

  function togglePick(acc: any) {
    setPicked((prev) => {
      const next = new Map(prev);
      if (next.has(acc.name)) next.delete(acc.name);
      else next.set(acc.name, acc);
      return next;
    });
  }

  async function submit() {
    if (picked.size === 0) { setError('Pick at least one account.'); return; }
    setBusy(true); setError('');
    try {
      // Bind every picked account to every selected target row's flag.
      const targetFlags = sourceRows
        .filter((r) => targetRowKeys.has(r.key))
        .map((r) => r.flag);
      if (targetFlags.length === 0) {
        setError('Pick at least one target row.');
        setBusy(false);
        return;
      }
      const items: { account: string; flag: string }[] = [];
      for (const a of picked.values()) {
        for (const tf of targetFlags) {
          items.push({ account: a.name, flag: tf });
        }
      }
      const res = await api.bulkSetAccountFlags(report.name!, items);
      setResult({ ...res, target_count: targetFlags.length });
    } catch (e: any) {
      setError(e?.message || 'Save failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal" style={{ maxWidth: 760 }}>
        <div className="modal-head">
          <div className="strong">Add accounts to rows</div>
          <button onClick={onClose} aria-label="Close"><i className="ti ti-x" aria-hidden /></button>
        </div>
        <div className="modal-body">
          <div className="muted" style={{ marginBottom: 8 }}>
            Pick one or more accounts. Group accounts (folders in your chart) automatically expand to every leaf under them when saved.
          </div>
          <label><span className="flbl">Search</span>
            <input
              type="text"
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by code or name…"
            />
          </label>

          <div className="picker-copyfrom">
            <span className="flbl" style={{ marginRight: 6 }}>Copy accounts from</span>
            <select
              defaultValue=""
              disabled={copyBusy}
              onChange={(e) => { if (e.target.value) { copyFromRow(e.target.value); e.target.value = ''; } }}
            >
              <option value="">— pick a row to copy its accounts —</option>
              {sourceRows.map((r) => (
                <option key={r.key} value={r.flag}>{r.label}</option>
              ))}
            </select>
            {copyBusy && <span className="muted" style={{ fontSize: 11 }}>Loading…</span>}
            <span className="muted" style={{ fontSize: 10, marginLeft: 4 }}>
              adds that row's accounts to your selection below
            </span>
          </div>
          <div style={{
            maxHeight: 320, overflow: 'auto', marginTop: 8,
            border: '0.5px solid var(--border)', borderRadius: 'var(--radius-md)',
          }}>
            {results.length === 0 && (
              <div className="muted" style={{ padding: '12px 14px' }}>
                No matching accounts. {report.company ? <>Filtered to company <code className="fp">{report.company}</code>.</> : 'No company set — clear search to see all.'}
              </div>
            )}
            {results.map((acc) => {
              const isPicked = picked.has(acc.name);
              return (
                <div
                  key={acc.name}
                  onClick={() => togglePick(acc)}
                  style={{
                    padding: '6px 10px', cursor: 'pointer',
                    display: 'flex', gap: 8, alignItems: 'center',
                    borderBottom: '0.5px solid var(--border)',
                    background: isPicked ? 'var(--info-bg)' : 'transparent',
                    color: isPicked ? 'var(--info-text)' : 'inherit',
                  }}
                >
                  <input type="checkbox" checked={isPicked} readOnly style={{ width: 'auto', height: 'auto', margin: 0 }} />
                  <code className="fp" style={{ minWidth: 70 }}>{acc.account_number || '—'}</code>
                  <span style={{ flex: 1 }}>
                    {acc.account_name}
                    {acc.is_group ? <span className="pill" style={{ marginLeft: 6, background: '#faeeda', color: '#854f0b', fontSize: 9 }}>GROUP</span> : ''}
                  </span>
                  <span className="muted" style={{ fontSize: 10 }}>{acc.root_type || ''}</span>
                </div>
              );
            })}
          </div>

          <div className="picker-targets">
            <div className="flbl" style={{ marginBottom: 6 }}>
              Bind selected accounts to these rows
              <span className="muted" style={{ fontSize: 10, marginLeft: 6 }}>
                — each row keeps its own dimension scope
              </span>
            </div>
            <div className="target-row-chips">
              {sourceRows.map((r) => (
                <label
                  key={r.key}
                  className={'target-row-chip' + (targetRowKeys.has(r.key) ? ' on' : '')}
                >
                  <input
                    type="checkbox"
                    checked={targetRowKeys.has(r.key)}
                    onChange={() => toggleTargetRow(r.key)}
                  />
                  {r.label}
                </label>
              ))}
            </div>
            <div className="target-row-quick">
              <button type="button" onClick={() => setTargetRowKeys(new Set(sourceRows.map((r) => r.key)))}>
                Select all rows
              </button>
              <button type="button" onClick={() => setTargetRowKeys(new Set([currentRowKey]))}>
                Just this row
              </button>
            </div>
          </div>

          {picked.size > 0 && (
            <div className="muted" style={{ marginTop: 8, fontSize: 11 }}>
              {picked.size} account{picked.size === 1 ? '' : 's'} selected → will bind to{' '}
              <strong>{targetRowKeys.size}</strong> row{targetRowKeys.size === 1 ? '' : 's'}.
              {picked.size * targetRowKeys.size > 50 && (
                <span> ({picked.size * targetRowKeys.size} bindings)</span>
              )}
            </div>
          )}
          {error && <div className="import-result is-error" style={{ marginTop: 10 }}>{error}</div>}
          {result && (
            <div className="import-result" style={{ marginTop: 10 }}>
              <div><strong>Created:</strong> {result.created} mapping{result.created === 1 ? '' : 's'}{result.target_count > 1 ? ` across ${result.target_count} rows` : ''}</div>
              {result.expanded_total > 0 && <div><strong>Group expansion:</strong> {result.expanded_total} leaf account{result.expanded_total === 1 ? '' : 's'}</div>}
              {result.skipped > 0 && <div><strong>Skipped:</strong> {result.skipped} (already bound)</div>}
              {result.warnings?.length > 0 && (
                <details>
                  <summary>{result.warnings.length} warning{result.warnings.length === 1 ? '' : 's'}</summary>
                  <ul>{result.warnings.slice(0, 25).map((w: string, i: number) => <li key={i}>{w}</li>)}</ul>
                </details>
              )}
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button onClick={onClose}>Cancel</button>
          <button onClick={submit} disabled={picked.size === 0 || busy} className="primary-btn">
            {busy ? 'Saving…' : `Add ${picked.size || ''} mapping${picked.size === 1 ? '' : 's'}`}
          </button>
          {result && !result.error && <button onClick={onDone} className="primary-btn">Done</button>}
        </div>
      </div>
    </div>
  );
}

/* ── Row-level dimension scope editor (v1.9.6) ─────────────────────────────
 * Sets an optional scope on a source row: a dimension type plus one OR MORE
 * values. Every account mapped to this row is then filtered to
 * <dimension> IN (values) — so one row (e.g. "Revenue - MSSP/MDR") can club
 * several departments. Leaving the type as "Whole company" clears the scope.
 */
function RowScopeEditor({
  report, scope, onChange,
}: {
  report: ReportDefinition;
  scope: { dimension_type: string; dimension_values: string[] } | null;
  onChange: (s: { dimension_type: string; dimension_values: string[] } | null) => void;
}) {
  const [dimType, setDimType] = useState<string>(scope?.dimension_type || '');
  const [values, setValues] = useState<string[]>(scope?.dimension_values || []);
  // Available values for the chosen dimension type.
  const [valueOptions, setValueOptions] = useState<string[]>([]);
  const [customDims, setCustomDims] = useState<{ label: string; options: string[] }[]>([]);

  // Discover custom Accounting Dimensions once.
  useEffect(() => {
    api.listReportFilterOptions(report.company || undefined)
      .then((o: any) => setCustomDims(o?.dimensions || []))
      .catch(() => setCustomDims([]));
  }, [report.company]);

  const builtins = ['Department', 'Cost Center', 'Project'];
  const allTypes = [...builtins, ...customDims.map((d) => d.label).filter((t) => !builtins.includes(t))];

  // Load the value list whenever the type changes.
  useEffect(() => {
    if (!dimType) { setValueOptions([]); return; }
    const custom = customDims.find((d) => d.label === dimType);
    if (custom) { setValueOptions(custom.options || []); return; }
    let cancelled = false;
    const co = report.company || undefined;
    const loader =
      dimType === 'Department' ? api.listDepartments(co, '', 500)
      : dimType === 'Cost Center' ? api.listCostCenters(co, '', 500)
      : dimType === 'Project' ? api.listProjects(co, '', 500)
      : Promise.resolve([] as any[]);
    loader
      .then((rows: any[]) => { if (!cancelled) setValueOptions(rows.map((r) => r.name || r.label || r)); })
      .catch(() => { if (!cancelled) setValueOptions([]); });
    return () => { cancelled = true; };
  }, [dimType, customDims, report.company]);

  function commit(nextType: string, nextValues: string[]) {
    if (nextType && nextValues.length > 0) {
      onChange({ dimension_type: nextType, dimension_values: nextValues });
    } else {
      onChange(null);  // whole company
    }
  }

  function toggleValue(v: string) {
    const next = values.includes(v) ? values.filter((x) => x !== v) : [...values, v];
    setValues(next);
    commit(dimType, next);
  }

  return (
    <div className="row-scope">
      <div className="row-scope-head">
        <span className="flbl">Dimension scope</span>
        <select
          value={dimType}
          onChange={(e) => {
            const t = e.target.value;
            setDimType(t);
            setValues([]);
            commit(t, []);
          }}
        >
          <option value="">— Whole company —</option>
          {allTypes.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        {dimType && (
          <span className="row-scope-hint">
            pick one or more — row sums {dimType} IN selected values
          </span>
        )}
      </div>
      {dimType && (
        <div className="row-scope-values">
          {valueOptions.length === 0 && <span className="muted">No values found for {dimType}.</span>}
          <label className={'scope-val scope-blank' + (values.includes('__BLANK__') ? ' on' : '')}>
            <input
              type="checkbox"
              checked={values.includes('__BLANK__')}
              onChange={() => toggleValue('__BLANK__')}
            />
            ({'No ' + dimType} — blank / un-tagged)
          </label>
          {valueOptions.map((v) => (
            <label key={v} className={'scope-val' + (values.includes(v) ? ' on' : '')}>
              <input
                type="checkbox"
                checked={values.includes(v)}
                onChange={() => toggleValue(v)}
              />
              {v}
            </label>
          ))}
        </div>
      )}
      {dimType && values.length > 0 && (
        <div className="row-scope-summary">
          Scope: <strong>{dimType}</strong> IN {values.map((v) => v === '__BLANK__' ? '(blank)' : v).join(', ')}
        </div>
      )}
    </div>
  );
}

/* ─── Row styling editor (v1.9.18) ──────────────────────────────────────────
 * Per-row visual formatting — bold, italic, text/background colour, borders.
 * Fixed palettes keep reports visually consistent. Styling flows to screen
 * and to every export (Excel/PDF/Print) via the shared resolver in format.ts.
 */
function RowStyleEditor({ style, onChange }: {
  style: RowStyle | null;
  onChange: (s: RowStyle | null) => void;
}) {
  const s = style || {};
  function patch(p: Partial<RowStyle>) {
    const next: RowStyle = { ...s, ...p };
    // Collapse to null when nothing is set — keeps the definition clean.
    const empty = !next.bold && !next.italic
      && (!next.text_color || next.text_color === 'default')
      && (!next.bg_color || next.bg_color === 'none')
      && !next.border_top && !next.border_bottom;
    onChange(empty ? null : next);
  }

  const textSwatch = (token: string) => ROW_TEXT_COLORS[token]?.css || 'inherit';
  const bgSwatch = (token: string) => ROW_BG_COLORS[token]?.css || 'transparent';

  return (
    <div className="row-style-editor">
      <div className="flbl" style={{ marginBottom: 6 }}>
        Row styling
        <span className="muted" style={{ fontSize: 10, marginLeft: 6 }}>
          — applies on screen and in Excel / PDF / Print
        </span>
      </div>
      <div className="row-style-controls">
        <button
          type="button"
          className={'rs-toggle' + (s.bold ? ' on' : '')}
          onClick={() => patch({ bold: !s.bold })}
          title="Bold"
        ><strong>B</strong></button>
        <button
          type="button"
          className={'rs-toggle' + (s.italic ? ' on' : '')}
          onClick={() => patch({ italic: !s.italic })}
          title="Italic"
        ><em>I</em></button>

        <span className="rs-divider" />

        <span className="rs-label">Text</span>
        {Object.keys(ROW_TEXT_COLORS).map((token) => (
          <button
            key={token}
            type="button"
            className={'rs-swatch' + ((s.text_color || 'default') === token ? ' on' : '')}
            style={{ color: textSwatch(token) }}
            onClick={() => patch({ text_color: token })}
            title={'Text: ' + token}
          >A</button>
        ))}

        <span className="rs-divider" />

        <span className="rs-label">Fill</span>
        {Object.keys(ROW_BG_COLORS).map((token) => (
          <button
            key={token}
            type="button"
            className={'rs-swatch rs-swatch-bg' + ((s.bg_color || 'none') === token ? ' on' : '')}
            style={{
              background: bgSwatch(token),
              ...(token === 'none' ? { backgroundImage: 'linear-gradient(45deg,#ccc 25%,transparent 25%,transparent 75%,#ccc 75%)', backgroundSize: '6px 6px' } : {}),
            }}
            onClick={() => patch({ bg_color: token })}
            title={'Fill: ' + token}
          />
        ))}

        <span className="rs-divider" />

        <button
          type="button"
          className={'rs-toggle' + (s.border_top ? ' on' : '')}
          onClick={() => patch({ border_top: !s.border_top })}
          title="Border on top"
        >▔</button>
        <button
          type="button"
          className={'rs-toggle' + (s.border_bottom ? ' on' : '')}
          onClick={() => patch({ border_bottom: !s.border_bottom })}
          title="Border on bottom"
        >▁</button>

        {style && (
          <>
            <span className="rs-divider" />
            <button type="button" className="rs-clear" onClick={() => onChange(null)} title="Clear styling">
              Clear
            </button>
          </>
        )}
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────────────────────────────────
 * T-Account row classification editor (v1.9.48)
 *
 * Lets an admin classify each row for the T-Account presentation. The
 * classification controls which section and side of the T-account the row
 * appears on:
 *
 *   debit_trading   — Trading section, left side (Opening Stock, Purchases, etc.)
 *   credit_trading  — Trading section, right side (Sales, Closing Stock)
 *   gp_balancer     — Optional explicit Gross Profit row (engine source of truth)
 *   debit_pl        — P&L section, left side (indirect expenses)
 *   credit_pl       — P&L section, right side (other incomes, b/d carry)
 *   np_balancer     — Optional explicit Net Profit row
 *
 * The "Less" sub-line lets a credit-side row (e.g. Sales) be presented as
 * its gross figure with a "Less: Returns" deduction inline.
 */
const T_SIDE_OPTIONS: Array<{ value: '' | TAccountSide; label: string; group: string }> = [
  { value: '',                label: '— Exclude from T-Account —',     group: '' },
  { value: 'debit_trading',   label: 'Trading · Debit (left)',         group: 'Trading' },
  { value: 'credit_trading',  label: 'Trading · Credit (right)',       group: 'Trading' },
  { value: 'gp_balancer',     label: 'Trading · Gross Profit balancer', group: 'Trading' },
  { value: 'debit_pl',        label: 'P&L · Debit (left)',             group: 'P&L' },
  { value: 'credit_pl',       label: 'P&L · Credit (right)',           group: 'P&L' },
  { value: 'np_balancer',     label: 'P&L · Net Profit balancer',      group: 'P&L' },
];


/** v2.61.0 — picker for an allocation row.
 *
 *  The row shows one cost centre's share of a pool. Which share depends on
 *  the cost centre the report is run with: select GRC and it shows GRC's
 *  figure, select none and it shows the whole pool being spread. Nothing to
 *  configure for that — it follows the report's own filter.
 */
function AllocationRowEditor({ row, onChange }: {
  row: DefinitionRow; onChange: (r: DefinitionRow) => void;
}) {
  const [rules, setRules] = useState<{ name: string; title: string; driver_label?: string;
    pool_report?: string; pool_row_key?: string; pool_cost_center?: string }[]>([]);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.allocationRules(null)
      .then((rs: any[]) => setRules(rs || []))
      .catch((e: any) => setErr(e?.message || 'Could not load allocation rules.'));
  }, []);

  const sel = rules.find((r) => r.name === (row as any).allocation_rule);

  return (
    <div className="row-sub">
      <label>
        <span className="flbl">Allocation rule</span>
        <select value={(row as any).allocation_rule || ''}
          onChange={(e) => onChange({ ...row, allocation_rule: e.target.value } as DefinitionRow)}>
          <option value="">— select —</option>
          {rules.map((r) => <option key={r.name} value={r.name}>{r.title}</option>)}
        </select>
        {/* An empty dropdown with no explanation reads as broken rather than
            unconfigured, and the save then fails with a row key the user never
            chose and cannot see. Say it here, where it can be acted on. */}
        {rules.length === 0 && !err && (
          <span className="row-warn">
            No allocation rules exist yet. Create an <strong>Insight Allocation Rule</strong> in the
            desk first — one per pool — then pick it here. This row cannot be saved until it names a rule.
          </span>
        )}
        {rules.length > 0 && !(row as any).allocation_rule && (
          <span className="row-warn">Pick a rule, or delete this row — the report will not save otherwise.</span>
        )}
      </label>
      <label>
        <span className="flbl">Sign</span>
        <select value={(row as any).sign || 'normal'}
          onChange={(e) => onChange({ ...row, sign: e.target.value } as DefinitionRow)}>
          <option value="normal">Normal (adds to cost)</option>
          <option value="invert">Invert</option>
        </select>
      </label>
      <label>
        <span className="flbl">Show</span>
        <select value={(row as any).show_when || 'cost_center'}
          onChange={(e) => onChange({ ...row, show_when: e.target.value } as DefinitionRow)}>
          <option value="cost_center">Only when a cost centre is selected — still counts in formulas</option>
          <option value="cost_center_exclude">Only when a cost centre is selected — and excluded from formulas</option>
          <option value="always">Always</option>
        </select>
      </label>
      <span className="row-hint">
        Run consolidated, this row would show the whole pool next to expenses that
        already contain it. Hidden by default unless one cost centre is selected —
        the value still reaches formulas either way, only the display is suppressed.
      </span>
      {err && <span className="row-hint" style={{ color: 'var(--neg)' }}>{err}</span>}
      {!rules.length && !err && (
        <span className="row-hint">
          No allocation rules defined yet. Create one under Insight Allocation Rule in the desk.
        </span>
      )}
      {sel && (
        <span className="row-hint">
          Pool: <code className="fp">{sel.pool_report} · {sel.pool_row_key} · {sel.pool_cost_center}</code>
          {' · '}spread on {sel.driver_label || 'driver'}.
          {' '}Run the report with a cost centre selected to see that centre&apos;s share;
          with none, the row shows the whole pool.
        </span>
      )}
    </div>
  );
}

function TAccountRowEditor({ row, allRows, onChange }: {
  row: DefinitionRow;
  allRows: DefinitionRow[];
  onChange: (r: DefinitionRow) => void;
}) {
  const side = (row.t_side || '') as ('' | TAccountSide);
  // "Less" deduction only makes sense for credit/debit rows that report a NET
  // figure with a deduction shown above. Most commonly: Sales (net) less
  // Sales Return; Purchases (net) less Purchases Return. We allow it on any
  // *_trading or *_pl row — the renderer ignores it on balancer rows.
  const showLess = side === 'credit_trading' || side === 'debit_trading'
                || side === 'credit_pl' || side === 'debit_pl';

  // Eligible "Less" target rows = other rows that exist as deductions. We
  // don't restrict by kind — section rows are nonsense here but the user
  // controls their own data. Show all rows except this one.
  const eligibleLessRows = allRows.filter((r) => r.key !== row.key);

  return (
    <div className="ta-rowedit">
      <div className="ta-rowedit-head">
        <i className="ti ti-layout-distribute-horizontal" aria-hidden />
        <span>T-Account placement</span>
      </div>
      <div className="form-grid-3">
        <label>
          <span className="flbl">T-side</span>
          <select
            value={side}
            onChange={(e) => onChange({ ...row, t_side: (e.target.value || null) as TAccountSide | null })}
          >
            {T_SIDE_OPTIONS.map((o) => (
              <option key={o.value || 'none'} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        {showLess ? (
          <>
            <label>
              <span className="flbl">Less: (deduction row)</span>
              <select
                value={row.less_row_key || ''}
                onChange={(e) => onChange({ ...row, less_row_key: e.target.value || null })}
              >
                <option value="">— No deduction —</option>
                {eligibleLessRows.map((r) => (
                  <option key={r.key} value={r.key}>{r.label}</option>
                ))}
              </select>
            </label>
            <label>
              <span className="flbl">Less label (optional)</span>
              <input
                value={row.less_label || ''}
                placeholder={row.less_row_key ? 'Defaults to deduction row label' : '(disabled — no deduction)'}
                disabled={!row.less_row_key}
                onChange={(e) => onChange({ ...row, less_label: e.target.value || null })}
              />
            </label>
          </>
        ) : (
          <>
            <div />
            <div />
          </>
        )}
      </div>
      {side && (
        <div className="ta-rowedit-hint">
          {side === 'debit_trading' && 'Appears on the LEFT side of the Trading section (e.g. Opening Stock, Purchases, direct expenses).'}
          {side === 'credit_trading' && 'Appears on the RIGHT side of the Trading section (e.g. Sales, Closing Stock).'}
          {side === 'gp_balancer' && 'Used as the explicit Gross Profit figure when present. If omitted, the view derives it from Trading credit − Trading debit.'}
          {side === 'debit_pl' && 'Appears on the LEFT side of the Profit & Loss section (indirect expenses).'}
          {side === 'credit_pl' && 'Appears on the RIGHT side of the Profit & Loss section (other incomes; Gross Profit b/d is auto-inserted).'}
          {side === 'np_balancer' && 'Used as the explicit Net Profit figure when present. If omitted, the view derives it.'}
          {row.less_row_key && (
            <> The deduction row will render as a <em>Less: …</em> sub-line below the gross figure.</>
          )}
        </div>
      )}
    </div>
  );
}
