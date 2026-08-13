import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

interface SheetCfg { type: string; title: string; options: Record<string, any> }
interface PackCfg { sheets: SheetCfg[]; language: string }

function q(v: string) { return encodeURIComponent(v || ''); }
function quarterStart() { const d = new Date(); const qm = Math.floor(d.getMonth() / 3) * 3; return new Date(d.getFullYear(), qm, 1).toISOString().slice(0, 10); }
function todayISO() { return new Date().toISOString().slice(0, 10); }

/** Export Packs (v2.28.0) — client-specific audit workbooks as CONFIGURATION.
 *  A pack = ordered sheet components from the backend catalog; the designer
 *  below renders each component's options from its schema, so new sheet types
 *  added on the backend appear here automatically. */
export default function ExportPacks() {
  const [packs, setPacks] = useState<any[]>([]);
  const [types, setTypes] = useState<any[]>([]);
  const [slug, setSlug] = useState('');
  const [companies, setCompanies] = useState<{ name: string; label: string }[]>([]);
  const [company, setCompany] = useState('');
  const [fromDate, setFromDate] = useState(quarterStart());
  const [toDate, setToDate] = useState(todayISO());
  const [editing, setEditing] = useState<{ slug?: string; title: string; description: string; config: PackCfg } | null>(null);
  const [glAccounts, setGlAccounts] = useState<{ name: string; label: string }[]>([]);
  const ensureGlAccounts = () => { if (!glAccounts.length) api.listGlAccounts(company || null).then(setGlAccounts).catch(() => {}); };
  const [msg, setMsg] = useState('');

  const refresh = () => api.listPacks().then((p) => { setPacks(p); if (p.length && !slug) setSlug(p[0].slug); }).catch(() => {});
  useEffect(() => {
    refresh();
    api.listSheetTypes().then(setTypes).catch(() => {});
    api.dimensionOptions('company').then((r: any[]) => {
      const cs = (r || []).map((x) => ({ name: x.name, label: x.label || x.name }));
      setCompanies(cs); if (cs.length && !company) setCompany(cs[0].name);
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function generate() {
    if (!slug) { setMsg(t('Pick a pack.')); return; }
    const url = '/api/method/neotec_insight.neotec_insight.api.packs.generate_pack'
      + `?slug=${q(slug)}&company=${q(company)}&from_date=${q(fromDate)}&to_date=${q(toDate)}`;
    window.open(url, '_blank');
  }

  async function openEditor(existing?: string) {
    setMsg('');
    if (existing) {
      const p = await api.loadPack(existing).catch(() => null);
      if (p) setEditing({ slug: p.slug, title: p.title, description: p.description || '', config: { sheets: p.config.sheets || [], language: p.config.language || 'both' } });
    } else {
      setEditing({ title: '', description: '', config: { sheets: [], language: 'both' } });
    }
  }

  async function savePack() {
    if (!editing) return;
    try {
      const r = await api.savePack(editing);
      setEditing(null); setSlug(r.slug); refresh();
    } catch (e: any) { setMsg(String(e?.message || e)); }
  }

  async function removePack(s: string) {
    if (!confirm(t('Delete this pack?'))) return;
    await api.deletePack(s).catch(() => {});
    if (slug === s) setSlug('');
    refresh();
  }

  const spec = (type: string) => types.find((x) => x.type === type);
  const upd = (i: number, patch: Partial<SheetCfg>) =>
    setEditing((e) => e && ({ ...e, config: { ...e.config, sheets: e.config.sheets.map((s, k) => k === i ? { ...s, ...patch } : s) } }));
  const move = (i: number, d: number) => setEditing((e) => {
    if (!e) return e;
    const a = [...e.config.sheets]; const j = i + d;
    if (j < 0 || j >= a.length) return e;
    const [x] = a.splice(i, 1); a.splice(j, 0, x);
    return { ...e, config: { ...e.config, sheets: a } };
  });

  return (
    <div className="vat-wrap">
      <div className="vat-hero">
        <div>
          <h1>{t('Export Packs')} <span className="vat-badge">{t('Configurable')}</span></h1>
          <p>{t('Client-specific audit workbooks assembled from sheet components — VAT registers, GL ledgers, the 16-box return. A new client requirement is a new pack, not a new build.')}</p>
        </div>
      </div>

      <div className="vat-controls">
        <label><span className="flbl">{t('Pack')}</span>
          <select value={slug} onChange={(e) => setSlug(e.target.value)}>
            {packs.map((p) => <option key={p.slug} value={p.slug}>{p.title}</option>)}
          </select>
        </label>
        <label><span className="flbl">{t('Company')}</span>
          <select value={company} onChange={(e) => setCompany(e.target.value)}>
            {companies.map((c) => <option key={c.name} value={c.name}>{c.label}</option>)}
          </select>
        </label>
        <label><span className="flbl">{t('From')}</span>
          <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} /></label>
        <label><span className="flbl">{t('To')}</span>
          <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} /></label>
        <button className="vat-run" onClick={generate}>{t('Generate workbook')}</button>
        <button className="vat-ghost" onClick={() => openEditor(slug)} disabled={!slug}>{t('Edit pack')}</button>
        <button className="vat-ghost" onClick={() => openEditor()}>+ {t('New pack')}</button>
        {slug && <button className="vat-ghost" onClick={() => removePack(slug)}>{t('Delete')}</button>}
      </div>
      {msg && <div className="studio-err">{msg}</div>}

      {slug && !editing && (
        <div className="vat-accts">
          {packs.find((p) => p.slug === slug)?.description || ''}
        </div>
      )}

      {editing && (
        <div className="studio-card" style={{ padding: 16, marginTop: 14 }}>
          <div className="studio-frow" style={{ gap: 8 }}>
            <input placeholder={t('Pack title')} value={editing.title} style={{ flex: 1 }}
              onChange={(e) => setEditing((x) => x && ({ ...x, title: e.target.value }))} />
            <select value={editing.config.language}
              onChange={(e) => setEditing((x) => x && ({ ...x, config: { ...x.config, language: e.target.value } }))}>
              <option value="both">{t('Bilingual headers (EN / AR)')}</option>
              <option value="en">English</option>
              <option value="ar">العربية</option>
            </select>
          </div>
          <input placeholder={t('Description (optional)')} value={editing.description} style={{ width: '100%', marginTop: 8 }}
            onChange={(e) => setEditing((x) => x && ({ ...x, description: e.target.value }))} />

          {editing.config.sheets.map((s, i) => {
            const sp = spec(s.type);
            return (
              <div key={i} className="menu-sec" style={{ marginTop: 12 }}>
                <div className="menu-sec-h">
                  <span className="menu-arrows">
                    <button onClick={() => move(i, -1)} disabled={i === 0}>↑</button>
                    <button onClick={() => move(i, 1)} disabled={i === editing.config.sheets.length - 1}>↓</button>
                  </span>
                  <span className="cls-badge">{sp?.label || s.type}</span>
                  <input className="menu-sec-name" value={s.title} placeholder={t('Sheet title')}
                    onChange={(e) => upd(i, { title: e.target.value })} />
                  <button className="studio-ghost" onClick={() => setEditing((x) => x && ({ ...x, config: { ...x.config, sheets: x.config.sheets.filter((_, k) => k !== i) } }))}>✕</button>
                </div>
                <div style={{ padding: '8px 12px', display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center' }}>
                  {(sp?.options || []).map((op: any) => op.kind === 'check' ? (
                    <label key={op.key} className="studio-cbx" style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5 }}>
                      <input type="checkbox"
                        checked={s.options[op.key] !== undefined ? !!s.options[op.key] : !!op.default}
                        onChange={(e) => upd(i, { options: { ...s.options, [op.key]: e.target.checked ? 1 : 0 } })} />
                      {t(op.label)}
                    </label>
                  ) : op.kind === 'select' ? (
                    <label key={op.key} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 12.5 }}>
                      {t(op.label)}
                      <select value={s.options[op.key] ?? op.default}
                        onChange={(e) => upd(i, { options: { ...s.options, [op.key]: e.target.value } })}>
                        {(op.choices || []).map((c: any) => <option key={c[0]} value={c[0]}>{t(c[1])}</option>)}
                      </select>
                    </label>
                  ) : (
                    op.key === 'accounts' ? (
                      (s.options['accounts_mode'] ?? 'output_vat') === 'custom' ? (
                        <span key={op.key} style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 300 }}>
                          <select multiple size={6} onFocus={ensureGlAccounts}
                            value={String(s.options[op.key] || '').split(',').map((x) => x.trim()).filter(Boolean)}
                            onChange={(e) => upd(i, { options: { ...s.options, [op.key]: Array.from(e.target.selectedOptions).map((o) => o.value).join(',') } })}>
                            {glAccounts.map((a) => <option key={a.name} value={a.name}>{a.label || a.name}</option>)}
                          </select>
                          <span className="studio-hint">{t('Ctrl/Cmd-click for multiple ledgers')}</span>
                        </span>
                      ) : null
                    ) : (
                      <input key={op.key} placeholder={t(op.label)} value={s.options[op.key] || ''}
                        style={{ minWidth: 260 }}
                        onChange={(e) => upd(i, { options: { ...s.options, [op.key]: e.target.value } })} />
                    )
                  ))}
                  {(sp?.columns || []).length > 0 && (
                    <details style={{ width: '100%' }}>
                      <summary className="studio-hint" style={{ cursor: 'pointer' }}>{t('Columns')}</summary>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', paddingTop: 6 }}>
                        {sp.columns.map((c: any) => {
                          const chosen: string[] = s.options.columns || sp.columns.map((x: any) => x[0]);
                          const on = chosen.includes(c[0]);
                          return (
                            <label key={c[0]} className={'studio-mchip' + (on ? ' on' : '')}>
                              <input type="checkbox" checked={on}
                                onChange={() => upd(i, { options: { ...s.options, columns: on ? chosen.filter((x) => x !== c[0]) : [...chosen, c[0]] } })} />
                              {c[1]}
                            </label>
                          );
                        })}
                      </div>
                    </details>
                  )}
                </div>
              </div>
            );
          })}

          <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
            <select id="add-sheet-type" defaultValue="">
              <option value="" disabled>{t('Add sheet…')}</option>
              {types.map((x) => <option key={x.type} value={x.type}>{x.label}</option>)}
            </select>
            <button className="studio-ghost" onClick={() => {
              const sel = document.getElementById('add-sheet-type') as HTMLSelectElement;
              const tp = sel?.value; if (!tp) return;
              const sp2 = spec(tp);
              const defaults: Record<string, any> = {};
              (sp2?.options || []).forEach((op: any) => { defaults[op.key] = op.default; });
              setEditing((x) => x && ({ ...x, config: { ...x.config, sheets: [...x.config.sheets, { type: tp, title: sp2?.label || tp, options: defaults }] } }));
              sel.value = '';
            }}>+ {t('Add')}</button>
            <span style={{ flex: 1 }} />
            <button className="studio-run" onClick={savePack}>{t('Save pack')}</button>
            <button className="studio-ghost" onClick={() => setEditing(null)}>{t('Cancel')}</button>
          </div>
        </div>
      )}
    </div>
  );
}
