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

// v3.4.6 — header & footer: letterhead as designed + custom images
import { frameParts, imagesRow } from '../src/utils/printSettings.ts';
const LH = { header_html: '<img src="/files/irsaa-head.png">', footer_html: '<img src="/files/irsaa-foot.png">' };
const S = (o) => ({ ...EMPTY_PRINT, align: {}, headerImages: [], footerImages: [], ...o });
let f = frameParts(S({ letterhead: 'IRSAA' }), LH);                       // the reported case
assert.ok(f.headerHtml.includes('irsaa-head.png') && f.footerHtml.includes('irsaa-foot.png'));
assert.equal(f.hideIdentity, true);                                        // band's logo/address not repeated
f = frameParts(S({ letterhead: 'IRSAA', lhStyle: 'band' }), LH);          // previous behaviour on request
assert.deepEqual([f.headerHtml, f.footerHtml, f.hideIdentity], ['', '', false]);
const row = (n) => imagesRow(['/a.png', '/b.png', '/c.png'].slice(0, n), 20);
assert.ok(/text-align:left/.test(row(1)) && !/right/.test(row(1)));
assert.ok(/text-align:left[\s\S]*text-align:right/.test(row(2)));
assert.ok(/text-align:left[\s\S]*text-align:center[\s\S]*text-align:right/.test(row(3)));
assert.equal(imagesRow(['/a', '/b', '/c', '/d'], 20).match(/<img/g).length, 3);   // never more than 3
f = frameParts(S({ headerImages: ['/x.png'], footerImages: ['/y.png', '/z.png'], headerRule: true, footerRule: true,
  footerLines: 'CR 1010232911\n<script>x</script>' }), null);
assert.ok(f.headerHtml.includes('/x.png') && f.headerHtml.includes('border-top'));
assert.ok(f.footerHtml.startsWith('<div style="border-top') && f.footerHtml.includes('/z.png'));
assert.ok(f.footerHtml.includes('CR 1010232911') && !f.footerHtml.includes('<script>'));   // text escaped
assert.equal(f.hideIdentity, false);                                       // custom images keep Insight's band
assert.deepEqual(frameParts(S({}), null), { headerHtml: '', footerHtml: '', hideIdentity: false });
console.log('header & footer: passed');

// v3.4.7 — single-image letterhead fits the printable width
import { fitWidth } from '../src/utils/printSettings.ts';
const one = '<div style="text-align:center"><img src="/files/irsaa-head.png" style="width:120mm;height:30mm"></div>';
let w = fitWidth(one);
assert.ok(w.includes('src="/files/irsaa-head.png"') && w.includes('width:100%;height:auto'));
assert.ok(!w.includes('120mm'));                                            // the fixed size is gone
assert.ok(fitWidth("<img src='/files/f.png'>").includes('src="/files/f.png"'));   // single quotes
const two = '<img src="/a.png"><img src="/b.png">';
assert.equal(fitWidth(two), two);                                          // several images: as designed
assert.equal(fitWidth('<p>Text only</p>'), '<p>Text only</p>');
f = frameParts(S({ letterhead: 'IRSAA' }), { header_html: one, footer_html: '<img src="/files/foot.png">' });
assert.ok(f.headerHtml.includes('width:100%') && f.footerHtml.includes('width:100%'));
f = frameParts(S({ letterhead: 'IRSAA', lhImageSize: 'natural' }), { header_html: one, footer_html: '' });
assert.ok(f.headerHtml.includes('120mm'));                                  // "As designed" keeps it
console.log('letterhead fit: passed');
