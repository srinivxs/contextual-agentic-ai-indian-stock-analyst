'use client';

import { acrossPercent, calloutSide, useChartHover } from '@/components/chartHover';
import { linePoints } from '@/components/Sparkline';
import { eventDateLabel } from '@/lib/insights';
import { priceLabel, signedPercent, type PricePoint } from '@/lib/prices';
import { direction } from '@/lib/series';

const WIDTH = 600;
const HEIGHT = 240;
const PAD = 30;

type Props = {
  /** Adjusted closes, oldest first, as the server sends them. */
  history: PricePoint[];
  /** The company's name, for the chart's spoken name. */
  name: string;
  /** The latest day's change in percent, as the server sends it; null when unknown. */
  changePct: string | null;
};

/**
 * A stock's end-of-day closes as a plain SVG area chart with a callout for the latest close, or for
 * the day under the mouse (with its change on the day before). The drawing is hidden from
 * assistive technology behind one name; a visually hidden table holds every close. Nothing is
 * drawn for a missing day.
 */
export function PriceChart({ history, name, changePct }: Props) {
  const hover = useChartHover(history.length);
  const last = history.at(-1);
  if (!last) {
    return <p className="muted home-chart-empty">No share prices stored yet for {name}.</p>;
  }
  const spots = linePoints(
    history.map((p) => Number(p.close)),
    WIDTH,
    HEIGHT,
    PAD,
  );
  const line = spots.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const area = `0,${HEIGHT} ${line} ${WIDTH},${HEIGHT}`;
  const index = hover.hovered ?? history.length - 1;
  const shown = history[index] ?? last;
  const before = index > 0 ? history[index - 1] : undefined;
  // The latest day keeps the server's change; any other day is compared with the day before it.
  const dayChange =
    hover.hovered === null
      ? changePct
      : before
        ? String((Number(shown.close) / Number(before.close) - 1) * 100)
        : null;
  const top = ((spots[index]?.[1] ?? HEIGHT / 2) / HEIGHT) * 100;
  const across = acrossPercent(index, history.length);
  const first = history[0]?.date ?? last.date;
  const dates =
    first === last.date
      ? eventDateLabel(last.date)
      : `${eventDateLabel(first)} to ${eventDateLabel(last.date)}`;
  const spoken = `Share price of ${name}, ₹, end-of-day closes, ${dates}`;
  const percent = signedPercent(dayChange);

  return (
    <div className="home-chart price-chart">
      <div
        className={[
          'home-chart-plot',
          spots.length > 0 && 'hoverable',
          hover.hovered !== null && 'hovering',
        ]
          .filter(Boolean)
          .join(' ')}
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
            <polyline className="home-chart-line price-line" points={line} fill="none" />
          </svg>
        )}
        {hover.hovered !== null && <span className="chart-guide" style={{ left: `${across}%` }} />}
        <div
          className={`home-callout ${calloutSide(across)}`}
          data-testid="chart-callout"
          style={{ left: `${across}%`, top: `${top}%` }}
        >
          <span className="home-callout-period">{eventDateLabel(shown.date)}</span>
          <strong className="num">{priceLabel(shown.close)}</strong>
          {percent && (
            <span className={`home-change ${direction(Number(dayChange))}`}>
              {percent} on the day
            </span>
          )}
        </div>
        <span className="home-chart-dot" style={{ left: `${across}%`, top: `${top}%` }} />
      </div>
      {spots.length > 0 && (
        <p className="home-chart-years price-chart-dates" aria-hidden="true">
          <span>{eventDateLabel(first)}</span>
          <span>{eventDateLabel(last.date)}</span>
        </p>
      )}
      <div className="visually-hidden">
        <table aria-label={spoken}>
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Close, ₹</th>
            </tr>
          </thead>
          <tbody>
            {history.map((p) => (
              <tr key={p.date}>
                <th scope="row">{eventDateLabel(p.date)}</th>
                <td>{priceLabel(p.close)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
