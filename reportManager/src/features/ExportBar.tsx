import { useState } from 'react';
import { t } from '../utils/i18n';
import BrandKitModal from './shell/BrandKitModal';
import { csvDoc, imageDoc, pdfDoc, printDoc, xlsxDoc, type ReportDoc } from '../utils/reportdoc';

/* v2.55.0 — the same output row on every report menu.
 *
 * Before this, what you could do with a report depended on which report it
 * was: the ledger had Excel/Print/PDF, the statements had Excel/Print, the
 * pivots had CSV, and nothing had an image. Each toolbar was hand-rolled next
 * to the Run button, so adding a format meant editing every screen and
 * inevitably missing one.
 *
 * A screen now hands over a `getDoc()` and gets all five formats plus the
 * print setup. `getDoc` is a callback rather than a value because building the
 * document for a large ledger is not free — it runs when a button is pressed,
 * not on every render.
 */

interface Props {
  /** Build the document to export. Return null when there's nothing to send. */
  getDoc: () => ReportDoc | null;
  /** Company docname — selects which Brand Kit the gear edits. */
  company?: string;
  companyLabel?: string;
  /** Disable every output (typically: the report hasn't been run yet). */
  disabled?: boolean;
  /** Hide individual formats where they genuinely don't apply. */
  omit?: Array<'xlsx' | 'csv' | 'pdf' | 'print' | 'image'>;
  /** Extra buttons rendered before the outputs (Run, Fields, …). */
  children?: React.ReactNode;
}

type Job = '' | 'pdf' | 'image' | 'xlsx';

export function ExportBar({ getDoc, company, companyLabel, disabled, omit = [], children }: Props) {
  const [busy, setBusy] = useState<Job>('');
  const [setupOpen, setSetupOpen] = useState(false);
  const [err, setErr] = useState('');

  const show = (k: 'xlsx' | 'csv' | 'pdf' | 'print' | 'image') => !omit.includes(k);

  /** One guard for all five: resolve the document, surface a failure inline
   *  rather than in an alert, and never leave a button stuck in "…". */
  const run = async (job: Job, fn: (doc: ReportDoc) => void | Promise<void>) => {
    if (busy) return;
    setErr('');
    let doc: ReportDoc | null = null;
    try { doc = getDoc(); } catch (e: any) { setErr(e?.message || t('Could not build the export.')); return; }
    if (!doc) { setErr(t('Run the report first.')); return; }
    if (job) setBusy(job);
    try {
      await fn(doc);
    } catch (e: any) {
      setErr(e?.message || t('Export failed.'));
    } finally {
      if (job) setBusy('');
    }
  };

  return (
    <div className="ni-exportbar">
      {children}
      {show('xlsx') && (
        <button type="button" disabled={disabled || busy === 'xlsx'} title={t('Download as an Excel workbook')}
          onClick={() => run('xlsx', xlsxDoc)}>{busy === 'xlsx' ? t('Building…') : t('Excel')}</button>
      )}
      {show('csv') && (
        <button type="button" disabled={disabled} title={t('Download as CSV')}
          onClick={() => run('', csvDoc)}>{t('CSV')}</button>
      )}
      {show('pdf') && (
        <button type="button" disabled={disabled || busy === 'pdf'} title={t('Render a PDF on the server')}
          onClick={() => run('pdf', pdfDoc)}>{busy === 'pdf' ? t('Rendering…') : t('PDF')}</button>
      )}
      {show('print') && (
        <button type="button" disabled={disabled} title={t('Open the print dialog')}
          onClick={() => run('', printDoc)}>{t('Print')}</button>
      )}
      {show('image') && (
        <button type="button" disabled={disabled || busy === 'image'} title={t('Download as a PNG image')}
          onClick={() => run('image', imageDoc)}>{busy === 'image' ? t('Capturing…') : t('Image')}</button>
      )}
      <button type="button" className="ni-exp-gear" title={t('Print setup — letterhead, logo, borders, paper')}
        onClick={() => setSetupOpen(true)}>
        <span aria-hidden>⚙</span>
        <span className="ni-exp-gear-lbl">{t('Setup')}</span>
      </button>
      {err && <span className="ni-exp-err" role="status">{err}</span>}
      {setupOpen && (
        <BrandKitModal company={company} companyLabel={companyLabel}
          onClose={() => setSetupOpen(false)} />
      )}
    </div>
  );
}

export default ExportBar;
