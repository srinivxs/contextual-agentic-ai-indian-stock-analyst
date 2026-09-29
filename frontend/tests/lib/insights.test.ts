import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import {
  basisNote,
  citationLink,
  derivedValueLabel,
  eventDateLabel,
  eventTypeLabel,
  factTable,
  formatAmount,
  getInsights,
  isTickerSymbol,
  periodLabel,
  scoreLabel,
  sentimentLabel,
  stockPageHref,
  type DerivedValue,
} from '@/lib/insights';
import {
  demoCitation,
  demoFact,
  demoInsights,
  demoRbiCitation,
  demoScreenerCitation,
  installFakeApi,
} from '../helpers/fakeApi';

afterEach(() => {
  vi.unstubAllGlobals();
});

const serve = (body: unknown): void => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })),
  );
};

describe('reading a stock’s insights', () => {
  it('asks for one stock and returns what the server sent', async () => {
    const api = installFakeApi({ insights: { DEMOA: demoInsights() } });

    expect(await getInsights('DEMOA')).toEqual(demoInsights());
    expect(api.requests).toEqual(['GET /api/v1/stocks/DEMOA/insights']);
  });

  it('accepts the empty answer for a stock nothing was extracted from yet', async () => {
    installFakeApi();
    const insights = await getInsights('DEMOB');
    expect(insights.key_facts).toEqual([]);
    expect(insights.derived.map((d) => d.status)).toEqual(Array(4).fill('insufficient_data'));
  });

  it('encodes the symbol so it can never add path segments', async () => {
    const api = installFakeApi();
    await expect(getInsights('A/../B')).rejects.toBeInstanceOf(ApiError);
    expect(api.requests).toEqual(['GET /api/v1/stocks/A%2F..%2FB/insights']);
  });

  it('passes a 404 for an unknown stock on', async () => {
    installFakeApi();
    await expect(getInsights('NOPE')).rejects.toMatchObject({ status: 404 });
  });

  it('accepts a disputed fact and a percentage with no currency', async () => {
    const body = demoInsights({
      key_facts: [
        demoFact({ status: 'disputed', disputed_by: [demoScreenerCitation()] }),
        demoFact({ metric: 'return_on_equity', currency: null, unit: 'PERCENT', value: '14.7' }),
        demoFact({ unit: 'USD_MILLION', currency: 'USD', basis: 'unspecified', value: '-12' }),
      ],
      sentiment: { status: 'ok', score: -0.5, label: 'negative', events_counted: 3 },
    });
    serve(body);
    expect(await getInsights('DEMOA')).toEqual(body);
  });

  const good = demoInsights();
  const fact = good.key_facts[0];
  const derived = good.derived[0];
  const event = good.events[0];

  it.each([
    ['not an object', null],
    ['no key facts', { ...good, key_facts: undefined }],
    ['a fact with an unknown metric', { ...good, key_facts: [{ ...fact, metric: 'ebitda' }] }],
    ['a fact with an unknown unit', { ...good, key_facts: [{ ...fact, unit: 'EUR_CRORE' }] }],
    ['a fact whose value is a number', { ...good, key_facts: [{ ...fact, value: 12 }] }],
    ['a fact whose value is not a decimal', { ...good, key_facts: [{ ...fact, value: '1e9' }] }],
    ['a fact with a strange period', { ...good, key_facts: [{ ...fact, period: '2026' }] }],
    ['a fact with an unknown basis', { ...good, key_facts: [{ ...fact, basis: 'group' }] }],
    ['a fact with an unknown status', { ...good, key_facts: [{ ...fact, status: 'maybe' }] }],
    [
      'a citation with a numeric url',
      { ...good, key_facts: [{ ...fact, citation: { ...demoCitation(), url: 1 } }] },
    ],
    [
      'a citation from an unknown source',
      { ...good, key_facts: [{ ...fact, citation: { ...demoCitation(), source: 'blog' } }] },
    ],
    [
      'a disputing citation that is malformed',
      { ...good, key_facts: [{ ...fact, disputed_by: [{}] }] },
    ],
    [
      'a derived value with an unknown status',
      { ...good, derived: [{ ...derived, status: 'great' }] },
    ],
    [
      'a derived value with an unknown name',
      { ...good, derived: [{ ...derived, name: 'pe_ratio' }] },
    ],
    ['a derived value without a reason', { ...good, derived: [{ ...derived, reason: null }] }],
    ['a sentiment score that is text', { ...good, sentiment: { ...good.sentiment, score: '0.4' } }],
    [
      'a sentiment with an unknown label',
      { ...good, sentiment: { ...good.sentiment, label: 'great' } },
    ],
    ['an event without a citation', { ...good, events: [{ ...event, citation: undefined }] }],
    ['an event with a strange date', { ...good, events: [{ ...event, event_date: 'yesterday' }] }],
  ])('refuses a response with %s', async (_label, body) => {
    serve(body);
    await expect(getInsights('DEMOA')).rejects.toMatchObject({
      status: 200,
      code: 'unexpected_response',
    });
  });
});

describe('formatting an amount', () => {
  it.each([
    ['123456.0000', 'INR_CRORE', '₹1,23,456 crore'],
    ['12345.0000', 'INR_CRORE', '₹12,345 crore'],
    ['1234567.5', 'INR_CRORE', '₹12,34,567.5 crore'],
    ['12.3456', 'INR_CRORE', '₹12.35 crore'],
    ['-1200.00', 'INR_CRORE', '-₹1,200 crore'],
    ['1800.0000', 'USD_MILLION', 'US$1,800 million'],
    ['1234567', 'USD_MILLION', 'US$1,234,567 million'],
    ['5.5', 'INR_PER_SHARE', '₹5.50 per share'],
    ['0.25', 'USD_PER_SHARE', 'US$0.25 per share'],
    ['14.70', 'PERCENT', '14.7%'],
    ['-2.5', 'PERCENT', '-2.5%'],
  ] as const)('%s %s -> %s', (value, unit, expected) => {
    expect(formatAmount(value, unit)).toBe(expected);
  });
});

describe('period labels', () => {
  it('leaves a fiscal year as it is and spaces out a quarter', () => {
    expect(periodLabel('FY2026')).toBe('FY2026');
    expect(periodLabel('Q3FY2026')).toBe('Q3 FY2026');
  });
});

describe('basis notes', () => {
  it('says nothing for consolidated and names the others', () => {
    expect(basisNote('consolidated')).toBe('');
    expect(basisNote('standalone')).toBe('(standalone)');
    expect(basisNote('unspecified')).toBe('(basis not stated)');
  });
});

describe('citation links', () => {
  it('links a filing to its BSE address', () => {
    const citation = demoCitation();
    expect(citationLink(citation)).toBe(citation.url);
  });

  it('links a screener citation to the company page on screener.in', () => {
    const citation = demoScreenerCitation();
    expect(citationLink(citation)).toBe(citation.url);
  });

  it.each([
    ['no address', demoCitation({ url: null })],
    ['a javascript: address', demoCitation({ url: 'javascript:alert(1)' })],
    ['a look-alike BSE host', demoCitation({ url: 'https://www.bseindia.com.evil.test/x.pdf' })],
    ['plain http', demoCitation({ url: 'http://www.bseindia.com/x.pdf' })],
    [
      'a look-alike screener host',
      demoScreenerCitation({ url: 'https://www.screener.in.evil.test/company/X/' }),
    ],
    [
      'a screener page that is not a company',
      demoScreenerCitation({ url: 'https://www.screener.in/login/' }),
    ],
    [
      'a filing that points at screener',
      demoCitation({ url: 'https://www.screener.in/company/DEMOA/' }),
    ],
    [
      'a screener citation that points at BSE',
      demoScreenerCitation({ url: 'https://www.bseindia.com/x.pdf' }),
    ],
  ])('gives no link for %s', (_label, citation) => {
    expect(citationLink(citation)).toBeNull();
  });
});

describe('the key-facts table', () => {
  it('has one row per metric and one column per period, newest first', () => {
    const table = factTable([
      demoFact({ period: 'FY2026' }),
      demoFact({ period: 'FY2025' }),
      demoFact({ metric: 'net_profit', label: 'Net profit', period: 'Q3FY2026' }),
      demoFact({ metric: 'net_profit', label: 'Net profit', period: 'FY2024' }),
    ]);

    expect(table.periods).toEqual(['FY2026', 'Q3FY2026', 'FY2025', 'FY2024']);
    expect(table.rows.map((row) => row.label)).toEqual(['Revenue from operations', 'Net profit']);
    expect(table.rows[0]?.cells.map((cell) => cell?.period ?? null)).toEqual([
      'FY2026',
      null,
      'FY2025',
      null,
    ]);
    expect(table.rows[1]?.cells.map((cell) => cell?.period ?? null)).toEqual([
      null,
      'Q3FY2026',
      null,
      'FY2024',
    ]);
  });

  it('puts a full year after its own fourth quarter', () => {
    const table = factTable([demoFact({ period: 'Q4FY2026' }), demoFact({ period: 'FY2026' })]);
    expect(table.periods).toEqual(['FY2026', 'Q4FY2026']);
  });

  it('keeps the first fact when the server sends two for one metric and period', () => {
    const table = factTable([demoFact({ value: '1' }), demoFact({ value: '2' })]);
    expect(table.rows[0]?.cells[0]?.value).toBe('1');
  });

  it('is empty when there are no facts', () => {
    expect(factTable([])).toEqual({ periods: [], rows: [] });
  });
});

describe('derived values', () => {
  const derived = (overrides: Partial<DerivedValue>): DerivedValue => ({
    name: 'debt_to_equity',
    label: 'Debt to equity',
    status: 'ok',
    value: '0.2500',
    reason: 'A reason.',
    citations: [],
    ...overrides,
  });

  it.each([
    [derived({}), '0.25'],
    [derived({ name: 'revenue_growth', value: '12.5' }), '+12.5%'],
    [derived({ name: 'profit_growth', value: '-3' }), '-3.0%'],
    [derived({ name: 'profit_growth', value: '0' }), '0.0%'],
    [derived({ name: 'latest_dividend', value: '5.5' }), '₹5.50 per share'],
    [derived({ status: 'not_applicable', value: null }), 'Not applicable'],
    [derived({ status: 'not_assessable', value: null }), 'Not assessable'],
    [derived({ status: 'insufficient_data', value: null }), 'Not enough data'],
    [derived({ status: 'ok', value: null }), 'Not enough data'],
  ])('%o reads %s', (value, expected) => {
    expect(derivedValueLabel(value)).toBe(expected);
  });
});

describe('sentiment and events', () => {
  it('names the sentiment, or says there were too few events', () => {
    expect(sentimentLabel({ status: 'ok', score: 0.4, label: 'positive', events_counted: 5 })).toBe(
      'Positive',
    );
    expect(sentimentLabel({ status: 'ok', score: 0, label: 'mixed', events_counted: 2 })).toBe(
      'Mixed',
    );
    expect(
      sentimentLabel({ status: 'insufficient_data', score: null, label: null, events_counted: 1 }),
    ).toBe('Not enough events');
  });

  it('shows a score with its sign', () => {
    expect(scoreLabel(0.42)).toBe('+0.42');
    expect(scoreLabel(-0.5)).toBe('-0.50');
    expect(scoreLabel(0)).toBe('0.00');
  });

  it('writes a date as DD Mon YYYY, whatever the viewer’s time zone', () => {
    expect(eventDateLabel('2026-07-01')).toBe('01 Jul 2026');
    expect(eventDateLabel('2025-12-31')).toBe('31 Dec 2025');
  });

  it('writes an event type in words', () => {
    expect(eventTypeLabel('earnings_results')).toBe('Earnings results');
    expect(eventTypeLabel('dividend')).toBe('Dividend');
  });
});

describe('the stock page address', () => {
  it('is the one static page with the symbol as a query parameter', () => {
    expect(stockPageHref('DEMOA')).toBe('/stock/?symbol=DEMOA');
    expect(stockPageHref('M&M')).toBe('/stock/?symbol=M%26M');
  });

  it('accepts only ticker-shaped symbols', () => {
    expect(isTickerSymbol('DEMOA')).toBe(true);
    expect(isTickerSymbol('M&M')).toBe(true);
    expect(isTickerSymbol('')).toBe(false);
    expect(isTickerSymbol('demoa')).toBe(false);
    expect(isTickerSymbol('A/B')).toBe(false);
    expect(isTickerSymbol('A'.repeat(21))).toBe(false);
  });
});

describe('RBI citations', () => {
  it('links an RBI citation only to rbi.org.in', () => {
    const citation = demoRbiCitation();
    expect(citationLink(citation)).toBe(citation.url);
    expect(citationLink(demoRbiCitation({ url: 'https://www.bseindia.com/x' }))).toBeNull();
    expect(citationLink(demoRbiCitation({ url: null }))).toBeNull();
  });

  it('accepts an insights reply with an rbi citation', async () => {
    installFakeApi({
      insights: {
        DEMOA: demoInsights({
          events: [
            {
              event_type: 'regulatory',
              sentiment: 'neutral',
              impact: 'low',
              event_date: '2026-09-12',
              summary: 'x',
              citation: demoRbiCitation(),
            },
          ],
        }),
      },
    });
    expect((await getInsights('DEMOA')).events[0]?.citation.source).toBe('rbi');
  });
});
