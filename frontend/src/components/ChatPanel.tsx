'use client';

import { useEffect, useState } from 'react';

import { FileIcon } from '@/components/Icons';
import { MemoryPanel } from '@/components/MemoryPanel';
import { Monogram } from '@/components/Monogram';
import { PriceChart } from '@/components/PriceChart';
import { STOCKS } from '@/components/StockJump';
import {
  availableRanges,
  historyStillFilling,
  keyMetrics,
  newestEvents,
  rangeHistory,
  RANGES,
  shortName,
} from '@/lib/chatContext';
import type { RangeKey } from '@/lib/chatContext';
import {
  citationLink,
  eventDateLabel,
  eventTypeLabel,
  getInsights,
  type Citation,
  type StockInsights,
} from '@/lib/insights';
import { getPrices, hasPrices, priceLabel, signedPercent, type Prices } from '@/lib/prices';
import { direction } from '@/lib/series';

/**
 * The side panel of the chat page: the stock being discussed (its end-of-day price, its latest
 * full-year figures and its recent events), and the investor profile. It only shows what the
 * server stores; a missing figure is said to be missing, never filled in.
 */

/** What one call to the server gave for one symbol: null while it is still loading. */
type Fetched<T> = { data: T | null; failed: boolean } | null;

/** Load something for a symbol; the answer of a stock we have moved away from is ignored. */
function useForStock<T>(symbol: string, load: (symbol: string) => Promise<T>): Fetched<T> {
  const [result, setResult] = useState<{ symbol: string; data: T | null } | null>(null);
  useEffect(() => {
    let cancelled = false;
    load(symbol).then(
      (data) => {
        if (!cancelled) setResult({ symbol, data });
      },
      () => {
        if (!cancelled) setResult({ symbol, data: null });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [symbol, load]);
  return result?.symbol === symbol ? { data: result.data, failed: result.data === null } : null;
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

function Overview({ symbol }: { symbol: string }) {
  const [range, setRange] = useState<RangeKey>('1y');
  const fetched = useForStock<Prices>(symbol, getPrices);
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
    // A range the history cannot fill yet is greyed out; the chart shows the longest one that has
    // data, or the one chosen when it has.
    const available = availableRanges(prices.history);
    const shown = available[range]
      ? range
      : ([...RANGES].reverse().find((r) => available[r.key])?.key ?? '1m');
    const since = prices.history[0]?.date;
    body = (
      <>
        <div className="chat-price-row">
          <strong className="chat-price num">{priceLabel(latest?.close ?? 0)}</strong>
          {percent && <span className={`chat-change ${TONE_CLASS[tone]}`}>{percent}</span>}
          <span className="muted">on the day</span>
        </div>
        <div className="chat-ranges" role="group" aria-label="Chart range">
          {RANGES.map((r) => (
            <button
              key={r.key}
              type="button"
              className="chat-range"
              aria-pressed={shown === r.key}
              disabled={!available[r.key]}
              title={available[r.key] ? undefined : 'Not enough history yet'}
              onClick={() => setRange(r.key)}
            >
              {r.label}
            </button>
          ))}
        </div>
        <PriceChart
          history={rangeHistory(prices.history, shown)}
          name={stock?.name ?? symbol}
          changePct={latest?.change_pct ?? null}
        />
        {since && historyStillFilling(prices.history) && (
          <p className="chat-asof muted">
            {`Prices from ${eventDateLabel(since)} so far; the rest of the year is still being fetched.`}
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
    <section className="chat-card" aria-label={title}>
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

function RecentNews({ symbol, insights }: { symbol: string; insights: Fetched<StockInsights> }) {
  const events = insights?.data ? newestEvents(insights.data.events) : [];
  return (
    <section className="chat-card" aria-label="Recent news">
      <h2>Recent news</h2>
      {insights === null ? (
        <p className="muted">Loading…</p>
      ) : insights.failed ? (
        <p className="muted">Events couldn&apos;t be loaded.</p>
      ) : events.length === 0 ? (
        <p className="muted">No events stored for {shortName(symbol)} yet.</p>
      ) : (
        <ul className="chat-news">
          {events.map((event, index) => {
            const link = citationLink(event.citation);
            return (
              <li key={index}>
                <span className="chat-news-mark" aria-hidden="true">
                  <FileIcon />
                </span>
                <div>
                  <p className="chat-news-meta muted">
                    {event.citation.label} · {eventDateLabel(event.event_date)} ·{' '}
                    {eventTypeLabel(event.event_type)}
                  </p>
                  {link ? (
                    <a href={link} target="_blank" rel="noopener noreferrer" className="doc-link">
                      {event.summary}
                    </a>
                  ) : (
                    <p>{event.summary}</p>
                  )}
                </div>
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
  refreshSignal,
}: {
  symbol: string;
  onPick: (symbol: string) => void;
  refreshSignal: number;
}) {
  const insights = useForStock<StockInsights>(symbol, getInsights);
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
      <Overview symbol={symbol} />
      <KeyMetrics insights={insights} />
      <RecentNews symbol={symbol} insights={insights} />
      <MemoryPanel refreshSignal={refreshSignal} />
    </aside>
  );
}
