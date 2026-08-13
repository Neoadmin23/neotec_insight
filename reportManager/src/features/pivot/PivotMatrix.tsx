import { useMemo, useState } from 'react';
import { t } from '../../utils/i18n';
import type { PivotResult } from '../../types';
import { fmt0 } from '../../utils/format';

interface Props {
  result: PivotResult;
  visibleDims: Set<string>;
  setVisibleDims: (s: Set<string>) => void;
  hideZero: boolean;
  totalLast: boolean;
}

export function PivotMatrix({ result, visibleDims, hideZero, totalLast }: Props) {
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const orderedDims = useMemo(() => {
    let dims = result.dimensions.filter((d) => visibleDims.has(d.name));
    if (hideZero) {
      dims = dims.filter((d) =>
        result.rows.some(
          (r) => r.kind !== 'section' && Math.abs(r.by_dim[d.name] || 0) > 0.005
        )
      );
    }
    return dims;
  }, [result, visibleDims, hideZero]);

  const sortedRows = useMemo(() => {
    if (!sortCol) return result.rows;
    // Group rows by section/non-section; sort only within data segments.
    const out: typeof result.rows = [];
    let segment: typeof result.rows = [];
    const flush = () => {
      segment.sort((a, b) => {
        const av = sortCol === '__total__' ? a.total : a.by_dim[sortCol] || 0;
        const bv = sortCol === '__total__' ? b.total : b.by_dim[sortCol] || 0;
        return sortDir === 'asc' ? av - bv : bv - av;
      });
      out.push(...segment);
      segment = [];
    };
    for (const r of result.rows) {
      if (r.kind === 'section') {
        flush();
        out.push(r);
      } else {
        segment.push(r);
      }
    }
    flush();
    return out;
  }, [result, sortCol, sortDir]);

  function toggleSort(col: string) {
    if (sortCol === col) setSortDir(sortDir === 'desc' ? 'asc' : 'desc');
    else { setSortCol(col); setSortDir('desc'); }
  }

  const totalFirst = !totalLast;
  const isSubtotal = (label: string) => {
    const l = (label || '').toLowerCase();
    return l.startsWith('gross income') || l.startsWith('ebitda') ||
      l.startsWith('net operating') || l.startsWith('net income') ||
      l.startsWith('total cost') || l.startsWith('total admin') ||
      l.startsWith('total g&a') || l.startsWith('total depreciation') ||
      l.startsWith('allocation cost');
  };

  return (
    <div className="matrix-wrap">
      <div className="matrix-scroll">
        <table className="pivot-matrix">
          <thead>
            <tr>
              <th className="pivot-row-head">P&amp;L line</th>
              {totalFirst && (
                <th
                  className={'pivot-col-total ' + (sortCol === '__total__' ? `sort-${sortDir}` : '')}
                  onClick={() => toggleSort('__total__')}
                >{t('Total')}</th>
              )}
              {orderedDims.map((d) => (
                <th
                  key={d.name}
                  className={sortCol === d.name ? `sort-${sortDir}` : ''}
                  onClick={() => toggleSort(d.name)}
                  title={d.company ? `${d.label} · ${d.company}` : d.label}
                >
                  {d.label}
                </th>
              ))}
              {totalLast && (
                <th
                  className={'pivot-col-total ' + (sortCol === '__total__' ? `sort-${sortDir}` : '')}
                  onClick={() => toggleSort('__total__')}
                >{t('Total')}</th>
              )}
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((r) => {
              if (r.kind === 'section') {
                return (
                  <tr key={r.key} className="pivot-row-section">
                    <td colSpan={orderedDims.length + 2}>{r.label}</td>
                  </tr>
                );
              }
              const subtotal = isSubtotal(r.label) || r.kind === 'formula';
              return (
                <tr key={r.key} className={subtotal ? 'pivot-row-subtotal' : ''}>
                  <td className="pivot-row-label">{r.label}</td>
                  {totalFirst && (
                    <td className={'pivot-col-total ' + (r.total < 0 ? 'neg' : r.total === 0 ? 'zero' : '')}>
                      {fmt0(r.total)}
                    </td>
                  )}
                  {orderedDims.map((d) => {
                    const v = r.by_dim[d.name] || 0;
                    return (
                      <td key={d.name} className={v < 0 ? 'neg' : v === 0 ? 'zero' : ''}>
                        {fmt0(v)}
                      </td>
                    );
                  })}
                  {totalLast && (
                    <td className={'pivot-col-total ' + (r.total < 0 ? 'neg' : r.total === 0 ? 'zero' : '')}>
                      {fmt0(r.total)}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
