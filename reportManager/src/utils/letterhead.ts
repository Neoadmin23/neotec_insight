import { api } from './api';

/* ─── Letter Head helpers (v1.9.53) ───────────────────────────────────────
 *
 * Shared between export paths (CSV, Excel, PDF, Print). Fetches the
 * resolved Letter Head payload from the backend; callers use header_html
 * for HTML-renderable formats and the structured fields for Excel/CSV.
 */

export interface LetterheadPayload {
  name: string;
  label: string;
  header_html: string;
  footer_html: string;
  logo_url: string;
  company_name: string;
  address_lines: string[];
  phone: string;
  email: string;
  website: string;
  tax_id: string;
  company_logo: string;
}

export const EMPTY_LETTERHEAD: LetterheadPayload = {
  name: '',
  label: '',
  header_html: '',
  footer_html: '',
  logo_url: '',
  company_name: '',
  address_lines: [],
  phone: '',
  email: '',
  website: '',
  tax_id: '',
  company_logo: '',
};

/** Fetch the resolved letterhead, or return empty when name is empty. The
 *  empty case is the "Without letterhead" path — we still fetch the company
 *  contact fields so Excel/CSV can show "Company Name" at the top even
 *  without a full Letter Head. */
export async function fetchLetterhead(name: string, company: string | undefined): Promise<LetterheadPayload> {
  try {
    const lh = await api.getLetterhead(name || '', company);
    return {
      name: lh.name || '',
      label: lh.label || '',
      header_html: lh.header_html || '',
      footer_html: lh.footer_html || '',
      logo_url: lh.logo_url || '',
      company_name: lh.company_name || '',
      address_lines: Array.isArray(lh.address_lines) ? lh.address_lines : [],
      phone: lh.phone || '',
      email: lh.email || '',
      website: lh.website || '',
      tax_id: lh.tax_id || '',
      company_logo: lh.company_logo || '',
    };
  } catch {
    // Failure is non-fatal: continue the export without letterhead. The
    // user gets data even if the corporate identity bit fails.
    return { ...EMPTY_LETTERHEAD };
  }
}

/** Fetch an image URL as a base64 data URL — used by Excel embedding,
 *  which needs the actual bytes, not a remote reference. Returns empty
 *  string on failure (CORS, 404, etc.) so the caller can fall back to
 *  text-only header. */
export async function fetchImageAsDataUrl(url: string): Promise<string> {
  if (!url) return '';
  try {
    const r = await fetch(url, { credentials: 'same-origin' });
    if (!r.ok) return '';
    const blob = await r.blob();
    return await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(typeof reader.result === 'string' ? reader.result : '');
      reader.onerror = () => resolve('');
      reader.readAsDataURL(blob);
    });
  } catch {
    return '';
  }
}
