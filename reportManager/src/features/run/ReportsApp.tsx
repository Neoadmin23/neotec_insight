import { useEffect, useState } from 'react';
import { t } from '../../utils/i18n';
import { api } from '../../utils/api';
import type { ReportDefinition, ReportSummary, RunResult, RunSnapshot } from '../../types';
import { useAccess } from '../../utils/access';
import { RunTab } from './RunTab';
import { RowsTab } from '../rows/RowsTab';
import { MapTab } from '../map/MapTab';
import { BudgetTab } from '../budget/BudgetTab';
import { EquityTab } from '../equity/EquityTab';

// v1.9.49 — 'equity' tab added for the Statement of Shareholder's Equity.
// Visible to all view tiers (CEO reads, CFO edits, viewer reads); the tab
// itself shows edit affordances only when access.canEdit is true.
type Tab = 'run' | 'rows' | 'map' | 'budget' | 'equity';

interface Props {
  onPushSnapshot: (snap: RunSnapshot) => void;
  onSelectedReportChange?: (reportName: string) => void;
}

export function ReportsApp({ onPushSnapshot, onSelectedReportChange }: Props) {
  const access = useAccess();
  const [tab, setTab] = useState<Tab>('run');
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [selectedReport, setSelectedReport] = useState<string>('');
  const [report, setReport] = useState<ReportDefinition | null>(null);
  const [lastRun, setLastRun] = useState<RunResult | null>(null);

  useEffect(() => {
    api.listReports().then((rs: any[]) => {
      setReports(rs);
      if (rs.length > 0) setSelectedReport(rs[0].slug || rs[0].name);
    }).catch((e) => console.error(e));
  }, []);

  // Bubble the resolved report's DocType name up so the Visuals workspace
  // knows which report it's saving dashboards under. We surface report.name
  // (the canonical DocType name) rather than the slug — the dashboard endpoint
  // accepts either, but name is unambiguous.
  useEffect(() => {
    if (onSelectedReportChange) onSelectedReportChange(report?.name || '');
  }, [report?.name, onSelectedReportChange]);

  useEffect(() => {
    if (!selectedReport) { setReport(null); return; }
    api.getReport(selectedReport).then((r: any) => setReport(r as ReportDefinition)).catch((e) => console.error(e));
  }, [selectedReport]);

  return (
    <div>
      <nav className="ni-tabs">
        <button className={'ni-tab' + (tab === 'run' ? ' is-active' : '')} onClick={() => setTab('run')}>
          <i className="ti ti-chart-bar" aria-hidden /> {t('Run')}
        </button>
        <button className={'ni-tab' + (tab === 'equity' ? ' is-active' : '')} onClick={() => setTab('equity')}
          title="Statement of Shareholder's Equity — Beginning + Movements = Ending">
          <i className="ti ti-building-bank" aria-hidden /> {t('Equity')}
        </button>
        {access.canEdit && (
          <>
            <button className={'ni-tab' + (tab === 'rows' ? ' is-active' : '')} onClick={() => setTab('rows')}>
              <i className="ti ti-list-tree" aria-hidden /> {t('Rows')}
            </button>
            <button className={'ni-tab' + (tab === 'map' ? ' is-active' : '')} onClick={() => setTab('map')}>
              <i className="ti ti-arrows-shuffle" aria-hidden /> {t('Account map')}
            </button>
            <button className={'ni-tab' + (tab === 'budget' ? ' is-active' : '')} onClick={() => setTab('budget')}>
              <i className="ti ti-target" aria-hidden /> {t('Budget')}
            </button>
          </>
        )}
      </nav>

      {tab === 'run' && (
        <RunTab
          reports={reports}
          selectedReport={selectedReport}
          setSelectedReport={setSelectedReport}
          report={report}
          onRunResult={setLastRun}
          lastRun={lastRun}
          onPushSnapshot={onPushSnapshot}
        />
      )}
      {tab === 'equity' && <EquityTab defaultCompany={report?.company || ''} canEdit={access.canEdit} />}
      {access.canEdit && tab === 'rows' && report && <RowsTab report={report} onChange={setReport} />}
      {access.canEdit && tab === 'map' && report && <MapTab report={report} />}
      {access.canEdit && tab === 'budget' && report && <BudgetTab report={report} />}
    </div>
  );
}
