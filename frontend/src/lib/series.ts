/**
 * One metric over the years for one stock, for the Home charts. The server sends ONLY screener.in's
 * consolidated, rupee, full-year figures (one source, so years compare like with like); this file
 * asks for them, checks the shape, and works out the latest value and its change on the year
 * before. Nothing here converts money or invents a year.
 */

import { ApiError, apiFetch } from '@/lib/api';
import { isCitation, type Citation } from '@/lib/insights';

const METRICS = ['net_profit', 'revenue_from_operations', 'net_interest_income'] as const;

export type SeriesMetric = (typeof METRICS)[number];

export type SeriesPoint = {
  /** "FY2026". */
  period: string;
  /** A decimal as text. */
  value: string;
  citation: Citation;
};

export type Series = {
  symbol: string;
  metric: SeriesMetric;
  label: string;
  unit: 'INR_CRORE';
  source: string;
  /** Oldest first, at most ten. */
  points: SeriesPoint[];
};

const text = (value: unknown): value is string => typeof value === 'string';
const FULL_YEAR = /^FY\d{4}$/;
const DECIMAL = /^-?\d+(\.\d+)?$/;

function isPoint(value: unknown): value is SeriesPoint {
  const p = (value ?? {}) as Partial<SeriesPoint>;
  return (
    text(p.period) &&
    FULL_YEAR.test(p.period) &&
    text(p.value) &&
    DECIMAL.test(p.value) &&
    isCitation(p.citation)
  );
}

function isSeries(value: unknown): value is Series {
  const s = (value ?? {}) as Partial<Series>;
  return (
    text(s.symbol) &&
    text(s.metric) &&
    (METRICS as readonly string[]).includes(s.metric) &&
    text(s.label) &&
    s.unit === 'INR_CRORE' &&
    text(s.source) &&
    Array.isArray(s.points) &&
    s.points.every(isPoint)
  );
}

export async function getSeries(
  symbol: string,
  metric: SeriesMetric = 'net_profit',
): Promise<Series> {
  // encodeURIComponent so a symbol can never add path segments ("/", "..") or a query string.
  const body = await apiFetch(
    `/api/v1/stocks/${encodeURIComponent(symbol)}/series?metric=${metric}`,
  );
  if (!isSeries(body)) throw new ApiError(200, 'unexpected_response');
  return body;
}

export type Change = {
  period: string;
  value: number;
  /** The year the change is measured against; null when it is not in the data. */
  previousPeriod: string | null;
  /** Null unless the year before is there and was a profit (a percent on a loss misleads). */
  percent: number | null;
};

const yearOf = (period: string): number => Number(period.slice(2));

/** The latest figure and its percent change on the year before, or null for no points. */
export function change(points: SeriesPoint[]): Change | null {
  const sorted = [...points].sort((a, b) => yearOf(a.period) - yearOf(b.period));
  const latest = sorted.at(-1);
  if (!latest) return null;
  const value = Number(latest.value);
  const before = sorted.at(-2);
  const consecutive = before !== undefined && yearOf(before.period) === yearOf(latest.period) - 1;
  const base = consecutive ? Number(before.value) : 0;
  return {
    period: latest.period,
    value,
    previousPeriod: consecutive ? before.period : null,
    percent: consecutive && base > 0 ? ((value - base) / base) * 100 : null,
  };
}

/** "+12.3%", "-4.1%", "0.0%", or null when there is no percent. */
export function percentLabel(percent: number | null): string | null {
  if (percent === null) return null;
  return `${percent > 0 ? '+' : ''}${percent.toFixed(1)}%`;
}

export type Direction = 'rise' | 'fall' | 'flat';

export function direction(percent: number | null): Direction {
  if (percent === null || percent === 0) return 'flat';
  return percent > 0 ? 'rise' : 'fall';
}

const INDIAN = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });

/** "₹1,23,456" (Indian digit grouping); the unit ("crore") is written by the caller. */
export function rupees(value: number): string {
  return `${value < 0 ? '-' : ''}₹${INDIAN.format(Math.abs(value))}`;
}
