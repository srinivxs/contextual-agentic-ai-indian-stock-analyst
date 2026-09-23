import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import {
  availableLabel,
  canCheck,
  getFilingCheck,
  lastCheckedLabel,
  startFilingCheck,
  type FilingCheck,
} from '@/lib/filingChecks';
import { installFakeApi } from '../helpers/fakeApi';

afterEach(() => {
  vi.unstubAllGlobals();
});

const IDLE: FilingCheck = {
  enabled: true,
  checking: false,
  last_checked_at: null,
  next_check_at: null,
};

// 08:35 UTC is 14:05 in India, where the times are shown.
const CHECKED = '2026-09-23T08:35:00+00:00';
const at = (iso: string): Date => new Date(iso);

describe('reading a stock’s check status', () => {
  it('returns what the server says', async () => {
    const api = installFakeApi({ checks: { DEMOA: { last_checked_at: CHECKED } } });
    expect(await getFilingCheck('DEMOA')).toEqual({ ...IDLE, last_checked_at: CHECKED });
    expect(api.requests).toEqual(['GET /api/v1/stocks/DEMOA/filings/check']);
  });

  it('refuses a response that is not the agreed shape', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ enabled: 'yes' }), { status: 200 })),
    );
    await expect(getFilingCheck('DEMOA')).rejects.toMatchObject({ code: 'unexpected_response' });
  });

  it('encodes the symbol so it can never add path segments', async () => {
    const api = installFakeApi();
    await expect(getFilingCheck('A/../B')).rejects.toBeInstanceOf(ApiError);
    expect(api.requests).toContain('GET /api/v1/stocks/A%2F..%2FB/filings/check');
  });
});

describe('starting a check', () => {
  it('says started when the server accepted it', async () => {
    const api = installFakeApi();
    expect(await startFilingCheck('DEMOA')).toBe('started');
    expect(api.requests).toEqual(['POST /api/v1/stocks/DEMOA/filings/check']);
  });

  it('says too soon when the stock was checked within the hour', async () => {
    installFakeApi({ checks: { DEMOA: { next_check_at: '2999-01-01T00:00:00+00:00' } } });
    expect(await startFilingCheck('DEMOA')).toBe('too_soon');
  });

  it('passes any other failure on', async () => {
    const api = installFakeApi();
    api.failWith('POST /api/v1/stocks/DEMOA/filings/check', 500);
    await expect(startFilingCheck('DEMOA')).rejects.toMatchObject({ status: 500 });
  });
});

describe('the labels', () => {
  it('says when it was last checked, in India time, and how long ago', () => {
    const check = { ...IDLE, last_checked_at: CHECKED };
    expect(lastCheckedLabel(check, at('2026-09-23T08:47:00Z'))).toBe(
      'Last checked 14:05 (12 minutes ago)',
    );
    expect(lastCheckedLabel(check, at('2026-09-23T08:36:00Z'))).toBe(
      'Last checked 14:05 (1 minute ago)',
    );
    expect(lastCheckedLabel(check, at('2026-09-23T08:35:30Z'))).toBe(
      'Last checked 14:05 (just now)',
    );
    expect(lastCheckedLabel(check, at('2026-09-23T11:40:00Z'))).toBe(
      'Last checked 14:05 (3 hours ago)',
    );
    expect(lastCheckedLabel(check, at('2026-09-25T09:00:00Z'))).toBe(
      'Last checked 14:05 (2 days ago)',
    );
  });

  it('says so when a stock has never been checked', () => {
    expect(lastCheckedLabel(IDLE, at('2026-09-23T08:47:00Z'))).toBe('Not checked yet');
  });

  it('says when the button works again', () => {
    expect(availableLabel('2026-09-23T09:35:00+00:00')).toBe('Available at 15:05');
  });
});

describe('whether the button works', () => {
  const now = at('2026-09-23T09:00:00Z');

  it('works when nothing is running and the hour has passed', () => {
    expect(canCheck(IDLE, now)).toBe(true);
    expect(canCheck({ ...IDLE, next_check_at: '2026-09-23T08:59:00Z' }, now)).toBe(true);
  });

  it('does not while a check is running, within the hour, or when switched off', () => {
    expect(canCheck({ ...IDLE, checking: true }, now)).toBe(false);
    expect(canCheck({ ...IDLE, next_check_at: '2026-09-23T09:35:00Z' }, now)).toBe(false);
    expect(canCheck({ ...IDLE, enabled: false }, now)).toBe(false);
  });
});
