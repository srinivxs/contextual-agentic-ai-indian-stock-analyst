/**
 * How the stocks fit the user's remembered profile (P14). The server judges every stock with
 * fixed rules over stored, cited figures (no LLM, no share prices); this file only asks it,
 * checks the shape of the reply, and names the statuses and outcomes in plain words.
 */

import { ApiError, apiFetch } from '@/lib/api';
import { isCitation, type Citation } from '@/lib/insights';

const STATUSES = ['match', 'partial', 'no_match', 'not_enough_data'] as const;
const OUTCOMES = ['pass', 'miss', 'fail', 'not_assessable', 'no_data'] as const;

export type MatchStatus = (typeof STATUSES)[number];
export type MatchOutcome = (typeof OUTCOMES)[number];

/** One criterion the profile asks for, judged on one stored figure. */
export type MatchReason = {
  criterion: string;
  preference: string;
  /** A must-have: missing it makes the stock "No match". */
  hard: boolean;
  outcome: MatchOutcome;
  text: string;
  citations: Citation[];
};

export type StockMatch = {
  symbol: string;
  name: string;
  status: MatchStatus;
  reasons: MatchReason[];
  /** Shown beside the status; they never change it. */
  cautions: MatchReason[];
};

export type MatchResult = {
  profile_empty: boolean;
  stocks: StockMatch[];
  disclaimer: string;
};

export const STATUS_LABELS: Record<MatchStatus, string> = {
  match: 'Match',
  partial: 'Partial match',
  no_match: 'No match',
  not_enough_data: 'Not enough data',
};

export const OUTCOME_LABELS: Record<MatchOutcome, string> = {
  pass: 'Meets',
  miss: 'Falls short',
  fail: 'Fails a must-have',
  not_assessable: "Can't be judged",
  no_data: 'No data',
};

const text = (value: unknown): value is string => typeof value === 'string';
const oneOf =
  (allowed: readonly string[]) =>
  (value: unknown): boolean =>
    text(value) && allowed.includes(value);
const listOf = (value: unknown, test: (v: unknown) => boolean): boolean =>
  Array.isArray(value) && value.every(test);

function isReason(value: unknown): value is MatchReason {
  const r = (value ?? {}) as Partial<MatchReason>;
  return (
    text(r.criterion) &&
    text(r.preference) &&
    typeof r.hard === 'boolean' &&
    oneOf(OUTCOMES)(r.outcome) &&
    text(r.text) &&
    listOf(r.citations, isCitation)
  );
}

function isStockMatch(value: unknown): value is StockMatch {
  const s = (value ?? {}) as Partial<StockMatch>;
  return (
    text(s.symbol) &&
    text(s.name) &&
    oneOf(STATUSES)(s.status) &&
    listOf(s.reasons, isReason) &&
    listOf(s.cautions, isReason)
  );
}

function isMatchResult(value: unknown): value is MatchResult {
  const m = (value ?? {}) as Partial<MatchResult>;
  return (
    typeof m.profile_empty === 'boolean' && listOf(m.stocks, isStockMatch) && text(m.disclaimer)
  );
}

/** Each stock's status and reasons for the signed-in user's profile. */
export async function getMatches(): Promise<MatchResult> {
  const body = await apiFetch('/api/v1/match');
  if (!isMatchResult(body)) throw new ApiError(200, 'unexpected_response');
  return body;
}
