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
  const [s, setS] = useState<ReportPrintSettings>({ ...value, align: { ...value.align } });
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
        <div className="pl-actions">
          <button type="button" className="bk-btn" onClick={() => setS({ ...EMPTY_PRINT, align: {} })}>{t('Reset to default')}</button>
          <button type="button" className="bk-btn bk-primary" onClick={() => { onSave(s); onClose(); }}>{t('Save')}</button>
        </div>
      </div>
    </div>
  );
}
