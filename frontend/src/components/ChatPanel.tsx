'use client';

import { useEffect, useState, type ReactNode } from 'react';

import {
  ArrowRightIcon,
  BarsIcon,
  CoinsIcon,
  ExternalIcon,
  PercentIcon,
  TrendIcon,
} from '@/components/Icons';
import { Monogram, preloadLogos } from '@/components/Monogram';
import { PriceChart } from '@/components/PriceChart';
import { STOCKS } from '@/components/StockJump';
import { keyMetrics, shortName } from '@/lib/chatContext';
import {
  citationLink,
  eventDateLabel,
  getInsights,
  stockPageHref,
  type Citation,
  type Metric,
  type StockInsights,
} from '@/lib/insights';
import { getPrices, hasPrices, priceLabel, signedPercent, type Prices } from '@/lib/prices';
import { direction } from '@/lib/series';

/**
 * The stock beside the chat (the owner's layout, 2026-10-08: the chat is the main feature, 3:2):
 * a row of tabs to switch stock, then two cards, the stock being discussed (its end-of-day price)
 * and its latest full-year figures. It only shows what the server stores; a missing figure is said
 * to be missing, never filled in.
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

/** A small tinted icon for each kind of figure, so the list reads at a glance. */
function metricIcon(metric: Metric): { kind: string; icon: ReactNode } {
  switch (metric) {
    case 'net_profit':
      return { kind: 'profit', icon: <CoinsIcon /> };
    case 'eps_basic':
      return { kind: 'eps', icon: <TrendIcon /> };
    case 'return_on_equity':
      return { kind: 'roe', icon: <PercentIcon /> };
    default:
      return { kind: 'revenue', icon: <BarsIcon /> };
  }
}

/**
 * Where a figure is from: just the link icon (the owner, 2026-10-08, as on the stock page), named
 * for a screen reader ("screener.in", "Filing p.44") and showing the full source on hover. A source
 * with no safe address shows the same icon, still named and still on hover, but not as a link.
 */
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
      className="chat-source-icon"
      href={link}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={short}
      title={citation.label}
    >
      <ExternalIcon />
    </a>
  ) : (
    <span className="chat-source-icon" role="img" aria-label={short} title={citation.label}>
      <ExternalIcon />
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
          markers
        />
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
    <section className="chat-zone chat-overview" aria-label={title}>
      <div className="chat-zone-head">
        <h2>{title}</h2>
      </div>
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

function KeyMetrics({ symbol, insights }: { symbol: string; insights: Fetched<StockInsights> }) {
  const found = insights?.data ? keyMetrics(insights.data) : null;
  const title = found?.period ? `Key metrics (${found.period})` : 'Key metrics';
  return (
    <section className="chat-zone chat-key-metrics" aria-label={title}>
      <div className="chat-zone-head">
        <h2>{title}</h2>
        <a className="chat-zone-link" href={stockPageHref(symbol)}>
          View details
          <ArrowRightIcon />
        </a>
      </div>
      {insights === null ? (
        <p className="muted">Loading…</p>
      ) : insights.failed ? (
        <p className="muted">Key figures couldn&apos;t be loaded.</p>
      ) : !found || found.rows.length === 0 ? (
        <p className="muted">No key figures stored yet.</p>
      ) : (
        <ul className="chat-metrics">
          {found.rows.map((row) => {
            const { kind, icon } = metricIcon(row.metric);
            return (
              <li key={row.metric}>
                <span className={`chat-metric-icon ${kind}`} aria-hidden="true">
                  {icon}
                </span>
                <div>
                  <span>{row.label}</span>
                  {row.period !== found.period && <span className="muted"> ({row.period})</span>}
                  <SourceChip citation={row.fact.citation} />
                </div>
                <strong className="num">{row.value}</strong>
                <span className={`chat-change ${TONE_CLASS[row.tone]}`}>{row.change}</span>
              </li>
            );
          })}
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
      <KeyMetrics symbol={symbol} insights={insights[symbol] ?? null} />
    </aside>
  );
}
