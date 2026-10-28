/* v3.4.0 — per-report print layout ("print positions") and letterhead choice.
 *
 * The Brand Kit (Setup) is company-wide. These settings belong to ONE report
 * (General Ledger, Statement, Balance Sheet…) and override the Brand Kit for
 * that report only; anything left at "default" falls back to the Brand Kit.
 * Pure helpers (no React) so they are tested in scripts/test-print.mjs. */
import type { Align, BandAlign, Brand, Slot } from './branddoc';

export interface ReportPrintSettings {
  /** ERPNext Letter Head name; '' = the Brand Kit's own logo and identity */
  letterhead: string;
  orientation: '' | 'portrait' | 'landscape';
  paper: '' | 'A4' | 'A3' | 'Letter' | 'Legal';
  titleSizePt: number;          // 0 = Brand Kit
  bodySizePx: number;           // 0 = Brand Kit
  gridLines: '' | 'grid' | 'rows' | 'none';
  pageNoPos: '' | Align | 'hide';
  align: Partial<Record<keyof BandAlign, Slot | ''>>;
}

export const BAND_ELEMENTS: Array<{ key: keyof BandAlign; label: string }> = [
  { key: 'logo', label: 'Logo' }, { key: 'title', label: 'Title' }, { key: 'company', label: 'Company' },
  { key: 'headerText', label: 'Header text / address' }, { key: 'period', label: 'Period' },
  { key: 'footerText', label: 'Footer text' }, { key: 'timestamp', label: 'Printed on' },
];

export const EMPTY_PRINT: ReportPrintSettings = {
  letterhead: '', orientation: '', paper: '', titleSizePt: 0, bodySizePx: 0, gridLines: '', pageNoPos: '', align: {},
};

const KEY = (report: string, company?: string | null) => `ni-print:${report}:${company || 'default'}`;

export function loadPrint(report: string, company?: string | null): ReportPrintSettings {
  try {
    const raw = localStorage.getItem(KEY(report, company)) || localStorage.getItem(KEY(report, null));
    return raw ? { ...EMPTY_PRINT, ...JSON.parse(raw), align: { ...(JSON.parse(raw).align || {}) } } : { ...EMPTY_PRINT, align: {} };
  } catch { return { ...EMPTY_PRINT, align: {} }; }
}

export function savePrint(report: string, company: string | null | undefined, s: ReportPrintSettings) {
  try { localStorage.setItem(KEY(report, company), JSON.stringify(s)); } catch { /* storage full or blocked */ }
}

/** Only what was actually chosen — "default" values never override the Brand Kit. */
export function toLayout(s: ReportPrintSettings): Partial<Brand> {
  const out: Partial<Brand> = {};
  if (s.orientation) out.orientation = s.orientation;
  if (s.paper) out.paper = s.paper;
  if (s.titleSizePt > 0) out.titleSizePt = s.titleSizePt;
  if (s.bodySizePx > 0) out.bodySizePx = s.bodySizePx;
  if (s.gridLines) out.gridLines = s.gridLines;
  if (s.pageNoPos) out.pageNoPos = s.pageNoPos;
  const al: Partial<BandAlign> = {};
  for (const [k, v] of Object.entries(s.align || {})) if (v) (al as any)[k] = v;
  if (Object.keys(al).length) (out as any).align = al;
  return out;
}

export interface LetterheadLike {
  logo_url?: string; company_logo?: string; company_name?: string; tax_id?: string;
  address_lines?: string[]; phone?: string; email?: string; website?: string; footer_html?: string;
}

const strip = (html?: string) => String(html || '').replace(/<(style|script)[^>]*>[\s\S]*?<\/\1>/gi, ' ')
  .replace(/<br\s*\/?>/gi, ' · ').replace(/<[^>]*>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim();

/** Brand Kit + this report's layout + the chosen ERPNext letterhead. */
export function mergeBrand(base: Brand, layout?: Partial<Brand>, lh?: LetterheadLike | null): Brand {
  const b: Brand = { ...base, ...(layout || {}), align: { ...base.align, ...((layout as any)?.align || {}) } };
  if (lh) {
    b.logoUrl = lh.logo_url || lh.company_logo || b.logoUrl;
    if (lh.company_name) b.companyName = lh.company_name;
    if (lh.tax_id) b.vatNo = lh.tax_id;
    const contact = [...(lh.address_lines || []), lh.phone, lh.email, lh.website].filter(Boolean).join(' · ');
    if (contact) b.headerText = contact;
    const foot = strip(lh.footer_html);
    if (foot) b.footerText = foot;
  }
  return b;
}
