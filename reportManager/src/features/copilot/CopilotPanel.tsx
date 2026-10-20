import { useEffect, useMemo, useRef, useState } from 'react';
import Chart from 'chart.js/auto';
import { api } from '../../utils/api';
import { getLang, t } from '../../utils/i18n';
import { Markdown } from '../../components/Markdown';
import { runAiJob } from '../../utils/aiJob';

/* Insight Copilot (v2.94.0) — ask anything, on any screen.
 * The server picks a read-only tool, computes the figures with the user's own
 * permissions, and returns text + a table and/or a chart. Nothing here does
 * arithmetic on the answer; it only displays what the server computed. */

type Col = { key: string; label: string; type?: string };
type Answer = {
  ok: boolean; question?: string; title?: string; answer: string; view?: string; columns?: Col[];
  rows?: any[]; chart?: { type: 'bar' | 'line' | 'pie'; labels: string[]; series: { name: string; data: number[] }[] } | null;
  suggestions?: string[]; from_date?: string; to_date?: string; company?: string; source?: string;
  report_config?: any;
  sql?: string; source_name?: string;   // v2.95.0 — answers from an outside database
};
type Turn = { id: number; q: string; a?: Answer; err?: string; secs?: number };
type Period = 'fy' | 'last_fy' | 'quarter' | '30d';

const PALETTE = ['#2563eb', '#16a34a', '#d97706', '#dc2626', '#7c3aed', '#0891b2', '#db2777', '#65a30d'];

function periodDates(p: Period): { from_date?: string; to_date?: string } {
  const d = new Date();
  const iso = (x: Date) => x.toISOString().slice(0, 10);
  if (p === '30d') { const f = new Date(d); f.setDate(f.getDate() - 30); return { from_date: iso(f), to_date: iso(d) }; }
  if (p === 'quarter') { const q = Math.floor(d.getMonth() / 3) * 3; return { from_date: iso(new Date(d.getFullYear(), q, 1)), to_date: iso(d) }; }
  if (p === 'last_fy') { const y = d.getFullYear() - 1; return { from_date: `${y}-01-01`, to_date: `${y}-12-31` }; }
  return {};   // server default: fiscal year to date
}

function fmt(v: any, type?: string) {
  if (v === null || v === undefined || v === '') return '—';
  if (type === 'currency' || type === 'number' || type === 'int' || typeof v === 'number') {
    const n = Number(v);
    if (Number.isFinite(n)) return n.toLocaleString(undefined, { maximumFractionDigits: type === 'int' ? 0 : 2 });
  }
  return String(v);
}

function toCsv(cols: Col[], rows: any[]) {
  const esc = (x: any) => { const s = x === null || x === undefined ? '' : String(x); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return [cols.map((c) => esc(c.label)).join(','), ...rows.map((r) => cols.map((c) => esc(r[c.key])).join(','))].join('\n');
}

function AnswerChart({ chart }: { chart: NonNullable<Answer['chart']> }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const pie = chart.type === 'pie';
    const c = new Chart(ref.current, {
      type: chart.type,
      data: {
        labels: chart.labels,
        datasets: chart.series.map((s, i) => ({
          label: s.name, data: s.data,
          backgroundColor: pie ? chart.labels.map((_l, j) => PALETTE[j % PALETTE.length]) : PALETTE[i % PALETTE.length],
          borderColor: PALETTE[i % PALETTE.length], fill: false, tension: 0.25,
        })),
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: pie || chart.series.length > 1 } } },
    });
    return () => c.destroy();
  }, [chart]);
  return <div className="cp-chart"><canvas ref={ref} /></div>;
}

function AnswerCard({ turn, onAsk }: { turn: Turn; onAsk: (q: string) => void }) {
  const a = turn.a;
  const [mode, setMode] = useState<'chart' | 'table'>(a?.chart ? 'chart' : 'table');
  const [saved, setSaved] = useState('');
  const [saving, setSaving] = useState(false);
  if (turn.err) return <div className="cp-a cp-err">{turn.err}</div>;
  if (!a) return <div className="cp-a cp-thinking">{t('Working it out…')}{turn.secs && turn.secs > 4 ? ` ${turn.secs}s` : ''}</div>;
  const cols = a.columns || [];
  const rows = a.rows || [];
  return (
    <div className="cp-a">
      {a.title && <div className="cp-title">{a.title}{a.from_date ? <span className="cp-sub"> · {a.from_date} → {a.to_date}</span> : null}</div>}
      <Markdown className="cp-md" text={a.answer} />
      {!!rows.length && (
        <>
          <div className="cp-tools">
            {a.chart && (
              <div className="cp-seg" role="tablist">
                <button type="button" className={mode === 'chart' ? 'on' : ''} onClick={() => setMode('chart')}>{t('Chart')}</button>
                <button type="button" className={mode === 'table' ? 'on' : ''} onClick={() => setMode('table')}>{t('Table')}</button>
              </div>
            )}
            <button type="button" className="cp-link" onClick={() => {
              const blob = new Blob(['\ufeff' + toCsv(cols, rows)], { type: 'text/csv;charset=utf-8' });
              const url = URL.createObjectURL(blob);
              const el = document.createElement('a');
              el.href = url; el.download = (a.title || 'insight').replace(/[^\w-]+/g, '_') + '.csv'; el.click();
              URL.revokeObjectURL(url);
            }}>{t('Export CSV')}</button>
            {a.sql && a.source_name && (
              <button type="button" className="cp-link" disabled={saving || !!saved} onClick={async () => {
                const title = prompt(t('Report title'), a.title || '');
                if (!title) return;
                setSaving(true);
                try {
                  await api.sourceSaveReport({ title, source: a.source_name!, sql: a.sql!, question: turn.q, view: a.view });
                  setSaved(title);
                } catch (e: any) { alert(e?.message || String(e)); }
                finally { setSaving(false); }
              }}>{saved ? `✓ ${t('Saved')}: ${saved}` : t('Save as report')}</button>
            )}
            {a.report_config && (
              <button type="button" className="cp-link" disabled={saving || !!saved} onClick={async () => {
                const title = prompt(t('Report title'), a.title || '');
                if (!title) return;
                setSaving(true);
                try { const r = await api.copilotSaveReport(title, a.report_config); setSaved(r?.title || title); }
                catch (e: any) { alert(e?.message || String(e)); }
                finally { setSaving(false); }
              }}>{saved ? `✓ ${t('Saved to Studio')}: ${saved}` : t('Save as report')}</button>
            )}
          </div>
          {mode === 'chart' && a.chart ? <AnswerChart chart={a.chart} /> : (
            <div className="cp-table-wrap">
              <table className="cp-table">
                <thead><tr>{cols.map((c) => <th key={c.key} className={c.type === 'text' ? '' : 'num'}>{c.label}</th>)}</tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i}>{cols.map((c) => <td key={c.key} className={c.type === 'text' ? '' : 'num'}>{fmt(r[c.key], c.type)}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      {a.sql && (
        <details className="cp-sql">
          <summary>{t('Query used')} · {a.source_name}</summary>
          <pre>{a.sql}</pre>
        </details>
      )}
      {!!a.suggestions?.length && (
        <div className="cp-sugg">
          {a.suggestions.map((s) => <button type="button" key={s} onClick={() => onAsk(s)}>{s}</button>)}
        </div>
      )}
    </div>
  );
}

export function CopilotPanel() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [period, setPeriod] = useState<Period>('fy');
  const [ex, setEx] = useState<{ ai: boolean; examples: string[] } | null>(null);
  const [sources, setSources] = useState<{ name: string; db_type: string }[]>([]);
  const [source, setSource] = useState('');
  const [saved, setSaved] = useState<{ name: string; source: string }[]>([]);
  const seq = useRef(0);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setOpen((o) => !o); }
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => {
    if (open && !ex) {
      api.copilotExamples().then(setEx).catch(() => setEx({ ai: false, examples: [] }));
      api.sourcesList().then(setSources).catch(() => setSources([]));
      api.sourceReports().then(setSaved).catch(() => setSaved([]));
    }
    if (open) setTimeout(() => inputRef.current?.focus(), 50);
  }, [open, ex]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [turns]);

  const periodLabel = useMemo(() => ({ fy: t('This year'), last_fy: t('Last year'), quarter: t('This quarter'), '30d': t('Last 30 days') }), []);

  async function ask(text: string) {
    const question = text.trim();
    if (!question || busy) return;
    const id = ++seq.current;
    setTurns((ts) => [...ts, { id, q: question }]);
    setQ('');
    setBusy(true);
    try {
      // v2.95.1 — background job + polling (no gateway time limit).
      const a = await runAiJob<Answer>(
        () => api.copilotStartAsk(question, { ...periodDates(period), lang: getLang(), source }),
        (secs) => setTurns((ts) => ts.map((x) => (x.id === id && !x.a ? { ...x, secs } : x))));
      setTurns((ts) => ts.map((x) => (x.id === id ? { ...x, a } : x)));
    } catch (e: any) {
      setTurns((ts) => ts.map((x) => (x.id === id ? { ...x, err: e?.message || String(e) } : x)));
    } finally {
      setBusy(false);
    }
  }

  async function runSaved(name: string) {
    if (busy) return;
    const id = ++seq.current;
    setTurns((ts) => [...ts, { id, q: `📄 ${name}` }]);
    setBusy(true);
    try {
      const a = await api.sourceRunReport(name);
      setTurns((ts) => ts.map((x) => (x.id === id ? { ...x, a } : x)));
    } catch (e: any) {
      setTurns((ts) => ts.map((x) => (x.id === id ? { ...x, err: e?.message || String(e) } : x)));
    } finally { setBusy(false); }
  }

  return (
    <>
      {!open && (
        <button type="button" className="cp-fab" onClick={() => setOpen(true)} title={t('Ask Insight (Ctrl+K)')}>
          ✨ {t('Ask Insight')}
        </button>
      )}
      {open && (
        <aside className="cp-panel" role="dialog" aria-label={t('Insight Copilot')}>
          <div className="cp-head">
            <div>
              <div className="cp-h">✨ {t('Insight Copilot')}</div>
              <div className="cp-sub">{ex && !ex.ai ? t('AI is off — answering common questions only') : t('Answers from your books, with your permissions')}</div>
            </div>
            <div className="cp-head-tools">
              {!!sources.length && (
                <select value={source} onChange={(e) => setSource(e.target.value)} aria-label={t('Data')}
                  title={t('Which data to ask about')}>
                  <option value="">{t('This ERPNext site')}</option>
                  {sources.map((s) => <option key={s.name} value={s.name}>{s.name} ({s.db_type})</option>)}
                </select>
              )}
              <select value={period} onChange={(e) => setPeriod(e.target.value as Period)} aria-label={t('Period')}
                disabled={!!source} title={source ? t('The period applies to ERPNext questions; say the dates in your question for an outside database') : ''}>
                {(Object.keys(periodLabel) as Period[]).map((p) => <option key={p} value={p}>{periodLabel[p]}</option>)}
              </select>
              {!!turns.length && <button type="button" className="cp-link" onClick={() => setTurns([])}>{t('Clear')}</button>}
              <button type="button" className="cp-x" onClick={() => setOpen(false)} aria-label={t('Close')}>×</button>
            </div>
          </div>
          <div className="cp-body">
            {!turns.length && (
              <div className="cp-empty">
                <p>{t('Ask about your figures in plain language. You get an answer, a table or a chart, and ideas for what to look at next.')}</p>
                {!source && (
                  <div className="cp-sugg">
                    {(ex?.examples || []).map((s) => <button type="button" key={s} onClick={() => ask(s)}>{s}</button>)}
                  </div>
                )}
                {source && <p>{t('Ask about the tables in')} <b>{source}</b>. {t('The query used is shown with every answer.')}</p>}
                {!!saved.length && (
                  <>
                    <div className="cp-sub" style={{ marginTop: 12 }}>{t('Saved reports')}</div>
                    <div className="cp-sugg">
                      {saved.map((r) => <button type="button" key={r.name} onClick={() => runSaved(r.name)}>📄 {r.name}</button>)}
                    </div>
                  </>
                )}
              </div>
            )}
            {turns.map((tn) => (
              <div key={tn.id} className="cp-turn">
                <div className="cp-q">{tn.q}</div>
                <AnswerCard turn={tn} onAsk={ask} />
              </div>
            ))}
            <div ref={endRef} />
          </div>
          <form className="cp-input" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
            <textarea ref={inputRef} rows={2} value={q} placeholder={t('e.g. Top 10 customers by sales this year')}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(q); } }} />
            <button type="submit" className="cp-send" disabled={busy || !q.trim()}>{busy ? '…' : t('Ask')}</button>
          </form>
        </aside>
      )}
    </>
  );
}
