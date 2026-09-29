'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { AppShell } from '@/components/AppShell';
import { Monogram } from '@/components/Monogram';
import { Citations } from '@/components/InsightSections';
import { ApiError } from '@/lib/api';
import {
  getMatches,
  OUTCOME_LABELS,
  STATUS_LABELS,
  type MatchOutcome,
  type MatchReason,
  type MatchResult,
  type StockMatch,
} from '@/lib/match';
import { signOut, useMe } from '@/lib/session';

const LOAD_FAILED = "We couldn't load the match. Reload the page to try again.";
const EMPTY_PROFILE =
  "Tell the chat your preferences first, for example: I'm conservative, dividend-focused and I avoid high debt.";

/** Preferences no stock can be judged on (they need share prices): shown once, above the cards. */
const PROFILE_WIDE = new Set(['value', 'horizon']);
const NOTHING_TO_CHECK =
  "Nothing in your profile can be checked against the filings yet. Value and a time horizon need share prices, which this app doesn't have. Add a debt or risk preference, or a dividend, growth, quality or momentum style, in the chat or its profile panel.";

/** The stocks without the profile-wide reasons, and those reasons once each. */
export function splitProfileWide(stocks: StockMatch[]): {
  cards: StockMatch[];
  wide: MatchReason[];
} {
  const wide = new Map<string, MatchReason>();
  const cards = stocks.map((stock) => ({
    ...stock,
    reasons: stock.reasons.filter((reason) => {
      if (!PROFILE_WIDE.has(reason.criterion)) return true;
      wide.set(reason.criterion, reason);
      return false;
    }),
  }));
  return { cards, wide: [...wide.values()] };
}

function WideNote({ reasons }: { reasons: MatchReason[] }) {
  if (reasons.length === 0) return null;
  return (
    <aside role="note" aria-label="Not judged for any stock" className="match-note">
      <h2>Not judged for any stock</h2>
      <ul>
        {reasons.map((reason) => (
          <li key={reason.criterion}>{reason.text}</li>
        ))}
      </ul>
    </aside>
  );
}

function MatchList({ stocks }: { stocks: StockMatch[] }) {
  const { cards, wide } = splitProfileWide(stocks);
  const nothingJudged = cards.every((stock) => stock.reasons.length === 0);
  return (
    <>
      <WideNote reasons={wide} />
      {nothingJudged ? (
        <div className="match-empty">
          <p>{NOTHING_TO_CHECK}</p>
          <a className="button" href="/chat/">
            Open the chat
          </a>
        </div>
      ) : (
        <div className="match-list">
          {cards.map((stock) => (
            <StockCard key={stock.symbol} stock={stock} />
          ))}
        </div>
      )}
    </>
  );
}

/** A small mark per outcome; the words beside it carry the meaning, the mark only backs them up. */
const OUTCOME_MARKS: Record<MatchOutcome, string> = {
  pass: '✓',
  miss: '!',
  fail: '✕',
  not_assessable: '?',
  no_data: '–',
};

function ReasonItem({ reason, caution }: { reason: MatchReason; caution: boolean }) {
  return (
    <li className={`match-reason outcome-${reason.outcome}`}>
      <span className="match-mark" aria-hidden="true">
        {caution ? '!' : OUTCOME_MARKS[reason.outcome]}
      </span>
      <div className="match-reason-body">
        <p className="match-reason-head">
          <span className="match-outcome">
            {caution ? 'Caution' : OUTCOME_LABELS[reason.outcome]}
          </span>
          {reason.hard && <span className="pill match-must">must-have</span>}
        </p>
        <p className="match-reason-text">{reason.text}</p>
        <Citations citations={reason.citations} />
      </div>
    </li>
  );
}

function StockCard({ stock }: { stock: StockMatch }) {
  return (
    <section className="match-card" aria-label={stock.name}>
      <div className="match-head">
        <Monogram symbol={stock.symbol} />
        <div className="match-title">
          <h2>{stock.name}</h2>
          <p className="muted match-symbol">{stock.symbol}</p>
        </div>
        <span className={`match-badge status-${stock.status}`}>
          <span className="dot" aria-hidden="true" />
          {STATUS_LABELS[stock.status]}
        </span>
      </div>
      {stock.reasons.length > 0 && (
        <ul className="match-reasons">
          {stock.reasons.map((reason) => (
            <ReasonItem
              key={`${reason.criterion}-${reason.preference}`}
              reason={reason}
              caution={false}
            />
          ))}
        </ul>
      )}
      {stock.cautions.length > 0 && (
        <ul className="match-reasons match-cautions">
          {stock.cautions.map((reason) => (
            <ReasonItem key={`${reason.criterion}-${reason.preference}`} reason={reason} caution />
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * How each stock fits the remembered profile (P14): a status, the reasons each with its cited
 * figure, and any cautions. Everything shown comes from the server's fixed rules; nothing here
 * judges anything.
 */
export function MatchView() {
  const me = useMe();
  const router = useRouter();
  const signedIn = me.status === 'signed-in';
  const [result, setResult] = useState<MatchResult | 'problem' | null>(null);

  useEffect(() => {
    if (me.status === 'signed-out') router.replace('/');
  }, [me.status, router]);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    getMatches().then(
      (matches) => {
        if (!cancelled) setResult(matches);
      },
      (error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) router.replace('/');
        else setResult('problem');
      },
    );
    return () => {
      cancelled = true;
    };
  }, [signedIn, router]);

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
  return (
    <AppShell email={me.user.email} onSignOut={leave} active="match">
      <h1>How the stocks fit your profile</h1>
      <p className="muted match-lede">
        Fixed rules over stored figures, each reason with its source.
      </p>
      {result === null && (
        <p role="status" className="muted">
          Loading…
        </p>
      )}
      {result === 'problem' && (
        <p role="alert" className="alert">
          {LOAD_FAILED}
        </p>
      )}
      {result !== null && result !== 'problem' && (
        <>
          {result.profile_empty ? (
            <div className="match-empty">
              <p>{EMPTY_PROFILE}</p>
              <a className="button" href="/chat/">
                Open the chat
              </a>
            </div>
          ) : (
            <MatchList stocks={result.stocks} />
          )}
          <p className="muted match-disclaimer">{result.disclaimer}</p>
        </>
      )}
    </AppShell>
  );
}
