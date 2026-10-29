// v3.4.4 — one line per voucher, on the lines from the customer's statement
import assert from 'node:assert/strict';
import { mergeByVoucher } from '../src/utils/statementCore.ts';

const L = (date, vno, vtype, against, debit, credit, bal) =>
  ({ posting_date: date, voucher_no: vno, voucher_type: vtype, against_voucher: against, debit, credit, balance_raw: bal });
const raw = [
  L('2026-01-29', 'ACC-SINV-2026-00076', 'Sales Invoice', 'ACC-SINV-2026-00076', 1500, 0, 13000),
  L('2026-01-29', 'ACC-SINV-2026-00076', 'Sales Invoice', 'ACC-SINV-2026-00076', 10000, 0, 23000),
  L('2026-02-28', 'ACC-SINV-2026-00154', 'Sales Invoice', 'ACC-SINV-2026-00154', 1500, 0, 24500),
  L('2026-02-28', 'ACC-SINV-2026-00154', 'Sales Invoice', 'ACC-SINV-2026-00154', 10000, 0, 34500),
  L('2026-03-15', 'ACC-PAY-2026-00495', 'Payment Entry', 'ACC-SINV-2026-00076', 0, 11500, 23000),
  L('2026-03-15', 'ACC-PAY-2026-00494', 'Payment Entry', 'ACC-SINV-2026-00005', 0, 11500, 11500),
];
const m = mergeByVoucher(raw);
assert.equal(m.length, 4);
assert.deepEqual([m[0].debit, m[0].balance_raw, m[0]._lines], [11500, 23000, 2]);   // one line at invoice total
assert.deepEqual([m[1].debit, m[1].balance_raw], [11500, 34500]);
assert.equal(m[2].voucher_no, 'ACC-PAY-2026-00495');                               // separate payments stay separate
assert.equal(m[3].voucher_no, 'ACC-PAY-2026-00494');
const sum = (a, k) => a.reduce((s, r) => s + (r[k] || 0), 0);
assert.equal(sum(m, 'debit'), sum(raw, 'debit'));                                  // totals unchanged
assert.equal(sum(m, 'credit'), sum(raw, 'credit'));
assert.equal(m[m.length - 1].balance_raw, raw[raw.length - 1].balance_raw);       // closing unchanged
assert.equal(raw[0].debit, 1500);                                                  // input not mutated
// the same voucher on a different date is not merged
assert.equal(mergeByVoucher([L('2026-01-01', 'JV-1', 'Journal Entry', '', 5, 0, 5), L('2026-01-02', 'JV-1', 'Journal Entry', '', 5, 0, 10)]).length, 2);
console.log('statement: 1 group passed');
