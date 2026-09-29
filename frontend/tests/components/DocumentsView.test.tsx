import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DocumentsView, REFRESH_MS } from '@/components/DocumentsView';
import {
  demoDocument,
  demoFeedItem,
  FEED_ATTRIBUTION,
  installFakeApi,
  STOCKS,
} from '../helpers/fakeApi';

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
    expect(within(main).getByRole('link', { name: 'Chat' })).toHaveAttribute('href', '/chat/');
    expect(within(main).getByRole('link', { name: 'Documents' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('moves between the tabs with the arrow keys, Home and End', async () => {
    installFakeApi({ documents: ALPHA_FILINGS });
    render(<DocumentsView />);

    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((t) => t.getAttribute('tabindex'))).toEqual(['0', '-1', '-1', '-1']);
    tabs[0]?.focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(tabs[1]).toHaveAttribute('aria-selected', 'true');
    expect(tabs[1]).toHaveFocus();
    await userEvent.keyboard('{End}');
    expect(tabs[3]).toHaveAttribute('aria-selected', 'true');
    expect(tabs[3]).toHaveFocus();
    await userEvent.keyboard('{ArrowRight}'); // wraps round to the first
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    await userEvent.keyboard('{ArrowLeft}'); // and back to the last
    expect(tabs[3]).toHaveAttribute('aria-selected', 'true');
    await userEvent.keyboard('{Home}');
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
  });

  it('shows a status pill on every document, including finished ones', async () => {
    installFakeApi({
      documents: [
        demoDocument({ id: 1, status: 'completed', period: 'Jul 2026' }),
        demoDocument({ id: 2, status: 'pending', period: 'Apr 2026' }),
      ],
    });
    render(<DocumentsView />);

    expect(await screen.findByText('Ready')).toHaveClass('pill', 'completed');
    expect(screen.getByText('Waiting')).toHaveClass('pill', 'pending');
  });

  it('has one tab per stock, with how many documents it has, the first one open', async () => {
    installFakeApi({ documents: ALPHA_FILINGS });
    render(<DocumentsView />);

    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual([
      `${STOCKS[0]?.name}4`,
      `${STOCKS[1]?.name}0`,
      `${STOCKS[2]?.name}0`,
      'RBI press releases',
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

  it('links the open stock to its key facts page', async () => {
    installFakeApi();
    render(<DocumentsView />);

    const link = await screen.findByRole('link', { name: 'Key facts for DEMOA' });
    expect(link).toHaveTextContent('Key facts');
    expect(link).toHaveAttribute('href', '/stock/?symbol=DEMOA');

    await userEvent.click(screen.getByRole('tab', { name: new RegExp(STOCKS[1]?.name ?? '') }));
    expect(screen.getByRole('link', { name: 'Key facts for DEMOB' })).toHaveAttribute(
      'href',
      '/stock/?symbol=DEMOB',
    );
  });

  it('says 1 page, not 1 pages', async () => {
    installFakeApi({ documents: ALPHA_FILINGS });
    render(<DocumentsView />);
    expect(await screen.findByText('1 page')).toBeInTheDocument();
  });

  it('offers a search of the open stock’s filings, and a fresh one after switching', async () => {
    const beta = demoDocument({ id: 9, symbol: 'DEMOB', period: 'Jan 2026' });
    installFakeApi({ documents: [...ALPHA_FILINGS, beta] });
    render(<DocumentsView />);

    const alpha = await screen.findByRole('searchbox', {
      name: `Search ${STOCKS[0]?.name}’s filings`,
    });
    await userEvent.type(alpha, 'attrition');
    await userEvent.click(screen.getByRole('tab', { name: new RegExp(STOCKS[1]?.name ?? '') }));

    const second = screen.getByRole('searchbox', { name: `Search ${STOCKS[1]?.name}’s filings` });
    expect(second).toHaveValue('');
  });

  it('offers no search for a stock with no documents yet', async () => {
    installFakeApi();
    render(<DocumentsView />);
    await screen.findByText(/no documents yet/i);
    expect(screen.queryByRole('searchbox', { name: /filings/ })).toBeNull(); // the top bar's box finds stocks
  });

  it('switches to another stock', async () => {
    const beta = demoDocument({ id: 9, symbol: 'DEMOB', period: 'Jan 2026' });
    installFakeApi({ documents: [...ALPHA_FILINGS, beta] });
    render(<DocumentsView />);

    await userEvent.click(
      await screen.findByRole('tab', { name: new RegExp(STOCKS[1]?.name ?? '') }),
    );

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
        demoDocument({
          id: 2,
          status: 'failed',
          period: 'Apr 2026',
          failure_reason: 'No text found.',
        }),
      ],
    });
    render(<DocumentsView />);

    expect(await screen.findByText('Processing')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
    expect(screen.getByText('No text found.')).toBeInTheDocument();
  });

  it('shows a document with no kind or address under its title, as plain text', async () => {
    installFakeApi({
      documents: [
        demoDocument({ title: 'DemoCo filing', kind: null, period: null, source_url: null }),
      ],
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

  describe('checking for new filings', () => {
    const CHECK = 'POST /api/v1/stocks/DEMOA/filings/check';

    it('offers the check for a stock never checked', async () => {
      installFakeApi();
      render(<DocumentsView />);

      expect(await screen.findByText('Not checked yet')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Check for new filings' })).toBeEnabled();
    });

    it('says when the stock was last checked', async () => {
      installFakeApi({ checks: { DEMOA: { last_checked_at: '2026-09-23T08:35:00+00:00' } } });
      render(<DocumentsView />);
      expect(await screen.findByText(/^Last checked 14:05 \(/)).toBeInTheDocument();
    });

    it('starts a check, then shows it running', async () => {
      const api = installFakeApi();
      render(<DocumentsView />);

      await userEvent.click(await screen.findByRole('button', { name: 'Check for new filings' }));

      expect(await screen.findByText('Checking for new filings…')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Checking…' })).toBeDisabled();
      expect(api.requests).toContain(CHECK);
    });

    it('keeps the button off within the hour and says when it works again', async () => {
      const next = new Date(Date.now() + 30 * 60_000).toISOString();
      installFakeApi({ checks: { DEMOA: { next_check_at: next } } });
      render(<DocumentsView />);

      expect(await screen.findByRole('button', { name: 'Check for new filings' })).toBeDisabled();
      expect(screen.getByText(/^Available at \d\d:\d\d$/)).toBeInTheDocument();
    });

    it('turns the button back on once the hour has passed', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const next = new Date(Date.now() + 60_000).toISOString();
      const api = installFakeApi({ checks: { DEMOA: { next_check_at: next } } });
      render(<DocumentsView />);
      expect(await screen.findByRole('button', { name: 'Check for new filings' })).toBeDisabled();

      api.setCheck('DEMOA', { next_check_at: null });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(61_000);
      });

      await vi.waitFor(() =>
        expect(screen.getByRole('button', { name: 'Check for new filings' })).toBeEnabled(),
      );
    });

    it('refreshes while a check runs, then stops', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const api = installFakeApi({ checks: { DEMOA: { checking: true } } });
      render(<DocumentsView />);
      await screen.findByText('Checking for new filings…');

      api.setCheck('DEMOA', { checking: false, last_checked_at: new Date().toISOString() });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(REFRESH_MS);
      });
      await vi.waitFor(() => expect(screen.queryByText('Checking for new filings…')).toBeNull());

      const settled = api.requests.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(REFRESH_MS * 3);
      });
      expect(api.requests.length).toBe(settled);
    });

    it('treats "checked less than an hour ago" as no error, just a fresh status', async () => {
      const api = installFakeApi();
      render(<DocumentsView />);
      const button = await screen.findByRole('button', { name: 'Check for new filings' });

      // Someone else checked this stock a moment ago.
      api.setCheck('DEMOA', { next_check_at: new Date(Date.now() + 3_600_000).toISOString() });
      await userEvent.click(button);

      await vi.waitFor(() =>
        expect(screen.getByRole('button', { name: 'Check for new filings' })).toBeDisabled(),
      );
      expect(screen.queryByRole('alert')).toBeNull();
    });

    it('says so when the check could not be started', async () => {
      const api = installFakeApi();
      api.failWith(CHECK, 500);
      render(<DocumentsView />);

      await userEvent.click(await screen.findByRole('button', { name: 'Check for new filings' }));

      expect(await screen.findByRole('alert')).toHaveTextContent(/couldn't start the check/i);
    });

    it('offers no button when automatic filings are switched off', async () => {
      installFakeApi({ checks: { DEMOA: { enabled: false } } });
      render(<DocumentsView />);

      expect(await screen.findByText(/switched off/i)).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /check for new filings/i })).toBeNull();
    });
  });

  it('goes back to sign-in when the session has ended', async () => {
    const api = installFakeApi();
    api.expireSession();
    render(<DocumentsView />);
    await vi.waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });
});

describe('the RBI press releases tab', () => {
  const openTab = async (): Promise<void> => {
    await userEvent.click(await screen.findByRole('tab', { name: /RBI press releases/ }));
  };

  it('comes after the stock tabs', async () => {
    installFakeApi();
    render(<DocumentsView />);
    const tabs = await screen.findAllByRole('tab');
    expect(tabs).toHaveLength(4);
    expect(tabs[3]).toHaveTextContent('RBI press releases');
    expect(tabs[3]).toHaveAttribute('aria-selected', 'false');
  });

  it('shows each item with title link, date, summary, and the attribution', async () => {
    installFakeApi({ feed: [demoFeedItem()] });
    render(<DocumentsView />);
    await openTab();

    const panel = screen.getByRole('tabpanel');
    expect(panel).toHaveAccessibleName('RBI press releases');
    const link = within(panel).getByRole('link', {
      name: 'Sample policy statement on the demo repo rate',
    });
    expect(link).toHaveAttribute('href', demoFeedItem().url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(within(panel).getByText('12 Sep 2026')).toBeInTheDocument();
    expect(within(panel).getByText(demoFeedItem().summary)).toBeInTheDocument();
    expect(within(panel).getByText(FEED_ATTRIBUTION)).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /RBI press releases/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('marks a fixture as a sample and does not link it', async () => {
    installFakeApi({
      feed: [demoFeedItem({ is_fixture: true, url: null, title: 'Fixture release' })],
    });
    render(<DocumentsView />);
    await openTab();

    const panel = screen.getByRole('tabpanel');
    expect(within(panel).getByText('Sample item (offline fixture)')).toBeInTheDocument();
    expect(within(panel).getByText('Fixture release')).toBeInTheDocument();
    expect(within(panel).queryByRole('link')).toBeNull();
  });

  it('shows a fixture as plain text even if it carries an allowed address', async () => {
    installFakeApi({ feed: [demoFeedItem({ is_fixture: true, title: 'Fixture with url' })] });
    render(<DocumentsView />);
    await openTab();
    expect(within(screen.getByRole('tabpanel')).queryByRole('link')).toBeNull();
  });

  it('shows a disallowed address as plain text', async () => {
    installFakeApi({
      feed: [demoFeedItem({ title: 'Odd release', url: 'https://rbi.org.in.evil.example/x' })],
    });
    render(<DocumentsView />);
    await openTab();

    const panel = screen.getByRole('tabpanel');
    expect(within(panel).getByText('Odd release')).toBeInTheDocument();
    expect(within(panel).queryByRole('link')).toBeNull();
  });

  it('copes with an item that has no date', async () => {
    installFakeApi({ feed: [demoFeedItem({ published_at: null })] });
    render(<DocumentsView />);
    await openTab();
    expect(within(screen.getByRole('tabpanel')).getByText('Date not given')).toBeInTheDocument();
  });

  it('says so when there are no press releases yet', async () => {
    installFakeApi({ feed: [] });
    render(<DocumentsView />);
    await openTab();
    expect(
      screen.getByText("No press releases yet. The worker checks RBI's feed every hour."),
    ).toBeInTheDocument();
  });

  it('shows a short message when the feed cannot be loaded, keeping the stock tabs', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/feed', 500);
    render(<DocumentsView />);
    await openTab();
    expect(await screen.findByRole('alert')).toHaveTextContent(/press releases/i);
    expect(screen.getAllByRole('tab')).toHaveLength(4);
  });

  it('does not stop the stock tabs from loading when the feed fails', async () => {
    const api = installFakeApi({ documents: ALPHA_FILINGS });
    api.failWith('GET /api/v1/feed', 500);
    render(<DocumentsView />);
    expect(await screen.findByRole('link', { name: 'Jul 2026' })).toBeInTheDocument();
  });
});
