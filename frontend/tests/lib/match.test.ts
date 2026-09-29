import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import { getMatches, OUTCOME_LABELS, STATUS_LABELS } from '@/lib/match';
import { demoMatches, demoReason, demoStockMatch, installFakeApi } from '../helpers/fakeApi';

function serve(body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    ),
  );
}

describe('getMatches', () => {
  it('returns the stocks with their reasons and cautions', async () => {
    const wanted = demoMatches({
      profile_empty: false,
      stocks: [demoStockMatch({ cautions: [demoReason({ criterion: 'sentiment', hard: false })] })],
    });
    const api = installFakeApi({ matches: wanted });

    expect(await getMatches()).toEqual(wanted);
    expect(api.requests).toEqual(['GET /api/v1/match']);
  });

  it('returns an empty profile with no stocks', async () => {
    installFakeApi();
    const result = await getMatches();
    expect(result.profile_empty).toBe(true);
    expect(result.stocks).toEqual([]);
  });

  const withReason = (reason: unknown) => ({
    ...demoMatches(),
    stocks: [{ ...demoStockMatch(), reasons: [reason] }],
  });

  it.each([
    ['null', null],
    ['no flag', { stocks: [], disclaimer: 'x' }],
    ['no disclaimer', { profile_empty: true, stocks: [] }],
    ['stocks not a list', { profile_empty: true, stocks: 'x', disclaimer: 'x' }],
    ['an unknown status', { ...demoMatches(), stocks: [{ ...demoStockMatch(), status: 'great' }] }],
    ['an unknown outcome', withReason({ ...demoReason(), outcome: 'ok' })],
    ['a hard flag that is not a boolean', withReason({ ...demoReason(), hard: 'yes' })],
    ['a bad citation', withReason({ ...demoReason(), citations: [{ label: 3 }] })],
    ['a stock that is null', { ...demoMatches(), stocks: [null] }],
  ])('refuses %s', async (_name, body) => {
    serve(body);
    await expect(getMatches()).rejects.toMatchObject({
      status: 200,
      code: 'unexpected_response',
    });
  });

  it('passes a failed request on', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/match', 500);
    await expect(getMatches()).rejects.toBeInstanceOf(ApiError);
  });
});

describe('the plain words', () => {
  it('names every status and outcome', () => {
    expect(Object.values(STATUS_LABELS)).toEqual([
      'Match',
      'Partial match',
      'No match',
      'Not enough data',
    ]);
    expect(Object.values(OUTCOME_LABELS)).toEqual([
      'Meets',
      'Falls short',
      'Fails a must-have',
      "Can't be judged",
      'No data',
    ]);
  });
});
