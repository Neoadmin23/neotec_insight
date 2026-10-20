// v2.97.1 — tests for src/utils/menuFold.ts (run: node --experimental-strip-types scripts/test-menu-fold.mjs)
import assert from 'node:assert/strict';
import { foldLegacyPerformance } from '../src/utils/menuFold.ts';

const ORDER = ['briefing', 'dashboard', 'health', 'insights', 'classification', 'group'];
const fold = (s) => foldLegacyPerformance(s, 'performance', 'Performance', ORDER);
const sec = (key, ...ws) => ({ key, label: key, tabs: ws.map((w) => (typeof w === 'string' ? { ws: w } : w)) });
const keys = (s) => s.map((x) => x.key);
const tabs = (s, k) => s.find((x) => x.key === k).tabs.map((t) => t.ws);

// 1. The state v2.97.0 left behind (the reported screenshot).
const broken = [sec('reports', 'reports'), sec('briefing', 'briefing'), sec('dashboard', 'dashboard'),
  sec('studio', 'studio'), sec('analysis', 'health', 'group'), sec('compliance', 'vat'),
  sec('operations', 'bank'), sec('performance', 'insights'), sec('cff', 'cashflowforecast')];
let out = fold(broken);
assert.deepEqual(keys(out), ['reports', 'performance', 'studio', 'compliance', 'operations', 'cff']);
assert.deepEqual(tabs(out, 'performance'), ['briefing', 'dashboard', 'health', 'insights', 'group']);

// 2. The original old layout, no Performance section yet.
out = fold([sec('reports', 'reports'), sec('briefing', 'briefing'), sec('dashboard', 'dashboard'),
  sec('analysis', 'health', 'group')]);
assert.deepEqual(keys(out), ['reports', 'performance']);
assert.deepEqual(tabs(out, 'performance'), ['briefing', 'dashboard', 'health', 'group']);

// 3. A tab someone hid stays hidden.
out = fold([sec('briefing', 'briefing'), sec('dashboard', { ws: 'dashboard', hidden: 1 }), sec('analysis', 'health', 'group')]);
assert.equal(out[0].tabs.find((t) => t.ws === 'dashboard').hidden, 1);

// 4. A section changed by hand is left alone.
out = fold([sec('briefing', 'briefing'), sec('analysis', 'health', 'group', 'vat'), sec('performance', 'insights')]);
assert.deepEqual(keys(out), ['performance', 'analysis']);
assert.deepEqual(tabs(out, 'analysis'), ['health', 'group', 'vat']);

// 5. Already folded: unchanged.
const good = [sec('reports', 'reports'), sec('performance', 'briefing', 'dashboard', 'health', 'insights', 'group')];
assert.equal(fold(good), good);

// 6. A renamed Performance label is kept.
out = fold([{ key: 'performance', label: 'Management', tabs: [{ ws: 'insights' }] }, sec('briefing', 'briefing')]);
assert.equal(out[0].label, 'Management');

console.log('menu fold: 6 cases passed');
