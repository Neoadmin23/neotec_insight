/* v3.8.2 — Trial Balance "net values", as ERPNext's "Show net values in
 * opening and closing columns": each row's opening and closing collapse to one
 * side (Dr − Cr); the Total is the sum of the netted top-level groups. Period
 * columns are unchanged. Pure. */

type Row = { opening_debit: number; opening_credit: number; closing_debit: number; closing_credit: number; [k: string]: any };

export function netRow<T extends Row>(r: T): T {
  const o = (r.opening_debit || 0) - (r.opening_credit || 0);
  const c = (r.closing_debit || 0) - (r.closing_credit || 0);
  return { ...r, opening_debit: o > 0 ? o : 0, opening_credit: o < 0 ? -o : 0,
    closing_debit: c > 0 ? c : 0, closing_credit: c < 0 ? -c : 0 };
}

export function netTrialBalance<T>(resIn: T): T {
  const res: any = resIn;
  if (!res?.result?.accounts) return resIn;
  const accounts = res.result.accounts.map((a: any) => netRow(a));
  const top = accounts.filter((a: any) => (a.depth || 0) === Math.min(...accounts.map((x: any) => x.depth || 0)));
  const sum = (k: string) => Math.round(top.reduce((s: number, a: any) => s + (a[k] || 0), 0) * 100) / 100;
  const totals = { ...res.result.totals, opening_debit: sum('opening_debit'), opening_credit: sum('opening_credit'),
    closing_debit: sum('closing_debit'), closing_credit: sum('closing_credit') };
  return { ...res, result: { ...res.result, accounts, totals } } as T;
}
