import { describe, expect, it, vi } from 'vitest';

import { change, direction, getSeries, percentLabel, type SeriesPoint } from '@/lib/series';

const citation = {
  source: 'screener' as const,
  label: 'screener.in · profit-loss · Net Profit · Mar 2026',
  url: 'https://www.screener.in/company/DEMOA/consolidated/',
  quote: null,
};

const point = (period: string, value: string): SeriesPoint => ({ period, value, citation });

const body = (points: unknown[] = [point('FY2025', '100'), point('FY2026', '120')]) => ({
  symbol: 'DEMOA',
  metric: 'net_profit',
  label: 'Net profit',
  unit: 'INR_CRORE',
  source: 'screener.in, consolidated',
  points,
});

function stub(payload: unknown, status = 200): ReturnType<typeof vi.fn> {
  const mock = vi.fn(
    async () =>
      new Response(JSON.stringify(payload), {
        status,
        headers: { 'content-type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', mock);
  return mock;
}

describe('getSeries', () => {
  it('asks for the metric of one stock and returns the checked body', async () => {
    const mock = stub(body());
    const series = await getSeries('DEMOA', 'net_profit');
    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/stocks/DEMOA/series?metric=net_profit');
    expect(series.points.map((p) => p.period)).toEqual(['FY2025', 'FY2026']);
    expect(series.source).toBe('screener.in, consolidated');
  });

  it('defaults to net profit and cannot add path segments through the symbol', async () => {
    const mock = stub(body());
    await getSeries('A/../B');
    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/stocks/A%2F..%2FB/series?metric=net_profit');
  });

  it('accepts an empty series', async () => {
    stub(body([]));
    expect((await getSeries('DEMOA')).points).toEqual([]);
  });

  it.each([
    ['not an object', null],
    ['no points list', { ...body(), points: 'x' }],
    ['an unknown metric', { ...body(), metric: 'total_equity' }],
    ['a unit that is not rupees crore', { ...body(), unit: 'USD_MILLION' }],
    ['a quarterly period', body([point('Q1FY2026', '1')])],
    ['a value that is not a decimal', body([point('FY2026', 'lots')])],
    ['a point without a citation', body([{ period: 'FY2026', value: '1' }])],
    ['a missing label', { ...body(), label: 3 }],
  ])('rejects %s', async (_name, payload) => {
    stub(payload);
    await expect(getSeries('DEMOA')).rejects.toMatchObject({ code: 'unexpected_response' });
  });

  it('passes an API error on', async () => {
    stub({ error: { code: 'not_found', message: 'x', request_id: 'r' } }, 404);
    await expect(getSeries('NOPE')).rejects.toMatchObject({ status: 404 });
  });
});

describe('change', () => {
  it('is null for no points', () => {
    expect(change([])).toBeNull();
  });

  it('gives the latest value and the percent change on the year before', () => {
    expect(change([point('FY2024', '80'), point('FY2025', '100'), point('FY2026', '120')])).toEqual(
      {
        period: 'FY2026',
        value: 120,
        previousPeriod: 'FY2025',
        percent: 20,
      },
    );
  });

  it('shows a fall as a negative percent', () => {
    expect(change([point('FY2025', '200'), point('FY2026', '150')])?.percent).toBe(-25);
  });

  it('has the latest value but no percent for a single year', () => {
    expect(change([point('FY2026', '120')])).toEqual({
      period: 'FY2026',
      value: 120,
      previousPeriod: null,
      percent: null,
    });
  });

  it('gives no percent when the year before is missing from the data', () => {
    expect(change([point('FY2023', '100'), point('FY2026', '120')])?.percent).toBeNull();
  });

  it('gives no percent on a base of zero or a loss', () => {
    expect(change([point('FY2025', '0'), point('FY2026', '5')])?.percent).toBeNull();
    expect(change([point('FY2025', '-10'), point('FY2026', '5')])?.percent).toBeNull();
  });

  it('does not depend on the order of the points it is given', () => {
    expect(change([point('FY2026', '120'), point('FY2025', '100')])?.percent).toBe(20);
  });
});

describe('percentLabel and direction', () => {
  it('formats a percent with its sign and one decimal', () => {
    expect(percentLabel(12.34)).toBe('+12.3%');
    expect(percentLabel(-4.06)).toBe('-4.1%');
    expect(percentLabel(0)).toBe('0.0%');
    expect(percentLabel(null)).toBeNull();
  });

  it('names the direction for colouring', () => {
    expect(direction(3)).toBe('rise');
    expect(direction(-3)).toBe('fall');
    expect(direction(0)).toBe('flat');
    expect(direction(null)).toBe('flat');
  });
});
