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
import { signOut, useMe } from '@/lib/session';
import { listStocks, type Stock } from '@/lib/stocks';

/** How often the page asks again while the worker is still processing something. */
export const REFRESH_MS = 5000;

const LOAD_FAILED = "We couldn't load the documents. Reload the page to try again.";

// A finished document needs no badge: the absence of one reads as "ready".
const STATUS_BADGES: Partial<Record<DocumentStatus, string>> = {
  pending: 'Waiting',
  processing: 'Processing',
  failed: 'Failed',
};

type Shelf = { stock: Stock; documents: StockDocument[] };

type Loaded = { ok: true; shelves: Shelf[] } | { ok: false; unauthorized: boolean };

async function readShelves(): Promise<Loaded> {
  try {
    const stocks = await listStocks();
    const lists = await Promise.all(stocks.map((stock) => listAllDocuments(stock.symbol)));
    return { ok: true, shelves: stocks.map((stock, i) => ({ stock, documents: lists[i] ?? [] })) };
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

function StockPanel({ shelf }: { shelf: Shelf }) {
  const groups = groupDocuments(shelf.documents);
  return (
    <div
      role="tabpanel"
      id={`panel-${shelf.stock.symbol}`}
      aria-label={shelf.stock.name}
      className="doc-panel"
    >
      {groups.length === 0 ? (
        <p className="muted empty">No documents yet. New filings appear here within a day.</p>
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
 * (ADR 018), grouped by kind and newest first. While any document is still
 * waiting or processing, the page refreshes itself.
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
        reports and announcements), fetched automatically every day. Open any one to read the
        original.
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
          <StockPanel shelf={current} />
        </div>
      )}
    </AppShell>
  );
}
