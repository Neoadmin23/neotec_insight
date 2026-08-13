import { useEffect, useRef, useState } from 'react';
import { api } from '../utils/api';
import { t } from '../utils/i18n';
import { loadPrintHeader, invalidatePrintHeader, printHtml, printElement, type Orientation } from '../utils/printDoc';

interface Props {
  title: string;
  meta?: string;
  /** Provide custom HTML to print … */
  getBody?: () => string;
  /** … or a ref to an on-screen element to print. */
  targetRef?: React.RefObject<HTMLElement>;
  defaultOrientation?: Orientation;
  compact?: boolean;
}

/** Drop-in print toolbar: Define header · orientation · Print/PDF.
 *  Shared across every report so the letterhead is defined once. */
export function PrintBar({ title, meta, getBody, targetRef, defaultOrientation = 'portrait', compact }: Props) {
  const [orient, setOrient] = useState<Orientation>(defaultOrientation);
  const [show, setShow] = useState(false);
  const [hdr, setHdr] = useState({ org_name: '', org_address: '', logo_url: '' });
  const loaded = useRef(false);

  useEffect(() => { loadPrintHeader().then((h) => { setHdr(h); loaded.current = true; }); }, []);

  const save = async () => {
    await api.setPrintHeader(hdr.org_name, hdr.org_address, hdr.logo_url).catch(() => {});
    invalidatePrintHeader(); loadPrintHeader(true);
  };
  const doPrint = () => {
    if (getBody) printHtml({ title, orientation: orient, bodyHtml: getBody(), meta });
    else if (targetRef?.current) printElement(targetRef.current, { title, orientation: orient, meta });
  };

  return (
    <div className="no-print" style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', position: 'relative' }}>
      <button className="btn btn-xs btn-default" onClick={() => setShow(!show)}>{t('Define header')}</button>
      <select className="form-control" style={{ width: 'auto', height: 26, padding: '2px 6px', fontSize: 12 }}
              value={orient} onChange={(e) => setOrient(e.target.value as Orientation)}>
        <option value="portrait">{t('Portrait')}</option>
        <option value="landscape">{t('Landscape')}</option>
      </select>
      <button className="btn btn-xs btn-primary" onClick={doPrint}>🖨 {compact ? t('Print') : t('Print / PDF')}</button>

      {show && (
        <div style={{ position: 'absolute', top: 32, insetInlineEnd: 0, zIndex: 30, background: '#fff',
                      border: '1px solid #ddd', borderRadius: 8, padding: 12, width: 320, boxShadow: '0 6px 24px rgba(0,0,0,.12)' }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>{t('Print header')}</div>
          <label style={{ display: 'block', fontSize: 12, marginBottom: 6 }}>
            <span className="text-muted">{t('Organisation name')}</span>
            <input className="form-control" value={hdr.org_name} onChange={(e) => setHdr({ ...hdr, org_name: e.target.value })} onBlur={save} />
          </label>
          <label style={{ display: 'block', fontSize: 12, marginBottom: 6 }}>
            <span className="text-muted">{t('Address / sub-header')}</span>
            <input className="form-control" value={hdr.org_address} onChange={(e) => setHdr({ ...hdr, org_address: e.target.value })} onBlur={save} />
          </label>
          <label style={{ display: 'block', fontSize: 12, marginBottom: 6 }}>
            <span className="text-muted">{t('Logo URL (optional)')}</span>
            <input className="form-control" value={hdr.logo_url} onChange={(e) => setHdr({ ...hdr, logo_url: e.target.value })} onBlur={save} />
          </label>
          <div className="text-muted" style={{ fontSize: 11 }}>{t('Saved once, used on every printed report, full width in either orientation.')}</div>
          <div style={{ textAlign: 'right', marginTop: 6 }}>
            <button className="btn btn-xs btn-default" onClick={() => setShow(false)}>{t('Done')}</button>
          </div>
        </div>
      )}
    </div>
  );
}
