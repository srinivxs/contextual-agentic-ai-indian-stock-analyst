import { describe, expect, it } from 'vitest';

import {
  followUps,
  keyMetrics,
  newestEvents,
  rangeHistory,
  shortName,
  stocksIn,
} from '@/lib/chatContext';
import { demoFact, demoInsights } from '../helpers/fakeApi';

describe('stocksIn', () => {
  it.each([
    ['How is TCS doing?', ['TCS']],
    ['tata consultancy services revenue', ['TCS']],
    ['What about Reliance Industries?', ['RELIANCE']],
    ['and RIL?', ['RELIANCE']],
    ['HDFC Bank NPA', ['HDFCBANK']],
    ['hdfc please', ['HDFCBANK']],
    ['HDFCBANK debt', ['HDFCBANK']],
  ])('finds the stock in %s', (text, expected) => {
    expect(stocksIn(text)).toEqual(expected);
  });

  it('lists several in the order they are mentioned, each once', () => {
    expect(stocksIn('Compare HDFC Bank with TCS and Reliance, then TCS again')).toEqual([
      'HDFCBANK',
      'TCS',
      'RELIANCE',
    ]);
  });

  it('finds none in other text, and is not fooled by parts of words', () => {
    expect(stocksIn('')).toEqual([]);
    expect(stocksIn('Which stocks have low debt?')).toEqual([]);
    expect(stocksIn('brilliant tcsx')).toEqual([]);
  });
});

describe('shortName and followUps', () => {
  it('names each stock the way people say it', () => {
    expect(shortName('TCS')).toBe('TCS');
    expect(shortName('HDFCBANK')).toBe('HDFC Bank');
    expect(shortName('RELIANCE')).toBe('Reliance');
  });

  it('builds the four follow-ups from the stock, comparing with the other two', () => {
    expect(followUps('TCS')).toEqual([
      'Show revenue and net profit for TCS',
      'Compare TCS with HDFC Bank and Reliance',
      'Latest news on TCS',
      'How does TCS fit my profile?',
    ]);
    expect(followUps('RELIANCE')[1]).toBe('Compare Reliance with TCS and HDFC Bank');
  });
});

describe('rangeHistory', () => {
  const history = [
    { date: '2025-09-29', close: '80' },
    { date: '2026-03-30', close: '90' },
    { date: '2026-06-29', close: '95' },
    { date: '2026-08-28', close: '99' },
    { date: '2026-09-28', close: '101.5' },
  ];

  it('keeps the last month, three months, six months or year of closes', () => {
    expect(rangeHistory(history, '1m').map((p) => p.date)).toEqual(['2026-08-28', '2026-09-28']);
    expect(rangeHistory(history, '3m').map((p) => p.date)).toEqual([
      '2026-06-29',
      '2026-08-28',
      '2026-09-28',
    ]);
    expect(rangeHistory(history, '6m')).toHaveLength(4);
    expect(rangeHistory(history, '1y')).toHaveLength(5);
  });

  it('is empty for no history', () => {
    expect(rangeHistory([], '1m')).toEqual([]);
  });
});

describe('newestEvents', () => {
  it('gives the newest three, newest first', () => {
    const base = demoInsights().events[0]!;
    const events = ['2026-01-01', '2026-07-01', '2026-03-01', '2026-05-01'].map((d) => ({
      ...base,
      event_date: d,
    }));
    expect(newestEvents(events).map((e) => e.event_date)).toEqual([
      '2026-07-01',
      '2026-05-01',
      '2026-03-01',
    ]);
  });
});

describe('keyMetrics', () => {
  const fact = demoFact;

  it('takes the latest full year of revenue, net profit, EPS and ROE, with growth as the change', () => {
    const insights = demoInsights({
      key_facts: [
        fact(),
        fact({ period: 'FY2025', value: '110000.0000' }),
        fact({ period: 'Q3FY2026', value: '999.0000' }),
        fact({ metric: 'net_profit', label: 'Net profit', value: '12345.5000' }),
        fact({ metric: 'eps_basic', label: 'Basic EPS', unit: 'INR_PER_SHARE', value: '45.6' }),
        fact({
          metric: 'return_on_equity',
          label: 'Return on equity',
          unit: 'PERCENT',
          value: '21.5',
        }),
        fact({ metric: 'total_borrowings', label: 'Total borrowings', value: '5.0' }),
      ],
    });
    const { period, rows } = keyMetrics(insights);
    expect(period).toBe('FY2026');
    expect(rows.map((r) => r.label)).toEqual([
      'Revenue from operations',
      'Net profit',
      'Basic EPS',
      'Return on equity',
    ]);
    expect(rows[0]).toMatchObject({ value: '₹1,23,456 crore', change: '+12.5%', tone: 'rise' });
    expect(rows[1]).toMatchObject({ value: '₹12,345.5 crore', change: null });
    expect(rows[2]?.value).toBe('₹45.60 per share');
    expect(rows[3]?.value).toBe('21.5%');
  });

  it('uses net interest income for a bank', () => {
    const insights = demoInsights({
      is_financial: true,
      key_facts: [
        fact({ metric: 'net_interest_income', label: 'Net interest income', value: '100.0' }),
        fact({ value: '5.0' }),
      ],
    });
    expect(keyMetrics(insights).rows.map((r) => r.label)).toEqual(['Net interest income']);
  });

  it('shows a fall in red and no change when growth cannot be worked out', () => {
    const insights = demoInsights({
      key_facts: [fact()],
      derived: demoInsights().derived.map((d) =>
        d.name === 'revenue_growth' ? { ...d, value: '-3.0' } : d,
      ),
    });
    expect(keyMetrics(insights).rows[0]).toMatchObject({ change: '-3.0%', tone: 'fall' });
    const none = demoInsights({ key_facts: [fact()], derived: [] });
    expect(keyMetrics(none).rows[0]).toMatchObject({ change: null, tone: 'flat' });
  });

  it('marks a figure from another year than the title, and finds no rows without full years', () => {
    const insights = demoInsights({
      key_facts: [fact(), fact({ metric: 'net_profit', label: 'Net profit', period: 'FY2025' })],
    });
    const { period, rows } = keyMetrics(insights);
    expect(period).toBe('FY2026');
    expect(rows[1]?.period).toBe('FY2025');
    expect(keyMetrics(demoInsights({ key_facts: [fact({ period: 'Q3FY2026' })] }))).toEqual({
      period: null,
      rows: [],
    });
  });
});
