/**
 * A stock's insights (P11c): its key facts, the values derived from them, the recent sentiment and
 * events. The server extracts, checks and computes all of it; this file only reads it, checks its
 * shape, and turns values into words for the page. Nothing here calculates a financial figure.
 */

import { ApiError, apiFetch } from '@/lib/api';
import { officialUrl, screenerUrl } from '@/lib/documents';

const METRICS = [
  'revenue_from_operations',
  'net_interest_income',
  'net_profit',
  'total_borrowings',
  'total_equity',
  'dividend_per_share',
  'eps_basic',
  'return_on_equity',
  'net_interest_margin',
  'gross_npa_ratio',
] as const;
const BASES = ['consolidated', 'standalone', 'unspecified'] as const;
const CURRENCIES = ['INR', 'USD'] as const;
const UNITS = ['INR_CRORE', 'USD_MILLION', 'INR_PER_SHARE', 'USD_PER_SHARE', 'PERCENT'] as const;
const FACT_STATUSES = ['single', 'agreed', 'disputed'] as const;
const SOURCES = ['filing', 'screener'] as const;
const DERIVED_NAMES = [
  'debt_to_equity',
  'revenue_growth',
  'profit_growth',
  'latest_dividend',
] as const;
const DERIVED_STATUSES = ['ok', 'not_applicable', 'not_assessable', 'insufficient_data'] as const;
const SENTIMENT_STATUSES = ['ok', 'insufficient_data'] as const;
const SENTIMENT_LABELS = ['negative', 'mixed', 'positive'] as const;

export type Metric = (typeof METRICS)[number];
export type Basis = (typeof BASES)[number];
export type Unit = (typeof UNITS)[number];
export type DerivedStatus = (typeof DERIVED_STATUSES)[number];

/** Where a figure came from: a page of a filing (with its words) or a screener.in table. */
export type Citation = {
  source: (typeof SOURCES)[number];
  label: string;
  url: string | null;
  /** The filing's own words; null for screener.in. */
  quote: string | null;
};

export type KeyFact = {
  metric: Metric;
  label: string;
  /** "FY2026" or "Q3FY2026". */
  period: string;
  basis: Basis;
  currency: (typeof CURRENCIES)[number] | null;
  unit: Unit;
  /** A decimal as text ("12345.0000"), so no digit is lost on the way. */
  value: string;
  status: (typeof FACT_STATUSES)[number];
  /** How many other sources agree within 1%. */
  corroborated_by: number;
  citation: Citation;
  /** The sources that disagree; empty unless the status is "disputed". */
  disputed_by: Citation[];
};

export type DerivedValue = {
  name: (typeof DERIVED_NAMES)[number];
  label: string;
  status: DerivedStatus;
  value: string | null;
  /** A plain sentence saying how the value was worked out, or why it could not be. */
  reason: string;
  citations: Citation[];
};

export type Sentiment = {
  status: (typeof SENTIMENT_STATUSES)[number];
  score: number | null;
  label: (typeof SENTIMENT_LABELS)[number] | null;
  events_counted: number;
};

export type StockEvent = {
  event_type: string;
  sentiment: string;
  impact: string;
  /** "2026-07-01". */
  event_date: string;
  summary: string;
  citation: Citation;
};

export type StockInsights = {
  symbol: string;
  name: string;
  is_financial: boolean;
  key_facts: KeyFact[];
  derived: DerivedValue[];
  sentiment: Sentiment;
  events: StockEvent[];
};

// Shape checks. Each one starts from `value ?? {}`, so null or a missing object simply fails.

const DECIMAL = /^-?\d+(\.\d+)?$/;
const PERIOD = /^(Q[1-4])?FY\d{4}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

const text = (value: unknown): value is string => typeof value === 'string';
const orNull = (value: unknown, test: (v: unknown) => boolean): boolean =>
  value === null || test(value);
const oneOf =
  (allowed: readonly string[]) =>
  (value: unknown): boolean =>
    text(value) && allowed.includes(value);
const decimal = (value: unknown): boolean => text(value) && DECIMAL.test(value);
const listOf = (value: unknown, test: (v: unknown) => boolean): boolean =>
  Array.isArray(value) && value.every(test);

function isCitation(value: unknown): value is Citation {
  const c = (value ?? {}) as Partial<Citation>;
  return oneOf(SOURCES)(c.source) && text(c.label) && orNull(c.url, text) && orNull(c.quote, text);
}

function isKeyFact(value: unknown): value is KeyFact {
  const f = (value ?? {}) as Partial<KeyFact>;
  return (
    oneOf(METRICS)(f.metric) &&
    text(f.label) &&
    text(f.period) &&
    PERIOD.test(f.period) &&
    oneOf(BASES)(f.basis) &&
    orNull(f.currency, oneOf(CURRENCIES)) &&
    oneOf(UNITS)(f.unit) &&
    decimal(f.value) &&
    oneOf(FACT_STATUSES)(f.status) &&
    typeof f.corroborated_by === 'number' &&
    isCitation(f.citation) &&
    listOf(f.disputed_by, isCitation)
  );
}

function isDerivedValue(value: unknown): value is DerivedValue {
  const d = (value ?? {}) as Partial<DerivedValue>;
  return (
    oneOf(DERIVED_NAMES)(d.name) &&
    text(d.label) &&
    oneOf(DERIVED_STATUSES)(d.status) &&
    orNull(d.value, decimal) &&
    text(d.reason) &&
    listOf(d.citations, isCitation)
  );
}

function isSentiment(value: unknown): value is Sentiment {
  const s = (value ?? {}) as Partial<Sentiment>;
  return (
    oneOf(SENTIMENT_STATUSES)(s.status) &&
    orNull(s.score, (v) => typeof v === 'number') &&
    orNull(s.label, oneOf(SENTIMENT_LABELS)) &&
    typeof s.events_counted === 'number'
  );
}

function isStockEvent(value: unknown): value is StockEvent {
  const e = (value ?? {}) as Partial<StockEvent>;
  return (
    text(e.event_type) &&
    text(e.sentiment) &&
    text(e.impact) &&
    text(e.event_date) &&
    DATE.test(e.event_date) &&
    text(e.summary) &&
    isCitation(e.citation)
  );
}

function isInsights(value: unknown): value is StockInsights {
  const i = (value ?? {}) as Partial<StockInsights>;
  return (
    text(i.symbol) &&
    text(i.name) &&
    typeof i.is_financial === 'boolean' &&
    listOf(i.key_facts, isKeyFact) &&
    listOf(i.derived, isDerivedValue) &&
    isSentiment(i.sentiment) &&
    listOf(i.events, isStockEvent)
  );
}

export async function getInsights(symbol: string): Promise<StockInsights> {
  // encodeURIComponent so a symbol can never add path segments ("/", "..") or a query string.
  const body = await apiFetch(`/api/v1/stocks/${encodeURIComponent(symbol)}/insights`);
  if (!isInsights(body)) throw new ApiError(200, 'unexpected_response');
  return body;
}

// Turning values into words.

// Rupees use Indian digit grouping (1,23,456); dollars the international one (123,456).
const INDIAN = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });
const INTERNATIONAL = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
const INDIAN_PAISE = new Intl.NumberFormat('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const INTERNATIONAL_CENTS = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * "₹1,23,456 crore", "US$1,800 million", "₹5.50 per share", "14.7%". Dollar amounts are shown as
 * the filing reported them, labelled US$, never converted to rupees.
 */
export function formatAmount(value: string, unit: Unit): string {
  const number = Number(value);
  const sign = number < 0 ? '-' : '';
  const size = Math.abs(number);
  switch (unit) {
    case 'INR_CRORE':
      return `${sign}₹${INDIAN.format(size)} crore`;
    case 'USD_MILLION':
      return `${sign}US$${INTERNATIONAL.format(size)} million`;
    case 'INR_PER_SHARE':
      return `${sign}₹${INDIAN_PAISE.format(size)} per share`;
    case 'USD_PER_SHARE':
      return `${sign}US$${INTERNATIONAL_CENTS.format(size)} per share`;
    case 'PERCENT':
      return `${sign}${INDIAN.format(size)}%`;
  }
}

/** "FY2026" stays as it is; "Q3FY2026" becomes "Q3 FY2026". */
export function periodLabel(period: string): string {
  const quarter = /^(Q[1-4])(FY\d{4})$/.exec(period);
  return quarter ? `${quarter[1]} ${quarter[2]}` : period;
}

/** A number that sorts periods: "Q3FY2026" -> 20263, and the full year after its Q4: 20265. */
function periodOrder(period: string): number {
  const parts = /^(?:Q([1-4]))?FY(\d{4})$/.exec(period);
  if (!parts) return 0;
  return Number(parts[2]) * 10 + (parts[1] ? Number(parts[1]) : 5);
}

/** Nothing for consolidated (the usual case); otherwise a short note beside the value. */
export function basisNote(basis: Basis): string {
  if (basis === 'standalone') return '(standalone)';
  if (basis === 'unspecified') return '(basis not stated)';
  return '';
}

/**
 * The address a citation may link to, or null. A filing may only link to BSE and a screener
 * figure only to a screener.in company page, so a bad value can never become a `javascript:` or
 * look-alike link in the page.
 */
export function citationLink(citation: Citation): string | null {
  return citation.source === 'filing' ? officialUrl(citation.url) : screenerUrl(citation.url);
}

export type FactRow = { metric: Metric; label: string; cells: (KeyFact | null)[] };
export type FactTable = { periods: string[]; rows: FactRow[] };

/**
 * The key-facts table: one column per period (newest first), one row per metric (in the server's
 * order), and in each cell that metric's fact for that period, or null when there is none.
 */
export function factTable(facts: KeyFact[]): FactTable {
  const periods = [...new Set(facts.map((f) => f.period))].sort(
    (a, b) => periodOrder(b) - periodOrder(a),
  );
  const metrics = [...new Set(facts.map((f) => f.metric))];
  const rows = metrics.map((metric) => {
    const own = facts.filter((f) => f.metric === metric);
    return {
      metric,
      label: own[0]?.label ?? metric,
      cells: periods.map((period) => own.find((f) => f.period === period) ?? null),
    };
  });
  return { periods, rows };
}

const STATUS_WORDS: Record<Exclude<DerivedStatus, 'ok'>, string> = {
  not_applicable: 'Not applicable',
  not_assessable: 'Not assessable',
  insufficient_data: 'Not enough data',
};

/** "0.25", "+12.5%", "₹5.50 per share", or why there is no value ("Not applicable"). */
export function derivedValueLabel(derived: DerivedValue): string {
  if (derived.status !== 'ok') return STATUS_WORDS[derived.status];
  if (derived.value === null) return STATUS_WORDS.insufficient_data;
  const number = Number(derived.value);
  switch (derived.name) {
    case 'debt_to_equity':
      return number.toFixed(2);
    case 'revenue_growth':
    case 'profit_growth':
      return `${number > 0 ? '+' : ''}${number.toFixed(1)}%`;
    case 'latest_dividend':
      return formatAmount(derived.value, 'INR_PER_SHARE');
  }
}

const capitalise = (words: string): string => words.charAt(0).toUpperCase() + words.slice(1);

/** "Positive", "Mixed", "Negative", or "Not enough events". */
export function sentimentLabel(sentiment: Sentiment): string {
  return sentiment.status === 'ok' && sentiment.label !== null
    ? capitalise(sentiment.label)
    : 'Not enough events';
}

/** "+0.42", "-0.50", "0.00". */
export function scoreLabel(score: number): string {
  return `${score > 0 ? '+' : ''}${score.toFixed(2)}`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * "2026-07-01" -> "01 Jul 2026". Read straight from the text, not through Date, so the viewer's
 * time zone can never move it to the day before.
 */
export function eventDateLabel(date: string): string {
  const [year, month, day] = date.split('-');
  const name = MONTHS[Number(month) - 1];
  return name ? `${day} ${name} ${year}` : date;
}

/** "earnings_results" -> "Earnings results". */
export function eventTypeLabel(eventType: string): string {
  return capitalise(eventType.split('_').join(' '));
}

const TICKER = /^[A-Z0-9&-]{1,20}$/;

/** The same rule the server applies to a ticker in a path. */
export function isTickerSymbol(symbol: string): boolean {
  return TICKER.test(symbol);
}

/** The one static stock page, told which stock by ?symbol= (ADR 006: no dynamic routes). */
export function stockPageHref(symbol: string): string {
  return `/stock/?symbol=${encodeURIComponent(symbol)}`;
}
