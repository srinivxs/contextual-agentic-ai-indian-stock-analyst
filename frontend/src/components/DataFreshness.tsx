'use client';

import { useEffect, useState } from 'react';

import { RefreshIcon } from '@/components/Icons';
import {
  dataNote,
  getDataStatus,
  refreshData,
  refreshMessage,
  type DataStatus,
} from '@/lib/dataStatus';

/** How often the note asks again while an update is still running. */
export const DATA_POLL_MS = 15_000;
const UPDATE_FAILED = "We couldn't start the update. Try again in a moment.";

export type Freshness = {
  status: DataStatus | null; // null until read, or if it could not be read (then no note)
  message: string | null; // what the last click did
  busy: boolean; // a click is being answered
  update: () => void;
};

/** The data status every page shows, read once and again every DATA_POLL_MS while updating. */
export function useFreshness(): Freshness {
  const [status, setStatus] = useState<DataStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const updating = status?.updating ?? false;

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
        () => setMessage(UPDATE_FAILED),
      )
      .finally(() => setBusy(false));
  };

  return { status, message, busy, update };
}

/** One click: new filings (and screener.in's figures), share prices and RBI releases. */
export function UpdateDataButton({ freshness }: { freshness: Freshness }) {
  return (
    <button
      type="button"
      className="button secondary update-data"
      onClick={freshness.update}
      disabled={freshness.busy}
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
