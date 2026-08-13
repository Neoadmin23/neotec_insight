import { useEffect, useMemo, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import LinkField from '../../components/LinkField';

/** VAT Settings (v2.70.0) — one screen for everything that governs a return.
 *
 *  It configures nothing of its own. VAT accounts come from Classification
 *  tags, deferral from Insight GTPL Rule, per-voucher overrides from Insight
 *  VAT Adjustment. Adding a second place to set any of them would guarantee the
 *  two eventually disagree, with no way to tell which produced a filed number.
 *
 *  What was missing was visibility: nothing showed which accounts the engine had
 *  resolved, whether it was obeying tags or guessing, or which rule governed a
 *  given quarter. */

type Rule = {
  name?: string; effective_from: string; is_active: number; target_box: string;
  trigger_basis: string; credit_note_presentation: string; order_date_field?: string;
  output_vat_account?: string; deferred_vat_account?: string; notes?: string;
  customer_groups: string[];
  customer_overrides: { customer: string; treatment: string; reason: string }[];
};

const BLANK: Rule = {
  effective_from: new Date().toISOString().slice(0, 10),
  is_active: 1, target_box: '1.2', trigger_basis: 'receipt_only',
  credit_note_presentation: 'gross_with_adjustment',
  customer_groups: [], customer_overrides: [],
};

const BASIS: Record<string, string> = {
  receipt_only: 'When payment is received',
  order_only: 'When the payment order is issued',
  earlier_of_receipt_or_order: 'Whichever comes first — receipt or payment order',
  invoice_date: 'On the invoice date (deferral off)',
};

export default function VatSettings() {
  const [companies, setCompanies] = useState<{ name: string; label?: string }[]>([]);
  const [company, setCompany] = useState('');
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Rule | null>(null);
  const [orders, setOrders] = useState<any[]>([]);
  const [deferred, setDeferred] = useState<any[]>([]);
  const [newOrder, setNewOrder] = useState<any | null>(null);

  useEffect(() => {
    api.listCompanies()
      .then((cs: any[]) => { setCompanies(cs || []); setCompany((c) => c || (cs?.[0]?.name || '')); })
      .catch((e: any) => setErr(String(e?.message || e)));
  }, []);

  function load(c: string) {
    if (!c) return;
    setErr('');
    api.vatSettings(c).then(setData).catch((e: any) => setErr(String(e?.message || e)));
    api.paymentOrders(c).then((r) => setOrders(r || [])).catch(() => setOrders([]));
    api.deferredInvoices(c).then((r) => setDeferred(r?.rows || [])).catch(() => setDeferred([]));
  }
  useEffect(() => { load(company); }, [company]);

  async function saveRule() {
    if (!editing) return;
    setBusy(true); setMsg(''); setErr('');
    try {
      await api.saveGtplRule(company, editing);
      setEditing(null); setMsg(t('Rule saved.')); load(company);
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  async function toggle(r: any) {
    setBusy(true);
    try { await api.setGtplRuleActive(r.name, !r.is_active); load(company); }
    catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  async function saveOrder() {
    if (!newOrder?.sales_invoice || !newOrder?.order_date) {
      setErr(t('An invoice and an order date are required.')); return;
    }
    setBusy(true); setMsg(''); setErr('');
    try {
      const r = await api.savePaymentOrder(company, newOrder);
      setNewOrder(null);
      setMsg(r?.inert
        ? t('Order recorded — but the rule in force releases on payment receipt, so this will not move the supply. Change the rule basis to use it.')
        : t('Payment order recorded. The supply now falls in the quarter containing that date.'));
      load(company);
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  async function removeOrder(name: string) {
    setBusy(true);
    try { await api.deletePaymentOrder(name); load(company); }
    catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  async function excludeAccount(account: string) {
    setBusy(true); setErr(''); setMsg('');
    try {
      await api.excludeFromVat(company, account);
      setMsg(t('{0} tagged Not VAT — it will no longer feed the return.').replace('{0}', account));
      load(company);
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  const acc = data?.accounts;
  const rules: any[] = data?.gtpl_rules || [];
  const adjustments: any[] = data?.adjustments || [];

  /* Which rule governs today — the same resolution the engine performs, shown
     rather than left to be inferred from a list sorted by date. */
  const governingNow = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    const live = rules.filter((r) => r.is_active && r.effective_from <= today);
    return live.length ? live[0] : null;
  }, [rules]);

  const groupOptions: string[] = data?.customer_groups || [];

  return (
    <div className="vs-workspace">
      <div className="studio-frow" style={{ gap: 8, alignItems: 'center', marginBottom: 10 }}>
        <label className="studio-hint">{t('Company')}</label>
        <select value={company} onChange={(e) => setCompany(e.target.value)}>
          {companies.length === 0 && <option value="">{t('Company')}</option>}
          {companies.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
        </select>
        {data && <span className="studio-hint">{t('Standard rate')}: {data.standard_rate}%</span>}
      </div>

      {err && <div className="studio-err">{err}</div>}
      {msg && <div className="theme-hint">{msg}</div>}
      {!data && !err && <div className="fh-loading">{t('Loading…')}</div>}

      {data && (
        <>
          {/* ---------------- VAT accounts ---------------- */}
          <section className="vs-card">
            <h3>{t('VAT control accounts')}</h3>
            <p className="theme-hint">
              {t('Set in the Classification tab, not here — tagging any account as Output VAT or Input VAT switches that side to strict mode, and only tagged accounts count. Untagged, the engine matches on account type and name, which is a guess.')}
            </p>
            <div className="vs-grid">
              {(['output', 'input'] as const).map((side) => {
                const s = acc?.[side];
                const guessing = s?.mode !== 'tagged';
                return (
                  <div key={side} className="vs-acc">
                    <div className="vs-acc-h">
                      <strong>{side === 'output' ? t('Output VAT') : t('Input VAT')}</strong>
                      <span className={guessing ? 'vs-pill warn' : 'vs-pill ok'}>
                        {guessing ? t('Heuristic — guessed from type and name') : t('Tagged — strict')}
                      </span>
                    </div>
                    {guessing && (s?.accounts || []).length > 0 &&
                      <div className="vs-warn" style={{ marginBottom: 8 }}>
                        {t('Check this list. Anything here that is not a VAT control account is being counted as VAT — its ledger entries feed the return.')}
                      </div>}
                    {(s?.accounts || []).length === 0
                      ? <div className="vs-empty">{t('No accounts resolved. The return will show no VAT on this side.')}</div>
                      : <ul className="vs-acc-list">
                          {s.accounts.map((a: any) => (
                            <li key={a.name} className="vs-acc-row">
                              <span>
                                {a.account_number && <span className="cls-num">{a.account_number}</span>}
                                {a.account_name || a.name}
                              </span>
                              {data.can_write &&
                                <button className="vat-drill-link" disabled={busy}
                                  title={t('Tag this account Not VAT so it stops feeding the return')}
                                  onClick={() => excludeAccount(a.name)}>{t('Not VAT')}</button>}
                            </li>
                          ))}
                        </ul>}
                  </div>
                );
              })}
            </div>
          </section>

          {/* ---------------- GTPL rules ---------------- */}
          <section className="vs-card">
            <div className="vs-card-h">
              <h3>{t('Government VAT deferral (GTPL)')}</h3>
              {data.can_write && !editing &&
                <button className="vs-btn" onClick={() => setEditing({ ...BLANK })}>＋ {t('New rule')}</button>}
            </div>
            <p className="theme-hint">
              {t('Output VAT on supplies to government entities falls due when the supply is paid, not when it is invoiced. The rule governing a period is the newest active rule effective on or before that period end — supersede by adding a later-dated rule rather than editing this one, so a filed quarter still reproduces.')}
            </p>

            {rules.length === 0 && !editing &&
              <div className="vs-empty">{t('No rule. Government sales are treated like any other standard-rated sale and box 1.2 does not appear on the return.')}</div>}

            {rules.length > 0 && (
              <table className="studio-table vs-table">
                <thead><tr>
                  <th>{t('Effective from')}</th><th>{t('Box')}</th><th>{t('Tax due')}</th>
                  <th>{t('Scope')}</th><th>{t('Status')}</th><th />
                </tr></thead>
                <tbody>
                  {rules.map((r) => (
                    <tr key={r.name} className={governingNow?.name === r.name ? 'vs-live' : ''}>
                      <td>{r.effective_from}
                        {governingNow?.name === r.name && <span className="vs-pill ok" style={{ marginInlineStart: 6 }}>{t('In force')}</span>}</td>
                      <td>{r.target_box || <em className="studio-hint">{t('merged')}</em>}</td>
                      <td>{t(BASIS[r.trigger_basis] || r.trigger_basis)}</td>
                      <td>
                        {(r.customer_groups || []).join(', ') || <em className="studio-hint">{t('none')}</em>}
                        {(r.customer_overrides || []).length > 0 &&
                          <span className="studio-hint"> · {r.customer_overrides.length} {t('override(s)')}</span>}
                      </td>
                      <td>{r.is_active ? t('Active') : t('Inactive')}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        {data.can_write && <>
                          <button className="vat-drill-link" onClick={() => setEditing({ ...r })}>{t('Edit')}</button>
                          {' · '}
                          <button className="vat-drill-link" onClick={() => toggle(r)} disabled={busy}>
                            {r.is_active ? t('Deactivate') : t('Activate')}
                          </button>
                        </>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {editing && (
              <div className="vs-editor">
                <h4>{editing.name ? t('Edit rule') : t('New rule')}</h4>
                {editing.name &&
                  <div className="vs-warn">
                    {t('Editing a rule that has already governed a filed quarter restates that quarter when it is re-run. To change the treatment going forward, close this and add a new rule with a later effective date instead.')}
                  </div>}
                <div className="vs-form">
                  <label>{t('Effective from')}
                    <input type="date" value={editing.effective_from}
                      onChange={(e) => setEditing({ ...editing, effective_from: e.target.value })} />
                  </label>
                  <label>{t('Return presentation')}
                    <select value={editing.target_box ? 'split' : 'merged'}
                      onChange={(e) => setEditing({ ...editing,
                        target_box: e.target.value === 'split' ? (editing.target_box || '1.2') : '' })}>
                      <option value="split">{t('Separate government box on the return')}</option>
                      <option value="merged">{t('Merged into standard-rated sales')}</option>
                    </select>
                    <span className="studio-hint">{t('The deferral is the same either way — this is only how it is disclosed.')}</span>
                  </label>
                  {editing.target_box !== '' && editing.target_box !== undefined &&
                    <label>{t('Government box number')}
                      <input value={editing.target_box}
                        onChange={(e) => setEditing({ ...editing, target_box: e.target.value })} />
                    </label>}
                  <label>{t('Tax becomes due')}
                    <select value={editing.trigger_basis}
                      onChange={(e) => setEditing({ ...editing, trigger_basis: e.target.value })}>
                      {Object.keys(BASIS).map((k) => <option key={k} value={k}>{t(BASIS[k])}</option>)}
                    </select>
                  </label>
                  <label>{t('Credit note presentation')}
                    <select value={editing.credit_note_presentation}
                      onChange={(e) => setEditing({ ...editing, credit_note_presentation: e.target.value })}>
                      <option value="gross_with_adjustment">{t('Gross — invoice in Amount, credit note in Adjustment')}</option>
                      <option value="net_of_credit_notes">{t('Net — only the surviving balance')}</option>
                    </select>
                  </label>
                  {['order_only', 'earlier_of_receipt_or_order'].includes(editing.trigger_basis) &&
                    <label>{t('Payment order date field')}
                      <input placeholder="custom_payment_order_date" value={editing.order_date_field || ''}
                        onChange={(e) => setEditing({ ...editing, order_date_field: e.target.value })} />
                      <span className="studio-hint">{t('ERPNext has no native field. Blank falls back to payment receipt.')}</span>
                    </label>}
                </div>

                <div className="vs-sub">
                  <strong>{t('Government customer groups')}</strong>
                  <div className="vs-chips">
                    {groupOptions.map((g) => {
                      const on = editing.customer_groups.includes(g);
                      return (
                        <button key={g} className={on ? 'vs-chip on' : 'vs-chip'}
                          onClick={() => setEditing({
                            ...editing,
                            customer_groups: on ? editing.customer_groups.filter((x) => x !== g)
                                               : [...editing.customer_groups, g],
                          })}>{g}</button>
                      );
                    })}
                  </div>
                </div>

                <div className="vs-sub">
                  <strong>{t('Per-customer overrides')}</strong>
                  <p className="studio-hint">
                    {t('Wins over the group test in both directions. The negative direction matters: a sovereign fund can sit in a governmental group while being invoiced commercially, and deferring it would defer VAT that was genuinely due.')}
                  </p>
                  {editing.customer_overrides.map((o, i) => (
                    <div key={i} className="vs-ovr">
                      <LinkField doctype="Customer" company={company} value={o.customer}
                        placeholder={t('Customer')}
                        onChange={(v) => {
                          const next = [...editing.customer_overrides];
                          next[i] = { ...o, customer: v };
                          setEditing({ ...editing, customer_overrides: next });
                        }} />
                      <select value={o.treatment}
                        onChange={(e) => {
                          const next = [...editing.customer_overrides];
                          next[i] = { ...o, treatment: e.target.value };
                          setEditing({ ...editing, customer_overrides: next });
                        }}>
                        <option value="Government">{t('Government')}</option>
                        <option value="Not Government">{t('Not Government')}</option>
                      </select>
                      <input placeholder={t('Reason (required — this is the audit trail)')} value={o.reason}
                        onChange={(e) => {
                          const next = [...editing.customer_overrides];
                          next[i] = { ...o, reason: e.target.value };
                          setEditing({ ...editing, customer_overrides: next });
                        }} />
                      <button className="vat-drill-link"
                        onClick={() => setEditing({
                          ...editing,
                          customer_overrides: editing.customer_overrides.filter((_x, j) => j !== i),
                        })}>×</button>
                    </div>
                  ))}
                  <button className="vs-btn ghost" onClick={() => setEditing({
                    ...editing,
                    customer_overrides: [...editing.customer_overrides,
                                         { customer: '', treatment: 'Government', reason: '' }],
                  })}>＋ {t('Add override')}</button>
                </div>

                <div className="vs-sub">
                  <strong>{t('Accounts for the ledger cross-check')}</strong>
                  <p className="studio-hint">{t('Optional, and reporting only — Insight never posts. Named here, the deferral register prints their period movement against the carried-forward figure.')}</p>
                  <div className="vs-form">
                    <label>{t('Output VAT account')}
                      <LinkField doctype="Account" company={company}
                        value={editing.output_vat_account || ''}
                        onChange={(v) => setEditing({ ...editing, output_vat_account: v })} />
                    </label>
                    <label>{t('Deferred output VAT account')}
                      <LinkField doctype="Account" company={company}
                        value={editing.deferred_vat_account || ''}
                        onChange={(v) => setEditing({ ...editing, deferred_vat_account: v })} />
                    </label>
                  </div>
                </div>

                <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                  <button className="vs-btn" onClick={saveRule} disabled={busy}>
                    {busy ? t('Saving…') : t('Save rule')}
                  </button>
                  <button className="vs-btn ghost" onClick={() => setEditing(null)}>{t('Cancel')}</button>
                </div>
              </div>
            )}
          </section>

          {/* ---------------- Payment orders ---------------- */}
          <section className="vs-card">
            <div className="vs-card-h">
              <h3>{t('Payment orders (أمر الدفع)')}</h3>
              {data.can_write && !newOrder &&
                <button className="vs-btn" onClick={() => setNewOrder({
                  sales_invoice: '', order_date: new Date().toISOString().slice(0, 10),
                  order_reference: '', amount: '', notes: '',
                })}>＋ {t('Record order')}</button>}
            </div>
            <p className="theme-hint">
              {t('A government payment order is itself a tax point: the invoice enters the return of the quarter containing the order date, whether or not the money has arrived. Record one here to bring a deferred supply into that quarter.')}
            </p>
            {governingNow && !['order_only', 'earlier_of_receipt_or_order'].includes(governingNow.trigger_basis) &&
              <div className="vs-warn">
                {t('The rule in force releases on payment receipt only, so orders recorded here will not move anything. Set the rule basis to "whichever comes first" for them to take effect.')}
              </div>}

            {newOrder && (
              <div className="vs-editor">
                <div className="vs-form">
                  <label>{t('Sales Invoice')}
                    <LinkField doctype="Sales Invoice" company={company}
                      value={newOrder.sales_invoice} placeholder="ACC-SINV-…"
                      onChange={(v) => setNewOrder({ ...newOrder, sales_invoice: v })} />
                    <span className="studio-hint">
                      {deferred.length} {t('supplies currently deferred')}
                      {deferred.length > 0 && <> · <button className="vat-drill-link"
                        onClick={() => setNewOrder({ ...newOrder, sales_invoice: deferred[0].voucher_no })}>
                        {t('oldest')}: {deferred[0].voucher_no}</button></>}
                    </span>
                  </label>
                  <label>{t('Payment order date')}
                    <input type="date" value={newOrder.order_date}
                      onChange={(e) => setNewOrder({ ...newOrder, order_date: e.target.value })} />
                  </label>
                  <label>{t('Order reference')}
                    <input value={newOrder.order_reference}
                      onChange={(e) => setNewOrder({ ...newOrder, order_reference: e.target.value })} />
                    <span className="studio-hint">{t('What ZATCA asks for on query.')}</span>
                  </label>
                  <label>{t('Amount (part orders only)')}
                    <input type="number" placeholder={t('Blank = whole invoice')} value={newOrder.amount}
                      onChange={(e) => setNewOrder({ ...newOrder, amount: e.target.value })} />
                    <span className="studio-hint">{t('A part-ordered invoice is flagged for a decision, not released.')}</span>
                  </label>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                  <button className="vs-btn" onClick={saveOrder} disabled={busy}>
                    {busy ? t('Saving…') : t('Record order')}
                  </button>
                  <button className="vs-btn ghost" onClick={() => setNewOrder(null)}>{t('Cancel')}</button>
                </div>
              </div>
            )}

            {orders.length === 0 && !newOrder
              ? <div className="vs-empty">{t('None recorded. Supplies are released on payment receipt alone.')}</div>
              : orders.length > 0 && (
                <table className="studio-table vs-table">
                  <thead><tr>
                    <th>{t('Order date')}</th><th>{t('Invoice')}</th><th>{t('Customer')}</th>
                    <th>{t('Reference')}</th><th>{t('Amount')}</th><th />
                  </tr></thead>
                  <tbody>
                    {orders.map((o) => (
                      <tr key={o.name}>
                        <td style={{ whiteSpace: 'nowrap' }}>{o.order_date}</td>
                        <td>{o.sales_invoice}</td>
                        <td>{o.customer}</td>
                        <td>{o.order_reference || <em className="studio-hint">{t('none')}</em>}</td>
                        <td>{o.amount
                          ? <span className="vs-pill warn">{t('Part')} {Number(o.amount).toLocaleString()}</span>
                          : <span className="vs-pill ok">{t('Whole invoice')}</span>}</td>
                        <td>{data.can_write &&
                          <button className="vat-drill-link" onClick={() => removeOrder(o.name)} disabled={busy}>{t('Remove')}</button>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
          </section>

          {/* ---------------- Adjustments ---------------- */}
          <section className="vs-card">
            <h3>{t('Period adjustments')}</h3>
            <p className="theme-hint">
              {t('Per-voucher include/exclude decisions, whether keyed by hand from the return drill-down or written by the deferral engine. The reason is what an auditor reads, so it is shown in full.')}
            </p>
            {adjustments.length === 0
              ? <div className="vs-empty">{t('None.')}</div>
              : <table className="studio-table vs-table">
                  <thead><tr>
                    <th>{t('Period')}</th><th>{t('Voucher')}</th>
                    <th>{t('Action')}</th><th>{t('Reason')}</th>
                  </tr></thead>
                  <tbody>
                    {adjustments.map((a) => (
                      <tr key={a.name}>
                        <td style={{ whiteSpace: 'nowrap' }}>{a.from_date} → {a.to_date}</td>
                        <td>{a.voucher_no}</td>
                        <td><span className={a.action === 'Include' ? 'vs-pill ok' : 'vs-pill warn'}>{t(a.action)}</span></td>
                        <td className="vs-reason">{a.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>}
          </section>
        </>
      )}
    </div>
  );
}
