/**
 * What the chat page's side panel needs, all plain functions: which stock a message is about, the
 * follow-up questions to offer, which closes a range tab shows, and which stored figures make the
 * key-metrics card. Nothing here invents or calculates a financial figure: the growth shown is the
 * server's own derived value.
 */

import {
  derivedValueLabel,
  formatAmount,
  type DerivedValue,
  type KeyFact,
  type Metric,
  type StockEvent,
  type StockInsights,
} from '@/lib/insights';
import type { PricePoint } from '@/lib/prices';

export type ChatStock = { symbol: string; short: string; pattern: RegExp };

/** The three stocks, with the ways a person may name them. */
export const CHAT_STOCKS: readonly ChatStock[] = [
  { symbol: 'TCS', short: 'TCS', pattern: /\b(?:tcs|tata consultancy(?: services)?)\b/i },
  { symbol: 'HDFCBANK', short: 'HDFC Bank', pattern: /\b(?:hdfc ?bank|hdfc)\b/i },
  { symbol: 'RELIANCE', short: 'Reliance', pattern: /\b(?:reliance industries|reliance|ril)\b/i },
];

/** The symbols named in a text, in the order first mentioned, each once. */
export function stocksIn(text: string): string[] {
  return CHAT_STOCKS.flatMap((stock) => {
    const at = text.search(stock.pattern);
    return at === -1 ? [] : [{ symbol: stock.symbol, at }];
  })
    .sort((a, b) => a.at - b.at)
    .map((found) => found.symbol);
}

export function shortName(symbol: string): string {
  return CHAT_STOCKS.find((stock) => stock.symbol === symbol)?.short ?? symbol;
}

/** Four questions about one stock. They fill the box; the reader decides whether to send. */
export function followUps(symbol: string): string[] {
  const name = shortName(symbol);
  const others = CHAT_STOCKS.filter((stock) => stock.symbol !== symbol).map((s) => s.short);
  return [
    `Show revenue and net profit for ${name}`,
    `Compare ${name} with ${others.join(' and ')}`,
    `Latest news on ${name}`,
    `How does ${name} fit my profile?`,
  ];
}

export const RANGES = [
  { key: '1m', label: '1M', months: 1 },
  { key: '3m', label: '3M', months: 3 },
  { key: '6m', label: '6M', months: 6 },
  { key: '1y', label: '1Y', months: 12 },
] as const;
export type RangeKey = (typeof RANGES)[number]['key'];

/** The closes of the last month(s) before the newest close (dates compared as text, no time zone). */
/** The first date a range shows, counted back from the newest close ("YYYY-MM-DD"). */
function rangeStart(newest: string, months: number): string {
  const [year = 0, month = 1, day = 1] = newest.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1 - months, day)).toISOString().slice(0, 10);
}

export function rangeHistory(history: PricePoint[], range: RangeKey): PricePoint[] {
  const newest = history.at(-1);
  if (!newest) return [];
  const months = RANGES.find((r) => r.key === range)?.months ?? 12;
  const from = rangeStart(newest.date, months);
  return history.filter((point) => point.date >= from);
}

/**
 * Which ranges show more than the shorter one before them. The shortest always does; a longer
 * range only once the stored history reaches back past the shorter range's start. BSE serves
 * only about a month of daily files, so the history starts short and grows a day at a time;
 * until then 3M, 6M and 1Y would repeat the 1M chart, so they stay off.
 */
export function availableRanges(history: PricePoint[]): Record<RangeKey, boolean> {
  const oldest = history[0]?.date;
  const newest = history.at(-1)?.date;
  const result = { '1m': true, '3m': false, '6m': false, '1y': false } as Record<RangeKey, boolean>;
  if (oldest === undefined || newest === undefined) return result;
  RANGES.forEach((range, index) => {
    const shorter = RANGES[index - 1];
    if (shorter) result[range.key] = oldest < rangeStart(newest, shorter.months);
  });
  return result;
}

/** True while the history does not reach back a full year yet. */
export function historyStillFilling(history: PricePoint[]): boolean {
  const oldest = history[0]?.date;
  const newest = history.at(-1)?.date;
  return oldest !== undefined && newest !== undefined && oldest > rangeStart(newest, 12);
}

/** The newest three events, newest first. */
export function newestEvents(events: StockEvent[]): StockEvent[] {
  return [...events].sort((a, b) => b.event_date.localeCompare(a.event_date)).slice(0, 3);
}

export type MetricRow = {
  metric: Metric;
  label: string;
  /** The full year the figure is for, e.g. "FY2026". */
  period: string;
  value: string;
  /** The server's year-on-year growth for this figure, or null when there is none. */
  change: string | null;
  tone: 'rise' | 'fall' | 'flat';
  fact: KeyFact;
};

const FULL_YEAR = /^FY(\d{4})$/;

/** The newest full-year fact of a metric; a consolidated one wins a tie. */
function latestYear(facts: KeyFact[], metric: Metric): KeyFact | null {
  const own = facts.filter((f) => f.metric === metric && FULL_YEAR.test(f.period));
  const year = (f: KeyFact) => Number(FULL_YEAR.exec(f.period)?.[1]);
  const ranked = own.sort(
    (a, b) =>
      year(b) - year(a) || Number(b.basis === 'consolidated') - Number(a.basis === 'consolidated'),
  );
  return ranked[0] ?? null;
}

function growthOf(derived: DerivedValue[], name: DerivedValue['name']) {
  const found = derived.find((d) => d.name === name);
  if (!found || found.status !== 'ok' || found.value === null) {
    return { change: null, tone: 'flat' as const };
  }
  const n = Number(found.value);
  return {
    change: derivedValueLabel(found),
    tone: n > 0 ? ('rise' as const) : n < 0 ? ('fall' as const) : ('flat' as const),
  };
}

/**
 * Latest full-year revenue (a bank: net interest income), net profit, basic EPS and return on
 * equity from the stored facts. A figure the store lacks is left out, never filled in. `period`
 * is the newest year among them, for the card's title.
 */
export function keyMetrics(insights: StockInsights): { period: string | null; rows: MetricRow[] } {
  const top: Metric = insights.is_financial ? 'net_interest_income' : 'revenue_from_operations';
  const wanted: [Metric, DerivedValue['name'] | null][] = [
    [top, 'revenue_growth'],
    ['net_profit', 'profit_growth'],
    ['eps_basic', null],
    ['return_on_equity', null],
  ];
  const rows: MetricRow[] = [];
  for (const [metric, growth] of wanted) {
    const fact = latestYear(insights.key_facts, metric);
    if (!fact) continue;
    rows.push({
      metric,
      label: fact.label,
      period: fact.period,
      value: formatAmount(fact.value, fact.unit),
      ...(growth ? growthOf(insights.derived, growth) : { change: null, tone: 'flat' as const }),
      fact,
    });
  }
  const years = rows.map((row) => row.period).sort();
  return { period: years.at(-1) ?? null, rows };
}
