/**
 * The stock page's Fundamentals card (the owner, 2026-09-30).
 *
 *   GET /api/v1/stocks/{symbol}/fundamentals   ten values in a fixed order, each with its status
 *
 * Most are screener.in's top ratios as the worker last read them from the stock's company page
 * (refreshed by "Update data"); EPS (TTM) and P/B are worked out from them, and debt to equity from
 * stored figures. A value the data does not have is said to be missing, never shown as zero.
 */

import { ApiError, apiFetch } from '@/lib/api';

export type FundamentalItem = {
  name: string;
  label: string; // "Mkt Cap", "P/E Ratio (TTM)" ...
  status: 'ok' | 'not_available' | 'not_applicable';
  value: string | null; // a plain decimal, "123456" or "22.5"
  unit: 'INR_CRORE' | 'INR_PER_SHARE' | 'PERCENT' | 'RATIO';
  source: 'screener' | 'computed' | 'none';
  note: string | null; // how it was worked out, or why there is none
};

export type Fundamentals = {
  symbol: string;
  as_of: string | null; // when screener.in's figures were read
  source_url: string | null;
  items: FundamentalItem[];
};

const text = (value: unknown): value is string => typeof value === 'string';
const orNull = (value: unknown): boolean => value === null || text(value);
const DECIMAL = /^-?\d+(?:\.\d+)?$/;

function isItem(value: unknown): value is FundamentalItem {
  const i = (value ?? {}) as Partial<FundamentalItem>;
  return (
    text(i.name) &&
    text(i.label) &&
    ['ok', 'not_available', 'not_applicable'].includes(i.status as string) &&
    (i.value === null || (text(i.value) && DECIMAL.test(i.value))) &&
    ['INR_CRORE', 'INR_PER_SHARE', 'PERCENT', 'RATIO'].includes(i.unit as string) &&
    ['screener', 'computed', 'none'].includes(i.source as string) &&
    orNull(i.note)
  );
}

function isFundamentals(value: unknown): value is Fundamentals {
  const f = (value ?? {}) as Partial<Fundamentals>;
  return (
    text(f.symbol) &&
    orNull(f.as_of) &&
    orNull(f.source_url) &&
    Array.isArray(f.items) &&
    f.items.every(isItem)
  );
}

export async function getFundamentals(symbol: string): Promise<Fundamentals> {
  const body = await apiFetch(`/api/v1/stocks/${encodeURIComponent(symbol)}/fundamentals`);
  if (!isFundamentals(body)) throw new ApiError(200, 'unexpected_response');
  return body;
}

const INDIAN = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const INDIAN_PAISE = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });

/** "₹1,23,456 Cr", "₹137.78", "25.5%", "22.5"; "Not available" or "Not applicable" otherwise. */
export function fundamentalValue(item: FundamentalItem): string {
  if (item.status === 'not_applicable') return 'Not applicable';
  if (item.status !== 'ok' || item.value === null) return 'Not available';
  const number = Number(item.value);
  switch (item.unit) {
    case 'INR_CRORE':
      return `₹${INDIAN.format(number)} Cr`;
    case 'INR_PER_SHARE':
      return `₹${INDIAN_PAISE.format(number)}`;
    case 'PERCENT':
      return `${INDIAN_PAISE.format(number)}%`;
    default:
      return INDIAN_PAISE.format(number);
  }
}
