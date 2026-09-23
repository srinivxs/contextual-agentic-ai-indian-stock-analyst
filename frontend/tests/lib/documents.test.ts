import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import {
  groupDocuments,
  isStillWorking,
  listAllDocuments,
  officialLink,
  pagesLabel,
} from '@/lib/documents';
import { demoDocument, installFakeApi } from '../helpers/fakeApi';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('listing a stock’s documents', () => {
  it('returns every document, following the cursor across pages', async () => {
    const documents = Array.from({ length: 7 }, (_, i) => demoDocument({ id: i + 1 }));
    const api = installFakeApi({ documents, pageSize: 3 });

    const all = await listAllDocuments('DEMOA');

    expect(all.map((d) => d.id)).toEqual([7, 6, 5, 4, 3, 2, 1]);
    expect(api.requests).toEqual([
      'GET /api/v1/stocks/DEMOA/documents?limit=100',
      'GET /api/v1/stocks/DEMOA/documents?limit=100&cursor=5',
      'GET /api/v1/stocks/DEMOA/documents?limit=100&cursor=2',
    ]);
  });

  it('encodes the symbol so it can never add path segments', async () => {
    const api = installFakeApi();
    await expect(listAllDocuments('A/../B')).rejects.toBeInstanceOf(ApiError);
    expect(api.requests).toContain('GET /api/v1/stocks/A%2F..%2FB/documents?limit=100');
  });

  it('refuses a response that is not the agreed shape', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ items: [{ id: 'x' }] }), { status: 200 })),
    );
    await expect(listAllDocuments('DEMOA')).rejects.toMatchObject({ code: 'unexpected_response' });
  });

  it('stops after a bounded number of pages, whatever the server says', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ items: [demoDocument()], next_cursor: 1 }), {
            status: 200,
          }),
      ),
    );
    const all = await listAllDocuments('DEMOA');
    expect(all.length).toBeLessThanOrEqual(20);
  });
});

describe('grouping for the page', () => {
  it('puts each kind in its own group, in a fixed order, newest first', () => {
    const groups = groupDocuments([
      demoDocument({ id: 1, kind: 'transcript', period: 'Oct 2025' }),
      demoDocument({ id: 2, kind: 'annual_report', period: 'Annual Report 2024' }),
      demoDocument({ id: 3, kind: 'transcript', period: 'Jul 2026' }),
      demoDocument({ id: 4, kind: 'announcement', period: 'Board meeting' }),
      demoDocument({ id: 5, kind: 'annual_report', period: 'Annual Report 2026' }),
      demoDocument({ id: 6, kind: 'presentation', period: 'Jan 2026' }),
      demoDocument({ id: 7, kind: 'transcript', period: 'Jan 2026' }),
      demoDocument({ id: 8, kind: null, period: null, source: 'upload', source_url: null }),
    ]);

    expect(groups.map((g) => [g.title, g.documents.map((d) => d.id)])).toEqual([
      ['Earnings calls', [3, 7, 1]],
      ['Investor presentations', [6]],
      ['Annual reports', [5, 2]],
      ['Announcements', [4]],
      ['Uploaded', [8]],
    ]);
  });

  it('leaves out a group with nothing in it', () => {
    const groups = groupDocuments([demoDocument({ kind: 'transcript' })]);
    expect(groups.map((g) => g.title)).toEqual(['Earnings calls']);
  });
});

describe('small labels', () => {
  it('says page or pages correctly', () => {
    expect(pagesLabel(1)).toBe('1 page');
    expect(pagesLabel(23)).toBe('23 pages');
    expect(pagesLabel(1503)).toBe('1,503 pages');
    expect(pagesLabel(null)).toBe('');
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
