import { resolvePrintTokens, printVarsCss } from './theme';

/* v2.48.0 — the Brand Kit: one presentation frame for every printed output.
 * The per-company setup (logo, position, centered layout, footer, page
 * numbers, paper, orientation) configured in the GL ⚙ becomes the app-wide
 * brand; each module supplies only its own title/subtitle/period and gets
 * the identical letterhead, bands and pagination.
 *
 * v2.48.2 — letterhead identity + band geometry + bidi safety:
 *   · the printed company name is the legal name, not the ERPNext docname;
 *     an Arabic legal name and VAT / CR numbers can sit under it;
 *   · the reserved band height is derived from the lines actually rendered
 *     (logo, title, subtitle, name, Arabic name, identity, period) instead
 *     of a fixed 16/21 mm guess that clipped once the block grew;
 *   · every free-text token is wrapped in <bdi> so mixed Arabic/English
 *     names cannot reorder the line around them.
 */

export type Align = 'left' | 'center' | 'right';
export type Slot = Align | 'hide';

/** Where each letterhead element sits. Every element is placed independently,
 *  the way Excel's page setup gives you a left / centre / right section — but
 *  per block rather than per section, so two blocks can share a column. */
export interface BandAlign {
  logo: Slot; title: Slot; headerText: Slot; company: Slot; period: Slot;
  footerText: Slot; timestamp: Slot;
}

export interface Brand {
  /** v2.50.1 — 'theme' prints the colours currently on screen. */
  printColors: 'brand' | 'theme';
  /** v2.52.0 — 'suppress' zeroes the page margin so the browser has nowhere to
   *  draw its own date / title / URL header and footer. Costs the page counter,
   *  which can only live in an @page margin box. */
  browserChrome: 'suppress' | 'show';
  showCompany: boolean; showPeriod: boolean; footerText: string;
  showPageNumbers: boolean; showTimestamp: boolean;
  paper: string; orientation: string;
  logoUrl: string; logoPos: 'start' | 'center' | 'end'; logoHeightMm: number;
  /** v2.55.3 — explicit logo box in millimetres. 0 means "derive from the
   *  other dimension and the image's own aspect ratio". Both are honoured in
   *  Print, PDF and the Excel drawing, so one setting sizes every output. */
  logoWidthMm: number;
  centered: boolean;
  /* v2.48.1 — play-on-top-of-default dials */
  titleSizePt: number;        // heading size in the band
  bodySizePx: number;         // table body size (GL print)
  accent: string;             // brand color: band border, headings, header fills
  borderPreset: 'minimal' | 'classic' | 'strong';
  /** v2.55.0 — how table rules are drawn in Print, PDF and Excel alike.
   *  'grid' boxes every cell (the default, because horizontal-only rules were
   *  routinely being lost to hairline rounding and colour-adjust defaults),
   *  'rows' keeps the old horizontal rules, 'none' prints unruled. */
  gridLines: 'grid' | 'rows' | 'none';
  /* v2.49.0 — per-element alignment, Excel-style */
  align: BandAlign;
  headerText: string;         // free text block in the header
  /** v2.55.0 — kept for backwards compatibility with brands saved by the
   *  General Ledger's own setup, which stored a per-report heading here.
   *  Deliberately NOT consumed as a global override: one report's renamed
   *  heading must not leak onto every other report's letterhead. */
  title?: string;
  subtitle?: string;
  pageNoPos: Align | 'hide';  // page numbers live in the @page margin box
  /* v2.48.2 — letterhead identity */
  companyName: string;        // legal name as printed (blank = the record's name)
  companyNameAr: string;      // Arabic legal name, printed under the Latin one
  vatNo: string;              // VAT registration number
  crNo: string;               // Commercial Registration number
}

const DEFAULT_BRAND: Brand = {
  showCompany: true, showPeriod: true, footerText: '',
  showPageNumbers: true, showTimestamp: true,
  paper: 'A4', orientation: 'landscape',
  logoUrl: '', logoPos: 'center', logoHeightMm: 12, logoWidthMm: 0, centered: true,
  titleSizePt: 15, bodySizePx: 12, accent: '#16404d', borderPreset: 'classic',
  gridLines: 'grid',
  companyName: '', companyNameAr: '', vatNo: '', crNo: '',
  align: { logo: 'center', title: 'center', headerText: 'hide', company: 'center', period: 'center',
           footerText: 'left', timestamp: 'right' },
  headerText: '', pageNoPos: 'center', printColors: 'brand', browserChrome: 'suppress',
};

/** Brands saved before v2.49.0 carry `centered` + `logoPos` instead of `align`.
 *  Derive the equivalent placement so an upgrade changes nothing on screen. */
function migrateAlign(b: any): BandAlign {
  if (b && b.align && b.align.title) return b.align;
  const logo: Slot = b?.logoPos === 'start' ? 'left' : b?.logoPos === 'end' ? 'right' : 'center';
  const centered = b?.centered !== false;
  return {
    logo: b?.logoUrl ? logo : 'hide',
    title: centered ? 'center' : 'left',
    headerText: 'hide',
    company: b?.showCompany === false ? 'hide' : (centered ? 'center' : 'right'),
    period: b?.showPeriod === false ? 'hide' : (centered ? 'center' : 'right'),
    footerText: 'left',
    timestamp: b?.showTimestamp === false ? 'hide' : 'right',
  };
}

/** Border color tokens per preset — consumed by print docs and the xlsx writer.
 *
 *  v2.55.0 — widths are whole pixels. Sub-pixel rules (.5px) survive on a
 *  retina screen and then round to zero in the print pipeline and in
 *  wkhtmltopdf, which is why rules kept vanishing from Print and PDF while
 *  looking fine on screen. Minimal is now a real 1px hairline; the tone, not
 *  the width, is what makes it minimal. */
export function borderTokens(b: Brand) {
  const p = b.borderPreset || 'classic';
  return p === 'minimal'
    ? { rule: '#D8D4CA', strong: '#9A9484', band: b.accent, ruleW: '1px', strongW: '1px' }
    : p === 'strong'
      ? { rule: '#8E887A', strong: '#222222', band: b.accent, ruleW: '1px', strongW: '2px' }
      : { rule: '#B9B4A6', strong: '#333333', band: b.accent, ruleW: '1px', strongW: '2px' };
}

/** The table rules every print document shares.
 *
 *  One builder so Print, server-rendered PDF and the on-screen image capture
 *  cannot disagree about whether a report has borders — the complaint that
 *  prompted this was exactly that divergence. `gridLines` decides whether
 *  cells are boxed, horizontally ruled, or plain. */
export function tableCss(b: Brand): string {
  const bt = borderTokens(b);
  const mode = b.gridLines || 'grid';
  const cellBorder = mode === 'grid'
    ? `border:${bt.ruleW} solid var(--th-rule);`
    : mode === 'rows'
      ? `border:0;border-bottom:${bt.ruleW} solid var(--th-rule);`
      : 'border:0;';
  const headBorder = mode === 'none'
    ? `border:0;border-bottom:${bt.strongW} solid var(--th-strong);`
    : mode === 'rows'
      ? `border:0;border-bottom:${bt.strongW} solid var(--th-strong);`
      : `border:${bt.ruleW} solid var(--th-strong);`;
  return `
    table.rpt{width:100%;border-collapse:collapse;table-layout:auto;}
    table.rpt th,table.rpt td{padding:4px 7px;text-align:left;vertical-align:top;${cellBorder}}
    table.rpt thead tr:not(.pr-lh) th{${headBorder}font-weight:600;
      background:var(--th-head-bg);color:var(--th-head-ink);}
    table.rpt th.num,table.rpt td.num{text-align:right;direction:ltr;unicode-bidi:isolate;white-space:nowrap;}
    table.rpt tr.sec td{font-weight:700;background:var(--th-head-bg);color:var(--th-head-ink);}
    table.rpt tr.grp td{font-weight:700;background:var(--th-group);}
    table.rpt tr.sub td{font-weight:600;background:var(--th-sub);}
    table.rpt tr.tot td{font-weight:700;background:var(--th-total);
      border-top:${bt.strongW} solid var(--th-strong);border-bottom:${bt.strongW} solid var(--th-strong);}
    table.rpt tr.grand td{font-weight:700;background:var(--th-total);
      border-top:${bt.strongW} solid var(--th-strong);border-bottom:${bt.strongW} double var(--th-strong);}
    table.rpt tbody tr{page-break-inside:avoid;}
  `;
}

function brandKeyFor(company?: string | null): string {
  if (company) return company;
  try { return localStorage.getItem('ni-active-company') || localStorage.getItem('ni-gl-lastco') || 'default'; }
  catch { return 'default'; }
}

export function loadBrand(company?: string | null): Brand {
  const co = brandKeyFor(company);
  try {
    const v = localStorage.getItem('ni-brand:' + co)
      || localStorage.getItem('ni-gl-printsetup:' + co)
      || localStorage.getItem('ni-brand:default');
    return { ...DEFAULT_BRAND, ...(v ? JSON.parse(v) : {}) };
  } catch { return { ...DEFAULT_BRAND }; }
}

export function brandDefaults(): Brand { return { ...DEFAULT_BRAND }; }

/* ── Site-wide persistence (v2.55.0) ──────────────────────────────────────
 *
 * The Brand Kit used to live only in localStorage, so the letterhead had to
 * be rebuilt on every machine and printed packs from two operators didn't
 * match. It is now stored on the site and cached locally: localStorage stays
 * the synchronous read path (print builders cannot await), the server is the
 * source of truth that survives a new browser.
 *
 * The API module is imported lazily to keep branddoc usable from the export
 * utilities without dragging the whole api surface into every print path.
 */

function writeLocalBrand(company: string | null | undefined, brand: Partial<Brand>) {
  try { localStorage.setItem('ni-brand:' + brandKeyFor(company), JSON.stringify(brand)); }
  catch { /* private mode */ }
}

/** Pull the saved Brand Kits into localStorage. Call once on boot; failures
 *  are silent because the local copy (or the defaults) still render. */
export async function syncBrandFromServer(): Promise<void> {
  try {
    const { api } = await import('./api');
    const store = await api.getBrand();
    if (!store || typeof store !== 'object') return;
    for (const [co, brand] of Object.entries(store)) {
      if (brand && typeof brand === 'object') writeLocalBrand(co, brand as Partial<Brand>);
    }
  } catch { /* offline or not permitted — local copy stands */ }
}

/** Save one company's Brand Kit locally (immediately) and site-wide (best
 *  effort). Returns false when the site-wide write was refused, so the caller
 *  can tell the user their setup is machine-local. */
export async function saveBrand(company: string | null | undefined, brand: Partial<Brand>): Promise<boolean> {
  writeLocalBrand(company, brand);
  try {
    const { api } = await import('./api');
    await api.saveBrand(brandKeyFor(company), brand);
    return true;
  } catch {
    return false;
  }
}

export interface FrameCtx {
  title: string; subtitle?: string; companyLabel?: string; periodLabel?: string;
  paperOverride?: string; orientationOverride?: string;
}

export interface FrameOpts {
  /** 'fixed' (default) pins the bands into the page margins with position:fixed.
   *  'flow' returns bare band markup meant for a table's <thead>/<tfoot>, which
   *  the browser repeats on every printed page while keeping it in normal flow —
   *  in-flow content cannot be painted over the rows, which is the failure mode
   *  'fixed' has whenever a negative offset gets clamped back into the page box
   *  (and on screen, where there is no margin box at all). */
  mode?: 'fixed' | 'flow';
}

const esc = (s: any) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** Isolate a free-text token: an Arabic name inside an English line (or the
 *  reverse) renders as its own run and never drags the neighbouring digits,
 *  dashes or punctuation with it. */
export const bdi = (s: any) => `<bdi>${esc(s)}</bdi>`;

/** The identity lines printed under the title: legal name, Arabic legal name,
 *  and the VAT / CR line. `fallback` is used when no legal name is configured
 *  (typically the Company record's own company_name). */
export function identityLines(b: Brand, fallback?: string, tr: (s: string) => string = (s) => s) {
  const name = (b.companyName || '').trim() || (fallback || '').trim();
  const nameAr = (b.companyNameAr || '').trim();
  const ids: string[] = [];
  if ((b.vatNo || '').trim()) ids.push(tr('VAT No') + ' ' + b.vatNo.trim());
  if ((b.crNo || '').trim()) ids.push(tr('CR No') + ' ' + b.crNo.trim());
  return { name, nameAr, idLine: ids.join('  \u00b7  ') };
}

/** A stable document title: it becomes the Save-as-PDF filename and the text
 *  the browser prints in its own header, so it carries the report, the company
 *  and the period instead of a bare "General Ledger". */
export function docTitle(ctx: FrameCtx): string {
  // An Arabic company name gives the whole title an RTL base direction, which
  // visually reverses the date range (`to → from`). Isolate the period so it
  // reads correctly in the browser chrome and the Save-as-PDF filename.
  const period = ctx.periodLabel ? '\u2066' + ctx.periodLabel + '\u2069' : '';
  return [ctx.title, ctx.companyLabel, period].filter(Boolean).join(' \u2014 ');
}

/* Approximate rendered line height in millimetres for a px font size. */
const lineMm = (px: number) => Math.round(px * 0.2646 * 1.35 * 10) / 10;

/** Builds the letterhead band and footer strip as ordinary in-flow markup,
 *  meant to be dropped into a table's <thead> / <tfoot>. Browsers repeat both
 *  groups on every printed page, so the band cannot be painted over the rows —
 *  the failure mode the old position:fixed frame had whenever its negative
 *  offset was clamped back into the page box.
 *
 *  Each element is placed by `align` into a left / centre / right column, so a
 *  logo can sit left while the title centres and the company details go right. */
export function buildFrame(bIn: Brand, ctx: FrameCtx, tr: (s: string) => string = (s) => s, _opts: FrameOpts = {}) {
  const b = { ...DEFAULT_BRAND, ...(bIn || {}) } as Brand;
  const al: BandAlign = { ...DEFAULT_BRAND.align, ...migrateAlign(bIn || {}), ...((bIn as any)?.align || {}) };
  const logoH = b.logoUrl ? Number(b.logoHeightMm || 0) : 0;
  const logoW = b.logoUrl ? Number(b.logoWidthMm || 0) : 0;
  const ttlPt = Number(b.titleSizePt || 15);
  const subPt = Math.max(9, Math.round(ttlPt * 0.72));
  const id = identityLines(b, ctx.companyLabel, tr);
  const paper = ctx.paperOverride || b.paper;
  const orient = ctx.orientationOverride || b.orientation;
  const genTs = new Date().toLocaleString();
  const bt = borderTokens(b);
  const tk = resolvePrintTokens(b);

  /* Collect each visible block under the column it was assigned. Order within
     a column is fixed so the result stays predictable however they are mixed. */
  const cols: Record<Align, string[]> = { left: [], center: [], right: [] };
  const put = (slot: Slot, html: string) => { if (slot !== 'hide' && html) cols[slot].push(html); };

  put(b.logoUrl ? al.logo : 'hide', `<img src="${esc(b.logoUrl)}" alt="" />`);
  put(al.title, `<div class="ttl">${bdi(ctx.title)}</div>${ctx.subtitle ? `<div class="sub">${bdi(ctx.subtitle)}</div>` : ''}`);
  put((b.headerText || '').trim() ? al.headerText : 'hide', `<div class="htx">${bdi(b.headerText)}</div>`);
  put(id.name || id.nameAr || id.idLine ? al.company : 'hide',
    `${id.name ? `<div class="co-name">${bdi(id.name)}</div>` : ''}`
    + `${id.nameAr ? `<div class="co-name-ar" lang="ar">${bdi(id.nameAr)}</div>` : ''}`
    + `${id.idLine ? `<div class="co-id">${bdi(id.idLine)}</div>` : ''}`);
  put(ctx.periodLabel ? al.period : 'hide', `<div class="co-per">${bdi(ctx.periodLabel)}</div>`);

  const foot: Record<Align, string[]> = { left: [], center: [], right: [] };
  const putF = (slot: Slot, html: string) => { if (slot !== 'hide' && html) foot[slot].push(html); };
  putF((b.footerText || '').trim() ? al.footerText : 'hide', bdi(b.footerText));
  putF(al.timestamp, esc(tr('Generated') + ' ' + genTs));

  const col = (k: Align, list: string[]) => `<div class="pr-col pr-${k}">${list.join('')}</div>`;
  const bandHtml = `<div class="pr-band">${(['left', 'center', 'right'] as Align[]).map((k) => col(k, cols[k])).join('')}</div>`;
  const stripHtml = (['left', 'center', 'right'] as Align[]).some((k) => foot[k].length)
    ? `<div class="pr-strip">${(['left', 'center', 'right'] as Align[]).map((k) => col(k, foot[k])).join('')}</div>`
    : '';

  // With a zero margin there is no margin box: the browser cannot print its
  // header/footer, and neither can we print a page counter.
  const suppress = b.browserChrome !== 'show';
  const pageBox = !suppress && b.pageNoPos && b.pageNoPos !== 'hide'
    ? `@bottom-${b.pageNoPos === 'left' ? 'left' : b.pageNoPos === 'right' ? 'right' : 'center'} { content: "${esc(tr('Page'))} " counter(page) " / " counter(pages); font-size: 9px; color: #666; }`
    : '';

  const css = `
    ${printVarsCss(tk)}
    @page { size: ${esc(paper)} ${esc(orient)}; margin: ${suppress ? '0' : '10mm 10mm 12mm 10mm'}; ${pageBox} }
    ${suppress ? '@media print { body { padding: 10mm 10mm 12mm 10mm !important; margin: 0 !important; } }' : ''}
    .pr-band{display:flex;align-items:flex-end;gap:6mm;padding:0 0 2mm;border-bottom:${bt.strongW} solid var(--th-accent);}
    .pr-strip{display:flex;align-items:flex-start;gap:6mm;padding-top:1.5mm;border-top:1px solid #bbb;font-size:9px;color:#666;font-weight:400;}
    .pr-col{flex:1 1 0;min-width:0;}
    .pr-col:empty{flex:1 1 0;}
    .pr-left{text-align:left;} .pr-center{text-align:center;} .pr-right{text-align:right;}
    .pr-band .ttl{font-size:${ttlPt}px;font-weight:700;color:var(--th-accent);line-height:1.35;}
    .pr-band .sub{font-size:${subPt}px;color:#555;font-weight:400;line-height:1.35;}
    .pr-band .htx{font-size:11px;color:#444;font-weight:400;line-height:1.35;}
    .pr-band .co-name{font-size:12px;font-weight:600;color:#333;line-height:1.35;}
    .pr-band .co-name-ar{font-size:12px;font-weight:600;color:#333;line-height:1.35;}
    .pr-band .co-id{font-size:10px;color:#666;font-weight:400;line-height:1.35;}
    .pr-band .co-per{font-size:11px;color:#444;font-weight:400;line-height:1.35;}
    /* max-width is the fix for a wide wordmark: without it the image
       overflowed its flex column and painted straight over the title. */
    .pr-band img{${logoH ? `height:${logoH}mm;` : ''}${logoW ? `width:${logoW}mm;` : ''}
      max-width:100%;max-height:${Math.max(logoH || 12, 8) * 1.6}mm;
      object-fit:contain;display:inline-block;vertical-align:bottom;}
    .pr-col.pr-left  .pr-band img,.pr-band .pr-left  img{object-position:left bottom;}
    .pr-band .pr-right img{object-position:right bottom;}
    thead{display:table-header-group;} tfoot{display:table-footer-group;}
    tr.pr-lh th{padding:0 0 3mm;border:0;background:none;font-weight:400;}
    tfoot td{border:0;padding:3mm 0 0;}
  `;

  return { css, headerHtml: bandHtml, footerHtml: stripHtml, headH: 0, footH: 0 };
}

/** Wraps the band in the table row that carries it, ready to be spliced into a
 *  <thead>. Keeping this here means every print path repeats the letterhead the
 *  same way instead of each one inventing its own markup. */
export function bandRow(bandHtml: string, colCount = 99) {
  return `<tr class="pr-lh"><th colspan="${colCount}">${bandHtml}</th></tr>`;
}

export function stripRow(stripHtml: string, colCount = 99) {
  return stripHtml ? `<tfoot><tr><td colspan="${colCount}">${stripHtml}</td></tr></tfoot>` : '';
}
