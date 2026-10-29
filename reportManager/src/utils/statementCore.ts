/* Statement of Account — pure helpers (tested in scripts/test-statement.mjs). */

/** v3.4.4 — merge consecutive ledger lines of the same voucher (an invoice
 *  posted as revenue + VAT lines) into one line at the voucher total. The
 *  running balance is the group's last balance, so totals never change. */
export function mergeByVoucher(rows: any[]): any[] {
  const out: any[] = [];
  for (const r of rows) {
    const prev = out[out.length - 1];
    const same = prev && prev.voucher_no && prev.voucher_no === r.voucher_no && prev.voucher_type === r.voucher_type
      && String(prev.posting_date) === String(r.posting_date) && (prev.against_voucher || '') === (r.against_voucher || '');
    if (same) {
      prev.debit = (Number(prev.debit) || 0) + (Number(r.debit) || 0);
      prev.credit = (Number(prev.credit) || 0) + (Number(r.credit) || 0);
      prev.debit_in_account_currency = (Number(prev.debit_in_account_currency) || 0) + (Number(r.debit_in_account_currency) || 0);
      prev.credit_in_account_currency = (Number(prev.credit_in_account_currency) || 0) + (Number(r.credit_in_account_currency) || 0);
      prev.balance_raw = r.balance_raw;
      prev._lines = (prev._lines || 1) + 1;
    } else {
      out.push({ ...r });
    }
  }
  return out;
}

