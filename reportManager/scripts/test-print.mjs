// v3.4.0 — tests for src/utils/printSettings.ts
import assert from 'node:assert/strict';
import { EMPTY_PRINT, mergeBrand, toLayout } from '../src/utils/printSettings.ts';

const BASE = { orientation: 'portrait', paper: 'A4', titleSizePt: 14, bodySizePx: 10, gridLines: 'grid', pageNoPos: 'right',
  logoUrl: '/files/brand.png', companyName: 'Brand Co', vatNo: '111', headerText: '', footerText: 'kit footer',
  align: { logo: 'left', title: 'center', headerText: 'right', company: 'left', period: 'right', footerText: 'center', timestamp: 'right' } };

// "Default" choices never override the Brand Kit
assert.deepEqual(toLayout({ ...EMPTY_PRINT, align: {} }), {});
let b = mergeBrand(BASE, toLayout({ ...EMPTY_PRINT, align: { title: '', logo: '' } }), null);
assert.equal(b.orientation, 'portrait'); assert.equal(b.align.title, 'center'); assert.equal(b.logoUrl, '/files/brand.png');

// the report's own layout wins, element by element
b = mergeBrand(BASE, toLayout({ ...EMPTY_PRINT, orientation: 'landscape', bodySizePx: 8, gridLines: 'rows',
  pageNoPos: 'hide', align: { logo: 'right', period: 'hide' } }), null);
assert.equal(b.orientation, 'landscape'); assert.equal(b.bodySizePx, 8); assert.equal(b.gridLines, 'rows');
assert.equal(b.pageNoPos, 'hide'); assert.equal(b.align.logo, 'right'); assert.equal(b.align.period, 'hide');
assert.equal(b.align.title, 'center');                              // untouched elements keep the Brand Kit
assert.equal(BASE.align.logo, 'left');                              // the Brand Kit itself is not mutated

// the chosen ERPNext letterhead supplies identity, contact and footer
b = mergeBrand(BASE, {}, { logo_url: '/files/lh.png', company_name: 'شركة المسح الرقمي', tax_id: '300000000000003',
  address_lines: ['King Fahd Rd', 'Riyadh'], phone: '+966 11 000 0000', email: 'ar@x.sa',
  footer_html: '<p>CR 1010<br>www.x.sa</p><script>alert(1)</script>' });
assert.equal(b.logoUrl, '/files/lh.png'); assert.equal(b.companyName, 'شركة المسح الرقمي'); assert.equal(b.vatNo, '300000000000003');
assert.equal(b.headerText, 'King Fahd Rd · Riyadh · +966 11 000 0000 · ar@x.sa');
assert.equal(b.footerText, 'CR 1010 · www.x.sa');                  // plain text, scripts dropped
// a letterhead without a logo keeps the Brand Kit logo
assert.equal(mergeBrand(BASE, {}, { company_name: 'X' }).logoUrl, '/files/brand.png');

console.log('print: 4 groups passed');
