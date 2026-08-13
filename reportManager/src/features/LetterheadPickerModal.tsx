import { useEffect, useState } from 'react';
import { api } from '../utils/api';

/* ─── Letter Head picker (v1.9.53) ────────────────────────────────────────
 *
 * Opens when the user clicks any Print or Export action. Shows the dropdown
 * of available Letter Heads (read from ERP's Letter Head DocType) with
 * the precedence-chain default pre-selected. The user can confirm, change,
 * or choose "Without letterhead" before the export proceeds.
 *
 * Design notes:
 *   - The dropdown options are LOADED ON DEMAND (when the modal opens),
 *     not at app boot. Letter Head changes infrequently and we don't want
 *     to fire a query on every page load just-in-case.
 *   - The default is resolved server-side via resolve_letterhead, which
 *     runs the full precedence chain (report → company → system).
 *   - We deliberately don't preview the letterhead in the modal. The
 *     name + an optional "default for this company" tag is enough; if
 *     the user picks wrong they re-export.
 *   - Cancel returns null; the caller should NOT proceed with the export.
 */

export interface LetterheadChoice {
  /** Letter Head document name, or empty string for "without letterhead". */
  name: string;
  /** Whether the user explicitly chose to export without letterhead. */
  withoutLetterhead: boolean;
}

interface Props {
  open: boolean;
  /** Report context, for resolving the default. */
  report?: string;
  /** Company context, for resolving the default. */
  company?: string;
  /** What's about to happen (just a label — "Export to Excel", "Print", etc.). */
  actionLabel: string;
  /** Called with the chosen Letter Head name, or null if the user cancelled. */
  onConfirm: (choice: LetterheadChoice) => void;
  onCancel: () => void;
}

export function LetterheadPickerModal({ open, report, company, actionLabel, onConfirm, onCancel }: Props) {
  const [letterHeads, setLetterHeads] = useState<Array<{ name: string; label: string; is_default: number }>>([]);
  const [selected, setSelected] = useState<string>('');
  const [defaultSource, setDefaultSource] = useState<string>('none');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    Promise.all([
      api.listLetterHeads(),
      api.resolveLetterhead(report, company),
    ])
      .then(([list, def]) => {
        if (cancelled) return;
        setLetterHeads(list || []);
        setSelected(def?.name || '');
        setDefaultSource(def?.source || 'none');
      })
      .catch((e: any) => {
        if (cancelled) return;
        setError(String(e?.message || 'Could not load letterheads.'));
        setLetterHeads([]);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, report, company]);

  if (!open) return null;

  function confirm() {
    onConfirm({ name: selected, withoutLetterhead: false });
  }
  function withoutLetterhead() {
    onConfirm({ name: '', withoutLetterhead: true });
  }

  const defaultNote = defaultSource === 'report' ? "Set as this report's default."
    : defaultSource === 'company' ? "Set as this company's default."
    : defaultSource === 'system' ? "System default."
    : null;

  return (
    <div className="lh-modal-backdrop">
      <div className="lh-modal-card">
        <div className="lh-modal-title">{actionLabel}</div>
        <div className="lh-modal-body">
          {loading && <div className="muted">Loading letterheads…</div>}
          {error && <div className="run-error">{error}</div>}
          {!loading && !error && (
            <>
              <label className="lh-modal-label">
                <span className="flbl">Letter Head</span>
                <select value={selected} onChange={(e) => setSelected(e.target.value)} disabled={letterHeads.length === 0}>
                  {letterHeads.length === 0 && <option value="">— No letterheads configured —</option>}
                  {letterHeads.map((lh) => (
                    <option key={lh.name} value={lh.name}>
                      {lh.label}{lh.is_default ? ' (system default)' : ''}
                    </option>
                  ))}
                </select>
              </label>
              {defaultNote && <div className="lh-modal-hint">{defaultNote}</div>}
              {letterHeads.length === 0 && (
                <div className="lh-modal-hint">
                  No Letter Heads found on this bench. Configure one via Frappe Desk → Letter Head, then re-open this dialog.
                  You can still proceed without letterhead.
                </div>
              )}
            </>
          )}
        </div>
        <div className="lh-modal-foot">
          <button onClick={onCancel} className="lh-modal-cancel">Cancel</button>
          <button onClick={withoutLetterhead} className="lh-modal-skip">Without letterhead</button>
          <button
            onClick={confirm}
            className="primary-btn"
            disabled={loading || !selected || !!error}
          >
            <i className="ti ti-download" aria-hidden /> Continue
          </button>
        </div>
      </div>
    </div>
  );
}
