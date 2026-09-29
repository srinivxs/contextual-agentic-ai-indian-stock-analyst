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
 * A stock's end-of-day closes as a plain SVG area chart with a callout for the latest close. The
 * drawing is hidden from assistive technology behind one name; a visually hidden table holds every
 * close. Nothing is drawn for a missing day.
 */
export function PriceChart({ history, name, changePct }: Props) {
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
  const top = ((spots.at(-1)?.[1] ?? HEIGHT / 2) / HEIGHT) * 100;
  const first = history[0]?.date ?? last.date;
  const dates =
    first === last.date
      ? eventDateLabel(last.date)
      : `${eventDateLabel(first)} to ${eventDateLabel(last.date)}`;
  const spoken = `Share price of ${name}, ₹, end-of-day closes, ${dates}`;
  const percent = signedPercent(changePct);

  return (
    <div className="home-chart price-chart">
      <div className="home-chart-plot">
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
        <div className="home-callout" data-testid="chart-callout" style={{ top: `${top}%` }}>
          <span className="home-callout-period">{eventDateLabel(last.date)}</span>
          <strong className="num">{priceLabel(last.close)}</strong>
          {percent && (
            <span className={`home-change ${direction(Number(changePct))}`}>
              {percent} on the day
            </span>
          )}
        </div>
        <span className="home-chart-dot" style={{ top: `${top}%` }} />
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
