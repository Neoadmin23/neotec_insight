import { useEffect, useState } from 'react';
import { t } from '../../utils/i18n';
import { api, fileToBase64 } from '../../utils/api';
import { exportMapXlsx } from '../../utils/export';
import type { AccountMapping, MappingRule, ReportDefinition } from '../../types';

interface Props {
  report: ReportDefinition;
}

export function MapTab({ report }: Props) {
  const [mappings, setMappings] = useState<AccountMapping[]>([]);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'all' | 'unmapped' | 'mapped' | 'orphan'>('all');
  const [rules, setRules] = useState<MappingRule[]>([]);
  const [importDlg, setImportDlg] = useState(false);
  const [structureDlg, setStructureDlg] = useState(false);
  const [addDlg, setAddDlg] = useState(false);
  const [status, setStatus] = useState<string>('');

  async function reload() {
    if (!report.name) return;
    const [m, r] = await Promise.all([api.listAccountMappings(report.name), api.listMappingRules()]);
    setMappings(m as AccountMapping[]);
    setRules(r as MappingRule[]);
  }

  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [report.name]);

  const flagsInRows = new Set(report.definition.rows.filter((r) => r.kind === 'source').map((r) => r.flag || r.label));
  const allFlagOptions = Array.from(new Set([...flagsInRows, ...mappings.map((m) => m.flag)].filter(Boolean))).sort();

  let visible = mappings.slice();
  if (search.trim()) {
    const s = search.toLowerCase();
    visible = visible.filter((m) =>
      (m.account_code || '').toLowerCase().includes(s) ||
      (m.account_name || '').toLowerCase().includes(s) ||
      (m.flag || '').toLowerCase().includes(s),
    );
  }
  if (filter === 'unmapped') visible = visible.filter((m) => !m.flag);
  if (filter === 'mapped') visible = visible.filter((m) => !!m.flag);
  if (filter === 'orphan') visible = visible.filter((m) => m.flag && !flagsInRows.has(m.flag));

  const total = mappings.length;
  const mapped = mappings.filter((m) => !!m.flag).length;
  const unmapped = total - mapped;
  const orphan = mappings.filter((m) => m.flag && !flagsInRows.has(m.flag)).length;

  // v1.9.9 — multi-flag: add one more flag binding for an account without
  // disturbing its existing bindings.
  async function addFlagBinding(account: string, flag: string, isGroup: number) {
    if (!flag) return;
    setStatus('Adding flag…');
    try {
      await api.saveAccountMapping({
        report: report.name!, account, flag,
        is_group_binding: isGroup,
      });
      await reload();
      setStatus('');
    } catch (e: any) { setStatus('Could not add flag: ' + (e?.message || '')); }
  }

  // Remove one specific binding (one account-to-flag row) by its mapping name.
  async function removeBinding(mappingName: string) {
    setStatus('Removing…');
    try {
      await api.deleteAccountMapping(report.name!, mappingName);
      await reload();
      setStatus('');
    } catch (e: any) { setStatus('Could not remove: ' + (e?.message || '')); }
  }

  // Group the (filtered) mapping rows by account, so one account shows once
  // with all its flag bindings as chips — even when bound to several rows.
  type AcctGroup = {
    account: string; account_code?: string; account_name?: string;
    is_group_binding?: number;
    bindings: AccountMapping[];   // rows that have a flag
    unmappedRow?: AccountMapping; // the placeholder row when account has no flag
  };
  const accountGroups: AcctGroup[] = (() => {
    const byAcct = new Map<string, AcctGroup>();
    for (const m of visible) {
      let g = byAcct.get(m.account);
      if (!g) {
        g = {
          account: m.account, account_code: m.account_code,
          account_name: m.account_name, is_group_binding: m.is_group_binding,
          bindings: [],
        };
        byAcct.set(m.account, g);
      }
      if (m.flag) g.bindings.push(m);
      else g.unmappedRow = m;
      if (m.is_group_binding) g.is_group_binding = 1;
    }
    return Array.from(byAcct.values());
  })();

  async function autosuggest() {
    setStatus('Auto-suggesting…');
    const res = (await api.autosuggestMappings(report.name!)) as { created: number };
    setStatus(`Auto-suggested ${res.created} mapping${res.created === 1 ? '' : 's'}.`);
    await reload();
  }

  async function pasteRows() {
    const raw = prompt("Paste rows in 'code<TAB>flag' format (one per line):");
    if (!raw) return;
    setStatus('Applying paste…');
    const lines = raw.split(/\r?\n/);
    let n = 0;
    for (const line of lines) {
      const [code, flag] = line.split(/\t|,/).map((s) => (s || '').trim());
      if (!code || !flag) continue;
      const match = mappings.find((m) => (m.account_code || '') === code);
      if (!match) continue;
      await api.setAccountFlag(report.name!, match.account, flag);
      n++;
    }
    setStatus(`Applied ${n} mapping${n === 1 ? '' : 's'} from paste.`);
    await reload();
  }

  return (
    <div>
      <div className="map-toolbar">
        <div className="map-toolbar-left">
          <div className="strong">Account → P&amp;L row mapping</div>
          <div className="muted">Mirrors your Excel <code className="fp">MAP</code> sheet (Chart of account → P&amp;L Classification).</div>
        </div>
        <div className="map-toolbar-right">
          <button onClick={() => setAddDlg(true)} className="primary-btn map-add-account-btn" title="Add an account to this report and assign a P&L flag">
            <i className="ti ti-plus" aria-hidden /> Add account → flag
          </button>
          <button onClick={() => setImportDlg(true)}><i className="ti ti-file-import" aria-hidden /> Import Map sheet</button>
          <button onClick={() => setStructureDlg(true)}><i className="ti ti-file-import" aria-hidden /> Import report structure</button>
          <button onClick={pasteRows}><i className="ti ti-clipboard" aria-hidden /> Paste rows</button>
          <button onClick={autosuggest}><i className="ti ti-wand" aria-hidden /> Auto-suggest</button>
          <button onClick={() => exportMapXlsx(mappings)}><i className="ti ti-download" aria-hidden /> Export</button>
        </div>
      </div>
      <div className="map-stats">
        <div className="map-stat"><div className="map-stat-l">{t('Total')}</div><div className="map-stat-v">{total}</div></div>
        <div className="map-stat"><div className="map-stat-l">Mapped</div><div className="map-stat-v" style={{ color: 'var(--color-text-success)' }}>{mapped}</div></div>
        <div className="map-stat"><div className="map-stat-l">Unmapped</div><div className="map-stat-v" style={{ color: 'var(--color-text-warning)' }}>{unmapped}</div></div>
        <div className="map-stat"><div className="map-stat-l">Orphan flags</div><div className="map-stat-v" style={{ color: 'var(--color-text-danger)' }}>{orphan}</div></div>
      </div>
      <div className="map-search-row">
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search by code, name, or flag…" />
        <select value={filter} onChange={(e) => setFilter(e.target.value as any)}>
          <option value="all">All accounts</option>
          <option value="unmapped">Unmapped only</option>
          <option value="mapped">Mapped only</option>
          <option value="orphan">Orphan only</option>
        </select>
      </div>
      {status && <div className="run-meta" style={{ margin: '8px 0' }}>{status}</div>}
      <div className="card">
        <div className="map-scroll">
          <table className="map-table">
            <thead>
              <tr>
                <th style={{ width: 110 }}>Code</th>
                <th>{t('Account')}</th>
                <th style={{ width: '42%' }}>P&amp;L flag</th>
              </tr>
            </thead>
            <tbody>
              {accountGroups.map((g) => {
                const hasAnyFlag = g.bindings.length > 0;
                const trCls = !hasAnyFlag ? 'unmapped' : '';
                return (
                  <tr key={g.account} className={trCls}>
                    <td className="code">
                      {g.account_code || '—'}
                      {g.is_group_binding ? (
                        <span
                          title="Group binding — every leaf under this group counts toward the flag, including leaves added later."
                          style={{
                            marginLeft: 6, fontSize: 9, padding: '1px 6px', borderRadius: 8,
                            background: 'var(--info-bg, #e6f1fb)', color: 'var(--info, #0c447c)',
                            display: 'inline-flex', alignItems: 'center', gap: 3, verticalAlign: 'middle',
                          }}
                        >
                          <i className="ti ti-folder" aria-hidden /> group
                        </span>
                      ) : null}
                    </td>
                    <td>{g.account_name || g.account}</td>
                    <td className="flag">
                      <div className="flag-chips">
                        {g.bindings.map((b) => {
                          const isOrphan = b.flag && !flagsInRows.has(b.flag);
                          return (
                            <span key={b.name} className={'flag-chip' + (isOrphan ? ' orphan' : '')}>
                              {b.flag}
                              <button
                                className="flag-chip-x"
                                title="Remove this flag binding"
                                onClick={() => removeBinding(b.name)}
                              >×</button>
                            </span>
                          );
                        })}
                        <select
                          className="flag-add"
                          value=""
                          onChange={(e) => {
                            const f = e.target.value;
                            if (f) addFlagBinding(g.account, f, g.is_group_binding ? 1 : 0);
                          }}
                        >
                          <option value="">
                            {hasAnyFlag ? '+ add flag…' : '— assign flag —'}
                          </option>
                          {allFlagOptions
                            .filter((f) => !g.bindings.some((b) => b.flag === f))
                            .map((f) => <option key={f} value={f}>{f}</option>)}
                        </select>
                      </div>
                      {g.bindings.length > 1 && (
                        <div className="multi-flag-note" title="This account feeds more than one report row. Make sure the rows' dimension scopes don't overlap, or its amount will be counted more than once.">
                          <i className="ti ti-info-circle" aria-hidden /> on {g.bindings.length} rows — check scopes don't overlap
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
              {accountGroups.length === 0 && mappings.length === 0 && (
                <tr><td colSpan={3} className="empty">
                  <div style={{ padding: '14px 16px', textAlign: 'left' }}>
                    <div style={{ fontWeight: 500, marginBottom: 6 }}>No accounts mapped yet.</div>
                    <div className="muted" style={{ marginBottom: 10 }}>
                      Three ways to add accounts to this report:
                    </div>
                    <ul style={{ paddingLeft: 18, margin: '0 0 12px', lineHeight: 1.7 }}>
                      <li><strong>Add account</strong> — pick one account at a time from your chart and assign a flag.</li>
                      <li><strong>Import Map sheet</strong> — upload your Excel template; the importer wires every account in one shot.</li>
                      <li><strong>Auto-suggest</strong> — uses the code-prefix rules below to bulk-match unmapped accounts.</li>
                    </ul>
                    <button className="primary-btn" onClick={() => setAddDlg(true)}><i className="ti ti-plus" aria-hidden /> Add account manually</button>
                  </div>
                </td></tr>
              )}
              {accountGroups.length === 0 && mappings.length > 0 && (
                <tr><td colSpan={3} className="empty">No accounts match the current filter.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card rule-card">
        <div className="rule-head">
          <div className="strong">Mapping rules by code prefix</div>
          <div className="muted">Auto-suggest uses these. Longer prefixes win; ties broken by priority.</div>
        </div>
        <RulesEditor rules={rules} onChange={reload} />
      </div>

      {addDlg && <AddAccountDialog report={report} flagsInRows={Array.from(flagsInRows)} existingFlagOptions={allFlagOptions} onClose={() => setAddDlg(false)} onDone={() => { setAddDlg(false); reload(); }} />}
      {importDlg && <MapImportDialog report={report.name!} onClose={() => setImportDlg(false)} onDone={() => { setImportDlg(false); reload(); }} />}
      {structureDlg && <StructureImportDialog onClose={() => setStructureDlg(false)} onDone={() => setStructureDlg(false)} />}
    </div>
  );
}

function RulesEditor({ rules, onChange }: { rules: MappingRule[]; onChange: () => void }) {
  const [draft, setDraft] = useState<MappingRule[]>(rules);
  useEffect(() => setDraft(rules), [rules]);

  async function save(r: MappingRule) {
    await api.saveMappingRule(r);
    onChange();
  }
  async function del(name: string) {
    await api.deleteMappingRule(name);
    onChange();
  }
  async function add() {
    await api.saveMappingRule({ prefix: '', flag: '', priority: 100, is_active: 1 });
    onChange();
  }

  return (
    <div>
      <div className="rule-list">
        {draft.map((r, i) => (
          <div key={r.name} className="rule-row">
            <input value={r.prefix} onChange={(e) => { const d = [...draft]; d[i] = { ...r, prefix: e.target.value }; setDraft(d); }} onBlur={() => save(draft[i])} placeholder="Prefix" />
            <input value={r.flag} onChange={(e) => { const d = [...draft]; d[i] = { ...r, flag: e.target.value }; setDraft(d); }} onBlur={() => save(draft[i])} placeholder="Flag" />
            <input type="number" value={r.priority} style={{ width: 70 }} onChange={(e) => { const d = [...draft]; d[i] = { ...r, priority: parseInt(e.target.value) || 100 }; setDraft(d); }} onBlur={() => save(draft[i])} />
            <button onClick={() => del(r.name)} aria-label="Delete rule"><i className="ti ti-x" aria-hidden /></button>
          </div>
        ))}
      </div>
      <button onClick={add} style={{ marginTop: 8 }}><i className="ti ti-plus" aria-hidden /> Add rule</button>
    </div>
  );
}

function MapImportDialog({ report, onClose, onDone }: { report: string; onClose: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [sheetName, setSheetName] = useState('MAP');
  const [accountCol, setAccountCol] = useState(2);
  const [flagCol, setFlagCol] = useState(3);
  const [headerRows, setHeaderRows] = useState(4);
  const [replace, setReplace] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);

  async function submit() {
    if (!file) return;
    setBusy(true);
    try {
      const b64 = await fileToBase64(file);
      const res = await api.importMapSheet(report, b64, { sheet_name: sheetName, account_col: accountCol, flag_col: flagCol, header_rows: headerRows, replace: replace ? 1 : 0 });
      setResult(res);
    } catch (e: any) {
      setResult({ error: e.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal">
        <div className="modal-head">
          <div className="strong">Import Map sheet</div>
          <button onClick={onClose} aria-label="Close"><i className="ti ti-x" aria-hidden /></button>
        </div>
        <div className="modal-body">
          <div className="form-row"><label><span className="flbl">.xlsx file</span><input type="file" accept=".xlsx" onChange={(e) => setFile(e.target.files?.[0] || null)} /></label></div>
          <div className="form-grid">
            <label><span className="flbl">Sheet name</span><input value={sheetName} onChange={(e) => setSheetName(e.target.value)} /></label>
            <label><span className="flbl">Header rows</span><input type="number" value={headerRows} onChange={(e) => setHeaderRows(parseInt(e.target.value) || 4)} /></label>
            <label><span className="flbl">Account column (1-based)</span><input type="number" value={accountCol} onChange={(e) => setAccountCol(parseInt(e.target.value) || 2)} /></label>
            <label><span className="flbl">Flag column (1-based)</span><input type="number" value={flagCol} onChange={(e) => setFlagCol(parseInt(e.target.value) || 3)} /></label>
          </div>
          <label className="chk" style={{ marginTop: 8 }}><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /> Replace existing mappings for this report</label>
          <div className="muted" style={{ marginTop: 8 }}>
            The importer parses each row in the form <code className="fp">CODE - Name English Name Arabic - ENTITY</code>, finds the matching Frappe Account by <code className="fp">account_number</code>, and writes the flag to <code className="fp">Account Flag Mapping</code>.
          </div>

          {result && (
            <div className={'import-result ' + (result.error ? 'is-error' : '')}>
              {result.error ? `Error: ${result.error}` : (
                <>
                  <div><strong>Sheet used:</strong> {result.sheet_used}</div>
                  <div><strong>Created:</strong> {result.created} mapping{result.created === 1 ? '' : 's'}</div>
                  <div><strong>Skipped (no flag):</strong> {result.skipped_no_flag}</div>
                  <div><strong>Skipped (no matching Account):</strong> {result.skipped_no_match}</div>
                  {result.warnings?.length > 0 && (
                    <details><summary>{result.warnings.length} warning{result.warnings.length === 1 ? '' : 's'}</summary>
                      <ul>{result.warnings.slice(0, 25).map((w: string, i: number) => <li key={i}>{w}</li>)}</ul>
                    </details>
                  )}
                </>
              )}
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button onClick={onClose}>Cancel</button>
          <button onClick={submit} disabled={!file || busy} className="primary-btn">{busy ? 'Importing…' : 'Run import'}</button>
          {result && !result.error && <button onClick={onDone} className="primary-btn">Done</button>}
        </div>
      </div>
    </div>
  );
}

function StructureImportDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [sheetName, setSheetName] = useState('P&L');
  const [labelCol, setLabelCol] = useState(2);
  const [dataColStart, setDataColStart] = useState(4);
  const [createReport, setCreateReport] = useState(true);
  const [reportName, setReportName] = useState('Imported P&L');
  const [reportSlug, setReportSlug] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);

  async function submit() {
    if (!file) return;
    setBusy(true);
    try {
      const b64 = await fileToBase64(file);
      const res = await api.importReportStructure(b64, {
        sheet_name: sheetName,
        label_col: labelCol,
        data_col_start: dataColStart,
        create_report: createReport ? 1 : 0,
        report_name: reportName,
        report_slug: reportSlug || undefined,
      });
      setResult(res);
    } catch (e: any) {
      setResult({ error: e.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal" style={{ maxWidth: 760 }}>
        <div className="modal-head">
          <div className="strong">Import report structure from Excel</div>
          <button onClick={onClose} aria-label="Close"><i className="ti ti-x" aria-hidden /></button>
        </div>
        <div className="modal-body">
          <div className="form-row"><label><span className="flbl">.xlsx file</span><input type="file" accept=".xlsx" onChange={(e) => setFile(e.target.files?.[0] || null)} /></label></div>
          <div className="form-grid">
            <label><span className="flbl">Sheet name</span><input value={sheetName} onChange={(e) => setSheetName(e.target.value)} /></label>
            <label><span className="flbl">Label column</span><input type="number" value={labelCol} onChange={(e) => setLabelCol(parseInt(e.target.value) || 2)} /></label>
            <label><span className="flbl">First data column</span><input type="number" value={dataColStart} onChange={(e) => setDataColStart(parseInt(e.target.value) || 4)} /></label>
          </div>
          <label className="chk" style={{ marginTop: 8 }}><input type="checkbox" checked={createReport} onChange={(e) => setCreateReport(e.target.checked)} /> Create a new Report Definition</label>
          {createReport && (
            <div className="form-grid">
              <label><span className="flbl">New report name</span><input value={reportName} onChange={(e) => setReportName(e.target.value)} /></label>
              <label><span className="flbl">Slug (optional)</span><input value={reportSlug} onChange={(e) => setReportSlug(e.target.value)} placeholder="auto-generated if empty" /></label>
            </div>
          )}
          <div className="muted" style={{ marginTop: 8 }}>
            Reads non-formula labels in column B and treats them as <code className="fp">section</code> / <code className="fp">source</code> / <code className="fp">formula</code> rows. Excel formulas like <code className="fp">=D8-D10-D12</code> are translated to row-key references; <code className="fp">SUM(D17:D25)</code> ranges are expanded to a sum of every row key in the range.
          </div>

          {result && (
            <div className={'import-result ' + (result.error ? 'is-error' : '')}>
              {result.error ? `Error: ${result.error}` : (
                <>
                  <div><strong>Sheet used:</strong> {result.sheet_used}</div>
                  <div><strong>Rows inferred:</strong> {result.rows?.length || 0}</div>
                  {result.created_report && <div><strong>Created report:</strong> {result.created_report}</div>}
                  {result.warnings?.length > 0 && (
                    <details><summary>{result.warnings.length} warning{result.warnings.length === 1 ? '' : 's'}</summary>
                      <ul>{result.warnings.slice(0, 25).map((w: string, i: number) => <li key={i}>{w}</li>)}</ul>
                    </details>
                  )}
                  <details><summary>Preview rows</summary>
                    <ul style={{ maxHeight: 220, overflow: 'auto' }}>
                      {(result.rows || []).slice(0, 60).map((r: any, i: number) => (
                        <li key={i}><code className="fp">{r.kind}</code> {r.label}{r.formula ? <> → <code className="fp">{r.formula}</code></> : ''}</li>
                      ))}
                    </ul>
                  </details>
                </>
              )}
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button onClick={onClose}>Cancel</button>
          <button onClick={submit} disabled={!file || busy} className="primary-btn">{busy ? 'Importing…' : 'Run import'}</button>
          {result && !result.error && <button onClick={onDone} className="primary-btn">Done</button>}
        </div>
      </div>
    </div>
  );
}

function AddAccountDialog({
  report,
  flagsInRows,
  existingFlagOptions,
  onClose,
  onDone,
}: {
  report: ReportDefinition;
  flagsInRows: string[];
  existingFlagOptions: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [search, setSearch] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const [flagOptions, setFlagOptions] = useState<string[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<any | null>(null);
  const [flag, setFlag] = useState<string>('');
  const [customFlag, setCustomFlag] = useState<string>('');
  const [useCustom, setUseCustom] = useState(false);
  // Default: bind groups as group (auto-include future leaves). User can flip
  // to "Expand to leaves now" if they want every leaf as a separate mapping.
  const [bindAsGroup, setBindAsGroup] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [resultMsg, setResultMsg] = useState('');

  // Initial load: fetch first 50 accounts + flags from server.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [accs, serverFlags] = await Promise.all([
          api.listAvailableAccounts(report.name!, '', 50),
          api.listExistingFlags(report.name!),
        ]);
        if (cancelled) return;
        setResults(accs);
        // Merge: flags from the definition + server-known flags + flags in mapping rows.
        const merged = Array.from(
          new Set([...flagsInRows, ...existingFlagOptions, ...(serverFlags as string[])].filter(Boolean)),
        ).sort();
        setFlagOptions(merged);
      } catch (e: any) {
        if (!cancelled) setError(e?.message || 'Could not load accounts.');
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [report.name]);

  // Debounced search.
  useEffect(() => {
    if (!report.name) return;
    const t = setTimeout(async () => {
      try {
        const accs = await api.listAvailableAccounts(report.name!, search, 50);
        setResults(accs);
      } catch (e: any) {
        setError(e?.message || 'Search failed.');
      }
    }, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  // When the user picks an already-mapped account, pre-fill the flag dropdown
  // with its current mapping so they can keep it (one click) or change it.
  useEffect(() => {
    if (selectedAccount?.existing_mapping?.flag) {
      setFlag(selectedAccount.existing_mapping.flag);
      setUseCustom(false);
    }
  }, [selectedAccount]);

  const effectiveFlag = useCustom ? customFlag.trim() : flag.trim();
  const canSubmit = selectedAccount && effectiveFlag.length > 0 && !busy;

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setError('');
    try {
      const isGroup = !!selectedAccount.is_group;
      // Use bulkSetAccountFlags so the group-binding path is consistent with
      // bulk imports. For a leaf account, this just creates one mapping row.
      const res = await api.bulkSetAccountFlags(report.name!, [
        {
          account: selectedAccount.name,
          flag: effectiveFlag,
          bind_as_group: isGroup && bindAsGroup ? 1 : 0,
        },
      ]);
      if (isGroup && bindAsGroup) {
        setResultMsg(`Bound group ${selectedAccount.account_number || selectedAccount.name} → ${effectiveFlag}. New leaves added under this group will be included automatically.`);
      } else if (isGroup) {
        setResultMsg(`Expanded group ${selectedAccount.account_number || selectedAccount.name} → ${res.expanded_total} leaves mapped to ${effectiveFlag}.`);
      } else {
        setResultMsg(`Mapped ${selectedAccount.account_number || selectedAccount.name} → ${effectiveFlag}.`);
      }
      setSelectedAccount(null);
      try {
        const accs = await api.listAvailableAccounts(report.name!, search, 50);
        setResults(accs);
      } catch {}
    } catch (e: any) {
      setError(e?.message || 'Save failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal" style={{ maxWidth: 720 }}>
        <div className="modal-head">
          <div className="strong">Add account to {report.report_name}</div>
          <button onClick={onClose} aria-label="Close"><i className="ti ti-x" aria-hidden /></button>
        </div>
        <div className="modal-body">
          <div className="muted" style={{ marginBottom: 10 }}>
            Pick an account from your chart, then choose which P&amp;L flag it should roll up to. The flag is the label of the source row in your report definition.
          </div>

          <label><span className="flbl">1 · Account</span>
            <input
              type="text"
              autoFocus
              placeholder="Search by code or name…"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setSelectedAccount(null); }}
            />
          </label>
          <div style={{ maxHeight: 220, overflow: 'auto', marginTop: 6, border: '0.5px solid var(--border)', borderRadius: 'var(--radius-md)' }}>
            {results.length === 0 && (
              <div className="muted" style={{ padding: '12px 14px' }}>
                {search.trim()
                  ? <>No account found matching <code className="fp">{search}</code>. Try a shorter search term, or check that the account exists in <code className="fp">/app/account</code>.</>
                  : (report.company ? <>Filtered to company <code className="fp">{report.company}</code>. Start typing to search.</> : 'No company set on this report — start typing to search all accounts.')
                }
              </div>
            )}
            {results.map((acc) => {
              const isPicked = selectedAccount?.name === acc.name;
              const existing = acc.existing_mapping;
              return (
                <div
                  key={acc.name}
                  onClick={() => setSelectedAccount(acc)}
                  style={{
                    padding: '6px 10px',
                    cursor: 'pointer',
                    display: 'flex',
                    gap: 8,
                    alignItems: 'center',
                    borderBottom: '0.5px solid var(--border)',
                    background: isPicked ? 'var(--info-bg)' : 'transparent',
                    color: isPicked ? 'var(--info-text)' : 'inherit',
                  }}
                >
                  <code className="fp" style={{ minWidth: 70 }}>{acc.account_number || '—'}</code>
                  <span style={{ flex: 1 }}>{acc.account_name}</span>
                  {existing && (
                    <span
                      title={`Already mapped to flag "${existing.flag}"${existing.is_group_binding ? ' (group binding — leaves resolved at runtime)' : ''} (source: ${existing.source || 'manual'}). Picking this account will overwrite the flag.`}
                      style={{
                        fontSize: 9, padding: '1px 6px', borderRadius: 8,
                        background: existing.is_group_binding ? 'var(--info-bg, #e6f1fb)' : '#faeeda',
                        color: existing.is_group_binding ? 'var(--info, #0c447c)' : '#854f0b',
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                      }}
                    >
                      <i className={existing.is_group_binding ? 'ti ti-folder' : 'ti ti-link'} aria-hidden />
                      {existing.flag}{existing.is_group_binding ? ' (grp)' : ''}
                    </span>
                  )}
                  {acc.is_group && !existing && (
                    <span
                      style={{
                        fontSize: 9, padding: '1px 6px', borderRadius: 8,
                        background: 'var(--surface-2)', color: 'var(--text-muted)',
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                      }}
                      title="This is a group account — you can bind the whole group at once."
                    >
                      <i className="ti ti-folder" aria-hidden /> group
                    </span>
                  )}
                  {acc.company && acc.company !== report.company && (
                    <span className="muted" style={{ fontSize: 10, padding: '0 6px', background: 'var(--surface-2)', borderRadius: 8 }}>{acc.company}</span>
                  )}
                  <span className="muted" style={{ fontSize: 10 }}>{acc.root_type || ''}</span>
                </div>
              );
            })}
          </div>

          <div style={{ marginTop: 14 }}>
            <span className="flbl">2 · P&amp;L flag</span>
            {selectedAccount?.existing_mapping && (
              <div style={{
                margin: '6px 0 8px', padding: '6px 10px',
                background: '#faeeda', color: '#854f0b',
                borderRadius: 'var(--radius-md)', fontSize: 11,
                display: 'flex', alignItems: 'center', gap: 6,
              }}>
                <i className="ti ti-info-circle" aria-hidden />
                <span>
                  This account is already mapped to <strong>{selectedAccount.existing_mapping.flag}</strong>
                  {selectedAccount.existing_mapping.is_group_binding ? ' (as a group binding)' : ''}.
                  Saving will overwrite that mapping.
                </span>
              </div>
            )}
            {selectedAccount?.is_group && (
              <div style={{
                margin: '6px 0 8px', padding: '8px 10px',
                background: 'var(--info-bg, #e6f1fb)', color: 'var(--info, #0c447c)',
                borderRadius: 'var(--radius-md)', fontSize: 11,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                  <i className="ti ti-folder" aria-hidden />
                  <span><strong>{selectedAccount.account_number || selectedAccount.account_name}</strong> is a group account.</span>
                </div>
                <div style={{ display: 'flex', gap: 4, marginLeft: 18, flexDirection: 'column' }}>
                  <label style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 11, cursor: 'pointer' }}>
                    <input
                      type="radio"
                      checked={bindAsGroup}
                      onChange={() => setBindAsGroup(true)}
                      style={{ width: 'auto', height: 'auto', marginTop: 2 }}
                    />
                    <span>
                      <strong>Bind the group itself.</strong> Every leaf under it counts toward the flag, including leaves added later. <em>(Recommended.)</em>
                    </span>
                  </label>
                  <label style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 11, cursor: 'pointer' }}>
                    <input
                      type="radio"
                      checked={!bindAsGroup}
                      onChange={() => setBindAsGroup(false)}
                      style={{ width: 'auto', height: 'auto', marginTop: 2 }}
                    />
                    <span>
                      <strong>Expand to leaves now.</strong> Create one mapping row per current leaf. New leaves added later will <em>not</em> be auto-included.
                    </span>
                  </label>
                </div>
              </div>
            )}
            <div style={{ display: 'flex', gap: 6, marginTop: 4, alignItems: 'center' }}>
              <input
                type="radio"
                id="flag-mode-existing"
                checked={!useCustom}
                onChange={() => setUseCustom(false)}
                style={{ width: 'auto', height: 'auto' }}
              />
              <label htmlFor="flag-mode-existing" style={{ marginRight: 12, fontSize: 12 }}>Use existing flag</label>
              <input
                type="radio"
                id="flag-mode-custom"
                checked={useCustom}
                onChange={() => setUseCustom(true)}
                style={{ width: 'auto', height: 'auto' }}
              />
              <label htmlFor="flag-mode-custom" style={{ fontSize: 12 }}>Create new flag</label>
            </div>
            {!useCustom ? (
              <select value={flag} onChange={(e) => setFlag(e.target.value)} style={{ marginTop: 6 }}>
                <option value="">— Pick a flag —</option>
                {flagOptions.map((f) => <option key={f} value={f}>{f}{flagsInRows.includes(f) ? '' : ' (no source row yet)'}</option>)}
              </select>
            ) : (
              <input
                type="text"
                placeholder="e.g. Other Revenue"
                value={customFlag}
                onChange={(e) => setCustomFlag(e.target.value)}
                style={{ marginTop: 6 }}
              />
            )}
            <div className="muted" style={{ marginTop: 6, fontSize: 11 }}>
              Flags marked "no source row yet" still work for storage, but the report won't show a line for them until you add a matching <code className="fp">source</code> row on the Rows tab.
            </div>
          </div>

          {error && <div className="import-result is-error" style={{ marginTop: 12 }}>{error}</div>}
          {resultMsg && <div className="import-result" style={{ marginTop: 12 }}>{resultMsg}</div>}
        </div>
        <div className="modal-foot">
          <button onClick={onClose}>Close</button>
          <button onClick={submit} disabled={!canSubmit} className="primary-btn">
            <i className="ti ti-plus" aria-hidden />
            {busy ? 'Saving…' : (selectedAccount?.existing_mapping ? 'Update mapping' : 'Add mapping')}
          </button>
          <button onClick={onDone} disabled={busy}>Done</button>
        </div>
      </div>
    </div>
  );
}
