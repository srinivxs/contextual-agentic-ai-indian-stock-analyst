/** The RBI press-release feed (P15): one call, and a shape check on what comes back. */

import { ApiError, apiFetch } from '@/lib/api';

export type FeedItem = {
  title: string;
  /** ISO time; null when the feed gave none. */
  published_at: string | null;
  /** About 300 characters. */
  summary: string;
  /** The release on rbi.org.in; null for a fixture. Still checked before it is linked. */
  url: string | null;
  /** True for the offline sample items. */
  is_fixture: boolean;
};

export type Feed = { items: FeedItem[]; attribution: string };

const text = (value: unknown): value is string => typeof value === 'string';
const orNull = (value: unknown): boolean => value === null || text(value);

function isItem(value: unknown): value is FeedItem {
  const i = (value ?? {}) as Partial<FeedItem>;
  return (
    text(i.title) &&
    orNull(i.published_at) &&
    text(i.summary) &&
    orNull(i.url) &&
    typeof i.is_fixture === 'boolean'
  );
}

/** The newest press releases (the server sends at most 10), with the line crediting RBI. */
export async function getFeed(): Promise<Feed> {
  const body = (await apiFetch('/api/v1/feed')) as Partial<Feed> | null;
  if (!Array.isArray(body?.items) || !body.items.every(isItem) || !text(body.attribution)) {
    throw new ApiError(200, 'unexpected_response');
  }
  return { items: body.items, attribution: body.attribution };
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * "2026-09-12T10:00:00Z" -> "12 Sep 2026", or null. Read from the text's date part, not through
 * Date, so the viewer's time zone cannot move it.
 */
export function feedDateLabel(publishedAt: string | null): string | null {
  const parts = /^(\d{4})-(\d{2})-(\d{2})/.exec(publishedAt ?? '');
  const month = MONTHS[Number(parts?.[2]) - 1];
  return parts && month ? `${parts[3]} ${month} ${parts[1]}` : null;
}
