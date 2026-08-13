// Theme engine — v2.23.0
//
// The whole UI is driven by the CSS design tokens declared on :root in
// index.css, so a "theme" is nothing more than a set of token overrides
// applied to document.documentElement. Two sources of themes:
//   1. A curated default pack (below).
//   2. A palette GENERATED from the company logo: we sample the logo pixels,
//      quantize to hue buckets, score by (saturation × frequency), pick the
//      dominant brand colour + a secondary, and derive the full accent +
//      semantic-tint token set from them. Neutral surfaces get a barely
//      perceptible tint of the brand hue so the whole app feels "of the brand"
//      without hurting readability.
//
// Persistence: localStorage (per browser/user). This is the app's own SPA —
// key `ni-theme` stores either { kind:'preset', key } or
// { kind:'logo', company, vars } so a generated palette survives reloads
// without refetching/reprocessing the logo.

export type ThemeVars = Record<string, string>;
export interface Theme { key: string; name: string; nameAr?: string; vars: ThemeVars; dark?: boolean }

const LS_KEY = 'ni-theme';

// ── Default pack ────────────────────────────────────────────────────────────
export const THEMES: Theme[] = [
  {
    key: 'classic', name: 'Insight Classic', nameAr: 'الكلاسيكي',
    vars: {}, // the :root defaults — applying resets every override
  },
  {
    key: 'desert', name: 'Desert Gold', nameAr: 'ذهبي صحراوي',
    vars: {
      '--bg': '#faf6ee', '--surface': '#fffdf8', '--surface-2': '#f4ecdb', '--surface-3': '#ecdfc6',
      '--info': '#9a6b1f', '--info-bg': '#f7ecd6', '--info-border': '#e3c894', '--info-text': '#4d3407',
      '--derived-bg': 'rgba(154, 107, 31, 0.04)',
    },
  },
  {
    key: 'emerald', name: 'Emerald Riyadh', nameAr: 'زمردي',
    vars: {
      '--bg': '#f3f7f4', '--surface': '#ffffff', '--surface-2': '#e9f1ea', '--surface-3': '#dde9df',
      '--info': '#0e6b46', '--info-bg': '#e2f2e9', '--info-border': '#abd8bf', '--info-text': '#053722',
      '--derived-bg': 'rgba(14, 107, 70, 0.04)',
    },
  },
  {
    // v2.50.0 — built from a greyscale extraction: white ground, four greys,
    // one near-black. No hue in the source, so none is invented.
    key: 'mono', name: 'Monochrome', nameAr: 'أحادي اللون',
    vars: {
      '--bg': '#f6f6f6', '--surface': '#ffffff', '--surface-2': '#ececec', '--surface-3': '#dcdcdc',
      '--info': '#3f3f3f', '--info-bg': '#ececec', '--info-border': '#bababa', '--info-text': '#080808',
      '--derived-bg': 'rgba(63, 63, 63, 0.04)',
    },
  },
  {
    key: 'royal', name: 'Royal Violet', nameAr: 'بنفسجي ملكي',
    vars: {
      '--bg': '#f6f4fa', '--surface': '#ffffff', '--surface-2': '#eeeaf6', '--surface-3': '#e4ddf0',
      '--info': '#5b3fa8', '--info-bg': '#ece5f9', '--info-border': '#c8b7ea', '--info-text': '#2a1a56',
      '--derived-bg': 'rgba(91, 63, 168, 0.04)',
    },
  },
  {
    key: 'ocean', name: 'Ocean Blue', nameAr: 'أزرق محيطي',
    vars: {
      '--bg': '#f2f6f9', '--surface': '#ffffff', '--surface-2': '#e7eff5', '--surface-3': '#dbe7f0',
      '--info': '#0f5e8f', '--info-bg': '#e1eff8', '--info-border': '#a9d0e8', '--info-text': '#063149',
      '--derived-bg': 'rgba(15, 94, 143, 0.04)',
    },
  },
  {
    key: 'midnight', name: 'Midnight', nameAr: 'منتصف الليل', dark: true,
    vars: {
      '--bg': '#15161a', '--surface': '#1e2026', '--surface-2': '#262932', '--surface-3': '#2e323d',
      '--border': 'rgba(255,255,255,0.10)', '--border-strong': 'rgba(255,255,255,0.20)',
      '--text': '#eceef2', '--text-muted': '#a7abb5', '--text-faint': '#767b87',
      '--info': '#6ea8dc', '--info-bg': 'rgba(110,168,220,0.12)', '--info-border': 'rgba(110,168,220,0.35)', '--info-text': '#cfe3f5',
      '--pos': '#3ec98f', '--pos-bg': 'rgba(62,201,143,0.10)',
      '--neg': '#e0716b', '--neg-bg': 'rgba(224,113,107,0.10)',
      '--warn': '#d9a44a', '--warn-bg': 'rgba(217,164,74,0.10)',
      '--budget-bg': 'rgba(217,164,74,0.06)', '--prior-bg': 'rgba(62,201,143,0.05)', '--derived-bg': 'rgba(110,168,220,0.06)',
      '--elevation-popover': '0 2px 10px rgba(0,0,0,0.45), 0 1px 2px rgba(0,0,0,0.35)',
      '--elevation-modal': '0 8px 32px rgba(0,0,0,0.55), 0 2px 6px rgba(0,0,0,0.40)',
    },
  },
  {
    key: 'rose', name: 'Rose Quartz', nameAr: 'وردي',
    vars: {
      '--bg': '#faf4f5', '--surface': '#fffcfd', '--surface-2': '#f5e7ea', '--surface-3': '#eddade',
      '--info': '#a53e5c', '--info-bg': '#f8e4ea', '--info-border': '#e6b3c2', '--info-text': '#521626',
      '--derived-bg': 'rgba(165, 62, 92, 0.04)',
    },
  },
  {
    key: 'graphite', name: 'Graphite Steel', nameAr: 'رمادي فولاذي',
    vars: {
      '--bg': '#f3f4f6', '--surface': '#fdfdfe', '--surface-2': '#e8eaee', '--surface-3': '#dcdfe5',
      '--info': '#41526b', '--info-bg': '#e6eaf1', '--info-border': '#b7c2d3', '--info-text': '#1c2735',
      '--derived-bg': 'rgba(65, 82, 107, 0.04)',
    },
  },
  {
    key: 'sandstone', name: 'Sandstone Coffee', nameAr: 'رملي',
    vars: {
      '--bg': '#f8f4ef', '--surface': '#fefcf9', '--surface-2': '#efe6da', '--surface-3': '#e4d7c6',
      '--info': '#7c563a', '--info-bg': '#f2e7dc', '--info-border': '#d8bfa6', '--info-text': '#3e2a1a',
      '--derived-bg': 'rgba(124, 86, 58, 0.04)',
    },
  },
  {
    key: 'oasis', name: 'Teal Oasis', nameAr: 'فيروزي',
    vars: {
      '--bg': '#f0f7f7', '--surface': '#fbfefe', '--surface-2': '#e2efef', '--surface-3': '#d3e6e6',
      '--info': '#0f7377', '--info-bg': '#ddf1f2', '--info-border': '#a3d6d8', '--info-text': '#053c3f',
      '--derived-bg': 'rgba(15, 115, 119, 0.04)',
    },
  },
  {
    key: 'burgundy', name: 'Burgundy Reserve', nameAr: 'خمري',
    vars: {
      '--bg': '#f9f3f3', '--surface': '#fefbfb', '--surface-2': '#f2e3e3', '--surface-3': '#e9d3d3',
      '--info': '#82333c', '--info-bg': '#f5e2e4', '--info-border': '#dcaab0', '--info-text': '#41141a',
      '--derived-bg': 'rgba(130, 51, 60, 0.04)',
    },
  },
  {
    key: 'indigo_night', name: 'Indigo Night', nameAr: 'نيلي داكن', dark: true,
    vars: {
      '--bg': '#14151f', '--surface': '#1c1e2c', '--surface-2': '#242739', '--surface-3': '#2d3147',
      '--border': 'rgba(255,255,255,0.10)', '--border-strong': 'rgba(255,255,255,0.20)',
      '--text': '#ebecf5', '--text-muted': '#a4a8c0', '--text-faint': '#717694',
      '--info': '#8f95e8', '--info-bg': 'rgba(143,149,232,0.13)', '--info-border': 'rgba(143,149,232,0.38)', '--info-text': '#d7daf8',
      '--pos': '#41c99a', '--pos-bg': 'rgba(65,201,154,0.10)',
      '--neg': '#e07a86', '--neg-bg': 'rgba(224,122,134,0.10)',
      '--warn': '#d9b055', '--warn-bg': 'rgba(217,176,85,0.10)',
      '--budget-bg': 'rgba(217,176,85,0.06)', '--prior-bg': 'rgba(65,201,154,0.05)', '--derived-bg': 'rgba(143,149,232,0.07)',
      '--elevation-popover': '0 2px 10px rgba(0,0,0,0.5), 0 1px 2px rgba(0,0,0,0.4)',
      '--elevation-modal': '0 8px 32px rgba(0,0,0,0.6), 0 2px 6px rgba(0,0,0,0.45)',
    },
  },
];

// Every token any theme may set — used to reset before applying the next one.
const ALL_TOKENS = Array.from(new Set(THEMES.flatMap((t) => Object.keys(t.vars)).concat([
  '--bg', '--surface', '--surface-2', '--surface-3', '--border', '--border-strong',
  '--text', '--text-muted', '--text-faint',
  '--info', '--info-bg', '--info-border', '--info-text',
  '--pos', '--pos-bg', '--neg', '--neg-bg', '--warn', '--warn-bg',
  '--budget-bg', '--prior-bg', '--derived-bg',
])));

export type SavedTheme = { kind: 'preset'; key: string } | { kind: 'logo'; company: string; vars: ThemeVars }
  | { kind: 'palette'; src: string; vars: ThemeVars };

export function applyVars(vars: ThemeVars) {
  const el = document.documentElement;
  for (const tok of ALL_TOKENS) el.style.removeProperty(tok);
  for (const [k, v] of Object.entries(vars)) el.style.setProperty(k, v);
}

export function applySaved(saved: SavedTheme | null) {
  if (!saved) return;
  if (saved.kind === 'preset') {
    const t = THEMES.find((x) => x.key === saved.key);
    if (t) applyVars(t.vars);
  } else if ((saved.kind === 'logo' || saved.kind === 'palette') && saved.vars) {
    applyVars(saved.vars);
  }
}

export function persist(saved: SavedTheme) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(saved)); } catch { /* private mode */ }
}

export function loadSaved(): SavedTheme | null {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? (JSON.parse(raw) as SavedTheme) : null;
  } catch { return null; }
}

export function initTheme() { applySaved(loadSaved()); }

// ── Logo → palette ──────────────────────────────────────────────────────────

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h = 0;
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0));
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return [h * 60, s, l];
}

function hsl(h: number, s: number, l: number, a?: number): string {
  const core = `${Math.round(h)}, ${Math.round(s * 100)}%, ${Math.round(l * 100)}%`;
  return a == null ? `hsl(${core})` : `hsla(${core}, ${a})`;
}

/** Extract the dominant brand hues from an image URL (same-origin /files/…). */
export async function extractLogoColors(url: string): Promise<Array<{ h: number; s: number; l: number; weight: number }>> {
  const img = await new Promise<HTMLImageElement>((res, rej) => {
    const i = new Image();
    i.crossOrigin = 'anonymous';
    i.onload = () => res(i);
    i.onerror = () => rej(new Error('Could not load the logo image.'));
    i.src = url;
  });
  const size = 64; // downsample — hue statistics don't need resolution
  const canvas = document.createElement('canvas');
  canvas.width = size; canvas.height = size;
  const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
  ctx.drawImage(img, 0, 0, size, size);
  const { data } = ctx.getImageData(0, 0, size, size);

  // Quantize into 24 hue buckets; ignore transparent, near-white, near-black
  // and near-grey pixels — those are background/outline, not brand colour.
  const buckets = new Map<number, { w: number; s: number; l: number; n: number }>();
  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3];
    if (a < 128) continue;
    const [h, s, l] = rgbToHsl(data[i], data[i + 1], data[i + 2]);
    if (l > 0.93 || l < 0.07 || s < 0.15) continue;
    const b = Math.floor(h / 15) % 24;
    const w = s * (1 - Math.abs(l - 0.5)); // saturated, mid-lightness pixels dominate
    const cur = buckets.get(b) || { w: 0, s: 0, l: 0, n: 0 };
    cur.w += w; cur.s += s; cur.l += l; cur.n += 1;
    buckets.set(b, cur);
  }
  const ranked = [...buckets.entries()]
    .map(([b, v]) => ({ h: b * 15 + 7.5, s: v.s / v.n, l: v.l / v.n, weight: v.w }))
    .sort((a, b2) => b2.weight - a.weight);
  if (!ranked.length) throw new Error('The logo has no usable brand colour (monochrome image).');
  // Secondary = strongest hue at least 60° away from the primary.
  const primary = ranked[0];
  const secondary = ranked.find((c) => {
    const d = Math.abs(c.h - primary.h);
    return Math.min(d, 360 - d) >= 60;
  });
  return secondary ? [primary, secondary] : [primary];
}

/** Build the full token set from extracted brand colours. */
export function paletteFromColors(colors: Array<{ h: number; s: number; l: number }>): ThemeVars {
  const p = colors[0];
  // A source with no meaningful chroma — a greyscale logo or a monochrome
  // palette — used to be dragged up to s=0.35 on hue 0 and came out pink.
  // Honour the absence of colour instead and derive a neutral set.
  if (p.s < 0.08) {
    const ink = Math.min(Math.max(p.l, 0.10), 0.30);
    const g = (l: number) => hsl(0, 0, l);
    return {
      '--bg': g(0.965), '--surface': g(1), '--surface-2': g(0.925), '--surface-3': g(0.865),
      '--info': g(ink), '--info-bg': g(0.925), '--info-border': g(0.73), '--info-text': g(0.05),
      '--derived-bg': `rgba(0, 0, 0, 0.04)`,
    };
  }
  // Clamp the accent into a readable range: enough saturation to feel branded,
  // low enough lightness to pass contrast on white surfaces.
  const s = Math.min(Math.max(p.s, 0.35), 0.75);
  const accentL = Math.min(Math.max(p.l, 0.28), 0.42);
  return {
    // Neutral surfaces with a whisper of the brand hue
    '--bg': hsl(p.h, 0.14, 0.965),
    '--surface': hsl(p.h, 0.10, 0.995),
    '--surface-2': hsl(p.h, 0.14, 0.93),
    '--surface-3': hsl(p.h, 0.15, 0.895),
    // Accent family
    '--info': hsl(p.h, s, accentL),
    '--info-bg': hsl(p.h, Math.min(s, 0.55), 0.94),
    '--info-border': hsl(p.h, Math.min(s, 0.5), 0.78),
    '--info-text': hsl(p.h, s, 0.16),
    '--derived-bg': hsl(p.h, s, accentL, 0.04),
  };
}

export async function themeFromLogo(url: string): Promise<ThemeVars> {
  const colors = await extractLogoColors(url);
  return paletteFromColors(colors);
}

// ── Pasted palette → theme ──────────────────────────────────────────────────

export interface PaletteEntry { hex: string; h: number; s: number; l: number; weight: number }

function parseHex(h: string): [number, number, number] | null {
  const m = String(h || '').trim().replace(/^#/, '');
  if (/^[0-9a-f]{3}$/i.test(m)) return [parseInt(m[0] + m[0], 16), parseInt(m[1] + m[1], 16), parseInt(m[2] + m[2], 16)];
  if (/^[0-9a-f]{6}$/i.test(m)) return [parseInt(m.slice(0, 2), 16), parseInt(m.slice(2, 4), 16), parseInt(m.slice(4, 6), 16)];
  return null;
}

/** Accepts whatever a colour-extraction tool produces: one entry per line, hex
 *  first and an optional coverage number after it in any separator —
 *  `#FFFFFF 86.91`, `#fff,86.91%`, `#DCDCDC\t0.09`, or a bare `#3F3F3F`.
 *  Header rows and stray text are ignored. */
export function parsePalette(text: string): PaletteEntry[] {
  const out: PaletteEntry[] = [];
  for (const raw of String(text || '').split(/[\r\n]+/)) {
    const line = raw.trim();
    if (!line) continue;
    const hm = line.match(/#?\b([0-9a-f]{6}|[0-9a-f]{3})\b/i);
    if (!hm) continue;
    const rgb = parseHex(hm[1]); if (!rgb) continue;
    const [h, s, l] = rgbToHsl(rgb[0], rgb[1], rgb[2]);
    const rest = line.slice((hm.index || 0) + hm[0].length);
    const pm = rest.match(/(\d+(?:\.\d+)?)/);
    out.push({ hex: '#' + rgb.map((v) => v.toString(16).padStart(2, '0')).join('').toUpperCase(),
               h, s, l, weight: pm ? parseFloat(pm[1]) : 1 });
  }
  return out;
}

/** Build a theme from the whole palette rather than from one lead colour.
 *  Entries are placed by lightness — the darkest becomes text, mid tones become
 *  borders, light tones become surfaces — so a six-grey extraction produces six
 *  greys on screen instead of a synthesised near-default. Chroma still leads the
 *  accent when the source has any. */
export function varsFromPaletteEntries(entries: PaletteEntry[]): ThemeVars {
  const byL = [...entries].sort((a, b) => a.l - b.l);
  const near = (target: number) =>
    byL.reduce((best, e) => (Math.abs(e.l - target) < Math.abs(best.l - target) ? e : best), byL[0]);

  const darkest = byL[0];
  const lightest = byL[byL.length - 1];
  const chroma = [...entries].sort((a, b) =>
    (b.s * Math.max(b.weight, 0.01)) - (a.s * Math.max(a.weight, 0.01)))[0];
  const hasHue = chroma.s >= 0.08;

  const accent = hasHue ? chroma : near(0.25);
  const accentL = Math.min(Math.max(accent.l, 0.18), 0.45);
  const surface = lightest.l >= 0.93 ? lightest : { ...lightest, l: 1, s: 0, h: accent.h };

  const col = (e: { h: number; s: number; l: number }, l?: number) => hsl(e.h, e.s, l == null ? e.l : l);

  return {
    '--bg': col(surface, Math.max(0, Math.min(surface.l, 0.985) - 0.025)),
    '--surface': col(surface, Math.min(surface.l, 1)),
    '--surface-2': col(near(0.87)),
    '--surface-3': col(near(0.75)),
    '--info': col(accent, accentL),
    '--info-bg': col(near(0.9)),
    '--info-border': col(near(0.72)),
    '--info-text': col(darkest, Math.min(darkest.l, 0.22)),
    '--text': col(darkest, Math.min(darkest.l, 0.16)),
    '--derived-bg': hasHue
      ? `hsla(${Math.round(accent.h)}, ${Math.round(Math.min(accent.s, 0.75) * 100)}%, ${Math.round(accentL * 100)}%, 0.04)`
      : 'rgba(0, 0, 0, 0.04)',
  };
}

/** Turn pasted text into a theme. Three or more colours are used in full;
 *  a shorter list falls back to deriving everything from the lead colour. */
export function themeFromPalette(text: string): ThemeVars {
  const entries = parsePalette(text);
  if (!entries.length) throw new Error('no colours found');
  if (entries.length >= 3) return varsFromPaletteEntries(entries);
  const scored = [...entries].sort((a, b) =>
    (b.s * Math.max(b.weight, 0.01)) - (a.s * Math.max(a.weight, 0.01)));
  const lead = scored[0].s >= 0.08
    ? scored[0]
    : [...entries].sort((a, b) => a.l - b.l).find((e) => e.l > 0.04) || entries[0];
  return paletteFromColors([{ h: lead.h, s: lead.s, l: lead.l }]);
}

// ── Print tokens ────────────────────────────────────────────────────────────
//
// The print documents used to carry their own hexes, so a theme change stopped
// at the edge of the screen. These map either the live app theme or the Brand
// Kit's own accent onto one set of roles, emitted as CSS variables that every
// print path references.

export interface PrintTokens {
  accent: string; ink: string; muted: string; rule: string; strong: string;
  paper: string; headBg: string; headInk: string; groupBg: string; subBg: string; totalBg: string;
}

const PRINT_FALLBACK: PrintTokens = {
  accent: '#16404d', ink: '#1a1a1a', muted: '#666666', rule: '#DDDDD8', strong: '#333333',
  paper: '#ffffff', headBg: '#ffffff', headInk: '#1a1a1a',
  groupBg: '#f3f1ec', subBg: '#faf9f6', totalBg: '#eeeeff',
};

/** Read the tokens currently painted on screen — whatever theme is active,
 *  preset, logo-generated or pasted — so the print matches the display. */
export function printTokensFromApp(): PrintTokens {
  try {
    const cs = getComputedStyle(document.documentElement);
    const v = (name: string, fb: string) => (cs.getPropertyValue(name) || '').trim() || fb;
    return {
      accent: v('--info', PRINT_FALLBACK.accent),
      ink: v('--text', PRINT_FALLBACK.ink),
      muted: v('--text-muted', PRINT_FALLBACK.muted),
      rule: v('--surface-3', PRINT_FALLBACK.rule),
      strong: v('--text', PRINT_FALLBACK.strong),
      // Paper stays white: tinting a whole printed page burns toner and reads
      // as a background fill rather than as a theme.
      paper: '#ffffff',
      headBg: v('--surface-2', PRINT_FALLBACK.headBg),
      headInk: v('--text', PRINT_FALLBACK.headInk),
      groupBg: v('--surface-2', PRINT_FALLBACK.groupBg),
      subBg: v('--surface', PRINT_FALLBACK.subBg),
      totalBg: v('--info-bg', PRINT_FALLBACK.totalBg),
    };
  } catch { return { ...PRINT_FALLBACK }; }
}

/** Derive print tokens from the Brand Kit's own accent + border preset. */
export function printTokensFromBrand(brand: any): PrintTokens {
  const accent = (brand?.accent || PRINT_FALLBACK.accent).trim();
  const preset = brand?.borderPreset || 'classic';
  return {
    ...PRINT_FALLBACK,
    accent,
    rule: preset === 'minimal' ? '#EDEAE2' : preset === 'strong' ? '#C9C4B8' : '#DDDDD8',
    strong: preset === 'minimal' ? '#B9B4A6' : preset === 'strong' ? '#222222' : '#333333',
  };
}

/** `printColors: 'theme'` follows what is on screen; anything else keeps the
 *  Brand Kit's configured colours. */
export function resolvePrintTokens(brand: any): PrintTokens {
  return brand?.printColors === 'theme' ? printTokensFromApp() : printTokensFromBrand(brand);
}

export function printVarsCss(tk: PrintTokens): string {
  return `:root{--th-accent:${tk.accent};--th-ink:${tk.ink};--th-muted:${tk.muted};--th-rule:${tk.rule};`
    + `--th-strong:${tk.strong};--th-paper:${tk.paper};--th-head-bg:${tk.headBg};--th-head-ink:${tk.headInk};`
    + `--th-group:${tk.groupBg};--th-sub:${tk.subBg};--th-total:${tk.totalBg};}`;
}
