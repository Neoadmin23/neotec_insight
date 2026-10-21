import { useState } from 'react';
import { api } from '../../utils/api';
import { runAiJob } from '../../utils/aiJob';
import { t } from '../../utils/i18n';
import type { Visual } from '../../utils/boardCore';

/* v3.0.0 — describe a dashboard or a financial statement; the AI drafts it,
 * the server checks it, you review it here before anything is saved. */

export interface BoardDraft { title: string; layout: Visual[]; notes: string[]; source: string }

export function AiDesign({ onBoard, onClose }: { onBoard: (d: BoardDraft) => void; onClose: () => void }) {
  const [kind, setKind] = useState<'board' | 'statement'>('board');
  const [st, setSt] = useState('pnl');
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [secs, setSecs] = useState(0);
  const [err, setErr] = useState('');
  const [draft, setDraft] = useState<any>(null);
  const [saved, setSaved] = useState('');

  async function go() {
    setBusy(true); setErr(''); setDraft(null); setSaved('');
    try {
      const r = await runAiJob<any>(() => api.aiDesignStart(kind, prompt, st), setSecs);
      if (kind === 'board') { onBoard(r); onClose(); } else setDraft(r);
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }
  async function save() {
    try { const r = await api.aiDesignSaveStatement(draft); setSaved(`${r.name} · ${r.rows} ${t('rows')} · ${r.mappings} ${t('accounts mapped')}`); }
    catch (e: any) { setErr(String(e?.message || e)); }
  }
  const examples = kind === 'board'
    ? [t('A CFO dashboard with margins, cash cycle and top customers'), t('A sales dashboard: top customers, concentration and what moved revenue')]
    : [t('A management P&L with gross profit, EBITDA, below-the-line items and allocations at the bottom'), t('A balance sheet with current and non-current sections and working capital')];

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel" role="dialog" style={{ width: 'min(820px, 100%)' }}>
        <div className="theme-h"><h3>✨ {t('Design with AI')}</h3><button type="button" className="cp-x" onClick={onClose}>×</button></div>
        <div className="studio-rel-form">
          <div className="cp-seg" role="tablist">
            <button type="button" className={kind === 'board' ? 'on' : ''} onClick={() => { setKind('board'); setDraft(null); }}>{t('Dashboard')}</button>
            <button type="button" className={kind === 'statement' ? 'on' : ''} onClick={() => { setKind('statement'); setDraft(null); }}>{t('Financial statement')}</button>
          </div>
          {kind === 'statement' && (
            <select value={st} onChange={(e) => setSt(e.target.value)}>
              <option value="pnl">{t('Profit and loss')}</option><option value="balance_sheet">{t('Balance sheet')}</option>
            </select>
          )}
          <textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder={examples[0]} />
          <div className="cp-sugg">{examples.map((x) => <button type="button" key={x} onClick={() => setPrompt(x)}>{x}</button>)}</div>
          <button type="button" className="bk-btn bk-primary" disabled={busy || !prompt.trim()} onClick={go}>
            {busy ? `${t('Designing…')} ${secs ? secs + 's' : ''}` : t('Design')}</button>
          {err && <div className="alloc-warn">{err}</div>}
        </div>
        {draft && (
          <div className="ad-review">
            <h4>{draft.title} <small>· {draft.company}</small></h4>
            {!!draft.errors?.length && (
              <div className="alloc-warn"><b>{t('Must be fixed before saving')}</b>
                <ul>{draft.errors.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
                <div>{t('Rephrase the request or press Design again.')}</div></div>
            )}
            {!!draft.warnings?.length && <ul className="ad-warn">{draft.warnings.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>}
            {draft.coverage?.missing_top?.length > 0 && (
              <details><summary>{t('Active accounts not in the statement')} ({draft.coverage.missing_active})</summary>
                <ul className="ad-warn">{draft.coverage.missing_top.map((m: any) => <li key={m.account}>{m.account} · {Number(m.activity).toLocaleString()}</li>)}</ul>
              </details>
            )}
            <div className="cp-table-wrap ad-rows"><table className="cp-table">
              <thead><tr><th>{t('Row')}</th><th>{t('Kind')}</th><th>{t('Built from')}</th></tr></thead>
              <tbody>{(draft.definition?.rows || []).map((r: any) => (
                <tr key={r.key} className={'ad-' + r.kind}>
                  <td>{r.label} <small>{r.key}</small></td><td>{t(r.kind)}</td>
                  <td>{r.kind === 'formula' ? <code>{r.formula}</code>
                    : r.kind === 'source' ? (draft.mappings || []).filter((m: any) => m.flag === r.flag).map((m: any) => m.account).join(', ') || '—'
                    : r.kind === 'allocation' ? r.allocation_rule : ''}</td>
                </tr>))}</tbody>
            </table></div>
            {saved ? <div className="alloc-ok">✓ {t('Draft report created (inactive)')}: {saved}. {t('Open Reports → Rows to refine and activate it.')}</div>
              : <button type="button" className="bk-btn bk-primary" disabled={!!draft.errors?.length} onClick={save}>{t('Create draft report')}</button>}
          </div>
        )}
      </div>
    </div>
  );
}
