'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';

import { AppShell } from '@/components/AppShell';
import {
  DerivedValues,
  KeyFacts,
  RecentEvents,
  RecentSentiment,
} from '@/components/InsightSections';
import { ApiError } from '@/lib/api';
import { getInsights, isTickerSymbol, type StockInsights } from '@/lib/insights';
import { signOut, useMe } from '@/lib/session';

const LOAD_FAILED = "We couldn't load the key facts. Reload the page to try again.";
const NOT_ADVICE =
  'Not investment advice. Figures come from company filings and screener.in, each with its source.';

type Outcome =
  | { phase: 'ready'; insights: StockInsights }
  | { phase: 'unknown' } // the server has no such stock (404)
  | { phase: 'problem' }; // anything else went wrong

function BackToStocks() {
  return (
    <p>
      <a href="/stocks/">Back to Stocks</a>
    </p>
  );
}

/**
 * One stock's key facts, derived values, sentiment and events (P11c). The page is one static
 * file for every stock and learns which from ?symbol= (ADR 006: no dynamic routes).
 */
export function StockView() {
  const me = useMe();
  const router = useRouter();
  const params = useSearchParams();
  const requested = params?.get('symbol') ?? '';
  // Not ticker-shaped (or missing): don't ask the server at all.
  const symbol = isTickerSymbol(requested) ? requested : null;
  // Kept together with the stock it belongs to, so a result for another stock counts as loading.
  const [result, setResult] = useState<{ symbol: string; outcome: Outcome } | null>(null);
  const outcome = result !== null && result.symbol === symbol ? result.outcome : null;
  const signedIn = me.status === 'signed-in';

  useEffect(() => {
    if (me.status === 'signed-out') router.replace('/');
  }, [me.status, router]);

  useEffect(() => {
    if (!signedIn || symbol === null) return;
    let cancelled = false;
    getInsights(symbol).then(
      (insights) => {
        if (!cancelled) setResult({ symbol, outcome: { phase: 'ready', insights } });
      },
      (error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) {
          router.replace('/'); // the session ended: go and sign in again
        } else if (error instanceof ApiError && error.status === 404) {
          setResult({ symbol, outcome: { phase: 'unknown' } });
        } else {
          setResult({ symbol, outcome: { phase: 'problem' } });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [signedIn, symbol, router]);

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

  const leave = () => void signOut().finally(() => router.replace('/'));

  if (symbol === null || outcome?.phase === 'unknown') {
    return (
      <AppShell email={me.user.email} onSignOut={leave}>
        <h1>Key facts</h1>
        <p className="muted">
          {symbol === null
            ? 'Choose a stock from the Stocks page.'
            : `We don’t know a stock called ${symbol}.`}
        </p>
        <BackToStocks />
      </AppShell>
    );
  }

  const insights = outcome?.phase === 'ready' ? outcome.insights : null;
  return (
    <AppShell email={me.user.email} onSignOut={leave}>
      <div className="stock-head">
        <h1>{insights?.name ?? symbol}</h1>
        <p className="symbol">{symbol}</p>
        <p className="muted">{NOT_ADVICE}</p>
      </div>
      {outcome?.phase === 'problem' && (
        <p role="alert" className="alert">
          {LOAD_FAILED}
        </p>
      )}
      {outcome === null && (
        <p role="status" className="muted">
          Loading key facts…
        </p>
      )}
      {insights && (
        <>
          <KeyFacts facts={insights.key_facts} />
          <DerivedValues derived={insights.derived} />
          <RecentSentiment sentiment={insights.sentiment} />
          <RecentEvents events={insights.events} />
        </>
      )}
    </AppShell>
  );
}
