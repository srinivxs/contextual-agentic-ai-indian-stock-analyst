/** The documents API, and the small pure helpers the Documents page is built from. */

import { ApiError, apiFetch } from '@/lib/api';

export type DocumentStatus = 'pending' | 'processing' | 'completed' | 'failed';
export type DocumentKind = 'transcript' | 'presentation' | 'annual_report' | 'announcement';

export type StockDocument = {
  id: number;
  symbol: string;
  title: string;
  status: DocumentStatus;
  size_bytes: number;
  page_count: number | null;
  failure_reason: string | null;
  created_at: string;
  /** Always 'bse' today: every document is an official filing the worker fetched (ADR 018). */
  source: string;
  source_url: string | null;
  /** What the filing is; null only if the server could not tell. */
  kind: DocumentKind | null;
  /** "Jul 2026", "Annual Report 2025", or an announcement's subject. */
  period: string | null;
};

const STATUSES: readonly string[] = ['pending', 'processing', 'completed', 'failed'];
const KINDS: readonly string[] = ['transcript', 'presentation', 'annual_report', 'announcement'];

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
    typeof d.source === 'string' &&
    (d.source_url === null || typeof d.source_url === 'string') &&
    (d.kind === null || (typeof d.kind === 'string' && KINDS.includes(d.kind))) &&
    (d.period === null || typeof d.period === 'string')
  );
}

/** More than enough for three stocks (about 30 documents each), and a stop if the server loops. */
const MAX_PAGES = 20;

/** Every document of a stock, newest first, following the cursor from page to page. */
export async function listAllDocuments(symbol: string): Promise<StockDocument[]> {
  // encodeURIComponent so a symbol can never add path segments ("/", "..") or a query string.
  const base = `/api/v1/stocks/${encodeURIComponent(symbol)}/documents?limit=100`;
  const all: StockDocument[] = [];
  let cursor: number | null = null;
  for (let page = 0; page < MAX_PAGES; page++) {
    const path: string = cursor === null ? base : `${base}&cursor=${cursor}`;
    const body = (await apiFetch(path)) as { items?: unknown; next_cursor?: unknown } | null;
    const items = body?.items;
    if (!Array.isArray(items) || !items.every(isDocument)) {
      throw new ApiError(200, 'unexpected_response');
    }
    all.push(...items);
    cursor = typeof body?.next_cursor === 'number' ? body.next_cursor : null;
    if (cursor === null) break;
  }
  return all;
}

const OFFICIAL_PREFIX = 'https://www.bseindia.com/';

/**
 * An address we are willing to link to: an https BSE one (ADR 018). The server already only
 * records BSE addresses; checking again here means a bad value can never become a `javascript:`
 * or look-alike link in the page.
 */
export function officialUrl(url: string | null): string | null {
  return url !== null && url.startsWith(OFFICIAL_PREFIX) ? url : null;
}

/** Where the official filing lives, for a document fetched from BSE. */
export function officialLink(document: StockDocument): string | null {
  return officialUrl(document.source_url);
}

/** True while the worker still has something to do, which is when the page keeps refreshing. */
export function isStillWorking(documents: StockDocument[]): boolean {
  return documents.some((d) => d.status === 'pending' || d.status === 'processing');
}

/** What a row is called: the filing's period, or its full title when it has none. */
export function documentLabel(document: StockDocument): string {
  return document.kind !== null && document.period ? document.period : document.title;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** A number that sorts periods: "Jul 2026" -> 202607, "Annual Report 2025" -> 202500. */
function periodOrder(period: string | null): number {
  const monthYear = /^([A-Z][a-z]{2}) (\d{4})$/.exec(period ?? '');
  if (monthYear) return Number(monthYear[2]) * 100 + MONTHS.indexOf(monthYear[1] ?? '') + 1;
  const year = /(\d{4})/.exec(period ?? '');
  return year ? Number(year[1]) * 100 : 0;
}

export type DocumentGroup = { key: string; title: string; documents: StockDocument[] };

const GROUPS: { kind: DocumentKind | null; title: string; byPeriod: boolean }[] = [
  { kind: 'transcript', title: 'Earnings calls', byPeriod: true },
  { kind: 'presentation', title: 'Investor presentations', byPeriod: true },
  { kind: 'annual_report', title: 'Annual reports', byPeriod: true },
  { kind: 'announcement', title: 'Announcements', byPeriod: false },
  { kind: null, title: 'Other documents', byPeriod: false },
];

/**
 * The page's sections, in a fixed order, each newest first. Filings with a period are sorted by
 * it; announcements and anything else keep the server's order, which is newest first already.
 */
export function groupDocuments(documents: StockDocument[]): DocumentGroup[] {
  return GROUPS.map(({ kind, title, byPeriod }) => {
    const members = documents.filter((d) => d.kind === kind);
    if (byPeriod) members.sort((a, b) => periodOrder(b.period) - periodOrder(a.period));
    return { key: kind ?? 'other', title, documents: members };
  }).filter((group) => group.documents.length > 0);
}

const NUMBER = new Intl.NumberFormat('en-US');

/** "1 page", "23 pages", "1,503 pages"; nothing when the count is not known yet. */
export function pagesLabel(pages: number | null): string {
  if (pages === null) return '';
  return `${NUMBER.format(pages)} ${pages === 1 ? 'page' : 'pages'}`;
}

/** "4 documents · 60 pages". */
export function summaryLabel(documents: StockDocument[]): string {
  const pages = documents.reduce((sum, d) => sum + (d.page_count ?? 0), 0);
  const count = `${documents.length} ${documents.length === 1 ? 'document' : 'documents'}`;
  return `${count} · ${pagesLabel(pages)}`;
}
