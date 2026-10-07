'use client';

import { acrossPercent, calloutSide, useChartHover } from '@/components/chartHover';
import { linePoints } from '@/components/Sparkline';
import { change, direction, percentLabel, rupees, type SeriesPoint } from '@/lib/series';

const WIDTH = 600;
const HEIGHT = 240;
const PAD = 44;

type Props = {
  /** Oldest first, as the server sends them. */
  points: SeriesPoint[];
  /** The company's name, for the chart's spoken name. */
  name: string;
  /** "Net profit". */
  label: string;
};

/**
 * A stock's figure over the years as a plain SVG area chart, with a floating callout for the latest
 * year. The drawing is hidden from assistive technology behind one name; a visually hidden table
 * holds every point, so the numbers are readable without seeing the line.
 */
export function ProfitChart({ points, name, label }: Props) {
  const hover = useChartHover(points.length);
  if (points.length === 0) {
    return (
      <p className="muted home-chart-empty">
        No {label.toLowerCase()} figures stored yet for {name}.
      </p>
    );
  }

  const values = points.map((p) => Number(p.value));
  const spots = linePoints(values, WIDTH, HEIGHT, PAD);
  const line = spots.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const area = `0,${HEIGHT} ${line} ${WIDTH},${HEIGHT}`;
  // The latest year, or the year under the mouse, with its change on the year before.
  const index = hover.hovered ?? points.length - 1;
  const spot = spots[index] ?? [WIDTH, HEIGHT / 2]; // one year: no line, just the figure
  const latest = change(points.slice(0, index + 1));
  const across = acrossPercent(index, points.length);
  const percent = latest ? percentLabel(latest.percent) : null;
  const first = points[0]?.period ?? '';
  const end = points.at(-1)?.period ?? '';
  const spoken = `${label} of ${name}, ₹ crore, ${first === end ? first : `${first} to ${end}`}`;

  return (
    <div className="home-chart">
      <div
        className={spots.length > 0 ? 'home-chart-plot hoverable' : 'home-chart-plot'}
        data-testid="chart-plot"
        onMouseMove={hover.onMouseMove}
        onMouseLeave={hover.onMouseLeave}
      >
        {spots.length > 0 && (
          <svg
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            preserveAspectRatio="none"
            role="img"
            aria-label={spoken}
            focusable="false"
          >
            <polygon className="home-chart-fill" points={area} />
            <polyline className="home-chart-line" points={line} fill="none" />
          </svg>
        )}
        {hover.hovered !== null && <span className="chart-guide" style={{ left: `${across}%` }} />}
        {latest && (
          <div
            className={`home-callout ${calloutSide(across)}`}
            data-testid="chart-callout"
            style={{ left: `${across}%`, top: `${(spot[1] / HEIGHT) * 100}%` }}
          >
            <span className="home-callout-period">{latest.period}</span>
            <strong className="num">{rupees(latest.value)} crore</strong>
            {percent && latest.previousPeriod && (
              <span className={`home-change ${direction(latest.percent)}`}>
                {percent} on {latest.previousPeriod}
              </span>
            )}
          </div>
        )}
        <span
          className="home-chart-dot"
          style={{ left: `${across}%`, top: `${(spot[1] / HEIGHT) * 100}%` }}
        />
      </div>
      <ol className="home-chart-years" aria-hidden="true">
        {points.map((p) => (
          <li key={p.period}>{p.period}</li>
        ))}
      </ol>
      <div className="visually-hidden">
        <table aria-label={spoken}>
          <thead>
            <tr>
              <th scope="col">Period</th>
              <th scope="col">{label}, ₹ crore</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p) => (
              <tr key={p.period}>
                <th scope="row">{p.period}</th>
                <td>{rupees(Number(p.value))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
