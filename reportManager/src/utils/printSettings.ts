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
  /** v3.4.6 — with a letterhead: print its own header/footer ('designed', the
   *  default) or use only its logo and details in Insight's band ('band') */
  lhStyle: '' | 'designed' | 'band';
  /** v3.4.6 — custom header / footer images (up to 3 each: left, centre, right) */
  headerImages: string[];
  footerImages: string[];
  imageHeightMm: number;
  headerRule: boolean;
  footerRule: boolean;
  /** extra footer text, one line per line */
  footerLines: string;
  /** v3.4.7 — a letterhead header/footer that is one image: stretch it to the
   *  printable width ('' = fit width, the default) or keep its own size */
  lhImageSize: '' | 'natural';
}

export const BAND_ELEMENTS: Array<{ key: keyof BandAlign; label: string }> = [
  { key: 'logo', label: 'Logo' }, { key: 'title', label: 'Title' }, { key: 'company', label: 'Company' },
  { key: 'headerText', label: 'Header text / address' }, { key: 'period', label: 'Period' },
  { key: 'footerText', label: 'Footer text' }, { key: 'timestamp', label: 'Printed on' },
];

export const EMPTY_PRINT: ReportPrintSettings = {
  letterhead: '', orientation: '', paper: '', titleSizePt: 0, bodySizePx: 0, gridLines: '', pageNoPos: '', align: {},
  lhStyle: '', headerImages: [], footerImages: [], imageHeightMm: 18, headerRule: false, footerRule: false, footerLines: '', lhImageSize: '',
};

const KEY = (report: string, company?: string | null) => `ni-print:${report}:${company || 'default'}`;

export function loadPrint(report: string, company?: string | null): ReportPrintSettings {
  try {
    const raw = localStorage.getItem(KEY(report, company)) || localStorage.getItem(KEY(report, null));
    if (!raw) return { ...EMPTY_PRINT, align: {}, headerImages: [], footerImages: [] };
    const v = JSON.parse(raw);
    return { ...EMPTY_PRINT, ...v, align: { ...(v.align || {}) }, headerImages: [...(v.headerImages || [])],
      footerImages: [...(v.footerImages || [])] };
  } catch { return { ...EMPTY_PRINT, align: {}, headerImages: [], footerImages: [] }; }
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


const escAttr = (u: string) => String(u).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
const escText = (u: string) => String(u).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** 1 image → left, 2 → left / right, 3 → left / centre / right. A plain table,
 *  not flexbox: the PDF engine (wkhtmltopdf) lays tables out reliably. */
export function imagesRow(urls: string[], heightMm: number): string {
  const list = (urls || []).filter(Boolean).slice(0, 3);
  if (!list.length) return '';
  const aligns = list.length === 1 ? ['left'] : list.length === 2 ? ['left', 'right'] : ['left', 'center', 'right'];
  const h = Math.max(6, Math.min(60, heightMm || 18));
  const cells = list.map((u, i) => `<td style="text-align:${aligns[i]};vertical-align:middle;border:0;padding:0;width:${Math.floor(100 / list.length)}%">`
    + `<img src="${escAttr(u)}" style="max-height:${h}mm;max-width:100%"></td>`).join('');
  return `<table class="pr-imgrow" style="width:100%;border-collapse:collapse;border:0"><tr>${cells}</tr></table>`;
}

const RULE = '<div style="border-top:2px solid #222;margin:2mm 0"></div>';

/** v3.4.7 — a block holding exactly one image is redrawn at the full printable
 *  width (height follows), so its edges reach the margins in portrait or
 *  landscape and on any paper size. Anything else is returned as designed. */
export function fitWidth(html: string): string {
  const imgs = String(html || '').match(/<img\b[^>]*>/gi) || [];
  if (imgs.length !== 1) return html;
  const src = /\bsrc\s*=\s*("([^"]*)"|'([^']*)')/i.exec(imgs[0]);
  const url = src ? (src[2] ?? src[3] ?? '') : '';
  if (!url) return html;
  return `<div class="pr-lh-fit" style="width:100%;margin:0;padding:0">`
    + `<img src="${escAttr(url)}" style="width:100%;height:auto;display:block;margin:0;border:0"></div>`;
}

export interface FrameParts { headerHtml: string; footerHtml: string; hideIdentity: boolean }

/** The report's header and footer: the chosen letterhead as designed in
 *  ERPNext (unless "Insight band" is chosen) plus up to 3 custom images each,
 *  separator rules and extra footer lines. */
export function frameParts(s: ReportPrintSettings, lh?: { header_html?: string; footer_html?: string } | null): FrameParts {
  const designed = !!(s.letterhead && lh && s.lhStyle !== 'band');
  const fit = (h?: string) => (s.lhImageSize === 'natural' ? (h || '') : fitWidth(h || ''));
  const head = [designed ? fit(lh?.header_html) : '', imagesRow(s.headerImages, s.imageHeightMm)].filter(Boolean).join('');
  const lines = String(s.footerLines || '').split('\n').map((l) => l.trim()).filter(Boolean)
    .map((l) => `<div style="text-align:center;font-size:8pt;color:#555">${escText(l)}</div>`).join('');
  const foot = [imagesRow(s.footerImages, s.imageHeightMm), designed ? fit(lh?.footer_html) : '', lines].filter(Boolean).join('');
  return {
    headerHtml: head ? head + (s.headerRule ? RULE : '') : '',
    footerHtml: foot ? (s.footerRule ? RULE : '') + foot : '',
    hideIdentity: designed && !!(lh?.header_html || '').trim(),
  };
}
