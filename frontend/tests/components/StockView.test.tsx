import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { StockView } from '@/components/StockView';
import {
  demoCitation,
  demoFact,
  demoInsights,
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

    expect(
      await screen.findByRole('heading', { level: 1, name: 'DemoCo Alpha Limited' }),
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

  it('offers a search of this stock’s filings', async () => {
    installFakeApi({ insights: { DEMOA: demoInsights() } });
    render(<StockView />);

    expect(
      await screen.findByRole('searchbox', { name: 'Search DemoCo Alpha Limited’s filings' }),
    ).toBeInTheDocument();
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
    expect(link).not.toHaveAttribute('title');
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

  it('marks a disputed value and lists the sources that disagree', async () => {
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

    expect(await screen.findByText('disputed')).toBeInTheDocument();
    const details = screen.getByText('Sources that disagree').closest('details');
    expect(details).not.toBeNull();
    expect(
      within(details as HTMLElement).getByRole('link', {
        name: 'screener.in · profit-loss · Sales · Mar 2026',
      }),
    ).toHaveAttribute('target', '_blank');
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
    expect(within(events).getByText('positive · medium impact')).toBeInTheDocument();
    expect(
      within(events).getByText('DemoCo Alpha reported higher quarterly revenue.'),
    ).toBeInTheDocument();
    expect(
      within(events).getByRole('link', { name: 'Announcement · Results · p.2' }),
    ).toHaveAttribute('target', '_blank');
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
    expect(link).toHaveClass('chip', 'chip-rbi');
  });

  it('shows an RBI citation on another host as plain text', async () => {
    withRbi(demoRbiCitation({ url: 'https://rbi.org.in.evil.example/x' }));
    render(<StockView />);
    expect(await screen.findByText(demoRbiCitation().label)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: demoRbiCitation().label })).toBeNull();
  });
});
