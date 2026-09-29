'use client';

import { useEffect, useRef, useState } from 'react';

import { RefreshIcon } from '@/components/Icons';
import { ApiError } from '@/lib/api';
import {
  clockOf,
  dataNote,
  getDataStatus,
  refreshData,
  refreshMessage,
  type DataStatus,
} from '@/lib/dataStatus';

/** How often the note asks again while an update is still running. */
export const DATA_POLL_MS = 15_000;
const UPDATE_FAILED = "We couldn't start the update. Try again in a moment.";
const TOO_SOON = 'Data was updated less than an hour ago.';

export type Freshness = {
  status: DataStatus | null; // null until read, or if it could not be read (then no note)
  message: string | null; // what the last click did
  busy: boolean; // a click is being answered
  waitUntil: string | null; // "Update data" works again at this time (once an hour)
  update: () => void;
};

/**
 * The data status every page shows, read once, again every DATA_POLL_MS while updating, and again
 * when the hour is up. ``onUpdated`` runs when an update finishes, so a page can load its data
 * again (the stock page's fundamentals, prices and facts).
 */
export function useFreshness(onUpdated?: () => void): Freshness {
  const [status, setStatus] = useState<DataStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const updating = status?.updating ?? false;
  const nextAt = status?.next_update_at ?? null;
  const waitUntil = nextAt !== null && Date.parse(nextAt) > now ? nextAt : null;

  // An update that was running and is now over: tell the page.
  const wasUpdating = useRef(false);
  const callback = useRef(onUpdated);
  useEffect(() => {
    callback.current = onUpdated; // the latest one, kept outside rendering
  });
  useEffect(() => {
    if (wasUpdating.current && !updating) callback.current?.();
    wasUpdating.current = updating;
  }, [updating]);

  // Turn the button back on when the hour is up.
  useEffect(() => {
    if (waitUntil === null) return;
    const timer = setTimeout(() => setNow(Date.now()), Date.parse(waitUntil) - Date.now() + 500);
    return () => clearTimeout(timer);
  }, [waitUntil]);

  useEffect(() => {
    let cancelled = false;
    const read = () =>
      getDataStatus().then(
        (found) => {
          if (!cancelled) setStatus(found);
        },
        () => {}, // no note rather than a wrong one; the page's own loading handles sign-in
      );
    void read();
    const timer = updating ? setInterval(() => void read(), DATA_POLL_MS) : undefined;
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [updating]);

  const update = () => {
    setBusy(true);
    setMessage(null);
    refreshData()
      .then(
        (result) => {
          setStatus(result.status);
          setMessage(refreshMessage(result));
        },
        (error: unknown) => {
          if (error instanceof ApiError && error.status === 429) {
            setMessage(TOO_SOON);
            void getDataStatus().then(setStatus, () => {}); // learn when it works again
          } else {
            setMessage(UPDATE_FAILED);
          }
        },
      )
      .finally(() => setBusy(false));
  };

  return { status, message, busy, waitUntil, update };
}

/**
 * One click: new filings (and screener.in's figures), share prices and RBI releases. Then off for
 * an hour (the owner's rule), saying when it works again.
 */
export function UpdateDataButton({ freshness }: { freshness: Freshness }) {
  const next = freshness.waitUntil;
  return (
    <button
      type="button"
      className="button secondary update-data"
      onClick={freshness.update}
      disabled={freshness.busy || next !== null}
      title={next ? `Updated recently: next update after ${clockOf(next)}` : undefined}
    >
      <RefreshIcon />
      Update data
    </button>
  );
}

/** The disclaimer under the top bar: the date the data is updated to, and that it is not live. */
export function DataNote({ freshness }: { freshness: Freshness }) {
  return (
    <>
      {freshness.status && (
        <p className="data-note" role="note" aria-label="Data freshness">
          {dataNote(freshness.status)}
        </p>
      )}
      {freshness.message && (
        <p className="data-message" role="status">
          {freshness.message}
        </p>
      )}
    </>
  );
}
