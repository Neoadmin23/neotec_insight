import { useEffect, useRef, useState } from 'react';
import { api, uploadFile } from '../../utils/api';
import { t } from '../../utils/i18n';
import { loadBrand, saveBrand, brandDefaults, type Brand } from '../../utils/branddoc';

/* v2.55.0 — the print setup, promoted out of the General Ledger.
 *
 * This panel used to exist once, behind the ⚙ on the ledger toolbar, and
 * every other report inherited whatever the ledger happened to be set to
 * without any way to see or change it from where you were standing. It is now
 * a shared component mounted by the ExportBar, so the same setup opens from
 * whichever report menu you are in, and it saves site-wide rather than into
 * one browser's localStorage.
 */

interface Props {
  company?: string;
  companyLabel?: string;
  onClose: () => void;
  onSaved?: (brand: Brand) => void;
}

const ALIGN_ROWS: [keyof Brand['align'], string][] = [
  ['logo', 'Logo'], ['title', 'Title'], ['headerText', 'Header text'],
  ['company', 'Company details'], ['period', 'Period'],
  ['footerText', 'Footer text'], ['timestamp', 'Generated timestamp'],
];

export default function BrandKitModal({ company, companyLabel, onClose, onSaved }: Props) {
  const [b, setB] = useState<Brand>(() => loadBrand(company || null));
  const [status, setStatus] = useState<'' | 'saving' | 'saved' | 'local' | 'error'>('');
  const [logoBusy, setLogoBusy] = useState(false);
  const [logoMsg, setLogoMsg] = useState('');
  const [canEditCompanyLogo, setCanEditCompanyLogo] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const saveTimer = useRef<number | undefined>(undefined);

  useEffect(() => { setB(loadBrand(company || null)); }, [company]);

  useEffect(() => {
    if (!company) { setCanEditCompanyLogo(false); return; }
    let alive = true;
    api.companyBrand(company)
      .then((r: any) => { if (alive) setCanEditCompanyLogo(!!r?.can_edit); })
      .catch(() => { if (alive) setCanEditCompanyLogo(false); });
    return () => { alive = false; };
  }, [company]);

  // Debounced so typing in a text field doesn't fire a write per keystroke;
  // the local copy is written synchronously inside saveBrand either way, so
  // an export taken mid-edit already sees the change.
  const set = (patch: Partial<Brand>) => {
    const next = { ...b, ...patch } as Brand;
    setB(next);
    setStatus('saving');
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(async () => {
      const ok = await saveBrand(company || null, next);
      setStatus(ok ? 'saved' : 'local');
      onSaved?.(next);
    }, 450);
  };

  const setAlign = (key: keyof Brand['align'], value: string) =>
    set({ align: { ...(b.align || brandDefaults().align), [key]: value } as Brand['align'] });

  async function pickLogo(file: File) {
    setLogoBusy(true); setLogoMsg('');
    try {
      const url = await uploadFile(file, false);
      set({ logoUrl: url });
      // Push it to the Company master when we're allowed, so the logo is the
      // company's, not this browser's — that's the whole point of having a
      // provision for a company with no logo on file.
      if (company && canEditCompanyLogo) {
        try {
          await api.setCompanyLogo(company, url);
          setLogoMsg(t('Uploaded and saved to the Company master.'));
        } catch {
          setLogoMsg(t('Uploaded. Saved for printing only — the Company master was not updated.'));
        }
      } else {
        setLogoMsg(t('Uploaded. Saved for printing only — the Company master was not updated.'));
      }
    } catch (e: any) {
      setLogoMsg(e?.message || t('Upload failed.'));
    } finally {
      setLogoBusy(false);
    }
  }

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel bk-panel" role="dialog" aria-label={t('Print setup')}>
        <div className="theme-h">
          <h3>{t('Print setup')}{companyLabel ? ` — ${companyLabel}` : ''}</h3>
          <span className={'bk-status bk-' + (status || 'idle')}>
            {status === 'saving' ? t('Saving…')
              : status === 'saved' ? t('Saved for everyone')
                : status === 'local' ? t('Saved on this device only')
                  : ''}
          </span>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>

        <div className="bk-body">
          <section className="bk-sec">
            <h4>{t('Heading')}</h4>
            {/* No per-report title here on purpose: each report supplies its
                own heading, so a heading typed once cannot leak onto every
                other report's letterhead. */}
            <label>{t('Header text')}
              <input value={b.headerText || ''} placeholder={t('free text block in the letterhead')}
                onChange={(e) => set({ headerText: e.target.value })} />
            </label>
            <label>{t('Footer text')}
              <input value={b.footerText || ''} placeholder={t('e.g. Confidential — internal use only')}
                onChange={(e) => set({ footerText: e.target.value })} />
            </label>
          </section>

          <section className="bk-sec">
            <h4>{t('Company identity')}</h4>
            <label className="chk">
              <input type="checkbox" checked={b.showCompany !== false}
                onChange={(e) => set({ showCompany: e.target.checked })} /> {t('Show company name')}
            </label>
            {b.showCompany !== false && (
              <>
                <label>{t('Company name (as printed)')}
                  <input value={b.companyName || ''} placeholder={companyLabel || company || ''}
                    onChange={(e) => set({ companyName: e.target.value })} />
                </label>
                <label>{t('Company name — Arabic')}
                  <input value={b.companyNameAr || ''} lang="ar" placeholder={t('optional second line')}
                    onChange={(e) => set({ companyNameAr: e.target.value })} />
                </label>
                <div className="bk-row">
                  <label>{t('VAT number')}
                    <input value={b.vatNo || ''} placeholder="3xxxxxxxxxxxxx3"
                      onChange={(e) => set({ vatNo: e.target.value })} />
                  </label>
                  <label>{t('CR number')}
                    <input value={b.crNo || ''} placeholder="10xxxxxxxx"
                      onChange={(e) => set({ crNo: e.target.value })} />
                  </label>
                </div>
              </>
            )}
            <label className="chk">
              <input type="checkbox" checked={b.showPeriod !== false}
                onChange={(e) => set({ showPeriod: e.target.checked })} /> {t('Show period (from → to)')}
            </label>
          </section>

          <section className="bk-sec">
            <h4>{t('Logo')}</h4>
            <div className="bk-logo-row">
              <div className="bk-logo-prev">
                {b.logoUrl
                  ? <img src={b.logoUrl} alt="" />
                  : <span className="bk-logo-empty">{t('No logo')}</span>}
              </div>
              <div className="bk-logo-ctl">
                <label>{t('Logo URL')}
                  <input value={b.logoUrl || ''} placeholder="/files/company_logo.png"
                    onChange={(e) => set({ logoUrl: e.target.value })} />
                </label>
                <div className="bk-row">
                  <button type="button" className="bk-btn" disabled={logoBusy}
                    onClick={() => fileRef.current?.click()}>
                    {logoBusy ? t('Uploading…') : t('Upload logo…')}
                  </button>
                  {b.logoUrl && (
                    <button type="button" className="bk-btn" onClick={() => { set({ logoUrl: '' }); setLogoMsg(''); }}>
                      {t('Remove')}
                    </button>
                  )}
                </div>
                {/* v2.55.3 — an explicit box in millimetres. A single height
                    preset let a wide wordmark run past its column and paint
                    over the report title; a width cap is the fix, and the same
                    box sizes the logo embedded in the Excel workbook. */}
                <div className="bk-row bk-logo-dims">
                  <label className="bk-inline">{t('Width (mm)')}
                    <input type="number" min={0} max={120} step={1} className="bk-num"
                      value={b.logoWidthMm || ''} placeholder={t('auto')}
                      onChange={(e) => set({ logoWidthMm: Math.max(0, Math.min(120, parseInt(e.target.value) || 0)) })} />
                  </label>
                  <label className="bk-inline">{t('Height (mm)')}
                    <input type="number" min={0} max={60} step={1} className="bk-num"
                      value={b.logoHeightMm || ''} placeholder={t('auto')}
                      onChange={(e) => set({ logoHeightMm: Math.max(0, Math.min(60, parseInt(e.target.value) || 0)) })} />
                  </label>
                  <span className="bk-presets">
                    {([['S', 0, 8], ['M', 0, 12], ['L', 0, 18], ['XL', 0, 26]] as [string, number, number][]).map(([lbl, w, h]) => (
                      <button key={lbl} type="button" className="bk-chip"
                        onClick={() => set({ logoWidthMm: w, logoHeightMm: h })}>{lbl}</button>
                    ))}
                  </span>
                </div>
                <p className="bk-hint">{t('Leave one blank to keep the image\'s own proportions. Set both to force a box — the image is fitted inside it, never stretched. Applies to Print, PDF and the logo embedded in Excel.')}</p>
                <input ref={fileRef} type="file" accept="image/*" hidden
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) pickLogo(f); e.target.value = ''; }} />
                {logoMsg && <p className="bk-hint">{logoMsg}</p>}
                {!logoMsg && (
                  <p className="bk-hint">
                    {canEditCompanyLogo
                      ? t('An uploaded logo is written back to the Company master, so every report and every user picks it up.')
                      : t('The Company master already carries a logo, or you cannot edit it — an upload here is used for printing only.')}
                  </p>
                )}
              </div>
            </div>
          </section>

          <section className="bk-sec">
            <h4>{t('Table rules')}</h4>
            <div className="bk-row">
              <label>{t('Grid lines')}
                <select value={b.gridLines || 'grid'} onChange={(e) => set({ gridLines: e.target.value as Brand['gridLines'] })}>
                  <option value="grid">{t('Full grid (box every cell)')}</option>
                  <option value="rows">{t('Horizontal rules only')}</option>
                  <option value="none">{t('No rules')}</option>
                </select>
              </label>
              <label>{t('Weight')}
                <select value={b.borderPreset || 'classic'} onChange={(e) => set({ borderPreset: e.target.value as Brand['borderPreset'] })}>
                  <option value="minimal">{t('Light')}</option>
                  <option value="classic">{t('Classic')}</option>
                  <option value="strong">{t('Strong')}</option>
                </select>
              </label>
              <label>{t('Accent colour')}
                <input type="color" className="bk-color" value={b.accent || '#16404d'}
                  onChange={(e) => set({ accent: e.target.value })} />
              </label>
            </div>
            <p className="bk-hint">{t('Grid lines apply to Print, PDF and Excel alike. Excel also prints its own gridlines unless rules are off.')}</p>
          </section>

          <section className="bk-sec">
            <h4>{t('Page')}</h4>
            <div className="bk-row">
              <label>{t('Paper')}
                <select value={b.paper || 'A4'} onChange={(e) => set({ paper: e.target.value })}>
                  <option>A4</option><option>A3</option><option>Letter</option><option>Legal</option>
                </select>
              </label>
              <label>{t('Orientation')}
                <select value={b.orientation || 'portrait'} onChange={(e) => set({ orientation: e.target.value })}>
                  <option value="portrait">{t('Portrait')}</option>
                  <option value="landscape">{t('Landscape')}</option>
                </select>
              </label>
              <label>{t('Heading size')}
                <select value={b.titleSizePt || 15} onChange={(e) => set({ titleSizePt: parseInt(e.target.value) })}>
                  <option value={13}>{t('Small')}</option><option value={15}>{t('Default')}</option>
                  <option value={18}>{t('Large')}</option><option value={22}>{t('XL')}</option>
                </select>
              </label>
              <label>{t('Body size')}
                <select value={b.bodySizePx || 12} onChange={(e) => set({ bodySizePx: parseInt(e.target.value) })}>
                  <option value={10}>{t('Compact')}</option><option value={12}>{t('Default')}</option>
                  <option value={14}>{t('Large')}</option>
                </select>
              </label>
            </div>
            <div className="bk-row">
              <label>{t('Print colours')}
                <select value={b.printColors || 'brand'} onChange={(e) => set({ printColors: e.target.value as Brand['printColors'] })}>
                  <option value="brand">{t('Brand Kit')}</option>
                  <option value="theme">{t('Match app theme')}</option>
                </select>
              </label>
              <label>{t('Browser header & footer')}
                <select value={b.browserChrome || 'suppress'} onChange={(e) => set({ browserChrome: e.target.value as Brand['browserChrome'] })}>
                  <option value="suppress">{t('Suppress')}</option>
                  <option value="show">{t('Show')}</option>
                </select>
              </label>
              <label>{t('Page numbers')}
                <select value={b.pageNoPos || 'center'} disabled={(b.browserChrome || 'suppress') !== 'show'}
                  onChange={(e) => set({ pageNoPos: e.target.value as Brand['pageNoPos'] })}>
                  <option value="left">{t('Left')}</option>
                  <option value="center">{t('Center')}</option>
                  <option value="right">{t('Right')}</option>
                  <option value="hide">{t('Hidden')}</option>
                </select>
              </label>
            </div>
            {(b.browserChrome || 'suppress') !== 'show' && (
              <p className="bk-hint">{t('Suppressing removes the page margin the browser prints its date, title and URL into — which also removes page numbers, the only place a page counter can live.')}</p>
            )}
          </section>

          <section className="bk-sec">
            <h4>{t('Letterhead layout')}</h4>
            <div className="align-grid">
              <div className="ag-row ag-head">
                <span>{t('Element')}</span><span>{t('Left')}</span><span>{t('Center')}</span>
                <span>{t('Right')}</span><span>{t('Hidden')}</span>
              </div>
              {ALIGN_ROWS.map(([key, label]) => {
                const cur = (b.align || brandDefaults().align)[key] || 'center';
                return (
                  <div className="ag-row" key={key}>
                    <span>{t(label)}</span>
                    {['left', 'center', 'right', 'hide'].map((v) => (
                      <span key={v}>
                        <input type="radio" name={'bk-ag-' + key} checked={cur === v}
                          onChange={() => setAlign(key, v)} />
                      </span>
                    ))}
                  </div>
                );
              })}
            </div>
          </section>

          <div className="bk-foot">
            <button type="button" className="bk-btn"
              onClick={() => { const d = brandDefaults(); setB(d); saveBrand(company || null, d).then((ok) => setStatus(ok ? 'saved' : 'local')); }}>
              {t('Reset to defaults')}
            </button>
            <button type="button" className="bk-btn bk-primary" onClick={onClose}>{t('Done')}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
