/** The documents API: a stock's documents, and what the page may do with them. */

import { ApiError, apiFetch } from '@/lib/api';

export type DocumentStatus = 'pending' | 'processing' | 'completed' | 'failed';

export type StockDocument = {
  id: number;
  symbol: string;
  title: string;
  status: DocumentStatus;
  size_bytes: number;
  page_count: number | null;
  failure_reason: string | null;
  created_at: string;
  source: 'upload' | 'bse';
  source_url: string | null;
};

const STATUSES: readonly string[] = ['pending', 'processing', 'completed', 'failed'];

function isDocument(value: unknown): value is StockDocument {
  const d = value as Partial<StockDocument> | null;
  return (
    typeof d?.id === 'number' &&
    typeof d.symbol === 'string' &&
    typeof d.title === 'string' &&
    typeof d.status === 'string' &&
    STATUSES.includes(d.status) &&
    typeof d.size_bytes === 'number' &&
    (d.page_count === null || typeof d.page_count === 'number') &&
    (d.failure_reason === null || typeof d.failure_reason === 'string') &&
    typeof d.created_at === 'string' &&
    (d.source === 'upload' || d.source === 'bse') &&
    (d.source_url === null || typeof d.source_url === 'string')
  );
}

/** The first page of a stock's documents, newest first. Three stocks and ~15 documents fit on it. */
export async function listDocuments(symbol: string): Promise<StockDocument[]> {
  // encodeURIComponent so a symbol can never add path segments ("/", "..") or a query string.
  const body = (await apiFetch(`/api/v1/stocks/${encodeURIComponent(symbol)}/documents`)) as {
    items?: unknown;
  } | null;
  const items = body?.items;
  if (!Array.isArray(items) || !items.every(isDocument)) {
    throw new ApiError(200, 'unexpected_response');
  }
  return items;
}

const OFFICIAL_PREFIX = 'https://www.bseindia.com/';

/**
 * Where the official filing lives, for a document fetched from BSE (ADR 018). The server already
 * only records BSE addresses; checking again here means a bad value can never become a
 * `javascript:` or look-alike link in the page.
 */
export function officialLink(document: StockDocument): string | null {
  const url = document.source_url;
  return url !== null && url.startsWith(OFFICIAL_PREFIX) ? url : null;
}

/** True while the worker still has something to do, which is when the page keeps refreshing. */
export function isStillWorking(documents: StockDocument[]): boolean {
  return documents.some((d) => d.status === 'pending' || d.status === 'processing');
}
