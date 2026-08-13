import { useEffect, useRef, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import {
  THEMES, applyVars, persist, loadSaved, themeFromLogo, themeFromPalette, parsePalette,
  type SavedTheme, type ThemeVars,
} from '../../utils/theme';

interface CompanyBrand { name: string; label: string; logo: string }

export function ThemePicker({ onClose }: { onClose: () => void }) {
  const [saved, setSaved] = useState<SavedTheme | null>(loadSaved());
  const [companies, setCompanies] = useState<CompanyBrand[]>([]);
  const [busyLogo, setBusyLogo] = useState('');
  const [logoErr, setLogoErr] = useState('');
  const [palText, setPalText] = useState(() => { const v = loadSaved(); return v && v.kind === 'palette' ? v.src : ''; });
  const [palErr, setPalErr] = useState('');
  const [palVars, setPalVars] = useState<ThemeVars | null>(() => { const v = loadSaved(); return v && v.kind === 'palette' ? v.vars : null; });
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => { api.companyBranding().then(setCompanies).catch(() => {}); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  /** v2.50.0 — build a theme from a pasted palette: any hex list, with or
   *  without coverage percentages, from any extraction tool. */
  function applyPalette() {
    try {
      const vars: ThemeVars = themeFromPalette(palText);
      applyVars(vars);
      const s: SavedTheme = { kind: 'palette', src: palText, vars };
      persist(s); setSaved(s); setPalVars(vars); setPalErr(''); setLogoErr('');
    } catch {
      setPalErr(t('No colours found — paste one hex per line, e.g. #3F3F3F 0.03'));
    }
  }

  function pickPreset(key: string) {
    const theme = THEMES.find((x) => x.key === key);
    if (!theme) return;
    applyVars(theme.vars);
    const s: SavedTheme = { kind: 'preset', key };
    persist(s); setSaved(s); setLogoErr(''); setPalVars(null);
  }

  async function pickLogo(c: CompanyBrand) {
    if (!c.logo) { setLogoErr(t('No logo is set on this Company. Upload one on the Company record first — or use “Or from any image” below.')); return; }
    setBusyLogo(c.name); setLogoErr('');
    try {
      const vars: ThemeVars = await themeFromLogo(c.logo);
      applyVars(vars);
      const s: SavedTheme = { kind: 'logo', company: c.name, vars };
      persist(s); setSaved(s);
    } catch (e: any) {
      setLogoErr(String(e?.message || e));
    } finally {
      setBusyLogo('');
    }
  }

  /** Instant palette from a user-picked image — read locally as a data URL and
   *  fed through the same extractor; nothing is uploaded anywhere. */
  async function pickImageFile(file: File) {
    setBusyLogo('__custom__'); setLogoErr('');
    try {
      const dataUrl = await new Promise<string>((res, rej) => {
        const r = new FileReader();
        r.onload = () => res(String(r.result));
        r.onerror = () => rej(new Error(t('Could not read the image file.')));
        r.readAsDataURL(file);
      });
      const vars: ThemeVars = await themeFromLogo(dataUrl);
      applyVars(vars);
      const s: SavedTheme = { kind: 'logo', company: '__custom__', vars };
      persist(s); setSaved(s);
    } catch (e: any) {
      setLogoErr(String(e?.message || e));
    } finally {
      setBusyLogo('');
    }
  }

  const activePreset = saved?.kind === 'preset' ? saved.key : (saved ? '' : 'classic');
  const activeLogoCompany = saved?.kind === 'logo' ? saved.company : '';

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel" ref={panelRef} role="dialog" aria-label={t('Colour theme')}>
        <div className="theme-h">
          <h3><i className="ti ti-palette" aria-hidden /> {t('Colour theme')}</h3>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>

        <div className="theme-sec-title">{t('Theme pack')}</div>
        <div className="theme-grid">
          {THEMES.map((th) => (
            <button
              key={th.key}
              className={'theme-card' + (activePreset === th.key ? ' is-active' : '')}
              onClick={() => pickPreset(th.key)}
              title={th.name}
            >
              <span className="theme-swatches">
                <span style={{ background: th.vars['--bg'] || '#f7f7f5' }} />
                <span style={{ background: th.vars['--surface-2'] || '#f1efe8' }} />
                <span style={{ background: th.vars['--info'] || '#185fa5' }} />
                <span style={{ background: th.vars['--pos'] || '#0f6e56' }} />
              </span>
              <span className="theme-card-name">{t(th.name)}</span>
            </button>
          ))}
        </div>

        <div className="theme-sec-title">{t('From a palette')}</div>
        <p className="theme-hint">{t('Paste any hex list — with or without coverage percentages. The most saturated colour, weighted by coverage, becomes the accent; a palette with no colour in it stays neutral instead of being tinted.')}</p>
        <textarea
          className="theme-pal-input" rows={5} value={palText} spellCheck={false}
          onChange={(e) => { setPalText(e.target.value); setPalErr(''); }}
          placeholder={'#FFFFFF\t86.91\n#DCDCDC\t0.09\n#3F3F3F\t0.03\n#080808\t0.04'}
        />
        <div className="theme-pal-row">
          <span className="theme-swatches">
            {parsePalette(palText).slice(0, 8).map((e, i) => (
              <span key={i} style={{ background: e.hex }} title={e.hex} />
            ))}
          </span>
          <button className="btn" onClick={applyPalette} disabled={!palText.trim()}>{t('Generate')}</button>
        </div>
        {palVars && (
          <div className={'theme-pal-applied' + (saved?.kind === 'palette' ? ' is-active' : '')}>
            <span className="theme-swatches">
              {['--surface', '--bg', '--surface-2', '--surface-3', '--info', '--text'].map((k) => (
                <span key={k} style={{ background: palVars[k] }} title={k} />
              ))}
            </span>
            <span className="theme-pal-note">
              <i className="ti ti-check" aria-hidden /> {t('Applied — accent, surfaces and text taken from this palette')}
            </span>
          </div>
        )}
        {palErr && <div className="theme-hint theme-err">{palErr}</div>}

        <div className="theme-sec-title">{t('From your company logo')}</div>
        <p className="theme-hint">{t('Insight reads the dominant colours of the logo and builds a matching palette — accent, tints and surfaces.')}</p>
        <div className="theme-logo-list">
          {companies.map((c) => (
            <button
              key={c.name}
              className={'theme-logo-btn' + (activeLogoCompany === c.name ? ' is-active' : '')}
              onClick={() => pickLogo(c)}
              disabled={busyLogo === c.name}
            >
              {c.logo
                ? <img src={c.logo} alt="" className="theme-logo-img" />
                : <span className="theme-logo-img theme-logo-none"><i className="ti ti-photo-off" aria-hidden /></span>}
              <span className="theme-logo-name">{c.label}</span>
              <span className="theme-logo-cta">
                {busyLogo === c.name ? t('Reading…') : activeLogoCompany === c.name ? t('Applied') : t('Generate')}
              </span>
            </button>
          ))}
          {companies.length === 0 && <div className="theme-hint">{t('No companies found.')}</div>}
        </div>

        <div className="theme-sec-title">{t('Or from any image')}</div>
        <p className="theme-hint">{t('No logo on the Company record? Pick any image — brand artwork, a brochure page, a photo — and the palette is generated instantly. The image never leaves your browser.')}</p>
        <label className={'theme-logo-btn theme-upload' + (activeLogoCompany === '__custom__' ? ' is-active' : '')}>
          <span className="theme-logo-img theme-logo-none" aria-hidden>🖼</span>
          <span className="theme-logo-name">
            {busyLogo === '__custom__' ? t('Reading…') : t('Choose an image…')}
          </span>
          <span className="theme-logo-cta">{activeLogoCompany === '__custom__' ? t('Applied') : t('Browse')}</span>
          <input
            type="file"
            accept="image/*"
            style={{ display: 'none' }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) pickImageFile(f); e.target.value = ''; }}
          />
        </label>
        {logoErr && <div className="theme-err">{logoErr}</div>}
      
        <div className="theme-sec-title" style={{ marginTop: 14 }}>{t('Number format')}</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="studio-mchip" onClick={() => { try { localStorage.setItem('ni-numfmt', 'western'); localStorage.setItem('ni-numfmt-user', '1'); } catch { /* */ } location.reload(); }}>1,234,567</button>
          <button className="studio-mchip" onClick={() => { try { localStorage.setItem('ni-numfmt', 'indian'); localStorage.setItem('ni-numfmt-user', '1'); } catch { /* */ } location.reload(); }}>12,34,567 ({t('lakh/crore')})</button>
        </div>
</div>
    </div>
  );
}
