import { useEffect, useMemo, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

interface AccRow {
  account: string; label: string; number: string;
  root_type: string; account_type: string; tag: string; implied: string;
}

/** Account Classification Studio (v2.26.0) — tag an account once; Financial
 *  Health (COGS), Cash Flow (cash/investing/financing) and Zakat all obey it.
 *  Custom labels ("label:<Name>") are management's own vocabulary. */
/** `embedded` drops the modal chrome so the same component can be a first-class
 *  workspace tab as well as the dialog behind Financial Health's 🏷 button.
 *  One component, two frames — forking it would have left two copies of the
 *  tagging rules to keep in step, and the tag is what Health, Cash Flow and
 *  Zakat all read. */
export default function ClassificationStudio({ company, onSaved, onClose, embedded }: {
  company: string; onSaved?: () => void; onClose?: () => void; embedded?: boolean;
}) {
  const [rows, setRows] = useState<AccRow[]>([]);
  const [systemTags, setSystemTags] = useState<any[]>([]);
  const [customLabels, setCustomLabels] = useState<string[]>([]);
  const [changes, setChanges] = useState<Record<string, string>>({});
  const [search, setSearch] = useState('');
  const [root, setRoot] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  useEffect(() => {
    api.getClassification(company || null).then((d) => {
      setRows(d.accounts || []); setSystemTags(d.system_tags || []); setCustomLabels(d.custom_labels || []);
    }).catch((e: any) => setMsg(String(e?.message || e)));
  }, [company]);

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((r) =>
      (!root || r.root_type === root) &&
      (!q || r.account.toLowerCase().includes(q) || r.label.toLowerCase().includes(q) || r.number.includes(q)));
  }, [rows, search, root]);

  const effTag = (r: AccRow) => changes[r.account] !== undefined ? changes[r.account] : r.tag;

  function setTag(r: AccRow, tag: string) {
    if (tag === '__new_label__') {
      const name = prompt(t('New label name (e.g. Direct Project Costs):'));
      if (!name) return;
      const clean = name.trim().slice(0, 50);
      if (!customLabels.includes(clean)) setCustomLabels((l) => [...l, clean].sort());
      tag = 'label:' + clean;
    }
    setChanges((c) => ({ ...c, [r.account]: tag }));
  }

  async function save() {
    setBusy(true); setMsg('');
    try {
      await api.saveClassification(company || null, changes);
      setMsg(t('Saved — Financial Health, Cash Flow and Zakat now use these tags.'));
      setRows((rs) => rs.map((r) => changes[r.account] !== undefined ? { ...r, tag: changes[r.account] } : r));
      setChanges({});
      onSaved && onSaved();
    } catch (e: any) { setMsg(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  const tagName = (tag: string) => tag.startsWith('label:') ? tag.slice(6)
    : (systemTags.find((s) => s.key === tag)?.label || tag);
  const dirty = Object.keys(changes).length;

  const Frame = ({ children }: { children: any }) => embedded
    ? <div className="cls-page">{children}</div>
    : (
      <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}>
        <div className="theme-panel" role="dialog" aria-label={t('Account Classification')} style={{ width: 'min(880px, 100%)' }}>
          {children}
        </div>
      </div>
    );

  return (
    <Frame>
        <div className="theme-h">
          <h3>🏷 {t('Account Classification Studio')}</h3>
          {!embedded && <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>}
        </div>
        <p className="theme-hint">
          {t('Tag an account once and every report obeys it — COGS drives Financial Health margins & cycle days; Cash / Investing / Financing / Provision drive the Cash Flow statement and the Zakat base. Custom labels are your own groupings, totalled in Financial Health. Your tag overrides the account type and every name heuristic.')}
        </p>
        <div className="studio-frow" style={{ margin: '8px 0', gap: 8 }}>
          <input placeholder={t('Search account, name or number…')} value={search}
            onChange={(e) => setSearch(e.target.value)} style={{ flex: 1 }} />
          <select value={root} onChange={(e) => setRoot(e.target.value)}>
            <option value="">{t('All roots')}</option>
            {['Asset', 'Liability', 'Equity', 'Income', 'Expense'].map((x) => <option key={x} value={x}>{t(x)}</option>)}
          </select>
          <span className="studio-hint">{shown.length}/{rows.length}</span>
        </div>

        <div className="cls-table-wrap">
          <table className="studio-table" style={{ width: '100%' }}>
            <thead><tr>
              <th>{t('Account')}</th><th>{t('Type')}</th><th>{t('Counted as')}</th><th style={{ width: 240 }}>{t('Set')}</th>
            </tr></thead>
            <tbody>
              {shown.map((r) => {
                const cur = effTag(r);
                const counted = cur ? tagName(cur) : (r.implied ? tagName(r.implied) : '—');
                const src = cur ? 'you' : (r.implied ? 'type' : '');
                return (
                  <tr key={r.account} className={changes[r.account] !== undefined ? 'cls-dirty' : ''}>
                    <td>
                      {r.number && <span className="cls-num">{r.number}</span>}
                      {r.label}
                      <span className="cls-root">{t(r.root_type)}</span>
                    </td>
                    <td className="cls-type">{r.account_type || '—'}</td>
                    <td>
                      {counted !== '—'
                        ? <span className={'cls-badge ' + src}>{counted}{src === 'type' && <em> · {t('type')}</em>}</span>
                        : '—'}
                    </td>
                    <td>
                      <select value={cur} onChange={(e) => setTag(r, e.target.value)} style={{ width: '100%' }}>
                        <option value="">— {t('none')} —</option>
                        <optgroup label={t('System (drives reports)')}>
                          {systemTags.map((s) => <option key={s.key} value={s.key} title={s.hint}>{t(s.label)}</option>)}
                        </optgroup>
                        <optgroup label={t('Custom labels')}>
                          {customLabels.map((l) => <option key={l} value={'label:' + l}>{l}</option>)}
                          <option value="__new_label__">＋ {t('New label…')}</option>
                        </optgroup>
                      </select>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
          <button className="studio-run" onClick={save} disabled={busy || !dirty}>
            {busy ? t('Saving…') : t('Save') + (dirty ? ` (${dirty})` : '')}
          </button>
          {msg && <span className="theme-hint">{msg}</span>}
          {!embedded && <button className="studio-run" onClick={onClose}>{t('Close')}</button>}
        </div>
    </Frame>
  );
}
