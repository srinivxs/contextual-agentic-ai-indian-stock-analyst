/**
 * A tiny fake of the backend's /api/v1, installed as `fetch`. Nothing here is real data: the stocks
 * are the fictional DemoCo family, never the three real companies.
 */
import { vi } from 'vitest';

export type FakeStock = { symbol: string; name: string; bse_code: string; sector: string };

export const STOCKS: FakeStock[] = [
  { symbol: 'DEMOA', name: 'DemoCo Alpha Limited', bse_code: '000001', sector: 'Demo Energy' },
  { symbol: 'DEMOB', name: 'DemoCo Beta Limited', bse_code: '000002', sector: 'Demo Software' },
  { symbol: 'DEMOC', name: 'DemoCo Gamma Limited', bse_code: '000003', sector: 'Demo Banking' },
];

const json = (status: number, body?: unknown): Response =>
  body === undefined
    ? new Response(null, { status })
    : new Response(JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json' },
      });

const envelope = (status: number, code: string): Response =>
  json(status, { error: { code, message: 'generic message', request_id: 'req-1' } });

export type Options = { signedIn?: boolean; email?: string; followed?: string[]; stocks?: FakeStock[] };

export type FakeApi = {
  /** Every request made, as "METHOD /path". */
  requests: string[];
  follows: Set<string>;
  /** Make the next matching request fail with this status. */
  failWith: (key: string, status: number) => void;
  /** Keep matching requests waiting until the returned function is called. */
  hold: (key: string) => () => void;
  expireSession: () => void;
};

export function installFakeApi(options: Options = {}): FakeApi {
  const state = { signedIn: options.signedIn ?? true };
  const email = options.email ?? 'reader@example.test';
  const stocks = options.stocks ?? STOCKS;
  const follows = new Set(options.followed ?? []);
  const failures = new Map<string, number>();
  const gates = new Map<string, Promise<void>>();
  const requests: string[] = [];

  const handler = async (input: string, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? 'GET';
    const key = `${method} ${input}`;
    requests.push(key);
    const gate = gates.get(key);
    if (gate) await gate;
    const forced = failures.get(key);
    if (forced !== undefined) {
      failures.delete(key);
      return envelope(forced, 'internal_error');
    }
    if (!state.signedIn) return envelope(401, 'unauthorized');

    if (key === 'GET /api/v1/me') return json(200, { id: 'user-1', email });
    if (key === 'POST /api/v1/auth/logout') {
      state.signedIn = false;
      return json(204);
    }
    if (key === 'GET /api/v1/stocks') {
      return json(200, { items: stocks.map((s) => ({ ...s, followed: follows.has(s.symbol) })) });
    }
    const match = /^(PUT|DELETE) \/api\/v1\/stocks\/([^/]+)\/follow$/.exec(key);
    if (match) {
      const symbol = decodeURIComponent(match[2] ?? '');
      if (!stocks.some((s) => s.symbol === symbol)) return envelope(404, 'not_found');
      if (match[1] === 'PUT') follows.add(symbol);
      else follows.delete(symbol);
      return json(204);
    }
    return envelope(404, 'not_found');
  };

  vi.stubGlobal('fetch', vi.fn(handler));
  return {
    requests,
    follows,
    failWith: (key, status) => void failures.set(key, status),
    hold: (key) => {
      let release = (): void => {};
      gates.set(key, new Promise<void>((resolve) => (release = resolve)));
      return release;
    },
    expireSession: () => {
      state.signedIn = false;
    },
  };
}
