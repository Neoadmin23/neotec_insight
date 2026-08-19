import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { loadBrand } from '../../utils/branddoc';
import { ExportBar } from '../ExportBar';
import { setActiveCompany } from '../../utils/activeCompany';
import type { ReportDoc, DocRow } from '../../utils/reportdoc';
import { api } from '../../utils/api';
import { t, arName, getLang, loadArabicLabels, mergeLabels } from '../../utils/i18n';
import { ArName } from '../../components/ArName';
import { DimensionMultiSelect } from '../DimensionMultiSelect';
import { fmtD } from '../../utils/format';

// Columns the user can pick. `key` maps to a transaction field; debit/credit/
// balance are always numeric. Order here is the display order.
const ALL_COLUMNS: { key: string; label: string; num?: boolean }[] = [
  { key: 'posting_date', label: 'Voucher Date' },
  { key: 'voucher_no', label: 'Voucher No' },
  { key: 'voucher_type', label: 'Voucher Type' },
  { key: 'account_label', label: 'Account' },
  { key: 'remarks', label: 'Particular' },
  { key: 'against', label: 'Details' },
  { key: 'description', label: 'Description' },
  { key: 'party', label: 'Party' },
  { key: 'cost_center', label: 'Cost Center' },
  { key: 'project', label: 'Project' },
  { key: 'department', label: 'Department' },
  { key: 'debit', label: 'Debit', num: true },
  { key: 'credit', label: 'Credit', num: true },
  { key: 'balance', label: 'Balance', num: true },
];
const DEFAULT_COLS = ['posting_date', 'voucher_no', 'remarks', 'against', 'debit', 'credit', 'balance'];
// v2.57.0 — a party ledger has lost the account from its block heading, so
// the account column earns its place in the default set instead.
const PARTY_DEFAULT_COLS = ['posting_date', 'voucher_no', 'account_label', 'remarks', 'debit', 'credit', 'balance'];

export type LedgerMode = 'accounts' | 'supplier' | 'customer';

/** How a ledger balance shows its sign (v2.83.0).
 *
 *  Dr/Cr is unambiguous but reads as bookkeeping notation; finance teams and
 *  auditors outside ERPNext usually expect a minus or brackets. The convention
 *  is a presentation choice, not an accounting one — the underlying number is
 *  identical — so it is a per-user setting rather than a report definition
 *  field, and it applies to every ledger at once.
 *
 *  DEBIT STAYS POSITIVE in every style. Only the credit side changes
 *  appearance. Flipping the sign of debits as well would make an Excel export
 *  sum to something different from the same export taken yesterday. */
export type BalanceStyle = 'drcr' | 'minus' | 'minus_red' | 'paren' | 'paren_red';

export const BALANCE_STYLES: { value: BalanceStyle; label: string; sample: string }[] = [
  { value: 'drcr',      label: 'Dr / Cr',              sample: '1,250.00Cr' },
  { value: 'minus',     label: 'Minus sign',           sample: '-1,250.00' },
  { value: 'minus_red', label: 'Minus sign, in red',   sample: '-1,250.00' },
  { value: 'paren',     label: 'Brackets',             sample: '(1,250.00)' },
  { value: 'paren_red', label: 'Brackets, in red',     sample: '(1,250.00)' },
];

function balText(raw: number, dec: number, style: BalanceStyle): string {
  const v = fmtD(Math.abs(raw), dec);
  if (raw >= 0) return style === 'drcr' ? v + 'Dr' : v;
  switch (style) {
    case 'minus': case 'minus_red': return '-' + v;
    case 'paren': case 'paren_red': return '(' + v + ')';
    default: return v + 'Cr';
  }
}

/** True when this value should print red under the chosen style. */
function balIsNeg(raw: number, style: BalanceStyle): boolean {
  return raw < 0 && (style === 'minus_red' || style === 'paren_red');
}

interface AcctOpt { name: string; account_number?: string; account_name?: string; is_group?: number; company?: string; parent_account?: string; lft?: number; rgt?: number; root_type?: string; }

export function GeneralLedger({ reportName, mode = 'accounts' }: { reportName?: string; mode?: LedgerMode }) {
  // A party ledger is the same engine with its subject fixed. Everything
  // below branches on `isParty` rather than forking the component — the
  // running-balance and export paths must not be able to drift apart.
  const isParty = mode !== 'accounts';
  const partyType = mode === 'customer' ? 'Customer' : 'Supplier';
  const today = new Date();
  const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const [company, setCompany] = useState<string>('');
  // v2.56.0 — 'auto' means: party when a supplier/customer filter is set,
  // account otherwise. The explicit values let the user override either way.
  const [groupBy, setGroupBy] = useState<'account' | 'party'>('account');
  useEffect(() => { setActiveCompany(company); }, [company]);

  // v2.57.0 — a party ledger implies its account set. Preselecting the
  // control accounts is the difference between "pick Creditors from a tree
  // of 400 accounts" and "choose a supplier and press Run". The picker stays
  // available for sites with several payable accounts (retention, related
  // party) that need narrowing.
  useEffect(() => {
    if (!isParty || !company) return;
    let alive = true;
    api.partyControlAccounts(company, partyType)
      .then((rows) => {
        if (!alive || !rows?.length) return;
        setSelected((cur) => (cur.length ? cur
          : rows.map((r) => ({ name: r.name, account_name: r.label } as AcctOpt))));
      })
      .catch(() => { /* the picker still works by hand */ });
    return () => { alive = false; };
  }, [isParty, partyType, company]);
  const [companies, setCompanies] = useState<any[]>([]);
  const [fromDate, setFromDate] = useState(iso(new Date(today.getFullYear(), 0, 1)));
  const [toDate, setToDate] = useState(iso(today));
  const [decimals, setDecimals] = useState(2);

  // account picker
  const [acctSearch, setAcctSearch] = useState('');
  const [acctResults, setAcctResults] = useState<AcctOpt[]>([]);
  const [selected, setSelected] = useState<AcctOpt[]>([]);
  const [searching, setSearching] = useState(false);
  // tree picker (v1.9.99) — browse the chart and select a group (→ its leaves) or leaves
  const [treeOpen, setTreeOpen] = useState(false);
  const [treeNodes, setTreeNodes] = useState<AcctOpt[]>([]);
  const [treeLoading, setTreeLoading] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // options
  const [optNoTxn, setOptNoTxn] = useState(false);
  const [optZeroClosing, setOptZeroClosing] = useState(false);
  const [optOnlyOpening, setOptOnlyOpening] = useState(false);
  const [optPageBreak, setOptPageBreak] = useState(false);
  const [optSplit, setOptSplit] = useState(true);
  const [supplier, setSupplier] = useState<string[]>([]);
  const [customer, setCustomer] = useState<string[]>([]);
  const [supplierOpts, setSupplierOpts] = useState<{ name: string; label?: string }[]>([]);
  const [customerOpts, setCustomerOpts] = useState<{ name: string; label?: string }[]>([]);
  const [costCenter, setCostCenter] = useState<string[]>([]);
  const [project, setProject] = useState<string[]>([]);
  const [department, setDepartment] = useState<string[]>([]);
  const [branch, setBranch] = useState<string[]>([]);
  const [dimOpts, setDimOpts] = useState<Record<string, { name: string; label?: string }[]>>({});

  // columns
  const [cols, setCols] = useState<string[]>(isParty ? PARTY_DEFAULT_COLS : DEFAULT_COLS);
  const [showColPicker, setShowColPicker] = useState(false);

  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    api.listCompanies().then((cs: any[]) => {
      setCompanies(cs || []);
      if (cs && cs.length && !company) setCompany(cs[0].name);
      cs && loadArabicLabels('Company', (cs || []).map((c) => c.name));
    }).catch(() => {});
  }, []);

  // Dimension options (with Arabic names), loaded per company.
  useEffect(() => {
    const kinds = ['supplier', 'customer', 'cost_center', 'project', 'department', 'branch'] as const;
    kinds.forEach((kind) => {
      api.dimensionOptions(kind, company || null).then((rows: any[]) => {
        const list = (rows || []).map((r) => ({ name: r.name, label: r.label || r.name }));
        const arMap: Record<string, string> = {};
        (rows || []).forEach((r) => { if (r.label_ar) arMap[r.name] = r.label_ar; });
        mergeLabels(arMap);
        if (kind === 'supplier') setSupplierOpts(list);
        else if (kind === 'customer') setCustomerOpts(list);
        else setDimOpts((m) => ({ ...m, [kind]: list }));
      }).catch(() => {});
    });
  }, [company]);

  const withBlank = (list: { name: string; label?: string }[]) =>
    [{ name: '__BLANK__', label: t('(No value)') }, ...(list || [])];

  // account search (debounced)
  useEffect(() => {
    if (!acctSearch || acctSearch.length < 2) { setAcctResults([]); return; }
    let alive = true; setSearching(true);
    const h = setTimeout(() => {
      api.listAvailableAccounts(reportName || '', acctSearch, 30, 1)
        .then((r: any[]) => { if (alive) { setAcctResults(r || []); setSearching(false); } })
        .catch(() => { if (alive) setSearching(false); });
    }, 250);
    return () => { alive = false; clearTimeout(h); };
  }, [acctSearch, reportName]);

  // reload the chart tree when the company changes
  useEffect(() => { setTreeNodes([]); setExpanded(new Set()); }, [company]);

  // clear any stale ledger output the moment no accounts are selected
  useEffect(() => { if (selected.length === 0) { setData(null); setErr(null); } }, [selected.length]);

  function addAcct(a: AcctOpt) {
    if (!selected.find((x) => x.name === a.name)) setSelected((s) => [...s, a]);
    setAcctSearch(''); setAcctResults([]);
  }
  function removeAcct(name: string) { setSelected((s) => s.filter((x) => x.name !== name)); }

  // ── Account tree picker ───────────────────────────────────────────────────
  function openTree() {
    setTreeOpen(true);
    if (treeNodes.length) return;
    setTreeLoading(true);
    api.accountTree(company || null)
      .then((rows: AcctOpt[]) => {
        setTreeNodes(rows || []);
        // expand root-level groups by default
        const roots = (rows || []).filter((n) => !n.parent_account && n.is_group).map((n) => n.name);
        setExpanded(new Set(roots));
      })
      .catch(() => setTreeNodes([]))
      .finally(() => setTreeLoading(false));
  }
  const childrenOf = useMemo(() => {
    const m: Record<string, AcctOpt[]> = {};
    for (const n of treeNodes) { const p = n.parent_account || '__root__'; (m[p] ||= []).push(n); }
    return m;
  }, [treeNodes]);
  const selectedSet = useMemo(() => new Set(selected.map((s) => s.name)), [selected]);
  useEffect(() => { setPage(0); }, [selected.length]);
  // v2.45.1 — hide-selected (display-only) + Table of Contents with search:
  // account → page number → click → fetch that page and scroll to the block.
  const [chipsVisible, setChipsVisible] = useState(false);
  const [tocOpen, setTocOpen] = useState(false);
  const [tocSearch, setTocSearch] = useState('');
  const [scrollTarget, setScrollTarget] = useState<string | null>(null);
  useEffect(() => {
    if (!scrollTarget || !data) return;
    const el = document.getElementById('glacc-' + scrollTarget);
    if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); setScrollTarget(null); }
  }, [data, scrollTarget]);
  function gotoAccount(name: string) {
    const idx = selected.findIndex((a) => a.name === name);
    if (idx < 0) return;
    const pg = Math.floor(idx / pageSize);
    setTocOpen(false); setScrollTarget(name);
    if (pg !== page || !data) { setPage(pg); run(pg); }
    else { const el = document.getElementById('glacc-' + name); el?.scrollIntoView({ behavior: 'smooth', block: 'start' }); setScrollTarget(null); }
  }
  // leaf accounts under a group, via nested-set lft/rgt range
  function leavesUnder(g: AcctOpt): AcctOpt[] {
    if (g.lft == null || g.rgt == null) return [];
    return treeNodes.filter((n) => !n.is_group && (n.lft ?? 0) > (g.lft as number) && (n.rgt ?? 0) < (g.rgt as number));
  }
  function groupState(g: AcctOpt): 'none' | 'some' | 'all' {
    const leaves = leavesUnder(g);
    if (!leaves.length) return 'none';
    const n = leaves.filter((l) => selectedSet.has(l.name)).length;
    return n === 0 ? 'none' : n === leaves.length ? 'all' : 'some';
  }
  function toggleLeaf(a: AcctOpt) {
    setSelected((s) => s.find((x) => x.name === a.name) ? s.filter((x) => x.name !== a.name) : [...s, a]);
  }
  function toggleGroup(g: AcctOpt) {
    const leaves = leavesUnder(g);
    const allSel = leaves.length > 0 && leaves.every((l) => selectedSet.has(l.name));
    setSelected((s) => {
      if (allSel) { const rm = new Set(leaves.map((l) => l.name)); return s.filter((x) => !rm.has(x.name)); }
      const have = new Set(s.map((x) => x.name));
      return [...s, ...leaves.filter((l) => !have.has(l.name))];
    });
  }
  function selectAllLeaves() {
    const leaves = treeNodes.filter((n) => !n.is_group);
    setSelected(leaves.map((l) => ({ name: l.name, account_number: l.account_number, account_name: l.account_name })));
  }
  function toggleExpand(name: string) {
    setExpanded((e) => { const n = new Set(e); n.has(name) ? n.delete(name) : n.add(name); return n; });
  }
  function renderNode(node: AcctOpt, depth: number): React.JSX.Element {
    const kids = childrenOf[node.name] || [];
    const isGroup = !!node.is_group;
    const isOpen = expanded.has(node.name);
    const label = (node.account_number ? node.account_number + ' · ' : '') + (node.account_name || node.name);
    const state = isGroup ? groupState(node) : (selectedSet.has(node.name) ? 'all' : 'none');
    return (
      <div key={node.name} className="gl-tnode">
        <div className="gl-trow" style={{ paddingInlineStart: depth * 15 + 6 + 'px' }}>
          {isGroup
            ? <button className="gl-tcaret" onClick={() => toggleExpand(node.name)} aria-label="toggle">{isOpen ? '▾' : '▸'}</button>
            : <span className="gl-tcaret-sp" />}
          <input
            type="checkbox"
            checked={state === 'all'}
            ref={(el) => { if (el) el.indeterminate = state === 'some'; }}
            onChange={() => (isGroup ? toggleGroup(node) : toggleLeaf(node))}
          />
          <span className={'gl-tlabel' + (isGroup ? ' grp' : '')}
            onClick={() => (isGroup ? toggleExpand(node.name) : toggleLeaf(node))}>
            <ArName name={node.name} fallback={label} source="Account" />
            {isGroup ? <span className="gl-grp">{t('group')}</span> : null}
          </span>
        </div>
        {isGroup && isOpen && kids.map((k) => renderNode(k, depth + 1))}
      </div>
    );
  }

  // ── v2.74.0 — exclusions, source-document columns, combined column ──
  const [exTypes, setExTypes] = useState<string[]>([]);
  const [exVouchers, setExVouchers] = useState<string[]>([]);
  const [docFields, setDocFields] = useState<Record<string, string[]>>({});
  const [fieldOpts, setFieldOpts] = useState<Record<string, any[]>>({});
  const [combine, setCombine] = useState<string[]>([]);
  const [combineSep, setCombineSep] = useState(' · ');
  const [combineLabel, setCombineLabel] = useState('Details');
  const [showAdv, setShowAdv] = useState(false);
  // Persisted per browser: a preparer who reads brackets wants brackets on
  // every ledger, every day, not once per session.
  const [balStyle, setBalStyle] = useState<BalanceStyle>(() => {
    try { return (localStorage.getItem('ni-bal-style') as BalanceStyle) || 'drcr'; }
    catch { return 'drcr'; }
  });
  useEffect(() => {
    try { localStorage.setItem('ni-bal-style', balStyle); } catch { /* private mode */ }
  }, [balStyle]);

  const vtypes: string[] = data?.filters?.voucher_types || [];

  // Field lists are fetched only for doctypes actually present in the window,
  // and only once the panel is opened — meta reads are not free and most runs
  // never touch this.
  useEffect(() => {
    if (!showAdv || !vtypes.length) return;
    const missing = vtypes.filter((v) => !fieldOpts[v]);
    if (!missing.length) return;
    api.voucherFieldOptions(missing)
      .then((r: any) => setFieldOpts((prev) => ({ ...prev, ...(r || {}) })))
      .catch(() => {});
  }, [showAdv, vtypes.join('|')]);

  // One dynamic column per picked source field, plus the combined column.
  // They are ordinary column defs, so Excel, CSV, PDF, Print and PNG pick
  // them up with no export-side change — the alternative, special-casing
  // them per writer, is how the five formats drift apart.
  const dynCols = useMemo(() => {
    const out: { key: string; label: string; num?: boolean; doc?: boolean }[] = [];
    Object.entries(docFields).forEach(([dt, fields]) => {
      (fields || []).forEach((f) => {
        const meta = (fieldOpts[dt] || []).find((x: any) => x.fieldname === f);
        out.push({ key: `${dt}::${f}`, label: `${meta?.label || f}`, doc: true });
      });
    });
    if (combine.length) out.push({ key: '__combined__', label: combineLabel || 'Combined', doc: true });
    return out;
  }, [docFields, fieldOpts, combine, combineLabel]);

  const colDefs = useMemo(() => {
    const base = ALL_COLUMNS.filter((c) => cols.includes(c.key));
    return [...base, ...dynCols.filter((d) => d.key === '__combined__' || cols.includes(d.key))];
  }, [cols, dynCols]);

  // v2.48.2 — the *name* of the company, not the link value. ERPNext's Company
  // docname is an identifier ("IRSAA Business Solution"); the registered name
  // lives in company_name (exposed as `label`), and an Arabic label may exist.
  // Headings print the name; filters keep using the docname.
  const companyLabel = useMemo(() => {
    const row = companies.find((c) => c.name === company);
    return arName(company, (row && (row.label || row.company_name)) || company || '');
  }, [companies, company]);

  // v2.45.0 — Noor's pagination: N accounts per page keeps every request
  // small (plain GET, the proven transport), the page renders instantly, and
  // a failing page never blocks the others.
  const [pageSize, setPageSize] = useState<number>(() => { try { return parseInt(localStorage.getItem('ni-gl-pagesize') || '10') || 10; } catch { return 10; } });
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(selected.length / pageSize));

  async function run(pg = page) {
    if (selected.length === 0) { setErr(t('Select at least one account.')); setData(null); return; }
    setLoading(true); setErr(null);
    try {
      const pageNames = selected.slice(pg * pageSize, (pg + 1) * pageSize).map((s) => s.name);
      const r = await api.generalLedger({
        company: company || null,
        accounts: pageNames,
        from_date: fromDate, to_date: toDate,
        show_without_transactions: optNoTxn ? 1 : 0,
        show_zero_closing: optZeroClosing ? 1 : 0,
        show_only_opening: optOnlyOpening ? 1 : 0,
        split_by_against: optSplit ? 1 : 0,
        supplier: supplier.length ? supplier : null,
        customer: customer.length ? customer : null,
        cost_center: costCenter.length ? costCenter : null,
        project: project.length ? project : null,
        department: department.length ? department : null,
        branch: branch.length ? branch : null,
        report: reportName || null,
        group_by: isParty ? 'party' : groupBy,
        // Only pay for the description lookup when the column is on show.
        with_description: cols.includes('description') ? 1 : 0,
        exclude_voucher_types: exTypes.length ? exTypes : null,
        exclude_vouchers: exVouchers.length ? exVouchers : null,
        doc_fields: Object.keys(docFields).length ? JSON.stringify(docFields) : null,
      });
      // load Arabic names for the accounts shown
      loadArabicLabels('Account', (r.accounts || []).map((b: any) => b.account));
      setData(r);
    } catch (e: any) {
      setErr(String(e?.message || e || 'Error'));
    } finally {
      setLoading(false);
    }
  }

  /** Desk URL for the source document.
   *  Built from voucher_type + voucher_no, which every GL row carries, so it
   *  works for any doctype without a per-type mapping to maintain. */
  function docUrl(tx: any): string {
    const dt = String(tx.voucher_type || '').toLowerCase().replace(/\s+/g, '-');
    return `/app/${dt}/${encodeURIComponent(tx.voucher_no || '')}`;
  }

  function docVal(tx: any, key: string): string {
    const v = tx?.doc?.[key];
    if (v === null || v === undefined || v === '') return '';
    return String(v).replace(/<[^>]*>/g, '').trim();
  }

  function cellVal(tx: any, key: string): string {
    if (key === 'debit') return tx.debit ? fmtD(tx.debit, decimals) : '';
    if (key === 'credit') return tx.credit ? fmtD(tx.credit, decimals) : '';
    if (key === 'balance') return balText(tx.balance_raw, decimals, balStyle);
    if (key === 'posting_date') return tx.posting_date || '';
    // The combined column joins whatever the user picked, skipping blanks so a
    // field that is empty on this document leaves no dangling separator.
    if (key === '__combined__') {
      return combine
        .map((k) => (k.includes('::') ? docVal(tx, k) : cellVal(tx, k)))
        .filter((x) => x !== '')
        .join(combineSep);
    }
    if (key.includes('::')) return docVal(tx, key);
    return (tx[key] || '').toString();
  }

  // ── the ledger as a portable document (v2.55.0) ───────────────────────
  // Excel, CSV, PDF, Print and PNG are all rendered from this one
  // description by the shared writers, so the ledger cannot drift from the
  // statements the way the two hand-built export paths used to.
  function buildGlDoc(): ReportDoc | null {
    if (!data) return null;
    const rows: DocRow[] = [];
    const nCols = colDefs.length;
    // Raw number for Excel, formatted string for every other output.
    const cellFor = (tx: any, c: { key: string; num?: boolean }) => {
      const n = Number(tx[c.key]);
      if (c.num && isFinite(n)) return { v: n, text: cellVal(tx, c.key), num: true as const };
      // The voucher number carries an absolute link so Excel and PDF stay
      // clickable off the machine that produced them — a relative /app path
      // resolves against the reader's browser, not the site.
      if (c.key === 'voucher_no' && tx.voucher_type && tx.voucher_no) {
        return { v: cellVal(tx, c.key), num: c.num, link: origin + docUrl(tx) };
      }
      return { v: cellVal(tx, c.key), num: c.num };
    };
    const origin = typeof window !== 'undefined' ? window.location.origin : '';

    data.accounts.forEach((b: any, idx: number) => {
      // Exports carry the same heading the screen shows — a party block is
      // headed by the party, not by an account code it doesn't have.
      const accLabel = b.group_kind === 'party'
        ? (b.party_type ? t(b.party_type) + ': ' : '') + arName(b.party, b.account_name)
        : arName(b.account, (b.account_number ? b.account_number + ' - ' : '') + b.account_name);
      rows.push({ kind: 'grp', cells: [{ v: accLabel, colSpan: nCols, bold: true }] });
      rows.push({
        kind: 'sub',
        cells: colDefs.map((c) => c.key === colDefs[0].key
          ? { v: t('Opening Balance'), bold: true }
          : c.key === 'balance' ? { v: balText(b.opening_raw, decimals, balStyle), num: true, bold: true, fg: balIsNeg(b.opening_raw, balStyle) ? '#a02323' : undefined }
            : { v: '', num: c.num }),
      });
      for (const tx of b.transactions) {
        rows.push({ cells: colDefs.map((c) => cellFor(tx, c)) });
      }
      rows.push({
        kind: 'tot',
        breakAfter: optPageBreak && idx < data.accounts.length - 1,
        cells: colDefs.map((c) => c.key === colDefs[0].key
          ? { v: t('Sub Total'), bold: true }
          : c.key === 'debit' ? { v: Number(b.sub_total.debit) || 0, text: fmtD(b.sub_total.debit, decimals), num: true, bold: true }
            : c.key === 'credit' ? { v: Number(b.sub_total.credit) || 0, text: fmtD(b.sub_total.credit, decimals), num: true, bold: true }
              : c.key === 'balance' ? { v: balText(b.closing_raw, decimals, balStyle), num: true, bold: true, fg: balIsNeg(b.closing_raw, balStyle) ? '#a02323' : undefined }
                : { v: '', num: c.num }),
      });
    });

    const rt = data.report_total;
    rows.push({
      kind: 'grand',
      cells: colDefs.map((c) => c.key === colDefs[0].key
        ? { v: t('REPORT TOTAL'), bold: true }
        : c.key === 'debit' ? { v: Number(rt.debit) || 0, text: fmtD(rt.debit, decimals), num: true, bold: true }
          : c.key === 'credit' ? { v: Number(rt.credit) || 0, text: fmtD(rt.credit, decimals), num: true, bold: true }
            : c.key === 'balance' ? { v: balText(rt.balance_raw, decimals, balStyle), num: true, bold: true, fg: balIsNeg(rt.balance_raw, balStyle) ? '#a02323' : undefined }
              : { v: '', num: c.num }),
    });

    const brand = loadBrand(company || null);
    return {
      // A party ledger is a document you send to the party, so it is titled
      // as one; the account ledger keeps its own name.
      title: (brand as any).title
        || (isParty ? t('Statement of Account') : t('General Ledger')),
      subtitle: isParty
        ? (data.accounts.length === 1
            ? t(partyType) + ': ' + arName(data.accounts[0].party, data.accounts[0].account_name)
            : t(partyType) + ' — ' + t('all'))
        : undefined,
      company,
      companyLabel,
      period: `${fromDate} → ${toDate}`
        + ((exTypes.length || exVouchers.length)
            // On the face of the document, not in a footnote. A statement that
            // silently omits credit notes is exactly the artefact that gets
            // handed to an auditor and read as complete.
            ? '  ·  ' + t('EXCLUDES') + ': '
              + [...exTypes, ...exVouchers].join(', ')
            : ''),
      columns: colDefs.map((c) => ({ label: t(c.label), num: c.num, width: c.key === 'remarks' ? 34 : undefined })),
      rows,
      fileBase: isParty ? (mode === 'customer' ? 'customer_ledger' : 'supplier_ledger') : 'general_ledger',
      orientation: 'landscape',
    };
  }

  return (
    <div className="gl-page">
      <div className="gl-filters card">
        <div className="gl-frow">
          <label><span className="flbl">{t('Company')}</span>
            <select value={company} onChange={(e) => setCompany(e.target.value)}>
              {companies.map((c) => <option key={c.name} value={c.name}>{arName(c.name, c.label || c.name)}</option>)}
            </select>
          </label>
          <label><span className="flbl">{t('From date')}</span>
            <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
          </label>
          <label><span className="flbl">{t('To date')}</span>
            <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
          </label>
          <label><span className="flbl">{t('Decimals')}</span>
            <select value={decimals} onChange={(e) => setDecimals(parseInt(e.target.value))}>
              <option value={0}>0</option><option value={2}>2</option><option value={3}>3</option>
            </select>
          </label>
          {/* v2.83.0 — sign convention for balances. Presentation only: the
              underlying figure is identical in every style, and debits stay
              positive throughout, so an export still sums the way it did
              before. Remembered per browser. */}
          <label><span className="flbl">{t('Balance shown as')}</span>
            <select value={balStyle} onChange={(e) => setBalStyle(e.target.value as BalanceStyle)}
              title={t('How credit balances are displayed — applies to this and every other ledger')}>
              {BALANCE_STYLES.map((b) => (
                <option key={b.value} value={b.value}>{t(b.label)} — {b.sample}</option>
              ))}
            </select>
          </label>
          {/* v2.74.0 — a party ledger shows only its own party filter. Both were
              rendered in every mode, so the Supplier tab offered a Customer
              filter that could only ever return nothing: the tab has already
              fixed the subject to suppliers, and the accounts are payable
              control accounts no customer posts to. */}
          {mode !== 'customer' &&
            <label><span className="flbl">{t('Supplier')} {supplier.length ? `(${supplier.length})` : ''}</span>
              <DimensionMultiSelect value={supplier} options={supplierOpts} onChange={setSupplier} placeholder={t('All')} />
            </label>}
          {mode !== 'supplier' &&
            <label><span className="flbl">{t('Customer')} {customer.length ? `(${customer.length})` : ''}</span>
              <DimensionMultiSelect value={customer} options={customerOpts} onChange={setCustomer} placeholder={t('All')} />
            </label>}
          <label><span className="flbl">{t('Cost Center')} {costCenter.length ? `(${costCenter.length})` : ''}</span>
            <DimensionMultiSelect value={costCenter} options={withBlank(dimOpts.cost_center || [])} onChange={setCostCenter} placeholder={t('All')} />
          </label>
          <label><span className="flbl">{t('Project')} {project.length ? `(${project.length})` : ''}</span>
            <DimensionMultiSelect value={project} options={withBlank(dimOpts.project || [])} onChange={setProject} placeholder={t('All')} />
          </label>
          <label><span className="flbl">{t('Department')} {department.length ? `(${department.length})` : ''}</span>
            <DimensionMultiSelect value={department} options={withBlank(dimOpts.department || [])} onChange={setDepartment} placeholder={t('All')} />
          </label>
          <label><span className="flbl">{t('Branch')} {branch.length ? `(${branch.length})` : ''}</span>
            <DimensionMultiSelect value={branch} options={withBlank(dimOpts.branch || [])} onChange={setBranch} placeholder={t('All')} />
          </label>
        </div>

        <div className="gl-acct-pick">
          <span className="flbl">{t('Accounts')} ({selected.length})
            {selected.length > 0 && (
              <button className="gl-chip" style={{ marginInlineStart: 8 }} onClick={() => setChipsVisible((v) => !v)}>
                {chipsVisible ? '▾ ' + t('Hide selected') : '▸ ' + t('Show selected')}
              </button>
            )}
            {selected.length > 1 && (
              <button className="gl-chip" onClick={() => setSelected([])}>{t('Clear all')}</button>
            )}
            {selected.length > pageSize && (
              <button className="gl-chip" onClick={() => setTocOpen((o) => !o)}>
                <i className="ti ti-list-search" aria-hidden /> {t('Contents')}
              </button>
            )}
          </span>
          <div className="gl-chips" style={chipsVisible ? undefined : { display: 'none' }}>
            {selected.map((a) => (
              <span key={a.name} className="gl-chip">
                {arName(a.name, (a.account_number ? a.account_number + ' · ' : '') + (a.account_name || a.name))}
                <button onClick={() => removeAcct(a.name)} aria-label={t('Delete')}>×</button>
              </span>
            ))}
            {selected.length === 0 && <span className="gl-hint">{t('Search and add one or more accounts')}</span>}
          </div>
          <div className="gl-search">
            <input value={acctSearch} onChange={(e) => setAcctSearch(e.target.value)} placeholder={t('Search') + '…'} />
            <button type="button" className="gl-tree-btn" onClick={() => (treeOpen ? setTreeOpen(false) : openTree())}>
              <i className="ti ti-list-tree" aria-hidden /> {treeOpen ? t('Hide tree') : t('Browse tree')}
            </button>
            {searching && <span className="gl-hint">…</span>}
            {acctResults.length > 0 && (
              <div className="gl-results">
                {acctResults.map((a) => (
                  <button key={a.name} className="gl-res" onClick={() => addAcct(a)}>
                    {(a.account_number ? a.account_number + ' · ' : '') + (a.account_name || a.name)}
                    {a.is_group ? <span className="gl-grp">grp</span> : null}
                  </button>
                ))}
              </div>
            )}
          </div>
          {treeOpen && (
            <div className="gl-tree">
              <div className="gl-tree-bar">
                <button type="button" onClick={selectAllLeaves}>{t('Select all')}</button>
                <button type="button" onClick={() => setSelected([])}>{t('Clear')}</button>
                <span className="gl-tree-count">{selected.length} {t('selected')}</span>
                <span className="gl-hint">{t('Tick a group to select all accounts under it')}</span>
              </div>
              <div className="gl-tree-body">
                {treeLoading
                  ? <div className="gl-hint" style={{ padding: '10px' }}>{t('Loading chart of accounts…')}</div>
                  : (childrenOf['__root__'] || []).map((n) => renderNode(n, 0))}
                {!treeLoading && (childrenOf['__root__'] || []).length === 0 &&
                  <div className="gl-hint" style={{ padding: '10px' }}>{t('No accounts found for this company.')}</div>}
              </div>
            </div>
          )}
        </div>

        <div className="gl-opts">
          <label className="gl-cbx"><input type="checkbox" checked={optPageBreak} onChange={(e) => setOptPageBreak(e.target.checked)} /> {t('Print each account on separate page')}</label>
          <label className="gl-cbx"><input type="checkbox" checked={optNoTxn} onChange={(e) => setOptNoTxn(e.target.checked)} /> {t('Show accounts without transactions')}</label>
          <label className="gl-cbx"><input type="checkbox" checked={optZeroClosing} onChange={(e) => setOptZeroClosing(e.target.checked)} /> {t('Show accounts with zero closing balance')}</label>
          <label className="gl-cbx"><input type="checkbox" checked={optOnlyOpening} onChange={(e) => setOptOnlyOpening(e.target.checked)} /> {t('Show accounts having only opening balance')}</label>
          <label className="gl-cbx"><input type="checkbox" checked={optSplit} onChange={(e) => setOptSplit(e.target.checked)} /> {t('Show each other account on its own line')}</label>
          {!isParty && (
            <label className="gl-cbx gl-groupby">{t('Group by')}
              <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as any)}>
                <option value="account">{t('Account')}</option>
                <option value="party">{t('Party')}</option>
              </select>
            </label>
          )}
        </div>

        <div className="gl-runbar">
          <button className="btn-primary" onClick={() => run()} disabled={loading}>{loading ? t('Running…') : t('Run')}</button>
          {tocOpen && (
            <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) setTocOpen(false); }}>
              <div className="theme-panel" role="dialog" style={{ width: 'min(560px, 100%)' }}>
                <div className="theme-h">
                  <h3><i className="ti ti-list-search" aria-hidden /> {t('Ledger contents')} — {selected.length} {t('accounts')}, {pageCount} {t('pages')}</h3>
                  <button className="fh-x" onClick={() => setTocOpen(false)}>×</button>
                </div>
                <div style={{ padding: '4px 12px' }}>
                  <input autoFocus value={tocSearch} onChange={(e) => setTocSearch(e.target.value)}
                    placeholder={t('Search') + '…'} style={{ width: '100%' }} />
                </div>
                <div style={{ maxHeight: '55vh', overflow: 'auto', padding: '0 6px 8px' }}>
                  {selected.map((a, i) => {
                    const label = (a.account_number ? a.account_number + ' · ' : '') + (a.account_name || a.name);
                    if (tocSearch && !label.toLowerCase().includes(tocSearch.toLowerCase()) && !a.name.toLowerCase().includes(tocSearch.toLowerCase())) return null;
                    const pg = Math.floor(i / pageSize);
                    return (
                      <button key={a.name} className="navgrp-item" style={{ display: 'flex', width: '100%', gap: 8, textAlign: 'start' }}
                        onClick={() => gotoAccount(a.name)}>
                        <span style={{ flex: 1 }}>{arName(a.name, label)}</span>
                        <span className="gl-hint">{t('p.')} {pg + 1}{pg === page ? ' •' : ''}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
          {selected.length > pageSize && (
            <span className="gl-pager" style={{ display: 'inline-flex', gap: 6, alignItems: 'center', marginInlineStart: 10 }}>
              <button className="vat-ghost" disabled={loading || page === 0}
                onClick={() => { const p = page - 1; setPage(p); run(p); }}>‹ {t('Prev')}</button>
              <span className="gl-hint">
                {t('Page')} {page + 1}/{pageCount} · {t('accounts')} {page * pageSize + 1}–{Math.min(selected.length, (page + 1) * pageSize)} {t('of')} {selected.length}
              </span>
              <button className="vat-ghost" disabled={loading || page >= pageCount - 1}
                onClick={() => { const p = page + 1; setPage(p); run(p); }}>{t('Next')} ›</button>
              <select value={pageSize} onChange={(e) => {
                const ps = parseInt(e.target.value) || 10;
                setPageSize(ps); setPage(0);
                try { localStorage.setItem('ni-gl-pagesize', String(ps)); } catch { /* */ }
              }} title={t('Accounts per page')}>
                <option value={5}>5 / {t('page')}</option>
                <option value={10}>10 / {t('page')}</option>
                <option value={25}>25 / {t('page')}</option>
              </select>
            </span>
          )}
          <ExportBar
            company={company}
            companyLabel={companyLabel}
            disabled={!data}
            getDoc={buildGlDoc}
          >
            <button onClick={() => setShowColPicker((v) => !v)}>{t('Fields')}</button>
            <button onClick={() => setShowAdv((v) => !v)}
              className={(exTypes.length || exVouchers.length || combine.length) ? 'is-on' : ''}>
              {t('Documents')}{(exTypes.length || exVouchers.length) ? ' •' : ''}
            </button>
          </ExportBar>
        </div>

        {showColPicker && (
          <div className="gl-colpick">
            <span className="flbl">{t('Pick fields')}</span>
            <div className="gl-col-grid">
              {ALL_COLUMNS.map((c) => (
                <label key={c.key} className="gl-cbx">
                  <input type="checkbox" checked={cols.includes(c.key)}
                    onChange={(e) => setCols((cur) => e.target.checked ? [...cur.filter((k) => k !== c.key), c.key].sort((a, b) => ALL_COLUMNS.findIndex(x => x.key === a) - ALL_COLUMNS.findIndex(x => x.key === b)) : cur.filter((k) => k !== c.key))} />
                  {t(c.label)}
                </label>
              ))}
            </div>
          </div>
        )}
        {showAdv && (
          <div className="gl-colpick gl-adv">
            <div className="gl-adv-sec">
              <span className="flbl">{t('Exclude document types')}</span>
              <div className="gl-col-grid">
                {vtypes.length === 0 && <span className="studio-hint">{t('Run the ledger to see which document types it contains.')}</span>}
                {vtypes.map((v) => (
                  <label key={v} className="gl-cbx">
                    <input type="checkbox" checked={exTypes.includes(v)}
                      onChange={(e) => setExTypes((cur) => e.target.checked ? [...cur, v] : cur.filter((x) => x !== v))} />
                    {v}
                  </label>
                ))}
              </div>
            </div>

            <div className="gl-adv-sec">
              <span className="flbl">{t('Exclude individual documents')}</span>
              <input className="gl-adv-input" placeholder={t('SRT-12-25-004, SRT-12-25-009 — comma separated')}
                value={exVouchers.join(', ')}
                onChange={(e) => setExVouchers(e.target.value.split(',').map((x) => x.trim()).filter(Boolean))} />
            </div>

            {(exTypes.length > 0 || exVouchers.length > 0) &&
              <div className="gl-adv-warn">
                {t('Opening balances are filtered too, so this ledger foots against itself but no longer against the account balance in ERPNext. Every export says so on its face.')}
              </div>}

            <div className="gl-adv-sec">
              <span className="flbl">{t('Columns from the source document')}</span>
              {vtypes.filter((v) => !exTypes.includes(v)).map((dt) => (
                <div key={dt} className="gl-adv-dt">
                  <strong>{dt}</strong>
                  <select value="" onChange={(e) => {
                    const f = e.target.value; if (!f) return;
                    setDocFields((cur) => ({ ...cur, [dt]: [...(cur[dt] || []), f] }));
                    setCols((cur) => [...cur, `${dt}::${f}`]);
                  }}>
                    <option value="">{t('Add a field…')}</option>
                    {(fieldOpts[dt] || [])
                      .filter((f: any) => !(docFields[dt] || []).includes(f.fieldname))
                      .map((f: any) => (
                        <option key={f.fieldname} value={f.fieldname}>
                          {f.label}{f.custom ? ' ★' : ''} — {f.fieldname}
                        </option>
                      ))}
                  </select>
                  <span className="gl-chips">
                    {(docFields[dt] || []).map((f) => (
                      <button key={f} className="gl-chip" onClick={() => {
                        setDocFields((cur) => ({ ...cur, [dt]: (cur[dt] || []).filter((x) => x !== f) }));
                        setCols((cur) => cur.filter((k) => k !== `${dt}::${f}`));
                        setCombine((cur) => cur.filter((k) => k !== `${dt}::${f}`));
                      }}>{(fieldOpts[dt] || []).find((o: any) => o.fieldname === f)?.label || f} ×</button>
                    ))}
                  </span>
                </div>
              ))}
            </div>

            <div className="gl-adv-sec">
              <span className="flbl">{t('Combine into one column')}</span>
              <div className="gl-col-grid">
                {[...ALL_COLUMNS.filter((c) => !c.num), ...dynCols.filter((d) => d.key !== '__combined__')].map((c) => (
                  <label key={c.key} className="gl-cbx">
                    <input type="checkbox" checked={combine.includes(c.key)}
                      onChange={(e) => setCombine((cur) => e.target.checked ? [...cur, c.key] : cur.filter((x) => x !== c.key))} />
                    {t(c.label)}
                  </label>
                ))}
              </div>
              {combine.length > 0 && (
                <div className="gl-adv-row">
                  <label><span className="flbl">{t('Heading')}</span>
                    <input value={combineLabel} onChange={(e) => setCombineLabel(e.target.value)} /></label>
                  <label><span className="flbl">{t('Separator')}</span>
                    <input value={combineSep} onChange={(e) => setCombineSep(e.target.value)} /></label>
                  <span className="studio-hint">{t('Empty fields are skipped, so no stray separators.')}</span>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {err && <div className="gl-msg gl-err">{err}</div>}
      {loading && <div className="gl-msg">{t('Loading…')}</div>}

      {!loading && data && (
        <div className="gl-result card">
          <div className="gl-result-head">
            {companyLabel} &nbsp;·&nbsp; {data.filters?.from_date} → {data.filters?.to_date} &nbsp;·&nbsp; {data.filters?.account_count} {t('Accounts')}
          </div>
          <div className="gl-table-wrap">
            <table className="gl-led">
              <thead>
                <tr>{colDefs.map((c) => <th key={c.key} className={c.num ? 'num' : ''}>{t(c.label)}</th>)}</tr>
              </thead>
              {data.accounts.map((b: any) => (
                <tbody key={b.account} id={'glacc-' + b.account} className="gl-block">
                  <tr className="gl-acc"><td colSpan={colDefs.length}>
                    {b.group_kind === 'party'
                      ? <span className="gl-party-head">
                          {b.party_type && <span className="gl-party-kind">{t(b.party_type)}</span>}
                          <ArName name={b.party} fallback={b.account_name} source={b.party_type || 'Supplier'} />
                        </span>
                      : <ArName name={b.account} fallback={(b.account_number ? b.account_number + ' - ' : '') + b.account_name} source="Account" />}
                  </td></tr>
                  <tr className="gl-op">
                    {colDefs.map((c) => (
                      <td key={c.key} className={c.num ? 'num' : ''}>
                        {c.key === colDefs[0].key ? t('Opening Balance') : c.key === 'balance' ? balText(b.opening_raw, decimals, balStyle) : ''}
                      </td>
                    ))}
                  </tr>
                  {b.transactions.map((tx: any, i: number) => (
                    <tr key={i}>
                      {colDefs.map((c) => (
                        <td key={c.key} className={c.num ? 'num' : ''}>
                          {c.key === 'balance'
                            ? <span className={balIsNeg(tx.balance_raw, balStyle) ? 'gl-neg' : undefined}>
                                {balText(tx.balance_raw, decimals, balStyle)}</span>
                            : c.key === 'voucher_no' && tx.voucher_type && tx.voucher_no
                            ? <a className="gl-doclink" href={docUrl(tx)} target="_blank" rel="noopener noreferrer"
                                title={t('Open {0}').replace('{0}', tx.voucher_type)}>{tx.voucher_no}</a>
                            : cellVal(tx, c.key)}
                        </td>
                      ))}
                    </tr>
                  ))}
                  <tr className="gl-st">
                    {colDefs.map((c) => (
                      <td key={c.key} className={c.num ? 'num' : ''}>
                        {c.key === colDefs[0].key ? t('Sub Total')
                          : c.key === 'debit' ? fmtD(b.sub_total.debit, decimals)
                          : c.key === 'credit' ? fmtD(b.sub_total.credit, decimals)
                          : c.key === 'balance' ? balText(b.closing_raw, decimals, balStyle) : ''}
                      </td>
                    ))}
                  </tr>
                </tbody>
              ))}
              <tbody>
                <tr className="gl-rt">
                  {colDefs.map((c) => (
                    <td key={c.key} className={c.num ? 'num' : ''}>
                      {c.key === colDefs[0].key ? t('REPORT TOTAL')
                        : c.key === 'debit' ? fmtD(data.report_total.debit, decimals)
                        : c.key === 'credit' ? fmtD(data.report_total.credit, decimals)
                        : c.key === 'balance' ? balText(data.report_total.balance_raw, decimals, balStyle) : ''}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
