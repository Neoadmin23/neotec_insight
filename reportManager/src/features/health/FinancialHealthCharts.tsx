import { useEffect, useRef } from 'react';
import Chart from 'chart.js/auto';
import { t } from '../../utils/i18n';

function useChart(make: () => any, deps: any[]) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const inst = useRef<Chart | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    if (inst.current) inst.current.destroy();
    inst.current = new Chart(ref.current, make());
    return () => { if (inst.current) inst.current.destroy(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return ref;
}

const COLOR = '#1db954';
const lineCfg = (labels: string[], data: any[], label: string, color: string) => ({
  type: 'line' as const,
  data: { labels, datasets: [{ label, data, borderColor: color, backgroundColor: color + '22', fill: true, tension: 0.3, pointRadius: 4, pointBackgroundColor: color }] },
  options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
    scales: { x: { grid: { display: false }, ticks: { font: { size: 10 } } }, y: { ticks: { font: { size: 10 } } } } },
});

export default function FinancialHealthCharts({ data }: { data: any }) {
  const sections = data?.sections || [];
  const trend = data?.trend || [];
  const years = trend.map((y: any) => y.year);

  const radarRef = useChart(() => ({
    type: 'radar',
    data: {
      labels: sections.map((s: any) => t(s.name).replace(/ Health$/, '')),
      datasets: [{ label: t('Section score'), data: sections.map((s: any) => s.score ?? 0),
        backgroundColor: 'rgba(29,185,84,.18)', borderColor: COLOR, pointBackgroundColor: COLOR, borderWidth: 2 }],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { r: { suggestedMin: 0, suggestedMax: 100, ticks: { stepSize: 20, font: { size: 9 }, backdropColor: 'transparent' }, pointLabels: { font: { size: 11 } } } } },
  }), [JSON.stringify(sections.map((s: any) => s.score))]);

  const cur = useChart(() => lineCfg(years, trend.map((y: any) => y.current_ratio), t('Current Ratio'), '#1db954'), [JSON.stringify(trend)]);
  const nm = useChart(() => lineCfg(years, trend.map((y: any) => y.net_margin), t('Net Profit Margin'), '#6c5ce7'), [JSON.stringify(trend)]);
  const roe = useChart(() => lineCfg(years, trend.map((y: any) => y.roe), t('Return on Equity (ROE)'), '#4a8cff'), [JSON.stringify(trend)]);
  const dso = useChart(() => lineCfg(years, trend.map((y: any) => y.dso), t('Days Sales Outstanding (DSO)'), '#ff6a3d'), [JSON.stringify(trend)]);

  return (
    <div className="fh-graph">
      <div className="fh-card fh-radar">
        <div className="fh-card-h">{t('Health profile')}</div>
        <div style={{ height: 320 }}><canvas ref={radarRef} /></div>
      </div>
      {trend.length > 1 && (
        <div className="fh-trends">
          {[['Current Ratio', cur], ['Net Profit Margin', nm], ['Return on Equity (ROE)', roe], ['Days Sales Outstanding (DSO)', dso]].map(([label, ref]: any) => (
            <div className="fh-card" key={label}>
              <div className="fh-card-h">{t(label)}</div>
              <div style={{ height: 160 }}><canvas ref={ref} /></div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
