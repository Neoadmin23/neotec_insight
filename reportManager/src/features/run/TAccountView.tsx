import type { ReportDefinition, RunResult, ExecutedRow, DefinitionRow, TAccountSide } from '../../types';
import { t } from '../../utils/i18n';

/* ─── T-Account Trading and Profit & Loss view (v1.9.48) ─────────────────
 *
 * Renders the classical horizontal Trading and Profit & Loss Account:
 *   Trading section:  [debit_trading + gp_balancer]  |  [credit_trading]
 *   P&L section:      [debit_pl + np_balancer]       |  [credit_pl]
 *
 * Each row carries a `t_side` classification from its definition. Rows
 * without it are omitted from this view (and a notice is shown if no rows
 * are classified at all — graceful fallback).
 *
 * Conventions intentionally preserved:
 *   - Currency header at the top of each value column.
 *   - "Less: X" sub-line inline with the gross figure when a row declares
 *     less_label / less_row_key (e.g. Sales 56,000 less Sales Return 1,000).
 *   - Gross Profit c/d on the debit side closes Trading; appears as b/d
 *     on the credit side of P&L — same number, both shown with the
 *     "c/d" and "b/d" suffixes.
 *   - Net Profit transferred to Capital closes the P&L section.
 *   - Section totals shown with double-rule (top and bottom border).
 *
 * NOTE on signs: the engine returns row values with their natural sign
 * (revenue positive, expense positive in their own context after sign
 * normalisation). The T-account view shows ABSOLUTE values on both sides
 * — the side itself signals whether the line is a debit or credit, as in
 * traditional bookkeeping. We don't flip signs; we present them.
 */

interface Props {
  run: RunResult;
  report: ReportDefinition;
  monthsAll: number[];
  decimals: number;
}

export function TAccountView({ run, report, monthsAll, decimals }: Props) {
  const currency = (run as any)?.filters?.company_currency || '';

  // Index executed rows by key for fast lookup.
  const execByKey: Record<string, ExecutedRow> = {};
  for (const r of (run.current?.rows || [])) execByKey[r.key] = r;

  // Carry the per-row classification from the definition forward (the
  // executed-row payload may not include t_side / less_* metadata).
  const defRows: DefinitionRow[] = report?.definition?.rows || [];
  const defByKey: Record<string, DefinitionRow> = {};
  for (const r of defRows) defByKey[r.key] = r;

  // Sum the chosen month range for a single row.
  const sumRow = (key: string): number => {
    const r = execByKey[key];
    if (!r) return 0;
    let t = 0;
    for (const m of monthsAll) t += Number(r.monthly?.[m] || 0);
    return t;
  };

  // Detect whether any rows are classified. If not, fall back gracefully.
  const classifiedRows = defRows.filter((r) => r.t_side);
  if (classifiedRows.length === 0) {
    return (
      <div className="taccount-empty">
        <div className="taccount-empty-title">T-Account view not configured</div>
        <p>
          This report has presentation set to <strong>T-Account</strong>, but none of its rows declare a
          <code> t_side</code> classification. The T-Account view needs each row to be tagged as one of:
          <em> debit_trading, credit_trading, gp_balancer, debit_pl, credit_pl,</em> or <em> np_balancer</em>.
        </p>
        <p>
          Switch the report back to <strong>Vertical</strong> presentation in the Reports → Rows tab,
          or add <code>t_side</code> values to the rows. Until then, no T-Account can be rendered.
        </p>
      </div>
    );
  }

  // Bucket rows by side, preserving the order from the report definition.
  const bySide: Record<TAccountSide, DefinitionRow[]> = {
    debit_trading: [], credit_trading: [], gp_balancer: [],
    debit_pl: [], credit_pl: [], np_balancer: [],
  };
  for (const r of defRows) {
    if (r.t_side) bySide[r.t_side].push(r);
  }

  // ── Trading section totals ────────────────────────────────────────────
  const tradingDebitTotalNoBalancer = bySide.debit_trading.reduce((s, r) => s + Math.abs(sumRow(r.key)), 0);
  const tradingCreditTotal = bySide.credit_trading.reduce((s, r) => s + Math.abs(sumRow(r.key)), 0);

  // Gross Profit is computed as: credit_trading − debit_trading (positive
  // = profit; negative = gross loss). If the definition supplies an
  // explicit gp_balancer row, prefer its computed value (engine source of
  // truth); otherwise derive it here. We compute either way to detect
  // sign and label correctly.
  const grossProfitFromBalancer = bySide.gp_balancer.length > 0
    ? bySide.gp_balancer.reduce((s, r) => s + sumRow(r.key), 0)
    : null;
  const grossProfitDerived = tradingCreditTotal - tradingDebitTotalNoBalancer;
  const grossProfit = grossProfitFromBalancer != null ? grossProfitFromBalancer : grossProfitDerived;
  const tradingBalanceTotal = Math.max(tradingDebitTotalNoBalancer + Math.max(grossProfit, 0), tradingCreditTotal + Math.max(-grossProfit, 0));

  // ── P&L section totals ────────────────────────────────────────────────
  // P&L credit side includes Gross Profit b/d (the SAME number we just
  // computed) plus any other income rows classified as credit_pl.
  const plOtherCreditTotal = bySide.credit_pl.reduce((s, r) => s + Math.abs(sumRow(r.key)), 0);
  const plCreditTotal = plOtherCreditTotal + Math.max(grossProfit, 0);

  const plDebitTotalNoBalancer = bySide.debit_pl.reduce((s, r) => s + Math.abs(sumRow(r.key)), 0)
    + Math.max(-grossProfit, 0);  // Gross loss b/d goes on the debit side

  const netProfitFromBalancer = bySide.np_balancer.length > 0
    ? bySide.np_balancer.reduce((s, r) => s + sumRow(r.key), 0)
    : null;
  const netProfitDerived = plCreditTotal - plDebitTotalNoBalancer;
  const netProfit = netProfitFromBalancer != null ? netProfitFromBalancer : netProfitDerived;
  const plBalanceTotal = Math.max(plCreditTotal + Math.max(-netProfit, 0), plDebitTotalNoBalancer + Math.max(netProfit, 0));

  const fmtNum = (v: number) =>
    Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

  // Render a single value row, optionally with a "Less: X" sub-line.
  const renderRow = (r: DefinitionRow, key: string) => {
    const v = sumRow(r.key);
    const lessKey = r.less_row_key;
    if (lessKey && execByKey[lessKey]) {
      const lessVal = sumRow(lessKey);
      const grossVal = Math.abs(v) + Math.abs(lessVal);
      return (
        <tr key={key}>
          <td className="ta-label">{r.label}</td>
          <td className="ta-deduct-val">{fmtNum(grossVal)}</td>
          <td className="ta-val">{fmtNum(v)}</td>
        </tr>
      );
    }
    return (
      <tr key={key}>
        <td className="ta-label" colSpan={2}>{r.label}</td>
        <td className="ta-val">{fmtNum(v)}</td>
      </tr>
    );
  };

  // Render the "Less: X" sub-line that goes BELOW a gross figure.
  // We use the parent row's less_label / less_row_key to lookup the value.
  // This is called as a separate <tr> inserted right after the parent.
  const renderLessSubLine = (r: DefinitionRow) => {
    if (!r.less_row_key || !execByKey[r.less_row_key]) return null;
    const lessVal = sumRow(r.less_row_key);
    return (
      <tr key={r.key + '-less'} className="ta-less-row">
        <td className="ta-less-label">Less: {r.less_label || execByKey[r.less_row_key].label}</td>
        <td className="ta-deduct-val">{fmtNum(lessVal)}</td>
        <td className="ta-val" />
      </tr>
    );
  };

  const fyLabel = `FY ${run.filters.fiscal_year}`;
  const periodLabel = `For the period ended ${monthsAll.length === 12 ? '31st Dec. ' + run.filters.fiscal_year : '...'}`;
  const companyLabel = run.filters?.company || '';

  return (
    <div className="taccount-wrap">
      <div className="taccount-header">
        <div className="ta-co">{companyLabel}</div>
        <div className="ta-title">Trading and Profit &amp; Loss Account</div>
        <div className="ta-period">{periodLabel} ({fyLabel})</div>
      </div>

      {/* ── Trading section ──────────────────────────────────────────── */}
      <div className="taccount-section">
        <div className="taccount-section-tag">Trading Account</div>
        <div className="taccount-cols">
          {/* Debit (left) side */}
          <table className="taccount-table">
            <thead>
              <tr><th colSpan={2} /><th className="ta-cur">{currency}</th></tr>
            </thead>
            <tbody>
              {bySide.debit_trading.flatMap((r) => {
                const out = [renderRow(r, r.key)];
                if (r.less_row_key) {
                  const less = renderLessSubLine(r);
                  if (less) out.push(less);
                }
                return out;
              })}
              {/* Gross Profit c/d (only when GP is positive — closes the debit side) */}
              {grossProfit > 0 && (
                <tr className="ta-balancer">
                  <td className="ta-label" colSpan={2}>Gross Profit c/d <span className="ta-cd">(Transfer to P&amp;L A/c)</span></td>
                  <td className="ta-val">{fmtNum(grossProfit)}</td>
                </tr>
              )}
              <tr className="ta-total-row">
                <td colSpan={2} />
                <td className="ta-val ta-total">{fmtNum(tradingBalanceTotal)}</td>
              </tr>
            </tbody>
          </table>

          {/* Credit (right) side */}
          <table className="taccount-table">
            <thead>
              <tr><th colSpan={2} /><th className="ta-cur">{currency}</th></tr>
            </thead>
            <tbody>
              {bySide.credit_trading.flatMap((r) => {
                const out = [renderRow(r, r.key)];
                if (r.less_row_key) {
                  const less = renderLessSubLine(r);
                  if (less) out.push(less);
                }
                return out;
              })}
              {/* Gross Loss c/d (only when GP is negative — closes the credit side) */}
              {grossProfit < 0 && (
                <tr className="ta-balancer">
                  <td className="ta-label" colSpan={2}>Gross Loss c/d</td>
                  <td className="ta-val">{fmtNum(-grossProfit)}</td>
                </tr>
              )}
              <tr className="ta-total-row">
                <td colSpan={2} />
                <td className="ta-val ta-total">{fmtNum(tradingBalanceTotal)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* ── P&L section ──────────────────────────────────────────────── */}
      <div className="taccount-section">
        <div className="taccount-section-tag">Profit &amp; Loss Account</div>
        <div className="taccount-cols">
          {/* Debit (left) side: expenses + Net Profit balancer */}
          <table className="taccount-table">
            <thead>
              <tr><th colSpan={2} /><th className="ta-cur">{currency}</th></tr>
            </thead>
            <tbody>
              {/* Gross Loss b/d, if Trading produced a loss */}
              {grossProfit < 0 && (
                <tr className="ta-bd-row">
                  <td className="ta-label" colSpan={2}>Gross Loss b/d</td>
                  <td className="ta-val">{fmtNum(-grossProfit)}</td>
                </tr>
              )}
              {bySide.debit_pl.flatMap((r) => {
                const out = [renderRow(r, r.key)];
                if (r.less_row_key) {
                  const less = renderLessSubLine(r);
                  if (less) out.push(less);
                }
                return out;
              })}
              {netProfit > 0 && (
                <tr className="ta-balancer">
                  <td className="ta-label" colSpan={2}>Net Profit <span className="ta-cd">(Transferred to Capital A/c)</span></td>
                  <td className="ta-val">{fmtNum(netProfit)}</td>
                </tr>
              )}
              <tr className="ta-total-row">
                <td colSpan={2} />
                <td className="ta-val ta-total">{fmtNum(plBalanceTotal)}</td>
              </tr>
            </tbody>
          </table>

          {/* Credit (right) side: Gross Profit b/d + other incomes */}
          <table className="taccount-table">
            <thead>
              <tr><th colSpan={2} /><th className="ta-cur">{currency}</th></tr>
            </thead>
            <tbody>
              {/* Gross Profit b/d — same number that closed Trading on the debit side. */}
              {grossProfit > 0 && (
                <tr className="ta-bd-row">
                  <td className="ta-label" colSpan={2}>Gross Profit b/d</td>
                  <td className="ta-val">{fmtNum(grossProfit)}</td>
                </tr>
              )}
              {bySide.credit_pl.flatMap((r) => {
                const out = [renderRow(r, r.key)];
                if (r.less_row_key) {
                  const less = renderLessSubLine(r);
                  if (less) out.push(less);
                }
                return out;
              })}
              {netProfit < 0 && (
                <tr className="ta-balancer">
                  <td className="ta-label" colSpan={2}>Net Loss <span className="ta-cd">(To Capital A/c)</span></td>
                  <td className="ta-val">{fmtNum(-netProfit)}</td>
                </tr>
              )}
              <tr className="ta-total-row">
                <td colSpan={2} />
                <td className="ta-val ta-total">{fmtNum(plBalanceTotal)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="taccount-footnote">
        Presented in T-Account format. Gross Profit shown as <em>c/d</em> on the Trading section's
        debit side and <em>b/d</em> on the Profit &amp; Loss section's credit side — same value, both
        sections balanced. Switch to <strong>Vertical</strong> presentation in the report definition
        for a top-to-bottom statement.
      </div>
    </div>
  );
}
