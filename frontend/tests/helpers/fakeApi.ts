/**
 * A tiny fake of the backend's /api/v1, installed as `fetch`. Nothing here is real data: the stocks
 * are the fictional DemoCo family, never the three real companies.
 */
import { vi } from 'vitest';

import type { ChatMessage, ChatSource, ChatTable, Conversation } from '@/lib/chat';
import type { DataStatus, RefreshResult } from '@/lib/dataStatus';
import type { Citation, DerivedValue, KeyFact, StockInsights } from '@/lib/insights';
import type { FeedItem } from '@/lib/feed';
import type { MatchReason, MatchResult, StockMatch } from '@/lib/match';
import type { Profile, ProfileChoice, ProfileFieldEntry } from '@/lib/profile';
import type { PriceRatio, Prices } from '@/lib/prices';
import type { Series, SeriesMetric, SeriesPoint } from '@/lib/series';

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

/** A fictional RBI press-release citation; override any field. */
export const demoRbiCitation = (overrides: Partial<Citation> = {}): Citation => ({
  source: 'rbi',
  label: 'RBI press release · 12 Sep 2026',
  url: 'https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=00001',
  quote: 'The policy rate is unchanged in this invented sample.',
  ...overrides,
});

/** A fictional RBI feed item (synthetic text only); override any field. */
export const demoFeedItem = (overrides: Partial<FeedItem> = {}): FeedItem => ({
  title: 'Sample policy statement on the demo repo rate',
  published_at: '2026-09-12T10:00:00Z',
  summary: 'An invented summary of a press release, for tests only.',
  url: 'https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=00001',
  is_fixture: false,
  ...overrides,
});

export const FEED_ATTRIBUTION = 'Source: Reserve Bank of India press releases (rbi.org.in)';

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

/** A UUID made from a number, so fake ids are valid, unique and easy to read in a test. */
export const demoId = (n: number): string =>
  `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;

/** A fictional filing source of a chat answer; override any field. */
export const demoChatSource = (overrides: Partial<ChatSource> = {}): ChatSource => ({
  marker: 1,
  source: 'filing',
  label: 'Annual report · Annual Report 2026 · p.44',
  url: 'https://www.bseindia.com/xml-data/corpfiling/AttachHis/demo-ar-2026.pdf#page=44',
  quote: 'Revenue from operations stood at ₹1,23,456 crore.',
  ...overrides,
});

/** A question as the server stores it; override any field. */
export const demoQuestion = (overrides: Partial<ChatMessage> = {}): ChatMessage => ({
  id: demoId(101),
  role: 'user',
  text: 'How much revenue did DemoCo Alpha report?',
  status: null,
  sources: [],
  created_at: '2026-09-27T10:00:00+00:00',
  ...overrides,
});

/** DemoCo Alpha's invented answer, citing a filing [1], a screener.in figure [2], a computation [3]. */
export const demoAnswer = (overrides: Partial<ChatMessage> = {}): ChatMessage => ({
  id: demoId(102),
  role: 'assistant',
  text: 'DemoCo Alpha reported revenue of ₹1,23,456 crore [1], net profit of ₹12,345.5 crore [2], and growth of 12.2% [3].',
  status: 'answered',
  sources: [
    demoChatSource(),
    demoChatSource({
      marker: 2,
      source: 'screener',
      label: 'screener.in · profit-loss · Net Profit · Mar 2026',
      url: 'https://www.screener.in/company/DEMOA/consolidated/',
      quote: null,
    }),
    demoChatSource({
      marker: 3,
      source: 'derived',
      label: 'Revenue growth, FY2026 against FY2025',
      url: null,
      quote: null,
    }),
  ],
  created_at: '2026-09-27T10:00:05+00:00',
  ...overrides,
});

/** The three real stocks the chat page's panel asks about (the fake has data for what it is given). */
export const CHAT_STOCKS: FakeStock[] = [
  { symbol: 'RELIANCE', name: 'Reliance Industries Limited', bse_code: '500325', sector: 'Energy' },
  { symbol: 'TCS', name: 'Tata Consultancy Services Limited', bse_code: '532540', sector: 'IT' },
  { symbol: 'HDFCBANK', name: 'HDFC Bank Limited', bse_code: '500180', sector: 'Banking' },
];

/** The table the server may add under an answer (invented figures); override any field. */
export const demoTable = (overrides: Partial<ChatTable> = {}): ChatTable => ({
  title: 'DemoCo Alpha net profit (consolidated, ₹ crore)',
  columns: ['Year', 'Net profit (₹ crore)', 'Change'],
  rows: [
    ['FY2026', '12,345', '+1.3%'],
    ['FY2025', '12,187', '-5.9%'],
    ['FY2024', '12,950', ''],
  ],
  source: {
    label: 'screener.in · consolidated, full years',
    url: 'https://www.screener.in/company/DEMOA/consolidated/',
  },
  ...overrides,
});

/** A stored conversation holding one question and its answer; override any field. */
export const demoConversation = (overrides: Partial<Conversation> = {}): Conversation => ({
  id: demoId(1),
  title: 'How much revenue did DemoCo Alpha report?',
  created_at: '2026-09-27T10:00:00+00:00',
  updated_at: '2026-09-27T10:00:05+00:00',
  messages: [demoQuestion(), demoAnswer()],
  ...overrides,
});

/** The fixed choices the server offers for editing the profile by hand (P13). */
export const PROFILE_CHOICES: ProfileChoice[] = [
  {
    field: 'risk_preference',
    label: 'Risk',
    single: true,
    options: [
      { value: 'conservative', label: 'Conservative' },
      { value: 'moderate', label: 'Moderate' },
      { value: 'aggressive', label: 'Aggressive' },
    ],
  },
  {
    field: 'debt_preference',
    label: 'Debt',
    single: true,
    options: [
      { value: 'avoid_high_debt', label: 'Avoid high debt' },
      { value: 'debt_ok', label: 'Debt is fine' },
    ],
  },
  {
    field: 'investment_style',
    label: 'Style',
    single: false,
    options: [
      { value: 'income', label: 'Dividends / income' },
      { value: 'growth', label: 'Growth' },
      { value: 'quality', label: 'Quality' },
      { value: 'value', label: 'Value' },
      { value: 'momentum', label: 'Momentum' },
    ],
  },
  {
    field: 'other_preferences',
    label: 'Other',
    single: false,
    options: [
      { value: 'long_term', label: 'Long-term horizon' },
      { value: 'short_term', label: 'Short-term horizon' },
      { value: 'stability', label: 'Stable, steady results' },
    ],
  },
];

/** A fictional remembered field for DemoCo's user; override any field. */
export const demoProfileField = (
  overrides: Partial<ProfileFieldEntry> = {},
): ProfileFieldEntry => ({
  field: 'risk_preference',
  values: ['conservative'],
  labels: ['Conservative'],
  quote: "I'm conservative, dividend-focused, and I avoid high debt.",
  source: 'chat',
  updated_at: '2026-09-28T10:00:00Z',
  ...overrides,
});

export type Options = {
  signedIn?: boolean;
  email?: string;
  followed?: string[];
  stocks?: FakeStock[];
  documents?: FakeDocument[];
  /** How many documents one page of the list holds (the real API allows up to 100). */
  pageSize?: number;
  /** What GET /api/v1/data/status answers; anything left out is idle, nothing stored. */
  dataStatus?: Partial<DataStatus>;
  /** What POST /api/v1/data/refresh answers per source; by default every source is queued. */
  refresh?: Partial<Omit<RefreshResult, 'status'>>;
  /** What every search returns (the fake does not rank anything). */
  searchHits?: FakeHit[];
  /** Search switched off on the server (409). */
  searchOff?: boolean;
  /** Each stock's insights; a known stock left out gets the empty default. */
  insights?: Record<string, FakeInsights>;
  /** The user's stored conversations, in any order (the fake sorts them newest first). */
  conversations?: Conversation[];
  /** What the chat answers to a question; the fake gives it a fresh id. Default: demoAnswer(). */
  chatAnswer?: (question: string) => ChatMessage;
  /** The chat switched off on the server (409). */
  chatOff?: boolean;
  /** The model failed or the chat's spending cap is used up (503). */
  chatUnavailable?: boolean;
  /** What is remembered about the user at the start (P13); the fake edits and forgets in place. */
  profileFields?: ProfileFieldEntry[];
  /** What GET /api/v1/match answers (P14); default: an empty profile. */
  matches?: MatchResult;
  /** The RBI feed items (P15), newest first; default none. */
  feed?: FeedItem[];
  /** Each stock's net-profit series; a known stock left out gets an empty one. */
  series?: Record<string, Series>;
  /** Each stock's end-of-day prices; a known stock left out gets none yet (latest null). */
  prices?: Record<string, Prices>;
};

/** A fictional reason for the Match page; override any field. */
export const demoReason = (overrides: Partial<MatchReason> = {}): MatchReason => ({
  criterion: 'debt',
  preference: 'avoid_high_debt',
  hard: true,
  outcome: 'pass',
  text: 'Debt to equity is 0.25, within the 1.0 limit for avoiding high debt.',
  citations: [demoCitation()],
  ...overrides,
});

/** One fictional stock's match; override any field. */
export const demoStockMatch = (overrides: Partial<StockMatch> = {}): StockMatch => ({
  symbol: 'DEMOA',
  name: 'DemoCo Alpha Limited',
  status: 'match',
  reasons: [demoReason()],
  cautions: [],
  ...overrides,
});

/** The match reply: an empty profile unless told otherwise. */
export const demoMatches = (overrides: Partial<MatchResult> = {}): MatchResult => ({
  profile_empty: true,
  stocks: [],
  disclaimer:
    'Not investment advice. The rules compare stored figures with your stated preferences; share prices are not used.',
  ...overrides,
});

/**
 * A fictional DemoCo net-profit series ending in FY2026: one value per year, oldest first, each
 * with an invented screener.in citation. Pass the values in ₹ crore.
 */
export const demoSeries = (
  symbol: string,
  values: number[],
  overrides: Partial<Series> = {},
): Series => {
  const lastYear = 2026;
  const points: SeriesPoint[] = values.map((value, index) => {
    const year = lastYear - (values.length - 1 - index);
    return {
      period: `FY${year}`,
      value: String(value),
      citation: demoScreenerCitation({
        label: `screener.in · profit-loss · Net Profit · Mar ${year}`,
        url: `https://www.screener.in/company/${symbol}/consolidated/`,
      }),
    };
  });
  return {
    symbol,
    metric: 'net_profit',
    label: 'Net profit',
    unit: 'INR_CRORE',
    source: 'screener.in, consolidated',
    points,
    ...overrides,
  };
};

/** A fictional price ratio (P/E or dividend yield); override any field. */
export const demoRatio = (overrides: Partial<PriceRatio> = {}): PriceRatio => ({
  status: 'ok',
  value: '12.5',
  reason: 'Invented sample: close divided by stored earnings per share.',
  citations: [],
  ...overrides,
});

/**
 * Invented end-of-day prices for a fictional stock: a close of 101.50 after 100.00 (+1.5%), five
 * adjusted closes, one corporate action. Pass `latest: null, history: []` for "no prices yet".
 */
export const demoPrices = (symbol: string, overrides: Partial<Prices> = {}): Prices => ({
  symbol,
  source: 'BSE daily price file (end of day, not live)',
  latest: {
    date: '2026-09-28',
    close: '101.5',
    prev_close: '100',
    change_pct: '1.5',
    citation: {
      source: 'filing',
      label: 'BSE daily price file · 28 Sep 2026',
      url: 'https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_20260928_F_0000.CSV',
      quote: null,
    },
  },
  history: [
    { date: '2026-09-22', close: '96' },
    { date: '2026-09-23', close: '97.5' },
    { date: '2026-09-24', close: '99' },
    { date: '2026-09-25', close: '100' },
    { date: '2026-09-28', close: '101.5' },
  ],
  returns: { '1m': '4.2', '3m': null, '6m': '-3.1', '1y': null },
  volatility_1y: '18.6',
  pe: demoRatio({ value: '12.5' }),
  dividend_yield: demoRatio({ value: '1.2' }),
  actions: [],
  ...overrides,
});

/** No prices yet, as the server answers while the price sync is off or still filling. */
export const noPrices = (symbol: string): Prices =>
  demoPrices(symbol, {
    latest: null,
    history: [],
    returns: { '1m': null, '3m': null, '6m': null, '1y': null },
    volatility_1y: null,
    pe: demoRatio({ status: 'not_assessable', value: null, reason: 'No price yet.' }),
    dividend_yield: demoRatio({ status: 'not_assessable', value: null, reason: 'No price yet.' }),
  });

export type FakeApi = {
  /** Every request made, as "METHOD /path". */
  requests: string[];
  /** Every request body sent, parsed from JSON, with its "METHOD /path". */
  bodies: { key: string; body: unknown }[];
  /** The conversations as the fake holds them now, by id. */
  conversations: Map<string, Conversation>;
  follows: Set<string>;
  /** Make the next matching request fail with this status. */
  failWith: (key: string, status: number) => void;
  /** Keep matching requests waiting until the returned function is called. */
  hold: (key: string) => () => void;
  expireSession: () => void;
  /** Replace the documents the fake serves (to move a status along, for example). */
  setDocuments: (documents: FakeDocument[]) => void;
  /** Change what the data status answers (an update finishing, for example). */
  setDataStatus: (status: Partial<DataStatus>) => void;
};

export const IDLE_DATA: DataStatus = {
  updating: false,
  filings_checked_at: null,
  prices_to: null,
  rbi_to: null,
  filings_on: true,
  prices_on: true,
  rbi_live: true,
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
  let dataStatus: DataStatus = { ...IDLE_DATA, ...options.dataStatus };
  const bodies: { key: string; body: unknown }[] = [];
  const conversations = new Map((options.conversations ?? []).map((c) => [c.id, c]));
  const profileFields = new Map((options.profileFields ?? []).map((f) => [f.field, f] as const));
  let counter = 1000; // fresh ids and times for what the fake creates, after any demo ones
  const next = (): { id: string; at: string } => {
    counter += 1;
    const at = new Date(Date.parse('2026-09-27T11:00:00Z') + counter * 1000).toISOString();
    return { id: demoId(counter), at };
  };

  const chat = (key: string, body: unknown): Response | null => {
    if (key === 'GET /api/v1/chat/conversations') {
      const items = [...conversations.values()]
        .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
        .slice(0, 20)
        .map(({ id, title, created_at, updated_at }) => ({ id, title, created_at, updated_at }));
      return json(200, { items });
    }
    const gone = /^DELETE \/api\/v1\/chat\/conversations\/([^/?]+)$/.exec(key);
    if (gone) {
      return conversations.delete(decodeURIComponent(gone[1] ?? ''))
        ? json(204)
        : envelope(404, 'not_found');
    }
    const one = /^GET \/api\/v1\/chat\/conversations\/([^/?]+)$/.exec(key);
    if (one) {
      const found = conversations.get(decodeURIComponent(one[1] ?? ''));
      return found ? json(200, found) : envelope(404, 'not_found');
    }
    if (key !== 'POST /api/v1/chat/messages') return null;
    // The real order: switched off, then the question, then the conversation, then the model.
    if (options.chatOff) return envelope(409, 'conflict');
    const { question, conversation_id: id } = (body ?? {}) as {
      question?: unknown;
      conversation_id?: unknown;
    };
    if (typeof question !== 'string' || question.trim().length < 3 || question.length > 1000) {
      return envelope(422, 'validation_error');
    }
    const existing = typeof id === 'string' ? conversations.get(id) : undefined;
    if (id !== null && existing === undefined) return envelope(404, 'not_found');
    if (options.chatUnavailable) return envelope(503, 'unavailable');
    const asked = next();
    const answered = next();
    const userMessage: ChatMessage = demoQuestion({
      id: asked.id,
      text: question,
      created_at: asked.at,
    });
    const answer: ChatMessage = {
      ...(options.chatAnswer ?? (() => demoAnswer()))(question),
      id: answered.id,
      created_at: answered.at,
    };
    const conversation: Conversation = existing ?? {
      id: next().id,
      title: question.slice(0, 60),
      created_at: asked.at,
      updated_at: asked.at,
      messages: [],
    };
    conversations.set(conversation.id, {
      ...conversation,
      updated_at: answered.at,
      messages: [...conversation.messages, userMessage, answer],
    });
    return json(200, { conversation_id: conversation.id, question: userMessage, answer });
  };

  const profile = (key: string, body: unknown): Response | null => {
    if (key === 'GET /api/v1/profile') {
      const result: Profile = { fields: [...profileFields.values()], choices: PROFILE_CHOICES };
      return json(200, result);
    }
    const put = /^PUT \/api\/v1\/profile\/([^/?]+)$/.exec(key);
    if (put) {
      const field = decodeURIComponent(put[1] ?? '');
      const choice = PROFILE_CHOICES.find((c) => c.field === field);
      if (!choice) return envelope(404, 'not_found');
      const { values } = (body ?? {}) as { values?: unknown };
      const validValues =
        Array.isArray(values) &&
        values.length > 0 &&
        values.every((v) => typeof v === 'string' && choice.options.some((o) => o.value === v)) &&
        (!choice.single || values.length === 1);
      if (!validValues) return envelope(422, 'validation_error');
      const chosen = values as string[];
      const labels = chosen.map((v) => choice.options.find((o) => o.value === v)?.label ?? v);
      const entry: ProfileFieldEntry = {
        field: choice.field,
        values: chosen,
        labels,
        quote: null,
        source: 'edited',
        updated_at: next().at,
      };
      profileFields.set(choice.field, entry);
      return json(200, entry);
    }
    const del = /^DELETE \/api\/v1\/profile\/([^/?]+)$/.exec(key);
    if (del) {
      const field = decodeURIComponent(del[1] ?? '');
      const choice = PROFILE_CHOICES.find((c) => c.field === field);
      if (!choice) return envelope(404, 'not_found');
      profileFields.delete(choice.field);
      return json(204);
    }
    if (key === 'DELETE /api/v1/profile') {
      profileFields.clear();
      return json(204);
    }
    return null;
  };

  const handler = async (input: string, init?: RequestInit): Promise<Response> => {
    const method = init?.method ?? 'GET';
    const key = `${method} ${input}`;
    requests.push(key);
    const body: unknown = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
    if (body !== undefined) bodies.push({ key, body });
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
    const seriesCall = /^GET \/api\/v1\/stocks\/([^/?]+)\/series\?metric=([a-z_]+)$/.exec(key);
    if (seriesCall) {
      const symbol = decodeURIComponent(seriesCall[1] ?? '');
      if (!stocks.some((s) => s.symbol === symbol)) return envelope(404, 'not_found');
      const metric = (seriesCall[2] ?? '') as SeriesMetric;
      return json(200, options.series?.[symbol] ?? demoSeries(symbol, [], { metric }));
    }
    const pricesCall = /^GET \/api\/v1\/stocks\/([^/?]+)\/prices$/.exec(key);
    if (pricesCall) {
      const symbol = decodeURIComponent(pricesCall[1] ?? '');
      if (!stocks.some((s) => s.symbol === symbol)) return envelope(404, 'not_found');
      return json(200, options.prices?.[symbol] ?? noPrices(symbol));
    }
    if (key === 'GET /api/v1/feed') {
      return json(200, { items: options.feed ?? [], attribution: FEED_ATTRIBUTION });
    }
    if (key === 'GET /api/v1/match') return json(200, options.matches ?? demoMatches());
    const chatted = chat(key, body);
    if (chatted) return chatted;
    const profiled = profile(key, body);
    if (profiled) return profiled;
    if (key === 'GET /api/v1/data/status') return json(200, dataStatus);
    if (key === 'POST /api/v1/data/refresh') {
      dataStatus = { ...dataStatus, updating: true };
      const result = { filings: 'queued', prices: 'queued', rbi: 'queued', ...options.refresh };
      return json(202, { ...result, status: dataStatus });
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
    bodies,
    conversations,
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
    setDataStatus: (status) => {
      dataStatus = { ...dataStatus, ...status };
    },
  };
}
