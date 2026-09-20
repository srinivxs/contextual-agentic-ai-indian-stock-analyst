'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { AppShell } from '@/components/AppShell';
import { StockCard } from '@/components/StockCard';
import { ApiError } from '@/lib/api';
import { signOut, useMe } from '@/lib/session';
import { followStock, listStocks, unfollowStock, type Stock } from '@/lib/stocks';

const LOAD_FAILED = "We couldn't load your stocks. Reload the page to try again.";
const UPDATE_FAILED = "We couldn't update that follow. Please try again.";
const SIGN_OUT_FAILED = "We couldn't sign you out. Please try again.";

const isUnauthorized = (error: unknown): boolean => error instanceof ApiError && error.status === 401;

type Loaded = { ok: true; stocks: Stock[] } | { ok: false; unauthorized: boolean };

/** Fetch the list and report what happened; deciding what to do about it is the component's job. */
async function readStocks(): Promise<Loaded> {
  try {
    return { ok: true, stocks: await listStocks() };
  } catch (error) {
    return { ok: false, unauthorized: isUnauthorized(error) };
  }
}

/**
 * The three stocks with a follow toggle. The server is the source of truth: after every change the
 * list is fetched again, so what is on screen is what the database holds.
 */
export function StocksView() {
  const me = useMe();
  const router = useRouter();
  const [stocks, setStocks] = useState<Stock[] | null>(null);
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const [problem, setProblem] = useState<string | null>(null);

  const signedIn = me.status === 'signed-in';

  const show = useCallback(
    (result: Loaded) => {
      if (result.ok) {
        setStocks(result.stocks);
        setProblem(null);
      } else if (result.unauthorized) {
        router.replace('/'); // the session ended: go and sign in again
      } else {
        setProblem(LOAD_FAILED); // anything else is a failure of ours, not a sign-out
      }
    },
    [router],
  );

  useEffect(() => {
    if (me.status === 'signed-out') router.replace('/');
  }, [me.status, router]);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    void readStocks().then((result) => {
      if (!cancelled) show(result);
    });
    return () => {
      cancelled = true;
    };
  }, [signedIn, show]);

  const toggle = async (stock: Stock): Promise<void> => {
    if (busy.has(stock.symbol)) return;
    setProblem(null);
    setBusy((current) => new Set(current).add(stock.symbol));
    try {
      if (stock.followed) await unfollowStock(stock.symbol);
      else await followStock(stock.symbol);
      show(await readStocks());
    } catch (error) {
      if (isUnauthorized(error)) router.replace('/');
      else setProblem(UPDATE_FAILED);
    } finally {
      setBusy((current) => {
        const next = new Set(current);
        next.delete(stock.symbol);
        return next;
      });
    }
  };

  const leave = async (): Promise<void> => {
    setProblem(null);
    try {
      await signOut();
      router.replace('/');
    } catch (error) {
      if (isUnauthorized(error)) router.replace('/'); // the session was already gone
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
    <AppShell email={me.user.email} onSignOut={() => void leave()}>
      <h1>Stocks</h1>
      <p className="muted">Follow the stocks you want to keep an eye on.</p>
      {problem && (
        <p role="alert" className="alert">
          {problem}
        </p>
      )}
      <div className="grid">
        {stocks?.map((stock) => (
          <StockCard
            key={stock.symbol}
            stock={stock}
            busy={busy.has(stock.symbol)}
            onToggle={(chosen) => void toggle(chosen)}
          />
        ))}
      </div>
    </AppShell>
  );
}
