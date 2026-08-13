import { useEffect, useRef } from 'react';
import Chart from 'chart.js/auto';

export interface ChartConfig {
  type: string;          // bar | line | area | hbar | stacked | pie | donut
  category?: string;     // for flat results: the label field
  measures?: string[];   // numeric field keys to plot
}

const PALETTE = ['#6c5ce7', '#1fb6a6', '#ff6a3d', '#ffd15c', '#4a8cff', '#e84393', '#00b894', '#a55eea', '#fd9644', '#26de81'];

/** Derive {labels, datasets} from a Studio run result for charting. */
export function deriveChartData(result: any, cfg: ChartConfig) {
  const pieish = cfg.type === 'pie' || cfg.type === 'donut';
  // 1) Pivot → categories = rows, series = columns
  if (result?.pivot) {
    const p = result.pivot;
    const labels = p.rows.map((r: any) => r.key);
    if (p.single || !p.columns.length) {
      return { labels, datasets: [{ label: p.value_label, data: p.rows.map((r: any) => r.total),
        backgroundColor: pieish ? labels.map((_: any, i: number) => PALETTE[i % PALETTE.length]) : PALETTE[0],
        borderColor: PALETTE[0], fill: cfg.type === 'area' }] };
    }
    if (pieish) {
      return { labels, datasets: [{ label: p.value_label, data: p.rows.map((r: any) => r.total), backgroundColor: labels.map((_: any, i: number) => PALETTE[i % PALETTE.length]) }] };
    }
    const datasets = p.columns.map((col: string, i: number) => ({
      label: col, data: p.rows.map((r: any) => r.cells[col] ?? 0),
      backgroundColor: PALETTE[i % PALETTE.length], borderColor: PALETTE[i % PALETTE.length], fill: cfg.type === 'area',
    }));
    return { labels, datasets };
  }
  // 2) Grouped → categories = group keys, measures = numeric columns' subtotals
  if (result?.groups) {
    const labels = result.groups.map((g: any) => g.key);
    const numeric = (result.columns || []).filter((c: any) => c.numeric);
    const measures = (cfg.measures && cfg.measures.length ? cfg.measures : numeric.slice(0, 1).map((c: any) => c.field));
    const sub = (g: any) => g.net_subtotal || g.subtotal || {};
    const datasets = measures.map((m: string, i: number) => {
      const c = numeric.find((x: any) => x.field === m);
      return { label: c?.label || m, data: result.groups.map((g: any) => sub(g)[m] ?? 0),
        backgroundColor: pieish ? labels.map((_: any, j: number) => PALETTE[j % PALETTE.length]) : PALETTE[i % PALETTE.length],
        borderColor: PALETTE[i % PALETTE.length], fill: cfg.type === 'area' };
    });
    return { labels, datasets: pieish ? datasets.slice(0, 1) : datasets };
  }
  // 3) Flat → aggregate (sum) measures by the chosen category
  const rows = result?.rows || [];
  const cat = cfg.category || (result?.columns || []).find((c: any) => !c.numeric)?.field;
  const numeric = (result?.columns || []).filter((c: any) => c.numeric);
  const measures = (cfg.measures && cfg.measures.length ? cfg.measures : numeric.slice(0, 1).map((c: any) => c.field));
  const agg: Record<string, Record<string, number>> = {};
  const labels: string[] = [];
  for (const r of rows) {
    const key = String(r[cat] ?? '—');
    if (!(key in agg)) { agg[key] = {}; labels.push(key); }
    for (const m of measures) agg[key][m] = (agg[key][m] || 0) + (Number(r[m]) || 0);
  }
  const datasets = measures.map((m: string, i: number) => {
    const c = numeric.find((x: any) => x.field === m);
    return { label: c?.label || m, data: labels.map((k) => agg[k][m] || 0),
      backgroundColor: pieish ? labels.map((_, j) => PALETTE[j % PALETTE.length]) : PALETTE[i % PALETTE.length],
      borderColor: PALETTE[i % PALETTE.length], fill: cfg.type === 'area' };
  });
  return { labels, datasets: pieish ? datasets.slice(0, 1) : datasets };
}

export default function StudioChart({ result, cfg, height, onPick }: { result: any; cfg: ChartConfig; height?: number; onPick?: (label: string) => void }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const chartRef = useRef<Chart | null>(null);

  useEffect(() => {
    if (!ref.current || !result) return;
    if (chartRef.current) chartRef.current.destroy();
    const { labels, datasets } = deriveChartData(result, cfg);
    const isPie = cfg.type === 'pie' || cfg.type === 'donut';
    const base = cfg.type === 'hbar' ? 'bar' : cfg.type === 'area' ? 'line' : cfg.type === 'stacked' ? 'bar' : cfg.type;
    const cjsType = (isPie ? cfg.type : base) as any;
    chartRef.current = new Chart(ref.current, {
      type: cjsType,
      data: { labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        // Drill-through: clicking a bar/slice/point passes its category label up.
        onClick: (_evt: any, elements: any[]) => {
          if (!onPick || !elements?.length) return;
          const lbl = labels[elements[0].index];
          if (lbl != null) onPick(String(lbl));
        },
        indexAxis: cfg.type === 'hbar' ? 'y' : 'x',
        plugins: {
          legend: { display: datasets.length > 1 || isPie, position: 'bottom', labels: { font: { size: 10 }, boxWidth: 10, padding: 8 } },
          tooltip: { enabled: true },
        },
        scales: isPie ? {} : {
          x: { stacked: cfg.type === 'stacked', grid: { display: false }, ticks: { font: { size: 10 } } },
          y: { stacked: cfg.type === 'stacked', ticks: { font: { size: 10 } } },
        },
      },
    });
    return () => { if (chartRef.current) chartRef.current.destroy(); };
  }, [result, cfg.type, JSON.stringify(cfg.measures), cfg.category]);  // onPick intentionally omitted — it only calls stable setters

  return <div style={{ position: 'relative', height: height || 360 }}><canvas ref={ref} role="img" aria-label="chart" /></div>;
}
