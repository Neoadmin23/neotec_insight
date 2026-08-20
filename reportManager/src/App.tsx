import { useEffect, useState , lazy, Suspense } from 'react';
import { ReportsApp } from './features/run/ReportsApp';
import { VisualsApp } from './features/visuals/VisualsApp';
import { DashboardApp } from './features/dashboard/DashboardApp';
import { GroupApp } from './features/group/GroupApp';
import { LedgerWorkspace } from './features/gl/LedgerWorkspace';
import { AllocationApp } from './features/allocation/AllocationApp';
import { ConfigBackupModal } from './features/shell/ConfigBackupModal';
const StudioApp = lazy(() => import('./features/studio/StudioApp'));
const FinancialHealth = lazy(() => import('./features/health/FinancialHealth'));
const ClassificationTab = lazy(() => import('./features/health/ClassificationTab'));
const VatSettings = lazy(() => import('./features/vat/VatSettings'));
const VatReturn = lazy(() => import('./features/vat/VatReturn'));
const CashFlowTab = lazy(() => import('./features/cashflow/CashFlowTab'));
const CashFlowForecastTab = lazy(() => import('./features/cashflowforecast/CashFlowForecastTab').then(m => ({ default: m.CashFlowForecastTab })));
const ZakatTab = lazy(() => import('./features/zakat/ZakatTab'));
const ExportPacks = lazy(() => import('./features/packs/ExportPacks'));
const AgeingTab = lazy(() => import('./features/ageing/AgeingTab'));
const GstTab = lazy(() => import('./features/gst/GstTab'));
const BankApp = lazy(() => import('./features/bank/BankApp'));
const HrApp = lazy(() => import('./features/hr/HrApp'));
import { CfoBriefing } from './features/briefing/CfoBriefing';
import { BackToErpMenu } from './features/shell/BackToErpMenu';
import { CompanyBrand } from './features/shell/CompanyBrand';
import { api } from './utils/api';
import { ThemePicker } from './features/shell/ThemePicker';
import MenuSetupModal, { type MenuSectionCfg } from './features/shell/MenuSetupModal';
import { initTheme } from './utils/theme';
import { syncBrandFromServer } from './utils/branddoc';
import { AccessContext, DEFAULT_ACCESS, type AccessProfile } from './utils/access';
import { DimensionFiltersContext, type DimensionFiltersMap } from './utils/dimFilters';
import { LangContext, getLang, setLangGlobal, t, loadDimensionOptions, type Lang } from './utils/i18n';
import type { Workspace } from './types';

/** v2.24.1 — the navigation taxonomy. Sections are the row-1 buttons; each
 *  section's tabs render as an always-visible row-2 sub-menu when active. */
interface NavTab { ws: Workspace; label: string; title?: string; needsGroup?: boolean }
interface NavSection { key: string; label: string; title?: string; needsGroup?: boolean; tabs: NavTab[] }

/** v2.55.1 — CFO Briefing, Dashboard and Analysis were three top-level
 *  buttons holding four tabs between them, which spent a third of the nav bar
 *  on one idea: how is the business doing. They are now one section with four
 *  sub-tabs, leaving Reports (what the books say), Studio (build your own),
 *  Compliance (statutory) and Operations (day to day) as clean, non-
 *  overlapping neighbours.
 *
 *  The label is editable in ☰ Menu setup without a rebuild — section names are
 *  saved site-wide, tab names come from this catalog. */
const PERFORMANCE_KEY = 'performance';
const PERFORMANCE_LABEL = 'Performance';

const DEFAULT_SECTIONS: NavSection[] = [
  { key: PERFORMANCE_KEY, label: PERFORMANCE_LABEL,
    title: 'How the business is doing — briefing, dashboards, ratios and the group view',
    tabs: [
      { ws: 'briefing', label: 'CFO Briefing', title: "The financial situation on one screen — CFO's primary view" },
      { ws: 'dashboard', label: 'Dashboard' },
      { ws: 'health', label: 'Financial Health' },
      { ws: 'classification', label: 'Classification',
        title: 'Tag an account once — COGS, Cash, Investing, Financing, Provisions and your own labels. Financial Health, Cash Flow and Zakat all obey it' },
      { ws: 'group', label: 'Group', title: 'Multi-company group view', needsGroup: true },
    ] },
  { key: 'reports', label: 'Reports', title: 'Financial statements, ledger and visual snapshots',
    tabs: [
      { ws: 'reports', label: 'Financial Reports' },
      { ws: 'general_ledger', label: 'General Ledger' },
      { ws: 'cashflow', label: 'Cash Flow', title: 'Statement of cash flows — operating, investing, financing' },
      { ws: 'ageing', label: 'Ageing (AR/AP)', title: 'Receivables & payables ageing — custom slabs, days or months, Top-N parties' },
      { ws: 'allocation', label: 'Allocation', title: 'Spread a shared cost pool across cost centres by head count, leads or any other driver' },
      { ws: 'visuals', label: 'Visuals' },
    ] },
  { key: 'studio', label: 'Studio', title: 'Self-service BI — query builder, datasets, dashboards',
    tabs: [{ ws: 'studio', label: 'Studio' }] },
  { key: 'compliance', label: 'Compliance', title: 'KSA statutory — VAT and Zakat',
    tabs: [
      { ws: 'vat', label: 'VAT Return' },
      { ws: 'vat_settings', label: 'VAT Settings',
        title: 'VAT control accounts, government deferral rules and period adjustments — everything governing how the return is produced' },
      { ws: 'zakat', label: 'Zakat', title: 'Zakat base estimate — equity method' },
      { ws: 'packs', label: 'Export Packs', title: 'Configurable audit workbooks — VAT registers, GL ledgers, the 16-box return' },
      { ws: 'gst', label: 'GST (India)', title: 'GSTR-3B style summary — output tax vs ITC per head, from the GL tax accounts' },
    ] },
  { key: 'operations', label: 'Operations',
    tabs: [
      { ws: 'bank', label: 'Bank', title: 'Bank slip reader & reconciliation' },
      { ws: 'hr', label: 'People', title: 'People, payroll, accruals & end-of-service' },
    ] },
  // v2.86.0 — own top-level section, deliberately, not a tab under Reports
  // next to the existing indirect 'Cash Flow'. Direct-method budget-vs-
  // actual forecast, own doctypes and API module — see
  // Cash_Flow_Phase2_Spec.md for why this is a separate feature rather than
  // a mode on the existing one.
  { key: 'cash_flow_forecast', label: 'Cash Flow Forecast',
    title: 'Direct-method cash flow — named categories, Budget entered by hand against Actual from GL cash-leg activity, monthly bank-balance rollforward',
    tabs: [{ ws: 'cashflowforecast', label: 'Cash Flow Forecast' }] },
];

/** Merge the saved layout with the built-in catalog: saved order wins, tabs
 *  unknown to this build are dropped, and tabs missing from the saved layout
 *  (added by newer versions) are appended to their default section. */
function applySiteNumFormat(saved: any) {
  try {
    const site = saved?.num_format;
    if (site && !localStorage.getItem('ni-numfmt-user')) localStorage.setItem('ni-numfmt', site);
  } catch { /* ignore */ }
}

/** The layout this section replaced: three top-level entries holding four
 *  tabs. A site that saved a menu before v2.55.1 still has them, and because
 *  a saved layout deliberately wins over the catalog, the new grouping would
 *  otherwise never appear.
 *
 *  So fold them — but only when all three are still exactly as they shipped.
 *  If an admin has moved a tab, renamed a section or hidden something, that
 *  was a decision and we leave it alone; they can merge by hand in Menu setup.
 *  The merged section lands where the earliest of the three sat, so the nav
 *  doesn't reshuffle around the operator. */
const LEGACY_PERF: Record<string, string[]> = {
  briefing: ['briefing'],
  dashboard: ['dashboard'],
  analysis: ['health', 'group'],
};

function foldLegacyPerformance(sections: MenuSectionCfg[]): MenuSectionCfg[] {
  if (sections.some((s) => s.key === PERFORMANCE_KEY)) return sections;
  const legacyKeys = Object.keys(LEGACY_PERF);
  if (!legacyKeys.every((k) => sections.some((s) => s.key === k))) return sections;
  for (const k of legacyKeys) {
    const sec = sections.find((s) => s.key === k)!;
    const have = (sec.tabs || []).map((tb) => tb.ws);
    const want = LEGACY_PERF[k];
    if (have.length !== want.length || want.some((w) => !have.includes(w))) return sections;
  }
  const merged: MenuSectionCfg = {
    key: PERFORMANCE_KEY,
    label: PERFORMANCE_LABEL,
    // Carry each tab's hidden flag across — a tab someone switched off stays off.
    tabs: legacyKeys.flatMap((k) => (sections.find((s) => s.key === k)!.tabs || [])),
  };
  const out: MenuSectionCfg[] = [];
  let placed = false;
  for (const sec of sections) {
    if (!(sec.key in LEGACY_PERF)) { out.push(sec); continue; }
    if (!placed) { out.push(merged); placed = true; }
  }
  return out;
}

function mergeMenu(saved: { sections?: MenuSectionCfg[] } | null): NavSection[] {
  const catalog: Record<string, NavTab & { defSection: string }> = {};
  for (const sec of DEFAULT_SECTIONS) for (const tb of sec.tabs) catalog[tb.ws] = { ...tb, defSection: sec.key };
  if (!saved?.sections?.length) return DEFAULT_SECTIONS;
  const savedSections = foldLegacyPerformance(saved.sections);
  const out: NavSection[] = savedSections.map((s) => ({
    key: s.key, label: s.label || s.key,
    tabs: (s.tabs || [])
      .filter((tb) => !tb.hidden && catalog[tb.ws])
      .map((tb) => catalog[tb.ws]),
  }));
  const placed = new Set<string>(savedSections.flatMap((s) => (s.tabs || []).map((tb) => tb.ws as string)));
  for (const ws of Object.keys(catalog)) {
    if (placed.has(ws)) continue;
    const home = out.find((s) => s.key === catalog[ws].defSection);
    if (home) home.tabs.push(catalog[ws]);
    else out.push({ key: catalog[ws].defSection, label: SECTION_LABELS[catalog[ws].defSection] || catalog[ws].defSection, tabs: [catalog[ws]] });
  }
  return out.filter((s) => s.tabs.length);
}

/** The saved layout re-expressed for the editor — every catalog tab present,
 *  hidden ones flagged, so the modal always edits the complete picture. */
function editableMenu(saved: { sections?: MenuSectionCfg[] } | null): MenuSectionCfg[] {
  const known = new Set<string>();
  const savedSections = saved?.sections?.length ? foldLegacyPerformance(saved.sections) : null;
  const base: MenuSectionCfg[] = (savedSections?.length ? savedSections : DEFAULT_SECTIONS.map((s) => ({
    key: s.key, label: s.label, tabs: s.tabs.map((tb) => ({ ws: tb.ws, hidden: 0 })),
  }))).map((s) => ({ key: s.key, label: s.label, tabs: (s.tabs || []).filter((tb) => {
    const ok = DEFAULT_SECTIONS.some((d) => d.tabs.some((x) => x.ws === tb.ws)) && !known.has(tb.ws);
    if (ok) known.add(tb.ws);
    return ok;
  }).map((tb) => ({ ws: tb.ws, hidden: tb.hidden ? 1 : 0 })) }));
  for (const sec of DEFAULT_SECTIONS) for (const tb of sec.tabs) {
    if (known.has(tb.ws)) continue;
    const home = base.find((s) => s.key === sec.key);
    if (home) home.tabs.push({ ws: tb.ws, hidden: 0 });
    else base.push({ key: sec.key, label: sec.label, tabs: [{ ws: tb.ws, hidden: 0 }] });
    known.add(tb.ws);
  }
  return base;
}

const CATALOG_LABELS: Record<string, string> = Object.fromEntries(
  DEFAULT_SECTIONS.flatMap((s) => s.tabs.map((tb) => [tb.ws, tb.label])));

// v2.86.3 — a NEW section (one a previously-saved site menu has never seen,
// e.g. Cash Flow Forecast on any site that customised its menu before
// v2.86.0 shipped) fell through mergeMenu's auto-append path, which used
// the section's raw KEY ('cash_flow_forecast') as its label instead of its
// real one ('Cash Flow Forecast') — every existing site with a saved
// layout hit this, not just one screenshot's worth. Same shape of bug as
// CATALOG_LABELS existing for tabs but nothing equivalent existed for
// sections.
const SECTION_LABELS: Record<string, string> = Object.fromEntries(
  DEFAULT_SECTIONS.map((s) => [s.key, s.label]));

export default function App() {
  // v1.9.41 — CFO Briefing is the new default landing tab.
  // v1.9.47 — Reports is back to first tab and default landing (per user
  // request). CFO Briefing remains as a primary tab, second position.
  // v2.80.0 — the frontend bundle and the installed Python app are read from
  // different places and can disagree. A deploy that ships Python but serves a
  // stale asset bundle leaves an old UI on a new backend, which silently
  // reinstates bugs that were already fixed — it hid one for a full round of
  // testing, because Frappe's "Installed Apps" shows the Python version while
  // the header shows the bundle, and nothing compared the two.
  const [backendVersion, setBackendVersion] = useState<string | null>(null);
  useEffect(() => {
    api.appVersion()
      .then((r: any) => setBackendVersion(r?.backend || null))
      .catch(() => {});
  }, []);
  const versionMismatch = !!backendVersion && backendVersion !== __APP_VERSION__;

  const [workspace, setWorkspace] = useState<Workspace>('reports');
  const [snapshots, setSnapshots] = useState<any[]>([]);
  const [currentReportName, setCurrentReportName] = useState<string>('');
  // v1.9.47 — replace the old single-flag group-access state with a full
  // access profile. Default to full access on first paint (safe fallback);
  // narrowed once the backend responds.
  const [access, setAccess] = useState<AccessProfile>(DEFAULT_ACCESS);

  // Bilingual EN/AR. Layout stays LTR; only display text is translated.
  const [lang, setLang] = useState<Lang>(getLang());
  const [showConfigBackup, setShowConfigBackup] = useState(false);
  const [showThemePicker, setShowThemePicker] = useState(false);
  // v2.24.1 — two-level navigation: primary SECTIONS on top, and the active
  // section's tabs in an always-visible sub-menu row underneath. Nothing is
  // hidden behind a dropdown; one glance shows the section and the tab.
  // Clicking a section jumps to its last-visited tab (first tab initially).
  const [lastTab, setLastTab] = useState<Record<string, Workspace>>({});
  const [savedMenu, setSavedMenu] = useState<{ sections?: MenuSectionCfg[] } | null>(null);
  const [showMenuSetup, setShowMenuSetup] = useState(false);
  const sections = mergeMenu(savedMenu);
  const loadMenu = () => api.getMenu().then((m) => {
    setSavedMenu(m && m.sections ? m : null);
    // v2.37.1 — site-level number format (region preset) applies unless the
    // user explicitly chose their own in the theme panel.
    try {
      if (m?.num_format && !localStorage.getItem('ni-numfmt-user')) localStorage.setItem('ni-numfmt', m.num_format);
    } catch { /* ignore */ }
  }).catch(() => {});
  useEffect(() => { loadMenu(); }, []);
  // v2.23.0 — apply the persisted colour theme (preset or logo-derived) on boot.
  useEffect(() => { initTheme(); }, []);
  // v2.55.0 — pull the site-wide Brand Kit into localStorage so the print
  // builders (which are synchronous) see the shared letterhead, not this
  // browser's stale copy.
  useEffect(() => { syncBrandFromServer(); }, []);
  const toggleLang = () => {
    const next: Lang = lang === 'en' ? 'ar' : 'en';
    setLangGlobal(next);
    setLang(next);
  };

  // v1.9.52 — global custom Accounting Dimensions filter state. Discovered
  // dimensions are loaded once (in RunTab); filters are read/written by
  // every workspace so a user's filter scope is honoured app-wide.
  const [dimFilters, setDimFilters] = useState<DimensionFiltersMap>({});
  const [dimensions, setDimensions] = useState<Array<{ fieldname: string; label: string; document_type: string }>>([]);

  useEffect(() => {
    let cancelled = false;
    api.insightGetAccessProfile()
      .then((p) => {
        if (cancelled) return;
        setAccess({
          roleTier: p?.role_tier || 'basic',
          canEdit: !!p?.can_edit,
          canSeeGroup: !!p?.can_see_group,
          hrOnly: !!p?.hr_only,
          user: p?.user || '',
        });
      })
      .catch(() => {
        // Endpoint failed — keep the default (full access). Backend still
        // enforces edit permission on writes, so this isn't a security hole.
      });
    return () => { cancelled = true; };
  }, []);

  // Force LTR layout on mount (independent of stored language or any
  // server-side dir the Frappe web template might apply).
  useEffect(() => { setLangGlobal(getLang()); }, []);

  // Load Arabic master-name maps (company + dimensions) so labels can show in
  // Arabic. Display-only; the loaders read Insight AI Settings → Arabic Label
  // Sources. A tick forces a re-render once the maps arrive.
  const [, setLabelTick] = useState(0);
  useEffect(() => {
    Promise.all([
      loadDimensionOptions('company'),
      loadDimensionOptions('cost_center'),
      loadDimensionOptions('project'),
      loadDimensionOptions('department'),
      loadDimensionOptions('branch'),
    ]).then(() => setLabelTick((x) => x + 1)).catch(() => {});
  }, []);

  const hasGroupAccess = access.canSeeGroup;

  return (
    <LangContext.Provider value={{ lang, toggle: toggleLang }}>
    <DimensionFiltersContext.Provider value={{ filters: dimFilters, setFilters: setDimFilters, dimensions, setDimensions }}>
    <AccessContext.Provider value={access}>
    <div className="ni-shell">
      <header className="ni-header">
        <div className="ni-brand">
          <div className="ni-mark" aria-hidden>N</div>
          <div>
            <div className="ni-kicker">Neotec</div>
            <div className="ni-name">Insight <span
              className={'ni-version' + (versionMismatch ? ' ni-version-stale' : '')}
              title={versionMismatch
                ? `This screen is running v${__APP_VERSION__}, but v${backendVersion} is installed on the server. `
                  + 'Hard-refresh (Ctrl+Shift+R). If it persists, the deploy shipped the Python but not the built assets.'
                : 'Frontend bundle version'}>
              v{__APP_VERSION__}{versionMismatch ? ' ⚠' : ''}</span>
            {access.roleTier !== 'admin' && (
              <span className={'ni-role-pill ni-role-' + access.roleTier} title={'Your access level: ' + access.roleTier.toUpperCase()}>
                {access.roleTier === 'cfo' ? 'CFO'
                  : access.roleTier === 'ceo' ? 'CEO'
                  : access.roleTier === 'group_viewer' ? 'Group'
                  : 'View'}
              </span>
            )}
            </div>
          </div>
        </div>
        <CompanyBrand />
        <nav className="ni-ws">
          {sections
            // v2.84.0 — a People-only user sees only People. Filtering here
            // rather than per-section keeps one rule in one place; the backend
            // refuses the other endpoints regardless.
            .map((sec) => access.hrOnly
              ? { ...sec, tabs: sec.tabs.filter((tb: any) => tb.ws === 'hr') }
              : sec)
            .filter((sec) => sec.tabs.length)
            .filter((sec) => !sec.needsGroup || hasGroupAccess).map((sec) => {
            const tabs = sec.tabs.filter((tb) => !tb.needsGroup || hasGroupAccess);
            const isActive = tabs.some((tb) => tb.ws === workspace);
            return (
              <button
                key={sec.key}
                className={'ni-sec' + (isActive ? ' is-active' : '') + (sec.key === 'studio' ? ' ni-sec-studio' : '')}
                onClick={() => setWorkspace(lastTab[sec.key] && tabs.some((tb) => tb.ws === lastTab[sec.key]) ? lastTab[sec.key] : tabs[0].ws)}
                title={sec.title}
              >
                {t(sec.label)}
              </button>
            );
          })}
          <span className="ni-nav-spacer" aria-hidden />
          {access.canEdit && (
            <button className="ws-btn ws-util" onClick={() => setShowMenuSetup(true)} title={t('Menu setup')}>
              ☰
            </button>
          )}
          <button className="ws-btn ws-util" onClick={() => setShowThemePicker(true)} title={t('Colour theme')}>
            <span className="theme-dots" aria-hidden><i /><i /><i /></span>
          </button>
          <button className="ws-btn ws-util" onClick={toggleLang} title="العربية / English">
            {lang === 'en' ? 'العربية' : 'English'}
          </button>
          <button className="ws-btn ws-util" onClick={() => setShowConfigBackup(true)} title={t('Configuration backup')}>
            {t('Backup')}
          </button>
          <BackToErpMenu />
        </nav>
      </header>

      {/* A stale bundle silently reinstates bugs that are already fixed on the
          server, and nothing on screen said so — this is stated plainly rather
          than left in a tooltip, because the person who needs it is the one
          looking at a wrong number. */}
      {versionMismatch && (
        <div className="ni-stale-banner" role="alert">
          <strong>This screen is out of date.</strong>{' '}
          You are viewing <code>v{__APP_VERSION__}</code>, but <code>v{backendVersion}</code> is
          installed on the server. Figures and fixes from the newer version are not active here.
          {' '}Press <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>R</kbd> to reload.
          If it persists, the deploy shipped the app code but not the built assets.
        </div>
      )}

      {(() => {
        const sec = sections.find((x) => x.tabs.some((tb) => tb.ws === workspace));
        if (!sec) return null;
        const tabs = sec.tabs.filter((tb) => !tb.needsGroup || hasGroupAccess);
        if (tabs.length < 2) return null;  // single-tab sections need no sub-row
        return (
          <div className="ni-subnav" role="tablist" aria-label={t(sec.label)}>
            <span className="ni-subnav-sec">{t(sec.label)}</span>
            {tabs.map((tb) => (
              <button
                key={tb.ws}
                role="tab"
                aria-selected={workspace === tb.ws}
                className={'ni-subtab' + (workspace === tb.ws ? ' on' : '')}
                title={tb.title}
                onClick={() => { setWorkspace(tb.ws); setLastTab((m) => ({ ...m, [sec.key]: tb.ws })); }}
              >
                {t(tb.label)}
              </button>
            ))}
          </div>
        );
      })()}

      {showConfigBackup && <ConfigBackupModal onClose={() => setShowConfigBackup(false)} />}
      {showThemePicker && <ThemePicker onClose={() => setShowThemePicker(false)} />}
      {showMenuSetup && (
        <MenuSetupModal initialNumFormat={(savedMenu as any)?.num_format}
          sections={editableMenu(savedMenu)}
          catalogLabels={CATALOG_LABELS}
          onSaved={loadMenu}
          onClose={() => setShowMenuSetup(false)}
        />
      )}

      <main className="ni-main">
        {workspace === 'briefing' && <CfoBriefing />}
        {workspace === 'dashboard' && <DashboardApp />}
        {workspace === 'group' && hasGroupAccess && <GroupApp />}
        {workspace === 'reports' && (
          <ReportsApp
            onPushSnapshot={(snap) => {
              setSnapshots((s) => [...s, snap]);
              setWorkspace('visuals');
            }}
            onSelectedReportChange={setCurrentReportName}
          />
        )}
        {workspace === 'general_ledger' && <LedgerWorkspace reportName={currentReportName} />}
        {workspace === 'allocation' && <AllocationApp />}
        {workspace === 'studio' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading Studio…')}</div>}>
            <StudioApp />
          </Suspense>
        )}
        {workspace === 'health' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading…')}</div>}>
            <FinancialHealth />
          </Suspense>
        )}
        {workspace === 'classification' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading…')}</div>}>
            <ClassificationTab />
          </Suspense>
        )}
        {workspace === 'vat_settings' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading…')}</div>}>
            <VatSettings />
          </Suspense>
        )}
        {workspace === 'vat' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading…')}</div>}>
            <VatReturn />
          </Suspense>
        )}
        {workspace === 'ageing' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading…')}</div>}>
            <AgeingTab />
          </Suspense>
        )}
        {workspace === 'gst' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading…')}</div>}>
            <GstTab />
          </Suspense>
        )}
        {workspace === 'packs' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading…')}</div>}>
            <ExportPacks />
          </Suspense>
        )}
        {workspace === 'zakat' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading…')}</div>}>
            <ZakatTab />
          </Suspense>
        )}
        {workspace === 'cashflow' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading…')}</div>}>
            <CashFlowTab />
          </Suspense>
        )}
        {workspace === 'cashflowforecast' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading…')}</div>}>
            <CashFlowForecastTab />
          </Suspense>
        )}
        {workspace === 'visuals' && (
          <VisualsApp snapshots={snapshots} setSnapshots={setSnapshots} reportName={currentReportName} />
        )}
        {workspace === 'bank' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading…')}</div>}>
            <BankApp />
          </Suspense>
        )}
        {workspace === 'hr' && (
          <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#9a948a' }}>{t('Loading…')}</div>}>
            <HrApp />
          </Suspense>
        )}
      </main>
    </div>
    </AccessContext.Provider>
    </DimensionFiltersContext.Provider>
    </LangContext.Provider>
  );
}
