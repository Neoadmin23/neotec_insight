/* v2.47.0 — styled GL Excel: the workbook mirrors the on-screen view.
 * Hand-built SpreadsheetML (SheetJS community cannot write styles):
 * dark column-header band, account bands, opening/sub-total shading,
 * report-total framing, thin row rules, merged & CENTERED title block. */

function esc(s: any): string {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function colRef(i: number): string {
  let s = ''; i++;
  while (i > 0) { const m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = Math.floor((i - 1) / 26); }
  return s;
}

export interface GlXlsxRow { kind: 'title' | 'subtitle' | 'meta' | 'footer' | 'gap' | 'colhead' | 'acc' | 'op' | 'txn' | 'st' | 'rt'; cells: (string | number)[] }

export interface GlXlsxTheme { accentHex?: string; titlePt?: number; borderPreset?: 'minimal' | 'classic' | 'strong' }

/** v2.53.0 — the workbook carries a real header, not just two text rows:
 *  the letterhead block and the column row freeze on screen and repeat on
 *  every printed page, and Excel's own page header/footer carries the
 *  identity and page numbers. */
export interface GlXlsxPage {
  /** rows to freeze and repeat when printing (letterhead + column header) */
  headRows?: number;
  left?: string; center?: string; right?: string;   // Excel page header
  footLeft?: string; footRight?: string;            // Excel page footer
  landscape?: boolean;
}

export function writeGlStyledXlsx(rows: GlXlsxRow[], numCols: boolean[], fileName: string, theme: GlXlsxTheme = {}, page: GlXlsxPage = {}) {
  const accent = (theme.accentHex || '#16404d').replace('#', '').toUpperCase();
  const titlePt = Math.round((theme.titlePt || 15) * 1.0);
  const bp = theme.borderPreset || 'classic';
  const ruleHex = bp === 'minimal' ? 'FFEDEAE2' : bp === 'strong' ? 'FFC9C4B8' : 'FFDDDDD8';
  const strongHex = bp === 'strong' ? 'FF222222' : bp === 'minimal' ? 'FFB9B4A6' : 'FF333333';
  const strongStyle = bp === 'minimal' ? 'thin' : 'medium';
  const nCols = numCols.length;

  // ── styles ──
  const fonts = [
    '<font><sz val="10"/><color rgb="FF2C2C2A"/><name val="Calibri"/></font>',                       // 0 body
    `<font><b/><sz val="${titlePt}"/><color rgb="FF${accent}"/><name val="Calibri"/></font>`,          // 1 title
    '<font><sz val="11"/><color rgb="FF555555"/><name val="Calibri"/></font>',                        // 2 subtitle/meta
    '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>',                    // 3 colhead
    '<font><b/><sz val="10"/><color rgb="FF2C2C2A"/><name val="Calibri"/></font>',                    // 4 bold body
    '<font><sz val="9"/><color rgb="FF888888"/><name val="Calibri"/></font>',                         // 5 footer
  ];
  const fills = [
    '<fill><patternFill patternType="none"/></fill>',
    '<fill><patternFill patternType="gray125"/></fill>',
    `<fill><patternFill patternType="solid"><fgColor rgb="FF${accent}"/></patternFill></fill>`,        // 2 colhead
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF3F1EC"/></patternFill></fill>',          // 3 acc band
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFAF9F6"/></patternFill></fill>',          // 4 op/st
    '<fill><patternFill patternType="solid"><fgColor rgb="FFEDEFF7"/></patternFill></fill>',          // 5 report total
  ];
  // v2.55.0 — cells are boxed, not just under-ruled. Horizontal-only rules
  // left the workbook looking unruled once it was printed, which is what the
  // "Excel borders are not printing" report was actually about.
  const sideThin = `style="thin"><color rgb="${ruleHex}"/>`;
  const sideStrong = `style="${strongStyle}"><color rgb="${strongHex}"/>`;
  const borders = [
    '<border><left/><right/><top/><bottom/><diagonal/></border>',
    `<border><left ${sideThin}</left><right ${sideThin}</right><top ${sideThin}</top><bottom ${sideThin}</bottom><diagonal/></border>`,
    `<border><left ${sideStrong}</left><right ${sideStrong}</right><top ${sideStrong}</top><bottom ${sideStrong}</bottom><diagonal/></border>`,
    `<border><left ${sideStrong}</left><right ${sideStrong}</right><top ${sideStrong}</top><bottom ${sideStrong}</bottom><diagonal/></border>`,
  ];
  interface Xf { f: number; fl: number; b: number; h?: string; wrap?: boolean }
  const xfs: Xf[] = [{ f: 0, fl: 0, b: 0 }];
  const xfIdx = (d: Xf) => {
    const k = JSON.stringify(d);
    const i = xfs.findIndex((x) => JSON.stringify(x) === k);
    if (i >= 0) return i;
    xfs.push(d); return xfs.length - 1;
  };
  const S = {
    title: xfIdx({ f: 1, fl: 0, b: 0, h: 'center' }),
    sub: xfIdx({ f: 2, fl: 0, b: 0, h: 'center' }),
    meta: xfIdx({ f: 2, fl: 0, b: 0, h: 'center' }),
    footer: xfIdx({ f: 5, fl: 0, b: 0, h: 'center' }),
    colheadL: xfIdx({ f: 3, fl: 2, b: 2, h: 'left' }),
    colheadR: xfIdx({ f: 3, fl: 2, b: 2, h: 'right' }),
    acc: xfIdx({ f: 4, fl: 3, b: 1, h: 'left' }),
    opL: xfIdx({ f: 4, fl: 4, b: 1, h: 'left' }),
    opR: xfIdx({ f: 4, fl: 4, b: 1, h: 'right' }),
    txnL: xfIdx({ f: 0, fl: 0, b: 1, h: 'left' }),
    txnR: xfIdx({ f: 0, fl: 0, b: 1, h: 'right' }),
    rtL: xfIdx({ f: 4, fl: 5, b: 3, h: 'left' }),
    rtR: xfIdx({ f: 4, fl: 5, b: 3, h: 'right' }),
  };

  // ── sheet ──
  const merges: string[] = [];
  const lines: string[] = [];
  rows.forEach((r, ri) => {
    const rn = ri + 1;
    const cells: string[] = [];
    const push = (ci: number, v: string | number, s: number) => {
      const ref = colRef(ci) + rn;
      if (typeof v === 'number' && isFinite(v)) cells.push(`<c r="${ref}" s="${s}"><v>${v}</v></c>`);
      else if (v !== '' && v != null) cells.push(`<c r="${ref}" s="${s}" t="inlineStr"><is><t xml:space="preserve">${esc(v)}</t></is></c>`);
      else cells.push(`<c r="${ref}" s="${s}"/>`);
    };
    if (r.kind === 'title' || r.kind === 'subtitle' || r.kind === 'meta' || r.kind === 'footer') {
      const s = r.kind === 'title' ? S.title : r.kind === 'footer' ? S.footer : S.sub;
      push(0, r.cells[0] ?? '', s);
      for (let c = 1; c < nCols; c++) push(c, '', s);
      merges.push(`${colRef(0)}${rn}:${colRef(nCols - 1)}${rn}`);
    } else if (r.kind === 'gap') {
      // empty spacer row
    } else if (r.kind === 'colhead') {
      r.cells.forEach((v, c) => push(c, v, numCols[c] ? S.colheadR : S.colheadL));
    } else if (r.kind === 'acc') {
      push(0, r.cells[0] ?? '', S.acc);
      for (let c = 1; c < nCols; c++) push(c, '', S.acc);
      merges.push(`${colRef(0)}${rn}:${colRef(nCols - 1)}${rn}`);
    } else if (r.kind === 'op' || r.kind === 'st') {
      r.cells.forEach((v, c) => push(c, v, numCols[c] ? S.opR : S.opL));
    } else if (r.kind === 'rt') {
      r.cells.forEach((v, c) => push(c, v, numCols[c] ? S.rtR : S.rtL));
    } else {
      r.cells.forEach((v, c) => push(c, v, numCols[c] ? S.txnR : S.txnL));
    }
    const ht = r.kind === 'title' ? ' ht="22" customHeight="1"' : r.kind === 'colhead' || r.kind === 'acc' ? ' ht="18" customHeight="1"' : '';
    lines.push(`<row r="${rn}"${ht}>${cells.join('')}</row>`);
  });

  const colsXml = numCols.map((n, i) =>
    `<col min="${i + 1}" max="${i + 1}" width="${i === 0 ? 34 : n ? 14 : 20}" customWidth="1"/>`).join('');

  // Header/footer codes are their own mini-language: a literal ampersand has
  // to be doubled or Excel reads it as a control code and drops the rest.
  // Two layers: a literal ampersand is doubled so Excel doesn't read it as a
  // control code, then the whole composed string is XML-escaped — including
  // the &L/&C/&R codes themselves, which Excel expects as &amp;L in the part.
  const hf = (v?: string) => (v || '').replace(/&/g, '&&');
  const hfXmlEsc = (v: string) => v.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const headRows = Math.max(0, page.headRows || 0);
  const freeze = headRows > 0
    ? `<sheetViews><sheetView workbookViewId="0"><pane ySplit="${headRows}" topLeftCell="A${headRows + 1}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>`
    : '';
  const oddHeader = [page.left ? '&L' + hf(page.left) : '', page.center ? '&C' + hf(page.center) : '', page.right ? '&R' + hf(page.right) : ''].join('');
  const oddFooter = [page.footLeft ? '&L' + hf(page.footLeft) : '', '&R' + (page.footRight ? hf(page.footRight) + '   ' : '') + 'Page &P of &N'].join('');
  const hfXml = (oddHeader || oddFooter)
    ? `<headerFooter><oddHeader>${hfXmlEsc(oddHeader)}</oddHeader><oddFooter>${hfXmlEsc(oddFooter)}</oddFooter></headerFooter>`
    : '';

  const sheet = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">${freeze}<cols>${colsXml}</cols><sheetData>${lines.join('')}</sheetData>${merges.length ? `<mergeCells count="${merges.length}">${merges.map((m) => `<mergeCell ref="${m}"/>`).join('')}</mergeCells>` : ''}<printOptions gridLines="1" horizontalCentered="0"/><pageMargins left="0.4" right="0.4" top="0.6" bottom="0.6" header="0.3" footer="0.3"/><pageSetup orientation="${page.landscape === false ? 'portrait' : 'landscape'}" fitToWidth="1" fitToHeight="0" paperSize="9"/>${hfXml}</worksheet>`;

  const styles = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="${fonts.length}">${fonts.join('')}</fonts><fills count="${fills.length}">${fills.join('')}</fills><borders count="${borders.length}">${borders.join('')}</borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="${xfs.length}">${xfs.map((x) => `<xf numFmtId="${0}" fontId="${x.f}" fillId="${x.fl}" borderId="${x.b}" applyFont="1" applyFill="1" applyBorder="1"${x.h ? ` applyAlignment="1"><alignment horizontal="${x.h}" vertical="center"${x.wrap ? ' wrapText="1"' : ''}/></xf>` : '/>'}`).join('')}</cellXfs></styleSheet>`;

  const wb = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="General Ledger" sheetId="1" r:id="rId1"/></sheets>${headRows > 0 ? `<definedNames><definedName name="_xlnm.Print_Titles" localSheetId="0">'General Ledger'!$1:$${headRows}</definedName></definedNames>` : ''}</workbook>`;
  const wbRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`;
  const rootRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`;
  const types = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>`;

  // minimal zip (store, no compression)
  const enc = new TextEncoder();
  const files: { name: string; data: Uint8Array }[] = [
    { name: '[Content_Types].xml', data: enc.encode(types) },
    { name: '_rels/.rels', data: enc.encode(rootRels) },
    { name: 'xl/workbook.xml', data: enc.encode(wb) },
    { name: 'xl/_rels/workbook.xml.rels', data: enc.encode(wbRels) },
    { name: 'xl/styles.xml', data: enc.encode(styles) },
    { name: 'xl/worksheets/sheet1.xml', data: enc.encode(sheet) },
  ];
  const crcTable = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
  const crc32 = (d: Uint8Array) => { let c = 0xFFFFFFFF; for (let i = 0; i < d.length; i++) c = crcTable[(c ^ d[i]) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
  const parts: Uint8Array[] = []; const central: Uint8Array[] = []; let offset = 0;
  const u16 = (v: number) => new Uint8Array([v & 255, (v >> 8) & 255]);
  const u32 = (v: number) => new Uint8Array([v & 255, (v >> 8) & 255, (v >> 16) & 255, (v >> 24) & 255]);
  const cat = (...a: Uint8Array[]) => { const n = a.reduce((x, y) => x + y.length, 0); const o = new Uint8Array(n); let p = 0; for (const x of a) { o.set(x, p); p += x.length; } return o; };
  for (const f of files) {
    const nm = enc.encode(f.name); const crc = crc32(f.data);
    const local = cat(u32(0x04034b50), u16(20), u16(0x0800), u16(0), u16(0), u16(0), u32(crc), u32(f.data.length), u32(f.data.length), u16(nm.length), u16(0), nm, f.data);
    parts.push(local);
    central.push(cat(u32(0x02014b50), u16(20), u16(20), u16(0x0800), u16(0), u16(0), u16(0), u32(crc), u32(f.data.length), u32(f.data.length), u16(nm.length), u16(0), u16(0), u16(0), u16(0), u32(0), u32(offset), nm));
    offset += local.length;
  }
  const cd = cat(...central);
  const eocd = cat(u32(0x06054b50), u16(0), u16(0), u16(files.length), u16(files.length), u32(cd.length), u32(offset), u16(0));
  const blob = new Blob([cat(...parts, cd, eocd)], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = fileName; a.click();
  URL.revokeObjectURL(a.href);
}
