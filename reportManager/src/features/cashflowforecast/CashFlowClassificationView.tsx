import { Fragment, useCallback, useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

/* Cash Flow Classification (v2.87.0) — Phase C (Queue) + Phase D (Rule Review).
 *
 * Same isolation boundary as the rest of Cash Flow Forecast. This is the
 * human-in-the-loop half of the cascade: Account Binding and Overrides are
 * tried first (server-side, before a transaction ever reaches this list);
 * what's shown here is only what neither resolved. Every suggestion is
 * exactly that — a suggestion, tier-labeled High/Medium/Low/Conflict — and
 * nothing here is ever auto-applied without a person clicking Confirm.
 */

type Suggestion = {
  tier: 'high' | 'medium' | 'low' | 'conflict' | 'none';
  target_line: string | null;
  confidence: number | null;
  rule: string | null;
  alternatives: { rule: string; target_line: string; confidence: number }[];
};

type QueueRow = {
  voucher_type: string;
  voucher_no: string;
  account: string;
  posting_date: string;
  debit: number;
  credit: number;
  against_account: string | null;
  remarks: string | null;
  cost_center: string | null;
  inferred_type: string;
  suggestion: Suggestion;
};

type Rule = {
  name: string;
  pattern: string;
  match_field: string;
  target_line: string;
  status: string;
  historical_support: number;
  historical_precision: number;
  rolling_precision: number | null;
  times_suggested: number;
  times_confirmed: number;
  times_corrected: number;
  sample_transactions: string;
};

type Line = { name: string; label: string; direction: string };

function fmt(n: number | undefined | null): string {
  const v = n || 0;
  if (v === 0) return '—';
  const s = Math.abs(Math.round(v)).toLocaleString('en-US');
  return v < 0 ? `(${s})` : s;
}

const TIER_LABEL: Record<string, string> = {
  high: 'High', medium: 'Medium', low: 'Low', conflict: 'Conflict', none: 'No match',
};

export function CashFlowClassificationView({ fiscalYear, company, bankAccounts }:
  { fiscalYear: number; company: string | null; bankAccounts: string[] }) {
  const [sub, setSub] = useState<'queue' | 'rules'>('queue');
  const [queue, setQueue] = useState<{ total_unclassified: number; shown: number; transactions: QueueRow[] } | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [ruleFilter, setRuleFilter] = useState<string>('Candidate');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chosenLine, setChosenLine] = useState<Record<string, string>>({});
  const [mining, setMining] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const loadQueue = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [q, ls] = await Promise.all([
        api.cashFlowClassificationQueue(fiscalYear, company, bankAccounts),
        api.cashFlowForecastLines(false),
      ]);
      setQueue(q);
      setLines(ls || []);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [fiscalYear, company, bankAccounts]);

  const loadRules = useCallback(async () => {
    const rows = await api.cashFlowClassificationRules(ruleFilter || null);
    setRules(rows || []);
  }, [ruleFilter]);

  useEffect(() => { if (sub === 'queue') loadQueue(); }, [sub, loadQueue]);
  useEffect(() => { if (sub === 'rules') loadRules(); }, [sub, loadRules]);

  function txnKey(r: QueueRow) { return `${r.voucher_type}::${r.voucher_no}`; }

  async function confirm(r: QueueRow, line: string) {
    const s = r.suggestion;
    // Provenance only applies when the accountant confirmed (or overrode)
    // an actual suggestion from a rule — a row with no suggestion at all,
    // or a conflict the accountant resolved by picking neither alternative,
    // is recorded as Manual, same as today's process.
    const suggestedByRule = (s.tier === 'high' || s.tier === 'medium' || s.tier === 'low') ? s.rule : null;
    const suggestedLine = suggestedByRule ? s.target_line : null;
    await api.cashFlowClassificationConfirm(
      r.voucher_type, r.voucher_no, line, null,
      suggestedByRule, suggestedLine, suggestedByRule ? s.confidence : null,
    );
    await loadQueue();
  }

  async function reject(r: QueueRow) {
    const ruleName = r.suggestion.rule;
    if (!ruleName) return;
    await api.cashFlowClassificationReject(ruleName);
    await loadQueue();
  }

  async function batchConfirmHighConfidence() {
    if (!queue) return;
    const items = queue.transactions
      .filter((r) => r.suggestion.tier === 'high' && r.suggestion.target_line)
      .map((r) => ({
        voucher_type: r.voucher_type, voucher_no: r.voucher_no,
        line: r.suggestion.target_line, suggested_by_rule: r.suggestion.rule,
        suggested_line: r.suggestion.target_line, confidence: r.suggestion.confidence,
      }));
    if (!items.length) return;
    if (!window.confirm(t(`Confirm all ${items.length} high-confidence suggestions?`))) return;
    await api.cashFlowClassificationBatchConfirm(items);
    await loadQueue();
  }

  async function mine() {
    setMining(true);
    try {
      const res = await api.cashFlowClassificationMineRules();
      window.alert(t(`Mined ${res.mined} candidate patterns, ${res.created} new (${res.skipped_existing} already existed).`));
      if (sub === 'rules') await loadRules();
    } finally {
      setMining(false);
    }
  }

  async function setRuleStatus(name: string, status: string) {
    await api.cashFlowClassificationSetRuleStatus(name, status);
    await loadRules();
  }

  const highCount = queue?.transactions.filter((r) => r.suggestion.tier === 'high').length || 0;

  return (
    <div className="cff-classify">
      <div className="cff-view-toggle" style={{ marginTop: 0 }}>
        <button className={sub === 'queue' ? 'active' : ''} onClick={() => setSub('queue')}>{t('Queue')}</button>
        <button className={sub === 'rules' ? 'active' : ''} onClick={() => setSub('rules')}>{t('Rules')}</button>
      </div>

      {sub === 'queue' && (
        <>
          <div className="cff-toolbar">
            <button className="cff-btn-sm" onClick={loadQueue} disabled={loading}>
              {loading ? t('Loading…') : t('Refresh')}
            </button>
            <button className="cff-btn-sm" onClick={mine} disabled={mining}>
              {mining ? t('Mining…') : t('Mine new rules from history')}
            </button>
            {highCount > 0 && (
              <button className="cff-btn-primary" onClick={batchConfirmHighConfidence}>
                {t('Confirm all high-confidence')} ({highCount})
              </button>
            )}
          </div>
          {error && <div className="cff-error">{error}</div>}
          {queue && (
            <div className="cff-queue-summary">
              {t('Showing')} {queue.shown} {t('of')} {queue.total_unclassified} {t('unclassified transactions')}
              {' — '}{t('everything else is already covered by an account binding or a prior decision.')}
            </div>
          )}

          <div className="cff-queue-list">
            {queue?.transactions.map((r) => {
              const key = txnKey(r);
              const s = r.suggestion;
              return (
                <div key={key} className="cff-queue-row">
                  <div className="cff-queue-main">
                    <div className="cff-queue-remarks">{r.remarks || <em>{t('(no remarks)')}</em>}</div>
                    <div className="cff-queue-meta">
                      <span>{r.account}</span>
                      {r.against_account && <span> · {r.against_account}</span>}
                      {r.cost_center && <span> · {r.cost_center}</span>}
                      <span> · {r.posting_date}</span>
                      <span className={`cff-dir-tag ${r.inferred_type === 'Cash Out' ? 'out' : 'in'}`}>
                        {r.inferred_type === 'Cash Out' ? t('OUT') : t('IN')}
                      </span>
                      <span className="cff-queue-amt">{fmt(r.debit || r.credit)}</span>
                    </div>
                  </div>

                  <div className="cff-queue-suggestion">
                    <span className={`cff-tier-badge cff-tier-${s.tier}`}>{t(TIER_LABEL[s.tier])}</span>
                    {s.target_line && (
                      <span className="cff-queue-line">{s.target_line}
                        {s.confidence != null && <span className="cff-queue-conf"> {s.confidence.toFixed(0)}%</span>}
                      </span>
                    )}
                    {s.tier === 'conflict' && (
                      <span className="cff-queue-alts">
                        {s.alternatives.map((a) => `${a.target_line} (${a.confidence.toFixed(0)}%)`).join(' vs. ')}
                      </span>
                    )}
                  </div>

                  <div className="cff-queue-actions">
                    <select value={chosenLine[key] ?? s.target_line ?? ''}
                      onChange={(e) => setChosenLine({ ...chosenLine, [key]: e.target.value })}>
                      <option value="">{t('— choose a line —')}</option>
                      {lines.map((l) => <option key={l.name} value={l.name}>{l.label}</option>)}
                    </select>
                    <button className="cff-btn-sm" disabled={!chosenLine[key] && !s.target_line}
                      onClick={() => confirm(r, chosenLine[key] || s.target_line!)}>
                      {t('Confirm')}
                    </button>
                    {s.rule && (
                      <button className="cff-btn-x" title={t('Reject this suggestion — the rule learns it missed')}
                        onClick={() => reject(r)}>×</button>
                    )}
                  </div>
                </div>
              );
            })}
            {queue && queue.transactions.length === 0 && (
              <div className="cff-setup-empty">{t('Nothing unclassified for this period — every real cash movement is already covered.')}</div>
            )}
          </div>
        </>
      )}

      {sub === 'rules' && (
        <>
          <div className="cff-toolbar">
            <select className="cff-input" value={ruleFilter} onChange={(e) => setRuleFilter(e.target.value)}>
              <option value="">{t('All statuses')}</option>
              <option value="Candidate">{t('Candidate')}</option>
              <option value="Under Review">{t('Under Review')}</option>
              <option value="Approved">{t('Approved')}</option>
              <option value="Active">{t('Active')}</option>
              <option value="Suspended">{t('Suspended')}</option>
              <option value="Retired">{t('Retired')}</option>
            </select>
            <button className="cff-btn-sm" onClick={mine} disabled={mining}>
              {mining ? t('Mining…') : t('Mine new rules from history')}
            </button>
          </div>
          <div className="cff-rules-list">
            {rules.map((rule) => (
              <div key={rule.name} className="cff-rule-card">
                <div className="cff-rule-hdr">
                  <code>{rule.pattern}</code>
                  <span className="cff-rule-arrow">→</span>
                  <strong>{rule.target_line}</strong>
                  <span className={`cff-tier-badge cff-status-${rule.status.toLowerCase().replace(' ', '-')}`}>{rule.status}</span>
                </div>
                <div className="cff-rule-evidence">
                  {t('Matches')}: {rule.match_field} · {t('Support')}: {rule.historical_support} · {t('Precision')}: {rule.historical_precision}%
                  {rule.rolling_precision != null && ` · ${t('Live precision')}: ${rule.rolling_precision}% (${rule.times_confirmed}/${rule.times_confirmed + rule.times_corrected})`}
                </div>
                {rule.sample_transactions && (
                  <div className="cff-rule-samples">
                    {rule.sample_transactions.split('\n').filter(Boolean).map((s, i) => <div key={i}>“{s}”</div>)}
                  </div>
                )}
                <div className="cff-rule-actions">
                  {rule.status === 'Candidate' && (
                    <button className="cff-btn-sm" onClick={() => setRuleStatus(rule.name, 'Under Review')}>{t('Start review')}</button>
                  )}
                  {rule.status === 'Under Review' && (
                    <>
                      <button className="cff-btn-sm" onClick={() => setRuleStatus(rule.name, 'Approved')}>{t('Approve')}</button>
                      <button className="cff-btn-sm" onClick={() => setRuleStatus(rule.name, 'Retired')}>{t('Retire')}</button>
                    </>
                  )}
                  {rule.status === 'Approved' && (
                    <button className="cff-btn-primary" onClick={() => setRuleStatus(rule.name, 'Active')}>{t('Activate')}</button>
                  )}
                  {rule.status === 'Active' && (
                    <button className="cff-btn-danger" onClick={() => setRuleStatus(rule.name, 'Suspended')}>{t('Suspend')}</button>
                  )}
                  {rule.status === 'Suspended' && (
                    <>
                      <button className="cff-btn-sm" onClick={() => setRuleStatus(rule.name, 'Active')}>{t('Reactivate')}</button>
                      <button className="cff-btn-danger" onClick={() => setRuleStatus(rule.name, 'Retired')}>{t('Retire')}</button>
                    </>
                  )}
                </div>
              </div>
            ))}
            {rules.length === 0 && (
              <div className="cff-setup-empty">{t('No rules in this status. Mine new ones from confirmed history, or change the filter.')}</div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
