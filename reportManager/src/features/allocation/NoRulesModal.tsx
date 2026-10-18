import { t } from '../../utils/i18n';
import { deskPrefix } from '../../utils/desk';

/**
 * v2.88.10 — explains, in a popup, why the Allocation rule picker is empty.
 *
 * "No rules defined" used to be all the page said, whether nothing had ever
 * been set up, the rule had been deleted, it sat under another company, or the
 * read had failed. The reason comes from allocation.rules_diagnostic, which
 * looks at the rules, the Deleted Document log and any head-count entries left
 * without a rule, so the message names the actual cause and the way back.
 */
interface Props { diag: any; company: string; onClose: () => void; onRetry: () => void; }

const TITLE: Record<string, string> = {
  deleted: 'The allocation rule was deleted',
  orphan_entries: 'The allocation rule is missing, but its data is still here',
  other_company: 'Rules exist, but not for this company',
  inactive_only: 'Rules exist, but none is Active',
  read_error: 'The allocation rules could not be read',
  none_exist: 'No allocation rule has been created on this site',
};

export function NoRulesModal({ diag, company, onClose, onRetry }: Props) {
  const reason: string = diag?.reason || 'none_exist';
  const deleted: any[] = (diag?.deleted || []).filter((d: any) => !d.restored);
  const orphans: any[] = diag?.orphans || [];
  const rules: any[] = diag?.rules || [];

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel" role="alertdialog" aria-label={t(TITLE[reason] || TITLE.none_exist)}
        style={{ maxWidth: 640 }}>
        <div className="theme-h">
          <h3>⚠ {t(TITLE[reason] || TITLE.none_exist)}</h3>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>
        <div className="cap-body">
          {reason === 'deleted' && (
            <>
              <p>{t('The rule was deleted. The site keeps a record of it, so it can be restored with its settings intact:')}</p>
              <table className="cap-table">
                <thead><tr><th>{t('Rule')}</th><th>{t('Deleted by')}</th><th>{t('When')}</th><th /></tr></thead>
                <tbody>
                  {deleted.map((d) => (
                    <tr key={d.name}>
                      <td>{d.deleted_name}</td><td>{d.owner}</td><td>{d.creation}</td>
                      <td><a className="vs-btn" target="_blank" rel="noopener noreferrer"
                        href={`${deskPrefix()}/deleted-document/${encodeURIComponent(d.name)}`}>{t('Open to restore')}</a></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="bk-hint">{t('Open the entry and press Restore. If an Import (Backup → replace) ran at that time, that is what removed it.')}</p>
            </>
          )}

          {reason === 'orphan_entries' && (
            <>
              <p>{t('Head-count entries are still stored, but the rule they belong to no longer exists. Create a rule with exactly the same title and they reattach automatically:')}</p>
              <table className="cap-table">
                <thead><tr><th>{t('Rule title to create')}</th><th className="num">{t('Entries kept')}</th><th /></tr></thead>
                <tbody>
                  {orphans.map((o) => (
                    <tr key={o.rule}>
                      <td><strong>{o.rule}</strong></td><td className="num">{o.entries}</td>
                      <td><a className="vs-btn" target="_blank" rel="noopener noreferrer"
                        href={`${deskPrefix()}/insight-allocation-rule/new?title=${encodeURIComponent(o.rule)}&company=${encodeURIComponent(company)}`}>
                        {t('Create it')}</a></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {(reason === 'other_company' || reason === 'inactive_only') && (
            <>
              <p>{reason === 'other_company'
                ? `${t('This page shows only rules for the selected company')} (${company}). ${t('These exist:')}`
                : t('Only Active rules are listed. These exist but are switched off:')}</p>
              <table className="cap-table">
                <thead><tr><th>{t('Rule')}</th><th>{t('Company')}</th><th>{t('Status')}</th><th /></tr></thead>
                <tbody>
                  {rules.map((r) => (
                    <tr key={r.name}>
                      <td>{r.name}</td><td>{r.company || '—'}</td>
                      <td>{r.is_active ? t('Active') : t('Inactive')}</td>
                      <td><a className="vs-btn" target="_blank" rel="noopener noreferrer"
                        href={`${deskPrefix()}/insight-allocation-rule/${encodeURIComponent(r.name)}`}>{t('Open')}</a></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {reason === 'read_error' && (
            <>
              <p>{t('The server could not read the rules, so this is not the same as having none:')}</p>
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{diag?.error}</pre>
              <p className="bk-hint">{t('If it mentions a missing table or field, run Migrate Database. If it mentions too many connections, the site is overloaded: wait a minute and retry.')}</p>
            </>
          )}

          {reason === 'none_exist' && (
            <>
              <p>{t('There is no Insight Allocation Rule on this site, no deleted one in the log, and no head-count data waiting for one. Either none was ever created here, or this is a different site from the one the rules were set up on.')}</p>
              <p><a className="vs-btn" target="_blank" rel="noopener noreferrer" href={deskPrefix() + '/insight-allocation-rule/new'}>
                {t('Create an allocation rule')}</a></p>
            </>
          )}

          {diag?.excluded_doctype === false && (
            <p className="alloc-warn">{t('The Excluded Cost Centres table is missing: run Migrate Database.')}</p>
          )}

          <div className="bk-foot cap-foot">
            <button className="bk-btn" onClick={onRetry}>{t('Check again')}</button>
            <button className="bk-btn bk-primary" onClick={onClose}>{t('Close')}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
