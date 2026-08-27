const BASE = '/api/method/neotec_insight.neotec_insight.api';

function readCsrfToken(): string {
  const w = window as any;
  if (w.frappe && typeof w.frappe.csrf_token === 'string' && w.frappe.csrf_token && w.frappe.csrf_token !== 'None') return w.frappe.csrf_token;
  if (typeof w.csrf_token === 'string' && w.csrf_token && w.csrf_token !== 'None') return w.csrf_token;
  const meta = document.querySelector('meta[name="csrf-token"]') as HTMLMetaElement | null;
  return meta?.content || '';
}

let csrfFetchInFlight: Promise<string> | null = null;

async function refreshCsrfToken(): Promise<string> {
  if (csrfFetchInFlight) return csrfFetchInFlight;
  csrfFetchInFlight = (async () => {
    try {
      const res = await fetch(`${BASE}.report.get_csrf`, {
        method: 'GET',
        credentials: 'include',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      if (!res.ok) return '';
      const body = await res.json();
      const token = body?.message?.csrf_token || '';
      if (token) {
        (window as any).csrf_token = token;
        if (!(window as any).frappe) (window as any).frappe = {};
        (window as any).frappe.csrf_token = token;
      }
      return token;
    } catch {
      return '';
    } finally {
      // Allow another refresh on the next failure (with a tiny delay).
      setTimeout(() => { csrfFetchInFlight = null; }, 100);
    }
  })();
  return csrfFetchInFlight;
}

async function getCsrfToken(): Promise<string> {
  const t = readCsrfToken();
  if (t) return t;
  // No token from page-load injection — fetch one before the first request.
  return refreshCsrfToken();
}

async function doFetch(method: string, args: Record<string, any> | undefined, httpMethod: 'GET' | 'POST', token: string): Promise<Response> {
  const url = `${BASE}.${method}`;
  const headers: Record<string, string> = {
    'X-Frappe-CSRF-Token': token,
    'X-Requested-With': 'XMLHttpRequest',
  };
  const init: RequestInit = { method: httpMethod, headers, credentials: 'include' };
  let finalUrl = url;
  if (httpMethod === 'POST') {
    // v2.44.4 — POST mirrors GET semantics exactly (form-encoded, nulls
    // dropped, objects JSON-stringified) — Frappe's native frappe.call
    // encoding. A raw JSON body with nulls drew 400s on some sites.
    const form = new URLSearchParams();
    Object.entries(args || {}).forEach(([k, v]) => {
      if (v === undefined || v === null) return;
      form.set(k, typeof v === 'object' ? JSON.stringify(v) : String(v));
    });
    headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8';
    init.body = form.toString();
  } else if (args && Object.keys(args).length > 0) {
    headers['Content-Type'] = 'application/json';
    const qs = new URLSearchParams();
    Object.entries(args).forEach(([k, v]) => {
      if (v === undefined || v === null) return;
      qs.set(k, typeof v === 'object' ? JSON.stringify(v) : String(v));
    });
    finalUrl = `${url}?${qs.toString()}`;
  }
  return fetch(finalUrl, init);
}

async function isCsrfError(res: Response): Promise<boolean> {
  if (res.status !== 400 && res.status !== 403) return false;
  try {
    const cloned = res.clone();
    const txt = await cloned.text();
    return /CSRFToken/i.test(txt) || /Invalid Request/i.test(txt) || /csrf_token/i.test(txt);
  } catch {
    return false;
  }
}

async function call<T>(method: string, args?: Record<string, any>, httpMethod: 'GET' | 'POST' = 'GET'): Promise<T> {
  let token = await getCsrfToken();
  let res = await doFetch(method, args, httpMethod, token);

  // Retry once with a freshly-fetched token if it's a CSRF problem.
  if (!res.ok && await isCsrfError(res)) {
    const fresh = await refreshCsrfToken();
    if (fresh && fresh !== token) {
      res = await doFetch(method, args, httpMethod, fresh);
    }
  }

  if (!res.ok) {
    // v2.44.5 — surface the server's REAL message. Read the body once as
    // text (a second read after a failed .json() returns nothing), then
    // parse; fall back through _server_messages → exception's last line →
    // exc_type → raw body with tags stripped.
    let detail = '';
    const raw = await res.text().catch(() => '');
    try {
      const body = JSON.parse(raw);
      const msgs = body?._server_messages;
      if (typeof msgs === 'string') {
        const parsed = JSON.parse(msgs);
        const first = Array.isArray(parsed) ? parsed[0] : parsed;
        if (typeof first === 'string') {
          try { const inner = JSON.parse(first); detail = inner.message || inner.title || first; }
          catch { detail = first; }
        }
      }
      if (!detail && typeof body?.exception === 'string') {
        const lines = body.exception.trim().split('\n');
        detail = lines[lines.length - 1];
      }
      if (!detail) detail = body?.exc_type || '';
      if (!detail && typeof body?.message === 'string') detail = body.message;
    } catch { /* not JSON */ }
    if (!detail) detail = raw.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 300);
    throw new Error(`API ${method} failed: ${res.status}${detail ? ' — ' + detail : ''}`);
  }
  const body = await res.json();
  return body.message as T;
}

/** v2.54.0 — post a print document to the server renderer and save the PDF it
 *  returns. The response is a binary download, not the usual JSON envelope, so
 *  this bypasses `call()` and reads a blob. */
async function renderPdf(html: string, filename: string, orientation = 'landscape', pageSize = 'A4') {
  const token = await getCsrfToken();
  const form = new URLSearchParams();
  form.set('html', html);
  form.set('filename', filename);
  form.set('orientation', orientation);
  form.set('page_size', pageSize);
  const res = await fetch(`${BASE}.pdf.render_pdf`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
      'X-Requested-With': 'XMLHttpRequest',
      ...(token ? { 'X-Frappe-CSRF-Token': token } : {}),
    },
    body: form.toString(),
  });
  if (!res.ok) {
    let msg = '';
    try {
      const txt = await res.text();
      msg = (txt.match(/<pre>([\s\S]*?)<\/pre>/) || [])[1]
        || (JSON.parse(txt)?._server_messages ? JSON.parse(JSON.parse(txt)._server_messages)[0] : '');
      if (msg) { try { msg = JSON.parse(msg).message || msg; } catch { /* plain text */ } }
    } catch { /* non-text body */ }
    throw new Error(msg || `PDF rendering failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${filename.replace(/[^A-Za-z0-9._\- ]+/g, '_').slice(0, 120)}.pdf`;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

export const api = {
  renderPdf,
  listReports: () => call<any[]>('report.list_reports'),
  getReport: (report: string) => call<any>('report.get_report', { report }),
  saveReport: (payload: any) => call<any>('report.save_report', { payload: JSON.stringify(payload) }, 'POST'),

  // ERP masters (Run-tab filter dropdowns)
  listCompanies: () => call<any[]>('report.list_companies'),
  listCostCenters: (company?: string, search = '', limit = 100) =>
    call<any[]>('report.list_cost_centers', { company: company || '', search, limit }),
  listProjects: (company?: string, search = '', limit = 100, status?: string) =>
    call<any[]>('report.list_projects', { company: company || '', search, limit, status: status ?? 'Open' }),
  listDepartments: (company?: string, search = '', limit = 100) =>
    call<any[]>('report.list_departments', { company: company || '', search, limit }),
  listBranches: (search = '', limit = 100) =>
    call<any[]>('report.list_branches', { search, limit }),
  listFiscalYears: () => call<any[]>('report.list_fiscal_years'),

  // Trial Balance + Balance Sheet (v1.8)
  runReportRowDrill: (args: {
    report: string;
    row_key: string;
    fiscal_year: number;
    month_from: number;
    month_to: number;
    cost_center?: string | string[] | null;
    project?: string | string[] | null;
    department?: string | string[] | null;
    branch?: string | string[] | null;
    company?: string | null;
    period_mode?: 'fiscal_year' | 'date_range';
    period_from_date?: string | null;
    period_to_date?: string | null;
    use_cache?: number;
  }) => {
    const serial = (v: string | string[] | null | undefined) =>
      Array.isArray(v) ? (v.length ? JSON.stringify(v) : null) : (v ?? null);
    return call<any>('report.run_report_row_drill', {
      ...args,
      cost_center: serial(args.cost_center),
      project: serial(args.project),
      department: serial(args.department),
      branch: serial(args.branch),
    });
  },

  // GL drill-through: raw GL Entry rows behind any Insight value (v1.9.67)
  glDrillEntries: (args: {
    report: string;
    row_key: string;
    account?: string | null;
    supplier?: string | string[] | null;
    customer?: string | string[] | null;
    fiscal_year: number;
    month_from: number;
    month_to: number;
    cost_center?: string | string[] | null;
    project?: string | string[] | null;
    department?: string | string[] | null;
    branch?: string | string[] | null;
    company?: string | null;
    period_mode?: 'fiscal_year' | 'date_range';
    period_from_date?: string | null;
    period_to_date?: string | null;
    limit?: number;
  }) => {
    const serial = (v: string | string[] | null | undefined) =>
      Array.isArray(v) ? (v.length ? JSON.stringify(v) : null) : (v ?? null);
    return call<any>('report.gl_drill_entries', {
      ...args,
      cost_center: serial(args.cost_center),
      project: serial(args.project),
      department: serial(args.department),
      branch: serial(args.branch),
      supplier: serial(args.supplier),
      customer: serial(args.customer),
    });
  },

  dimensionOptions: (kind: string, company?: string | null) =>
    call<any[]>('ai.dimension_options', { kind, company: company ?? null }),

  exportConfiguration: (sections?: string[]) =>
    call<any>('report.export_configuration', sections && sections.length ? { sections: JSON.stringify(sections) } : {}),
  configSectionCounts: () => call<any>('report.config_section_counts'),
  configAreas: () => call<{ label: string; doctypes: string[] }[]>('report.config_areas'),
  importConfiguration: (payload: any, mode: 'replace' | 'merge' = 'replace') =>
    call<any>('report.import_configuration', { payload: JSON.stringify(payload), mode }, 'POST'),

  // ── Report Studio (no-code BI) ──
  studioListSources: (search = '') => call<any[]>('studio.list_sources', { search }),
  studioListFields: (doctype: string) => call<any[]>('studio.list_fields', { doctype }),
  studioListLinkFields: (doctype: string) => call<any[]>('studio.list_link_fields', { doctype }),
  studioFieldValues: (doctype: string, field: string, search = '') =>
    call<any[]>('studio.field_values', { doctype, field, search }),
  studioRunQuery: (config: any) => call<any>('studio.run_query', { config: JSON.stringify(config) }, 'POST'),
  studioAiBuild: (doctype: string, prompt: string) => call<any>('studio.ai_build', { doctype, prompt }, 'POST'),
  studioSaveReport: (report: any) => call<any>('studio.save_report', { report: JSON.stringify(report) }, 'POST'),
  studioListReports: () => call<any[]>('studio.list_reports'),
  studioLoadReport: (slug: string) => call<any>('studio.load_report', { slug }),

  financialHealth: (company?: string | null, fiscalYear?: string | null) =>
    call<any>('health.financial_health', { company: company ?? null, fiscal_year: fiscalYear ?? null }),
  healthBreakdown: (bucket: string, company?: string | null) =>
    call<any>('health.health_breakdown', { bucket, company: company ?? null }),
  healthAiAnalysis: (company?: string | null, fiscalYear?: string | null, lang = 'en') =>
    call<any>('health.health_ai_analysis', { company: company ?? null, fiscal_year: fiscalYear ?? null, lang }),
  scanAccountTypes: (company?: string | null) =>
    call<any>('health_setup.scan_account_types', { company: company ?? null }),
  applyAccountTypes: (changes: any[], company?: string | null) =>
    call<any>('health_setup.apply_account_types', { changes: JSON.stringify(changes), company: company ?? null }, 'POST'),
  listEbitdaAddbacks: (company?: string | null) =>
    call<any>('health_setup.list_ebitda_addbacks', { company: company ?? null }),
  saveEbitdaAddbacks: (changes: any[], company?: string | null) =>
    call<any>('health_setup.save_ebitda_addbacks', { changes: JSON.stringify(changes), company: company ?? null }, 'POST'),

  vatReturn: (company?: string | null, fromDate?: string | null, toDate?: string | null) =>
    call<any>('vat.vat_return', { company: company ?? null, from_date: fromDate ?? null, to_date: toDate ?? null }),
  vatBoxDrill: (company: string | null, fromDate: string, toDate: string, box: number | string) =>
    call<any>('vat.vat_box_drill', { company: company ?? null, from_date: fromDate, to_date: toDate, box }),

  listVatAdjustments: (company: string | null, fromDate: string, toDate: string) =>
    call<any[]>('vat.list_vat_adjustments', { company: company ?? null, from_date: fromDate, to_date: toDate }),
  saveVatAdjustment: (a: any) => call<any>('vat.save_vat_adjustment', a, 'POST'),
  deleteVatAdjustment: (name: string) => call<any>('vat.delete_vat_adjustment', { name }, 'POST'),
  clearVatAdjustment: (company: string | null, fromDate: string, toDate: string, voucherType: string, voucherNo: string) =>
    call<any>('vat.clear_vat_adjustment', { company: company ?? null, from_date: fromDate, to_date: toDate, voucher_type: voucherType, voucher_no: voucherNo }, 'POST'),
  vatFindVouchers: (company: string | null, voucherType: string, query: string) =>
    call<any[]>('vat.find_vouchers', { company: company ?? null, voucher_type: voucherType, query }),

  arApAgeing: (company: string | null, asOf: string, partyType: string, basedOn: string, mode: string, slabs: string, topN: number, excludeParties?: string[], allocation?: string, includeParties?: string[]) =>
    call<any>('ageing.ar_ap_ageing', { company: company ?? null, as_of: asOf, party_type: partyType, based_on: basedOn, mode, slabs, top_n: topN, exclude_parties: JSON.stringify(excludeParties || []), allocation: allocation || 'actual', include_parties: JSON.stringify(includeParties || []) }),
  partyTree: (partyType: string) => call<any[]>('ageing.party_tree', { party_type: partyType }),
  ageingListParties: (company: string | null, partyType: string, query: string) =>
    call<any[]>('ageing.list_parties', { company: company ?? null, party_type: partyType, query }),
  billwise: (company: string | null, partyType: string, parties: string[], asOf: string, basedOn: string, mode: string, slabs: string) =>
    call<any>('ageing.billwise', { company: company ?? null, party_type: partyType, parties: JSON.stringify(parties), as_of: asOf, based_on: basedOn, mode, slabs }, 'POST'),
  listGlAccounts: (company: string | null) => call<any[]>('packs.list_gl_accounts', { company: company ?? null }),

  cashFlow: (company?: string | null, fromDate?: string | null, toDate?: string | null) =>
    call<any>('cashflow.cash_flow', { company: company ?? null, from_date: fromDate ?? null, to_date: toDate ?? null }),
  companyBranding: () => call<any[]>('cashflow.company_branding'),
  studioTimeIntel: (config: any) => call<any>('studio.time_intelligence', { config: JSON.stringify(config) }, 'POST'),

  listDatasets: () => call<any[]>('datasets.list_datasets'),
  saveDataset: (dataset: any) => call<any>('datasets.save_dataset', { dataset: JSON.stringify(dataset) }, 'POST'),
  loadDataset: (slug: string) => call<any>('datasets.load_dataset', { slug }),
  deleteDataset: (slug: string) => call<any>('datasets.delete_dataset', { slug }, 'POST'),
  runDataset: (slug: string, dimension?: string | null, measures?: string[], filters?: any[]) =>
    call<any>('datasets.run_dataset', { slug, dimension: dimension ?? null, measures: JSON.stringify(measures || []), filters: JSON.stringify(filters || []) }, 'POST'),

  previewDataset: (baseDoctype: string, config: any, dimension?: string | null) =>
    call<any>('datasets.preview_dataset', { base_doctype: baseDoctype, config: JSON.stringify(config), dimension: dimension ?? null }, 'POST'),
  getClassification: (company?: string | null) => call<any>('classify.get_classification', { company: company ?? null }),

  // VAT settings — everything governing a return, read in one call.
  vatSettings: (company?: string | null) => call<any>('vat_settings.vat_settings', { company: company ?? null }),
  saveGtplRule: (company: string | null, payload: any) =>
    call<any>('vat_settings.save_gtpl_rule', { company: company ?? null, payload: JSON.stringify(payload) }, 'POST'),
  setGtplRuleActive: (name: string, active: boolean) =>
    call<any>('vat_settings.set_gtpl_rule_active', { name, active: active ? 1 : 0 }, 'POST'),
  deleteGtplRule: (name: string) => call<any>('vat_settings.delete_gtpl_rule', { name }, 'POST'),
  paymentOrders: (company?: string | null) => call<any[]>('vat_settings.payment_orders', { company: company ?? null }),
  savePaymentOrder: (company: string | null, payload: any) =>
    call<any>('vat_settings.save_payment_order', { company: company ?? null, payload: JSON.stringify(payload) }, 'POST'),
  deletePaymentOrder: (name: string) => call<any>('vat_settings.delete_payment_order', { name }, 'POST'),
  linkOptions: (doctype: string, company: string | null, parent?: string | null, query?: string) =>
    call<any>('vat_settings.link_options', { doctype, company: company ?? null, parent: parent ?? null, query: query || '' }),
  excludeFromVat: (company: string | null, account: string, restore = false) =>
    call<any>('vat_settings.exclude_from_vat', { company: company ?? null, account, restore: restore ? 1 : 0 }, 'POST'),
  deferredInvoices: (company: string | null, asOf?: string) =>
    call<any>('vat_settings.deferred_invoices', { company: company ?? null, as_of: asOf ?? null }),
  gtplPreview: (company: string | null, fromDate: string, toDate: string) =>
    call<any>('gtpl.gtpl_preview', { company: company ?? null, from_date: fromDate, to_date: toDate }),
  applyGtplAdjustments: (company: string | null, fromDate: string, toDate: string) =>
    call<any>('gtpl.apply_gtpl_adjustments', { company: company ?? null, from_date: fromDate, to_date: toDate }, 'POST'),
  saveClassification: (company: string | null, changes: Record<string, string>) =>
    call<any>('classify.save_classification', { company: company ?? null, changes: JSON.stringify(changes) }, 'POST'),
  labelSummary: (company: string | null, fromDate: string, toDate: string) =>
    call<any>('classify.label_summary', { company: company ?? null, from_date: fromDate, to_date: toDate }),

  listSchedules: (report?: string) => call<any[]>('datasets.list_schedules', { report: report ?? null }),
  saveSchedule: (schedule: any) => call<any>('datasets.save_schedule', { schedule: JSON.stringify(schedule) }, 'POST'),
  deleteSchedule: (name: string) => call<any>('datasets.delete_schedule', { name }, 'POST'),
  runScheduleNow: (name: string) => call<any>('datasets.run_schedule_now', { name }, 'POST'),

  getMenu: () => call<any>('navmenu.get_menu'),
  saveMenu: (menu: any) => call<any>('navmenu.save_menu', { menu: JSON.stringify(menu) }, 'POST'),
  resetMenu: () => call<any>('navmenu.reset_menu', {}, 'POST'),

  gstSummary: (company: string | null, fromDate: string, toDate: string) =>
    call<any>('gst.gst_summary', { company: company ?? null, from_date: fromDate, to_date: toDate }),
  gstHeadDrill: (company: string | null, fromDate: string, toDate: string, side: string, head: string) =>
    call<any>('gst.gst_head_drill', { company: company ?? null, from_date: fromDate, to_date: toDate, side, head }),
  listPacks: () => call<any[]>('packs.list_packs'),
  listSheetTypes: () => call<any[]>('packs.list_sheet_types'),
  loadPack: (slug: string) => call<any>('packs.load_pack', { slug }),
  savePack: (pack: any) => call<any>('packs.save_pack', { pack: JSON.stringify(pack) }, 'POST'),
  deletePack: (slug: string) => call<any>('packs.delete_pack', { slug }, 'POST'),

  zakatEstimate: (company?: string | null, fromDate?: string | null, toDate?: string | null, calendar?: string) =>
    call<any>('zakat.zakat_estimate', { company: company ?? null, from_date: fromDate ?? null, to_date: toDate ?? null, calendar: calendar || 'hijri' }),

  runTrialBalancePivot: (args: {
    report: string; company: string; fiscal_year: string | number;
    as_of_date: string; pivot_by: string;
    finance_book?: string | null;
    show_group_accounts?: number; show_zero_values?: number;
    presentation_currency?: string | null; use_cache?: number;
  }) => call<any>('report.run_trial_balance_pivot', args),

  runBalanceSheetPivot: (args: {
    report: string; company: string; as_of_date: string; pivot_by: string;
    finance_book?: string | null;
    show_group_accounts?: number; show_zero_values?: number;
    presentation_currency?: string | null; use_cache?: number;
  }) => call<any>('report.run_balance_sheet_pivot', args),

  runBalanceSheetComboPivot: (args: {
    report: string; company: string; as_of_date: string; dim1: string; dim2: string;
    finance_book?: string | null;
    show_group_accounts?: number; show_zero_values?: number;
    presentation_currency?: string | null; use_cache?: number;
  }) => call<any>('report.run_balance_sheet_combo_pivot', args),

  runPnlStatement: (args: {
    report: string;
    company: string;
    from_date: string;
    to_date: string;
    cost_center?: string | string[] | null;
    project?: string | string[] | null;
    department?: string | string[] | null;
    branch?: string | string[] | null;
    finance_book?: string | null;
    dimension_filters?: Record<string, string | string[]>;
    show_group_accounts?: number;
    show_zero_values?: number;
    presentation_currency?: string | null;
    use_cache?: number;
  }) => call<any>('report.run_pnl_statement', {
    ...args,
    dimension_filters: args.dimension_filters ? JSON.stringify(args.dimension_filters) : undefined,
  }),

  runPnlStatementPivot: (args: {
    report: string;
    company: string;
    from_date: string;
    to_date: string;
    pivot_by: string;
    finance_book?: string | null;
    show_group_accounts?: number;
    show_zero_values?: number;
    presentation_currency?: string | null;
    use_cache?: number;
  }) => call<any>('report.run_pnl_statement_pivot', args),

  // v2.65.0 — P&L Statement split into period columns (Granularity).
  runPnlStatementPeriods: (args: {
    report: string; company: string; from_date: string; to_date: string;
    granularity: string;
    cost_center?: string | string[] | null; project?: string | string[] | null; department?: string | string[] | null;
    branch?: string | string[] | null; finance_book?: string | null;
    dimension_filters?: Record<string, string | string[]>; show_group_accounts?: number; show_zero_values?: number;
    presentation_currency?: string | null;
  }) => call<any>('report.run_pnl_statement_periods', args),

  runPnlStatementComboPivot: (args: {
    report: string;
    company: string;
    from_date: string;
    to_date: string;
    dim1: string;
    dim2: string;
    finance_book?: string | null;
    show_group_accounts?: number;
    show_zero_values?: number;
    presentation_currency?: string | null;
    use_cache?: number;
  }) => call<any>('report.run_pnl_statement_combo_pivot', args),

  listReportFilterOptions: (company?: string) =>
    call<any>('report.list_report_filter_options', company ? { company } : {}),

  runTrialBalance: (args: {
    report: string;
    company: string;
    fiscal_year: string | number;
    as_of_date: string;
    from_date?: string | null;
    cost_center?: string | string[] | null;
    project?: string | string[] | null;
    department?: string | string[] | null;
    branch?: string | string[] | null;
    root_types?: string[];
    finance_book?: string | null;
    dimension_filters?: Record<string, string | string[]>;
    show_group_accounts?: number;
    show_zero_values?: number;
    presentation_currency?: string | null;
    use_cache?: number;
  }) => {
    // v1.9.58 — multi-select natives serialised as JSON arrays.
    const serial = (v: string | string[] | null | undefined) =>
      Array.isArray(v) ? (v.length ? JSON.stringify(v) : null) : (v ?? null);
    return call<any>('report.run_trial_balance', {
      ...args,
      cost_center: serial(args.cost_center),
      project: serial(args.project),
      department: serial(args.department),
      branch: serial(args.branch),
      dimension_filters: args.dimension_filters ? JSON.stringify(args.dimension_filters) : undefined,
    });
  },

  runTrialBalanceParties: (args: {
    report: string;
    account: string;
    company: string;
    fiscal_year: string | number;
    as_of_date: string;
    cost_center?: string | string[] | null;
    project?: string | string[] | null;
    presentation_currency?: string | null;
    use_cache?: number;
  }) => {
    const serial = (v: string | string[] | null | undefined) =>
      Array.isArray(v) ? (v.length ? JSON.stringify(v) : null) : (v ?? null);
    return call<any>('report.run_trial_balance_parties', {
      ...args,
      cost_center: serial(args.cost_center),
      project: serial(args.project),
    });
  },

  runBalanceSheet: (args: {
    report: string;
    company: string;
    as_of_date: string;
    prior_as_of_date?: string | null;
    cost_center?: string | string[] | null;
    project?: string | string[] | null;
    department?: string | string[] | null;
    branch?: string | string[] | null;
    finance_book?: string | null;
    dimension_filters?: Record<string, string | string[]>;
    show_group_accounts?: number;
    show_zero_values?: number;
    show_unclosed_pl?: number;
    presentation_currency?: string | null;
    use_cache?: number;
  }) => {
    const serial = (v: string | string[] | null | undefined) =>
      Array.isArray(v) ? (v.length ? JSON.stringify(v) : null) : (v ?? null);
    return call<any>('report.run_balance_sheet', {
      ...args,
      cost_center: serial(args.cost_center),
      project: serial(args.project),
      department: serial(args.department),
      branch: serial(args.branch),
      dimension_filters: args.dimension_filters ? JSON.stringify(args.dimension_filters) : undefined,
    });
  },

  runReportDimensionPivot: (args: {
    report: string;
    fiscal_year: number;
    month_from: number;
    month_to: number;
    pivot_by: string;
    company?: string | null;
    use_cache?: number;
  }) => call<any>('report.run_report_dimension_pivot', args),

  /* ─── v1.9.63 — Combo view + multi-period TB/BS ───────────────────── */

  /** Combo view dispatcher: one row per (report row × dim1 × dim2 tuple).
   *  Works on P&L, Trial Balance, and Balance Sheet reports. The server
   *  detects report_type and routes to the right engine. */
  runComboReport: (args: {
    report: string;
    dim1: string;
    dim2: string;
    fiscal_year?: number;
    month_from?: number;
    month_to?: number;
    company?: string | null;
    as_of_date?: string | null;
    from_date?: string | null;
    cost_center?: string | string[] | null;
    project?: string | string[] | null;
    department?: string | string[] | null;
    branch?: string | string[] | null;
    finance_book?: string | null;
    dimension_filters?: Record<string, string | string[]> | null;
    fy_start_month_override?: number | null;
    /** v1.9.65 — 'fiscal_year' (default) or 'date_range'. */
    period_mode?: 'fiscal_year' | 'date_range';
    /** v1.9.65 — ISO date 'YYYY-MM-DD'. Used when period_mode='date_range'. */
    period_from_date?: string | null;
    period_to_date?: string | null;
    use_cache?: number;
  }) => {
    const serial = (v: string | string[] | null | undefined) =>
      Array.isArray(v) ? (v.length ? JSON.stringify(v) : null) : (v ?? null);
    return call<any>('report.run_combo_report', {
      ...args,
      cost_center: serial(args.cost_center),
      project: serial(args.project),
      department: serial(args.department),
      branch: serial(args.branch),
      dimension_filters: args.dimension_filters ? JSON.stringify(args.dimension_filters) : undefined,
    });
  },

  /** Multi-period Trial Balance — one closing-balance column per period
   *  boundary (month, quarter, half, year). */
  runTrialBalanceMultiPeriod: (args: {
    report: string;
    company: string;
    fiscal_year: string | number;
    granularity: 'month' | 'quarter' | 'half' | 'year';
    cost_center?: string | string[] | null;
    project?: string | string[] | null;
    department?: string | string[] | null;
    branch?: string | string[] | null;
    root_types?: string[];
    finance_book?: string | null;
    dimension_filters?: Record<string, string | string[]>;
    show_group_accounts?: number;
    show_zero_values?: number;
    presentation_currency?: string | null;
    fy_start_month_override?: number | null;
    use_cache?: number;
  }) => {
    const serial = (v: string | string[] | null | undefined) =>
      Array.isArray(v) ? (v.length ? JSON.stringify(v) : null) : (v ?? null);
    return call<any>('report.run_trial_balance_multi_period', {
      ...args,
      cost_center: serial(args.cost_center),
      project: serial(args.project),
      department: serial(args.department),
      branch: serial(args.branch),
      dimension_filters: args.dimension_filters ? JSON.stringify(args.dimension_filters) : undefined,
    });
  },

  /** Multi-period Balance Sheet — delegates to the TB multi-period
   *  endpoint with root_types pre-set to Asset/Liability/Equity. */
  runBalanceSheetMultiPeriod: (args: {
    report: string;
    company: string;
    fiscal_year: string | number;
    granularity: 'month' | 'quarter' | 'half' | 'year';
    cost_center?: string | string[] | null;
    project?: string | string[] | null;
    department?: string | string[] | null;
    branch?: string | string[] | null;
    finance_book?: string | null;
    dimension_filters?: Record<string, string | string[]>;
    show_group_accounts?: number;
    show_zero_values?: number;
    presentation_currency?: string | null;
    fy_start_month_override?: number | null;
    use_cache?: number;
  }) => {
    const serial = (v: string | string[] | null | undefined) =>
      Array.isArray(v) ? (v.length ? JSON.stringify(v) : null) : (v ?? null);
    return call<any>('report.run_balance_sheet_multi_period', {
      ...args,
      cost_center: serial(args.cost_center),
      project: serial(args.project),
      department: serial(args.department),
      branch: serial(args.branch),
      dimension_filters: args.dimension_filters ? JSON.stringify(args.dimension_filters) : undefined,
    });
  },

  runReport: (args: {
    report: string;
    fiscal_year: number;
    month_from: number;
    month_to: number;
    segment: string;
    cost_center?: string | string[] | null;
    project?: string | string[] | null;
    department?: string | string[] | null;
    branch?: string | string[] | null;
    prior_years: number;
    comparison_mode: string;
    granularity?: string;
    compare_to_book?: string | null;
    use_cache?: number;
    /** v1.9.52/v1.9.58 — custom Accounting Dimensions as a fieldname→value
     *  dict. Values may be string OR string[] (multi-select). Server
     *  validates every key against the configured dimension set. */
    dimension_filters?: Record<string, string | string[]> | null;
    /** v1.9.60 — calendar month (1..12) to override the company's
     *  configured FY-start for this run. Used for group reporting (a
     *  KSA Jan-start subsidiary viewing its books as Apr-Mar for parent
     *  reporting). Null/undefined = use the company's configured calendar. */
    fy_start_month_override?: number | null;
    /** v1.9.65 — 'fiscal_year' (default, preserves all prior behavior)
     *  or 'date_range'. In date_range mode period_from_date +
     *  period_to_date are used directly; FY / month range / override
     *  are ignored. */
    period_mode?: 'fiscal_year' | 'date_range';
    /** v1.9.65 — ISO date 'YYYY-MM-DD'. Required when period_mode='date_range'. */
    period_from_date?: string | null;
    period_to_date?: string | null;
  }) => {
    // v1.9.58 — multi-select: serialise arrays as JSON so they survive
    // form-encoding through Frappe's whitelist call boundary. Scalars
    // pass through unchanged for backward compatibility.
    const serial = (v: string | string[] | null | undefined) =>
      Array.isArray(v) ? (v.length ? JSON.stringify(v) : null) : (v ?? null);
    const payload: any = {
      ...args,
      cost_center: serial(args.cost_center),
      project: serial(args.project),
      department: serial(args.department),
      branch: serial(args.branch),
    };
    if (args.dimension_filters) {
      payload.dimension_filters = JSON.stringify(args.dimension_filters);
    }
    return call<any>('report.run_report', payload);
  },

  reportIntegrity: (args: {
    report: string;
    fiscal_year: number;
    month_from: number;
    month_to: number;
    period_mode?: 'fiscal_year' | 'date_range';
    period_from_date?: string | null;
    period_to_date?: string | null;
    fy_start_month_override?: number | null;
  }) => call<any>('report.report_integrity', args),

  saveBudgetCells: (book: string, cells: any[]) =>
    call<any>('report.save_budget_cells', { book, cells: JSON.stringify(cells) }, 'POST'),

  listBudgetBooks: (report: string, fiscalYear?: number) =>
    call<any[]>('report.list_budget_books', fiscalYear != null ? { report, fiscal_year: fiscalYear } : { report }),
  getBudgetBook: (book: string) =>
    call<any>('report.get_budget_book', { book }),
  budgetImport: (payload: any) => call<any>('budget_import.budget_import', { report: payload.report, fiscal_year: payload.fiscal_year, dimension_type: payload.dimension_type, custom_dimension_fieldname: payload.custom_dimension_fieldname ?? null, rows: JSON.stringify(payload.rows) }, 'POST'),
  createBudgetBook: (payload: any) =>
    call<any>('report.create_budget_book', { payload: JSON.stringify(payload) }, 'POST'),
  updateBudgetBook: (book: string, payload: any) =>
    call<any>('report.update_budget_book', { book, payload: JSON.stringify(payload) }, 'POST'),
  deleteBudgetBook: (book: string) =>
    call<any>('report.delete_budget_book', { book }, 'POST'),
  rollupToTotal: (report: string, fiscalYear: number) =>
    call<any>('report.rollup_to_total', { report, fiscal_year: fiscalYear }, 'POST'),

  // Saved Dashboards (v1.7)
  listDashboards: (report?: string) =>
    call<any[]>('report.list_dashboards', report ? { report } : {}),
  getDashboard: (dashboard: string) =>
    call<any>('report.get_dashboard', { dashboard }),
  saveDashboard: (payload: any) =>
    call<any>('report.save_dashboard', { payload: JSON.stringify(payload) }, 'POST'),
  deleteDashboard: (dashboard: string) =>
    call<any>('report.delete_dashboard', { dashboard }, 'POST'),

  listAccountMappings: (report: string) => call<any[]>('report.list_account_mappings', { report }),
  listAvailableAccounts: (report: string, search = '', limit = 50, includeGroups = 1) =>
    call<any[]>('report.list_available_accounts', { report, search, limit, include_groups: includeGroups }),

  // Full chart of accounts for a company (tree-ordered) — GL tree picker.
  accountTree: (company?: string | null) =>
    call<any[]>('report.account_tree', { company: company || null }),

  // Hierarchical P&L drill: primary dim → secondary dim → section → account.
  plHierarchy: (args: {
    report?: string | null;
    company?: string | null;
    fiscal_year: number;
    month_from: number;
    month_to: number;
    primary_dim: string;
    secondary_dim?: string;
    period_mode?: 'fiscal_year' | 'date_range';
    period_from_date?: string | null;
    period_to_date?: string | null;
    fy_start_month_override?: number | null;
    cost_center?: string[] | null;
    project?: string[] | null;
    finance_book?: string | null;
    dimension_filters?: any;
  }) => call<any>('report.pl_hierarchy', args),

  // General Ledger (v1.9.65 feature add) — grouped, ERP-style.
  voucherFieldOptions: (voucherTypes: string[]) =>
    call<any>('report.voucher_field_options', { voucher_types: JSON.stringify(voucherTypes) }),
  generalLedger: (args: {
    company?: string | null;
    accounts: string[];
    from_date: string;
    to_date: string;
    cost_center?: string | string[] | null;
    project?: string | string[] | null;
    department?: string | string[] | null;
    branch?: string | string[] | null;
    supplier?: string | string[] | null;
    customer?: string | string[] | null;
    show_without_transactions?: number;
    show_zero_closing?: number;
    show_only_opening?: number;
    split_by_against?: number;
    report?: string | null;
    group_by?: string | null;
    with_description?: number;
    exclude_voucher_types?: string[] | null;
    exclude_vouchers?: string[] | null;
    doc_fields?: string | null;
  }) => {
    const serial = (v: string | string[] | null | undefined) =>
      Array.isArray(v) ? (v.length ? JSON.stringify(v) : null) : (v ?? null);
    return call<any>('report.general_ledger', {
      ...args,
      accounts: JSON.stringify(args.accounts || []),
      cost_center: serial(args.cost_center),
      project: serial(args.project),
      department: serial(args.department),
      branch: serial(args.branch),
    });
  },
  // v2.63.0 — driver capture & audit evidence
  allocationCapturePreview: (rule: string, year: number, months?: number[] | null) =>
    call<any>('allocation.capture_preview', { rule, year, months: JSON.stringify(months || null) }),
  allocationCaptureCommit: (rule: string, year: number, months?: number[] | null, freeze?: boolean) =>
    call<any>('allocation.capture_commit',
      { rule, year, months: JSON.stringify(months || null), freeze: freeze ? 1 : 0 }, 'POST'),
  allocationUnassigned: (rule: string, year: number) =>
    call<any>('allocation.unassigned_employees', { rule, year }),
  allocationEvidence: (rule: string, cost_center: string, year: number, month: number) =>
    call<any>('allocation.get_evidence', { rule, cost_center, year, month }),
  allocationFreeze: (rule: string, year: number, months: number[], frozen: boolean) =>
    call<any>('allocation.freeze_months',
      { rule, year, months: JSON.stringify(months), frozen: frozen ? 1 : 0 }, 'POST'),

  // v2.58.0 — cost pool allocation
  allocationRules: (company?: string | null) =>
    call<any[]>('allocation.list_rules', { company: company || null }),
  allocationCostCenters: (company?: string | null) =>
    call<{ name: string; label: string }[]>('allocation.list_cost_centers', { company: company || null }),
  allocationGrid: (rule: string, year: number, company?: string | null) =>
    call<any>('allocation.get_grid', { rule, year, company: company || null }),
  allocationSaveGrid: (rule: string, year: number, company: string | null, cells: any,
                       manual_pool: any, basis: any, remove?: string[]) =>
    call<any>('allocation.save_grid', {
      rule, year, company: company || null,
      cells: JSON.stringify(cells || {}), manual_pool: JSON.stringify(manual_pool || {}),
      basis: JSON.stringify(basis || {}), remove: JSON.stringify(remove || []),
    }, 'POST'),
  allocationRun: (rule: string, year: number, company?: string | null) =>
    call<any>('allocation.run', { rule, year, company: company || null }),

  // v2.86.0 — Cash Flow Forecast. Fully separate feature, own module —
  // NOT grouped under allocationXxx naming even though the call<> pattern
  // is identical, so nobody skimming this file mistakes it for part of
  // the allocation feature.
  cashFlowForecastLines: (includeInactive?: boolean) =>
    call<any[]>('cash_flow_forecast.list_lines', { include_inactive: includeInactive ? 1 : 0 }),
  cashFlowForecastSaveLine: (line: any) =>
    call<any>('cash_flow_forecast.save_line', { line: JSON.stringify(line) }, 'POST'),
  cashFlowForecastDeleteLine: (name: string) =>
    call<any>('cash_flow_forecast.delete_line', { name }, 'POST'),
  cashFlowForecastOverrides: (line?: string | null) =>
    call<any[]>('cash_flow_forecast.list_overrides', { line: line || null }),
  cashFlowForecastSaveOverride: (line: string, voucherType: string, voucherNo: string, note: string) =>
    call<any>('cash_flow_forecast.save_override',
      { line, voucher_type: voucherType, voucher_no: voucherNo, note }, 'POST'),
  cashFlowForecastDeleteOverride: (name: string) =>
    call<any>('cash_flow_forecast.delete_override', { name }, 'POST'),
  cashFlowForecastPreviewImport: (fileBase64: string, sheetName?: string | null) =>
    call<any>('cash_flow_forecast.preview_classified_history_import',
      { file_base64: fileBase64, sheet_name: sheetName || null }, 'POST'),
  cashFlowForecastCommitImport: (fileBase64: string, sheetName?: string | null) =>
    call<any>('cash_flow_forecast.commit_classified_history_import',
      { file_base64: fileBase64, sheet_name: sheetName || null }, 'POST'),
  cashFlowForecastPreviewStatementImport: (fileBase64: string, sheetName?: string | null, fiscalYear?: number | null) =>
    call<any>('cash_flow_forecast.preview_statement_template_import',
      { file_base64: fileBase64, sheet_name: sheetName || null, fiscal_year: fiscalYear || null }, 'POST'),
  cashFlowForecastCommitStatementImport: (fileBase64: string, sheetName?: string | null, fiscalYear?: number | null) =>
    call<any>('cash_flow_forecast.commit_statement_template_import',
      { file_base64: fileBase64, sheet_name: sheetName || null, fiscal_year: fiscalYear || null }, 'POST'),
  cashFlowForecastBudgetGrid: (fiscalYear: number, company?: string | null) =>
    call<any>('cash_flow_forecast.get_budget_grid', { fiscal_year: fiscalYear, company: company || null }),
  cashFlowForecastSaveBudgetGrid: (fiscalYear: number, cells: any, company?: string | null) =>
    call<any>('cash_flow_forecast.save_budget_grid',
      { fiscal_year: fiscalYear, cells: JSON.stringify(cells || {}), company: company || null }, 'POST'),
  cashFlowForecastRun: (fiscalYear: number, company?: string | null) =>
    call<any>('cash_flow_forecast.run', { fiscal_year: fiscalYear, company: company || null }),
  cashFlowForecastRunFiltered: (fiscalYear: number, company?: string | null, bankAccounts?: string[] | null) =>
    call<any>('cash_flow_forecast.run', {
      fiscal_year: fiscalYear, company: company || null,
      bank_accounts: bankAccounts && bankAccounts.length ? JSON.stringify(bankAccounts) : null,
    }),
  cashFlowForecastLineTransactions: (
    fiscalYear: number, line: string, monthIndex: number,
    company?: string | null, bankAccounts?: string[] | null,
  ) => call<any>('cash_flow_forecast.list_line_transactions', {
    fiscal_year: fiscalYear, line, month_index: monthIndex, company: company || null,
    bank_accounts: bankAccounts && bankAccounts.length ? JSON.stringify(bankAccounts) : null,
  }),
  cashFlowForecastCompanies: () => call<{ name: string; default_currency: string }[]>('cash_flow_forecast.list_companies'),
  cashFlowForecastBankAccounts: (company?: string | null) =>
    call<{ name: string; account_name: string; account_type: string }[]>(
      'cash_flow_forecast.list_bank_accounts', { company: company || null }),

  // v2.87.0 — Cash Flow Classification (Phase B/C/D). Same isolated feature
  // as cashFlowForecast* above, own module on the backend
  // (api/cash_flow_classification.py) — kept a separate prefix here too so
  // it reads as its own capability, not folded into the forecast calls.
  cashFlowClassificationQueue: (fiscalYear: number, company?: string | null, bankAccounts?: string[] | null, limit = 200) =>
    call<any>('cash_flow_classification.list_unclassified_transactions', {
      fiscal_year: fiscalYear, company: company || null,
      bank_accounts: bankAccounts && bankAccounts.length ? JSON.stringify(bankAccounts) : null,
      limit,
    }),
  cashFlowClassificationConfirm: (
    voucherType: string, voucherNo: string, line: string, note?: string | null,
    suggestedByRule?: string | null, suggestedLine?: string | null, confidence?: number | null,
  ) => call<any>('cash_flow_classification.confirm_classification', {
    voucher_type: voucherType, voucher_no: voucherNo, line, note: note || null,
    suggested_by_rule: suggestedByRule || null, suggested_line: suggestedLine || null,
    confidence: confidence ?? null,
  }, 'POST'),
  cashFlowClassificationReject: (suggestedByRule: string) =>
    call<any>('cash_flow_classification.reject_suggestion', { suggested_by_rule: suggestedByRule }, 'POST'),
  cashFlowClassificationBatchConfirm: (items: any[]) =>
    call<any>('cash_flow_classification.batch_confirm', { items: JSON.stringify(items) }, 'POST'),
  cashFlowClassificationRules: (status?: string | null) =>
    call<any[]>('cash_flow_classification.list_rules', { status: status || null }),
  cashFlowClassificationSetRuleStatus: (name: string, status: string) =>
    call<any>('cash_flow_classification.set_rule_status', { name, status }, 'POST'),
  cashFlowClassificationMineRules: (minSupport = 3, minPurity = 95) =>
    call<any>('cash_flow_classification.mine_rules', { min_support: minSupport, min_purity: minPurity }, 'POST'),

  // v2.57.0 — party ledgers
  partyControlAccounts: (company: string | null, party_type: string) =>
    call<{ name: string; label: string }[]>('report.party_control_accounts', { company, party_type }),
  listParties: (party_type: string, search?: string, company?: string | null) =>
    call<{ name: string; label: string }[]>('report.list_parties',
      { party_type, search: search || null, company: company || null }),
  listExistingFlags: (report: string) => call<string[]>('report.list_existing_flags', { report }),
  expandAccountGroup: (account: string) => call<any[]>('report.expand_account_group', { account }),
  listAccountsForFlag: (report: string, flag: string) =>
    call<any[]>('report.list_accounts_for_flag', { report, flag }),
  bulkSetAccountFlags: (report: string, items: { account: string; flag: string; bind_as_group?: number }[]) =>
    call('report.bulk_set_account_flags', { report, items: JSON.stringify(items) }, 'POST'),
  setAccountFlag: (report: string, account: string, flag: string | null) =>
    call('report.set_account_flag', { report, account, flag: flag ?? '' }, 'POST'),
  saveAccountMapping: (args: {
    report: string;
    account: string;
    flag: string;
    is_group_binding?: number;
    dimension_filters?: { dimension_type: string; dimension_value: string }[];
    mapping_name?: string;
  }) => call('report.save_account_mapping', {
    ...args,
    is_group_binding: args.is_group_binding ?? 0,
    dimension_filters: JSON.stringify(args.dimension_filters ?? []),
  }, 'POST'),
  deleteAccountMapping: (report: string, mappingName: string) =>
    call('report.delete_account_mapping', { report, mapping_name: mappingName }, 'POST'),
  autosuggestMappings: (report: string) =>
    call<{ created: number }>('report.autosuggest_mappings', { report }, 'POST'),

  importMapSheet: (report: string, fileBase64: string, opts?: Partial<{ sheet_name: string; account_col: number; flag_col: number; header_rows: number; replace: number }>) =>
    call('report.import_map_sheet', { report, file_base64: fileBase64, ...opts }, 'POST'),

  importReportStructure: (fileBase64: string, opts?: Partial<{ sheet_name: string; label_col: number; data_col_start: number; use_flags_from_sheet: string; create_report: number; report_name: string; report_slug: string }>) =>
    call('report.import_report_structure_from_excel', { file_base64: fileBase64, ...opts }, 'POST'),

  suggestFlag: (code: string) => call<{ flag: string | null }>('report.suggest_flag', { code }, 'POST'),
  listMappingRules: () => call<any[]>('report.list_mapping_rules'),
  saveMappingRule: (rule: { name?: string; prefix: string; flag: string; priority: number; is_active: number }) =>
    call('report.save_mapping_rule', rule, 'POST'),
  deleteMappingRule: (name: string) => call('report.delete_mapping_rule', { name }, 'POST'),
  listQuickLinks: () => call<Array<{
    label: string; url: string; icon?: string;
    open_in_new_tab?: number; sort_order?: number;
  }>>('report.list_quick_links'),
  getLiquidity: (
    company: string,
    fiscal_year: number,
    projection_months = 6,
    projection_baseline = 'committed',
    collection_mode = 'best_case',
    collection_schedule?: Record<string, { pct: number; weights: number[] }>,
    payment_schedule?: Record<string, { weights: number[] }>,
    dimension_filters?: Record<string, string> | null,
  ) =>
    call<any>('report.get_liquidity', {
      company, fiscal_year, projection_months, projection_baseline,
      collection_mode,
      collection_schedule: collection_schedule ? JSON.stringify(collection_schedule) : '',
      payment_schedule: payment_schedule ? JSON.stringify(payment_schedule) : '',
      ...(dimension_filters && Object.keys(dimension_filters).length > 0
        ? { dimension_filters: JSON.stringify(dimension_filters) }
        : {}),
    }),
  getFinancialRatios: (company: string, fiscal_year: number) =>
    call<any>('report.get_financial_ratios', { company, fiscal_year }),
  listVarianceNotes: (report: string, fiscal_year: number) =>
    call<Array<{ name: string; row_key: string; commentary: string; modified: string; modified_by: string }>>(
      'report.list_variance_notes', { report, fiscal_year }),
  saveVarianceNote: (report: string, row_key: string, fiscal_year: number, commentary: string) =>
    call<any>('report.save_variance_note', { report, row_key, fiscal_year, commentary }, 'POST'),
  getRolling12: (report: string, fiscal_year: number) =>
    call<any>('report.get_rolling_12', { report, fiscal_year }),
  insightHasGroupAccess: () =>
    call<{ has_access: boolean }>('report.insight_has_group_access'),
  insightGetAccessProfile: () =>
    call<{ role_tier: 'admin' | 'cfo' | 'ceo' | 'group_viewer' | 'hr' | 'basic'; can_edit: boolean; can_see_group: boolean; hr_only?: boolean; user: string }>(
      'report.insight_get_access_profile'),
  listGroupCompanies: () =>
    call<Array<{ name: string; label: string; currency: string; is_group: number; parent_company: string }>>(
      'report.list_group_companies'),
  getGroupDashboard: (
    report: string, fiscal_year: number, companies: string[],
    presentation_currency?: string,
    dimension_filters?: Record<string, string> | null,
  ) =>
    call<any>('report.get_group_dashboard', {
      report, fiscal_year,
      companies: JSON.stringify(companies),
      presentation_currency: presentation_currency || '',
      ...(dimension_filters && Object.keys(dimension_filters).length > 0
        ? { dimension_filters: JSON.stringify(dimension_filters) }
        : {}),
    }),
  getSensitivityScenario: (args: {
    company: string;
    fiscal_year: number;
    projection_months?: number;
    projection_baseline?: string;
    collection_mode?: string;
    stress_collection_days?: number;
    stress_revenue_pct?: number;
    stress_cost_pct?: number;
  }) => call<any>('report.get_sensitivity_scenario', args),
  getSensitivityTornado: (args: {
    company: string;
    fiscal_year: number;
    projection_months?: number;
    projection_baseline?: string;
    collection_mode?: string;
  }) => call<any>('report.get_sensitivity_tornado', args),
  getFragilityRadar: (company: string, fiscal_year: number, top_n = 5) =>
    call<any>('report.get_fragility_radar', { company, fiscal_year, top_n }),
  getMultiYearTrend: (
    report: string, end_year: number, years = 5,
    breakdown: 'total' | 'branch' = 'total',
    dimension_filters?: Record<string, string> | null,
  ) =>
    call<any>('report.get_multi_year_trend', {
      report, end_year, years, breakdown,
      ...(dimension_filters && Object.keys(dimension_filters).length > 0
        ? { dimension_filters: JSON.stringify(dimension_filters) }
        : {}),
    }),
  // v1.9.49 — Statement of Shareholder's Equity
  getEquityMovement: (company: string, fiscal_year: number, period = 'FY', report?: string) =>
    call<any>('report.get_equity_movement', { company, fiscal_year, period, report }),
  saveEquityMovement: (payload: any) =>
    call<{ name: string }>('report.save_equity_movement', { payload }),
  deleteEquityMovement: (name: string) =>
    call<{ deleted: boolean }>('report.delete_equity_movement', { name }),
  // v1.9.51 — Configurable equity components and movement types.
  listEquityComponents: () =>
    call<Array<{ value: string; label: string; display_order: number; is_seeded: number; description?: string }>>('report.list_equity_components'),
  listEquityMovementTypes: () =>
    call<Array<{ value: string; label: string; display_order: number; is_opening_balance: number; default_sign: string; is_seeded: number; description?: string }>>('report.list_equity_movement_types'),
  // v1.9.52 — Custom Accounting Dimensions discovery + values.
  createTaccountVariant: (report: string) =>
    call<any>('report.create_taccount_variant', { report }, 'POST'),
  coverageCheck: (report: string, company: string | null, fiscalYear: number) =>
    call<any>('report.coverage_check', { report, company: company ?? null, fiscal_year: fiscalYear }),
  listAccountingDimensions: () =>
    call<Array<{ fieldname: string; label: string; document_type: string }>>('report.list_accounting_dimensions'),
  listDimensionValues: (fieldname: string, search = '', limit = 100) =>
    call<Array<{ name: string; label: string }>>('report.list_dimension_values', { fieldname, search, limit }),
  // v1.9.53 — Letter Head for print/export.
  listLetterHeads: () =>
    call<Array<{ name: string; label: string; is_default: number }>>('report.list_letter_heads'),
  resolveLetterhead: (report?: string, company?: string) =>
    call<{ name: string; source: 'report' | 'company' | 'system' | 'none' }>('report.resolve_letterhead', { report: report || '', company: company || '' }),
  getLetterhead: (name: string, company?: string) =>
    call<{
      name: string;
      label: string;
      header_html: string;
      footer_html: string;
      logo_url: string;
      company_name: string;
      address_lines: string[];
      phone: string;
      email: string;
      website: string;
      tax_id: string;
      company_logo: string;
    }>('report.get_letterhead', { name, company: company || '' }),
  // v1.9.56 — Budget derive + copy. All budget values now come from real
  // Insight Budget Cell documents; these endpoints create cells in bulk.
  deriveBudgetCells: (args: {
    book: string;
    basis_offset?: number;
    default_growth_pct?: number;
    row_overrides?: Record<string, number>;
    preview?: 0 | 1;
  }) => call<{
    preview: boolean;
    cells_created: number;
    cells_replaced: number;
    basis_year: number;
    growth_summary: Array<{ row_key: string; label: string; growth_pct: number; source: 'row_override' | 'section_override' | 'default' }>;
    preview_cells?: Array<{ row_key: string; month: number; amount: number }>;
  }>('report.derive_budget_cells', {
    ...args,
    row_overrides: args.row_overrides ? JSON.stringify(args.row_overrides) : '',
  }),
  copyBudgetBook: (source_book: string, target_fiscal_year: number, target_label?: string) =>
    call<{ name: string; cells_copied: number }>('report.copy_budget_book', {
      source_book, target_fiscal_year, target_label: target_label || '',
    }),

  // Bank Slip Reader + (soon) reconciliation
  listSlips: (limit = 25, status?: string) =>
    call<any[]>('bank_reader.list_slips', { limit, status: status || '' }),
  readSlip: (file_url: string, company?: string) =>
    call<any>('bank_reader.read_slip', { file_url, company: company || '' }, 'POST'),
  stageDraftPaymentEntry: (slip: string) =>
    call<any>('bank_reader.stage_draft_payment_entry', { slip }, 'POST'),
  listOllamaModels: () => call<any>('bank_reader.list_ollama_models'),
  searchParties: (party_type: string, txt = '', limit = 20) =>
    call<any[]>('bank_reader.search_parties', { party_type, txt, limit }),
  setSlipParty: (slip: string, party_type: string, party: string) =>
    call<any>('bank_reader.set_slip_party', { slip, party_type, party }, 'POST'),
  setSlipDirection: (slip: string, direction: string) =>
    call<any>('bank_reader.set_slip_direction', { slip, direction }, 'POST'),
  getSlip: (slip: string) => call<any>('bank_reader.get_slip', { slip }),
  searchAccounts: (company: string, txt = '', root_type = '', limit = 20) =>
    call<any[]>('bank_reader.search_accounts', { company, txt, root_type, limit }),
  searchBankAccounts: (company: string, txt = '', limit = 20) =>
    call<any[]>('bank_reader.search_bank_accounts', { company, txt, limit }),
  setSlipAccounts: (slip: string, bank_account?: string, account?: string) =>
    call<any>('bank_reader.set_slip_accounts', { slip, bank_account: bank_account ?? '', account: account ?? '' }, 'POST'),
  previewStatement: (file_url: string) =>
    call<any>('statement_reader.preview_statement', { file_url }, 'POST'),
  importStatement: (file_url: string, bank_account: string, level = 'batch') =>
    call<any>('statement_reader.import_statement', { file_url, bank_account, level }, 'POST'),
  findMatches: (bank_account: string, from_date = '', to_date = '', window_before = 3, window_after = 7) =>
    call<any>('reconcile.find_matches', { bank_account, from_date, to_date, window_before, window_after }, 'POST'),
  confirmMatch: (bank_transaction: string, voucher_type: string, voucher_name: string) =>
    call<any>('reconcile.confirm_match', { bank_transaction, voucher_type, voucher_name }, 'POST'),
  unmatch: (bank_transaction: string) =>
    call<any>('reconcile.unmatch', { bank_transaction }, 'POST'),
  getReconcileSettings: () => call<any>('reconcile.get_reconcile_settings', {}),
  setReconcileSettings: (bank_charges_account?: string, input_vat_account?: string) =>
    call<any>('reconcile.set_reconcile_settings', { bank_charges_account: bank_charges_account ?? '', input_vat_account: input_vat_account ?? '' }, 'POST'),
  bookBankCharge: (bank_transaction: string, bank_charges_account: string, input_vat_account?: string, fee?: number, vat?: number) =>
    call<any>('reconcile.book_bank_charge', { bank_transaction, bank_charges_account, input_vat_account: input_vat_account ?? '', fee, vat }, 'POST'),
  listReconciled: (bank_account: string, limit = 100) =>
    call<any>('reconcile.list_reconciled', { bank_account, limit }),
  reconciliationSummary: (bank_account: string, from_date = '', to_date = '') =>
    call<any>('reconcile.reconciliation_summary', { bank_account, from_date, to_date }),
  reconciliationReport: (bank_account: string, from_date = '', to_date = '', include_open = 1) =>
    call<any>('reconcile.reconciliation_report', { bank_account, from_date, to_date, include_open }),
  backfillClearance: (bank_account: string) =>
    call<any>('reconcile.backfill_clearance', { bank_account }, 'POST'),
  reconciliationBridge: (bank_account: string, as_of = '', statement_balance?: number) =>
    call<any>('reconcile.reconciliation_bridge', { bank_account, as_of, statement_balance }),
  getPrintHeader: () => call<any>('reconcile.get_print_header', {}),
  morningBrief: (company = '', as_of = '', narrative = 1) =>
    call<any>('cfo.morning_brief', { company, as_of, narrative }),
  hrSummary: (company = '', as_of = '', period = 'this_month') =>
    call<any>('cfo.hr_summary', { company, as_of, period }),
  eosbBreakdown: (company = '', as_of = '') =>
    call<any>('cfo.eosb_breakdown', { company, as_of }),
  provisionFieldOptions: () => call<any>('cfo.provision_field_options', {}),
  getProvisionConfig: () => call<any>('cfo.get_provision_config', {}),
  setProvisionConfig: (vacation_days?: number, ticket_source?: string, insurance_source?: string) =>
    call<any>('cfo.set_provision_config', { vacation_days, ticket_source: ticket_source ?? '', insurance_source: insurance_source ?? '' }, 'POST'),
  setPrintHeader: (org_name?: string, org_address?: string, logo_url?: string) =>
    call<any>('reconcile.set_print_header', { org_name: org_name ?? '', org_address: org_address ?? '', logo_url: logo_url ?? '' }, 'POST'),

  // v2.55.0 — Brand Kit (print setup) persisted site-wide, and the company
  // identity block the shell header renders.
  appVersion: () => call<any>('navmenu.app_version'),
  getBrand: () => call<Record<string, any>>('navmenu.get_brand'),
  saveBrand: (company: string, brand: any) =>
    call<any>('navmenu.save_brand', { company, brand: JSON.stringify(brand || {}) }, 'POST'),
  companyBrand: (company?: string) =>
    call<any>('report.company_brand', { company: company || '' }),
  setCompanyLogo: (company: string, logo_url: string) =>
    call<any>('report.set_company_logo', { company, logo_url }, 'POST'),
};

/** Upload a file to Frappe and return its file_url (used by the Bank tab). */
export async function uploadFile(file: File, isPrivate = true): Promise<string> {
  const w = window as any;
  const token = (w.frappe && w.frappe.csrf_token) || w.csrf_token || '';
  const fd = new FormData();
  fd.append('file', file, file.name);
  fd.append('is_private', isPrivate ? '1' : '0');
  fd.append('folder', 'Home');
  const res = await fetch('/api/method/upload_file', {
    method: 'POST',
    headers: { 'X-Frappe-CSRF-Token': token, 'X-Requested-With': 'XMLHttpRequest' },
    credentials: 'include',
    body: fd,
  });
  if (!res.ok) throw new Error('Upload failed (' + res.status + ')');
  const data = await res.json();
  return data.message.file_url as string;
}

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => {
      const result = fr.result as string;
      const i = result.indexOf(',');
      resolve(i >= 0 ? result.slice(i + 1) : result);
    };
    fr.onerror = () => reject(new Error('Could not read file'));
    fr.readAsDataURL(file);
  });
}
