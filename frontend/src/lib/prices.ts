/**
 * End-of-day share prices for one stock, from BSE's daily price files (not live). The server sends
 * the latest close, a year of closes adjusted for bonus issues and splits, returns, volatility,
 * P/E and dividend yield; this file asks for them, checks the shape, and formats them. Nothing
 * here estimates a price or fills a gap.
 */

import { ApiError, apiFetch } from '@/lib/api';
import { eventDateLabel, isCitation, type Citation } from '@/lib/insights';

export type PriceLatest = {
  date: string;
  close: string;
  prev_close: string | null;
  change_pct: string | null;
  citation: Citation;
};

export type PricePoint = { date: string; close: string };

export const RETURN_KEYS = ['1m', '3m', '6m', '1y'] as const;
export type ReturnKey = (typeof RETURN_KEYS)[number];

/** P/E or dividend yield: a value, or the reason there is none. */
export type PriceRatio = {
  status: string;
  value: string | null;
  reason: string;
  citations: Citation[];
};

export type PriceAction = { date: string; factor: string };

export type Prices = {
  symbol: string;
  source: string;
  latest: PriceLatest | null;
  /** Oldest first, adjusted for bonus issues and splits. */
  history: PricePoint[];
  returns: Record<ReturnKey, string | null>;
  volatility_1y: string | null;
  pe: PriceRatio;
  dividend_yield: PriceRatio;
  actions: PriceAction[];
};

const text = (value: unknown): value is string => typeof value === 'string';
const DECIMAL = /^-?\d+(\.\d+)?$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const decimal = (value: unknown): value is string => text(value) && DECIMAL.test(value);
const decimalOrNull = (value: unknown): boolean => value === null || decimal(value);
const date = (value: unknown): boolean => text(value) && DATE.test(value);

function isLatest(value: unknown): boolean {
  const l = (value ?? {}) as Partial<PriceLatest>;
  return (
    date(l.date) &&
    decimal(l.close) &&
    decimalOrNull(l.prev_close) &&
    decimalOrNull(l.change_pct) &&
    isCitation(l.citation)
  );
}

function isPoint(value: unknown): boolean {
  const p = (value ?? {}) as Partial<PricePoint>;
  return date(p.date) && decimal(p.close);
}

function isRatio(value: unknown): boolean {
  const r = (value ?? {}) as Partial<PriceRatio>;
  return (
    text(r.status) &&
    decimalOrNull(r.value) &&
    text(r.reason) &&
    Array.isArray(r.citations) &&
    r.citations.every(isCitation)
  );
}

function isAction(value: unknown): boolean {
  const a = (value ?? {}) as Partial<PriceAction>;
  return date(a.date) && decimal(a.factor);
}

function isPrices(value: unknown): value is Prices {
  const p = (value ?? {}) as Partial<Prices>;
  const returns = (p.returns ?? {}) as Record<string, unknown>;
  return (
    text(p.symbol) &&
    text(p.source) &&
    (p.latest === null || isLatest(p.latest)) &&
    Array.isArray(p.history) &&
    p.history.every(isPoint) &&
    RETURN_KEYS.every((key) => key in returns && decimalOrNull(returns[key])) &&
    p.volatility_1y !== undefined &&
    decimalOrNull(p.volatility_1y) &&
    isRatio(p.pe) &&
    isRatio(p.dividend_yield) &&
    Array.isArray(p.actions) &&
    p.actions.every(isAction)
  );
}

export async function getPrices(symbol: string): Promise<Prices> {
  // encodeURIComponent so a symbol can never add path segments ("/", "..") or a query string.
  const body = await apiFetch(`/api/v1/stocks/${encodeURIComponent(symbol)}/prices`);
  if (!isPrices(body)) throw new ApiError(200, 'unexpected_response');
  return body;
}

/** True only when there is a latest close: no close means no price, never a zero. */
export function hasPrices(prices: Prices | null | undefined): prices is Prices {
  return !!prices && prices.latest !== null;
}

const RUPEES = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** "₹2,071.70": Indian digit grouping, always two decimals. */
export function priceLabel(value: string | number): string {
  return `₹${RUPEES.format(Number(value))}`;
}

/** "+0.6%", "−0.6%" (a real minus sign), "0.0%"; null when there is no figure. */
export function signedPercent(value: string | null): string | null {
  if (value === null) return null;
  const n = Number(value);
  const body = Math.abs(n).toFixed(1);
  if (Number(body) === 0) return '0.0%';
  return `${n > 0 ? '+' : '−'}${body}%`;
}

/** "26 Aug 2025: bonus issue or split, factor 0.5; earlier prices adjusted". */
export function actionLine(action: PriceAction): string {
  return `${eventDateLabel(action.date)}: bonus issue or split, factor ${action.factor}; earlier prices adjusted`;
}
