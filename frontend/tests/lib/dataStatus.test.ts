import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import {
  dataNote,
  getDataStatus,
  refreshData,
  refreshMessage,
  type DataStatus,
  type RefreshResult,
} from '@/lib/dataStatus';

const STATUS: DataStatus = {
  updating: false,
  filings_checked_at: '2026-09-29T09:30:00+00:00',
  prices_to: '2026-09-28',
  rbi_to: '2026-09-29T06:00:00+00:00',
  filings_on: true,
  prices_on: true,
  rbi_live: true,
};

function serve(status: number, body: unknown): ReturnType<typeof vi.fn> {
  const mock = vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', mock);
  return mock;
}

afterEach(() => vi.unstubAllGlobals());

describe('reading and starting an update', () => {
  it('reads the status', async () => {
    const mock = serve(200, STATUS);
    expect(await getDataStatus()).toEqual(STATUS);
    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/data/status');
  });

  it('refuses a status of the wrong shape', async () => {
    serve(200, { ...STATUS, updating: 'yes' });
    await expect(getDataStatus()).rejects.toBeInstanceOf(ApiError);
  });

  it('starts an update with a POST and reads what happened per source', async () => {
    const result: RefreshResult = {
      filings: 'queued',
      prices: 'current',
      rbi: 'recent',
      status: STATUS,
    };
    const mock = serve(202, result);
    expect(await refreshData()).toEqual(result);
    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/data/refresh');
    expect((mock.mock.calls[0]?.[1] as RequestInit).method).toBe('POST');
  });

  it('refuses an update answer of the wrong shape', async () => {
    serve(202, { filings: 'maybe', prices: 'queued', rbi: 'queued', status: STATUS });
    await expect(refreshData()).rejects.toBeInstanceOf(ApiError);
  });
});

describe('the note every page shows', () => {
  it('gives the newest date and each source, and says the data is not live', () => {
    expect(dataNote(STATUS)).toBe(
      'Data updated to 29 Sep 2026: filings checked 29 Sep 2026, share prices to the 28 Sep ' +
        '2026 close, RBI releases to 29 Sep 2026. Not live data.',
    );
  });

  it('says what is missing, switched off or only a sample', () => {
    expect(
      dataNote({
        ...STATUS,
        filings_checked_at: null,
        prices_to: null,
        rbi_to: null,
        rbi_live: false,
      }),
    ).toBe(
      'No data updated yet: filings not checked yet, no share prices yet, RBI releases are ' +
        'sample items. Not live data.',
    );
  });

  it('says so while an update runs', () => {
    expect(dataNote({ ...STATUS, updating: true })).toMatch(/^Updating… Data updated to/);
  });
});

describe('what a click did', () => {
  const result = (overrides: Partial<RefreshResult>): RefreshResult => ({
    filings: 'queued',
    prices: 'queued',
    rbi: 'queued',
    status: STATUS,
    ...overrides,
  });

  it('names what is being updated', () => {
    expect(refreshMessage(result({}))).toBe(
      'Updating filings, share prices and RBI releases. New data appears within a few minutes.',
    );
    expect(refreshMessage(result({ filings: 'recent', prices: 'current' }))).toBe(
      'Updating RBI releases. New data appears within a few minutes.',
    );
  });

  it('says when everything is already up to date, and what is switched off', () => {
    expect(refreshMessage(result({ filings: 'recent', prices: 'current', rbi: 'recent' }))).toBe(
      'Already up to date: filings are checked at most once an hour and RBI releases every 15 ' +
        'minutes.',
    );
    expect(refreshMessage(result({ filings: 'off', prices: 'off', rbi: 'recent' }))).toBe(
      'Already up to date: filings are checked at most once an hour and RBI releases every 15 ' +
        'minutes. Automatic filings are switched off. Share prices are switched off.',
    );
  });
});
