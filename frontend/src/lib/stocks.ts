/** The stocks API: the list, and following and unfollowing. */

import { ApiError, apiFetch } from '@/lib/api';

export type Stock = {
  symbol: string;
  name: string;
  bse_code: string;
  sector: string;
  followed: boolean;
};

function isStock(value: unknown): value is Stock {
  const s = value as Partial<Stock> | null;
  return (
    typeof s?.symbol === 'string' &&
    typeof s.name === 'string' &&
    typeof s.bse_code === 'string' &&
    typeof s.sector === 'string' &&
    typeof s.followed === 'boolean'
  );
}

export async function listStocks(): Promise<Stock[]> {
  const body = (await apiFetch('/api/v1/stocks')) as { items?: unknown } | null;
  const items = body?.items;
  if (!Array.isArray(items) || !items.every(isStock)) {
    throw new ApiError(200, 'unexpected_response');
  }
  return items;
}

// encodeURIComponent so a symbol can never add path segments ("/", "..") or a query string.
const followPath = (symbol: string): string =>
  `/api/v1/stocks/${encodeURIComponent(symbol)}/follow`;

/** PUT: "make it so that I follow this". Repeating it changes nothing. */
export async function followStock(symbol: string): Promise<void> {
  await apiFetch(followPath(symbol), { method: 'PUT' });
}

/** DELETE: "make it so that I do not follow this". Repeating it changes nothing. */
export async function unfollowStock(symbol: string): Promise<void> {
  await apiFetch(followPath(symbol), { method: 'DELETE' });
}
