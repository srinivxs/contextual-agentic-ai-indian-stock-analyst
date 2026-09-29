/**
 * The share price card of the stock page: the latest end-of-day close and its day change, the stored
 * adjusted closes, and any bonus issue or split. Valuation now lives in the Fundamentals card
 * (the owner, 2026-09-30: returns, volatility, P/E and dividend yield were removed from here).
 */

import { CitationChip } from '@/components/InsightSections';
import { PriceChart } from '@/components/PriceChart';
import { eventDateLabel } from '@/lib/insights';
import { actionLine, priceLabel, signedPercent, type Prices } from '@/lib/prices';
import { direction } from '@/lib/series';

const toneOf = (percent: string | null): string =>
  direction(percent === null ? null : Number(percent));

export type PriceState =
  { phase: 'loading' } | { phase: 'problem' } | { phase: 'ready'; prices: Prices };

export function PriceSection({ state, name }: { state: PriceState; name: string }) {
  let body;
  if (state.phase === 'loading') {
    body = <p className="muted">Loading share price…</p>;
  } else if (state.phase === 'problem') {
    body = (
      <p className="muted">{`We couldn't load the share price. Reload the page to try again.`}</p>
    );
  } else if (state.prices.latest === null) {
    body = <p className="muted">Prices not loaded yet</p>;
  } else {
    const { latest, history, actions } = state.prices;
    const day = signedPercent(latest.change_pct);
    body = (
      <>
        <p className="price-now">
          <span className="price-close num">{priceLabel(latest.close)}</span>
          <span className="muted">close on {eventDateLabel(latest.date)}</span>
          {day && (
            <span
              className={`home-change ${toneOf(latest.change_pct)}`}
            >{`${day} on the day`}</span>
          )}
        </p>
        <PriceChart history={history} name={name} changePct={latest.change_pct} />
        {actions.length > 0 && (
          <ul className="price-actions">
            {actions.map((action) => (
              <li key={action.date}>{actionLine(action)}</li>
            ))}
          </ul>
        )}
        <p className="price-source">
          <CitationChip citation={latest.citation} icon />
        </p>
      </>
    );
  }
  return (
    <section aria-labelledby="share-price" className="stock-section">
      <h2 id="share-price">Share price</h2>
      <div className="panel price-card">
        {body}
        <p className="muted price-note">
          End-of-day prices from BSE&apos;s daily price files. Not live, not investment advice.
        </p>
      </div>
    </section>
  );
}
