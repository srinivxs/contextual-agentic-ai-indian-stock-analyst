/**
 * The Home page's "Latest from filings and RBI": the newest events of every stock and the RBI
 * press-release feed, merged into one list, newest first. Only stored, cited items go in, and every
 * link is checked by the same helpers the other pages use.
 */

import { rbiUrl } from '@/lib/documents';
import { citationLink, eventDateLabel, eventTypeLabel, type StockInsights } from '@/lib/insights';
import { feedDateLabel, type FeedItem } from '@/lib/feed';

export type NewsItem = {
  key: string;
  kind: 'stock' | 'rbi';
  /** The stock's symbol for a filing event; "RBI" for a release. */
  mark: string;
  /** "TCS · Earnings results". */
  source: string;
  /** "2026-07-01", or null when the item has no date. */
  date: string | null;
  dateLabel: string | null;
  headline: string;
  /** A checked official address, or null (then the headline is plain text). */
  href: string | null;
};

const isoDay = (value: string | null): string | null => {
  const day = /^\d{4}-\d{2}-\d{2}/.exec(value ?? '')?.[0];
  return day ?? null;
};

/** At most `limit` items, newest first; undated ones after the dated. */
export function latestNews(insights: StockInsights[], feed: FeedItem[], limit = 5): NewsItem[] {
  const feedLinks = new Set(feed.map((item) => rbiUrl(item.url)).filter((url) => url !== null));

  const events: NewsItem[] = insights.flatMap((stock) =>
    stock.events.flatMap((event, index): NewsItem[] => {
      const href = citationLink(event.citation);
      // An RBI release also reaches a stock's events; the feed already lists it once.
      if (event.citation.source === 'rbi' && href !== null && feedLinks.has(href)) return [];
      const type = eventTypeLabel(event.event_type);
      return [
        {
          key: `event-${stock.symbol}-${index}`,
          kind: event.citation.source === 'rbi' ? 'rbi' : 'stock',
          mark: event.citation.source === 'rbi' ? 'RBI' : stock.symbol,
          source: `${event.citation.source === 'rbi' ? 'RBI' : stock.symbol} · ${type}`,
          date: event.event_date,
          dateLabel: eventDateLabel(event.event_date),
          headline: event.summary,
          href,
        },
      ];
    }),
  );

  const releases: NewsItem[] = feed.map((item, index) => ({
    key: `feed-${index}`,
    kind: 'rbi',
    mark: 'RBI',
    source: item.is_fixture ? 'RBI · sample release' : 'RBI · press release',
    date: isoDay(item.published_at),
    dateLabel: feedDateLabel(item.published_at),
    headline: item.title,
    href: rbiUrl(item.url),
  }));

  return [...releases, ...events]
    .sort((a, b) => (b.date ?? '').localeCompare(a.date ?? ''))
    .slice(0, limit);
}
