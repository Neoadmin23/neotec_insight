import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';
import ClassificationStudio from './ClassificationStudio';

/** Account Classification as a first-class workspace (v2.69.0).
 *
 *  It was reachable only through a 🏷 button inside Financial Health, which
 *  understated it: the tag it sets is read by Financial Health, the Cash Flow
 *  statement and the Zakat base alike, so burying it behind one of its three
 *  consumers made it look like a setting belonging to that one screen.
 *
 *  The button stays. Someone who has just seen a wrong COGS figure wants to fix
 *  it without leaving the page, and removing that path to tidy the navigation
 *  would cost more than the tab gains. */
export default function ClassificationTab() {
  const [companies, setCompanies] = useState<{ name: string; label?: string }[]>([]);
  const [company, setCompany] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => {
    api.listCompanies()
      .then((cs: any[]) => {
        setCompanies(cs || []);
        setCompany((c) => c || (cs && cs.length ? cs[0].name : ''));
      })
      .catch((e: any) => setErr(String(e?.message || e)));
  }, []);

  return (
    <div className="cls-workspace">
      <div className="studio-frow" style={{ gap: 8, alignItems: 'center', marginBottom: 8 }}>
        <label className="studio-hint">{t('Company')}</label>
        <select value={company} onChange={(e) => setCompany(e.target.value)}>
          {companies.length === 0 && <option value="">{t('Company')}</option>}
          {companies.map((c) => <option key={c.name} value={c.name}>{c.label || c.name}</option>)}
        </select>
      </div>
      {err && <div className="studio-err">{err}</div>}
      {/* Keyed on company so switching reloads the account list rather than
          showing another company's tags against this one's name. */}
      {company
        ? <ClassificationStudio key={company} company={company} embedded />
        : !err && <div className="fh-loading">{t('Loading…')}</div>}
    </div>
  );
}
