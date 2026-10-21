// v2.99.0 — tests for src/utils/boardCore.ts
import assert from 'node:assert/strict';
import * as b from '../src/utils/boardCore.ts';

const V = (id, x, y, w = 4, h = 3) => ({ id, title: id, type: 'bar', source: { kind: 'analysis', key: 'k' }, x, y, w, h });
const noOverlap = (l) => l.every((a) => l.every((c) => a === c || !(a.x < c.x + c.w && c.x < a.x + a.w && a.y < c.y + c.h && c.y < a.y + a.h)));

// layout: clamp, move onto another pushes it down, resize pushes neighbours, chains settle
assert.deepEqual(b.clampVisual(V('a', 11, -2, 6, 1)), { ...V('a', 6, 0, 6, 2) });
let L = [V('a', 0, 0), V('b', 4, 0), V('c', 0, 3)];
L = b.placeVisual(L, { ...L[1], x: 0, y: 0 });
assert.ok(noOverlap(L)); assert.equal(L.find((v) => v.id === 'b').y, 0);
L = b.placeVisual(L, { ...L.find((v) => v.id === 'b'), w: 12, h: 4 });
assert.ok(noOverlap(L)); assert.equal(b.nextY(L), Math.max(...L.map((v) => v.y + v.h)));

// data
const ds = { key: 'k', title: 'Sales', figures: [], notes: [], chart: null,
  columns: [{ key: 'region', label: 'Region', type: 'text' }, { key: 'month', label: 'Month', type: 'text' },
    { key: 'amount', label: 'Amount', type: 'currency' }],
  rows: [{ region: 'Riyadh', month: 'Jan', amount: 10 }, { region: 'Riyadh', month: 'Feb', amount: 5 },
    { region: 'Jeddah', month: 'Jan', amount: 7 }] };
const p = b.pivot(ds, 'region', 'month', 'amount');
assert.deepEqual(p.rowKeys, ['Riyadh', 'Jeddah']); assert.equal(p.cells['Riyadh\u0000Jan'], 10);
assert.equal(p.rowTotals.Riyadh, 15); assert.equal(p.colTotals.Jan, 17); assert.equal(p.total, 22);
assert.equal(b.kpiValue(ds).value, 22);
assert.equal(b.kpiValue({ ...ds, figures: [{ label: 'Net margin', value: 12.5, format: 'percent' }] }).label, 'Net margin');

// click-to-filter: applies to visuals with that column, never to the one clicked, ignores others
const f = { key: 'region', value: 'Riyadh', from: 'v1' };
assert.equal(b.applyCrossFilter(ds, f, 'v2').rows.length, 2);
assert.equal(b.applyCrossFilter(ds, f, 'v1').rows.length, 3);
assert.equal(b.applyCrossFilter({ ...ds, columns: [ds.columns[2]] }, f, 'v2').rows.length, 3);

// charts
const c = b.chartData(ds, 'bar'); assert.deepEqual(c.labels, ['Riyadh', 'Riyadh', 'Jeddah']); assert.equal(c.series[0].name, 'Amount');
const wf = { ...ds, chart: { type: 'waterfall', labels: ['Revenue', 'Net'], series: [{ name: 'A', data: [[0, 10], [0, 4]], kinds: ['total', 'total'] }] } };
assert.equal(b.chartData(wf, 'waterfall').waterfall, true);
assert.equal(b.chartData(ds, 'pie').series.length, 1);

console.log('board: 4 groups passed');
