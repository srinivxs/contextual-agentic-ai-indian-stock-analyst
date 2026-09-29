'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useState, type KeyboardEvent } from 'react';

import { AppShell } from '@/components/AppShell';
import { Monogram } from '@/components/Monogram';
import { STOCKS } from '@/components/StockJump';
import { rbiUrl } from '@/lib/documents';
import { feedDateLabel, getFeed, type Feed } from '@/lib/feed';
import {
  citationLink,
  eventDateLabel,
  eventTypeLabel,
  getInsights,
  type Sentiment,
  type StockInsights,
} from '@/lib/insights';
import { shortName } from '@/lib/chatContext';
import { signOut, useMe } from '@/lib/session';
import { listStocks } from '@/lib/stocks';

/**
 * News (the owner's request): what the stored data says happened, in one place. A stock's items
 * are the events the server tagged from its official filings (each with its source); the RBI's
 * are its press releases. Nothing is written here: every headline, date, type and sentiment word
 * comes from a stored row, and a headline links only to an address we accept.
 */

const ALL_TAB = 'all';
const RBI_TAB = 'rbi';
const SAMPLE_PILL = 'Sample item (offline fixture)';

type NewsItem = {
  key: string;
  /** A stock symbol, or 'RBI'. */
  mark: string;
  headline: string;
  /** Already safe to use as an href, or null. */
  link: string | null;
  type: string;
  dateLabel: string;
  /** "2026-09-25", for ordering; '' sorts last. */
  sortDate: string;
  sentiment: string | null;
  /** The long source label, shown small. */
  source: string;
  sample: boolean;
  /** 'RBI' or a stock symbol: which tab it belongs to. */
  tab: string;
};

type StockNews = { symbol: string; items: NewsItem[]; sentiment: Sentiment | null };

const capitalise = (words: string): string => words.charAt(0).toUpperCase() + words.slice(1);

function stockItems(symbol: string, insights: StockInsights): NewsItem[] {
  return insights.events.map((event, index) => ({
    key: `${symbol}-${index}`,
    mark: symbol,
    headline: event.summary,
    link: citationLink(event.citation),
    type: eventTypeLabel(event.event_type),
    dateLabel: eventDateLabel(event.event_date),
    sortDate: event.event_date,
    sentiment: capitalise(event.sentiment),
    source: event.citation.label,
    sample: false,
    tab: symbol,
  }));
}

function feedItems(feed: Feed): NewsItem[] {
  return feed.items.map((item, index) => ({
    key: `rbi-${index}`,
    mark: 'RBI',
    headline: item.title,
    // A sample is never linked, whatever address it carries.
    link: item.is_fixture ? null : rbiUrl(item.url),
    type: 'Press release',
    dateLabel: feedDateLabel(item.published_at) ?? 'Date not given',
    sortDate: (item.published_at ?? '').slice(0, 10),
    sentiment: null,
    source: item.summary,
    sample: item.is_fixture,
    tab: RBI_TAB,
  }));
}

/** Newest first; items with the same date keep their order. */
const newestFirst = (items: NewsItem[]): NewsItem[] =>
  [...items].sort((a, b) => b.sortDate.localeCompare(a.sortDate));

/** "News sentiment: positive (0.42 from 12 events in the last year)", or null if none is known. */
function sentimentSummary(sentiment: Sentiment | null): string | null {
  if (!sentiment || sentiment.status !== 'ok' || sentiment.label === null) return null;
  if (sentiment.score === null) return null;
  const events = `${sentiment.events_counted} ${sentiment.events_counted === 1 ? 'event' : 'events'}`;
  return `News sentiment: ${sentiment.label} (${sentiment.score.toFixed(2)} from ${events} in the last year)`;
}

function NewsRow({ item }: { item: NewsItem }) {
  return (
    <li className="news-item">
      {item.tab === RBI_TAB ? (
        <span className="news-rbi" aria-hidden="true">
          RBI
        </span>
      ) : (
        <Monogram symbol={item.mark} />
      )}
      <div className="news-body">
        <p className="news-headline">
          {item.link ? (
            <a href={item.link} target="_blank" rel="noopener noreferrer" className="doc-link">
              {item.headline}
            </a>
          ) : (
            item.headline
          )}
        </p>
        <p className="news-meta muted">
          <span>{item.type}</span>
          <span>{item.dateLabel}</span>
          {item.sentiment && (
            <span className={`news-tag ${item.sentiment.toLowerCase()}`}>{item.sentiment}</span>
          )}
          {item.sample && <span className="pill">{SAMPLE_PILL}</span>}
        </p>
        <p className="news-source muted">{item.source}</p>
      </div>
    </li>
  );
}

export function NewsView() {
  const me = useMe();
  const router = useRouter();
  const signedIn = me.status === 'signed-in';
  const [selected, setSelected] = useState(ALL_TAB);
  const [stocks, setStocks] = useState<StockNews[] | null>(null);
  const [failedStocks, setFailedStocks] = useState<string[]>([]);
  const [listFailed, setListFailed] = useState(false);
  const [feed, setFeed] = useState<Feed | null>(null);
  const [feedFailed, setFeedFailed] = useState(false);

  useEffect(() => {
    if (me.status === 'signed-out') router.replace('/');
  }, [me.status, router]);

  // The stocks and the RBI feed load on their own: one failing never hides the others.
  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    void (async () => {
      let symbols: string[];
      try {
        const listed = await listStocks();
        symbols = STOCKS.map((s) => s.symbol).filter((symbol) =>
          listed.some((stock) => stock.symbol === symbol),
        );
      } catch {
        if (!cancelled) {
          setListFailed(true);
          setStocks([]);
        }
        return;
      }
      const settled = await Promise.allSettled(symbols.map((symbol) => getInsights(symbol)));
      if (cancelled) return;
      const loaded: StockNews[] = [];
      const failed: string[] = [];
      settled.forEach((result, index) => {
        const symbol = symbols[index] ?? '';
        if (result.status === 'fulfilled') {
          loaded.push({
            symbol,
            items: stockItems(symbol, result.value),
            sentiment: result.value.sentiment,
          });
        } else {
          failed.push(shortName(symbol));
        }
      });
      setStocks(loaded);
      setFailedStocks(failed);
    })();
    return () => {
      cancelled = true;
    };
  }, [signedIn]);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    getFeed().then(
      (loaded) => {
        if (!cancelled) setFeed(loaded);
      },
      () => {
        if (!cancelled) setFeedFailed(true);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [signedIn]);

  if (me.status === 'error') {
    return (
      <main className="page centered">
        <p role="alert" className="alert">
          We couldn&apos;t load the news. Reload the page to try again.
        </p>
      </main>
    );
  }
  if (!signedIn) {
    return (
      <main className="page centered">
        <p role="status" className="muted">
          Loading…
        </p>
      </main>
    );
  }

  const tabs = [
    { id: ALL_TAB, label: 'All' },
    ...STOCKS.map((s) => ({ id: s.symbol, label: shortName(s.symbol) })),
    { id: RBI_TAB, label: 'RBI' },
  ];
  const tabIds = tabs.map((t) => t.id);
  /** Arrow keys, Home and End move between the tabs, as a tab list should. */
  const onTabKey = (event: KeyboardEvent<HTMLDivElement>): void => {
    const at = tabIds.indexOf(selected);
    let next = -1;
    if (event.key === 'ArrowRight') next = (at + 1) % tabIds.length;
    else if (event.key === 'ArrowLeft') next = (at - 1 + tabIds.length) % tabIds.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabIds.length - 1;
    const target = tabIds[next];
    if (target === undefined) return;
    event.preventDefault();
    setSelected(target);
    document.getElementById(`news-tab-${target}`)?.focus();
  };

  const loading = stocks === null || (feed === null && !feedFailed);
  const all = [...(stocks ?? []).flatMap((s) => s.items), ...(feed ? feedItems(feed) : [])];
  const shown = newestFirst(selected === ALL_TAB ? all : all.filter((i) => i.tab === selected));
  const current = stocks?.find((s) => s.symbol === selected);
  const summary = sentimentSummary(current?.sentiment ?? null);
  const selectedLabel = tabs.find((t) => t.id === selected)?.label ?? '';
  const emptyText =
    selected === ALL_TAB
      ? 'No news yet.'
      : selected === RBI_TAB
        ? 'No RBI press releases yet.'
        : `No news for ${selectedLabel} yet.`;

  const problems = [
    ...(listFailed ? ['the stocks'] : failedStocks),
    ...(feedFailed ? ['RBI'] : []),
  ];
  const showAttribution = feed !== null && (selected === ALL_TAB || selected === RBI_TAB);

  return (
    <AppShell
      email={me.user.email}
      onSignOut={() => void signOut().finally(() => router.replace('/'))}
      active="news"
    >
      <div className="page-intro">
        <h1>News</h1>
        <p className="muted">
          Events taken from each company&apos;s official filings, and the RBI&apos;s press releases.
          Each item shows its source.
        </p>
      </div>
      {problems.length > 0 && (
        <p role="alert" className="alert">
          {`We couldn't load the news for ${problems.join(', ')}. The rest is shown below.`}
        </p>
      )}
      <div className="news-card">
        <div role="tablist" aria-label="News sources" className="tabs" onKeyDown={onTabKey}>
          {tabs.map((tab) => {
            const active = tab.id === selected;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                id={`news-tab-${tab.id}`}
                aria-selected={active}
                tabIndex={active ? 0 : -1}
                aria-controls="news-panel"
                className={active ? 'tab active' : 'tab'}
                onClick={() => setSelected(tab.id)}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
        <div
          role="tabpanel"
          id="news-panel"
          aria-labelledby={`news-tab-${selected}`}
          className="news-panel"
        >
          {summary && <p className="news-summary muted">{summary}</p>}
          {loading && shown.length === 0 ? (
            <p role="status" className="muted">
              Loading news…
            </p>
          ) : shown.length === 0 ? (
            <p className="muted empty">{emptyText}</p>
          ) : (
            <ul className="news-list">
              {shown.map((item) => (
                <NewsRow key={item.key} item={item} />
              ))}
            </ul>
          )}
          {showAttribution && <p className="muted feed-attribution">{feed.attribution}</p>}
        </div>
      </div>
    </AppShell>
  );
}
