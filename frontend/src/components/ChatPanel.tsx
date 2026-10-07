'use client';

import { useEffect, useState } from 'react';

import { Monogram, preloadLogos } from '@/components/Monogram';
import { PriceChart } from '@/components/PriceChart';
import { STOCKS } from '@/components/StockJump';
import { historyStillFilling, keyMetrics, shortName } from '@/lib/chatContext';
import {
  citationLink,
  eventDateLabel,
  getInsights,
  type Citation,
  type StockInsights,
} from '@/lib/insights';
import { getPrices, hasPrices, priceLabel, signedPercent, type Prices } from '@/lib/prices';
import { direction } from '@/lib/series';

/**
 * The side panel of the chat page: the stock being discussed (its end-of-day price and its latest
 * full-year figures). It only shows what the
 * server stores; a missing figure is said to be missing, never filled in.
 */

/** What one call to the server gave for one symbol: null while it is still loading. */
type Fetched<T> = { data: T | null; failed: boolean } | null;

/**
 * Load one thing for every stock, once, when the page opens (three stocks, so a few small
 * requests). Switching between the tabs then shows what is already here: nothing is loaded again
 * and nothing collapses to "Loading…" and back, which the owner saw as a flash on every switch.
 */
function useForEveryStock<T>(load: (symbol: string) => Promise<T>): Record<string, Fetched<T>> {
  const [results, setResults] = useState<Record<string, Fetched<T>>>({});
  useEffect(() => {
    let cancelled = false;
    const keep = (symbol: string, data: T | null) => {
      if (!cancelled) setResults((all) => ({ ...all, [symbol]: { data, failed: data === null } }));
    };
    for (const { symbol } of STOCKS) {
      load(symbol).then(
        (data) => keep(symbol, data),
        () => keep(symbol, null),
      );
    }
    return () => {
      cancelled = true;
    };
  }, [load]);
  return results;
}

const TONE_CLASS = { rise: 'rise', fall: 'fall', flat: 'flat' } as const;

/** A short name for where a figure is from, linking to it when the address is safe. */
function SourceChip({ citation }: { citation: Citation }) {
  const page = /p\.(\d+)/.exec(citation.label)?.[1];
  const short =
    citation.source === 'screener'
      ? 'screener.in'
      : citation.source === 'rbi'
        ? 'RBI'
        : page
          ? `Filing p.${page}`
          : 'Filing';
  const link = citationLink(citation);
  return link ? (
    <a
      className="chat-source-chip"
      href={link}
      target="_blank"
      rel="noopener noreferrer"
      title={citation.label}
    >
      {short}
    </a>
  ) : (
    <span className="chat-source-chip" title={citation.label}>
      {short}
    </span>
  );
}

function Overview({ symbol, fetched }: { symbol: string; fetched: Fetched<Prices> }) {
  const stock = STOCKS.find((s) => s.symbol === symbol);
  const title = `${shortName(symbol)} overview`;
  const prices = fetched?.data;

  let body;
  if (fetched === null) {
    body = <p className="muted">Loading prices…</p>;
  } else if (fetched.failed) {
    body = <p className="muted">Prices couldn&apos;t be loaded.</p>;
  } else if (!hasPrices(prices)) {
    body = <p className="muted">Prices not loaded yet</p>;
  } else {
    const latest = prices.latest;
    const percent = signedPercent(latest?.change_pct ?? null);
    const tone = direction(latest?.change_pct == null ? null : Number(latest.change_pct));
    const link = latest ? citationLink(latest.citation) : null;
    const since = prices.history[0]?.date;
    body = (
      <>
        <div className="chat-price-row">
          <strong className="chat-price num">{priceLabel(latest?.close ?? 0)}</strong>
          {percent && <span className={`chat-change ${TONE_CLASS[tone]}`}>{percent}</span>}
          <span className="muted">on the day</span>
        </div>
        <PriceChart
          history={prices.history}
          name={stock?.name ?? symbol}
          changePct={latest?.change_pct ?? null}
        />
        {since && historyStillFilling(prices.history) && (
          <p className="chat-asof muted">
            {`Prices from ${eventDateLabel(since)}: BSE keeps about a month of daily files, so the history grows by a day each trading day.`}
          </p>
        )}
        <p className="chat-asof muted">
          <span>{`As of ${eventDateLabel(latest?.date ?? '')}, BSE end of day`}</span>
          {link && (
            <a href={link} target="_blank" rel="noopener noreferrer" className="doc-link">
              Source
            </a>
          )}
        </p>
      </>
    );
  }

  return (
    <section className="chat-card chat-overview" aria-label={title}>
      <h2>{title}</h2>
      <div className="chat-stock-row">
        <Monogram symbol={symbol} size="lg" />
        <div>
          <strong>{symbol}</strong>
          <p className="muted">{stock?.name ?? symbol}</p>
        </div>
      </div>
      {body}
    </section>
  );
}

function KeyMetrics({ insights }: { insights: Fetched<StockInsights> }) {
  const found = insights?.data ? keyMetrics(insights.data) : null;
  const title = found?.period ? `Key metrics (${found.period})` : 'Key metrics';
  return (
    <section className="chat-card" aria-label={title}>
      <h2>{title}</h2>
      {insights === null ? (
        <p className="muted">Loading…</p>
      ) : insights.failed ? (
        <p className="muted">Key figures couldn&apos;t be loaded.</p>
      ) : !found || found.rows.length === 0 ? (
        <p className="muted">No key figures stored yet.</p>
      ) : (
        <ul className="chat-metrics">
          {found.rows.map((row) => (
            <li key={row.metric}>
              <div>
                <span>{row.label}</span>
                {row.period !== found.period && <span className="muted"> ({row.period})</span>}
                <SourceChip citation={row.fact.citation} />
              </div>
              <strong className="num">{row.value}</strong>
              <span className={`chat-change ${TONE_CLASS[row.tone]}`}>{row.change}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ChatPanel({
  symbol,
  onPick,
}: {
  symbol: string;
  onPick: (symbol: string) => void;
}) {
  const prices = useForEveryStock<Prices>(getPrices);
  const insights = useForEveryStock<StockInsights>(getInsights);
  useEffect(preloadLogos, []);
  return (
    <aside className="chat-panel" aria-label="Stock context">
      <div className="chat-stock-tabs" role="group" aria-label="Stock">
        {STOCKS.map((stock) => (
          <button
            key={stock.symbol}
            type="button"
            className="chat-range"
            aria-pressed={stock.symbol === symbol}
            onClick={() => onPick(stock.symbol)}
          >
            {shortName(stock.symbol)}
          </button>
        ))}
      </div>
      <Overview symbol={symbol} fetched={prices[symbol] ?? null} />
      <KeyMetrics insights={insights[symbol] ?? null} />
    </aside>
  );
}
