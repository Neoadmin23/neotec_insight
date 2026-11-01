import { useEffect, useState } from 'react';
import { api } from '../utils/api';
import { t } from '../utils/i18n';
import { BAND_ELEMENTS, EMPTY_PRINT, type ReportPrintSettings } from '../utils/printSettings';

/* v3.4.0 — this report's print layout ("print positions") and letterhead.
 * Overrides the company Brand Kit for this report only; "Default" keeps the
 * Brand Kit's value. Applies to Print, PDF, Image and Excel. */
export function PrintLayoutModal({ reportLabel, value, onSave, onClose }: {
  reportLabel: string; value: ReportPrintSettings; onSave: (s: ReportPrintSettings) => void; onClose: () => void;
}) {
  const [s, setS] = useState<ReportPrintSettings>({ ...value, align: { ...value.align },
    headerImages: [...(value.headerImages || [])], footerImages: [...(value.footerImages || [])] });
  const [busy, setBusy] = useState('');
  const [heads, setHeads] = useState<Array<{ name: string; label: string; is_default: number }>>([]);
  useEffect(() => { api.listLetterHeads().then((h) => setHeads(h || [])).catch(() => setHeads([])); }, []);
  const pos = (k: string) => (s.align as any)[k] || '';
  const setPos = (k: string, v: string) => setS({ ...s, align: { ...s.align, [k]: v } });
  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel pl-panel" role="dialog" aria-label={t('Report layout')}>
        <div className="theme-h">
          <h3>{t('Report layout')} — {reportLabel}</h3>
          <button type="button" className="cp-x" onClick={onClose}>×</button>
        </div>
        <p className="bk-hint">{t('For this report only. "Default" keeps the company print setup (⚙ Setup).')}</p>
        <div className="pl-grid">
          <label>{t('Letterhead')}
            <select value={s.letterhead} onChange={(e) => setS({ ...s, letterhead: e.target.value })}>
              <option value="">{t('Default (company print setup)')}</option>
              {heads.map((h) => <option key={h.name} value={h.name}>{h.label}{h.is_default ? ` · ${t('default')}` : ''}</option>)}
            </select>
          </label>
          <label>{t('Orientation')}
            <select value={s.orientation} onChange={(e) => setS({ ...s, orientation: e.target.value as any })}>
              <option value="">{t('Default')}</option><option value="portrait">{t('Portrait')}</option>
              <option value="landscape">{t('Landscape')}</option>
            </select>
          </label>
          <label>{t('Paper')}
            <select value={s.paper} onChange={(e) => setS({ ...s, paper: e.target.value as any })}>
              <option value="">{t('Default')}</option>
              {['A4', 'A3', 'Letter', 'Legal'].map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label>{t('Title size (pt)')}
            <input type="number" min={0} max={40} value={s.titleSizePt || ''} placeholder={t('Default')}
              onChange={(e) => setS({ ...s, titleSizePt: Number(e.target.value) || 0 })} />
          </label>
          <label>{t('Table text size (px)')}
            <input type="number" min={0} max={24} value={s.bodySizePx || ''} placeholder={t('Default')}
              onChange={(e) => setS({ ...s, bodySizePx: Number(e.target.value) || 0 })} />
          </label>
          <label>{t('Table lines')}
            <select value={s.gridLines} onChange={(e) => setS({ ...s, gridLines: e.target.value as any })}>
              <option value="">{t('Default')}</option><option value="grid">{t('Grid')}</option>
              <option value="rows">{t('Rows only')}</option><option value="none">{t('None')}</option>
            </select>
          </label>
        </div>
        <h4 className="pl-h">{t('Positions')}</h4>
        <div className="pl-grid">
          {BAND_ELEMENTS.map((el) => (
            <label key={el.key}>{t(el.label)}
              <select value={pos(el.key)} onChange={(e) => setPos(el.key, e.target.value)}>
                <option value="">{t('Default')}</option><option value="left">{t('Left')}</option>
                <option value="center">{t('Centre')}</option><option value="right">{t('Right')}</option>
                <option value="hide">{t('Hide')}</option>
              </select>
            </label>
          ))}
          <label>{t('Page numbers')}
            <select value={s.pageNoPos} onChange={(e) => setS({ ...s, pageNoPos: e.target.value as any })}>
              <option value="">{t('Default')}</option><option value="left">{t('Left')}</option>
              <option value="center">{t('Centre')}</option><option value="right">{t('Right')}</option>
              <option value="hide">{t('Hide')}</option>
            </select>
          </label>
        </div>
        <h4 className="pl-h">{t('Header & footer')}</h4>
        <div className="pl-grid">
          <label>{t('Letterhead style')}
            <select value={s.lhStyle} disabled={!s.letterhead} onChange={(e) => setS({ ...s, lhStyle: e.target.value as any })}>
              <option value="">{t('As designed in ERPNext (header & footer)')}</option>
              <option value="band">{t('Insight band (letterhead logo and details only)')}</option>
            </select>
          </label>
          <label>{t('Letterhead image size')}
            <select value={s.lhImageSize || ''} disabled={!s.letterhead || s.lhStyle === 'band'}
              onChange={(e) => setS({ ...s, lhImageSize: e.target.value as any })}>
              <option value="">{t('Fit page width (follows orientation and paper)')}</option>
              <option value="natural">{t('As designed (image\'s own size)')}</option>
            </select>
          </label>
          <label>{t('Image height (mm)')}
            <input type="number" min={6} max={60} value={s.imageHeightMm || 18}
              onChange={(e) => setS({ ...s, imageHeightMm: Number(e.target.value) || 18 })} />
          </label>
          <label className="pl-check"><input type="checkbox" checked={s.headerRule}
            onChange={(e) => setS({ ...s, headerRule: e.target.checked })} /> {t('Line under the header')}</label>
          <label className="pl-check"><input type="checkbox" checked={s.footerRule}
            onChange={(e) => setS({ ...s, footerRule: e.target.checked })} /> {t('Line above the footer')}</label>
        </div>
        {(['headerImages', 'footerImages'] as const).map((k) => (
          <div key={k} className="pl-imgs">
            <div className="pl-imgs-h">{k === 'headerImages' ? t('Header images') : t('Footer images')}
              <small> — {t('1 = left · 2 = left / right · 3 = left / centre / right')}</small></div>
            <div className="pl-slots">
              {[0, 1, 2].map((i) => {
                const url = s[k][i];
                return (
                  <div key={i} className="pl-slot">
                    {url ? (
                      <>
                        <img src={url} alt="" />
                        <button type="button" className="cp-link" onClick={() => setS({ ...s, [k]: s[k].filter((_u, j) => j !== i) })}>{t('Remove')}</button>
                      </>
                    ) : i === s[k].length ? (
                      <label className="bk-btn">{busy === `${k}${i}` ? t('Uploading…') : t('Upload image')}
                        <input type="file" accept="image/*" hidden onChange={async (e) => {
                          const f = e.target.files?.[0];
                          if (!f) return;
                          setBusy(`${k}${i}`);
                          try { const u = await api.uploadImage(f); if (u) setS((cur) => ({ ...cur, [k]: [...cur[k], u].slice(0, 3) })); }
                          catch (err: any) { alert(err?.message || String(err)); }
                          finally { setBusy(''); }
                        }} />
                      </label>
                    ) : <span className="pl-empty">—</span>}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
        <label className="pl-lines">{t('Footer text (one line per line)')}
          <textarea rows={2} value={s.footerLines} onChange={(e) => setS({ ...s, footerLines: e.target.value })} />
        </label>
        <div className="pl-actions">
          <button type="button" className="bk-btn" onClick={() => setS({ ...EMPTY_PRINT, align: {}, headerImages: [], footerImages: [] })}>{t('Reset to default')}</button>
          <button type="button" className="bk-btn bk-primary" onClick={() => { onSave(s); onClose(); }}>{t('Save')}</button>
        </div>
      </div>
    </div>
  );
}
