import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import { hitLabel, pageLink, searchFilings } from '@/lib/search';
import { demoHit, installFakeApi } from '../helpers/fakeApi';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('searching the filings', () => {
  it('asks for one stock’s passages and returns them', async () => {
    const api = installFakeApi({ searchHits: [demoHit()] });

    const outcome = await searchFilings('employee attrition', 'DEMOA');

    expect(outcome).toEqual({ status: 'ok', hits: [demoHit()] });
    expect(api.requests).toEqual(['GET /api/v1/search?q=employee%20attrition&symbol=DEMOA']);
  });

  it('encodes the question and symbol so neither can add parameters or path segments', async () => {
    const api = installFakeApi();
    await searchFilings('a&symbol=X#?', 'A/B');
    expect(api.requests).toEqual(['GET /api/v1/search?q=a%26symbol%3DX%23%3F&symbol=A%2FB']);
  });

  it('says when search is switched off on the server', async () => {
    installFakeApi({ searchOff: true });
    expect(await searchFilings('revenue', 'DEMOA')).toEqual({ status: 'off' });
  });

  it('says when search is unavailable for now', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/search?q=revenue&symbol=DEMOA', 503);
    expect(await searchFilings('revenue', 'DEMOA')).toEqual({
      status: 'unavailable',
    });
  });

  it('passes any other failure on', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/search?q=revenue&symbol=DEMOA', 500);
    await expect(searchFilings('revenue', 'DEMOA')).rejects.toBeInstanceOf(ApiError);
  });

  it('refuses a response that is not the agreed shape', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ items: [{ page: 'one' }] }), {
            status: 200,
          }),
      ),
    );
    await expect(searchFilings('revenue', 'DEMOA')).rejects.toMatchObject({
      code: 'unexpected_response',
    });
  });
});

describe('a result’s link and label', () => {
  it('opens the official filing at the cited page', () => {
    const hit = demoHit({ page: 7 });
    expect(pageLink(hit)).toBe(`${hit.source_url}#page=7`);
  });

  it.each(['javascript:alert(1)', 'https://evil.example/x.pdf', null])(
    'has no link unless the address is an https BSE one: %s',
    (url) => {
      expect(pageLink(demoHit({ source_url: url }))).toBeNull();
    },
  );

  it('names the filing by its kind and period, and falls back to the title', () => {
    expect(hitLabel(demoHit({ kind: 'transcript', period: 'Jul 2026' }))).toBe(
      'Earnings call · Jul 2026',
    );
    expect(hitLabel(demoHit({ kind: 'annual_report', period: 'Annual Report 2025' }))).toBe(
      'Annual report · Annual Report 2025',
    );
    expect(hitLabel(demoHit({ kind: null, period: null, title: 'DemoCo filing' }))).toBe(
      'DemoCo filing',
    );
  });
});
