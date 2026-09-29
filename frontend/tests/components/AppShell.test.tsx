import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppShell, initials } from '@/components/AppShell';
import { DATA_POLL_MS } from '@/components/DataFreshness';
import { Monogram, monogramLetters, monogramTint } from '@/components/Monogram';
import { findStocks, stockHref } from '@/components/StockJump';

import { installFakeApi } from '../helpers/fakeApi';

describe('the app shell', () => {
  it('lists the sections and marks the current one', () => {
    render(
      <AppShell email="reader@example.test" onSignOut={() => {}} active="chat">
        <p>content</p>
      </AppShell>,
    );
    const menu = screen.getByRole('navigation', { name: 'Main' });
    const links = Array.from(menu.querySelectorAll('a')).map((a) => a.textContent);
    expect(links).toEqual(['Home', 'Chat', 'Stocks', 'News', 'Documents', 'Match']);
    expect(screen.getByRole('link', { name: 'Chat' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Home' })).not.toHaveAttribute('aria-current');
    expect(screen.getByText('content')).toBeInTheDocument();
    expect(screen.getByText(/Not investment advice/)).toBeInTheDocument();
  });

  it('shows who is signed in and signs out', async () => {
    const onSignOut = vi.fn();
    render(
      <AppShell email="reader@example.test" onSignOut={onSignOut}>
        <p>content</p>
      </AppShell>,
    );
    expect(screen.getByText('reader@example.test')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Dark mode' })).toBeInTheDocument();
    expect(screen.getByText('RE')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(onSignOut).toHaveBeenCalledOnce();
  });

  it('makes two letters for the avatar', () => {
    expect(initials('info@example.com')).toBe('IN');
    expect(initials('a.b@example.com')).toBe('AB');
    expect(initials('@example.com')).toBe('?');
  });
});

describe('finding a stock', () => {
  it('matches a ticker or any part of the name, every word', () => {
    expect(findStocks('tcs').map((s) => s.symbol)).toEqual(['TCS']);
    expect(findStocks('HDFC').map((s) => s.symbol)).toEqual(['HDFCBANK']);
    expect(findStocks('tata consult').map((s) => s.symbol)).toEqual(['TCS']);
    expect(findStocks('  ')).toEqual([]);
    expect(findStocks('infosys')).toEqual([]);
  });

  it('links to the stock page', () => {
    expect(stockHref('HDFCBANK')).toBe('/stock/?symbol=HDFCBANK');
  });

  it('suggests matching stocks as you type and says when there are none', async () => {
    render(
      <AppShell email="reader@example.test" onSignOut={() => {}}>
        <p>content</p>
      </AppShell>,
    );
    const box = screen.getByRole('searchbox', { name: 'Find a stock' });
    await userEvent.type(box, 'rel');
    expect(screen.getByRole('link', { name: /RELIANCE/ })).toHaveAttribute(
      'href',
      '/stock/?symbol=RELIANCE',
    );
    await userEvent.clear(box);
    await userEvent.type(box, 'infy');
    expect(screen.getByText(/follows RELIANCE, TCS and HDFC Bank only/)).toBeInTheDocument();
  });

  it('opens the first match on Enter and does nothing without one', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });
    render(
      <AppShell email="reader@example.test" onSignOut={() => {}}>
        <p>content</p>
      </AppShell>,
    );
    const box = screen.getByRole('searchbox', { name: 'Find a stock' });
    await userEvent.type(box, 'zzz{Enter}');
    expect(assign).not.toHaveBeenCalled();
    await userEvent.clear(box);
    await userEvent.type(box, 'tcs{Enter}');
    expect(assign).toHaveBeenCalledWith('/stock/?symbol=TCS');
    vi.unstubAllGlobals();
  });

  it('jumps to the box on "/" unless you are typing elsewhere', async () => {
    render(
      <AppShell email="reader@example.test" onSignOut={() => {}}>
        <input aria-label="other" />
      </AppShell>,
    );
    const box = screen.getByRole('searchbox', { name: 'Find a stock' });
    await userEvent.keyboard('/');
    expect(box).toHaveFocus();
    const other = screen.getByRole('textbox', { name: 'other' });
    await userEvent.click(other);
    await userEvent.keyboard('/');
    expect(other).toHaveFocus();
    expect(other).toHaveValue('/');
  });
});

describe('the stock monogram', () => {
  it('shows two letters in a tint that depends only on the symbol', () => {
    expect(monogramLetters('HDFCBANK')).toBe('HD');
    expect(monogramLetters('M&M')).toBe('MM');
    expect(monogramLetters('&')).toBe('?');
    expect(monogramTint('DEMO')).toBe(monogramTint('DEMO'));
    render(<Monogram symbol="DEMO" size="lg" />);
    expect(screen.getByText('DE')).toHaveClass('monogram', 'lg');
  });

  it.each([
    ['TCS', '/logos/TCS.png'],
    ['RELIANCE', '/logos/RELIANCE.png'],
    ['HDFCBANK', '/logos/HDFCBANK.png'],
  ])('shows the owner-supplied logo for %s instead of letters', (symbol, src) => {
    const { container } = render(<Monogram symbol={symbol} size="md" />);
    const mark = container.querySelector('.monogram');
    expect(mark).toHaveClass('monogram', 'md', 'has-logo');
    const logo = mark?.querySelector('img');
    expect(logo).toHaveAttribute('src', src);
    expect(logo).toHaveAttribute('alt', ''); // decorative: the name is always written next to it
    expect(mark).not.toHaveTextContent(/\w/);
  });
});

describe('how fresh the data is, on every page', () => {
  const shell = () =>
    render(
      <AppShell email="reader@example.test" onSignOut={() => {}}>
        <p>content</p>
      </AppShell>,
    );
  const note = () => screen.findByRole('note', { name: 'Data freshness' });

  it('says the date the data is updated to, source by source, and that it is not live', async () => {
    installFakeApi({
      dataStatus: {
        filings_checked_at: '2026-09-29T09:30:00+00:00',
        prices_to: '2026-09-28',
        rbi_to: '2026-09-29T06:00:00+00:00',
      },
    });
    shell();
    expect(await note()).toHaveTextContent(
      'Data updated to 29 Sep 2026: filings checked 29 Sep 2026, share prices to the 28 Sep ' +
        '2026 close, RBI releases to 29 Sep 2026. Not live data.',
    );
  });

  it('puts Update data right of the stock search and left of the light and dark switch', async () => {
    installFakeApi();
    shell();
    const search = screen.getByRole('search');
    const update = await screen.findByRole('button', { name: 'Update data' });
    const theme = screen.getByRole('switch', { name: 'Dark mode' });
    expect(search.compareDocumentPosition(update) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(update.compareDocumentPosition(theme) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('updates filings, prices and RBI releases in one click, and says so', async () => {
    const api = installFakeApi();
    shell();
    await userEvent.click(await screen.findByRole('button', { name: 'Update data' }));
    expect(await screen.findByText(/New data appears within a few minutes/)).toHaveTextContent(
      'Updating filings, share prices and RBI releases. New data appears within a few minutes.',
    );
    expect(api.requests).toContain('POST /api/v1/data/refresh');
    expect(await note()).toHaveTextContent(/^Updating…/);
  });

  it('keeps asking while an update runs, then stops', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = installFakeApi({ dataStatus: { updating: true } });
    shell();
    expect(await note()).toHaveTextContent(/^Updating…/);

    api.setDataStatus({ updating: false, prices_to: '2026-09-29' });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DATA_POLL_MS);
    });
    await vi.waitFor(() => expect(screen.getByRole('note')).toHaveTextContent(/^Data updated/));

    const settled = api.requests.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DATA_POLL_MS * 3);
    });
    expect(api.requests.length).toBe(settled);
    vi.useRealTimers();
  });

  it('says so when an update could not be started, and shows nothing it could not read', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/data/status', 500);
    api.failWith('POST /api/v1/data/refresh', 500);
    shell();
    await userEvent.click(await screen.findByRole('button', { name: 'Update data' }));
    expect(
      await screen.findByText("We couldn't start the update. Try again in a moment."),
    ).toBeInTheDocument();
    expect(screen.queryByRole('note', { name: 'Data freshness' })).toBeNull();
  });
});
