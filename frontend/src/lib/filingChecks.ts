/**
 * Checking a stock for new filings on demand (ADR 018): the status the Documents page shows next
 * to each stock, the "Check for new filings" button, and the small labels around it.
 *
 * The server does the real work and enforces the rules (one check per stock per hour, for everyone
 * together). The page only mirrors them, so the button is off when it would be refused anyway.
 */

import { ApiError, apiFetch } from '@/lib/api';

export type FilingCheck = {
  /** False when automatic filings are switched off: there is nothing to check with. */
  enabled: boolean;
  /** A check, or a download it queued, is still waiting or running. */
  checking: boolean;
  /** When the newest finished check ended; null if the stock was never checked. */
  last_checked_at: string | null;
  /** When the button works again; null means now. */
  next_check_at: string | null;
};

const isTime = (value: unknown): boolean =>
  value === null || (typeof value === 'string' && !Number.isNaN(Date.parse(value)));

function isFilingCheck(value: unknown): value is FilingCheck {
  const c = value as Partial<FilingCheck> | null;
  return (
    typeof c?.enabled === 'boolean' &&
    typeof c.checking === 'boolean' &&
    isTime(c.last_checked_at) &&
    isTime(c.next_check_at)
  );
}

// encodeURIComponent so a symbol can never add path segments ("/", "..") or a query string.
const checkPath = (symbol: string): string =>
  `/api/v1/stocks/${encodeURIComponent(symbol)}/filings/check`;

export async function getFilingCheck(symbol: string): Promise<FilingCheck> {
  const body = await apiFetch(checkPath(symbol));
  if (!isFilingCheck(body)) throw new ApiError(200, 'unexpected_response');
  return body;
}

/**
 * Ask for a check now. "too_soon" means the stock was checked within the hour (perhaps by
 * someone else a moment ago): not an error, the page just shows the fresh status.
 */
export async function startFilingCheck(symbol: string): Promise<'started' | 'too_soon'> {
  try {
    await apiFetch(checkPath(symbol), { method: 'POST' });
    return 'started';
  } catch (error) {
    if (error instanceof ApiError && error.status === 429) return 'too_soon';
    throw error;
  }
}

export function canCheck(check: FilingCheck, now: Date): boolean {
  if (!check.enabled || check.checking) return false;
  return check.next_check_at === null || Date.parse(check.next_check_at) <= now.getTime();
}

// Times are shown in India time, whatever the viewer's own clock: the filings are Indian, and it
// keeps the page (and its tests) the same everywhere.
const CLOCK = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  timeZone: 'Asia/Kolkata',
});

const clock = (iso: string): string => CLOCK.format(new Date(iso));

const plural = (n: number, unit: string): string => `${n} ${unit}${n === 1 ? '' : 's'} ago`;

function ago(iso: string, now: Date): string {
  const minutes = Math.floor((now.getTime() - Date.parse(iso)) / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return plural(minutes, 'minute');
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return plural(hours, 'hour');
  return plural(Math.floor(hours / 24), 'day');
}

/** "Last checked 14:05 (12 minutes ago)", or "Not checked yet". */
export function lastCheckedLabel(check: FilingCheck, now: Date): string {
  if (check.last_checked_at === null) return 'Not checked yet';
  return `Last checked ${clock(check.last_checked_at)} (${ago(check.last_checked_at, now)})`;
}

/** "Available at 15:05". */
export function availableLabel(nextCheckAt: string): string {
  return `Available at ${clock(nextCheckAt)}`;
}
