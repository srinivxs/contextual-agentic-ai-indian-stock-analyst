'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { AppShell } from '@/components/AppShell';
import { ApiError } from '@/lib/api';
import {
  isStillWorking,
  listDocuments,
  officialLink,
  type DocumentStatus,
  type StockDocument,
} from '@/lib/documents';
import { signOut, useMe } from '@/lib/session';
import { listStocks, type Stock } from '@/lib/stocks';

/** How often the page asks again while the worker is still processing something. */
export const REFRESH_MS = 5000;

const LOAD_FAILED = "We couldn't load the documents. Reload the page to try again.";

const STATUS_LABELS: Record<DocumentStatus, string> = {
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
    const lists = await Promise.all(stocks.map((stock) => listDocuments(stock.symbol)));
    return { ok: true, shelves: stocks.map((stock, i) => ({ stock, documents: lists[i] ?? [] })) };
  } catch (error) {
    return { ok: false, unauthorized: error instanceof ApiError && error.status === 401 };
  }
}

/**
 * Every stock's documents: the official filings the worker fetched from BSE (ADR 018) and anything
 * uploaded. While any document is still waiting or processing, the page refreshes itself.
 */
export function DocumentsView() {
  const me = useMe();
  const router = useRouter();
  const [shelves, setShelves] = useState<Shelf[] | null>(null);
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

  return (
    <AppShell
      email={me.user.email}
      onSignOut={() => void signOut().finally(() => router.replace('/'))}
    >
      <h1>Documents</h1>
      <p className="muted">
        Official filings are fetched from BSE automatically: the latest earnings-call transcripts
        and annual report for each stock.
      </p>
      {problem && (
        <p role="alert" className="alert">
          {problem}
        </p>
      )}
      {shelves?.map(({ stock, documents }) => (
        <section key={stock.symbol} aria-labelledby={`docs-${stock.symbol}`} className="card">
          <h2 id={`docs-${stock.symbol}`}>{stock.name}</h2>
          {documents.length === 0 ? (
            <p className="muted">No documents yet.</p>
          ) : (
            <ul className="documents">
              {documents.map((document) => {
                const link = officialLink(document);
                return (
                  <li key={document.id}>
                    <span>{document.title}</span>{' '}
                    <span className={`status ${document.status}`}>
                      {STATUS_LABELS[document.status]}
                    </span>
                    {document.page_count !== null && (
                      <span className="muted"> · {document.page_count} pages</span>
                    )}
                    {link && (
                      <>
                        {' '}
                        <a href={link} target="_blank" rel="noopener noreferrer">
                          Official filing (BSE)
                        </a>
                      </>
                    )}
                    {document.status === 'failed' && document.failure_reason && (
                      <p className="alert">{document.failure_reason}</p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      ))}
    </AppShell>
  );
}
