import { useMemo, useState } from 'react';
import { t } from '../utils/i18n';
import { fmtD } from '../utils/format';

/* ─── ComboView (v1.9.63) ─────────────────────────────────────────────────
 *
 * Renders the response from `report.run_combo_report` — flat list of
 * tuples representing (report-row × dim1 × dim2). The same component
 * handles P&L, Trial Balance, and Balance Sheet because the backend
 * dispatcher returns a uniform shape regardless of report type.
 *
 * Two presentation formats:
 *   - Format A: flat table, one row per tuple. Exports cleanly to Excel.
 *   - Format B: hierarchy with collapsible outer groups, outer dim
 *               nested over inner dim.
 *
 * User picks which format via a header toggle. Both formats render the
 * same data — switching is free (no re-fetch).
 */

interface ComboTuple {
  row_key: string;
  row_label: string;
  tuple: Record<string, string>;
  value: number;
}

interface ComboResult {
  view: 'combo';
  dimensions_picked: [string, string];
  rows: ComboTuple[];
  filters?: any;
}

interface AccountingDim {
  fieldname: string;
  label: string;
}

interface Props {
  result: ComboResult;
  decimals?: number;
  /** Optional dimension catalogue for resolving human-readable labels. */
  dimensions?: AccountingDim[];
}

type Format = 'flat' | 'hierarchy';

export function ComboView({ result, decimals = 0, dimensions = [] }: Props) {
  const [format, setFormat] = useState<Format>('flat');
  const [swapped, setSwapped] = useState(false);
  // Collapsed group keys (used in Format B). Keys are outer-dim values.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const [dimA, dimB] = result.dimensions_picked;
  // Effective outer/inner — when swapped, inner becomes outer.
  const outerDim = swapped ? dimB : dimA;
  const innerDim = swapped ? dimA : dimB;

  // Resolve dimension labels (Cost Center / Project / etc.) for the
  // column headers.
  const labelFor = (fieldname: string): string => {
    const natives: Record<string, string> = {
      cost_center: 'Cost Center', project: 'Project',
      department: 'Department', branch: 'Branch',
    };
    if (natives[fieldname]) return natives[fieldname];
    const found = dimensions.find((d) => d.fieldname === fieldname);
    return found?.label || fieldname;
  };

  // Format the tuples grouped by outer dim — used for hierarchy rendering
  // and the group-level totals shown in flat mode's subtotal rows.
  const grouped = useMemo(() => {
    const byOuter = new Map<string, ComboTuple[]>();
    for (const r of result.rows) {
      const k = r.tuple[outerDim] || '(Unassigned)';
      if (!byOuter.has(k)) byOuter.set(k, []);
      byOuter.get(k)!.push(r);
    }
    return byOuter;
  }, [result.rows, outerDim]);

  const grandTotal = useMemo(
    () => result.rows.reduce((s, r) => s + (r.value || 0), 0),
    [result.rows],
  );

  function toggleGroup(key: string) {
    setCollapsed((cur) => {
      const next = new Set(cur);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  if (result.rows.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon"><i className="ti ti-table-off" aria-hidden /></div>
        <h3 className="empty-state-title">No tuples found</h3>
        <p className="empty-state-body">
          The combination of {labelFor(dimA)} and {labelFor(dimB)} has no
          non-zero values for the current filters. Try widening the date
          range or removing dimension filters.
        </p>
      </div>
    );
  }

  return (
    <div className="combo-view">
      {/* ─── Toolbar — format toggle + swap arrow ───────────────────── */}
      <div className="combo-toolbar">
        <div className="combo-toolbar-left">
          <span className="combo-pair-label">
            <span className="badge is-info">{labelFor(outerDim)}</span>
            <button
              type="button"
              className="combo-swap-btn"
              onClick={() => setSwapped((s) => !s)}
              title="Swap outer and inner dimensions"
              aria-label="Swap dimensions"
            >
              <i className="ti ti-arrows-exchange" aria-hidden />
            </button>
            <span className="badge is-info">{labelFor(innerDim)}</span>
          </span>
          <span className="combo-meta">{result.rows.length} tuple{result.rows.length === 1 ? '' : 's'}</span>
        </div>
        <div className="combo-toolbar-right">
          <div className="combo-format-toggle" role="tablist" aria-label="Combo display format">
            <button
              type="button"
              className={'combo-fmt-btn' + (format === 'flat' ? ' is-active' : '')}
              onClick={() => setFormat('flat')}
              role="tab"
              aria-selected={format === 'flat'}
            >
              <i className="ti ti-list" aria-hidden /> Flat
            </button>
            <button
              type="button"
              className={'combo-fmt-btn' + (format === 'hierarchy' ? ' is-active' : '')}
              onClick={() => setFormat('hierarchy')}
              role="tab"
              aria-selected={format === 'hierarchy'}
            >
              <i className="ti ti-tree" aria-hidden /> Hierarchy
            </button>
          </div>
        </div>
      </div>

      {/* ─── Format A — flat tuple list ────────────────────────────── */}
      {format === 'flat' && (
        <div className="matrix-wrap">
          <div className="matrix-scroll">
            <table className="matrix combo-flat">
              <thead>
                <tr className="h1">
                  <th>Row</th>
                  <th>{labelFor(outerDim)}</th>
                  <th>{labelFor(innerDim)}</th>
                  <th className="num">Value</th>
                </tr>
              </thead>
              <tbody>
                {Array.from(grouped.entries()).map(([outerVal, tuples]) => (
                  <FlatGroup
                    key={outerVal}
                    outerVal={outerVal}
                    tuples={tuples}
                    innerDim={innerDim}
                    decimals={decimals}
                  />
                ))}
                <tr className="r-section combo-grand">
                  <td colSpan={3}>Grand total</td>
                  <td className={'num' + (grandTotal < 0 ? ' neg' : '')}>{fmtD(grandTotal, decimals)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ─── Format B — hierarchy with collapsible groups ──────────── */}
      {format === 'hierarchy' && (
        <div className="combo-hierarchy">
          {Array.from(grouped.entries()).map(([outerVal, tuples]) => {
            const isCollapsed = collapsed.has(outerVal);
            const groupTotal = tuples.reduce((s, t) => s + (t.value || 0), 0);
            return (
              <div key={outerVal} className="combo-group">
                <button
                  type="button"
                  className="combo-group-header"
                  onClick={() => toggleGroup(outerVal)}
                  aria-expanded={!isCollapsed}
                >
                  <span className="combo-group-caret" aria-hidden>{isCollapsed ? '▸' : '▾'}</span>
                  <span className="combo-group-name">{outerVal}</span>
                  <span className="combo-group-count">{tuples.length}</span>
                  <span className={'combo-group-total' + (groupTotal < 0 ? ' neg' : '')}>
                    {fmtD(groupTotal, decimals)}
                  </span>
                </button>
                {!isCollapsed && (
                  <div className="combo-group-body">
                    <table className="matrix combo-tree">
                      <tbody>
                        {tuples.map((t, i) => (
                          <tr key={i}>
                            <td className="combo-tree-row">{t.row_label}</td>
                            <td className="combo-tree-inner">{t.tuple[innerDim] || '(Unassigned)'}</td>
                            <td className={'num' + (t.value < 0 ? ' neg' : '')}>{fmtD(t.value, decimals)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          })}
          <div className="combo-grand-row">
            <span>Grand total</span>
            <span className={'combo-grand-value' + (grandTotal < 0 ? ' neg' : '')}>{fmtD(grandTotal, decimals)}</span>
          </div>
        </div>
      )}
    </div>
  );
}

/** Subtotal-aware group rendering for Format A. Shows the tuples then a
 *  subtotal row for the outer-dim group. */
function FlatGroup({
  outerVal, tuples, innerDim, decimals,
}: {
  outerVal: string;
  tuples: ComboTuple[];
  innerDim: string;
  decimals: number;
}) {
  const groupTotal = tuples.reduce((s, t) => s + (t.value || 0), 0);
  return (
    <>
      {tuples.map((t, i) => (
        <tr key={`${outerVal}-${i}`}>
          <td>{t.row_label}</td>
          <td>{i === 0 ? outerVal : ''}</td>
          <td>{t.tuple[innerDim] || '(Unassigned)'}</td>
          <td className={'num' + (t.value < 0 ? ' neg' : '')}>{fmtD(t.value, decimals)}</td>
        </tr>
      ))}
      <tr className="combo-subtotal">
        <td colSpan={3} className="combo-subtotal-label">Subtotal — {outerVal}</td>
        <td className={'num' + (groupTotal < 0 ? ' neg' : '')}>{fmtD(groupTotal, decimals)}</td>
      </tr>
    </>
  );
}
