import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { StocksView } from '@/components/StocksView';
import { installFakeApi, STOCKS } from '../helpers/fakeApi';

// The real router object is stable between renders, so the mock's must be too.
const nav = vi.hoisted(() => {
  const replace = vi.fn();
  return { replace, router: { replace } };
});
vi.mock('next/navigation', () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(''),
}));

const followButton = (symbol: string): HTMLElement =>
  screen.getByRole('button', { name: `Follow ${symbol}` });
const unfollowButton = (symbol: string): HTMLElement =>
  screen.getByRole('button', { name: `Unfollow ${symbol}` });

describe('the stocks page', () => {
  it('shows one card per stock with its name, symbol, BSE code and sector', async () => {
    installFakeApi();
    render(<StocksView />);

    for (const stock of STOCKS) {
      expect(await screen.findByRole('heading', { name: stock.name })).toBeInTheDocument();
      expect(screen.getAllByText(stock.symbol).length).toBeGreaterThan(0);
      expect(screen.getByText(new RegExp(stock.bse_code))).toBeInTheDocument();
      expect(screen.getByText(stock.sector)).toBeInTheDocument();
    }
    expect(screen.getAllByRole('article')).toHaveLength(STOCKS.length);
  });

  it('keeps the order the server gave', async () => {
    installFakeApi();
    render(<StocksView />);

    const headings = await screen.findAllByRole('heading', { level: 2 });
    expect(headings.map((h) => h.textContent)).toEqual(STOCKS.map((s) => s.name));
  });

  it('shows who is signed in and the not-advice notice', async () => {
    installFakeApi({ email: 'ada@example.test' });
    render(<StocksView />);

    expect(await screen.findByText('ada@example.test')).toBeInTheDocument();
    expect(screen.getByText(/not investment advice/i)).toBeInTheDocument();
  });

  it('renders stock text as text, never as HTML', async () => {
    installFakeApi({
      stocks: [{ symbol: 'DEMOA', name: '<img src=x onerror=alert(1)>', bse_code: '000001', sector: 'S' }],
    });
    const { container } = render(<StocksView />);

    await screen.findByRole('heading', { name: '<img src=x onerror=alert(1)>' });
    expect(container.querySelector('img')).toBeNull();
  });
});

describe('following', () => {
  it('follows with one PUT and then shows the stock as followed', async () => {
    const api = installFakeApi();
    render(<StocksView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Follow DEMOB' }));

    expect(await screen.findByRole('button', { name: 'Unfollow DEMOB' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(api.requests.filter((r) => r === 'PUT /api/v1/stocks/DEMOB/follow')).toHaveLength(1);
    expect([...api.follows]).toEqual(['DEMOB']);
    // The others are untouched.
    expect(followButton('DEMOA')).toHaveAttribute('aria-pressed', 'false');
  });

  it('unfollows with DELETE', async () => {
    const api = installFakeApi({ followed: ['DEMOB'] });
    render(<StocksView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Unfollow DEMOB' }));

    expect(await screen.findByRole('button', { name: 'Follow DEMOB' })).toBeInTheDocument();
    expect(api.requests).toContain('DELETE /api/v1/stocks/DEMOB/follow');
    expect(api.follows.size).toBe(0);
  });

  it('shows stocks the user already follows as followed', async () => {
    installFakeApi({ followed: ['DEMOA', 'DEMOC'] });
    render(<StocksView />);

    expect(await screen.findByRole('button', { name: 'Unfollow DEMOA' })).toBeInTheDocument();
    expect(unfollowButton('DEMOC')).toBeInTheDocument();
    expect(followButton('DEMOB')).toBeInTheDocument();
  });

  it('disables the button while the request is in flight, so a double click sends one PUT', async () => {
    const api = installFakeApi();
    const release = api.hold('PUT /api/v1/stocks/DEMOA/follow');
    render(<StocksView />);

    const button = await screen.findByRole('button', { name: 'Follow DEMOA' });
    await userEvent.click(button);
    expect(button).toBeDisabled();
    await userEvent.click(button);
    release();

    expect(await screen.findByRole('button', { name: 'Unfollow DEMOA' })).toBeEnabled();
    expect(api.requests.filter((r) => r.startsWith('PUT '))).toHaveLength(1);
  });

  it('survives a refresh: a second render still shows the follow, because the server kept it', async () => {
    installFakeApi();
    const first = render(<StocksView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Follow DEMOA' }));
    await screen.findByRole('button', { name: 'Unfollow DEMOA' });
    first.unmount();

    render(<StocksView />);
    expect(await screen.findByRole('button', { name: 'Unfollow DEMOA' })).toBeInTheDocument();
  });

  it('says so when the update fails, and leaves the button as it was', async () => {
    const api = installFakeApi();
    api.failWith('PUT /api/v1/stocks/DEMOA/follow', 500);
    render(<StocksView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Follow DEMOA' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn.t update/i);
    expect(followButton('DEMOA')).toBeEnabled();
    expect(followButton('DEMOA')).toHaveAttribute('aria-pressed', 'false');
  });
});

describe('signing out and losing the session', () => {
  it('sends a signed-out visitor to the sign-in page without ever asking for stocks', async () => {
    const api = installFakeApi({ signedIn: false });
    render(<StocksView />);

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
    expect(api.requests).not.toContain('GET /api/v1/stocks');
  });

  it('goes back to the sign-in page when the session ends while the page is open', async () => {
    const api = installFakeApi();
    render(<StocksView />);
    const button = await screen.findByRole('button', { name: 'Follow DEMOA' });
    api.expireSession();
    await userEvent.click(button);

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });

  it('signs out with a POST and then returns to the sign-in page', async () => {
    const api = installFakeApi();
    render(<StocksView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }));

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
    expect(api.requests).toContain('POST /api/v1/auth/logout');
  });

  it('stays put and says so when signing out fails', async () => {
    const api = installFakeApi();
    api.failWith('POST /api/v1/auth/logout', 500);
    render(<StocksView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn.t sign you out/i);
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('shows an error, not the sign-in page, when the stock list cannot be loaded', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/stocks', 500);
    render(<StocksView />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn.t load/i);
    expect(nav.replace).not.toHaveBeenCalled();
  });
});
