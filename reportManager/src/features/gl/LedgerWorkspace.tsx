import { useState } from 'react';
import { t } from '../../utils/i18n';
import { GeneralLedger, type LedgerMode } from './GeneralLedger';

/* v2.57.0 — three ledgers, one engine.
 *
 * A Supplier Ledger and a Customer Ledger are the same query as the General
 * Ledger with the subject fixed to a party. Writing them as separate screens
 * would have been quicker today and wrong by month nine: someone fixes a
 * running-balance edge case in one and not the other, and two screens report
 * different balances for the same supplier. In an audited ledger that is not
 * a cosmetic bug.
 *
 * So the tabs are configuration. Each mounts the same component with a
 * different preset; the query, the running balance and all five export
 * formats are shared and cannot drift.
 *
 * `key` on the element is deliberate: switching tabs remounts rather than
 * reusing state, so a supplier ledger never inherits the account ledger's
 * column picks or half-typed filters.
 */

const TABS: { mode: LedgerMode; label: string; title: string }[] = [
  { mode: 'accounts', label: 'Accounts', title: 'Grouped by account — the classic general ledger' },
  { mode: 'supplier', label: 'Supplier', title: 'Grouped by supplier, across the payable control accounts' },
  { mode: 'customer', label: 'Customer', title: 'Grouped by customer, across the receivable control accounts' },
];

export function LedgerWorkspace({ reportName }: { reportName?: string }) {
  const [mode, setMode] = useState<LedgerMode>('accounts');

  return (
    <div>
      <nav className="view-subtabs" role="tablist" aria-label={t('Ledger')}>
        {TABS.map((tb) => (
          <button
            key={tb.mode}
            role="tab"
            aria-selected={mode === tb.mode}
            title={t(tb.title)}
            className={'view-subtab' + (mode === tb.mode ? ' is-active' : '')}
            onClick={() => setMode(tb.mode)}
          >
            {t(tb.label)}
          </button>
        ))}
      </nav>
      <GeneralLedger key={mode} reportName={reportName} mode={mode} />
    </div>
  );
}

export default LedgerWorkspace;
