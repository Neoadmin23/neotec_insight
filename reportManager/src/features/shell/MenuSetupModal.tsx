import { useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import type { Workspace } from '../../types';

export interface MenuTabCfg { ws: Workspace; hidden?: number }
export interface MenuSectionCfg { key: string; label: string; tabs: MenuTabCfg[] }

/** Frontend menu manager (v2.25.0). Edits a plain {sections:[{key,label,tabs}]}
 *  structure; App.tsx merges it with the built-in catalog, so unknown tabs are
 *  ignored and new tabs from future versions still appear. */
export default function MenuSetupModal({ sections, catalogLabels, onSaved, onClose, initialNumFormat }: {
  sections: MenuSectionCfg[];
  catalogLabels: Record<string, string>;
  onSaved: () => void;
  initialNumFormat?: string;
  onClose: () => void;
}) {
  const [secs, setSecs] = useState<MenuSectionCfg[]>(() => JSON.parse(JSON.stringify(sections)));
  const [numFormat, setNumFormat] = useState<string>(initialNumFormat || 'western');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const move = <T,>(arr: T[], i: number, d: number): T[] => {
    const j = i + d;
    if (j < 0 || j >= arr.length) return arr;
    const a = [...arr]; const [x] = a.splice(i, 1); a.splice(j, 0, x);
    return a;
  };

  const updSec = (i: number, patch: Partial<MenuSectionCfg>) =>
    setSecs((s) => s.map((x, k) => (k === i ? { ...x, ...patch } : x)));

  function moveTab(si: number, ti: number, targetKey: string) {
    setSecs((s) => {
      const copy = JSON.parse(JSON.stringify(s)) as MenuSectionCfg[];
      const [tab] = copy[si].tabs.splice(ti, 1);
      const tgt = copy.find((x) => x.key === targetKey);
      if (tgt) tgt.tabs.push(tab); else copy[si].tabs.splice(ti, 0, tab);
      return copy;
    });
  }

  function addSection() {
    const label = prompt(t('New section name:'));
    if (!label) return;
    const key = label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || `sec_${Date.now()}`;
    if (secs.some((s) => s.key === key)) { setMsg(t('A section with that name exists.')); return; }
    setSecs((s) => [...s, { key, label, tabs: [] }]);
  }

  function applyPreset(region: 'ksa' | 'india') {
    // v2.37.1 — one-click region preset: hides the other jurisdiction's tabs
    // and stamps the site-wide number format into the saved menu config.
    const hide = region === 'india' ? ['vat', 'zakat', 'packs'] : ['gst'];
    const keep = region === 'india' ? ['gst'] : ['vat', 'zakat', 'packs'];
    setSecs((m) => m.map((sec) => ({ ...sec, tabs: sec.tabs.map((tb) =>
      hide.includes(tb.ws) ? { ...tb, hidden: 1 } : keep.includes(tb.ws) ? { ...tb, hidden: 0 } : tb) })));
    setNumFormat(region === 'india' ? 'indian' : 'western');
  }

  async function save() {
    const visible = secs.flatMap((s) => s.tabs.filter((tb) => !tb.hidden));
    if (!visible.length) { setMsg(t('At least one tab must stay visible.')); return; }
    setBusy(true); setMsg('');
    try {
      await api.saveMenu({ sections: secs.filter((s) => s.tabs.length || s.label), num_format: numFormat });
      onSaved(); onClose();
    } catch (e: any) { setMsg(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  async function reset() {
    if (!confirm(t('Reset the menu to the default layout?'))) return;
    setBusy(true);
    try { await api.resetMenu(); onSaved(); onClose(); }
    catch (e: any) { setMsg(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel" role="dialog" aria-label={t('Menu setup')} style={{ width: 'min(720px, 100%)' }}>
        <div className="theme-h">
          <h3>☰ {t('Menu setup')}</h3>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>
        <p className="theme-hint">{t('Arrange sections and tabs for everyone on this site. Single-tab sections appear without a sub-menu row. Hidden tabs disappear from the menu (their data is untouched).')}</p>

        {secs.map((sec, si) => (
          <div key={sec.key} className="menu-sec">
            <div className="menu-sec-h">
              <span className="menu-arrows">
                <button onClick={() => setSecs((s) => move(s, si, -1))} disabled={si === 0} aria-label={t('Move section up')}>↑</button>
                <button onClick={() => setSecs((s) => move(s, si, 1))} disabled={si === secs.length - 1} aria-label={t('Move section down')}>↓</button>
              </span>
              <input className="menu-sec-name" value={sec.label}
                onChange={(e) => updSec(si, { label: e.target.value })} />
              <span className="menu-count">{sec.tabs.filter((x) => !x.hidden).length} {t('visible')}</span>
              {sec.tabs.length === 0 && (
                <button className="studio-ghost" onClick={() => setSecs((s) => s.filter((_, k) => k !== si))}>{t('Remove')}</button>
              )}
            </div>
            {sec.tabs.map((tab, ti) => (
              <div key={tab.ws} className={'menu-tab-row' + (tab.hidden ? ' hid' : '')}>
                <span className="menu-arrows">
                  <button onClick={() => updSec(si, { tabs: move(sec.tabs, ti, -1) })} disabled={ti === 0} aria-label={t('Move up')}>↑</button>
                  <button onClick={() => updSec(si, { tabs: move(sec.tabs, ti, 1) })} disabled={ti === sec.tabs.length - 1} aria-label={t('Move down')}>↓</button>
                </span>
                <span className="menu-tab-name">{catalogLabels[tab.ws] || tab.ws}</span>
                <select value={sec.key} onChange={(e) => moveTab(si, ti, e.target.value)} title={t('Move to section')}>
                  {secs.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
                </select>
                <label className="menu-hide">
                  <input type="checkbox" checked={!tab.hidden}
                    onChange={(e) => updSec(si, { tabs: sec.tabs.map((x, k) => k === ti ? { ...x, hidden: e.target.checked ? 0 : 1 } : x) })} />
                  {t('Visible')}
                </label>
              </div>
            ))}
            {sec.tabs.length === 0 && <div className="studio-hint" style={{ padding: '4px 10px 8px' }}>{t('Empty — move tabs here from another section.')}</div>}
          </div>
        ))}

        <div style={{ display: 'flex', gap: 8, marginTop: 14, alignItems: 'center' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
            <span className="theme-hint">{t('Region preset')}:</span>
            <button className="studio-ghost" onClick={() => applyPreset('ksa')}>🇸🇦 {t('KSA (VAT · Zakat · Packs)')}</button>
            <button className="studio-ghost" onClick={() => applyPreset('india')}>🇮🇳 {t('India (GST · lakh/crore)')}</button>
            <span className="theme-hint">{t('Applies hidden tabs + site number format — Save to commit for ALL users of this site.')}</span>
          </div>
          <button className="studio-run" onClick={save} disabled={busy}>{busy ? t('Saving…') : t('Save menu')}</button>
          <button className="studio-ghost" onClick={addSection}>+ {t('New section')}</button>
          <span style={{ flex: 1 }} />
          <button className="studio-ghost" onClick={reset} disabled={busy}>{t('Reset to default')}</button>
        </div>
        {msg && <div className="theme-err" style={{ marginTop: 8 }}>{msg}</div>}
      </div>
    </div>
  );
}
