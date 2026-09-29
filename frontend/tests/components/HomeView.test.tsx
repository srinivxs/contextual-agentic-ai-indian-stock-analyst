import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { HomeView } from '@/components/HomeView';
import {
  demoFeedItem,
  demoInsights,
  demoMatches,
  demoPrices,
  demoProfileField,
  demoSeries,
  demoStockMatch,
  installFakeApi,
  STOCKS,
  type Options,
} from '../helpers/fakeApi';

// The real router object is stable between renders, so the mock's must be too.
const nav = vi.hoisted(() => {
  const replace = vi.fn();
  const push = vi.fn();
  return { replace, push, router: { replace, push } };
});
vi.mock('next/navigation', () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(''),
}));

const SERIES = {
  DEMOA: demoSeries('DEMOA', [100, 120]),
  DEMOB: demoSeries('DEMOB', [200, 150]),
  DEMOC: demoSeries('DEMOC', []),
};

const api = (options: Options = {}) => installFakeApi({ series: SERIES, ...options });

const region = (name: string): HTMLElement => screen.getByRole('region', { name });

async function loaded(): Promise<void> {
  await screen.findByRole('img', { name: /^Net profit of DemoCo Alpha Limited/ });
}

describe('the home page: hero', () => {
  it('names the product and the grounding, and marks Home as the current page', async () => {
    api();
    render(<HomeView />);
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Your personal Indian stock analyst' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Grounded in official filings')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Home' })).toHaveAttribute('aria-current', 'page');
  });

  it("charts the first stock's net profit with a callout and a named source", async () => {
    api();
    render(<HomeView />);
    await loaded();

    const callout = screen.getByTestId('chart-callout');
    expect(callout).toHaveTextContent('FY2026');
    expect(callout).toHaveTextContent('₹120 crore');
    expect(callout).toHaveTextContent('+20.0% on FY2025');
    const hero = region('Your personal Indian stock analyst');
    expect(within(hero).getByText(/Net profit, ₹ crore, consolidated/)).toBeInTheDocument();
    expect(within(hero).getByRole('link', { name: 'screener.in' })).toHaveAttribute(
      'href',
      'https://www.screener.in/company/DEMOA/consolidated/',
    );
  });

  it('has real tabs that switch the chart, by click and by arrow key', async () => {
    const user = userEvent.setup();
    api();
    render(<HomeView />);
    await loaded();

    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual(STOCKS.map((s) => s.symbol));
    expect(screen.getByRole('tablist', { name: 'Stock shown in the chart' })).toBeInTheDocument();
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    expect(tabs[1]).toHaveAttribute('aria-selected', 'false');
    expect(tabs[1]).toHaveAttribute('tabindex', '-1');

    await user.click(tabs[1] as HTMLElement);
    expect(
      screen.getByRole('img', {
        name: 'Net profit of DemoCo Beta Limited, ₹ crore, FY2025 to FY2026',
      }),
    ).toBeInTheDocument();
    expect(screen.getByTestId('chart-callout')).toHaveTextContent('-25.0% on FY2025');
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', tabs[1]?.id);

    tabs[1]?.focus();
    await user.keyboard('{ArrowRight}');
    expect(tabs[2]).toHaveAttribute('aria-selected', 'true');
    expect(tabs[2]).toHaveFocus();
    expect(
      screen.getByText(/No net profit figures stored yet for DemoCo Gamma Limited/),
    ).toBeVisible();
    await user.keyboard('{ArrowRight}');
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{ArrowLeft}');
    expect(tabs[2]).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{Home}');
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{End}');
    expect(tabs[2]).toHaveAttribute('aria-selected', 'true');
  });

  it('says so when a stock has no stored figures', async () => {
    api({ series: { ...SERIES, DEMOA: demoSeries('DEMOA', []) } });
    render(<HomeView />);
    expect(
      await screen.findByText(/No net profit figures stored yet for DemoCo Alpha Limited/),
    ).toBeVisible();
  });

  it('shows a short message when the stock series cannot be loaded, and the rest still works', async () => {
    const fake = api();
    fake.failWith('GET /api/v1/stocks/DEMOA/series?metric=net_profit', 500);
    render(<HomeView />);

    expect(await screen.findByText(/couldn't load the net profit figures/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Your personal/);
    // The other stocks' cards still show their figures.
    expect(await screen.findByText(/-25.0%/)).toBeInTheDocument();
  });
});

describe('the home page: stat cards', () => {
  it('shows each stock latest net profit, its change and a link to its page', async () => {
    api();
    render(<HomeView />);
    await loaded();

    const cards = region('At a glance');
    const alpha = within(cards).getByRole('link', { name: /DemoCo Alpha Limited/ });
    expect(alpha).toHaveAttribute('href', '/stock/?symbol=DEMOA');
    expect(alpha).toHaveTextContent('₹120');
    expect(alpha).toHaveTextContent('crore');
    expect(alpha).toHaveTextContent('+20.0%');
    expect(alpha).toHaveTextContent('FY2026');
    const beta = within(cards).getByRole('link', { name: /DemoCo Beta Limited/ });
    expect(beta).toHaveTextContent('-25.0%');
    expect(beta.querySelector('.home-change')).toHaveClass('fall');
    expect(alpha.querySelector('.home-change')).toHaveClass('rise');
    expect(alpha.querySelector('svg.home-spark')).not.toBeNull();
    expect(within(cards).getByRole('link', { name: /DemoCo Gamma Limited/ })).toHaveTextContent(
      'No figures yet',
    );
  });

  it('marks a stock whose series failed, without hiding the others', async () => {
    const fake = api();
    fake.failWith('GET /api/v1/stocks/DEMOB/series?metric=net_profit', 500);
    render(<HomeView />);
    await loaded();
    const cards = region('At a glance');
    expect(
      await within(cards).findByRole('link', { name: /DemoCo Beta Limited/ }),
    ).toHaveTextContent('Unavailable');
    expect(within(cards).getByRole('link', { name: /DemoCo Alpha Limited/ })).toHaveTextContent(
      '+20.0%',
    );
  });
});

describe('the home page: your stocks', () => {
  it('lists only the followed stocks with their match status and latest figure', async () => {
    api({
      followed: ['DEMOB'],
      matches: demoMatches({
        profile_empty: false,
        stocks: [demoStockMatch({ symbol: 'DEMOB', status: 'partial' })],
      }),
    });
    render(<HomeView />);
    await loaded();

    const list = region('Your stocks');
    const row = await within(list).findByRole('link', { name: /DEMOB/ });
    expect(row).toHaveAttribute('href', '/stock/?symbol=DEMOB');
    expect(row).toHaveTextContent('DemoCo Beta Limited');
    expect(row).toHaveTextContent('Partial match');
    expect(row).toHaveTextContent('₹150 crore');
    expect(within(list).queryByText('DEMOA')).toBeNull();
    expect(within(list).getByRole('link', { name: 'Follow stocks' })).toHaveAttribute(
      'href',
      '/stocks/',
    );
  });

  it('shows no match badge while the profile is empty', async () => {
    api({ followed: ['DEMOA'] });
    render(<HomeView />);
    await loaded();
    const row = await within(region('Your stocks')).findByRole('link', { name: /DEMOA/ });
    expect(row).not.toHaveTextContent(/match/i);
  });

  it('invites the user to follow a stock when none is followed', async () => {
    api();
    render(<HomeView />);
    await loaded();
    const list = region('Your stocks');
    expect(await within(list).findByText(/You don't follow any stock yet/)).toBeInTheDocument();
    expect(within(list).getByRole('link', { name: 'Follow stocks' })).toBeInTheDocument();
  });

  it('offers to manage the follows once every stock is followed', async () => {
    api({ followed: STOCKS.map((s) => s.symbol) });
    render(<HomeView />);
    await loaded();
    expect(
      await within(region('Your stocks')).findByRole('link', { name: 'Manage stocks' }),
    ).toHaveAttribute('href', '/stocks/');
  });

  it('shows a short message when the stock list cannot be loaded', async () => {
    const fake = api();
    fake.failWith('GET /api/v1/stocks', 500);
    render(<HomeView />);
    expect(await screen.findByText(/couldn't load your stocks/i)).toBeInTheDocument();
    // The page itself still stands.
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/Your personal/);
  });
});

describe('the home page: session and words', () => {
  it('sends a signed-out visitor to sign in', async () => {
    nav.replace.mockClear();
    api({ signedIn: false });
    render(<HomeView />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });

  it('sends the user to sign in when a call finds the session gone', async () => {
    nav.replace.mockClear();
    const fake = api();
    fake.failWith('GET /api/v1/match', 401);
    render(<HomeView />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });

  it('shows an error when who is signed in cannot be found out', async () => {
    const fake = api();
    fake.failWith('GET /api/v1/me', 500);
    render(<HomeView />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn't load/i);
  });

  it('signs out from the top bar', async () => {
    const user = userEvent.setup();
    nav.replace.mockClear();
    const fake = api();
    render(<HomeView />);
    await loaded();
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
    expect(fake.requests).toContain('POST /api/v1/auth/logout');
  });

  it('shows a message when signing out fails', async () => {
    const user = userEvent.setup();
    const fake = api();
    fake.failWith('POST /api/v1/auth/logout', 500);
    render(<HomeView />);
    await loaded();
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn't sign you out/i);
  });

  it('says it is not investment advice and never uses advice words', async () => {
    api({
      followed: ['DEMOA'],
      profileFields: [demoProfileField()],
      feed: [demoFeedItem()],
      insights: { DEMOA: demoInsights() },
      matches: demoMatches({ profile_empty: false, stocks: [demoStockMatch()] }),
    });
    const { container } = render(<HomeView />);
    await loaded();

    expect(screen.getAllByText(/Not investment advice/).length).toBeGreaterThan(0);
    const words = container.querySelector('main')?.textContent ?? '';
    expect(words).not.toMatch(/\b(buy|sell|recommend\w*|should)\b/i);
  });
});

const PRICES = {
  DEMOA: demoPrices('DEMOA'),
  DEMOB: demoPrices('DEMOB', {
    latest: {
      ...demoPrices('DEMOB').latest!,
      close: '2050.25',
      prev_close: '2062.5',
      change_pct: '-0.6',
    },
  }),
  // DEMOC has no prices yet (the fake's default).
};

const withPrices = (options: Options = {}) => api({ prices: PRICES, ...options });

async function pricesLoaded(): Promise<void> {
  await screen.findByRole('img', { name: /^Share price of DemoCo Alpha Limited/ });
}

describe('the home page: share prices', () => {
  it('opens on the share price chart with its callout, caption and source', async () => {
    withPrices();
    render(<HomeView />);
    await pricesLoaded();

    const callout = screen.getByTestId('chart-callout');
    expect(callout).toHaveTextContent('28 Sep 2026');
    expect(callout).toHaveTextContent('₹101.50');
    expect(callout).toHaveTextContent('+1.5% on the day');
    const hero = region('Your personal Indian stock analyst');
    expect(
      within(hero).getByText(/End-of-day closes, adjusted for bonus issues and splits/),
    ).toBeInTheDocument();
    expect(within(hero).getByRole('link', { name: 'BSE daily price files' })).toHaveAttribute(
      'href',
      expect.stringContaining('https://www.bseindia.com/download/BhavCopy/'),
    );
    expect(screen.queryByRole('img', { name: /^Net profit of/ })).toBeNull();
  });

  it('switches to net profit and back with a labelled toggle group', async () => {
    const user = userEvent.setup();
    withPrices();
    render(<HomeView />);
    await pricesLoaded();

    const group = screen.getByRole('group', { name: 'Chart shows' });
    const price = within(group).getByRole('button', { name: 'Share price' });
    const profit = within(group).getByRole('button', { name: 'Net profit' });
    expect(price).toHaveAttribute('aria-pressed', 'true');
    expect(profit).toHaveAttribute('aria-pressed', 'false');

    await user.click(profit);
    expect(await screen.findByRole('img', { name: /^Net profit of DemoCo Alpha/ })).toBeVisible();
    expect(profit).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('img', { name: /^Share price of/ })).toBeNull();

    price.focus();
    await user.keyboard('{Enter}');
    expect(await screen.findByRole('img', { name: /^Share price of/ })).toBeInTheDocument();
  });

  it('keeps the chosen view when another stock tab is picked', async () => {
    const user = userEvent.setup();
    withPrices();
    render(<HomeView />);
    await pricesLoaded();
    await user.click(screen.getByRole('button', { name: 'Net profit' }));
    await user.click(screen.getAllByRole('tab')[1]!);
    expect(await screen.findByRole('img', { name: /^Net profit of DemoCo Beta/ })).toBeVisible();
  });

  it('shows the latest close and the day change on each card, net profit as the small line', async () => {
    withPrices();
    render(<HomeView />);
    await pricesLoaded();

    const cards = region('At a glance');
    const alpha = within(cards).getByRole('link', { name: /DemoCo Alpha Limited/ });
    expect(alpha).toHaveTextContent('₹101.50');
    expect(alpha).toHaveTextContent('+1.5%');
    expect(alpha.querySelector('.home-change')).toHaveClass('rise');
    expect(alpha).toHaveTextContent('FY2026 net profit ₹120 crore');
    const beta = within(cards).getByRole('link', { name: /DemoCo Beta Limited/ });
    expect(beta).toHaveTextContent('₹2,050.25');
    expect(beta).toHaveTextContent('−0.6%');
    expect(beta.querySelector('.home-change')).toHaveClass('fall');
  });

  it('shows the close and change in the followed rows', async () => {
    withPrices({ followed: ['DEMOB'] });
    render(<HomeView />);
    await pricesLoaded();
    const row = await within(region('Your stocks')).findByRole('link', { name: /DEMOB/ });
    expect(row).toHaveTextContent('₹2,050.25');
    expect(row).toHaveTextContent('−0.6%');
    expect(row).toHaveTextContent('FY2026 net profit ₹150 crore');
  });

  it('falls back to net profit and says prices are not loaded when a stock has none', async () => {
    withPrices();
    render(<HomeView />);
    await pricesLoaded();
    await userEvent.click(screen.getAllByRole('tab')[2]!);

    // DEMOC has no net profit either; the message names the gap rather than drawing a zero.
    const hero = region('Your personal Indian stock analyst');
    expect(await within(hero).findByText('Prices not loaded yet')).toBeInTheDocument();
    expect(within(hero).queryByRole('group', { name: 'Chart shows' })).toBeNull();
    expect(within(hero).queryByRole('img', { name: /^Share price of/ })).toBeNull();
  });

  it('shows the net profit view and a muted note when no stock has prices', async () => {
    api();
    render(<HomeView />);
    await loaded();
    const hero = region('Your personal Indian stock analyst');
    expect(within(hero).getByText('Prices not loaded yet')).toHaveClass('muted');
    expect(within(hero).queryByRole('group', { name: 'Chart shows' })).toBeNull();

    const alpha = within(region('At a glance')).getByRole('link', { name: /DemoCo Alpha/ });
    expect(alpha).toHaveTextContent('Prices not loaded yet');
    expect(alpha).toHaveTextContent('₹120');
    expect(alpha).not.toHaveTextContent('₹0');
  });

  it('treats a failed prices call like no prices, without hiding the rest', async () => {
    const fake = withPrices();
    fake.failWith('GET /api/v1/stocks/DEMOA/prices', 500);
    render(<HomeView />);
    await loaded();
    const alpha = within(region('At a glance')).getByRole('link', { name: /DemoCo Alpha/ });
    expect(alpha).toHaveTextContent('Prices not loaded yet');
    expect(within(region('At a glance')).getByRole('link', { name: /Beta/ })).toHaveTextContent(
      '₹2,050.25',
    );
  });

  it('sends the user to sign in when the prices call says the session ended', async () => {
    nav.replace.mockClear();
    const fake = withPrices();
    fake.failWith('GET /api/v1/stocks/DEMOA/prices', 401);
    render(<HomeView />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });

  it('never uses advice words or calls the prices live', async () => {
    withPrices({ followed: ['DEMOA'] });
    const { container } = render(<HomeView />);
    await pricesLoaded();
    const words = container.querySelector('main')?.textContent ?? '';
    expect(words).not.toMatch(/\b(buy|sell|live|recommend\w*|should)\b/i);
  });
});
