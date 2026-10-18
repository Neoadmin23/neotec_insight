// v2.93.0 — desk links that work on Frappe v15 (/app) and v16 (/desk).
// The prefix arrives with the access profile at start-up; until then the
// v15 path is used, which v16 also redirects.
let prefix = '/app';

export function setDeskPrefix(p?: string | null) {
  if (p === '/app' || p === '/desk') prefix = p;
}

export function deskPrefix(): string {
  return prefix;
}

/** deskUrl('payment-entry/PE-0001') -> '/desk/payment-entry/PE-0001' on v16. */
export function deskUrl(path = ''): string {
  const clean = String(path).replace(/^\/+/, '');
  return clean ? `${prefix}/${clean}` : prefix;
}
