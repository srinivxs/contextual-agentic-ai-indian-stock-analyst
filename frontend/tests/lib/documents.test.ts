import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import { isStillWorking, listDocuments, officialLink } from '@/lib/documents';
import { demoDocument, installFakeApi } from '../helpers/fakeApi';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('listing a stock’s documents', () => {
  it('returns the documents the server gave, in its order', async () => {
    const first = demoDocument({ id: 2, title: 'newer' });
    const second = demoDocument({ id: 1, title: 'older' });
    installFakeApi({ documents: [first, second] });

    expect(await listDocuments('DEMOA')).toEqual([first, second]);
  });

  it('encodes the symbol so it can never add path segments', async () => {
    const api = installFakeApi();
    await expect(listDocuments('A/../B')).rejects.toBeInstanceOf(ApiError);
    expect(api.requests).toContain('GET /api/v1/stocks/A%2F..%2FB/documents');
  });

  it('refuses a response that is not the agreed shape', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ items: [{ id: 'x' }] }), { status: 200 })),
    );
    await expect(listDocuments('DEMOA')).rejects.toMatchObject({ code: 'unexpected_response' });
  });
});

describe('the link to the official filing', () => {
  it('is the BSE address of a fetched filing', () => {
    const document = demoDocument();
    expect(officialLink(document)).toBe(document.source_url);
  });

  it('does not exist for an upload', () => {
    expect(officialLink(demoDocument({ source: 'upload', source_url: null }))).toBeNull();
  });

  it.each([
    'javascript:alert(1)',
    'http://www.bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname=0000.pdf',
    'https://www.bseindia.com.evil.example/x.pdf',
    'https://evil.example/x.pdf',
    '//www.bseindia.com/x.pdf',
  ])('is withheld for anything but an https BSE address: %s', (url) => {
    expect(officialLink(demoDocument({ source_url: url }))).toBeNull();
  });
});

describe('whether anything is still being worked on', () => {
  it('is true while a document is pending or processing', () => {
    expect(isStillWorking([demoDocument({ status: 'processing' })])).toBe(true);
    expect(isStillWorking([demoDocument(), demoDocument({ status: 'pending' })])).toBe(true);
  });

  it('is false once everything has finished, one way or the other', () => {
    expect(isStillWorking([demoDocument(), demoDocument({ status: 'failed' })])).toBe(false);
    expect(isStillWorking([])).toBe(false);
  });
});
