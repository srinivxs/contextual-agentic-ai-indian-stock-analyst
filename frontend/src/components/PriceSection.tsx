/**
 * The share price card of the stock page: the latest end-of-day close, a year of adjusted closes,
 * returns, volatility, P/E and dividend yield, and any bonus issue or split. Each figure is shown as
 * the server sent it; a missing one says why, never a zero.
 */

import { CitationChip } from '@/components/InsightSections';
import { PriceChart } from '@/components/PriceChart';
import { eventDateLabel } from '@/lib/insights';
import {
  actionLine,
  priceLabel,
  RETURN_KEYS,
  signedPercent,
  type PriceRatio,
  type Prices,
  type ReturnKey,
} from '@/lib/prices';
import { direction } from '@/lib/series';

const RETURN_LABELS: Record<ReturnKey, string> = {
  '1m': '1 month',
  '3m': '3 months',
  '6m': '6 months',
  '1y': '1 year',
};

const NOT_ENOUGH = 'not enough history';

const toneOf = (percent: string | null): string =>
  direction(percent === null ? null : Number(percent));

/** P/E or dividend yield: the value when there is one, else the state and always the reason. */
function RatioItem({ label, ratio, unit }: { label: string; ratio: PriceRatio; unit: string }) {
  const known = ratio.status === 'ok' && ratio.value !== null;
  return (
    <li className="price-stat">
      <span className="price-stat-name">{label}</span>
      <span className={known ? 'price-stat-value num' : 'price-stat-value muted'}>
        {known
          ? `${ratio.value}${unit}`
          : ratio.status === 'not_assessable'
            ? 'Not assessable'
            : 'Not available'}
      </span>
      <span className="price-stat-reason muted">{ratio.reason}</span>
      {ratio.citations.length > 0 && (
        <span className="chips">
          {ratio.citations.map((citation, index) => (
            <CitationChip key={`${index}-${citation.label}`} citation={citation} icon />
          ))}
        </span>
      )}
    </li>
  );
}

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
    const { latest, history, returns, volatility_1y: volatility, actions } = state.prices;
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
        <ul className="price-stats" aria-label="Returns">
          {RETURN_KEYS.map((key) => {
            const value = returns[key];
            const text = signedPercent(value);
            return (
              <li key={key} className="price-stat">
                <span className="price-stat-name">{RETURN_LABELS[key]}</span>
                {text ? (
                  <span className={`price-stat-value num home-change ${toneOf(value)}`}>
                    {text}
                  </span>
                ) : (
                  <span className="price-stat-value muted">{NOT_ENOUGH}</span>
                )}
              </li>
            );
          })}
        </ul>
        <ul className="price-stats" aria-label="Risk and valuation">
          <li className="price-stat">
            <span className="price-stat-name">Volatility, 1 year</span>
            {volatility === null ? (
              <span className="price-stat-value muted">{NOT_ENOUGH}</span>
            ) : (
              <span className="price-stat-value num">{`${volatility}%`}</span>
            )}
          </li>
          <RatioItem label="P/E" ratio={state.prices.pe} unit="" />
          <RatioItem label="Dividend yield" ratio={state.prices.dividend_yield} unit="%" />
        </ul>
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
