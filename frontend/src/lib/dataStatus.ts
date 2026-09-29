/**
 * How fresh the data is, and the one-click update (the owner, 2026-09-29).
 *
 *   GET  /api/v1/data/status    the dates the data is updated to, and whether an update runs
 *   POST /api/v1/data/refresh   queue every update its limits allow; what happened per source
 *
 * The server only queues jobs for its worker, each within its own limit (filings once an hour,
 * RBI releases every 15 minutes, prices one run at a time), so a click can never hammer a source.
 * Every page shows the note this module writes, a disclaimer that the data is not live.
 */

import { ApiError, apiFetch } from '@/lib/api';

export type DataStatus = {
  updating: boolean;
  /** The newest finished filings check (a timestamp), or null. */
  filings_checked_at: string | null;
  /** The newest stored close, "2026-09-28", or null. */
  prices_to: string | null;
  /** The newest live RBI release stored (a timestamp), or null. */
  rbi_to: string | null;
  /** When "Update data" works again (once an hour); null means now. */
  next_update_at: string | null;
  filings_on: boolean;
  prices_on: boolean;
  /** False: the RBI tab shows sample items, not the live feed. */
  rbi_live: boolean;
};

export type RefreshResult = {
  filings: 'queued' | 'recent' | 'off';
  prices: 'queued' | 'recent' | 'current' | 'off';
  rbi: 'queued' | 'recent';
  status: DataStatus;
};

const text = (value: unknown): value is string => typeof value === 'string';
const orNull = (value: unknown): boolean => value === null || text(value);
const bool = (value: unknown): value is boolean => typeof value === 'boolean';

function isStatus(value: unknown): value is DataStatus {
  const s = (value ?? {}) as Partial<DataStatus>;
  return (
    bool(s.updating) &&
    orNull(s.filings_checked_at) &&
    orNull(s.prices_to) &&
    orNull(s.rbi_to) &&
    orNull(s.next_update_at) &&
    bool(s.filings_on) &&
    bool(s.prices_on) &&
    bool(s.rbi_live)
  );
}

function isRefresh(value: unknown): value is RefreshResult {
  const r = (value ?? {}) as Partial<RefreshResult>;
  return (
    ['queued', 'recent', 'off'].includes(r.filings as string) &&
    ['queued', 'recent', 'current', 'off'].includes(r.prices as string) &&
    ['queued', 'recent'].includes(r.rbi as string) &&
    isStatus(r.status)
  );
}

export async function getDataStatus(): Promise<DataStatus> {
  const body = await apiFetch('/api/v1/data/status');
  if (!isStatus(body)) throw new ApiError(200, 'unexpected_response');
  return body;
}

export async function refreshData(): Promise<RefreshResult> {
  const body = await apiFetch('/api/v1/data/refresh', { method: 'POST' });
  if (!isRefresh(body)) throw new ApiError(202, 'unexpected_response');
  return body;
}

/** "2026-09-28" stays that day; a timestamp is the reader's own local day. */
function dayOf(iso: string): Date {
  const plain = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return plain ? new Date(Number(plain[1]), Number(plain[2]) - 1, Number(plain[3])) : new Date(iso);
}

// Written by hand: locales disagree ("Sep" or "Sept"), and the note should read the same for all.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const label = (day: Date): string =>
  `${day.getDate()} ${MONTHS[day.getMonth()] ?? ''} ${day.getFullYear()}`;

/**
 * The disclaimer every page shows: "Data updated to 29 Sep 2026: filings checked 29 Sep 2026,
 * share prices to the 28 Sep 2026 close, RBI releases to 29 Sep 2026. Not live data."
 */
export function dataNote(status: DataStatus): string {
  const filings = status.filings_checked_at ? dayOf(status.filings_checked_at) : null;
  const prices = status.prices_to ? dayOf(status.prices_to) : null;
  const rbi = status.rbi_live && status.rbi_to ? dayOf(status.rbi_to) : null;
  const parts = [
    filings ? `filings checked ${label(filings)}` : 'filings not checked yet',
    prices ? `share prices to the ${label(prices)} close` : 'no share prices yet',
    status.rbi_live
      ? rbi
        ? `RBI releases to ${label(rbi)}`
        : 'no RBI releases yet'
      : 'RBI releases are sample items',
  ];
  const known = [filings, prices, rbi].filter((day): day is Date => day !== null);
  const newest = known.length ? new Date(Math.max(...known.map((day) => day.getTime()))) : null;
  const head = newest ? `Data updated to ${label(newest)}` : 'No data updated yet';
  const note = `${head}: ${parts.join(', ')}. Not live data.`;
  return status.updating ? `Updating… ${note}` : note;
}

/** "16:05" in the reader's own time: when "Update data" works again. */
export function clockOf(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

/** What a click on "Update data" did, in one or two sentences. */
export function refreshMessage(result: RefreshResult): string {
  const updating = [
    result.filings === 'queued' && 'filings',
    result.prices === 'queued' && 'share prices',
    result.rbi === 'queued' && 'RBI releases',
  ].filter((part): part is string => Boolean(part));
  const listed =
    updating.length > 1
      ? `${updating.slice(0, -1).join(', ')} and ${updating.at(-1) ?? ''}`
      : (updating[0] ?? '');
  const off = [
    result.filings === 'off' && 'Automatic filings are switched off.',
    result.prices === 'off' && 'Share prices are switched off.',
  ].filter((part): part is string => Boolean(part));
  const lead = updating.length
    ? `Updating ${listed}. New data appears within a few minutes.`
    : 'Already up to date: filings are checked at most once an hour and RBI releases every 15 minutes.';
  return [lead, ...off].join(' ');
}
