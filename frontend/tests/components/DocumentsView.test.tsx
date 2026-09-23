import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DocumentsView, REFRESH_MS } from '@/components/DocumentsView';
import { demoDocument, installFakeApi, STOCKS } from '../helpers/fakeApi';

const nav = vi.hoisted(() => {
  const replace = vi.fn();
  return { replace, router: { replace } };
});
vi.mock('next/navigation', () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(''),
}));

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  nav.replace.mockReset();
});

const ALPHA_FILINGS = [
  demoDocument({ id: 1, kind: 'transcript', period: 'Oct 2025', page_count: 22 }),
  demoDocument({ id: 2, kind: 'transcript', period: 'Jul 2026', page_count: 25 }),
  demoDocument({ id: 3, kind: 'annual_report', period: 'Annual Report 2025', page_count: 1 }),
  demoDocument({ id: 4, kind: 'announcement', period: 'Board meeting on results' }),
];

describe('the documents page', () => {
  it('can be reached from, and leads back to, the stocks page', async () => {
    installFakeApi();
    render(<DocumentsView />);

    const main = await screen.findByRole('navigation', { name: 'Main' });
    expect(within(main).getByRole('link', { name: 'Stocks' })).toHaveAttribute('href', '/stocks/');
    expect(within(main).getByRole('link', { name: 'Documents' })).toHaveAttribute(
      'href',
      '/documents/',
    );
  });

  it('has one tab per stock, with how many documents it has, the first one open', async () => {
    installFakeApi({ documents: ALPHA_FILINGS });
    render(<DocumentsView />);

    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual([
      `${STOCKS[0]?.name}4`,
      `${STOCKS[1]?.name}0`,
      `${STOCKS[2]?.name}0`,
    ]);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName(STOCKS[0]?.name ?? '');
  });

  it('groups a stock’s documents by kind, newest first, with a summary', async () => {
    installFakeApi({ documents: ALPHA_FILINGS });
    render(<DocumentsView />);

    const calls = await screen.findByRole('region', { name: /Earnings calls/ });
    const periods = within(calls)
      .getAllByRole('link')
      .map((link) => link.textContent);
    expect(periods).toEqual(['Jul 2026', 'Oct 2025']);
    expect(screen.getByRole('region', { name: /Annual reports/ })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /Announcements/ })).toBeInTheDocument();
    expect(screen.getByText('4 documents · 60 pages')).toBeInTheDocument();
  });

  it('opens the official filing in a new tab, safely', async () => {
    installFakeApi({ documents: ALPHA_FILINGS });
    render(<DocumentsView />);

    const link = await screen.findByRole('link', { name: 'Jul 2026' });
    expect(link).toHaveAttribute('href', ALPHA_FILINGS[1]?.source_url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('says 1 page, not 1 pages', async () => {
    installFakeApi({ documents: ALPHA_FILINGS });
    render(<DocumentsView />);
    expect(await screen.findByText('1 page')).toBeInTheDocument();
  });

  it('switches to another stock', async () => {
    const beta = demoDocument({ id: 9, symbol: 'DEMOB', period: 'Jan 2026' });
    installFakeApi({ documents: [...ALPHA_FILINGS, beta] });
    render(<DocumentsView />);

    await userEvent.click(await screen.findByRole('tab', { name: new RegExp(STOCKS[1]?.name ?? '') }));

    expect(screen.getByRole('tabpanel')).toHaveAccessibleName(STOCKS[1]?.name ?? '');
    expect(screen.getByRole('link', { name: 'Jan 2026' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Jul 2026' })).toBeNull();
  });

  it('says so when a stock has no documents yet', async () => {
    installFakeApi();
    render(<DocumentsView />);
    expect(await screen.findByText(/no documents yet/i)).toBeInTheDocument();
  });

  it('shows a document that is still being processed, and why one failed', async () => {
    installFakeApi({
      documents: [
        demoDocument({ id: 1, status: 'processing', period: 'Jul 2026' }),
        demoDocument({ id: 2, status: 'failed', period: 'Apr 2026', failure_reason: 'No text found.' }),
      ],
    });
    render(<DocumentsView />);

    expect(await screen.findByText('Processing')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
    expect(screen.getByText('No text found.')).toBeInTheDocument();
  });

  it('shows a document with no kind or address under its title, as plain text', async () => {
    installFakeApi({
      documents: [demoDocument({ title: 'DemoCo filing', kind: null, period: null, source_url: null })],
    });
    render(<DocumentsView />);

    expect(await screen.findByText('DemoCo filing')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /Other documents/ })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'DemoCo filing' })).toBeNull();
  });

  it('renders text as text, never as HTML', async () => {
    installFakeApi({
      documents: [demoDocument({ kind: 'announcement', period: '<img src=x onerror=alert(1)>' })],
    });
    const { container } = render(<DocumentsView />);

    await screen.findByText('<img src=x onerror=alert(1)>');
    expect(container.querySelector('img')).toBeNull();
  });

  it('keeps refreshing while something is being processed, then stops', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = installFakeApi({ documents: [demoDocument({ status: 'processing' })] });
    render(<DocumentsView />);
    await screen.findByText('Processing');

    api.setDocuments([demoDocument({ status: 'completed' })]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REFRESH_MS);
    });
    await vi.waitFor(() => expect(screen.queryByText('Processing')).toBeNull());

    const settled = api.requests.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REFRESH_MS * 3);
    });
    expect(api.requests.length).toBe(settled); // nothing left to wait for: no more requests
  });

  it('goes back to sign-in when the session has ended', async () => {
    const api = installFakeApi();
    api.expireSession();
    render(<DocumentsView />);
    await vi.waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });
});
