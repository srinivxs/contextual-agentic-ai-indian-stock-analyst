'use client';

import { useRouter } from 'next/navigation';
import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from 'react';

import { AppShell } from '@/components/AppShell';
import {
  ClockIcon,
  LeafIcon,
  PencilIcon,
  PlusIcon,
  SendIcon,
  ShieldIcon,
  TargetIcon,
  TrendIcon,
} from '@/components/Icons';
import { Monogram } from '@/components/Monogram';
import { ProfitChart } from '@/components/ProfitChart';
import { Sparkline } from '@/components/Sparkline';
import { ApiError } from '@/lib/api';
import { getFeed, type FeedItem } from '@/lib/feed';
import { latestNews } from '@/lib/homeNews';
import { citationLink, getInsights, stockPageHref, type StockInsights } from '@/lib/insights';
import { getMatches, STATUS_LABELS, type MatchResult, type MatchStatus } from '@/lib/match';
import { getProfile, type Profile, type ProfileFieldName } from '@/lib/profile';
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

const SUGGESTIONS = [
  'Analyse RELIANCE',
  'Compare TCS and HDFC Bank',
  'Latest news on TCS',
  'Which stocks have low debt?',
  'Match me',
];
const chatHref = (question: string): string => `/chat/?q=${encodeURIComponent(question)}`;

const CAN_DO = [
  'Answer questions about RELIANCE, TCS and HDFC Bank from their filings',
  'Show key figures and how they changed',
  'Summarise recent news and its sentiment',
  'Say how each stock fits your preferences',
];

const PROFILE_ICONS: Record<ProfileFieldName, ReactNode> = {
  risk_preference: <ShieldIcon />,
  debt_preference: <TargetIcon />,
  investment_style: <TrendIcon />,
  other_preferences: <ClockIcon />,
};

type SeriesByStock = (Series | null)[];

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

function Hero({ stocks, series }: { stocks: Loadable<Stock[]>; series: Loadable<SeriesByStock> }) {
  const titleId = useId();
  const [chosen, setChosen] = useState<string | null>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const tabsId = useId();

  const list = stocks.status === 'ready' ? stocks.data : [];
  const index = Math.max(
    0,
    list.findIndex((s) => s.symbol === chosen),
  );
  const stock = list[index];
  const current = series.status === 'ready' ? series.data[index] : undefined;

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

function StatCard({ stock, series }: { stock: Stock; series: Series | null | undefined }) {
  const { latest, percent, tone, values } = summary(series);
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
        </span>
        {latest && values.length > 1 && <Sparkline values={values} tone={tone} />}
      </span>
    </a>
  );
}

function MatchCard({ match }: { match: Loadable<MatchResult> }) {
  if (match.status !== 'ready') {
    return (
      <a className="home-card home-stat" href="/match/">
        <span className="home-stat-label">Your match</span>
        <span className="muted">
          {match.status === 'error' ? "We couldn't load your match." : 'Loading…'}
        </span>
      </a>
    );
  }
  const { profile_empty: empty, stocks } = match.data;
  if (empty) {
    return (
      <a className="home-card home-stat" href="/chat/">
        <span className="home-stat-label">Your match</span>
        <span className="home-stat-body">
          <span className="home-big-text">Tell the chat your preferences</span>
        </span>
      </a>
    );
  }
  const count = (status: MatchStatus): number => stocks.filter((s) => s.status === status).length;
  return (
    <a className="home-card home-stat" href="/match/">
      <span className="home-stat-label">Your match</span>
      <span className="home-stat-body">
        <span className="home-stat-figure">
          <span className="home-big num">{count('match')} match</span>
          <span className="muted">
            {count('partial')} partial · {count('no_match')} no match · {count('not_enough_data')}{' '}
            not enough data
          </span>
        </span>
      </span>
    </a>
  );
}

function YourStocks({
  stocks,
  series,
  match,
}: {
  stocks: Loadable<Stock[]>;
  series: Loadable<SeriesByStock>;
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
                  {latest && (
                    <>
                      <span className="num">{rupees(latest.value)} crore</span>
                      <span className="muted">{latest.period} net profit</span>
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

function ChatCard() {
  const router = useRouter();
  const titleId = useId();
  const inputId = useId();
  const [question, setQuestion] = useState('');

  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const text = question.trim();
    if (text) router.push(chatHref(text));
  };

  return (
    <section className="home-card home-chat" aria-labelledby={titleId}>
      <h2 id={titleId}>Chat with your analyst</h2>
      <div className="home-chat-intro">
        <span className="home-avatar" aria-hidden="true">
          <LeafIcon size={20} />
        </span>
        <div className="home-bubble">
          <p>
            <strong>Hello</strong>
          </p>
          <p>I can help you to:</p>
          <ul>
            {CAN_DO.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="home-pill small">Answers only from stored filings</p>
        </div>
      </div>
      <ul className="home-chips">
        {SUGGESTIONS.map((text) => (
          <li key={text}>
            <a className="home-chip" href={chatHref(text)}>
              {text}
            </a>
          </li>
        ))}
      </ul>
      <form className="home-ask" onSubmit={submit}>
        <label className="visually-hidden" htmlFor={inputId}>
          Ask about a stock
        </label>
        <input
          id={inputId}
          type="text"
          value={question}
          maxLength={1000}
          placeholder="Ask about a stock…"
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button type="submit" className="home-send" aria-label="Send">
          <SendIcon />
        </button>
      </form>
    </section>
  );
}

type NewsSources = { insights: (StockInsights | null)[] };

function News({
  insights,
  feed,
}: {
  insights: Loadable<NewsSources>;
  feed: Loadable<{ items: FeedItem[] }>;
}) {
  const titleId = useId();
  const waiting = insights.status === 'loading' || feed.status === 'loading';
  const insightList = insights.status === 'ready' ? insights.data.insights : [];
  const insightsFailed =
    insights.status === 'error' || (insightList.length > 0 && insightList.every((i) => i === null));
  const allFailed = insightsFailed && feed.status === 'error';
  const someFailed =
    insightsFailed || feed.status === 'error' || insightList.some((i) => i === null);
  const items = waiting
    ? []
    : latestNews(
        insightList.filter((i): i is StockInsights => i !== null),
        feed.status === 'ready' ? feed.data.items : [],
      );

  let body: ReactNode;
  if (waiting) body = <p className="muted">Loading…</p>;
  else if (allFailed) body = <p className="muted">{`We couldn't load the latest news.`}</p>;
  else {
    body = (
      <>
        {items.length === 0 ? (
          <p className="muted">Nothing new yet. New filings and RBI releases appear here.</p>
        ) : (
          <ul className="home-list">
            {items.map((item) => (
              <li key={item.key} className="home-news">
                {item.kind === 'rbi' ? (
                  <span className="monogram md tint-c" aria-hidden="true">
                    RBI
                  </span>
                ) : (
                  <Monogram symbol={item.mark} />
                )}
                <span className="home-news-text">
                  <span className="home-news-meta muted">
                    {item.source}
                    {item.dateLabel ? ` · ${item.dateLabel}` : ''}
                  </span>
                  {item.href ? (
                    <a className="home-news-title" href={item.href}>
                      {item.headline}
                    </a>
                  ) : (
                    <span className="home-news-title">{item.headline}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
        {someFailed && <p className="muted home-note">Some news could not be loaded.</p>}
      </>
    );
  }
  return (
    <section className="home-card" aria-labelledby={titleId}>
      <h2 id={titleId}>Latest from filings and RBI</h2>
      {body}
    </section>
  );
}

function InvestorProfile({ profile }: { profile: Loadable<Profile> }) {
  const titleId = useId();
  let body: ReactNode = <p className="muted">Loading…</p>;
  if (profile.status === 'error') {
    body = <p className="muted">{`We couldn't load your profile.`}</p>;
  } else if (profile.status === 'ready') {
    const { fields, choices } = profile.data;
    body =
      fields.length === 0 ? (
        <p className="muted">Nothing remembered yet. Tell the chat what you look for in a stock.</p>
      ) : (
        <ul className="home-list">
          {choices.map((choice) => {
            const entry = fields.find((f) => f.field === choice.field);
            return (
              <li key={choice.field} className="home-profile-row">
                <span className="home-icon" aria-hidden="true">
                  {PROFILE_ICONS[choice.field]}
                </span>
                <span className="home-profile-name">{choice.label}</span>
                <span className={entry ? 'home-profile-value' : 'home-profile-value muted'}>
                  {entry ? entry.labels.join(', ') : 'Not set'}
                </span>
              </li>
            );
          })}
        </ul>
      );
  }
  return (
    <section className="home-card" aria-labelledby={titleId}>
      <div className="home-card-head">
        <h2 id={titleId}>Your investor profile</h2>
        <a className="home-edit" href="/chat/">
          <PencilIcon />
          Edit
        </a>
      </div>
      {body}
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
  const profile = useLoad(getProfile, signedIn);
  const feed = useLoad(getFeed, signedIn);
  const stockList = stocks.status === 'ready' ? stocks.data : [];
  const stocksReady = stocks.status === 'ready';
  const seriesLoaded = useLoad(() => eachStock(stockList, (s) => getSeries(s)), stocksReady);
  const insightsLoaded = useLoad(
    async () => ({ insights: await eachStock(stockList, (s) => getInsights(s)) }),
    stocksReady,
  );
  // Without the stock list neither can start; say so instead of waiting for ever.
  const series: Loadable<SeriesByStock> = stocks.status === 'error' ? stocks : seriesLoaded;
  const insights: Loadable<NewsSources> =
    stocks.status === 'error' ? { status: 'error' } : insightsLoaded;

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
        <p role="alert" className="alert">
          {LOAD_FAILED}
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

  return (
    <AppShell email={me.user.email} onSignOut={() => void leave()} active="home">
      {problem && (
        <p role="alert" className="alert">
          {problem}
        </p>
      )}
      <div className="home">
        <Hero stocks={stocks} series={series} />
        <section className="home-stats" aria-label="At a glance">
          {stockList.map((stock, position) => (
            <StatCard
              key={stock.symbol}
              stock={stock}
              series={series.status === 'ready' ? series.data[position] : undefined}
            />
          ))}
          <MatchCard match={match} />
        </section>
        <div className="home-columns">
          <YourStocks stocks={stocks} series={series} match={match} />
          <ChatCard />
          <div className="home-side">
            <News insights={insights} feed={feed} />
            <InvestorProfile profile={profile} />
          </div>
        </div>
        <p className="muted home-foot">
          Not investment advice. Every figure comes from official filings or screener.in and is
          shown with its source.
        </p>
      </div>
    </AppShell>
  );
}
