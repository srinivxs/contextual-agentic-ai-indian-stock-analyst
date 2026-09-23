import { act, render, screen, within } from '@testing-library/react';
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

const section = (name: string): HTMLElement =>
  screen.getByRole('region', { name: new RegExp(name) });

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

  it('has one section per stock, in the server’s order', async () => {
    installFakeApi();
    render(<DocumentsView />);

    const headings = await screen.findAllByRole('heading', { level: 2 });
    expect(headings.map((h) => h.textContent)).toEqual(STOCKS.map((s) => s.name));
  });

  it('lists each document with its status and a link to the official filing', async () => {
    const filing = demoDocument({ title: 'DEMOA earnings call transcript, Jul 2026' });
    installFakeApi({ documents: [filing] });
    render(<DocumentsView />);

    await screen.findByText('DEMOA earnings call transcript, Jul 2026');
    const alpha = section('DemoCo Alpha');
    expect(within(alpha).getByText('Ready')).toBeInTheDocument();
    const link = within(alpha).getByRole('link', { name: /official filing/i });
    expect(link).toHaveAttribute('href', filing.source_url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('says so when a stock has no documents yet', async () => {
    installFakeApi();
    render(<DocumentsView />);
    expect(await screen.findAllByText(/no documents yet/i)).toHaveLength(STOCKS.length);
  });

  it('shows why a document failed', async () => {
    installFakeApi({
      documents: [demoDocument({ status: 'failed', failure_reason: 'No text found in this PDF.' })],
    });
    render(<DocumentsView />);

    expect(await screen.findByText('Failed')).toBeInTheDocument();
    expect(screen.getByText('No text found in this PDF.')).toBeInTheDocument();
  });

  it('has no link for an upload, which has no public address', async () => {
    installFakeApi({
      documents: [demoDocument({ title: 'Uploaded', source: 'upload', source_url: null })],
    });
    render(<DocumentsView />);

    await screen.findByText('Uploaded');
    expect(screen.queryByRole('link', { name: /official filing/i })).toBeNull();
  });

  it('renders titles as text, never as HTML', async () => {
    installFakeApi({ documents: [demoDocument({ title: '<img src=x onerror=alert(1)>' })] });
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
    expect(await screen.findByText('Ready')).toBeInTheDocument();

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
