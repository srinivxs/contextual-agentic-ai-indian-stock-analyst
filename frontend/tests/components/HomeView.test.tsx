import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { HomeView } from '@/components/HomeView';
import {
  demoCitation,
  demoFeedItem,
  demoInsights,
  demoMatches,
  demoPrices,
  demoRbiCitation,
  demoProfileField,
  demoSeries,
  demoStockMatch,
  emptyInsights,
  installFakeApi,
  STOCKS,
  type FakeInsights,
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

const eventOn = (day: string, summary: string): FakeInsights['events'][number] => ({
  event_type: 'earnings_results',
  sentiment: 'positive',
  impact: 'medium',
  event_date: day,
  summary,
  citation: demoCitation({ label: `Announcement · ${summary.slice(0, 8)} · p.2` }),
});

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
    expect(await screen.findByText('Hello')).toBeInTheDocument();
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

  it('invites the user to tell the chat their preferences when the profile is empty', async () => {
    api();
    render(<HomeView />);
    await loaded();
    const link = screen.getByRole('link', { name: /Your match/ });
    expect(link).toHaveAttribute('href', '/chat/');
    expect(link).toHaveTextContent('Tell the chat your preferences');
  });

  it('counts the stocks per status once there is a profile, and links to Match', async () => {
    api({
      matches: demoMatches({
        profile_empty: false,
        stocks: [
          demoStockMatch({ symbol: 'DEMOA', status: 'match' }),
          demoStockMatch({ symbol: 'DEMOB', status: 'partial' }),
          demoStockMatch({ symbol: 'DEMOC', status: 'not_enough_data' }),
        ],
      }),
    });
    render(<HomeView />);
    await loaded();
    const link = await screen.findByRole('link', { name: /Your match/ });
    expect(link).toHaveAttribute('href', '/match/');
    expect(link).toHaveTextContent('1 match');
    expect(link).toHaveTextContent('1 partial');
    expect(link).toHaveTextContent('0 no match');
    expect(link).toHaveTextContent('1 not enough data');
  });

  it('shows a short message when the match cannot be loaded', async () => {
    const fake = api();
    fake.failWith('GET /api/v1/match', 500);
    render(<HomeView />);
    await loaded();
    expect(await screen.findByText(/couldn't load your match/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /DemoCo Alpha Limited/ })).toBeInTheDocument();
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
    // Independent parts still load.
    expect(await screen.findByText('Hello')).toBeInTheDocument();
  });
});

describe('the home page: chat card', () => {
  it('greets, says what it can do, and says where its answers come from', async () => {
    api();
    render(<HomeView />);
    const chat = await screen.findByRole('region', { name: 'Chat with your analyst' });
    expect(within(chat).getByText('Hello')).toBeInTheDocument();
    expect(within(chat).getAllByRole('listitem').length).toBeGreaterThanOrEqual(4);
    expect(within(chat).getByText(/from their filings/)).toBeInTheDocument();
    expect(within(chat).getByText('Answers only from stored filings')).toBeInTheDocument();
  });

  it('offers suggestion chips that open the chat with the question filled in', async () => {
    api();
    render(<HomeView />);
    const chat = await screen.findByRole('region', { name: 'Chat with your analyst' });
    const chips: [string, string][] = [
      ['Analyse RELIANCE', '/chat/?q=Analyse%20RELIANCE'],
      ['Compare TCS and HDFC Bank', '/chat/?q=Compare%20TCS%20and%20HDFC%20Bank'],
      ['Latest news on TCS', '/chat/?q=Latest%20news%20on%20TCS'],
      ['Which stocks have low debt?', '/chat/?q=Which%20stocks%20have%20low%20debt%3F'],
      ['Match me', '/chat/?q=Match%20me'],
    ];
    for (const [text, href] of chips) {
      expect(within(chat).getByRole('link', { name: text })).toHaveAttribute('href', href);
    }
  });

  it('sends what is typed to the chat page', async () => {
    const user = userEvent.setup();
    nav.push.mockClear();
    api();
    render(<HomeView />);
    const chat = await screen.findByRole('region', { name: 'Chat with your analyst' });

    await user.type(within(chat).getByLabelText('Ask about a stock'), 'How is TCS doing?');
    await user.click(within(chat).getByRole('button', { name: 'Send' }));
    expect(nav.push).toHaveBeenCalledWith('/chat/?q=How%20is%20TCS%20doing%3F');
  });

  it('sends nothing for a blank question', async () => {
    const user = userEvent.setup();
    nav.push.mockClear();
    api();
    render(<HomeView />);
    const chat = await screen.findByRole('region', { name: 'Chat with your analyst' });

    await user.type(within(chat).getByLabelText('Ask about a stock'), '   {Enter}');
    expect(nav.push).not.toHaveBeenCalled();
  });
});

describe('the home page: latest news', () => {
  it('merges stock events and RBI releases, newest first, with safe links', async () => {
    api({
      insights: {
        DEMOA: demoInsights({ events: [eventOn('2026-07-01', 'Alpha reported higher revenue.')] }),
        DEMOB: demoInsights({
          symbol: 'DEMOB',
          events: [eventOn('2026-08-15', 'Beta signed a large order.')],
        }),
      },
      feed: [demoFeedItem({ title: 'Policy statement on the demo repo rate' })],
    });
    render(<HomeView />);
    await loaded();

    const news = region('Latest from filings and RBI');
    const items = await within(news).findAllByRole('listitem');
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent('Policy statement on the demo repo rate');
    expect(items[0]).toHaveTextContent('RBI');
    expect(items[0]).toHaveTextContent('12 Sep 2026');
    expect(items[1]).toHaveTextContent('Beta signed a large order.');
    expect(items[1]).toHaveTextContent('DEMOB');
    expect(items[1]).toHaveTextContent('15 Aug 2026');
    expect(items[2]).toHaveTextContent('Alpha reported higher revenue.');
    expect(within(items[0] as HTMLElement).getByRole('link')).toHaveAttribute(
      'href',
      'https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=00001',
    );
    expect(within(items[2] as HTMLElement).getByRole('link')).toHaveAttribute(
      'href',
      'https://www.bseindia.com/xml-data/corpfiling/AttachHis/demo-ar-2026.pdf#page=44',
    );
  });

  it('lists an RBI release that reached a stock as an event once, not twice', async () => {
    const rbi = demoRbiCitation();
    const other = demoRbiCitation({ url: 'https://www.rbi.org.in/Scripts/Other.aspx?prid=2' });
    const events = [
      { ...eventOn('2026-09-12', 'Repeat of the feed item.'), citation: rbi },
      { ...eventOn('2026-09-10', 'A release only the stock carries.'), citation: other },
    ];
    api({
      insights: { DEMOA: demoInsights({ events }) },
      feed: [demoFeedItem({ title: 'The feed item' })],
    });
    render(<HomeView />);
    await loaded();
    const items = await within(region('Latest from filings and RBI')).findAllByRole('listitem');
    expect(items.map((i) => i.textContent)).toEqual([
      expect.stringContaining('The feed item'),
      expect.stringContaining('A release only the stock carries.'),
    ]);
    expect(items[1]).toHaveTextContent('RBI · Earnings results');
  });

  it('shows at most five items', async () => {
    const events = ['01', '02', '03', '04', '05', '06', '07'].map((d) =>
      eventOn(`2026-06-${d}`, `Event number ${d}.`),
    );
    api({ insights: { DEMOA: demoInsights({ events }) } });
    render(<HomeView />);
    await loaded();
    const items = await within(region('Latest from filings and RBI')).findAllByRole('listitem');
    expect(items).toHaveLength(5);
    expect(items[0]).toHaveTextContent('Event number 07.');
    expect(items[4]).toHaveTextContent('Event number 03.');
  });

  it('does not link an address that is not an official one', async () => {
    api({
      feed: [
        demoFeedItem({ title: 'A release with a bad address', url: 'https://evil.example/x' }),
      ],
    });
    render(<HomeView />);
    await loaded();
    const item = (await within(region('Latest from filings and RBI')).findAllByRole('listitem'))[0];
    expect(item).toHaveTextContent('A release with a bad address');
    expect(within(item as HTMLElement).queryByRole('link')).toBeNull();
  });

  it('shows an RBI release that has no date, after the dated ones', async () => {
    api({
      insights: { DEMOA: demoInsights({ events: [eventOn('2026-07-01', 'Alpha dated event.')] }) },
      feed: [demoFeedItem({ title: 'Undated release', published_at: null })],
    });
    render(<HomeView />);
    await loaded();
    const items = await within(region('Latest from filings and RBI')).findAllByRole('listitem');
    expect(items.map((i) => i.textContent)).toEqual([
      expect.stringContaining('Alpha dated event.'),
      expect.stringContaining('Undated release'),
    ]);
  });

  it('says when there is nothing yet', async () => {
    api({ insights: { DEMOA: emptyInsights(STOCKS[0]!) } });
    render(<HomeView />);
    await loaded();
    expect(
      await within(region('Latest from filings and RBI')).findByText(/Nothing new yet/),
    ).toBeInTheDocument();
  });

  it('shows what loaded when one source fails', async () => {
    const fake = api({
      insights: { DEMOA: demoInsights({ events: [eventOn('2026-07-01', 'Alpha reported.')] }) },
    });
    fake.failWith('GET /api/v1/feed', 500);
    render(<HomeView />);
    await loaded();
    const news = region('Latest from filings and RBI');
    expect(await within(news).findByText('Alpha reported.')).toBeInTheDocument();
    expect(within(news).getByText(/Some news could not be loaded/)).toBeInTheDocument();
  });

  it('shows a short message when nothing could be loaded', async () => {
    const fake = api();
    fake.failWith('GET /api/v1/feed', 500);
    for (const stock of STOCKS) {
      fake.failWith(`GET /api/v1/stocks/${stock.symbol}/insights`, 500);
    }
    render(<HomeView />);
    await loaded();
    expect(
      await within(region('Latest from filings and RBI')).findByText(
        /couldn't load the latest news/i,
      ),
    ).toBeInTheDocument();
  });
});

describe('the home page: investor profile', () => {
  it('lists what is remembered by field, and links Edit to the chat', async () => {
    api({
      profileFields: [
        demoProfileField(),
        demoProfileField({
          field: 'investment_style',
          values: ['income', 'quality'],
          labels: ['Dividends / income', 'Quality'],
        }),
      ],
    });
    render(<HomeView />);
    await loaded();

    const profile = region('Your investor profile');
    const rows = await within(profile).findAllByRole('listitem');
    expect(rows.map((r) => r.textContent)).toEqual([
      'RiskConservative',
      'DebtNot set',
      'StyleDividends / income, Quality',
      'OtherNot set',
    ]);
    expect(within(profile).getByRole('link', { name: 'Edit' })).toHaveAttribute('href', '/chat/');
  });

  it('invites the user to tell the chat when nothing is remembered', async () => {
    api();
    render(<HomeView />);
    await loaded();
    const profile = region('Your investor profile');
    expect(await within(profile).findByText(/Nothing remembered yet/)).toBeInTheDocument();
    expect(within(profile).getByRole('link', { name: 'Edit' })).toBeInTheDocument();
  });

  it('shows a short message when the profile cannot be loaded', async () => {
    const fake = api();
    fake.failWith('GET /api/v1/profile', 500);
    render(<HomeView />);
    await loaded();
    expect(await screen.findByText(/couldn't load your profile/i)).toBeInTheDocument();
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
    await screen.findByText('Conservative');

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
