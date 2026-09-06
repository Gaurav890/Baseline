import type { DashboardData } from "@/lib/types";

export function TrendChart({ data }: { data: DashboardData["trend"] }) {
  const width = 720;
  const height = 210;
  const pad = { x: 18, top: 18, bottom: 28 };
  const values = data.length ? data : [{ date: "Now", quality: 0, latency_ms: 0, runs: 0 }];
  const x = (index: number) =>
    pad.x + (index * (width - pad.x * 2)) / Math.max(1, values.length - 1);
  const y = (value: number) => pad.top + ((1 - value) / 0.18) * (height - pad.top - pad.bottom);
  const qualityPath = values.map((item, index) => `${x(index)},${y(item.quality)}`).join(" ");
  const areaPath = `${x(0)},${height - pad.bottom} ${qualityPath} ${x(values.length - 1)},${height - pad.bottom}`;

  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Quality score trend">
        <defs>
          <linearGradient id="quality-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#ff6b35" stopOpacity=".26" />
            <stop offset="100%" stopColor="#ff6b35" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.85, 0.9, 0.95, 1].map((tick) => (
          <g key={tick}>
            <line x1="18" x2="702" y1={y(tick)} y2={y(tick)} className="chart-grid" />
            <text x="18" y={y(tick) - 6} className="chart-label">
              {Math.round(tick * 100)}%
            </text>
          </g>
        ))}
        <polygon points={areaPath} fill="url(#quality-fill)" />
        <polyline points={qualityPath} className="chart-line" />
        {values.map((item, index) => (
          <g key={`${item.date}-${index}`}>
            <circle cx={x(index)} cy={y(item.quality)} r="3.5" className="chart-point" />
            {(index === 0 || index === values.length - 1 || index % 2 === 1) && (
              <text x={x(index)} y={height - 7} textAnchor="middle" className="chart-date">
                {item.date.replace("2026-", "")}
              </text>
            )}
          </g>
        ))}
      </svg>
    </div>
  );
}

