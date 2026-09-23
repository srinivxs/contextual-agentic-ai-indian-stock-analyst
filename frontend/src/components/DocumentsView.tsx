'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { AppShell } from '@/components/AppShell';
import { ApiError } from '@/lib/api';
import {
  documentLabel,
  groupDocuments,
  isStillWorking,
  listAllDocuments,
  officialLink,
  pagesLabel,
  summaryLabel,
  type DocumentStatus,
  type StockDocument,
} from '@/lib/documents';
import {
  availableLabel,
  canCheck,
  getFilingCheck,
  lastCheckedLabel,
  startFilingCheck,
  type FilingCheck,
} from '@/lib/filingChecks';
import { signOut, useMe } from '@/lib/session';
import { listStocks, type Stock } from '@/lib/stocks';

/** How often the page asks again while the worker is still processing something. */
export const REFRESH_MS = 5000;

const LOAD_FAILED = "We couldn't load the documents. Reload the page to try again.";
const CHECK_FAILED = "We couldn't start the check. Try again in a moment.";

// A finished document needs no badge: the absence of one reads as "ready".
const STATUS_BADGES: Partial<Record<DocumentStatus, string>> = {
  pending: 'Waiting',
  processing: 'Processing',
  failed: 'Failed',
};

type Shelf = { stock: Stock; documents: StockDocument[]; check: FilingCheck };

type Loaded = { ok: true; shelves: Shelf[] } | { ok: false; unauthorized: boolean };

/** The earliest time, still ahead, at which some stock's button turns back on. */
function earliestFutureCheck(shelves: Shelf[] | null): number | null {
  const now = Date.now();
  const times = (shelves ?? [])
    .map((shelf) => (shelf.check.next_check_at ? Date.parse(shelf.check.next_check_at) : NaN))
    .filter((time) => time > now);
  return times.length > 0 ? Math.min(...times) : null;
}

async function readShelves(): Promise<Loaded> {
  try {
    const stocks = await listStocks();
    const shelves = await Promise.all(
      stocks.map(async (stock) => {
        const [documents, check] = await Promise.all([
          listAllDocuments(stock.symbol),
          getFilingCheck(stock.symbol),
        ]);
        return { stock, documents, check };
      }),
    );
    return { ok: true, shelves };
  } catch (error) {
    return { ok: false, unauthorized: error instanceof ApiError && error.status === 401 };
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
        {badge && <span className={`pill ${document.status}`}>{badge}</span>}
      </span>
    </li>
  );
}

/** "Last checked 14:05 (12 minutes ago)" and the "Check for new filings" button. */
function Freshness({ check, onCheck }: { check: FilingCheck; onCheck: () => void }) {
  if (!check.enabled) {
    return (
      <div className="doc-freshness">
        <p className="muted">Automatic filing checks are switched off.</p>
      </div>
    );
  }
  const now = new Date();
  const waitUntil = !check.checking && !canCheck(check, now) ? check.next_check_at : null;
  return (
    <div className="doc-freshness">
      <p className="muted" role="status">
        {check.checking ? 'Checking for new filings…' : lastCheckedLabel(check, now)}
      </p>
      <span className="doc-check">
        {waitUntil && <span className="muted">{availableLabel(waitUntil)}</span>}
        <button
          type="button"
          className="button secondary"
          disabled={!canCheck(check, now)}
          onClick={onCheck}
        >
          {check.checking ? 'Checking…' : 'Check for new filings'}
        </button>
      </span>
    </div>
  );
}

function StockPanel({ shelf, onCheck }: { shelf: Shelf; onCheck: () => void }) {
  const groups = groupDocuments(shelf.documents);
  return (
    <div
      role="tabpanel"
      id={`panel-${shelf.stock.symbol}`}
      aria-label={shelf.stock.name}
      className="doc-panel"
    >
      <Freshness check={shelf.check} onCheck={onCheck} />
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
 * (ADR 018), grouped by kind and newest first, with when the stock was last checked and a button
 * to check it again (at most once an hour). While a check runs or a document is still waiting or
 * processing, the page refreshes itself; when the hour is up, it refreshes once to turn the button
 * back on.
 */
export function DocumentsView() {
  const me = useMe();
  const router = useRouter();
  const [shelves, setShelves] = useState<Shelf[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
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

  // Then load again every REFRESH_MS, but only while something is still being worked on.
  const working =
    shelves?.some((shelf) => shelf.check.checking || isStillWorking(shelf.documents)) ?? false;
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

  // And once more when the earliest cooldown ends, so the button turns itself back on.
  const nextCheckAt = earliestFutureCheck(shelves);
  useEffect(() => {
    if (!signedIn || nextCheckAt === null) return;
    let cancelled = false;
    const timer = setTimeout(
      () => {
        void readShelves().then((result) => {
          if (!cancelled) show(result);
        });
      },
      Math.max(0, nextCheckAt - Date.now()) + 1000,
    );
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [signedIn, nextCheckAt, show]);

  const check = async (symbol: string): Promise<void> => {
    try {
      await startFilingCheck(symbol); // "too_soon" needs no message: the new status explains it
    } catch {
      setProblem(CHECK_FAILED);
      return;
    }
    show(await readShelves());
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

  const current = shelves?.find((s) => s.stock.symbol === selected) ?? shelves?.[0] ?? null;

  return (
    <AppShell
      email={me.user.email}
      onSignOut={() => void signOut().finally(() => router.replace('/'))}
    >
      <h1>Documents</h1>
      <p className="muted">
        Official BSE filings from the last three years (earnings calls, presentations, annual
        reports and announcements), fetched automatically every day, when you follow a stock, and
        when you ask. Open any one to read the original.
      </p>
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
          <div role="tablist" aria-label="Stocks" className="tabs">
            {shelves.map((shelf) => {
              const active = shelf.stock.symbol === current.stock.symbol;
              return (
                <button
                  key={shelf.stock.symbol}
                  type="button"
                  role="tab"
                  id={`tab-${shelf.stock.symbol}`}
                  aria-selected={active}
                  aria-controls={`panel-${shelf.stock.symbol}`}
                  className={active ? 'tab active' : 'tab'}
                  onClick={() => setSelected(shelf.stock.symbol)}
                >
                  {shelf.stock.name}
                  <span className="count">{shelf.documents.length}</span>
                </button>
              );
            })}
          </div>
          <StockPanel shelf={current} onCheck={() => void check(current.stock.symbol)} />
        </div>
      )}
    </AppShell>
  );
}
