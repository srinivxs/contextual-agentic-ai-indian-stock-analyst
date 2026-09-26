/**
 * A tiny fake of the backend's /api/v1, installed as `fetch`. Nothing here is real data: the stocks
 * are the fictional DemoCo family, never the three real companies.
 */
import { vi } from 'vitest';

import type { Citation, DerivedValue, KeyFact, StockInsights } from '@/lib/insights';

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

export type FakeDocument = {
  id: number;
  symbol: string;
  title: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  size_bytes: number;
  page_count: number | null;
  failure_reason: string | null;
  created_at: string;
  source: string;
  source_url: string | null;
  kind: 'transcript' | 'presentation' | 'annual_report' | 'announcement' | null;
  period: string | null;
};

/** A fictional DemoCo filing; override any field. */
export const demoDocument = (overrides: Partial<FakeDocument> = {}): FakeDocument => ({
  id: 1,
  symbol: 'DEMOA',
  title: 'DEMOA earnings call transcript, Jul 2026',
  status: 'completed',
  size_bytes: 12345,
  page_count: 12,
  failure_reason: null,
  created_at: '2026-09-23T10:00:00+00:00',
  source: 'bse',
  source_url: 'https://www.bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname=0000.pdf',
  kind: 'transcript',
  period: 'Jul 2026',
  ...overrides,
});

export type FakeHit = {
  symbol: string;
  document_id: number;
  title: string;
  kind: 'transcript' | 'presentation' | 'annual_report' | 'announcement' | null;
  period: string | null;
  page: number;
  excerpt: string;
  source_url: string | null;
  score: number;
};

/** A fictional DemoCo search result; override any field. */
export const demoHit = (overrides: Partial<FakeHit> = {}): FakeHit => ({
  symbol: 'DEMOA',
  document_id: 1,
  title: 'DEMOA earnings call transcript, Jul 2026',
  kind: 'transcript',
  period: 'Jul 2026',
  page: 7,
  excerpt: 'Employee attrition at DemoCo fell this quarter.',
  source_url: 'https://www.bseindia.com/xml-data/corpfiling/AttachHis/0000.pdf',
  score: 0.61,
  ...overrides,
});

/** The insights of one stock, exactly the shape the server sends (P11c). */
export type FakeInsights = StockInsights;

const DERIVED_LABELS: [DerivedValue['name'], string][] = [
  ['debt_to_equity', 'Debt to equity'],
  ['revenue_growth', 'Revenue growth'],
  ['profit_growth', 'Profit growth'],
  ['latest_dividend', 'Latest dividend'],
];

/** What the server sends for a stock nothing has been extracted from yet. */
export const emptyInsights = (stock: FakeStock): FakeInsights => ({
  symbol: stock.symbol,
  name: stock.name,
  is_financial: false,
  key_facts: [],
  derived: DERIVED_LABELS.map(([name, label]) => ({
    name,
    label,
    status: 'insufficient_data',
    value: null,
    reason: 'There are no figures to work from yet.',
    citations: [],
  })),
  sentiment: { status: 'insufficient_data', score: null, label: null, events_counted: 0 },
  events: [],
});

/** A fictional DemoCo filing citation; override any field. */
export const demoCitation = (overrides: Partial<Citation> = {}): Citation => ({
  source: 'filing',
  label: 'Annual report · Annual Report 2026 · p.44',
  url: 'https://www.bseindia.com/xml-data/corpfiling/AttachHis/demo-ar-2026.pdf#page=44',
  quote: 'Revenue from operations stood at ₹1,23,456 crore.',
  ...overrides,
});

/** A fictional screener.in citation for DemoCo; override any field. */
export const demoScreenerCitation = (overrides: Partial<Citation> = {}): Citation => ({
  source: 'screener',
  label: 'screener.in · profit-loss · Net Profit · Mar 2026',
  url: 'https://www.screener.in/company/DEMOA/consolidated/',
  quote: null,
  ...overrides,
});

/** A fictional DemoCo fact; override any field. */
export const demoFact = (overrides: Partial<KeyFact> = {}): KeyFact => ({
  metric: 'revenue_from_operations',
  label: 'Revenue from operations',
  period: 'FY2026',
  basis: 'consolidated',
  currency: 'INR',
  unit: 'INR_CRORE',
  value: '123456.0000',
  status: 'single',
  corroborated_by: 0,
  citation: demoCitation(),
  disputed_by: [],
  ...overrides,
});

/** DemoCo Alpha's invented insights: a few facts, all four derived values, sentiment, events. */
export const demoInsights = (overrides: Partial<FakeInsights> = {}): FakeInsights => ({
  symbol: 'DEMOA',
  name: 'DemoCo Alpha Limited',
  is_financial: false,
  key_facts: [
    demoFact(),
    demoFact({
      period: 'FY2025',
      value: '110000.0000',
      citation: demoCitation({ label: 'Annual report · Annual Report 2025 · p.40', quote: null }),
    }),
    demoFact({
      metric: 'net_profit',
      label: 'Net profit',
      value: '12345.5000',
      status: 'agreed',
      corroborated_by: 1,
      citation: demoScreenerCitation(),
    }),
  ],
  derived: [
    {
      name: 'debt_to_equity',
      label: 'Debt to equity',
      status: 'ok',
      value: '0.2500',
      reason: 'FY2026, consolidated: borrowings ₹2,500 crore / equity ₹10,000 crore.',
      citations: [demoCitation({ label: 'Annual report · Annual Report 2026 · p.51' })],
    },
    {
      name: 'revenue_growth',
      label: 'Revenue growth',
      status: 'ok',
      value: '12.5',
      reason: 'FY2026 against FY2025, consolidated.',
      citations: [demoCitation({ label: 'Annual report · Annual Report 2026 · p.45' })],
    },
    {
      name: 'profit_growth',
      label: 'Profit growth',
      status: 'insufficient_data',
      value: null,
      reason: 'Net profit is known for one year only.',
      citations: [],
    },
    {
      name: 'latest_dividend',
      label: 'Latest dividend',
      status: 'ok',
      value: '5.5',
      reason: 'FY2026 dividend per share.',
      citations: [demoCitation({ label: 'Announcement · Dividend · p.1' })],
    },
  ],
  sentiment: { status: 'ok', score: 0.42, label: 'positive', events_counted: 5 },
  events: [
    {
      event_type: 'earnings_results',
      sentiment: 'positive',
      impact: 'medium',
      event_date: '2026-07-01',
      summary: 'DemoCo Alpha reported higher quarterly revenue.',
      citation: demoCitation({ label: 'Announcement · Results · p.2' }),
    },
  ],
  ...overrides,
});

export type FakeCheck = {
  enabled: boolean;
  checking: boolean;
  last_checked_at: string | null;
  next_check_at: string | null;
};

const IDLE_CHECK: FakeCheck = {
  enabled: true,
  checking: false,
  last_checked_at: null,
  next_check_at: null,
};

export type Options = {
  signedIn?: boolean;
  email?: string;
  followed?: string[];
  stocks?: FakeStock[];
  documents?: FakeDocument[];
  /** How many documents one page of the list holds (the real API allows up to 100). */
  pageSize?: number;
  /** Each stock's filing-check status; anything left out is enabled, idle, never checked. */
  checks?: Record<string, Partial<FakeCheck>>;
  /** What every search returns (the fake does not rank anything). */
  searchHits?: FakeHit[];
  /** Search switched off on the server (409). */
  searchOff?: boolean;
  /** Each stock's insights; a known stock left out gets the empty default. */
  insights?: Record<string, FakeInsights>;
};

export type FakeApi = {
  /** Every request made, as "METHOD /path". */
  requests: string[];
  follows: Set<string>;
  /** Make the next matching request fail with this status. */
  failWith: (key: string, status: number) => void;
  /** Keep matching requests waiting until the returned function is called. */
  hold: (key: string) => () => void;
  expireSession: () => void;
  /** Replace the documents the fake serves (to move a status along, for example). */
  setDocuments: (documents: FakeDocument[]) => void;
  /** Change one stock's filing-check status. */
  setCheck: (symbol: string, check: Partial<FakeCheck>) => void;
};

export function installFakeApi(options: Options = {}): FakeApi {
  const state = { signedIn: options.signedIn ?? true };
  const email = options.email ?? 'reader@example.test';
  const stocks = options.stocks ?? STOCKS;
  const follows = new Set(options.followed ?? []);
  const failures = new Map<string, number>();
  const gates = new Map<string, Promise<void>>();
  const requests: string[] = [];
  let documents = options.documents ?? [];
  const checks = new Map<string, FakeCheck>();
  const checkOf = (symbol: string): FakeCheck => ({
    ...IDLE_CHECK,
    ...options.checks?.[symbol],
    ...checks.get(symbol),
  });

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
    const listing = /^GET \/api\/v1\/stocks\/([^/?]+)\/documents(?:\?(.*))?$/.exec(key);
    if (listing) {
      const symbol = decodeURIComponent(listing[1] ?? '');
      if (!stocks.some((s) => s.symbol === symbol)) return envelope(404, 'not_found');
      // Newest first by id, one page at a time, like the real endpoint.
      const query = new URLSearchParams(listing[2] ?? '');
      const cursor = Number(query.get('cursor') ?? Infinity);
      const limit = Math.min(Number(query.get('limit') ?? 20), options.pageSize ?? 100);
      const all = documents
        .filter((d) => d.symbol === symbol && d.id < cursor)
        .sort((a, b) => b.id - a.id);
      const items = all.slice(0, limit);
      const more = all.length > limit;
      return json(200, { items, next_cursor: more ? (items.at(-1)?.id ?? null) : null });
    }
    if (key.startsWith('GET /api/v1/search?')) {
      if (options.searchOff) return envelope(409, 'conflict');
      return json(200, { items: options.searchHits ?? [] });
    }
    const insights = /^GET \/api\/v1\/stocks\/([^/?]+)\/insights$/.exec(key);
    if (insights) {
      const symbol = decodeURIComponent(insights[1] ?? '');
      const stock = stocks.find((s) => s.symbol === symbol);
      if (!stock) return envelope(404, 'not_found');
      return json(200, options.insights?.[symbol] ?? emptyInsights(stock));
    }
    const checking = /^(GET|POST) \/api\/v1\/stocks\/([^/]+)\/filings\/check$/.exec(key);
    if (checking) {
      const symbol = decodeURIComponent(checking[2] ?? '');
      if (!stocks.some((s) => s.symbol === symbol)) return envelope(404, 'not_found');
      const check = checkOf(symbol);
      if (checking[1] === 'GET') return json(200, check);
      // The real rules: switched off, already running, within the hour, or start one now.
      if (!check.enabled) return envelope(409, 'conflict');
      if (check.checking) return json(202, check);
      if (check.next_check_at && Date.parse(check.next_check_at) > Date.now()) {
        return envelope(429, 'rate_limited');
      }
      const next = new Date(Date.now() + 3_600_000).toISOString();
      checks.set(symbol, { ...check, checking: true, next_check_at: next });
      return json(202, checkOf(symbol));
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
    setDocuments: (next) => {
      documents = next;
    },
    setCheck: (symbol, check) => {
      checks.set(symbol, { ...checkOf(symbol), ...check });
    },
  };
}
