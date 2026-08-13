import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { FrappeProvider } from 'frappe-react-sdk';
import App from './App';
import './index.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <FrappeProvider url="">
      <App />
    </FrappeProvider>
  </StrictMode>
);


// v2.39.1 — fatal-error overlay: a crash anywhere must never leave a silent
// white page. The overlay text IS the bug report.
function showFatal(msg: string) {
  try {
    let el = document.getElementById('ni-fatal-overlay');
    if (!el) {
      el = document.createElement('div');
      el.id = 'ni-fatal-overlay';
      el.setAttribute('style', 'position:fixed;inset:12px;z-index:99999;background:#fdecea;border:3px solid #b3261e;border-radius:12px;padding:18px;color:#7a1712;font:12px/1.5 monospace;white-space:pre-wrap;overflow:auto;');
      document.body.appendChild(el);
    }
    el.textContent = 'Insight crashed — please screenshot this box:\n\n' + msg;
  } catch { /* */ }
}
window.addEventListener('error', (e) => showFatal(String(e.error?.stack || e.message || e.error)));
window.addEventListener('unhandledrejection', (e: any) => {
  // Background-promise failures don't invalidate the rendered report — show a
  // dismissible notice instead of the full-screen box.
  try {
    const el = document.createElement('div');
    el.setAttribute('style', 'position:fixed;bottom:14px;inset-inline-end:14px;z-index:99998;max-width:520px;background:#fdecea;border:2px solid #b3261e;border-radius:10px;padding:10px 14px;color:#7a1712;font:11px/1.5 monospace;white-space:pre-wrap;');
    el.textContent = 'Background error (report still valid — screenshot if repeating):\n' + String(e.reason?.message || e.reason);
    const x = document.createElement('button');
    x.textContent = '×'; x.setAttribute('style', 'float:inline-end;border:0;background:none;color:#7a1712;font-weight:700;cursor:pointer;');
    x.onclick = () => el.remove(); el.prepend(x);
    document.body.appendChild(el); setTimeout(() => el.remove(), 30000);
  } catch { /* */ }
});
