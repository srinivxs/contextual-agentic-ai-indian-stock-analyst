/**
 * Searching the filings by meaning (P10b): the one call, and the small helpers its results need.
 *
 * The server finds the passages closest in meaning to the question and returns short excerpts
 * with their filing, page and official BSE address. Nothing here ranks or judges anything.
 */

import { ApiError, apiFetch } from '@/lib/api';
import { officialUrl, type DocumentKind } from '@/lib/documents';

export type SearchHit = {
  symbol: string;
  document_id: number;
  title: string;
  kind: DocumentKind | null;
  period: string | null;
  page: number;
  excerpt: string;
  source_url: string | null;
  score: number;
};

export type SearchOutcome =
  | { status: 'ok'; hits: SearchHit[] }
  | { status: 'off' } // EMBEDDINGS_ENABLED is off on the server
  | { status: 'unavailable' }; // Bedrock throttled or the pass expired: try again later

const text = (value: unknown): boolean => typeof value === 'string';
const orNull = (value: unknown, test: (v: unknown) => boolean): boolean =>
  value === null || test(value);

function isHit(value: unknown): value is SearchHit {
  const h = value as Partial<SearchHit> | null;
  return (
    text(h?.symbol) &&
    typeof h?.document_id === 'number' &&
    text(h.title) &&
    orNull(h.kind, text) &&
    orNull(h.period, text) &&
    typeof h.page === 'number' &&
    text(h.excerpt) &&
    orNull(h.source_url, text) &&
    typeof h.score === 'number'
  );
}

export async function searchFilings(question: string, symbol: string): Promise<SearchOutcome> {
  // encodeURIComponent so neither value can add a parameter, a fragment or a path segment.
  const path =
    `/api/v1/search?q=${encodeURIComponent(question)}` + `&symbol=${encodeURIComponent(symbol)}`;
  let body: unknown;
  try {
    body = await apiFetch(path);
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) return { status: 'off' };
    if (error instanceof ApiError && error.status === 503) return { status: 'unavailable' };
    throw error;
  }
  const items = (body as { items?: unknown } | null)?.items;
  if (!Array.isArray(items) || !items.every(isHit)) {
    throw new ApiError(200, 'unexpected_response');
  }
  return { status: 'ok', hits: items };
}

/** The official filing, opened at the cited page (PDF viewers honour #page=N). */
export function pageLink(hit: SearchHit): string | null {
  const url = officialUrl(hit.source_url);
  return url === null ? null : `${url}#page=${hit.page}`;
}

const KIND_NAMES: Record<DocumentKind, string> = {
  transcript: 'Earnings call',
  presentation: 'Investor presentation',
  annual_report: 'Annual report',
  announcement: 'Announcement',
};

/** "Earnings call · Jul 2026", or the title when the filing has no kind or period. */
export function hitLabel(hit: SearchHit): string {
  return hit.kind !== null && hit.period ? `${KIND_NAMES[hit.kind]} · ${hit.period}` : hit.title;
}
