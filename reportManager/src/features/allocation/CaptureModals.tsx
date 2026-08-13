import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { t } from '../../utils/i18n';

/* Capture & evidence (v2.63.0).
 *
 * Two dialogs that exist for the same reason: a number feeding a P&L should
 * be able to say where it came from.
 *
 * `CaptureModal` shows what a capture *would* write before anything is
 * written — including what it will deliberately leave alone. A job that
 * silently rewrites driver values behind a filed statement is the kind of
 * convenience that becomes a problem at audit.
 *
 * `EvidenceModal` shows the people behind one cell. It reads the snapshot
 * taken at capture time rather than re-counting, so the list still
 * reconciles to the number even after someone transfers or is deleted —
 * which is exactly when an auditor is looking at it.
 */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const ACTION_LABEL: Record<string, string> = {
  write: 'write', update: 'update', unchanged: 'unchanged',
  kept: 'kept — set by hand', frozen: 'frozen', drift: 'drift',
};

interface Props { rule: string; year: number; ccLabel: (cc: string) => string; onClose: () => void; onSaved: () => void; }

export function CaptureModal({ rule, year, ccLabel, onClose, onSaved }: Props) {
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [freeze, setFreeze] = useState(false);
  const [msg, setMsg] = useState('');
  const [locking, setLocking] = useState(false);

  useEffect(() => {
    setErr('');
    api.allocationCapturePreview(rule, year)
      .then(setData)
      .catch((e: any) => setErr(e?.message || t('Preview failed.')));
  }, [rule, year]);

  async function commit() {
    setBusy(true); setErr('');
    try {
      const r: any = await api.allocationCaptureCommit(rule, year, null, freeze);
      setMsg(`${r.written} ${t('written')} · ${r.skipped} ${t('left alone')}`
        + (r.drifted ? ` · ${r.drifted} ${t('drifted')}` : ''));
      onSaved();
    } catch (e: any) {
      setErr(e?.message || t('Capture failed.'));
    } finally { setBusy(false); }
  }

  const months = (data?.months || []).filter((m: any) => m.rows.length || m.unassigned.length);
  const totals = months.reduce((acc: any, m: any) => {
    for (const r of m.rows) acc[r.action] = (acc[r.action] || 0) + 1;
    return acc;
  }, {});
  const unassigned: any[] = [];
  for (const m of months) for (const u of m.unassigned) {
    if (!unassigned.some((x) => x.id === u.id)) unassigned.push(u);
  }

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel cap-panel" role="dialog" aria-label={t('Capture head count')}>
        <div className="theme-h">
          <h3>{t('Capture head count')} — {year}</h3>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>
        <div className="cap-body">
          {err && <div className="alloc-warn">{err}</div>}
          {!data && !err && <p className="bk-hint">{t('Counting…')}</p>}

          {data && data.driver_source !== 'employee_headcount' && (
            <div className="alloc-warn">
              {t('This rule\'s driver source is not set to Employee head count, so there is nothing to capture. Set it on the rule first.')}
            </div>
          )}

          {/* The months entered by hand before capture existed are the ones a
              disagreeing count would damage. Offer the lock before the
              capture, not after. */}
          {!!data?.unprotected_manual_months?.length && (
            <div className="alloc-warn">
              <strong>
                {data.unprotected_manual_months.map((m: number) => t(MONTHS[m - 1])).join(', ')}
                {' '}{t('hold values entered by hand and are not frozen.')}
              </strong>{' '}
              {t('If the count disagrees with what was entered, accepting would overwrite figures that have already been reported. Freeze them first — they will then be compared and any difference shown, but not changed.')}
              <div style={{ marginTop: 8 }}>
                <button type="button" className="bk-btn" disabled={locking}
                  onClick={async () => {
                    setLocking(true);
                    try {
                      await api.allocationFreeze(rule, year, data.unprotected_manual_months, true);
                      const r = await api.allocationCapturePreview(rule, year);
                      setData(r);
                    } catch (e: any) { setErr(e?.message || t('Could not freeze.')); }
                    finally { setLocking(false); }
                  }}>
                  {locking ? t('Freezing…') : t('Freeze these months')}
                </button>
              </div>
            </div>
          )}

          {!!unassigned.length && (
            <div className="alloc-warn">
              <strong>{unassigned.length} {t('records have no cost centre')}</strong>{' — '}
              {unassigned.slice(0, 6).map((u) => u.name).join(', ')}
              {unassigned.length > 6 ? ` +${unassigned.length - 6}` : ''}.{' '}
              {t('They are in no column, so the denominator is short and every other cost centre takes a larger share. Assign them, or the split is quietly wrong.')}
            </div>
          )}

          {data && (
            <p className="bk-hint">
              {t('Counted as of')} {data.config?.as_of === 'month_start' ? t('the first day') : t('the last day')}
              {' '}{t('of each month, from')}{' '}
              {data.config?.cc_from === 'department_map' ? t('the department mapping') : 'Employee.payroll_cost_center'}.
              {' '}{t('Nothing is written until you accept.')}
            </p>
          )}

          {months.map((m: any) => (
            <div className="cap-month" key={m.month}>
              <div className="cap-month-h">
                <h4>{t(MONTHS[m.month - 1])} {year}</h4>
                <span>{t('as of')} {m.as_of}</span>
                {m.reconstructed && <span className="alloc-tag">{t('reconstructed')}</span>}
              </div>
              <table className="cap-table">
                <thead><tr>
                  <th>{t('Cost centre')}</th><th className="num">{t('Stored')}</th>
                  <th className="num">{t('Counted')}</th><th className="num">Δ</th><th>{t('Action')}</th>
                </tr></thead>
                <tbody>
                  {m.rows.map((r: any) => (
                    <tr key={r.cost_center} className={'cap-' + r.action}>
                      <td>{ccLabel(r.cost_center)}</td>
                      <td className="num">{r.stored === null ? '—' : r.stored}</td>
                      <td className="num">{r.counted}</td>
                      <td className="num">{r.delta === null ? '' : (r.delta > 0 ? '+' : '') + r.delta}</td>
                      <td>{t(ACTION_LABEL[r.action] || r.action)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!!m.notes?.length && (
                <p className="bk-hint">{m.notes.slice(0, 3).join(' · ')}</p>
              )}
            </div>
          ))}

          {data && !months.length && (
            <p className="bk-hint">{t('No employees found for this company in this year.')}</p>
          )}

          <div className="bk-foot cap-foot">
            <label className="chk">
              <input type="checkbox" checked={freeze} onChange={(e) => setFreeze(e.target.checked)} />
              {' '}{t('Freeze these months — later captures report drift instead of changing them')}
            </label>
            <span className="cap-sum">
              {Object.entries(totals).map(([k, v]) => `${v} ${t(ACTION_LABEL[k] || k)}`).join(' · ')}
            </span>
            {msg && <span className="alloc-ok">{msg}</span>}
            <button className="bk-btn bk-primary" disabled={busy || !months.length} onClick={commit}>
              {busy ? t('Writing…') : t('Accept and write')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function EvidenceModal({ rule, costCenter, ccLabel, year, month, onClose }: {
  rule: string; costCenter: string; ccLabel: string; year: number; month: number; onClose: () => void;
}) {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.allocationEvidence(rule, costCenter, year, month)
      .then(setData)
      .catch((e: any) => setErr(e?.message || t('Could not load the evidence.')));
  }, [rule, costCenter, year, month]);

  function exportCsv() {
    const rows = data?.members || [];
    const isLead = data?.source === 'crm_leads';
    const head = isLead
      ? ['ID', 'Lead', 'Business line', 'Status', 'Assigned on', 'Assigned to', 'Cost centre']
      : ['ID', 'Name', 'Department', 'Designation', 'Joined', 'Left', 'Cost centre'];
    const body = rows.map((m: any) => isLead
      ? [m.id, m.name, m.department, m.designation, m.assigned_on || m.joined, m.owner, m.cost_center]
      : [m.id, m.name, m.department, m.designation, m.joined, m.left, m.cost_center]);
    const csv = [head, ...body]
      .map((r) => r.map((c: any) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(','))
      .join('\r\n');
    const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `evidence-${ccLabel}-${MONTHS[month - 1]}-${year}.csv`.replace(/[\\/:*?"<>|]+/g, '-');
    document.body.appendChild(a); a.click(); a.remove();
  }

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel ev-panel" role="dialog" aria-label={t('Evidence')}>
        <div className="theme-h">
          <h3>{ccLabel} — {t(MONTHS[month - 1])} {year}</h3>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>
        <div className="cap-body">
          {err && <div className="alloc-warn">{err}</div>}

          {data && !data.available && (
            <p className="bk-hint">
              {data.reason === 'manual'
                ? `${t('Entered by hand')} — ${data.value ?? ''}${data.modified_by ? ` · ${data.modified_by}` : ''}${data.modified ? ` · ${String(data.modified).slice(0, 16)}` : ''}. ${t('There is no supporting list because nothing was captured.')}`
                : data.reason === 'no_entry' ? t('No entry stored for this cost centre and month.')
                  : t('The stored evidence could not be read.')}
            </p>
          )}

          {data?.available && (
            <>
              <div className="ev-meta">
                <span>{t('Captured')} {String(data.captured_on || '').slice(0, 16)}
                  {data.captured_by ? ` · ${data.captured_by}` : ''}</span>
                <span>{t('as of')} {data.as_of}</span>
                {data.source === 'reconstructed' && <span className="alloc-tag">{t('reconstructed')}</span>}
                {!!data.is_frozen && <span className="alloc-tag">{t('frozen')}</span>}
                {!!data.is_override && <span className="alloc-tag alloc-tag-warn">{t('overridden')}</span>}
              </div>
              <div className={data.reconciles ? 'ev-ok' : 'ev-bad'}>
                {data.reconciles
                  ? `${data.members?.length ?? 0} ${t('listed, matching the stored count of')} ${data.stored_value}.`
                  : `${t('The list holds')} ${data.members?.length ?? 0} ${t('but the stored value is')} ${data.stored_value} — ${t('the value was changed after capture.')}`}
              </div>
              <table className="cap-table ev-table">
                <thead><tr>
                  <th>{data.source === 'crm_leads' ? t('Lead') : t('Employee')}</th>
                  <th>{data.source === 'crm_leads' ? t('Assigned to') : t('Department')}</th>
                  <th>{data.source === 'crm_leads' ? t('Assigned on') : t('Joined')}</th>
                  <th>{data.source === 'crm_leads' ? t('Status') : t('Left')}</th>
                </tr></thead>
                <tbody>
                  {(data.members || []).map((m: any) => (
                    <tr key={m.id}>
                      <td>{m.name}<span className="ev-id">{m.id}</span>
                        {m.via === 'transfer_replay' && <span className="alloc-tag">{t('transferred later')}</span>}</td>
                      <td>{(data.source === 'crm_leads' ? m.owner : m.department) || '—'}</td>
                      <td>{(data.source === 'crm_leads' ? (m.assigned_on || m.joined) : m.joined) || '—'}</td>
                      <td>{(data.source === 'crm_leads' ? m.designation : m.left) || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!!data.undated_inactive?.length && (
                <>
                  <div className="alloc-warn" style={{ marginTop: 16 }}>
                    <strong>{data.undated_inactive.length} {t('employees are not Active but have no relieving date')}</strong>{'. '}
                    {t('They are excluded from every month, because without a date there is nothing to say when they stopped. Add a relieving date and they will count correctly up to it.')}
                  </div>
                  <table className="cap-table">
                    <thead><tr>
                      <th>{t('Employee')}</th><th>{t('Status')}</th>
                      <th>{t('Department')}</th><th>{t('Joined')}</th>
                    </tr></thead>
                    <tbody>
                      {data.undated_inactive.map((p: any) => (
                        <tr key={p.id}>
                          <td>
                            <a href={`/app/employee/${encodeURIComponent(p.id)}`}
                               target="_blank" rel="noopener noreferrer">{p.name}</a>
                            <span className="ev-id">{p.id}</span>
                          </td>
                          <td>{p.status || '—'}</td>
                          <td>{p.department || '—'}</td>
                          <td>{p.joined || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}

              <div className="bk-foot">
                <button className="bk-btn" onClick={exportCsv}>{t('Export list')}</button>
                <button className="bk-btn bk-primary" onClick={onClose}>{t('Close')}</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}


/** The pre-flight worklist (v2.63.1).
 *
 * An unassigned employee is the quietest failure in the whole feature: no
 * error, no mismatch, totals still tie to the pool — they simply leave the
 * denominator and every remaining cost centre takes a larger share. So the
 * list is a first-class screen with names, departments, the months affected,
 * a link straight to the record, and an export for whoever fixes them.
 */
export function UnassignedModal({ rule, year, onClose }: {
  rule: string; year: number; onClose: () => void;
}) {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.allocationUnassigned(rule, year)
      .then(setData)
      .catch((e: any) => setErr(e?.message || t('Could not run the check.')));
  }, [rule, year]);

  function exportCsv() {
    const head = ['Employee ID', 'Name', 'Department', 'Designation', 'Joined', 'Left', 'Months affected'];
    const body = (data?.people || []).map((p: any) => [
      p.id, p.name, p.department, p.designation, p.joined, p.left,
      (p.months || []).map((m: number) => MONTHS[m - 1]).join(' '),
    ]);
    const csv = [head, ...body]
      .map((r) => r.map((c: any) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(','))
      .join('\r\n');
    const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `employees-without-cost-centre-${year}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
  }

  const people = data?.people || [];

  return (
    <div className="theme-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="theme-panel cap-panel" role="dialog" aria-label={t('Employees without a cost centre')}>
        <div className="theme-h">
          <h3>{t('Employees without a cost centre')} — {year}</h3>
          <button className="fh-x" onClick={onClose} aria-label={t('Close')}>×</button>
        </div>
        <div className="cap-body">
          {err && <div className="alloc-warn">{err}</div>}
          {!data && !err && <p className="bk-hint">{t('Checking…')}</p>}

          {data && !people.length && !data.undated_inactive?.length && (
            <div className="ev-ok">
              {t('Every employee resolves to a cost centre. The denominator is complete.')}
            </div>
          )}
          {data && !people.length && !!data.undated_inactive?.length && (
            <div className="ev-ok">
              {t('Every counted employee resolves to a cost centre — but see the excluded records below.')}
            </div>
          )}

          {data && !!people.length && (
            <>
              <div className="alloc-warn">
                <strong>{data.total_people} {t('employees have no cost centre')}</strong>{'. '}
                {t('They are counted in no column, so each assigned cost centre is carrying up to')}{' '}
                <strong>{data.worst_distortion_pct}%</strong>{' '}
                {t('more than its true share. Nothing will look wrong — the totals still tie to the pool.')}
              </div>
              <p className="bk-hint">
                {data.cc_from === 'department_map'
                  ? (data.has_dept_map
                      ? t('The rule resolves cost centre from the department mapping. These departments are not in it.')
                      : t('The rule is set to use a department mapping, but the mapping table on the rule is empty.'))
                  : t('The rule reads Employee.payroll_cost_center. Set it on these records, or switch the rule to a department mapping.')}
              </p>

              <table className="cap-table">
                <thead><tr>
                  <th>{t('Employee')}</th><th>{t('Department')}</th>
                  <th>{t('Designation')}</th><th>{t('Joined')}</th><th>{t('Months')}</th>
                </tr></thead>
                <tbody>
                  {people.map((p: any) => (
                    <tr key={p.id}>
                      <td>
                        <a href={`/app/employee/${encodeURIComponent(p.id)}`}
                           target="_blank" rel="noopener noreferrer">{p.name}</a>
                        <span className="ev-id">{p.id}</span>
                      </td>
                      <td>{p.department || '—'}</td>
                      <td>{p.designation || '—'}</td>
                      <td>{p.joined || '—'}</td>
                      <td className="ev-months">
                        {(p.months || []).map((m: number) => MONTHS[m - 1]).join(' ')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <table className="cap-table" style={{ marginTop: 14 }}>
                <thead><tr>
                  <th>{t('Month')}</th><th className="num">{t('Counted')}</th>
                  <th className="num">{t('Missing')}</th><th className="num">{t('Overstatement')}</th>
                </tr></thead>
                <tbody>
                  {(data.per_month || []).filter((r: any) => r.total).map((r: any) => (
                    <tr key={r.month} className={r.missing ? 'cap-kept' : ''}>
                      <td>{t(MONTHS[r.month - 1])}</td>
                      <td className="num">{r.counted}</td>
                      <td className="num">{r.missing || ''}</td>
                      <td className="num">{r.missing ? r.distortion_pct + '%' : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div className="bk-foot">
                <button className="bk-btn" onClick={exportCsv}>{t('Export list')}</button>
                <button className="bk-btn bk-primary" onClick={onClose}>{t('Close')}</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
