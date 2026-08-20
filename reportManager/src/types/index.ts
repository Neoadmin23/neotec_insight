export type Workspace = 'reports' | 'visuals' | 'dashboard' | 'group' | 'briefing' | 'general_ledger' | 'ageing' | 'studio' | 'health' | 'classification' | 'vat' | 'vat_settings' | 'zakat' | 'packs' | 'gst' | 'cashflow' | 'bank' | 'hr' | 'allocation' | 'cashflowforecast';
export type RowKind = 'section' | 'source' | 'formula' | 'allocation';
export type ComparisonMode = 'actuals_only' | 'vs_budget';
export type Granularity =
  | 'month'
  | 'quarter'
  | 'half'
  | 'ytd'
  | 'month_quarter'
  | 'month_half'
  | 'quarter_frame'
  | 'quarter_half'
  | 'month_quarter_half'
  | 'quarter_ytd';

export type PeriodTier = 'month' | 'quarter' | 'half' | 'ytd';

export type BookStatus = 'draft' | 'submitted' | 'approved' | 'locked';
export type DimensionType = 'total' | 'cost_center' | 'department' | 'project' | 'custom';

export interface BudgetBook {
  name: string;
  slug: string;
  label: string;
  fiscal_year: number;
  dimension_type: DimensionType;
  dimension_value: string | null;
  owner_user: string | null;
  status: BookStatus;
  is_primary_axis_book: number;
  label_is_custom: number;
  can_edit?: boolean;
}

export interface BudgetBookSummary {
  name: string;
  slug: string;
  label: string;
  fiscal_year: number;
  dimension_type: DimensionType;
  dimension_value: string | null;
  owner_user: string | null;
  status: BookStatus;
  is_primary_axis_book: number;
}

export interface Period {
  key: string;
  label: string;
  months: number[];
  gran: PeriodTier;
}

export interface PeriodGroup {
  key?: string;
  tier: PeriodTier;
  periods: Period[];
}

export interface RowDimensionScope {
  dimension_type: string;       // 'Department' | 'Cost Center' | 'Project' | custom
  dimension_values: string[];   // OR within the dimension — e.g. ['MSSP','MDR']
}

/** v1.9.18 — optional per-row visual styling. Flows to screen + all exports.
 *  Colours are fixed-palette tokens (not freeform) so reports stay consistent. */
export interface RowStyle {
  bold?: boolean;
  italic?: boolean;
  /** palette token: 'default' | 'muted' | 'blue' | 'green' | 'red' */
  text_color?: string;
  /** palette token: 'none' | 'grey' | 'blue' | 'yellow' */
  bg_color?: string;
  border_top?: boolean;
  border_bottom?: boolean;
}

export type TAccountSide =
  | 'debit_trading'    // Left side of Trading section: Opening Stock, Purchases, Direct Expenses
  | 'credit_trading'   // Right side of Trading: Sales, Closing Stock
  | 'gp_balancer'      // Gross Profit c/d — closes Trading on the debit side (when GP positive)
  | 'debit_pl'         // Left side of P&L section: indirect expenses
  | 'credit_pl'        // Right side of P&L: Gross Profit b/d + other incomes
  | 'np_balancer';     // Net Profit transferred to Capital — closes P&L on the debit side

export interface DefinitionRow {
  key: string;
  kind: RowKind;
  label: string;
  flag?: string;
  accounts?: string[];
  formula?: string;
  /** v2.61.0 — for kind 'allocation': the Insight Allocation Rule this row
   *  draws from. Its monthly values land in the formula context like any
   *  source row, so other rows can reference it by key. */
  allocation_rule?: string;
  /** v2.62.1 — 'cost_center' hides the row unless the report is run for a
   *  single cost centre; 'always' shows it regardless.
   *  v2.75.0 — valid on EVERY row kind. The default differs by kind and must:
   *  allocation rows default to 'cost_center' (a pool shown consolidated reads
   *  as a real charge and double-counts), every other kind defaults to
   *  'always'. Hidden rows still feed formulas; only the display is suppressed. */
  show_when?: 'cost_center' | 'cost_center_exclude' | 'always';
  sign?: 'normal' | 'invert';
  /** v1.9.6 — optional per-row dimension scope. When set, every account
   *  mapped to this row is filtered to dimension_type IN dimension_values. */
  dimension_scope?: RowDimensionScope | null;
  /** v1.9.18 — optional per-row visual styling. */
  style?: RowStyle | null;
  /** v1.9.65 — when true, the row is excluded from display but still counts
   *  in formulas and section totals (computed server-side regardless). */
  hidden?: boolean;
  /** v1.9.48 — T-account classification. When the report's
   *  presentation_format is 't_account', the renderer uses this to decide
   *  which side and section the row appears on. Optional: rows without it
   *  are omitted from the T-account view. The vertical view ignores it. */
  t_side?: TAccountSide | null;
  /** v1.9.48 — optional gross-figure annotation for the T-account view.
   *  When present, the row is rendered with a "Less: X" sub-line.
   *  Example: Sales (gross 56,000) less Sales Return (1,000) = net 55,000.
   *  The row's own value is the NET figure; less_label + less_value_key
   *  are the deduction shown above it. */
  less_label?: string | null;
  less_row_key?: string | null;
}

export interface ReportDefinition {
  name?: string;
  report_name: string;
  slug: string;
  description?: string;
  is_active?: number | boolean;
  is_default?: number | boolean;
  /** v1.9.48 — controls how the report is rendered. */
  presentation_format?: 'vertical' | 't_account';
  company?: string;
  version?: number;
  report_type?: 'pnl' | 'trial_balance' | 'balance_sheet';
  comparison_mode?: ComparisonMode;
  prior_years?: number;
  definition: { rows: DefinitionRow[]; comparison?: { mode: ComparisonMode; prior_years: number } };
  columns: any[];
  filters: any[];
}

export interface ReportSummary {
  name: string;
  report_name: string;
  slug: string;
  description?: string;
  is_active?: number;
  is_default?: number;
  presentation_format?: 'vertical' | 't_account';
  version?: number;
  company?: string;
  report_type?: 'pnl' | 'trial_balance' | 'balance_sheet';
  comparison_mode?: ComparisonMode;
  prior_years?: number;
  modified?: string;
}

export interface AccountMapping {
  name: string;
  account: string;
  account_code?: string;
  account_name?: string;
  flag: string;
  source?: string;
  auto_suggested?: number;
  is_group_binding?: number;
  dimension_filters?: { dimension_type: string; dimension_value: string }[];
  scope_summary?: string;
}

export interface MappingRule {
  name: string;
  prefix: string;
  flag: string;
  priority: number;
  is_active: number;
}

export interface ExecutedRow extends DefinitionRow {
  monthly: Record<number, number>;
}

export interface RunResult {
  report: { name: string; report_name: string; slug: string };
  filters: {
    fiscal_year: number;
    company?: string | null;
    company_currency?: string | null;
    month_from: number;
    month_to: number;
    segment: string;
    cost_center?: string | string[] | null;
    project?: string | string[] | null;
    department?: string | string[] | null;
    prior_years: number;
    comparison_mode: ComparisonMode;
    granularity: Granularity;
    compare_to_book?: string | null;
    compare_to_book_resolved?: BudgetBookSummary | null;
    /** v1.9.59 — calendar month (1..12) when this company's fiscal year
     *  begins. KSA=1 (Jan), India=4 (April), Australia=7 (July). The
     *  frontend uses this to label month columns in fiscal-year order. */
    fy_start_month?: number;
    /** v1.9.59 — display label like "FY 2025" (Jan-start) or
     *  "FY 2024-25" (April-start). Use in headers and exports. */
    fy_label?: string;
    /** v1.9.60 — when set (1..12), the reporting calendar was overridden
     *  for this run — e.g. a KSA company rendered as Apr-Mar for group
     *  reporting. Null = company's configured calendar was used. */
    fy_start_month_override?: number | null;
    /** v1.9.65 — 'fiscal_year' (default) or 'date_range'. */
    period_mode?: 'fiscal_year' | 'date_range';
    /** v1.9.65 — ISO date 'YYYY-MM-DD'. Set when period_mode='date_range'. */
    period_from_date?: string | null;
    period_to_date?: string | null;
  };
  current: { rows: ExecutedRow[]; months: number[] };
  priors: Array<{ fiscal_year: number; rows: ExecutedRow[] }>;
  budget: { rows: ExecutedRow[]; months: number[]; book?: BudgetBookSummary | null } | null;
  period_groups: PeriodGroup[];
  binding_meta?: Record<string, FlagBindingMeta>;
  period_order?: Array<{ tier: PeriodTier; key: string; label: string; months: number[]; gran: PeriodTier }>;
  performance?: { execution_ms: number; cache_hit: boolean };
}

export interface FlagBindingMeta {
  is_group: boolean;
  resolved_count: number;
  direct_count: number;
  group_codes: string[];
  new_count: number;
  new_accounts: Array<{ code: string; name: string }>;
  new_truncated: boolean;
  /** v2.76.1 — at least one Account Flag Mapping row exists for this flag,
   *  whether or not it currently resolves to any account. False means the
   *  row was never mapped at all. */
  has_binding: boolean;
  /** v2.76.1 — directly-bound accounts that no longer exist in the chart of
   *  accounts (deleted or renamed out from under the mapping). They still
   *  sit in the mapping and still feed the SQL query; they just can never
   *  match anything again. */
  missing_accounts: string[];
  missing_count: number;
}

// ─── Dimension Pivot view (v1.6) ─────────────────────────────────────────
// v1.9.52 — widened from a closed union to a string. Native dimensions
// (cost_center, department, project, branch) and any custom Accounting
// Dimension fieldname are all valid. The backend validates against its
// discovered dimension set; the frontend trusts the discovery payload.
export type PivotBy = string;

export interface PivotDimension {
  name: string;
  label: string;
  company: string;
  revenue: number;  // used by "Top N by revenue" quick filter
}

export interface PivotRow {
  key: string;
  kind: 'source' | 'formula' | 'section';
  label: string;
  formula?: string;
  flag?: string;
  by_dim: Record<string, number>;
  total: number;
}

export interface PivotResult {
  report: { name: string; report_name: string; slug: string };
  filters: {
    fiscal_year: number;
    month_from: number;
    month_to: number;
    pivot_by: PivotBy;
    company: string | null;
  };
  dimensions: PivotDimension[];
  rows: PivotRow[];
  performance?: { execution_ms: number; cache_hit: boolean };
}

export interface RunSnapshot {
  id: string;
  name: string;
  createdAt: number;
  run: RunResult;
  rowDefs: Pick<DefinitionRow, 'key' | 'label' | 'kind'>[];
}

// ─── Trial Balance / Balance Sheet (v1.8) ────────────────────────────────
export type ReportType = 'pnl' | 'trial_balance' | 'balance_sheet';

export interface TrialBalanceAccount {
  name: string;
  label: string;
  code: string;
  parent: string;
  is_group: number;
  account_type: string;
  root_type: string;
  depth: number;
  lft: number;
  rgt: number;
  has_parties: boolean;
  opening_debit: number;
  opening_credit: number;
  period_debit: number;
  period_credit: number;
  closing_debit: number;
  closing_credit: number;
}

export interface TrialBalanceResult {
  report: { name: string; report_name: string; slug: string; report_type: string };
  filters: {
    company: string; fiscal_year: string | number;
    fiscal_year_start: string; as_of_date: string;
    cost_center?: string | null; project?: string | null;
    root_types: string[];
  };
  result: {
    accounts: TrialBalanceAccount[];
    totals: {
      opening_debit: number; opening_credit: number;
      period_debit: number; period_credit: number;
      closing_debit: number; closing_credit: number;
    };
    currency?: {
      presentation_currency?: string; company_currency?: string;
      conversion_rate?: number; as_of_date?: string; rate_missing?: number | boolean;
    } | null;
  };
  performance?: { execution_ms: number; cache_hit: boolean };
}

export interface TrialBalanceParty {
  party_type: string;
  party: string;
  party_name: string;
  opening_debit: number;
  opening_credit: number;
  period_debit: number;
  period_credit: number;
  closing_debit: number;
  closing_credit: number;
}

export interface BalanceSheetAccount {
  name: string;
  label: string;
  code: string;
  parent: string;
  is_group: number;
  account_type: string;
  root_type: 'Asset' | 'Liability' | 'Equity' | string;
  depth: number;
  lft: number;
  rgt: number;
  has_parties: boolean;
  current: number;
  prior: number | null;
}

export interface BalanceSheetResult {
  report: { name: string; report_name: string; slug: string; report_type: string };
  filters: {
    company: string; as_of_date: string;
    prior_as_of_date?: string | null;
    cost_center?: string | null; project?: string | null;
  };
  result: {
    accounts: BalanceSheetAccount[];
    sections: {
      asset: { current: number; prior: number | null };
      liability: { current: number; prior: number | null };
      equity: { current: number; prior: number | null };
      current_year_earnings: { current: number; prior: number | null };
      lia_plus_eq: { current: number; prior: number | null };
      diff: { current: number; prior: number | null };
    };
  };
  performance?: { execution_ms: number; cache_hit: boolean };
}

// ─── Row drill (v1.8.1) ──────────────────────────────────────────────────
export interface RowDrillAccount {
  account: string;
  account_code: string;
  account_name: string;
  monthly: Record<number, number>;
  monthly_prev?: Record<number, number>;
  is_group_binding_leaf: boolean;
  parent_group: string | null;
}

export interface RowDrillResult {
  row_key: string;
  flag: string;
  accounts: RowDrillAccount[];
  totals: Record<number, number>;
  performance?: { execution_ms: number; cache_hit: boolean };
}

// ─── Profit & Loss Statement (CoA-based, v1.9.1) ─────────────────────────
export interface PnlStatementAccount {
  name: string;
  label: string;
  code: string;
  parent: string;
  is_group: number;
  root_type: 'Income' | 'Expense' | string;
  account_type: string;
  depth: number;
  lft: number;
  rgt: number;
  amount: number;
}

export interface PnlStatementResult {
  report: { name: string; report_name: string; slug: string; report_type: string };
  filters: {
    company: string; from_date: string; to_date: string;
    cost_center?: string | null; project?: string | null; department?: string | null;
    finance_book?: string | null;
    show_group_accounts: number; show_zero_values: number;
    presentation_currency?: string | null;
  };
  result: {
    accounts: PnlStatementAccount[];
    summary: { total_income: number; total_expense: number; net_profit: number; is_loss: boolean };
    currency: any;
  };
  performance?: { execution_ms: number; cache_hit: boolean };
}

export interface PnlStatementPivotAccount {
  name: string;
  label: string;
  code: string;
  parent: string;
  is_group: number;
  root_type: string;
  depth: number;
  lft: number;
  rgt: number;
  by_dim: Record<string, number>;
  total: number;
}

export interface PnlStatementPivotResult {
  report: { name: string; report_name: string; slug: string; report_type: string };
  filters: { company: string; from_date: string; to_date: string; pivot_by: string };
  result: {
    accounts: PnlStatementPivotAccount[];
    dimensions: { name: string; label: string }[];
    summary: {
      by_dim: Record<string, { total_income: number; total_expense: number; net_profit: number }>;
      total: { total_income: number; total_expense: number; net_profit: number; is_loss: boolean };
    };
    currency: any;
  };
  performance?: { execution_ms: number; cache_hit: boolean };
}

// ─── TB / BS dimension pivots (v1.9.3) ───────────────────────────────────
export interface BalancePivotAccount {
  name: string;
  label: string;
  code: string;
  parent: string;
  is_group: number;
  root_type: string;
  depth: number;
  lft: number;
  rgt: number;
  by_dim: Record<string, number>;
  total: number;
}

export interface BalancePivotResult {
  report: { name: string; report_name: string; slug: string; report_type: string };
  filters: any;
  result: {
    accounts: BalancePivotAccount[];
    dimensions: { name: string; label: string }[];
    currency: any;
  };
  performance?: { execution_ms: number; cache_hit: boolean };
}

export interface SavedDashboard {
  name: string;
  label: string;
  slug: string;
  report: string;
  owner_user: string;
  is_shared: number;
  description: string;
  modified?: string;
  can_edit?: boolean;
  is_mine?: boolean;
}

export interface SavedDashboardFull extends SavedDashboard {
  tiles: any[];
  filters: any;
}

export type ChartType =
  | 'bar' | 'line' | 'area' | 'stacked' | 'grouped' | 'hbar'
  | 'pie' | 'donut' | 'waterfall' | 'kpi' | 'table';

export interface Tile {
  id: string;
  runId: string;
  title: string;
  type: ChartType;
  rowKeys: string[];
  series: 'actual' | 'actual_budget' | 'actual_prior' | 'actual_budget_prior';
  palette: 'brand' | 'cool' | 'warm' | 'mono';
}
