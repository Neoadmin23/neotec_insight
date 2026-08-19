/* v2.55.0 — one document, five outputs.
 *
 * Every report menu used to carry its own idea of what "export" meant: the
 * ledger had Excel + Print + PDF, the statements had Excel + Print, the pivots
 * had CSV, and each one built its own markup with its own (or no) borders.
 * The result was a PDF button that existed in one place, letterheads that
 * drifted, and rules that printed on one screen and vanished on another.
 *
 * A report now describes itself once — columns, rows, title, period — and this
 * module renders that description to Excel, CSV, PDF, Print and PNG. Adding a
 * format is a change here, not in fifteen components; and because Print and
 * PDF are the same string, they cannot disagree.
 */

import { api } from './api';
import {
  loadBrand, buildFrame, borderTokens, tableCss, docTitle, bdi, identityLines,
  type Brand,
} from './branddoc';

/* ── the model ──────────────────────────────────────────────────────────── */

export interface DocColumn {
  label: string;
  /** right-aligned, and written to Excel as a number when the value is one */
  num?: boolean;
  /** column width in Excel characters; sensible defaults applied when absent */
  width?: number;
}

export interface DocCell {
  /** The raw value. Numbers land in Excel as numbers, so this must stay
   *  unformatted — `1234.5`, never `"1,234.50"`. */
  v: string | number | null | undefined;
  /** v2.55.3 — how the value should READ in Print, PDF, CSV and PNG.
   *  Without this the shared writers stringified raw numbers and printed
   *  `5385470.85` where the screen showed `5,385,470.85`. Excel keeps `v`
   *  so the figures stay computable. */
  text?: string;
  num?: boolean;
  bold?: boolean;
  /** indent depth for hierarchy labels — 1 level ≈ 14px / 2 Excel indents */
  indent?: number;
  colSpan?: number;
  /** override colour, e.g. a loss in red. '#rrggbb'. */
  fg?: string;
  /** v2.74.0 — absolute URL to the source document. Rendered as an anchor in
   *  HTML, Print and PDF, and as a HYPERLINK formula in Excel. CSV keeps the
   *  plain text and gains no link, because a CSV cell cannot carry one. */
  link?: string;
}

export type RowKind = 'normal' | 'sec' | 'grp' | 'sub' | 'tot' | 'grand' | 'gap';

export interface DocRow {
  kind?: RowKind;
  cells: DocCell[];
  /** start a new page after this row (the ledger's per-account page break) */
  breakAfter?: boolean;
}

export interface ReportDoc {
  /** heading printed in the letterhead band (Brand Kit title overrides it) */
  title: string;
  subtitle?: string;
  /** company docname — selects the Brand Kit and the letterhead identity */
  company?: string;
  /** company display label, Arabic-resolved by the caller */
  companyLabel?: string;
  /** e.g. "2026-01-01 → 2026-07-30" or "As of 2026-07-30" */
  period?: string;
  columns: DocColumn[];
  rows: DocRow[];
  /** filename stem; the period and company are appended */
  fileBase: string;
  /** overrides the Brand Kit orientation for this report only */
  orientation?: 'portrait' | 'landscape';
  /** overrides the Brand Kit paper size for this report only */
  paper?: string;
  /** extra note printed under the table (currency conversion, caveats) */
  note?: string;
}

const esc = (s: any) => String(s ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** Display text: the caller's formatted string when it gave one, otherwise
 *  the raw value. Used by every output except Excel. */
function cellText(c: DocCell): string {
  if (c.text !== undefined && c.text !== null) return c.text;
  if (c.v === null || c.v === undefined) return '';
  return typeof c.v === 'number' ? String(c.v) : c.v;
}

function fileName(doc: ReportDoc, ext: string): string {
  const stem = [doc.fileBase || 'report', doc.companyLabel || doc.company || '', doc.period || '']
    .filter(Boolean).join(' - ')
    .replace(/[\\/:*?"<>|\u2192]+/g, '-')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 120);
  return `${stem}.${ext}`;
}

function resolveBrand(doc: ReportDoc): Brand {
  const b = loadBrand(doc.company || null);
  return {
    ...b,
    orientation: doc.orientation || b.orientation,
    paper: doc.paper || b.paper,
  };
}

/* ── HTML (drives Print, PDF and PNG) ───────────────────────────────────── */

/** The single print document. Print opens it, the server renders it to PDF,
 *  and the image export rasterises it — so all three are the same artefact. */
export function buildDocHtml(doc: ReportDoc, opts: { forScreen?: boolean } = {}): string {
  const b = resolveBrand(doc);
  const bt = borderTokens(b);
  const nCols = Math.max(1, doc.columns.length);

  const ctx = {
    title: doc.title,
    subtitle: doc.subtitle,
    companyLabel: doc.companyLabel || doc.company || '',
    periodLabel: doc.period || '',
    paperOverride: b.paper,
    orientationOverride: b.orientation,
  };
  const fr = buildFrame(b, ctx, (s) => s, { mode: 'flow' });

  const th = doc.columns
    .map((c) => `<th class="${c.num ? 'num' : ''}">${esc(c.label)}</th>`)
    .join('');

  const body = doc.rows.map((r) => {
    if (r.kind === 'gap') return `<tr class="gap"><td colspan="${nCols}">&nbsp;</td></tr>`;
    const cls = [r.kind && r.kind !== 'normal' ? r.kind : '', r.breakAfter ? 'brk' : '']
      .filter(Boolean).join(' ');
    const tds = r.cells.map((c) => {
      const isNum = !!c.num;
      const style = [
        c.indent ? `padding-left:${8 + 14 * c.indent}px` : '',
        c.bold ? 'font-weight:700' : '',
        c.fg ? `color:${esc(c.fg)}` : '',
      ].filter(Boolean).join(';');
      return `<td${c.colSpan && c.colSpan > 1 ? ` colspan="${c.colSpan}"` : ''}`
        + `${isNum ? ' class="num"' : ''}${style ? ` style="${style}"` : ''}>`
        + (c.link
            ? `<a href="${esc(c.link)}" target="_blank" rel="noopener noreferrer" class="doclink">${bdi(cellText(c))}</a>`
            : (isNum ? esc(cellText(c)) : bdi(cellText(c))))
        + '</td>';
    }).join('');
    return `<tr${cls ? ` class="${cls}"` : ''}>${tds}</tr>`;
  }).join('');

  const note = doc.note
    ? `<div class="rpt-note">${bdi(doc.note)}</div>`
    : '';

  return `<!doctype html><html lang="en" dir="ltr"><head><meta charset="utf-8" />
    <title>${esc(docTitle(ctx))}</title><style>
    /* Colour-adjust is the reason fills and rules print at all: without it
       Chromium and wkhtmltopdf both drop backgrounds in print media, which
       reads to the operator as "the borders aren't printing". */
    *{-webkit-print-color-adjust:exact;print-color-adjust:exact;color-adjust:exact;box-sizing:border-box;}
    body{font:${Number(b.bodySizePx || 12)}px -apple-system,system-ui,"Segoe UI",Tahoma,Arial,sans-serif;
         color:var(--th-ink);background:var(--th-paper);margin:0;}
    ${fr.css}
    ${tableCss(b)}
    tr.brk{page-break-after:always;}
    tr.gap td{border:0;background:none;height:6px;padding:0;}
    .rpt-note{margin-top:6px;font-size:10px;color:#666;}
    thead tr.pr-lh th{border:0 !important;background:none !important;}
    tfoot td{border:0 !important;background:none !important;}
    @media screen{body{padding:18px;}}
    ${opts.forScreen ? 'body{padding:0;background:#fff;}' : ''}
  </style></head><body>
    <table class="rpt">
      <thead>
        <tr class="pr-lh"><th colspan="${nCols}">${fr.headerHtml}</th></tr>
        <tr>${th}</tr>
      </thead>
      ${fr.footerHtml ? `<tfoot><tr><td colspan="${nCols}">${fr.footerHtml}</td></tr></tfoot>` : ''}
      <tbody>${body}</tbody>
    </table>
    ${note}
  </body></html>`
    // A zero-width border colour token is never wanted; keep the strong rule
    // available to callers that inline their own styles.
    .replace('/*__STRONG__*/', bt.strongW);
}

/* ── Print ──────────────────────────────────────────────────────────────── */

export function printDoc(doc: ReportDoc) {
  const html = buildDocHtml(doc);
  const w = window.open('', '_blank');
  if (!w) { alert('Pop-ups are blocked — allow them for this site to print.'); return; }
  w.document.write(html);
  w.document.close();
  w.focus();
  setTimeout(() => w.print(), 350);
}

/* ── PDF (server-rendered from the same document) ───────────────────────── */

export async function pdfDoc(doc: ReportDoc): Promise<void> {
  const b = resolveBrand(doc);
  const html = buildDocHtml(doc);
  const stem = fileName(doc, '').replace(/\.$/, '');
  await api.renderPdf(html, stem, b.orientation || 'portrait', b.paper || 'A4');
}

/* ── CSV ────────────────────────────────────────────────────────────────── */

function csvEsc(v: any): string {
  const s = String(v ?? '');
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function csvDoc(doc: ReportDoc) {
  const b = resolveBrand(doc);
  const id = identityLines(b, doc.companyLabel || doc.company || '');
  const lines: string[] = [];
  lines.push(csvEsc(doc.title));
  if (doc.subtitle) lines.push(csvEsc(doc.subtitle));
  if (id.name) lines.push(csvEsc(id.name));
  if (id.nameAr) lines.push(csvEsc(id.nameAr));
  if (id.idLine) lines.push(csvEsc(id.idLine));
  if (doc.period) lines.push(csvEsc(doc.period));
  lines.push('');
  lines.push(doc.columns.map((c) => csvEsc(c.label)).join(','));
  for (const r of doc.rows) {
    if (r.kind === 'gap') { lines.push(''); continue; }
    const out: string[] = [];
    r.cells.forEach((c) => {
      const indent = c.indent ? '  '.repeat(c.indent) : '';
      out.push(csvEsc(indent + cellText(c)));
      for (let k = 1; k < (c.colSpan || 1); k++) out.push('');
    });
    lines.push(out.join(','));
  }
  if (doc.note) { lines.push(''); lines.push(csvEsc(doc.note)); }
  if (b.footerText) { lines.push(''); lines.push(csvEsc(b.footerText)); }
  // The BOM is what makes Excel open a UTF-8 CSV with Arabic intact.
  download(new Blob(['\uFEFF' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' }),
    fileName(doc, 'csv'));
}

/* ── Image (PNG of the rendered document) ───────────────────────────────── */

export async function imageDoc(doc: ReportDoc): Promise<void> {
  const html = buildDocHtml(doc, { forScreen: true });
  // html2canvas reads computed styles off the live DOM, so the document has to
  // be attached — parked off-canvas rather than hidden, because display:none
  // has no layout to measure.
  const host = document.createElement('div');
  host.setAttribute('aria-hidden', 'true');
  host.style.cssText = 'position:fixed;top:0;left:-20000px;width:1400px;background:#fff;z-index:-1;';
  const inner = html.slice(html.indexOf('<body>') + 6, html.lastIndexOf('</body>'));
  const styleMatch = html.match(/<style>([\s\S]*?)<\/style>/);
  host.innerHTML = (styleMatch ? `<style>${styleMatch[1]}</style>` : '') + inner;
  document.body.appendChild(host);
  try {
    const { default: html2canvas } = await import('html2canvas');
    const canvas = await html2canvas(host, {
      backgroundColor: '#ffffff',
      scale: Math.min(2, window.devicePixelRatio || 1) * 1.5,
      logging: false,
      useCORS: true,
    });
    const blob: Blob | null = await new Promise((res) => canvas.toBlob(res, 'image/png'));
    if (blob) download(blob, fileName(doc, 'png'));
  } finally {
    host.remove();
  }
}


/* ── logo bytes for the workbook (v2.55.3) ─────────────────────────────────
 *
 * Print and PDF render the letterhead logo from a URL; Excel cannot — a
 * workbook has to carry the image itself, as a drawing part. So the same
 * logo that heads the printed page is fetched, embedded and anchored above
 * the title rows, which is what closes the "Excel has no heading" gap.
 *
 * SVG is deliberately skipped: Excel's drawing model does not render it, and
 * a broken image placeholder is worse than a text-only header.
 */

interface LogoBytes { data: Uint8Array; ext: string; wPx: number; hPx: number }

/** Intrinsic pixel size from the file header, so an unspecified dimension can
 *  be derived from the image's own aspect ratio rather than guessed. */
function imageSize(d: Uint8Array, ext: string): { w: number; h: number } {
  try {
    if (ext === 'png' && d.length > 24) {
      const dv = new DataView(d.buffer, d.byteOffset);
      return { w: dv.getUint32(16), h: dv.getUint32(20) };
    }
    if (ext === 'jpeg') {
      let i = 2;
      while (i < d.length - 9) {
        if (d[i] !== 0xFF) { i++; continue; }
        const m = d[i + 1];
        // SOF0..SOF15, skipping the four non-frame markers in that range.
        if (m >= 0xC0 && m <= 0xCF && m !== 0xC4 && m !== 0xC8 && m !== 0xCC) {
          return { w: (d[i + 7] << 8) | d[i + 8], h: (d[i + 5] << 8) | d[i + 6] };
        }
        i += 2 + ((d[i + 2] << 8) | d[i + 3]);
      }
    }
    if (ext === 'gif' && d.length > 10) {
      return { w: d[6] | (d[7] << 8), h: d[8] | (d[9] << 8) };
    }
  } catch { /* fall through to the default below */ }
  return { w: 0, h: 0 };
}

async function fetchLogo(url: string): Promise<LogoBytes | null> {
  if (!url) return null;
  if (/\.svg(\?|$)/i.test(url)) return null;
  try {
    const r = await fetch(url, { credentials: 'same-origin' });
    if (!r.ok) return null;
    const type = (r.headers.get('content-type') || '').toLowerCase();
    if (type.includes('svg')) return null;
    const ext = type.includes('png') ? 'png'
      : type.includes('jpeg') || type.includes('jpg') ? 'jpeg'
        : type.includes('gif') ? 'gif'
          : /\.png(\?|$)/i.test(url) ? 'png'
            : /\.jpe?g(\?|$)/i.test(url) ? 'jpeg'
              : /\.gif(\?|$)/i.test(url) ? 'gif' : '';
    if (!ext) return null;
    const data = new Uint8Array(await r.arrayBuffer());
    const { w, h } = imageSize(data, ext);
    return { data, ext, wPx: w, hPx: h };
  } catch {
    // A logo that will not load is not a reason to lose the workbook.
    return null;
  }
}

const MM_TO_EMU = 36000;
const MM_TO_PT = 2.834645;

/** Resolve the drawing box in millimetres from the Brand Kit settings and the
 *  image's own proportions, so setting only one dimension still looks right. */
function logoBoxMm(b: Brand, img: LogoBytes): { w: number; h: number } {
  const setW = Number(b.logoWidthMm || 0);
  const setH = Number(b.logoHeightMm || 0);
  const ratio = img.wPx && img.hPx ? img.wPx / img.hPx : 3;
  if (setW && setH) return { w: setW, h: setH };
  if (setW) return { w: setW, h: setW / ratio };
  if (setH) return { w: setH * ratio, h: setH };
  return { w: 12 * ratio, h: 12 };
}

/* ── Excel ──────────────────────────────────────────────────────────────── */

const xesc = (s: any) => String(s ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function colRef(i: number): string {
  let s = ''; i++;
  while (i > 0) { const m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = Math.floor((i - 1) / 26); }
  return s;
}

/** Excel export with real cell borders.
 *
 *  The previous writer declared exactly one, empty border and pointed every
 *  style at it, so no workbook this app produced had ever had a rule in it —
 *  which is why "Excel border lines are not printing" was literally true.
 *  Gridline printing is switched on too, so even unstyled areas rule up. */
export async function xlsxDoc(doc: ReportDoc) {
  const b = resolveBrand(doc);
  const img = b.showCompany !== false ? await fetchLogo(b.logoUrl || '') : null;
  const logoBox = img ? logoBoxMm(b, img) : null;
  const accent = (b.accent || '#16404d').replace('#', '').toUpperCase();
  const bp = b.borderPreset || 'classic';
  const ruleHex = bp === 'minimal' ? 'FFD8D4CA' : bp === 'strong' ? 'FF8E887A' : 'FFB9B4A6';
  const strongHex = bp === 'strong' ? 'FF222222' : bp === 'minimal' ? 'FF9A9484' : 'FF333333';
  const strongStyle = bp === 'strong' ? 'medium' : 'thin';
  const grid = (b.gridLines || 'grid');
  const nCols = Math.max(1, doc.columns.length);

  const fonts = [
    '<font><sz val="10"/><color rgb="FF2C2C2A"/><name val="Calibri"/></font>',                        // 0 body
    `<font><b/><sz val="${Math.round(b.titleSizePt || 15)}"/><color rgb="FF${accent}"/><name val="Calibri"/></font>`, // 1 title
    '<font><sz val="11"/><color rgb="FF555555"/><name val="Calibri"/></font>',                        // 2 meta
    '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>',                    // 3 col head
    '<font><b/><sz val="10"/><color rgb="FF2C2C2A"/><name val="Calibri"/></font>',                    // 4 bold body
    '<font><sz val="9"/><color rgb="FF888888"/><name val="Calibri"/></font>',                         // 5 footer
    '<font><b/><sz val="10"/><color rgb="FFA02323"/><name val="Calibri"/></font>',                    // 6 negative
  ];
  const fills = [
    '<fill><patternFill patternType="none"/></fill>',
    '<fill><patternFill patternType="gray125"/></fill>',
    `<fill><patternFill patternType="solid"><fgColor rgb="FF${accent}"/><bgColor indexed="64"/></patternFill></fill>`, // 2 col head
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF3F1EC"/><bgColor indexed="64"/></patternFill></fill>',   // 3 group
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFAF9F6"/><bgColor indexed="64"/></patternFill></fill>',   // 4 sub
    '<fill><patternFill patternType="solid"><fgColor rgb="FFEDEFF7"/><bgColor indexed="64"/></patternFill></fill>',   // 5 total
  ];

  // Border ids: 0 none · 1 body · 2 column head · 3 total band
  const side = (style: string, hex: string) => `style="${style}"><color rgb="${hex}"/>`;
  const box = (style: string, hex: string) =>
    `<border><left ${side(style, hex)}</left><right ${side(style, hex)}</right>`
    + `<top ${side(style, hex)}</top><bottom ${side(style, hex)}</bottom><diagonal/></border>`;
  const rowsOnly = (style: string, hex: string) =>
    `<border><left/><right/><top/><bottom ${side(style, hex)}</bottom><diagonal/></border>`;
  const bodyBorder = grid === 'grid' ? box('thin', ruleHex)
    : grid === 'rows' ? rowsOnly('thin', ruleHex)
      : '<border><left/><right/><top/><bottom/><diagonal/></border>';
  const headBorder = grid === 'none' ? rowsOnly(strongStyle, strongHex) : box(strongStyle, strongHex);
  const totalBorder = grid === 'none'
    ? `<border><left/><right/><top ${side(strongStyle, strongHex)}</top><bottom ${side(strongStyle, strongHex)}</bottom><diagonal/></border>`
    : box(strongStyle, strongHex);
  const borders = [
    '<border><left/><right/><top/><bottom/><diagonal/></border>',
    bodyBorder, headBorder, totalBorder,
  ];

  interface Xf { f: number; fl: number; b: number; h?: string; ind?: number; wrap?: boolean }
  const xfs: Xf[] = [{ f: 0, fl: 0, b: 0 }];
  const xfIdx = (d: Xf) => {
    const k = JSON.stringify(d);
    const i = xfs.findIndex((x) => JSON.stringify(x) === k);
    if (i >= 0) return i;
    xfs.push(d); return xfs.length - 1;
  };

  const styleFor = (kind: RowKind, c: DocCell) => {
    const align = c.num ? 'right' : 'left';
    const ind = !c.num && c.indent ? Math.min(8, c.indent * 2) : 0;
    if (kind === 'sec') return xfIdx({ f: 3, fl: 2, b: 2, h: align, ind });
    if (kind === 'grp') return xfIdx({ f: 4, fl: 3, b: 1, h: align, ind });
    if (kind === 'sub') return xfIdx({ f: 4, fl: 4, b: 1, h: align, ind });
    if (kind === 'tot' || kind === 'grand') {
      return xfIdx({ f: c.fg === '#a02323' ? 6 : 4, fl: 5, b: 3, h: align, ind });
    }
    return xfIdx({ f: c.bold ? 4 : 0, fl: 0, b: 1, h: align, ind });
  };

  const S = {
    title: xfIdx({ f: 1, fl: 0, b: 0, h: 'center' }),
    meta: xfIdx({ f: 2, fl: 0, b: 0, h: 'center' }),
    footer: xfIdx({ f: 5, fl: 0, b: 0, h: 'center' }),
    headL: xfIdx({ f: 3, fl: 2, b: 2, h: 'left' }),
    headR: xfIdx({ f: 3, fl: 2, b: 2, h: 'right' }),
  };

  const merges: string[] = [];
  const lines: string[] = [];
  let rn = 0;

  const bandRow = (text: string, style: number, ht?: number) => {
    rn += 1;
    const cells: string[] = [`<c r="A${rn}" s="${style}" t="inlineStr"><is><t xml:space="preserve">${xesc(text)}</t></is></c>`];
    for (let c = 1; c < nCols; c++) cells.push(`<c r="${colRef(c)}${rn}" s="${style}"/>`);
    if (nCols > 1) merges.push(`A${rn}:${colRef(nCols - 1)}${rn}`);
    lines.push(`<row r="${rn}"${ht ? ` ht="${ht}" customHeight="1"` : ''}>${cells.join('')}</row>`);
  };

  const id = identityLines(b, doc.companyLabel || doc.company || '');
  // A tall blank row reserves the space the drawing floats over, so the logo
  // sits above the title instead of on top of it.
  if (logoBox) {
    rn += 1;
    lines.push(`<row r="${rn}" ht="${Math.round(logoBox.h * MM_TO_PT) + 6}" customHeight="1"/>`);
  }
  bandRow(doc.title, S.title, 22);
  if (doc.subtitle) bandRow(doc.subtitle, S.meta);
  if (b.showCompany !== false) {
    if (id.name) bandRow(id.name, S.meta);
    if (id.nameAr) bandRow(id.nameAr, S.meta);
    if (id.idLine) bandRow(id.idLine, S.meta);
  }
  if (b.showPeriod !== false && doc.period) bandRow(doc.period, S.meta);
  rn += 1; lines.push(`<row r="${rn}"/>`);          // spacer

  rn += 1;
  lines.push(`<row r="${rn}" ht="18" customHeight="1">`
    + doc.columns.map((c, i) =>
      `<c r="${colRef(i)}${rn}" s="${c.num ? S.headR : S.headL}" t="inlineStr">`
      + `<is><t xml:space="preserve">${xesc(c.label)}</t></is></c>`).join('')
    + '</row>');
  const headRows = rn;

  const breaks: number[] = [];
  for (const r of doc.rows) {
    rn += 1;
    if (r.kind === 'gap') { lines.push(`<row r="${rn}"/>`); continue; }
    const kind = r.kind || 'normal';
    const cells: string[] = [];
    let ci = 0;
    for (const c of r.cells) {
      const s = styleFor(kind, c);
      const ref = `${colRef(ci)}${rn}`;
      const raw = c.v;
      const asNum = c.num && typeof raw === 'number' && isFinite(raw);
      if (asNum) cells.push(`<c r="${ref}" s="${s}"><v>${raw}</v></c>`);
      else if (raw !== '' && raw !== null && raw !== undefined) {
        if (c.link) {
          const disp = cellText(c).replace(/"/g, '""');
          cells.push(`<c r="${ref}" s="${s}"><f>HYPERLINK("${xesc(c.link)}","${xesc(disp)}")</f></c>`);
        } else {
          cells.push(`<c r="${ref}" s="${s}" t="inlineStr"><is><t xml:space="preserve">${xesc(cellText(c))}</t></is></c>`);
        }
      } else cells.push(`<c r="${ref}" s="${s}"/>`);
      if (c.colSpan && c.colSpan > 1) {
        for (let k = 1; k < c.colSpan; k++) cells.push(`<c r="${colRef(ci + k)}${rn}" s="${s}"/>`);
        merges.push(`${colRef(ci)}${rn}:${colRef(ci + c.colSpan - 1)}${rn}`);
      }
      ci += c.colSpan && c.colSpan > 1 ? c.colSpan : 1;
    }
    // Pad short rows so the boxed border runs the full table width.
    for (; ci < nCols; ci++) {
      cells.push(`<c r="${colRef(ci)}${rn}" s="${styleFor(kind, { v: '' })}"/>`);
    }
    lines.push(`<row r="${rn}"${kind === 'sec' ? ' ht="17" customHeight="1"' : ''}>${cells.join('')}</row>`);
    if (r.breakAfter) breaks.push(rn);
  }
  if (doc.note) { rn += 1; lines.push(`<row r="${rn}"/>`); bandRow(doc.note, S.footer); }
  if (b.footerText) { rn += 1; lines.push(`<row r="${rn}"/>`); bandRow(b.footerText, S.footer); }

  const colsXml = doc.columns.map((c, i) =>
    `<col min="${i + 1}" max="${i + 1}" width="${c.width || (i === 0 ? 40 : c.num ? 15 : 22)}" customWidth="1"/>`).join('');

  const hf = (v?: string) => (v || '').replace(/&/g, '&&');
  const hfEsc = (v: string) => v.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const oddHeader = [
    id.name ? '&L' + hf(id.name) : '',
    '&C' + hf(doc.title),
    doc.period ? '&R' + hf(doc.period) : '',
  ].join('');
  const oddFooter = '&L' + hf(b.footerText || '') + '&RPage &P of &N';

  const brkXml = breaks.length
    ? `<rowBreaks count="${breaks.length}" manualBreakCount="${breaks.length}">`
      + breaks.map((r) => `<brk id="${r}" max="16383" man="1"/>`).join('') + '</rowBreaks>'
    : '';

  const sheetName = 'Report';
  const sheet = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">`
    + `<sheetViews><sheetView workbookViewId="0"><pane ySplit="${headRows}" topLeftCell="A${headRows + 1}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>`
    + `<cols>${colsXml}</cols><sheetData>${lines.join('')}</sheetData>`
    + (merges.length ? `<mergeCells count="${merges.length}">${merges.map((m) => `<mergeCell ref="${m}"/>`).join('')}</mergeCells>` : '')
    // gridLines="1" makes Excel print its own rules as well, so a workbook
    // reads as a table even where a style carries no border.
    + `<printOptions gridLines="${grid === 'none' ? 0 : 1}" horizontalCentered="0"/>`
    + '<pageMargins left="0.4" right="0.4" top="0.7" bottom="0.7" header="0.3" footer="0.3"/>'
    + `<pageSetup orientation="${(b.orientation || 'portrait') === 'portrait' ? 'portrait' : 'landscape'}" fitToWidth="1" fitToHeight="0" paperSize="9"/>`
    + `<headerFooter><oddHeader>${hfEsc(oddHeader)}</oddHeader><oddFooter>${hfEsc(oddFooter)}</oddFooter></headerFooter>`
    + brkXml
    + (img ? '<drawing r:id="rId1"/>' : '')
    + '</worksheet>';

  const styles = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">`
    + `<fonts count="${fonts.length}">${fonts.join('')}</fonts>`
    + `<fills count="${fills.length}">${fills.join('')}</fills>`
    + `<borders count="${borders.length}">${borders.join('')}</borders>`
    + '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    + `<cellXfs count="${xfs.length}">`
    + xfs.map((x) => '<xf numFmtId="0" fontId="' + x.f + '" fillId="' + x.fl + '" borderId="' + x.b
      + '" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
      + `<alignment horizontal="${x.h || 'general'}" vertical="center"${x.ind ? ` indent="${x.ind}"` : ''}${x.wrap ? ' wrapText="1"' : ''}/></xf>`).join('')
    + '</cellXfs>'
    + '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    + '</styleSheet>';

  const wb = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">`
    + `<sheets><sheet name="${sheetName}" sheetId="1" r:id="rId1"/></sheets>`
    + `<definedNames><definedName name="_xlnm.Print_Titles" localSheetId="0">${sheetName}!$1:$${headRows}</definedName></definedNames>`
    + '</workbook>';

  const extra: { name: string; text?: string; data?: Uint8Array }[] = [];
  if (img && logoBox) {
    const cx = Math.round(logoBox.w * MM_TO_EMU);
    const cy = Math.round(logoBox.h * MM_TO_EMU);
    extra.push({ name: `xl/media/logo.${img.ext === 'jpeg' ? 'jpg' : img.ext}`, data: img.data });
    extra.push({
      name: 'xl/drawings/drawing1.xml',
      text: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        + '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"'
        + ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        + '<xdr:oneCellAnchor>'
        + '<xdr:from><xdr:col>0</xdr:col><xdr:colOff>38100</xdr:colOff>'
        + '<xdr:row>0</xdr:row><xdr:rowOff>19050</xdr:rowOff></xdr:from>'
        + `<xdr:ext cx="${cx}" cy="${cy}"/>`
        + '<xdr:pic><xdr:nvPicPr><xdr:cNvPr id="1" name="Logo" descr="Company logo"/>'
        + '<xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr></xdr:nvPicPr>'
        + '<xdr:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        + ' r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
        + `<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${cx}" cy="${cy}"/></a:xfrm>`
        + '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic>'
        + '<xdr:clientData/></xdr:oneCellAnchor></xdr:wsDr>',
    });
    extra.push({
      name: 'xl/drawings/_rels/drawing1.xml.rels',
      text: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/logo.${img.ext === 'jpeg' ? 'jpg' : img.ext}"/>`
        + '</Relationships>',
    });
    extra.push({
      name: 'xl/worksheets/_rels/sheet1.xml.rels',
      text: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>'
        + '</Relationships>',
    });
  }

  download(zipXlsx({ sheet, styles, wb }, extra, img ? (img.ext === 'jpeg' ? 'jpg' : img.ext) : ''),
    fileName(doc, 'xlsx'));
}

/* ── plumbing: a stored-entry zip, enough for OOXML ─────────────────────── */

function zipXlsx(
  parts: { sheet: string; styles: string; wb: string },
  extra: { name: string; text?: string; data?: Uint8Array }[] = [],
  imageExt = '',
): Blob {
  const hasDrawing = extra.some((e) => e.name === 'xl/drawings/drawing1.xml');
  const wbRels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    + '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    + '</Relationships>';
  const rootRels = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    + '</Relationships>';
  const types = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    + '<Default Extension="xml" ContentType="application/xml"/>'
    + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    + '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    + (imageExt ? `<Default Extension="${imageExt}" ContentType="image/${imageExt === 'jpg' ? 'jpeg' : imageExt}"/>` : '')
    + (hasDrawing ? '<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>' : '')
    + '</Types>';

  const enc = new TextEncoder();
  const files: { name: string; data: Uint8Array }[] = [
    { name: '[Content_Types].xml', data: enc.encode(types) },
    { name: '_rels/.rels', data: enc.encode(rootRels) },
    { name: 'xl/workbook.xml', data: enc.encode(parts.wb) },
    { name: 'xl/_rels/workbook.xml.rels', data: enc.encode(wbRels) },
    { name: 'xl/styles.xml', data: enc.encode(parts.styles) },
    { name: 'xl/worksheets/sheet1.xml', data: enc.encode(parts.sheet) },
    ...extra.map((e) => ({ name: e.name, data: e.data ?? enc.encode(e.text || '') })),
  ];

  const crcTable = (() => {
    const tb = new Uint32Array(256);
    for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; tb[n] = c >>> 0; }
    return tb;
  })();
  const crc32 = (d: Uint8Array) => {
    let c = 0xFFFFFFFF;
    for (let i = 0; i < d.length; i++) c = crcTable[(c ^ d[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  };
  const u16 = (v: number) => new Uint8Array([v & 255, (v >> 8) & 255]);
  const u32 = (v: number) => new Uint8Array([v & 255, (v >> 8) & 255, (v >> 16) & 255, (v >> 24) & 255]);
  const cat = (...a: Uint8Array[]) => {
    const n = a.reduce((x, y) => x + y.length, 0);
    const o = new Uint8Array(n); let p = 0;
    for (const x of a) { o.set(x, p); p += x.length; }
    return o;
  };

  const local: Uint8Array[] = []; const central: Uint8Array[] = []; let offset = 0;
  for (const f of files) {
    const nm = enc.encode(f.name); const crc = crc32(f.data);
    const rec = cat(u32(0x04034b50), u16(20), u16(0x0800), u16(0), u16(0), u16(0),
      u32(crc), u32(f.data.length), u32(f.data.length), u16(nm.length), u16(0), nm, f.data);
    local.push(rec);
    central.push(cat(u32(0x02014b50), u16(20), u16(20), u16(0x0800), u16(0), u16(0), u16(0),
      u32(crc), u32(f.data.length), u32(f.data.length), u16(nm.length), u16(0), u16(0), u16(0),
      u16(0), u32(0), u32(offset), nm));
    offset += rec.length;
  }
  const cd = cat(...central);
  const eocd = cat(u32(0x06054b50), u16(0), u16(0), u16(files.length), u16(files.length),
    u32(cd.length), u32(offset), u16(0));
  return new Blob([cat(...local, cd, eocd)],
    { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
}

function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
