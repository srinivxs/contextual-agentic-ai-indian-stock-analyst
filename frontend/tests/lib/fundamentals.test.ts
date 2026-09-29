import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import { fundamentalValue, getFundamentals, type FundamentalItem } from '@/lib/fundamentals';

import { demoFundamentals } from '../helpers/fakeApi';

function serve(body: unknown): ReturnType<typeof vi.fn> {
  const mock = vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', mock);
  return mock;
}

afterEach(() => vi.unstubAllGlobals());

const item = (overrides: Partial<FundamentalItem>): FundamentalItem => ({
  name: 'x',
  label: 'X',
  status: 'ok',
  value: '1',
  unit: 'RATIO',
  source: 'screener',
  note: null,
  ...overrides,
});

describe('the fundamentals card', () => {
  it('asks for one stock and checks the shape', async () => {
    const mock = serve(demoFundamentals('TCS'));
    expect((await getFundamentals('TCS')).items).toHaveLength(10);
    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/stocks/TCS/fundamentals');
  });

  it('refuses a card of the wrong shape, or a value that is not a plain number', async () => {
    serve({ ...demoFundamentals('TCS'), items: [item({ value: '1e9' })] });
    await expect(getFundamentals('TCS')).rejects.toBeInstanceOf(ApiError);
    serve({ symbol: 'TCS' });
    await expect(getFundamentals('TCS')).rejects.toBeInstanceOf(ApiError);
  });

  it('writes each value in its unit, Indian grouping for rupees', () => {
    expect(fundamentalValue(item({ value: '1234567', unit: 'INR_CRORE' }))).toBe('₹12,34,567 Cr');
    expect(fundamentalValue(item({ value: '137.78', unit: 'INR_PER_SHARE' }))).toBe('₹137.78');
    expect(fundamentalValue(item({ value: '25.5', unit: 'PERCENT' }))).toBe('25.5%');
    expect(fundamentalValue(item({ value: '22.5', unit: 'RATIO' }))).toBe('22.5');
  });

  it('says a missing value is missing, never zero', () => {
    expect(fundamentalValue(item({ status: 'not_available', value: null }))).toBe('Not available');
    expect(fundamentalValue(item({ status: 'not_applicable', value: null }))).toBe(
      'Not applicable',
    );
  });
});
