import { useState } from 'react';
import { t } from '../../utils/i18n';
import { api } from '../../utils/api';

/**
 * v2.88.13 — paste head counts from a table (Excel, a screenshot retyped, a
 * previous report). One cost centre per line: its name, then Jan..Dec. Cost
 * centres are matched by name, blanks are skipped (so months left blank can be
 * filled by Capture from HR), frozen entries are never touched, and Check
 * shows what would happen before anything is written.
 */
interface Props { rule: string; year: number; onClose: () => void; onDone: () => void; }

// v2.89.4 — the example used to look like real data. People took the grey
// placeholder for figures they had typed, and Check and Import stayed
// disabled because the box was in fact empty.
const EXAMPLE = 'Example — type or paste over this:\nAudit & GRC,8,8,8,8,9,8,8\nInternal Audit,6,6,6,5,5,5,5';

export function ImportHeadCountsModal({ rule, year, onClose, onDone }: Props) {
  const [text, setText] = useState('');
  const [overwrite, setOverwrite] = useState(false);
  const [basis, setBasis] = useState<'head_count' | 'amount'>('head_count');
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [done, setDone] = useState(false);

  async function go(dry: boolean) {
    setBusy(true); setErr('');
    try {
      const r = await api.allocationImportHeadCounts(rule, year, text, overwrite, dry, basis);
      setRes(r);
      if (!dry) { setDone(true); onDone(); }
      return r;
    } catch (e: any) { setErr(e?.message || String(e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel" role="dialog" style={{ maxWidth: 720 }}>
        <div className="theme-h">
          <h3>{t('Paste figures')} — {rule} {year}</h3>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>
        <div className="cap-body">
          <div style={{ display: 'flex', gap: 16, marginBottom: 6 }}>
            <label className="chk"><input type="radio" checked={basis === 'head_count'}
              onChange={() => { setBasis('head_count'); setRes(null); }} /> {t('Head count table')}</label>
            <label className="chk"><input type="radio" checked={basis === 'amount'}
              onChange={() => { setBasis('amount'); setRes(null); }} /> {t('Amount table (taken out of the pool first)')}</label>
          </div>
          <p className="bk-hint">
            {t('One cost centre per line: its name, then the months from Jan. Separate with commas or tabs (a copy from Excel works). Leave a month blank to skip it.')}
          </p>
          <textarea value={text} rows={12} spellCheck={false}
            onChange={(e) => { setText(e.target.value); setRes(null); setDone(false); }}
            placeholder={EXAMPLE}
            style={{ width: '100%', fontFamily: 'monospace', fontSize: 12 }} />
          <label className="chk">
            <input type="checkbox" checked={overwrite} onChange={(e) => { setOverwrite(e.target.checked); setRes(null); }} />
            {' '}{t('Replace values that already exist (frozen months are never changed)')}
          </label>
          {!text.trim() && <div className="bk-hint">{t('The box is empty — the grey lines are only an example.')}</div>}
          {err && <div className="alloc-warn">{err}</div>}
          {res && (
            <div className={res.unmatched?.length ? 'alloc-warn' : 'alloc-ok'}>
              <strong>{res.dry_run ? t('Check') : t('Imported')}:</strong>{' '}
              {res.created} {t('new')} · {res.updated} {t('replaced')} · {res.skipped_existing} {t('left as they were')}
              {res.skipped_frozen ? ` · ${res.skipped_frozen} ${t('frozen, untouched')}` : ''}
              {!!res.unmatched?.length && (
                <div>{t('Not matched to a cost centre:')} <strong>{res.unmatched.join(', ')}</strong>.{' '}
                  {t('Nothing was imported. Correct the names (they must match the cost centre names in ERPNext) and press Import again.')}</div>
              )}
            </div>
          )}
          <div className="bk-foot cap-foot">
            <button className="bk-btn" disabled={busy || !text.trim()} onClick={() => go(true)}>{t('Check')}</button>
            {/* v2.89.4 — Import no longer waits for a separate Check: it checks
                first and imports only when every line matched a cost centre. */}
            <button className="bk-btn bk-primary" disabled={busy || !text.trim() || done}
              onClick={async () => {
                const r: any = await go(true);
                if (r && !r.unmatched?.length) await go(false);
              }}>{busy ? t('Working…') : t('Import')}</button>
            <button className="bk-btn" onClick={onClose}>{t('Close')}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
