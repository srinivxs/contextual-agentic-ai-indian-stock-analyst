'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState, type KeyboardEvent } from 'react';

import { AppShell } from '@/components/AppShell';
import { DemoOffline } from '@/components/DemoOffline';
import { SearchBox } from '@/components/SearchBox';
import { ApiError } from '@/lib/api';
import {
  documentLabel,
  groupDocuments,
  isStillWorking,
  listAllDocuments,
  officialLink,
  pagesLabel,
  rbiUrl,
  summaryLabel,
  type DocumentStatus,
  type StockDocument,
} from '@/lib/documents';
import { feedDateLabel, getFeed, type Feed, type FeedItem } from '@/lib/feed';
import { stockPageHref } from '@/lib/insights';
import { signOut, useMe } from '@/lib/session';
import { listStocks, type Stock } from '@/lib/stocks';

/** How often the page asks again while the worker is still processing something. */
export const REFRESH_MS = 5000;

const LOAD_FAILED = "We couldn't load the documents. Reload the page to try again.";
const FEED_FAILED = "We couldn't load the RBI press releases. Reload the page to try again.";
const FEED_EMPTY = "No press releases yet. The worker checks RBI's feed every hour.";
/** The selected-tab value of the press-release tab; a stock symbol can never be lowercase. */
const RBI_TAB = 'rbi';

const STATUS_BADGES: Record<DocumentStatus, string> = {
  pending: 'Waiting',
  processing: 'Processing',
  completed: 'Ready',
  failed: 'Failed',
};

type Shelf = { stock: Stock; documents: StockDocument[] };

type Loaded = { ok: true; shelves: Shelf[] } | { ok: false; unauthorized: boolean };

async function readShelves(): Promise<Loaded> {
  try {
    const stocks = await listStocks();
    const shelves = await Promise.all(
      stocks.map(async (stock) => ({ stock, documents: await listAllDocuments(stock.symbol) })),
    );
    return { ok: true, shelves };
  } catch (error) {
    return {
      ok: false,
      unauthorized: error instanceof ApiError && error.status === 401,
    };
  }
}

function DocumentRow({ document }: { document: StockDocument }) {
  const link = officialLink(document);
  const label = documentLabel(document);
  const badge = STATUS_BADGES[document.status];
  return (
    <li className="doc-row">
      <span className="doc-label">
        {link ? (
          <a href={link} target="_blank" rel="noopener noreferrer" className="doc-link">
            {label}
          </a>
        ) : (
          <span>{label}</span>
        )}
        {document.status === 'failed' && document.failure_reason && (
          <span className="doc-reason">{document.failure_reason}</span>
        )}
      </span>
      <span className="doc-pages">{pagesLabel(document.page_count)}</span>
      <span className="doc-status">
        <span className={`pill ${document.status}`}>{badge}</span>
      </span>
    </li>
  );
}

function FeedRow({ item }: { item: FeedItem }) {
  // A fixture is a sample: never linked, whatever address it carries.
  const link = item.is_fixture ? null : rbiUrl(item.url);
  return (
    <li className="feed-row">
      <span className="doc-label">
        {link ? (
          <a href={link} target="_blank" rel="noopener noreferrer" className="doc-link">
            {item.title}
          </a>
        ) : (
          <span>{item.title}</span>
        )}
        <span className="muted feed-date">
          {feedDateLabel(item.published_at) ?? 'Date not given'}
        </span>
        {item.is_fixture && <span className="pill">Sample item (offline fixture)</span>}
      </span>
      <p className="feed-summary">{item.summary}</p>
    </li>
  );
}

/** The latest RBI press releases: title, date, short summary, and the credit line. */
function FeedPanel({ feed, failed }: { feed: Feed | null; failed: boolean }) {
  return (
    <div
      role="tabpanel"
      id={`panel-${RBI_TAB}`}
      aria-label="RBI press releases"
      className="doc-panel"
    >
      {failed && (
        <p role="alert" className="alert">
          {FEED_FAILED}
        </p>
      )}
      {!failed && feed === null && (
        <p role="status" className="muted">
          Loading press releases…
        </p>
      )}
      {feed && feed.items.length === 0 && <p className="muted empty">{FEED_EMPTY}</p>}
      {feed && feed.items.length > 0 && (
        <ul className="doc-list feed-list">
          {feed.items.map((item, index) => (
            <FeedRow key={`${index}-${item.title}`} item={item} />
          ))}
        </ul>
      )}
      {feed && <p className="muted feed-attribution">{feed.attribution}</p>}
    </div>
  );
}

function StockPanel({ shelf }: { shelf: Shelf }) {
  const groups = groupDocuments(shelf.documents);
  return (
    <div
      role="tabpanel"
      id={`panel-${shelf.stock.symbol}`}
      aria-label={shelf.stock.name}
      className="doc-panel"
    >
      <p className="doc-facts">
        <a
          href={stockPageHref(shelf.stock.symbol)}
          className="stock-link"
          aria-label={`Key facts for ${shelf.stock.symbol}`}
        >
          Key facts
        </a>
      </p>
      {groups.length > 0 && (
        // Keyed by stock, so switching tabs starts a fresh search for the new one.
        <SearchBox
          key={shelf.stock.symbol}
          symbol={shelf.stock.symbol}
          stockName={shelf.stock.name}
        />
      )}
      {groups.length === 0 ? (
        <p className="muted empty">No documents yet.</p>
      ) : (
        <>
          <p className="muted doc-summary">{summaryLabel(shelf.documents)}</p>
          {groups.map((group) => (
            <section
              key={group.key}
              aria-labelledby={`group-${shelf.stock.symbol}-${group.key}`}
              className="doc-group"
            >
              <h3 id={`group-${shelf.stock.symbol}-${group.key}`}>
                {group.title}
                <span className="count">{group.documents.length}</span>
              </h3>
              <ul className="doc-list">
                {group.documents.map((document) => (
                  <DocumentRow key={document.id} document={document} />
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </div>
  );
}

/**
 * Every stock's documents, one tab per stock: the official filings the worker fetched from BSE
 * (ADR 018), grouped by kind and newest first. While a document is still waiting or processing,
 * the page refreshes itself. New filings are asked for with "Update data" in the top bar, for
 * every stock at once (app/data_status.py).
 */
export function DocumentsView() {
  const me = useMe();
  const router = useRouter();
  const [shelves, setShelves] = useState<Shelf[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [feed, setFeed] = useState<Feed | null>(null);
  const [feedFailed, setFeedFailed] = useState(false);
  const signedIn = me.status === 'signed-in';

  const show = useCallback(
    (result: Loaded) => {
      if (result.ok) {
        setShelves(result.shelves);
        setProblem(null);
      } else if (result.unauthorized) {
        router.replace('/');
      } else {
        setProblem(LOAD_FAILED);
      }
    },
    [router],
  );

  useEffect(() => {
    if (me.status === 'signed-out') router.replace('/');
  }, [me.status, router]);

  // Load once when signed in.
  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    void readShelves().then((result) => {
      if (!cancelled) show(result);
    });
    return () => {
      cancelled = true;
    };
  }, [signedIn, show]);

  // The RBI feed is loaded once and on its own, so its failure never hides the stock tabs.
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

  // Then load again every REFRESH_MS, but only while something is still being worked on.
  const working = shelves?.some((shelf) => isStillWorking(shelf.documents)) ?? false;
  useEffect(() => {
    if (!signedIn || !working) return;
    let cancelled = false;
    const timer = setInterval(() => {
      void readShelves().then((result) => {
        if (!cancelled) show(result);
      });
    }, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [signedIn, working, show]);

  if (me.status === 'error') {
    return (
      <main className="page centered">
        {me.offline ? (
          <DemoOffline />
        ) : (
          <p role="alert" className="alert">
            {LOAD_FAILED}
          </p>
        )}
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

  const tabIds = [...(shelves ?? []).map((s) => s.stock.symbol), RBI_TAB];
  /** Arrow keys, Home and End move between the tabs, as a tab list should. */
  const onTabKey = (event: KeyboardEvent<HTMLDivElement>, activeId: string): void => {
    const at = tabIds.indexOf(activeId);
    let next = -1;
    if (event.key === 'ArrowRight') next = (at + 1) % tabIds.length;
    else if (event.key === 'ArrowLeft') next = (at - 1 + tabIds.length) % tabIds.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabIds.length - 1;
    const target = tabIds[next];
    if (target === undefined) return;
    event.preventDefault();
    setSelected(target);
    document.getElementById(`tab-${target}`)?.focus();
  };
  const rbiSelected = selected === RBI_TAB;
  const current = shelves?.find((s) => s.stock.symbol === selected) ?? shelves?.[0] ?? null;

  return (
    <AppShell
      email={me.user.email}
      onSignOut={() => void signOut().finally(() => router.replace('/'))}
      active="documents"
    >
      <div className="page-intro">
        <h1>Documents</h1>
        <p className="muted">
          Official BSE filings from the last three years (earnings calls, presentations, annual
          reports and announcements), fetched automatically every day, when you follow a stock, and
          with Update data at the top. Open any one to read the original.
        </p>
      </div>
      {problem && (
        <p role="alert" className="alert">
          {problem}
        </p>
      )}
      {shelves === null && !problem && (
        <p role="status" className="muted">
          Loading documents…
        </p>
      )}
      {shelves && current && (
        <div className="doc-card">
          <div
            role="tablist"
            aria-label="Stocks"
            className="tabs"
            onKeyDown={(event) => onTabKey(event, rbiSelected ? RBI_TAB : current.stock.symbol)}
          >
            {shelves.map((shelf) => {
              const active = !rbiSelected && shelf.stock.symbol === current.stock.symbol;
              return (
                <button
                  key={shelf.stock.symbol}
                  type="button"
                  role="tab"
                  id={`tab-${shelf.stock.symbol}`}
                  aria-selected={active}
                  tabIndex={active ? 0 : -1}
                  aria-controls={`panel-${shelf.stock.symbol}`}
                  className={active ? 'tab active' : 'tab'}
                  onClick={() => setSelected(shelf.stock.symbol)}
                >
                  {shelf.stock.name}
                  <span className="count">{shelf.documents.length}</span>
                </button>
              );
            })}
            <button
              type="button"
              role="tab"
              id={`tab-${RBI_TAB}`}
              aria-selected={rbiSelected}
              tabIndex={rbiSelected ? 0 : -1}
              aria-controls={`panel-${RBI_TAB}`}
              className={rbiSelected ? 'tab active' : 'tab'}
              onClick={() => setSelected(RBI_TAB)}
            >
              RBI press releases
            </button>
          </div>
          {rbiSelected ? (
            <FeedPanel feed={feed} failed={feedFailed} />
          ) : (
            <StockPanel shelf={current} />
          )}
        </div>
      )}
    </AppShell>
  );
}
