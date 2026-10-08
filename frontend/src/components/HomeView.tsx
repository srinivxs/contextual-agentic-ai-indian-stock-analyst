'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';

import { AppShell } from '@/components/AppShell';
import { DemoOffline } from '@/components/DemoOffline';
import { PlusIcon } from '@/components/Icons';
import { Monogram } from '@/components/Monogram';
import { ProfitChart } from '@/components/ProfitChart';
import { PriceChart } from '@/components/PriceChart';
import { Sparkline } from '@/components/Sparkline';
import { ApiError } from '@/lib/api';
import { citationLink, stockPageHref } from '@/lib/insights';
import { getMatches, STATUS_LABELS, type MatchResult } from '@/lib/match';
import { getPrices, hasPrices, priceLabel, signedPercent, type Prices } from '@/lib/prices';
import { change, direction, getSeries, percentLabel, rupees, type Series } from '@/lib/series';
import { signOut, useMe } from '@/lib/session';
import { listStocks, type Stock } from '@/lib/stocks';

const isUnauthorized = (error: unknown): boolean =>
  error instanceof ApiError && error.status === 401;

/** What each call is doing: on its way, done, or failed (which never hides the other calls). */
type Loadable<T> = { status: 'loading' } | { status: 'ready'; data: T } | { status: 'error' };

/**
 * Runs one load once `enabled`. A 401 means the session ended: go and sign in again. Any other
 * failure only marks this one part as failed.
 */
function useLoad<T>(load: () => Promise<T>, enabled: boolean): Loadable<T> {
  const router = useRouter();
  const [state, setState] = useState<Loadable<T>>({ status: 'loading' });
  const latest = useRef(load);
  useEffect(() => {
    latest.current = load;
  });
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    latest.current().then(
      (data) => {
        if (!cancelled) setState({ status: 'ready', data });
      },
      (error: unknown) => {
        if (cancelled) return;
        if (isUnauthorized(error)) router.replace('/');
        else setState({ status: 'error' });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [enabled, router]);
  return state;
}

/** One stock's call for a list of stocks: a failure gives null, but a 401 still stops everything. */
async function eachStock<T>(stocks: Stock[], load: (symbol: string) => Promise<T>) {
  return Promise.all(
    stocks.map(async (stock): Promise<T | null> => {
      try {
        return await load(stock.symbol);
      } catch (error) {
        if (isUnauthorized(error)) throw error;
        return null;
      }
    }),
  );
}

const LOAD_FAILED = "We couldn't load your home page. Reload the page to try again.";
const SIGN_OUT_FAILED = "We couldn't sign you out. Please try again.";

/** The tab and card names people use; anything else shows the ticker as it is. */
const SHORT_NAMES: Record<string, string> = { HDFCBANK: 'HDFC Bank' };
const shortName = (symbol: string): string => SHORT_NAMES[symbol] ?? symbol;

type SeriesByStock = (Series | null)[];
type PricesByStock = (Prices | null)[];

const NO_PRICES = 'Prices not loaded yet';

/**
 * What one stock's prices give the cards and rows: the latest close and the day's change. Null
 * unless there is a latest close, so a missing price is never shown as a zero.
 */
function priceSummary(prices: Prices | null | undefined) {
  if (!hasPrices(prices) || !prices.latest) return null;
  const { close, change_pct: changePct } = prices.latest;
  return {
    close: priceLabel(close),
    percent: signedPercent(changePct),
    tone: direction(changePct === null ? null : Number(changePct)),
    values: prices.history.map((p) => Number(p.close)),
  };
}

/** "FY2026 net profit ₹120 crore", the smaller line under a price. */
const profitLine = (latest: { period: string; value: number }): string =>
  `${latest.period} net profit ${rupees(latest.value)} crore`;

type ChartView = 'price' | 'profit';

/** The latest figure and change of one stock's series, worked out once for the cards and rows. */
function summary(series: Series | null | undefined) {
  const latest = series ? change(series.points) : null;
  return {
    latest,
    percent: latest ? percentLabel(latest.percent) : null,
    tone: direction(latest?.percent ?? null),
    values: series ? series.points.map((p) => Number(p.value)) : [],
  };
}

function Hero({
  stocks,
  series,
  prices,
}: {
  stocks: Loadable<Stock[]>;
  series: Loadable<SeriesByStock>;
  prices: Loadable<PricesByStock>;
}) {
  const titleId = useId();
  const [chosen, setChosen] = useState<string | null>(null);
  const [picked, setPicked] = useState<ChartView | null>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const tabsId = useId();

  const list = stocks.status === 'ready' ? stocks.data : [];
  const index = Math.max(
    0,
    list.findIndex((s) => s.symbol === chosen),
  );
  const stock = list[index];
  const current = series.status === 'ready' ? series.data[index] : undefined;
  const stockPrices = prices.status === 'ready' ? prices.data[index] : undefined;
  const priced = hasPrices(stockPrices);
  // Without prices there is nothing to choose: the net profit view stands alone.
  const view: ChartView = priced ? (picked ?? 'price') : 'profit';

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const last = list.length - 1;
    const target =
      event.key === 'ArrowRight'
        ? (index + 1) % list.length
        : event.key === 'ArrowLeft'
          ? (index - 1 + list.length) % list.length
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? last
              : null;
    if (target === null) return;
    event.preventDefault();
    setChosen(list[target]?.symbol ?? null);
    tabRefs.current[target]?.focus();
  };

  let body: ReactNode = <p className="muted">Loading…</p>;
  if (stocks.status === 'error') {
    body = <p className="muted">The chart needs your stock list, which could not load.</p>;
  } else if (stock && prices.status === 'loading') {
    body = <p className="muted">Loading…</p>;
  } else if (stock && stockPrices && stockPrices.latest && view === 'price') {
    const source = citationLink(stockPrices.latest.citation);
    body = (
      <>
        <PriceChart
          history={stockPrices.history}
          name={stock.name}
          changePct={stockPrices.latest.change_pct}
        />
        <p className="home-caption muted">
          End-of-day closes, adjusted for bonus issues and splits ·{' '}
          {source ? <a href={source}>BSE daily price files</a> : 'BSE daily price files'}
        </p>
      </>
    );
  } else if (stock && series.status === 'error') {
    body = <p className="muted">{`We couldn't load the net profit figures.`}</p>;
  } else if (stock && series.status === 'ready') {
    if (current === null || current === undefined) {
      body = (
        <p className="muted">{`We couldn't load the net profit figures for ${stock.name}.`}</p>
      );
    } else {
      const lastPoint = current.points.at(-1);
      const source = lastPoint ? citationLink(lastPoint.citation) : null;
      body = (
        <>
          {!priced && <p className="home-caption muted">{NO_PRICES}</p>}
          <ProfitChart points={current.points} name={stock.name} label={current.label} />
          <p className="home-caption muted">
            {current.label}, ₹ crore, consolidated ·{' '}
            {source ? <a href={source}>screener.in</a> : 'screener.in'}
          </p>
        </>
      );
    }
  }

  return (
    <section className="home-hero" aria-labelledby={titleId}>
      <div className="home-hero-copy">
        <p className="home-pill">
          <span className="home-dot" aria-hidden="true" />
          Grounded in official filings
        </p>
        <h1 id={titleId}>
          Your personal <span className="home-accent">Indian stock analyst</span>
        </h1>
        <p className="home-lede">
          Ask about RELIANCE, TCS and HDFC Bank. Every answer is cited to an official filing or to
          screener.in.
        </p>
      </div>
      <div className="home-hero-chart">
        {priced && (
          <div className="home-toggle" role="group" aria-label="Chart shows">
            {(
              [
                ['price', 'Share price'],
                ['profit', 'Net profit'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className="home-toggle-option"
                aria-pressed={view === value}
                onClick={() => setPicked(value)}
              >
                {label}
              </button>
            ))}
          </div>
        )}
        {list.length > 0 && (
          <div
            className="home-tabs"
            role="tablist"
            aria-label="Stock shown in the chart"
            onKeyDown={onKeyDown}
          >
            {list.map((item, position) => (
              <button
                key={item.symbol}
                ref={(element) => {
                  tabRefs.current[position] = element;
                }}
                type="button"
                role="tab"
                id={`${tabsId}-tab-${item.symbol}`}
                aria-selected={position === index}
                aria-controls={`${tabsId}-panel`}
                tabIndex={position === index ? 0 : -1}
                className="home-tab"
                onClick={() => setChosen(item.symbol)}
              >
                {shortName(item.symbol)}
              </button>
            ))}
          </div>
        )}
        <div
          role="tabpanel"
          id={`${tabsId}-panel`}
          aria-labelledby={stock ? `${tabsId}-tab-${stock.symbol}` : undefined}
        >
          {body}
        </div>
      </div>
    </section>
  );
}

function StatCard({
  stock,
  series,
  prices,
  pricesSettled,
}: {
  stock: Stock;
  series: Series | null | undefined;
  prices: Prices | null | undefined;
  pricesSettled: boolean;
}) {
  const { latest, percent, tone, values } = summary(series);
  const price = priceSummary(prices);
  if (price) {
    return (
      <a className="home-card home-stat" href={stockPageHref(stock.symbol)}>
        <span className="home-stat-label">{stock.name}</span>
        <span className="home-stat-body">
          <span className="home-stat-figure">
            <span className="home-big num">{price.close}</span>
            <span className="home-stat-sub">
              {price.percent && (
                <span className={`home-change ${price.tone}`}>{price.percent}</span>
              )}
              <span className="muted">on the day</span>
            </span>
            {latest && <span className="home-stat-small muted">{profitLine(latest)}</span>}
          </span>
          {price.values.length > 1 && <Sparkline values={price.values} tone={price.tone} />}
        </span>
      </a>
    );
  }
  let figure: ReactNode = <span className="muted">Loading…</span>;
  if (series === null) figure = <span className="muted">Unavailable</span>;
  else if (series && !latest) figure = <span className="muted">No figures yet</span>;
  else if (latest) {
    figure = (
      <>
        <span className="home-big num">{rupees(latest.value)}</span>{' '}
        <span className="home-unit">crore</span>
      </>
    );
  }
  return (
    <a className="home-card home-stat" href={stockPageHref(stock.symbol)}>
      <span className="home-stat-label">{stock.name}</span>
      <span className="home-stat-body">
        <span className="home-stat-figure">
          <span>{figure}</span>
          {latest && (
            <span className="home-stat-sub">
              {percent && <span className={`home-change ${tone}`}>{percent}</span>}
              <span className="muted">{latest.period} net profit</span>
            </span>
          )}
          {pricesSettled && <span className="home-stat-small muted">{NO_PRICES}</span>}
        </span>
        {latest && values.length > 1 && <Sparkline values={values} tone={tone} />}
      </span>
    </a>
  );
}

function YourStocks({
  stocks,
  series,
  prices,
  match,
}: {
  stocks: Loadable<Stock[]>;
  series: Loadable<SeriesByStock>;
  prices: Loadable<PricesByStock>;
  match: Loadable<MatchResult>;
}) {
  const titleId = useId();
  const all = stocks.status === 'ready' ? stocks.data : [];
  const followed = all.filter((s) => s.followed);
  const matches = match.status === 'ready' && !match.data.profile_empty ? match.data.stocks : [];
  let body: ReactNode = <p className="muted">Loading…</p>;
  if (stocks.status === 'error') {
    body = <p className="muted">{`We couldn't load your stocks.`}</p>;
  } else if (stocks.status === 'ready' && followed.length === 0) {
    body = <p className="muted">{`You don't follow any stock yet. Follow one to see it here.`}</p>;
  } else if (stocks.status === 'ready') {
    body = (
      <ul className="home-list">
        {followed.map((stock) => {
          const position = all.findIndex((s) => s.symbol === stock.symbol);
          const { latest } = summary(series.status === 'ready' ? series.data[position] : undefined);
          const price = priceSummary(prices.status === 'ready' ? prices.data[position] : undefined);
          const status = matches.find((m) => m.symbol === stock.symbol)?.status;
          return (
            <li key={stock.symbol}>
              <a className="home-row" href={stockPageHref(stock.symbol)}>
                <Monogram symbol={stock.symbol} />
                <span className="home-row-name">
                  <strong>{stock.symbol}</strong>
                  <span className="muted">{stock.name}</span>
                </span>
                <span className="home-row-figure">
                  {price ? (
                    <>
                      <span className="num">
                        {price.close}{' '}
                        {price.percent && (
                          <span className={`home-change ${price.tone}`}>{price.percent}</span>
                        )}
                      </span>
                      {latest && <span className="muted">{profitLine(latest)}</span>}
                    </>
                  ) : (
                    <>
                      {latest && (
                        <>
                          <span className="num">{rupees(latest.value)} crore</span>
                          <span className="muted">{latest.period} net profit</span>
                        </>
                      )}
                      {prices.status !== 'loading' && <span className="muted">{NO_PRICES}</span>}
                    </>
                  )}
                  {status && (
                    <span className={`home-badge home-badge-${status}`}>
                      {STATUS_LABELS[status]}
                    </span>
                  )}
                </span>
              </a>
            </li>
          );
        })}
      </ul>
    );
  }
  const allFollowed = all.length > 0 && followed.length === all.length;
  return (
    <section className="home-card home-stocks" aria-labelledby={titleId}>
      <h2 id={titleId}>Your stocks</h2>
      {body}
      <a className="home-soft-button" href="/stocks/">
        <PlusIcon />
        {allFollowed ? 'Manage stocks' : 'Follow stocks'}
      </a>
    </section>
  );
}

/** The signed-in landing page: what the app knows, in one screen, every figure with its source. */
export function HomeView() {
  const me = useMe();
  const router = useRouter();
  const [problem, setProblem] = useState<string | null>(null);
  const signedIn = me.status === 'signed-in';

  useEffect(() => {
    if (me.status === 'signed-out') router.replace('/');
  }, [me.status, router]);

  const stocks = useLoad(listStocks, signedIn);
  const match = useLoad(getMatches, signedIn);
  const stockList = stocks.status === 'ready' ? stocks.data : [];
  const stocksReady = stocks.status === 'ready';
  const seriesLoaded = useLoad(() => eachStock(stockList, (s) => getSeries(s)), stocksReady);
  const pricesLoaded = useLoad(() => eachStock(stockList, (s) => getPrices(s)), stocksReady);
  // Without the stock list neither can start; say so instead of waiting for ever.
  const series: Loadable<SeriesByStock> = stocks.status === 'error' ? stocks : seriesLoaded;
  const prices: Loadable<PricesByStock> = stocks.status === 'error' ? stocks : pricesLoaded;

  const leave = async (): Promise<void> => {
    setProblem(null);
    try {
      await signOut();
      router.replace('/');
    } catch (error) {
      if (isUnauthorized(error)) router.replace('/');
      else setProblem(SIGN_OUT_FAILED);
    }
  };

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

  return (
    <AppShell email={me.user.email} onSignOut={() => void leave()} active="home">
      {problem && (
        <p role="alert" className="alert">
          {problem}
        </p>
      )}
      <div className="home">
        <Hero stocks={stocks} series={series} prices={prices} />
        <section className="home-stats" aria-label="At a glance">
          {stockList.map((stock, position) => (
            <StatCard
              key={stock.symbol}
              stock={stock}
              series={series.status === 'ready' ? series.data[position] : undefined}
              prices={prices.status === 'ready' ? prices.data[position] : undefined}
              pricesSettled={prices.status !== 'loading'}
            />
          ))}
        </section>
        <YourStocks stocks={stocks} series={series} prices={prices} match={match} />
        <p className="muted home-foot">
          Not investment advice. Every figure comes from official filings, BSE&apos;s end-of-day
          price files or screener.in and is shown with its source.
        </p>
      </div>
    </AppShell>
  );
}
