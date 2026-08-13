import { useEffect, useRef, useState } from 'react';
import { api, uploadFile } from '../../utils/api';
import { arName, t } from '../../utils/i18n';
import { getActiveCompany, onActiveCompany, setActiveCompany } from '../../utils/activeCompany';
import { loadBrand, saveBrand } from '../../utils/branddoc';

/* v2.55.0 — whose books am I looking at?
 *
 * The shell said "Neotec Insight v2.54.0" and nothing else, so a screenshot of
 * a report, or an operator with three companies on the site, had no visible
 * answer to that question. This block sits in the header and shows the active
 * company's logo and name; clicking it switches company and, when the Company
 * master carries no logo, offers to upload one.
 *
 * Logo resolution, in order: the Brand Kit's own logo (set in Print setup) →
 * the Company master's `company_logo` → the default Letter Head image → a
 * monogram. That order matters: whatever prints on the letterhead is what
 * shows in the header, so the two can't disagree.
 */

interface Brand {
  company: string;
  label: string;
  logo: string;
  logoSource: string;
  canEdit: boolean;
}

const EMPTY: Brand = { company: '', label: '', logo: '', logoSource: '', canEdit: false };

function monogram(label: string): string {
  const words = (label || '').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '·';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

export function CompanyBrand() {
  const [company, setCompany] = useState<string>(getActiveCompany());
  const [brand, setBrand] = useState<Brand>(EMPTY);
  const [companies, setCompanies] = useState<{ name: string; label: string }[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  // Follow whichever tab last published a company selection.
  useEffect(() => onActiveCompany(setCompany), []);

  useEffect(() => {
    api.listCompanies()
      .then((cs: any[]) => {
        const list = (cs || []).map((c) => ({ name: c.name, label: c.label || c.name }));
        setCompanies(list);
        // First paint with no tab having published yet: adopt the first
        // company so the header is never blank.
        if (!getActiveCompany() && list.length) setActiveCompany(list[0].name);
      })
      .catch(() => { /* header degrades to the app mark */ });
  }, []);

  useEffect(() => {
    let alive = true;
    api.companyBrand(company || '')
      .then((r: any) => {
        if (!alive || !r) return;
        setBrand({
          company: r.company || company || '',
          label: r.label || r.company || company || '',
          logo: r.logo || '',
          logoSource: r.logo_source || '',
          canEdit: !!r.can_edit,
        });
        // The backend resolved a company for us on first load — adopt it so
        // the Brand Kit and every export key off the same name.
        if (r.company && !getActiveCompany()) setActiveCompany(r.company);
      })
      .catch(() => { if (alive) setBrand({ ...EMPTY, company, label: company }); });
    return () => { alive = false; };
  }, [company]);

  // Close on outside click — a header popover that traps the pointer is worse
  // than no popover.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [open]);

  const kitLogo = (() => { try { return loadBrand(company || null).logoUrl || ''; } catch { return ''; } })();
  const logo = kitLogo || brand.logo;
  const label = arName(brand.company || company, brand.label || company);

  async function upload(file: File) {
    setBusy(true); setMsg('');
    try {
      const url = await uploadFile(file, false);
      const co = brand.company || company;
      if (co && brand.canEdit) {
        await api.setCompanyLogo(co, url);
        setBrand((b) => ({ ...b, logo: url, logoSource: 'company' }));
        setMsg(t('Saved to the Company master.'));
      } else {
        // No write permission on Company — keep it in the Brand Kit so at
        // least this site's printed output carries the mark.
        const kit = loadBrand(co || null);
        await saveBrand(co || null, { ...kit, logoUrl: url });
        setBrand((b) => ({ ...b, logo: url, logoSource: 'brandkit' }));
        setMsg(t('Saved for reports. Ask an administrator to add it to the Company master.'));
      }
    } catch (e: any) {
      setMsg(e?.message || t('Upload failed.'));
    } finally {
      setBusy(false);
    }
  }

  if (!company && !brand.label) return null;

  return (
    <div className="ni-cobrand" ref={boxRef}>
      <button type="button" className="ni-cobrand-btn" onClick={() => setOpen((v) => !v)}
        title={label + ' — ' + t('click to switch company or set a logo')} aria-expanded={open}>
        {/* Logo above, name beneath — a mark reads first and the name
            qualifies it, which is also how the printed letterhead stacks. */}
        {logo
          ? <img className="ni-cobrand-logo" src={logo} alt="" />
          : <span className="ni-cobrand-mono" aria-hidden>{monogram(label)}</span>}
        <span className="ni-cobrand-nameline">
          {/* dir=auto: an Arabic legal name has to lay out RTL, or it clips
              from the wrong end and the visible half is the tail. */}
          <span className="ni-cobrand-name" dir="auto">{label}</span>
          <span className="ni-cobrand-caret" aria-hidden>▾</span>
        </span>
      </button>

      {open && (
        <div className="ni-cobrand-pop" role="dialog" aria-label={t('Company')}>
          {companies.length > 1 && (
            <label className="ni-cobrand-field">
              <span>{t('Company')}</span>
              <select value={company} onChange={(e) => { setActiveCompany(e.target.value); setMsg(''); }}>
                {companies.map((c) => (
                  <option key={c.name} value={c.name}>{arName(c.name, c.label)}</option>
                ))}
              </select>
            </label>
          )}

          <div className="ni-cobrand-full" dir="auto">{label}</div>
          <div className="ni-cobrand-logobox">
            <div className="ni-cobrand-prev">
              {logo ? <img src={logo} alt="" /> : <span>{t('No logo on file')}</span>}
            </div>
            <div className="ni-cobrand-logoctl">
              <button type="button" className="bk-btn" disabled={busy} onClick={() => fileRef.current?.click()}>
                {busy ? t('Uploading…') : logo ? t('Replace logo…') : t('Upload logo…')}
              </button>
              <input ref={fileRef} type="file" accept="image/*" hidden
                onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ''; }} />
              <p className="bk-hint">
                {msg || (logo
                  ? (brand.logoSource === 'company' ? t('From the Company master.')
                    : brand.logoSource === 'letterhead' ? t('From the default Letter Head.')
                      : t('Set in Print setup — used on reports only.'))
                  : t('No logo in the Company master. Upload one and it is used here and on every printed report.'))}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default CompanyBrand;
