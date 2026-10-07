import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatView } from '@/components/ChatView';
import {
  CHAT_STOCKS,
  demoAnswer,
  demoChatSource,
  demoConversation,
  demoFact,
  demoInsights,
  demoPrices,
  demoProfileField,
  demoQuestion,
  demoScreenerCitation,
  demoTable,
  installFakeApi,
  noPrices,
  type Options,
} from '../helpers/fakeApi';

const nav = vi.hoisted(() => {
  const replace = vi.fn();
  return { replace, router: { replace } };
});
vi.mock('next/navigation', () => ({ useRouter: () => nav.router }));

beforeEach(() => {
  nav.replace.mockReset();
});

const install = (options: Options = {}) => installFakeApi({ stocks: CHAT_STOCKS, ...options });

const box = (): HTMLElement => screen.getByRole('textbox', { name: 'Your question' });
const thread = (): HTMLElement => screen.getByRole('list', { name: 'Messages' });
const panel = (): HTMLElement => screen.getByRole('complementary', { name: 'Stock context' });

async function ask(question: string): Promise<void> {
  await userEvent.type(await screen.findByRole('textbox', { name: 'Your question' }), question);
  await userEvent.click(screen.getByRole('button', { name: 'Send' }));
}

describe('the header', () => {
  it('has the title, the honest subtitle, the pill and the conversations button', async () => {
    install();
    render(<ChatView />);
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Chat with your analyst' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'Grounded in official filings, screener.in and BSE end-of-day prices · TCS, HDFC Bank, Reliance',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('Answers only from stored data')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Conversations' })).toBeInTheDocument();
  });

  it('keeps the conversation list closed until asked, and closes it with Escape', async () => {
    install({ conversations: [demoConversation()] });
    render(<ChatView />);
    const button = await screen.findByRole('button', { name: 'Conversations' });
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: 'New chat' })).toBeNull();

    await userEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: 'New chat' })).toBeInTheDocument();

    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('button', { name: 'New chat' })).toBeNull();
    expect(button).toHaveAttribute('aria-expanded', 'false');
  });

  it('closes the list when the button is pressed again, and when New chat is picked', async () => {
    install();
    render(<ChatView />);
    const button = await screen.findByRole('button', { name: 'Conversations' });
    await userEvent.click(button);
    await userEvent.click(button);
    expect(screen.queryByRole('button', { name: 'New chat' })).toBeNull();
    await userEvent.click(button);
    await userEvent.click(screen.getByRole('button', { name: 'New chat' }));
    expect(screen.queryByRole('group', { name: 'Conversation list' })).toBeNull();
  });
});

describe('the messages', () => {
  it('shows the question on the right with the initials and the time', async () => {
    install({ email: 'reader@example.test' });
    render(<ChatView />);
    await ask('How much revenue did DemoCo Alpha report?');
    await screen.findByText(/reported revenue of/);

    const mine = within(thread())
      .getByText('How much revenue did DemoCo Alpha report?')
      .closest('li') as HTMLElement;
    expect(mine).toHaveClass('user');
    expect(within(mine).getByText('RE')).toBeInTheDocument();
    expect(mine.querySelector('time')?.textContent).toMatch(/^\d{2}:\d{2}$/);
  });

  it('shows the pending question with the initials but no time yet', async () => {
    const api = install({ email: 'reader@example.test' });
    const release = api.hold('POST /api/v1/chat/messages');
    render(<ChatView />);
    await ask('How did revenue grow?');
    const pending = (await within(thread()).findByText('How did revenue grow?')).closest('li');
    expect(pending).toHaveClass('pending');
    expect(within(pending as HTMLElement).getByText('RE')).toBeInTheDocument();
    expect(pending?.querySelector('time')).toBeNull();
    release();
    await screen.findByText(/reported revenue of/);
  });

  it('shows the answer in a card with an avatar and its time, figures in bold', async () => {
    install();
    render(<ChatView />);
    await ask('How did revenue grow?');
    await screen.findByText(/reported revenue of/);

    const card = thread().querySelector('.chat-message.assistant') as HTMLElement;
    expect(card.querySelector('.chat-avatar')).not.toBeNull();
    expect(card.querySelector('time')?.textContent).toMatch(/^\d{2}:\d{2}$/);
    const bold = [...card.querySelectorAll('strong')].map((b) => b.textContent);
    expect(bold).toEqual(['₹1,23,456 crore', '₹12,345.5 crore', '12.2%']);
    // Nothing is altered: the points, joined back with a space, are the answer word for word.
    const points = [...card.querySelectorAll('.chat-text')].map((p) => p.textContent);
    expect(points.join(' ')).toBe(demoAnswer().text);
  });

  it('gives an abstention no bold figures and no time-less card', async () => {
    install({
      chatAnswer: () =>
        demoAnswer({ status: 'abstained', text: "I don't have that in the data.", sources: [] }),
    });
    render(<ChatView />);
    await ask('What is the P/E?');
    const notice = await screen.findByText("I don't have that in the data.");
    expect(notice.querySelector('strong')).toBeNull();
  });
});

describe('the table under an answer', () => {
  it('shows the title, the columns, the rows, coloured changes and a linked source', async () => {
    install({ chatAnswer: () => demoAnswer({ table: demoTable() }) });
    render(<ChatView />);
    await ask('Net profit of DemoCo?');

    const table = await screen.findByRole('table', {
      name: 'DemoCo Alpha net profit (consolidated, ₹ crore)',
    });
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((h) => h.textContent),
    ).toEqual(['Year', 'Net profit (₹ crore)', 'Change']);
    expect(within(table).getByText('FY2026')).toBeInTheDocument();
    expect(within(table).getByText('12,345')).toBeInTheDocument();
    expect(within(table).getByText('+1.3%')).toHaveClass('rise');
    expect(within(table).getByText('-5.9%')).toHaveClass('fall');
    expect(within(table).getAllByRole('row')).toHaveLength(4);

    const link = screen.getByRole('link', { name: 'screener.in · consolidated, full years' });
    expect(link).toHaveAttribute('href', 'https://www.screener.in/company/DEMOA/consolidated/');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('shows a source it may not link to as plain text', async () => {
    install({
      chatAnswer: () =>
        demoAnswer({
          table: demoTable({ source: { label: 'Odd source', url: 'https://evil.example/x' } }),
        }),
    });
    render(<ChatView />);
    await ask('Net profit of DemoCo?');
    expect(await screen.findByText('Odd source')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Odd source' })).toBeNull();
  });

  it('shows no table for an answer without one', async () => {
    install();
    render(<ChatView />);
    await ask('How did revenue grow?');
    await screen.findByText(/reported revenue of/);
    expect(within(thread()).queryByRole('table')).toBeNull();
  });

  it('shows the table of a stored conversation, and drops a broken one', async () => {
    const stored = demoConversation({
      messages: [
        demoQuestion(),
        demoAnswer({ id: '00000000-0000-4000-8000-000000000501', table: demoTable() }),
        demoAnswer({
          id: '00000000-0000-4000-8000-000000000502',
          table: { title: 5 } as never,
        }),
      ],
    });
    install({ conversations: [stored] });
    render(<ChatView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Conversations' }));
    await userEvent.click(await screen.findByRole('button', { name: stored.title! }));
    await within(thread()).findAllByText(/reported revenue of/);
    expect(within(thread()).getAllByRole('table')).toHaveLength(1);
  });
});

describe('the sources box', () => {
  it('is titled, numbers each source, and says what kind of source it is', async () => {
    install();
    render(<ChatView />);
    await ask('How did revenue grow?');
    const list = await screen.findByRole('list', { name: 'Sources' });
    const box = list.closest('.chat-sources-box') as HTMLElement;
    expect(within(box).getByText('Sources')).toBeInTheDocument();

    const items = within(list).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('Official filing (BSE)');
    expect(items[1]).toHaveTextContent('screener.in figure');
    expect(items[2]).toHaveTextContent('Computed from stored figures');
  });

  it('opens a source in a new tab from the title, and keeps the quote reachable', async () => {
    install();
    render(<ChatView />);
    await ask('How did revenue grow?');
    const list = await screen.findByRole('list', { name: 'Sources' });
    const first = within(list).getAllByRole('listitem')[0] as HTMLElement;
    const link = within(first).getByRole('link', { name: demoChatSource().label });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(
      within(first)
        .getByText(demoChatSource().quote as string)
        .closest('details'),
    ).not.toBeNull();
  });
});

describe('follow-up suggestions', () => {
  it('are offered under the latest answer, for the stock asked about', async () => {
    install();
    render(<ChatView />);
    await ask('How is TCS doing?');
    const group = await screen.findByRole('group', { name: 'Follow-up suggestions' });
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual([
      'Show revenue and net profit for TCS',
      'Compare TCS with HDFC Bank and Reliance',
      'Latest news on TCS',
      'How does TCS fit my profile?',
    ]);
  });

  it('fill the box and never send', async () => {
    const api = install();
    render(<ChatView />);
    await ask('How is TCS doing?');
    await screen.findByText(/reported revenue of/);
    await userEvent.click(await screen.findByRole('button', { name: 'Latest news on TCS' }));
    expect(box()).toHaveValue('Latest news on TCS');
    expect(api.bodies).toHaveLength(1); // only the first question
  });

  it('are not shown while an answer is awaited, nor before any question', async () => {
    const api = install();
    const release = api.hold('POST /api/v1/chat/messages');
    render(<ChatView />);
    await screen.findByRole('textbox', { name: 'Your question' });
    expect(screen.queryByRole('group', { name: 'Follow-up suggestions' })).toBeNull();
    await ask('How is TCS doing?');
    await screen.findByText('Thinking…');
    expect(screen.queryByRole('group', { name: 'Follow-up suggestions' })).toBeNull();
    release();
    await screen.findByRole('group', { name: 'Follow-up suggestions' });
  });

  it('follow the stock chosen in the panel', async () => {
    install();
    render(<ChatView />);
    await ask('How did revenue grow?');
    await screen.findByRole('group', { name: 'Follow-up suggestions' });
    expect(screen.getByRole('button', { name: 'Latest news on Reliance' })).toBeInTheDocument();
    await userEvent.click(within(panel()).getByRole('button', { name: 'HDFC Bank' }));
    expect(screen.getByRole('button', { name: 'Latest news on HDFC Bank' })).toBeInTheDocument();
  });
});

describe('the stock in the panel', () => {
  it('starts with the first stock, and switches to the one a question names', async () => {
    install();
    render(<ChatView />);
    expect(
      await within(await screen.findByRole('complementary', { name: 'Stock context' })).findByRole(
        'heading',
        { name: 'Reliance overview' },
      ),
    ).toBeInTheDocument();

    await ask('How is HDFC Bank doing?');
    expect(
      await within(panel()).findByRole('heading', { name: 'HDFC Bank overview' }),
    ).toBeInTheDocument();
    expect(within(panel()).getByRole('button', { name: 'HDFC Bank' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('keeps the stock when a question names none', async () => {
    install();
    render(<ChatView />);
    await ask('Tell me about TCS');
    await within(panel()).findByRole('heading', { name: 'TCS overview' });
    await ask('And the net profit?');
    await screen.findAllByText(/reported revenue of/);
    expect(within(panel()).getByRole('heading', { name: 'TCS overview' })).toBeInTheDocument();
  });

  it('switches with the tabs, and asks the server about that stock', async () => {
    const api = install();
    render(<ChatView />);
    await within(await screen.findByRole('complementary', { name: 'Stock context' })).findByRole(
      'heading',
      { name: 'Reliance overview' },
    );
    await userEvent.click(within(panel()).getByRole('button', { name: 'TCS' }));
    await within(panel()).findByRole('heading', { name: 'TCS overview' });
    await waitFor(() => expect(api.requests).toContain('GET /api/v1/stocks/TCS/prices'));
    expect(api.requests).toContain('GET /api/v1/stocks/TCS/insights');
  });

  it('switches between stocks at once, with no reload flash, asking each only once', async () => {
    const api = install({
      prices: { RELIANCE: demoPrices('RELIANCE'), TCS: demoPrices('TCS') },
    });
    render(<ChatView />);
    await within(await screen.findByRole('complementary', { name: 'Stock context' })).findByRole(
      'heading',
      { name: 'Reliance overview' },
    );
    // every stock's panel figures are asked for once, up front
    await waitFor(() => {
      for (const symbol of ['RELIANCE', 'TCS', 'HDFCBANK']) {
        expect(api.requests).toContain(`GET /api/v1/stocks/${symbol}/prices`);
        expect(api.requests).toContain(`GET /api/v1/stocks/${symbol}/insights`);
      }
    });
    const flashes = () =>
      within(panel()).queryByText('Loading prices…') ?? within(panel()).queryByText('Loading…');
    for (const name of ['TCS', 'HDFC Bank', 'Reliance', 'TCS']) {
      await userEvent.click(within(panel()).getByRole('button', { name }));
      expect(flashes()).toBeNull(); // shown at once: nothing is loaded again
    }
    const asked = (path: string) => api.requests.filter((r) => r === `GET ${path}`).length;
    expect(asked('/api/v1/stocks/TCS/prices')).toBe(1);
    expect(asked('/api/v1/stocks/TCS/insights')).toBe(1);
  });

  it('follows the latest question of a conversation that is opened', async () => {
    const stored = demoConversation({
      messages: [demoQuestion({ text: 'What does HDFCBANK earn?' }), demoAnswer()],
    });
    install({ conversations: [stored] });
    render(<ChatView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Conversations' }));
    await userEvent.click(await screen.findByRole('button', { name: stored.title! }));
    expect(
      await within(panel()).findByRole('heading', { name: 'HDFC Bank overview' }),
    ).toBeInTheDocument();
  });
});

describe('the overview card', () => {
  const history = [
    { date: '2025-10-01', close: '80' },
    { date: '2026-06-29', close: '95' },
    { date: '2026-08-28', close: '99' },
    { date: '2026-09-28', close: '101.5' },
  ];
  const withPrices = () => install({ prices: { RELIANCE: demoPrices('RELIANCE', { history }) } });
  const chartRows = () =>
    within(screen.getByRole('table', { name: /Share price of/ })).getAllByRole('row').length - 1;

  it('shows the last close, its change, the chart and where the price is from', async () => {
    withPrices();
    render(<ChatView />);
    const overview = await screen.findByRole('region', { name: 'Reliance overview' });
    expect(await within(overview).findAllByText('₹101.50')).not.toHaveLength(0);
    expect(within(overview).getByText('+1.5%')).toHaveClass('rise');
    expect(within(overview).getByText('RELIANCE')).toBeInTheDocument();
    expect(within(overview).getByText('Reliance Industries')).toBeInTheDocument();
    // The card's title is on screen, as in the owner's layout (no breadcrumb).
    expect(within(overview).getByRole('heading', { name: 'Reliance overview' })).not.toHaveClass(
      'visually-hidden',
    );
    expect(within(overview).queryByRole('navigation', { name: 'Breadcrumb' })).toBeNull();
    expect(within(overview).getByText('As of 28 Sep 2026, BSE end of day')).toBeInTheDocument();
    expect(within(overview).getByRole('img', { name: /Share price of/ })).toBeInTheDocument();
    // a marker on every stored close
    expect(overview.querySelectorAll('.chart-marker')).toHaveLength(history.length);
    expect(within(overview).getByRole('link', { name: 'Source' })).toHaveAttribute(
      'href',
      demoPrices('RELIANCE').latest?.citation.url,
    );
  });

  it('shows a fall in the fall colour', async () => {
    const prices = demoPrices('RELIANCE');
    install({
      prices: {
        RELIANCE: {
          ...prices,
          latest: prices.latest && { ...prices.latest, change_pct: '-0.8' },
        },
      },
    });
    render(<ChatView />);
    expect(await screen.findByText('−0.8%')).toHaveClass('fall');
  });

  it('shows every stored close, with no range buttons (BSE keeps about a month)', async () => {
    withPrices();
    render(<ChatView />);
    const overview = await screen.findByRole('region', { name: 'Reliance overview' });
    await within(overview).findAllByText('₹101.50');
    expect(chartRows()).toBe(4);
    for (const name of ['1M', '3M', '6M', '1Y', '1D', '1W']) {
      expect(within(overview).queryByRole('button', { name })).toBeNull();
    }
    expect(within(overview).queryByRole('group', { name: 'Chart range' })).toBeNull();
  });

  it('says how far back the prices go with just the first and last dates (the owner)', async () => {
    install({ prices: { RELIANCE: demoPrices('RELIANCE') } }); // five days: 22 to 28 Sep 2026
    render(<ChatView />);
    const overview = await screen.findByRole('region', { name: 'Reliance overview' });
    await within(overview).findByRole('img', { name: /Share price of/ });
    const dates = overview.querySelector('.price-chart-dates');
    expect(dates).toHaveTextContent('22 Sep 2026');
    expect(dates).toHaveTextContent('28 Sep 2026');
    expect(within(overview).queryByText(/BSE keeps about a month/)).toBeNull();
  });

  it('says prices are not loaded yet, with no chart', async () => {
    install({ prices: { RELIANCE: noPrices('RELIANCE') } });
    render(<ChatView />);
    const overview = await screen.findByRole('region', { name: 'Reliance overview' });
    expect(await within(overview).findByText('Prices not loaded yet')).toBeInTheDocument();
    expect(within(overview).queryByRole('img')).toBeNull();
    expect(within(overview).queryByRole('button', { name: '1M' })).toBeNull();
  });

  it('says so quietly when prices cannot be loaded, without an alert', async () => {
    const api = install();
    api.failWith('GET /api/v1/stocks/RELIANCE/prices', 500);
    render(<ChatView />);
    const overview = await screen.findByRole('region', { name: 'Reliance overview' });
    expect(await within(overview).findByText("Prices couldn't be loaded.")).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('the layout', () => {
  it('puts the conversation first (the left, larger column) and the stock beside it', async () => {
    install();
    render(<ChatView />);
    const conversation = await screen.findByRole('region', { name: 'Conversation' });
    // The chat is the main feature (the owner, 3:2): it comes first, the stock panel after it.
    expect(
      conversation.compareDocumentPosition(panel()) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('fills the window: the messages scroll, the question box stays at the bottom', async () => {
    install();
    render(<ChatView />);
    const conversation = await screen.findByRole('region', { name: 'Conversation' });
    expect(screen.getByRole('main')).toHaveClass('page-fill');
    const scroller = thread().closest('.chat-scroll');
    expect(scroller).not.toBeNull();
    expect(conversation).toContainElement(scroller as HTMLElement);
    // The question box is outside the scrolling part, so it never scrolls away.
    expect(scroller).not.toContainElement(box());
    expect(conversation).toContainElement(box());
  });

  it('keeps the newest message in view', async () => {
    install();
    render(<ChatView />);
    const scroller = (await screen.findByRole('list', { name: 'Messages' })).closest(
      '.chat-scroll',
    ) as HTMLElement;
    let top = 0;
    Object.defineProperty(scroller, 'scrollHeight', { configurable: true, get: () => 900 });
    Object.defineProperty(scroller, 'scrollTop', {
      configurable: true,
      get: () => top,
      set: (value: number) => {
        top = value;
      },
    });
    await ask('How is TCS doing?');
    await screen.findAllByText(/reported revenue of/);
    expect(top).toBe(900);
  });

  it('has the stock tabs on top of the panel, then the overview, then the key metrics', async () => {
    install();
    render(<ChatView />);
    const group = await within(
      await screen.findByRole('complementary', { name: 'Stock context' }),
    ).findByRole('group', { name: 'Stock' });
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['Reliance', 'TCS', 'HDFC Bank']);
    const overview = screen.getByRole('region', { name: 'Reliance overview' });
    const metrics = await screen.findByRole('region', { name: /^Key metrics/ });
    const follows = (a: Node, b: Node) =>
      a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING;
    expect(follows(group, overview)).toBeTruthy();
    expect(follows(overview, metrics)).toBeTruthy();
  });
});

describe('the key metrics card', () => {
  const insights = demoInsights({
    symbol: 'RELIANCE',
    name: 'Reliance Industries Limited',
    key_facts: [
      demoFact(),
      demoFact({
        metric: 'net_profit',
        label: 'Net profit',
        value: '12345.5000',
        citation: demoScreenerCitation(),
      }),
      demoFact({ metric: 'eps_basic', label: 'Basic EPS', unit: 'INR_PER_SHARE', value: '45.6' }),
    ],
  });

  it('shows the latest full year in the title, each figure with its change and source', async () => {
    install({ insights: { RELIANCE: insights } });
    render(<ChatView />);
    const card = await screen.findByRole('region', { name: 'Key metrics (FY2026)' });
    const rows = within(card).getAllByRole('listitem');
    expect(rows).toHaveLength(3);
    expect(within(card).getByRole('link', { name: /View details/ })).toHaveAttribute(
      'href',
      '/stock/?symbol=RELIANCE',
    );
    expect(rows.map((row) => row.querySelector('.chat-metric-icon')?.classList[1])).toEqual([
      'revenue',
      'profit',
      'eps',
    ]);
    expect(rows[0]).toHaveTextContent('Revenue from operations');
    expect(rows[0]).toHaveTextContent('₹1,23,456 crore');
    expect(within(rows[0] as HTMLElement).getByText('+12.5%')).toHaveClass('rise');
    expect(rows[1]).toHaveTextContent('₹12,345.5 crore');
    expect(rows[2]).toHaveTextContent('₹45.60 per share');
    // Each figure links to its source with just the link icon (the owner), named for a screen
    // reader and on hover.
    const screener = within(rows[1] as HTMLElement).getByRole('link', { name: 'screener.in' });
    expect(screener).toHaveAttribute('href', 'https://www.screener.in/company/DEMOA/consolidated/');
    expect(screener).toHaveTextContent(/^$/);
    expect(screener.querySelector('svg')).not.toBeNull();
    expect(screener).toHaveAttribute('title');
    expect(within(rows[0] as HTMLElement).getByRole('link', { name: 'Filing p.44' })).toBeVisible();
  });

  it('shows nothing invented when no figures are stored', async () => {
    install();
    render(<ChatView />);
    const card = await screen.findByRole('region', { name: 'Key metrics' });
    expect(await within(card).findByText('No key figures stored yet.')).toBeInTheDocument();
    expect(within(card).queryByRole('listitem')).toBeNull();
  });

  it('shows a source without an address as the same icon, named, but not a link', async () => {
    install({
      insights: {
        RELIANCE: demoInsights({
          key_facts: [demoFact({ citation: { ...demoScreenerCitation(), url: null } })],
        }),
      },
    });
    render(<ChatView />);
    const card = await screen.findByRole('region', { name: /^Key metrics/ });
    await within(card).findAllByRole('listitem');
    expect(within(card).getByRole('img', { name: 'screener.in' })).toHaveTextContent(/^$/);
    expect(within(card).queryByRole('link', { name: 'screener.in' })).toBeNull();
  });

  it('has no operating margin', async () => {
    install({ insights: { RELIANCE: insights } });
    render(<ChatView />);
    await screen.findByRole('region', { name: 'Key metrics (FY2026)' });
    expect(screen.queryByText(/operating margin/i)).toBeNull();
  });
});

describe('what the panel no longer holds', () => {
  it('has no news card and no investor profile, only the stock, overview and key metrics', async () => {
    install({
      profileFields: [demoProfileField()],
      insights: { RELIANCE: demoInsights() },
    });
    render(<ChatView />);
    await screen.findByRole('region', { name: 'Key metrics (FY2026)' });
    expect(within(panel()).getByRole('region', { name: 'Reliance overview' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Recent news' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'Your investor profile' })).toBeNull();
    expect(screen.queryByText('DemoCo Alpha reported higher quarterly revenue.')).toBeNull();
  });
});

describe('the words on the page', () => {
  it('never claim live data or advice', async () => {
    install({
      prices: { RELIANCE: demoPrices('RELIANCE') },
      insights: { RELIANCE: demoInsights() },
      chatAnswer: () => demoAnswer({ table: demoTable() }),
    });
    render(<ChatView />);
    await ask('How did revenue grow?');
    await screen.findByRole('group', { name: 'Follow-up suggestions' });
    await screen.findAllByText('₹101.50');

    const words = (document.body.textContent ?? '').replace(/not live/gi, '');
    expect(words).not.toMatch(/\blive\b/i);
    expect(words).not.toMatch(/\b(buy|sell)\b/i);
    expect(words).not.toMatch(/\bNSE\b/);
    expect(words).not.toMatch(/deep research/i);
  });
});

describe('a question asked back', () => {
  const askedBack = (question: string) =>
    question === 'What is the latest revenue?'
      ? demoAnswer({
          text: 'Which company do you mean: TCS, HDFC Bank, or Reliance?',
          sources: [],
          choices: [
            { label: 'TCS', question: 'What is the latest revenue for TCS?' },
            { label: 'Reliance', question: 'What is the latest revenue for Reliance?' },
          ],
        })
      : demoAnswer();

  it('offers its choices in place of the follow-up suggestions', async () => {
    install({ chatAnswer: askedBack });
    render(<ChatView />);
    await ask('What is the latest revenue?');
    const group = await screen.findByRole('group', { name: 'Choices' });
    expect(
      within(group)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['TCS', 'Reliance']);
    expect(screen.queryByRole('group', { name: 'Follow-up suggestions' })).toBeNull();
  });

  it('sends the chosen question at once, with nothing to type or submit', async () => {
    const api = install({ chatAnswer: askedBack });
    render(<ChatView />);
    await ask('What is the latest revenue?');
    const group = await screen.findByRole('group', { name: 'Choices' });
    await userEvent.click(within(group).getByRole('button', { name: 'Reliance' }));
    await screen.findByText(/reported revenue of/);
    expect(api.bodies.map((b) => (b.body as { question: string }).question)).toEqual([
      'What is the latest revenue?',
      'What is the latest revenue for Reliance?',
    ]);
    expect(within(thread()).getByText('What is the latest revenue for Reliance?')).toBeVisible();
    expect(box()).toHaveValue('');
    // an answered question offers the usual follow-ups again
    expect(screen.queryByRole('group', { name: 'Choices' })).toBeNull();
  });
});
