import { describe, expect, it, vi } from 'vitest';

import {
  actionLine,
  getPrices,
  hasPrices,
  priceLabel,
  signedPercent,
  type Prices,
} from '@/lib/prices';
import { demoPrices } from '../helpers/fakeApi';

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

const NO_RETURNS = { '1m': 'x', '3m': null, '6m': null, '1y': null };

describe('getPrices', () => {
  it('asks for one stock and returns the checked body', async () => {
    const mock = stub(demoPrices('DEMOA'));
    const prices = await getPrices('DEMOA');
    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/stocks/DEMOA/prices');
    expect(prices.latest?.close).toBe('101.5');
    expect(prices.history.length).toBeGreaterThan(1);
  });

  it('encodes the symbol so it cannot add path segments', async () => {
    const mock = stub(demoPrices('DEMOA'));
    await getPrices('A/../B');
    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/stocks/A%2F..%2FB/prices');
  });

  it('accepts a stock with no prices yet', async () => {
    stub(demoPrices('DEMOA', { latest: null, history: [] }));
    expect((await getPrices('DEMOA')).latest).toBeNull();
  });

  it.each([
    ['not an object', 'nope'],
    ['a bad close', demoPrices('DEMOA', { history: [{ date: '2026-09-01', close: 'abc' }] })],
    ['a bad date', demoPrices('DEMOA', { history: [{ date: 'yesterday', close: '1' }] })],
    ['a missing returns key', { ...demoPrices('DEMOA'), returns: { '1m': null } }],
    ['a non-numeric return', demoPrices('DEMOA', { returns: NO_RETURNS })],
    ['a bad volatility', { ...demoPrices('DEMOA'), volatility_1y: 5 }],
    ['a missing volatility', { ...demoPrices('DEMOA'), volatility_1y: undefined }],
    [
      'a ratio without a reason',
      { ...demoPrices('DEMOA'), pe: { status: 'ok', value: '1', citations: [] } },
    ],
    ['a bad action', demoPrices('DEMOA', { actions: [{ date: '2025', factor: '0.5' }] })],
    [
      'a bad citation',
      {
        ...demoPrices('DEMOA'),
        latest: { ...demoPrices('DEMOA').latest, citation: { source: 'web', label: 'x' } },
      },
    ],
  ])('refuses %s', async (_name, payload) => {
    stub(payload);
    await expect(getPrices('DEMOA')).rejects.toMatchObject({ code: 'unexpected_response' });
  });
});

describe('formatting', () => {
  it('writes a price in rupees with Indian grouping and two decimals', () => {
    expect(priceLabel('2071.7')).toBe('₹2,071.70');
    expect(priceLabel('123456.5')).toBe('₹1,23,456.50');
    expect(priceLabel(99)).toBe('₹99.00');
  });

  it('writes a signed percent with a real minus sign', () => {
    expect(signedPercent('0.6')).toBe('+0.6%');
    expect(signedPercent('-0.6')).toBe('−0.6%');
    expect(signedPercent('0')).toBe('0.0%');
    expect(signedPercent('-0.01')).toBe('0.0%');
    expect(signedPercent('22.44')).toBe('+22.4%');
    expect(signedPercent(null)).toBeNull();
  });

  it('knows when prices exist', () => {
    expect(hasPrices(demoPrices('DEMOA'))).toBe(true);
    expect(hasPrices(demoPrices('DEMOA', { latest: null }) as Prices)).toBe(false);
    expect(hasPrices(null)).toBe(false);
    expect(hasPrices(undefined)).toBe(false);
  });

  it('describes a corporate action plainly', () => {
    expect(actionLine({ date: '2025-08-26', factor: '0.5' })).toBe(
      '26 Aug 2025: bonus issue or split, factor 0.5; earlier prices adjusted',
    );
  });
});
