import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DATA_POLL_MS } from '@/components/DataFreshness';
import { StockView } from '@/components/StockView';
import {
  demoCitation,
  demoFact,
  demoFundamentals,
  demoInsights,
  demoPrices,
  noPrices,
  demoRbiCitation,
  demoScreenerCitation,
  installFakeApi,
} from '../helpers/fakeApi';

// The real router object is stable between renders, so the mock's must be too.
const nav = vi.hoisted(() => {
  const replace = vi.fn();
  return { replace, router: { replace }, query: { value: 'symbol=DEMOA' } };
});
vi.mock('next/navigation', () => ({
  useRouter: () => nav.router,
  useSearchParams: () => new URLSearchParams(nav.query.value),
}));

beforeEach(() => {
  nav.query.value = 'symbol=DEMOA';
  nav.replace.mockReset();
});

const section = (name: string): HTMLElement => screen.getByRole('region', { name });

describe('the stock page', () => {
  it('names the company and says the figures are not investment advice', async () => {
    const api = installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);

    // The first test of the file loads the page cold: under the full coverage run it can take
    // longer than the default second.
    expect(
      await screen.findByRole(
        'heading',
        { level: 1, name: 'DemoCo Alpha Limited' },
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('DEMOA')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Not investment advice. Figures come from company filings and screener.in, each with its source.',
      ),
    ).toBeInTheDocument();
    expect(api.requests).toContain('GET /api/v1/stocks/DEMOA/insights');
  });

  it('has a header card with a monogram, a way back, and Stocks as the current menu item', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    const { container } = render(<StockView />);

    await screen.findByRole('heading', { level: 1, name: 'DemoCo Alpha Limited' });
    expect(container.querySelector('.stock-head .monogram.lg')).not.toBeNull();
    expect(screen.getByRole('link', { name: 'Back to Stocks' })).toHaveAttribute(
      'href',
      '/stocks/',
    );
    const menu = screen.getByRole('navigation', { name: 'Main' });
    expect(within(menu).getByRole('link', { name: 'Stocks' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('has no search box: searching the filings lives on the Documents page', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);

    await screen.findByRole('heading', { level: 1, name: 'DemoCo Alpha Limited' });
    expect(screen.queryByRole('searchbox', { name: /filings/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Search the filings' })).not.toBeInTheDocument();
  });

  it('shows a loading message until the insights arrive', async () => {
    const api = installFakeApi();
    const release = api.hold('GET /api/v1/stocks/DEMOA/insights');
    render(<StockView />);

    expect(await screen.findByText('Loading key facts…')).toBeInTheDocument();
    release();
    expect(await screen.findByText('No facts extracted yet.')).toBeInTheDocument();
  });
});

describe('key facts', () => {
  it('is one card per metric, newest figure first, older periods beneath', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);

    const facts = await screen.findByRole('region', { name: 'Key facts' });
    const labels = within(facts)
      .getAllByRole('heading', { level: 3 })
      .map((h) => h.textContent);
    expect(labels).toEqual(['Revenue from operations', 'Net profit']);
    const [revenue] = within(facts).getAllByRole('listitem');
    const card = revenue as HTMLElement;
    expect(card.querySelector('.fact-value')?.textContent).toBe('₹1,23,456 crore');
    expect(card.querySelector('.fact-period')?.textContent).toBe('FY2026');
    const earlier = within(card).getByRole('list', { name: 'Earlier Revenue from operations' });
    expect(within(earlier).getByText('₹1,10,000 crore')).toBeInTheDocument();
    expect(within(earlier).getByText('FY2025')).toBeInTheDocument();
    expect(within(facts).getByText('₹12,345.5 crore')).toBeInTheDocument();
  });

  it('shows a quarter with a space in its period', async () => {
    installFakeApi({
      insights: { DEMOA: demoInsights({ key_facts: [demoFact({ period: 'Q3FY2026' })] }) },
    });
    render(<StockView />);

    expect(await screen.findByText('Q3 FY2026')).toBeInTheDocument();
  });

  it('cites each value with a link that opens the source in a new tab, safely', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);

    const link = await screen.findByRole('link', {
      name: 'Annual report · Annual Report 2026 · p.44',
    });
    expect(link).toHaveAttribute('href', demoCitation().url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(link).toHaveAttribute('title', demoCitation().quote);
  });

  it('links a screener figure to screener.in, with no quote', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);

    const link = await screen.findByRole('link', { name: demoScreenerCitation().label });
    expect(link).toHaveAttribute('href', demoScreenerCitation().url);
    expect(link).toHaveAttribute('title', demoScreenerCitation().label); // no quote: the source
  });

  it('shows a citation it may not link to as plain text', async () => {
    installFakeApi({
      insights: {
        DEMOA: demoInsights({
          key_facts: [
            demoFact({
              citation: demoCitation({ label: 'Odd source', url: 'javascript:alert(1)' }),
            }),
          ],
        }),
      },
    });
    render(<StockView />);

    expect(await screen.findByText('Odd source')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Odd source' })).toBeNull();
  });

  it('says how many other sources agree', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);
    expect(await screen.findByText('agreed by 1 other source')).toBeInTheDocument();
  });

  it('shows a source as a small link icon, the full source in its name', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);

    const link = await screen.findByRole('link', {
      name: 'Annual report · Annual Report 2026 · p.44',
    });
    expect(link).toHaveClass('source-icon');
    expect(link.textContent).toBe(''); // only the icon is drawn
    expect(link.querySelector('svg')).not.toBeNull();
  });

  it('shows one figure where sources disagree, with no list of the others', async () => {
    installFakeApi({
      insights: {
        DEMOA: demoInsights({
          key_facts: [
            demoFact({
              status: 'disputed',
              disputed_by: [
                demoScreenerCitation({ label: 'screener.in · profit-loss · Sales · Mar 2026' }),
              ],
            }),
          ],
        }),
      },
    });
    render(<StockView />);

    expect(
      await screen.findByRole('link', { name: 'Annual report · Annual Report 2026 · p.44' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('disputed')).toBeNull();
    expect(screen.queryByText('Sources that disagree')).toBeNull();
    expect(
      screen.queryByRole('link', { name: 'screener.in · profit-loss · Sales · Mar 2026' }),
    ).toBeNull();
  });

  it('says when a value is standalone, or its basis is not stated', async () => {
    installFakeApi({
      insights: {
        DEMOA: demoInsights({
          key_facts: [
            demoFact({ basis: 'standalone' }),
            demoFact({ metric: 'net_profit', label: 'Net profit', basis: 'unspecified' }),
          ],
        }),
      },
    });
    render(<StockView />);

    expect(await screen.findByText('(standalone)')).toBeInTheDocument();
    expect(screen.getByText('(basis not stated)')).toBeInTheDocument();
  });

  it('labels an amount in dollars as US$, never converted', async () => {
    installFakeApi({
      insights: {
        DEMOA: demoInsights({
          key_facts: [demoFact({ currency: 'USD', unit: 'USD_MILLION', value: '1800.0000' })],
        }),
      },
    });
    render(<StockView />);
    expect(await screen.findByText('US$1,800 million')).toBeInTheDocument();
  });

  it('says so when nothing has been extracted yet', async () => {
    installFakeApi();
    render(<StockView />);

    expect(await screen.findByText('No facts extracted yet.')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { level: 3 })).toBeNull();
  });
});

describe('derived values', () => {
  it('shows each value with its reason and sources', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);

    await screen.findByRole('heading', { level: 1, name: 'DemoCo Alpha Limited' });
    const derived = section('Derived values');
    const items = within(derived).getAllByRole('listitem');
    expect(items.map((item) => item.querySelector('.derived-name')?.textContent)).toEqual([
      'Debt to equity',
      'Revenue growth',
      'Profit growth',
      'Latest dividend',
    ]);
    expect(within(derived).getByText('0.25')).toBeInTheDocument();
    expect(within(derived).getByText('+12.5%')).toBeInTheDocument();
    expect(within(derived).getByText('Not enough data')).toBeInTheDocument();
    expect(within(derived).getByText('₹5.50 per share')).toBeInTheDocument();
    expect(
      within(derived).getByText(
        'FY2026, consolidated: borrowings ₹2,500 crore / equity ₹10,000 crore.',
      ),
    ).toBeInTheDocument();
    expect(
      within(derived).getByRole('link', { name: 'Annual report · Annual Report 2026 · p.51' }),
    ).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('colours growth by its sign and nothing else', async () => {
    const base = demoInsights();
    installFakeApi({
      insights: {
        DEMOA: demoInsights({
          derived: base.derived.map((d) =>
            d.name === 'profit_growth' ? { ...d, status: 'ok', value: '-4.2' } : d,
          ),
        }),
      },
    });
    render(<StockView />);

    const derived = await screen.findByRole('region', { name: 'Derived values' });
    expect(within(derived).getByText('+12.5%')).toHaveClass('rise');
    expect(within(derived).getByText('-4.2%')).toHaveClass('fall');
    expect(within(derived).getByText('0.25')).toHaveClass('derived-value');
    expect(within(derived).getByText('0.25')).not.toHaveClass('rise');
    expect(within(derived).getByText('0.25')).not.toHaveClass('fall');
    expect(within(derived).queryByText('Not enough data')).toBeNull();
  });

  it('puts a status that is not a value into words, muted', async () => {
    const base = demoInsights();
    installFakeApi({
      insights: {
        DEMOA: demoInsights({
          is_financial: true,
          derived: base.derived.map((d, i) =>
            i === 0
              ? { ...d, status: 'not_applicable', value: null, reason: 'Not used for banks.' }
              : i === 1
                ? { ...d, status: 'not_assessable', value: null, reason: 'Different bases.' }
                : d,
          ),
        }),
      },
    });
    render(<StockView />);

    expect(await screen.findByText('Not applicable')).toHaveClass('muted');
    expect(screen.getByText('Not used for banks.')).toBeInTheDocument();
    expect(screen.getByText('Not assessable')).toBeInTheDocument();
  });
});

describe('sentiment and events', () => {
  it('shows the sentiment badge, its score and how many events it counts', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);

    await screen.findByRole('heading', { level: 1, name: 'DemoCo Alpha Limited' });
    const sentiment = section('Recent sentiment');
    expect(within(sentiment).getByText('Positive')).toHaveClass('sentiment', 'positive');
    expect(within(sentiment).getByText('Score +0.42 from 5 events')).toBeInTheDocument();
  });

  it('says when there are too few events for a sentiment', async () => {
    installFakeApi();
    render(<StockView />);

    expect(await screen.findByText('Not enough events')).toBeInTheDocument();
    expect(screen.getByText('No events extracted yet.')).toBeInTheDocument();
  });

  it('lists recent events with date, type, sentiment, impact, summary and source', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);

    await screen.findByRole('heading', { level: 1, name: 'DemoCo Alpha Limited' });
    const events = section('Recent events');
    expect(within(events).getByText('01 Jul 2026')).toBeInTheDocument();
    expect(within(events).getByText('Earnings results')).toBeInTheDocument();
    expect(within(events).getByText('Positive')).toHaveClass('event-tone', 'positive');
    expect(within(events).getByText('Medium impact')).toBeInTheDocument();
    expect(
      within(events).getByText('DemoCo Alpha reported higher quarterly revenue.'),
    ).toBeInTheDocument();
    expect(
      within(events).getByRole('link', { name: 'Announcement · Results · p.2' }),
    ).toHaveAttribute('target', '_blank');
  });

  it('shows each day once, on the left, however many events it had', async () => {
    const event = (date: string, summary: string, sentiment = 'neutral') => ({
      event_type: 'other',
      sentiment,
      impact: 'low',
      event_date: date,
      summary,
      citation: demoCitation({ label: `Announcement · ${summary}` }),
    });
    installFakeApi({
      insights: {
        DEMOA: demoInsights({
          events: [
            event('2026-09-25', 'First.', 'negative'),
            event('2026-09-22', 'Second.'),
            event('2026-09-22', 'Third.'),
          ],
        }),
      },
    });
    render(<StockView />);

    await screen.findByRole('heading', { level: 1, name: 'DemoCo Alpha Limited' });
    const events = section('Recent events');
    const days = [...events.querySelectorAll('.event-day')].map((day) => day.textContent);
    expect(days).toEqual(['25', '22']); // the 22nd once, for both of its events
    expect(within(events).getAllByText('22 Sep 2026')).toHaveLength(2); // still read out for each
    expect(within(events).getByText('Negative')).toHaveClass('event-tone', 'negative');
    expect(within(events).getAllByRole('listitem')).toHaveLength(3);
  });
});

describe('safety and failures', () => {
  it('renders text as text, never as HTML', async () => {
    const evil = '<img src=x onerror=alert(1)>';
    installFakeApi({
      insights: {
        DEMOA: demoInsights({
          name: evil,
          key_facts: [demoFact({ label: evil, citation: demoCitation({ label: evil }) })],
          events: [{ ...demoInsights().events[0]!, summary: evil }],
        }),
      },
    });
    const { container } = render(<StockView />);

    await screen.findByRole('heading', { level: 1, name: evil });
    expect(container.querySelector('img')).toBeNull();
  });

  it('says so, with a way back, for a stock the server does not know', async () => {
    nav.query.value = 'symbol=NOPE';
    installFakeApi();
    render(<StockView />);

    expect(await screen.findByText('We don’t know a stock called NOPE.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to Stocks' })).toHaveAttribute(
      'href',
      '/stocks/',
    );
  });

  it.each([
    ['no symbol at all', ''],
    ['a symbol that is not a ticker', 'symbol=..%2Fme'],
  ])('asks nothing of the server for %s', async (_label, query) => {
    nav.query.value = query;
    const api = installFakeApi();
    render(<StockView />);

    expect(await screen.findByText('Choose a stock from the Stocks page.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to Stocks' })).toBeInTheDocument();
    expect(api.requests.filter((r) => r.includes('/insights'))).toEqual([]);
  });

  it('sends a signed-out visitor to the sign-in page', async () => {
    const api = installFakeApi({ signedIn: false });
    render(<StockView />);

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
    expect(api.requests.filter((r) => r.includes('/insights'))).toEqual([]);
  });

  it('goes back to the sign-in page when the session has ended', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/stocks/DEMOA/insights', 401);
    render(<StockView />);

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });

  it('shows an error, not the sign-in page, when the insights cannot be loaded', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/stocks/DEMOA/insights', 500);
    render(<StockView />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn.t load/i);
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('shows an error when it cannot find out who is signed in', async () => {
    const api = installFakeApi();
    api.failWith('GET /api/v1/me', 500);
    render(<StockView />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn.t load/i);
  });

  it('signs out and returns to the sign-in page', async () => {
    const api = installFakeApi();
    render(<StockView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }));

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
    expect(api.requests).toContain('POST /api/v1/auth/logout');
  });
});

describe('the stock page, RBI citations', () => {
  const withRbi = (citation = demoRbiCitation()) =>
    installFakeApi({
      insights: {
        DEMOA: demoInsights({
          events: [
            {
              event_type: 'regulatory',
              sentiment: 'neutral',
              impact: 'low',
              event_date: '2026-09-12',
              summary: 'An invented regulator notice.',
              citation,
            },
          ],
        }),
      },
    });

  it('links an RBI press release to rbi.org.in, marked as RBI', async () => {
    withRbi();
    render(<StockView />);
    const link = await screen.findByRole('link', { name: demoRbiCitation().label });
    expect(link).toHaveAttribute('href', demoRbiCitation().url);
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(link).toHaveClass('source-icon', 'rbi');
  });

  it('shows an RBI citation on another host as plain text', async () => {
    withRbi(demoRbiCitation({ url: 'https://rbi.org.in.evil.example/x' }));
    render(<StockView />);
    expect(await screen.findByText(demoRbiCitation().label)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: demoRbiCitation().label })).toBeNull();
  });
});

describe('the share price card', () => {
  const show = (prices = demoPrices('DEMOA')) => {
    const api = installFakeApi({ insights: { DEMOA: demoInsights() }, prices: { DEMOA: prices } });
    render(<StockView />);
    return api;
  };
  const card = async (): Promise<ReturnType<typeof within>> => {
    const region = await screen.findByRole('region', { name: 'Share price' });
    await waitFor(() => expect(within(region).queryByText(/Loading share price/)).toBeNull());
    return within(region);
  };

  it('shows the latest close with its date and the day change, and the chart', async () => {
    show();
    const c = await card();
    expect(c.getByText('₹101.50', { selector: '.price-close' })).toBeInTheDocument();
    expect(c.getByText('close on 28 Sep 2026')).toBeInTheDocument();
    for (const day of c.getAllByText('+1.5% on the day')) expect(day).toHaveClass('rise');
    expect(
      c.getByRole('img', { name: /^Share price of DemoCo Alpha Limited/ }),
    ).toBeInTheDocument();
  });

  it('sits above the key facts', async () => {
    show();
    const price = await screen.findByRole('region', { name: 'Share price' });
    const facts = await screen.findByRole('region', { name: 'Key facts' });
    expect(price.compareDocumentPosition(facts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('has no returns, volatility, P/E or dividend yield (they moved to Fundamentals)', async () => {
    show();
    const c = await card();
    expect(c.queryByRole('list', { name: 'Returns' })).toBeNull();
    expect(c.queryByRole('list', { name: 'Risk and valuation' })).toBeNull();
    for (const label of ['1 month', 'Volatility, 1 year', 'P/E', 'Dividend yield']) {
      expect(c.queryByText(label)).toBeNull();
    }
    expect(c.queryByText('not enough history')).toBeNull();
  });

  it('lists a bonus issue or split plainly', async () => {
    show(demoPrices('DEMOA', { actions: [{ date: '2025-08-26', factor: '0.5' }] }));
    const c = await card();
    expect(
      c.getByText('26 Aug 2025: bonus issue or split, factor 0.5; earlier prices adjusted'),
    ).toBeInTheDocument();
  });

  it('lists no corporate actions when there are none', async () => {
    show();
    const c = await card();
    expect(c.queryByText(/bonus issue or split/)).toBeNull();
  });

  it('links the latest price file with a citation chip', async () => {
    show();
    const c = await card();
    const chip = await c.findByRole('link', { name: /BSE daily price file · 28 Sep 2026/ });
    expect(chip).toHaveAttribute('href', expect.stringContaining('https://www.bseindia.com/'));
    expect(chip).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('says the prices are end of day and not advice, and uses no advice words', async () => {
    show();
    await card();
    const region = screen.getByRole('region', { name: 'Share price' });
    expect(region).toHaveTextContent(
      "End-of-day prices from BSE's daily price files. Not live, not investment advice.",
    );
    const words = (region.textContent ?? '').replace(/not live/gi, '');
    expect(words).not.toMatch(/\b(buy|sell|live|recommend\w*|should)\b/i);
  });

  it('says prices are not loaded yet, with no number, when there are none', async () => {
    show(noPrices('DEMOA'));
    const c = await card();
    expect(await c.findByText('Prices not loaded yet')).toBeInTheDocument();
    expect(c.queryByRole('img')).toBeNull();
    expect(c.queryByText(/₹/)).toBeNull();
  });

  it('shows a short message, and the rest of the page, when the prices cannot load', async () => {
    const api = installFakeApi({ insights: { DEMOA: demoInsights() } });
    api.failWith('GET /api/v1/stocks/DEMOA/prices', 500);
    render(<StockView />);
    const c = await card();
    expect(await c.findByText(/couldn't load the share price/i)).toBeInTheDocument();
    expect(await screen.findByRole('region', { name: 'Key facts' })).toBeInTheDocument();
  });

  it('sends the visitor to sign in when the prices call says the session ended', async () => {
    const api = installFakeApi({ insights: { DEMOA: demoInsights() } });
    api.failWith('GET /api/v1/stocks/DEMOA/prices', 401);
    render(<StockView />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });
});

describe('the fundamentals card', () => {
  it('shows the ten fundamentals in two columns, with the date and the page they are from', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);
    const card = await screen.findByRole('region', { name: 'Fundamentals' });
    await within(card).findByText('Mkt Cap');
    const terms = within(card)
      .getAllByRole('term')
      .map((t) => t.textContent);
    expect(terms).toEqual([
      'Mkt Cap',
      'ROE',
      'P/E Ratio (TTM)',
      'EPS (TTM)',
      'P/B Ratio',
      'Div Yield',
      'Industry P/E',
      'Book Value',
      'Debt to Equity',
      'Face Value',
    ]);
    const value = (label: string) => within(card).getByText(label).nextElementSibling;
    expect(value('Mkt Cap')).toHaveTextContent('₹1,23,456 Cr');
    expect(value('EPS (TTM)')).toHaveTextContent('₹137.78');
    expect(value('EPS (TTM)')).toHaveAttribute('title', 'Computed: price / P/E.');
    expect(value('Industry P/E')).toHaveTextContent('Not available');
    expect(value('Industry P/E')).toHaveClass('muted');
    expect(card).toHaveTextContent('screener.in figures as of 30 Sep 2026');
    expect(within(card).getByRole('link', { name: 'screener.in company page' })).toHaveAttribute(
      'href',
      'https://www.screener.in/company/DEMOA/consolidated/',
    );
  });

  it('sits between the share price and the key facts', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);
    const price = await screen.findByRole('region', { name: 'Share price' });
    const card = await screen.findByRole('region', { name: 'Fundamentals' });
    const facts = await screen.findByRole('region', { name: 'Key facts' });
    expect(price.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(card.compareDocumentPosition(facts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('says so before screener.in was ever read, with no link', async () => {
    installFakeApi({
      insights: { DEMOA: demoInsights() },
      fundamentals: {
        DEMOA: { ...demoFundamentals('DEMOA'), as_of: null, source_url: null },
      },
    });
    render(<StockView />);
    const card = await screen.findByRole('region', { name: 'Fundamentals' });
    expect(
      await within(card).findByText('screener.in figures not read yet: press Update data.'),
    ).toBeInTheDocument();
    expect(within(card).queryByRole('link')).toBeNull();
  });

  it('loads the card again when an update finishes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = installFakeApi({
      insights: { DEMOA: demoInsights() },
      dataStatus: { updating: true },
    });
    render(<StockView />);
    await screen.findByRole('region', { name: 'Fundamentals' });
    const asked = () =>
      api.requests.filter((r) => r === 'GET /api/v1/stocks/DEMOA/fundamentals').length;
    await waitFor(() => expect(asked()).toBe(1));

    api.setDataStatus({ updating: false });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DATA_POLL_MS);
    });
    await waitFor(() => expect(asked()).toBe(2));
    vi.useRealTimers();
  });

  it('shows a short message when the card cannot load, keeping the rest of the page', async () => {
    const api = installFakeApi({ insights: { DEMOA: demoInsights() } });
    api.failWith('GET /api/v1/stocks/DEMOA/fundamentals', 500);
    render(<StockView />);
    const card = await screen.findByRole('region', { name: 'Fundamentals' });
    expect(await within(card).findByText(/couldn't load the fundamentals/i)).toBeInTheDocument();
    expect(await screen.findByRole('region', { name: 'Key facts' })).toBeInTheDocument();
  });
});
