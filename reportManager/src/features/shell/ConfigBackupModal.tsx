import { useEffect, useRef, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

// v2.87.3 — AREAS used to be hardcoded here, a THIRD independently
// maintained list alongside two more in api/report.py (export_
// configuration's own dict, and _IMPORT_ORDER) — any of the three could
// silently miss a new doctype, and a coverage audit found seven already
// had. All three are now derived from ONE registry
// (utils/config_backup_registry.py); this component just fetches it.
type Area = { label: string; doctypes: string[] };

/* Configuration backup — export ALL Insight config (report definitions,
 * account→flag mappings, budget, equity, dashboards, AI settings, etc.) to a
 * JSON file, and restore it on another site. Intended for SAME company / chart
 * (names identical). */
export function ConfigBackupModal({ onClose }: { onClose: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [summary, setSummary] = useState<any>(null);
  const [pending, setPending] = useState<any>(null); // parsed bundle awaiting confirm
  const [areas, setAreas] = useState<Area[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [counts, setCounts] = useState<Record<string, number>>({});
  const fileRef = useRef<HTMLInputElement>(null);
  const allDt = areas.flatMap((a) => a.doctypes);

  useEffect(() => {
    api.configAreas().then((rows) => {
      setAreas(rows || []);
      setSelected(new Set((rows || []).flatMap((a) => a.doctypes)));
    }).catch(() => {});
    api.configSectionCounts().then(setCounts).catch(() => {});
  }, []);

  const areaSelected = (a: Area) => a.doctypes.every((d) => selected.has(d));
  const areaCount = (a: Area) => a.doctypes.reduce((s, d) => s + (counts[d] || 0), 0);
  const toggleArea = (a: Area) => setSelected((s) => {
    const n = new Set(s); const on = a.doctypes.every((d) => n.has(d));
    a.doctypes.forEach((d) => (on ? n.delete(d) : n.add(d)));
    return n;
  });
  const allOn = allDt.length > 0 && selected.size === allDt.length;
  const toggleAll = () => setSelected(allOn ? new Set() : new Set(allDt));

  async function doExport() {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const picked = [...selected];
      const bundle = await api.exportConfiguration(picked.length === allDt.length ? undefined : picked);
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      const stamp = new Date().toISOString().slice(0, 10);
      a.href = URL.createObjectURL(blob);
      a.download = `neotec_insight_config_${stamp}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      const total = Object.values(bundle.counts || {}).reduce((s: number, n: any) => s + (n || 0), 0);
      setMsg(`${t('Exported')} — ${total} ${t('records')} (v${bundle.version || '?'}).`);
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally { setBusy(false); }
  }

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setErr(null); setMsg(null); setSummary(null);
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result));
        if (!parsed || !parsed.data) throw new Error(t('Not a valid configuration file.'));
        setPending(parsed);
      } catch (e: any) { setErr(String(e?.message || e)); }
    };
    reader.readAsText(f);
  }

  async function doImport() {
    if (!pending) return;
    setBusy(true); setErr(null); setMsg(null);
    try {
      const res = await api.importConfiguration(pending, 'replace');
      setSummary(res);
      setPending(null);
      if (fileRef.current) fileRef.current.value = '';
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally { setBusy(false); }
  }

  const pendingTotal = pending
    ? Object.values(pending.counts || {}).reduce((s: number, n: any) => s + (n || 0), 0) : 0;

  return (
    <div className="gl-scrim" onClick={onClose}>
      <div className="gl-modal" style={{ width: 620 }} onClick={(e) => e.stopPropagation()}>
        <div className="gl-head">
          <div>
            <div className="gl-title">{t('Configuration backup')}</div>
            <div className="gl-sub">{t('Move your report setup between sites (same company)')}</div>
          </div>
          <button className="gl-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>

        <div style={{ padding: '14px 18px', display: 'flex', flexDirection: 'column', gap: 18 }}>
          <section>
            <div className="flbl" style={{ marginBottom: 6 }}>{t('Export')}</div>
            <p style={{ fontSize: 12.5, color: '#6b675f', margin: '0 0 8px' }}>
              {t('Download Insight configuration as one JSON file. Choose which areas to include — handy when a sandbox only needs part of the setup.')}
            </p>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <label style={{ fontSize: 12.5, fontWeight: 600, display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
                <input type="checkbox" checked={allOn} ref={(el) => { if (el) el.indeterminate = !allOn && selected.size > 0; }} onChange={toggleAll} />
                {t('All areas')}
              </label>
              <span style={{ fontSize: 11, color: '#9a948a' }}>{selected.size}/{allDt.length} {t('selected')}</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 14px', marginBottom: 10,
                          border: '1px solid var(--border,#eee)', borderRadius: 8, padding: '8px 10px' }}>
              {areas.map((a) => (
                <label key={a.label} style={{ fontSize: 12.5, display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
                  <input type="checkbox" checked={areaSelected(a)} onChange={() => toggleArea(a)} />
                  <span>{t(a.label)}</span>
                  <span style={{ fontSize: 11, color: '#9a948a', marginInlineStart: 'auto' }}>{areaCount(a)}</span>
                </label>
              ))}
            </div>
            <button className="btn-primary gl-open" onClick={doExport} disabled={busy || selected.size === 0} style={{ border: 0 }}>
              {busy ? t('Working…') : selected.size === allDt.length ? t('Export all configuration') : `${t('Export selected')} (${selected.size})`}
            </button>
          </section>

          <section style={{ borderTop: '1px solid var(--border,#eee)', paddingTop: 14 }}>
            <div className="flbl" style={{ marginBottom: 6 }}>{t('Import / Restore')}</div>
            <p style={{ fontSize: 12.5, color: '#b3261e', margin: '0 0 8px' }}>
              ⚠ {t('Restore REPLACES all existing Insight configuration on this site. Use it on a fresh install or to overwrite with your tested setup.')}
            </p>
            <input ref={fileRef} type="file" accept="application/json,.json" onChange={onPickFile} />
            {pending && (
              <div style={{ marginTop: 10 }}>
                <div style={{ fontSize: 12.5, marginBottom: 8 }}>
                  {t('Ready to restore')}: <b>{pendingTotal}</b> {t('records')}
                  {pending.version ? ` · v${pending.version}` : ''}
                  {pending.site ? ` · ${t('from')} ${pending.site}` : ''}
                </div>
                <button className="btn-primary gl-open" onClick={doImport} disabled={busy} style={{ border: 0, background: '#b3261e' }}>
                  {busy ? t('Restoring…') : t('Confirm restore (replace all)')}
                </button>
              </div>
            )}
          </section>

          {msg && <div className="gl-recon ok" style={{ margin: 0 }}>{msg}</div>}
          {err && <div className="gl-msg gl-err" style={{ padding: 8, textAlign: 'left' }}>{err}</div>}

          {summary && (
            <div className="gl-recon ok" style={{ margin: 0 }}>
              <div style={{ fontWeight: 700, marginBottom: 4 }}>
                ✓ {t('Restore complete')}{summary.from_version ? ` (v${summary.from_version})` : ''}
              </div>
              <div style={{ fontSize: 12, fontWeight: 400 }}>
                {Object.entries(summary.inserted || {}).map(([k, v]: any) => (
                  <span key={k} style={{ marginRight: 12 }}>{k.replace('Insight ', '')}: {v}</span>
                ))}
              </div>
              {summary.errors && summary.errors.length > 0 && (
                <details style={{ marginTop: 6, fontWeight: 400 }}>
                  <summary style={{ color: '#b3261e', cursor: 'pointer' }}>
                    {summary.errors.length} {t('warnings')}
                  </summary>
                  <ul style={{ fontSize: 11, margin: '6px 0 0', paddingLeft: 18 }}>
                    {summary.errors.map((e: string, i: number) => <li key={i}>{e}</li>)}
                  </ul>
                </details>
              )}
              <div style={{ fontSize: 11.5, fontWeight: 400, marginTop: 6, color: '#1d7a45' }}>
                {t('Reload the page to see your restored reports.')}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
