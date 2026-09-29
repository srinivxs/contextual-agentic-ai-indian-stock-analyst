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
    // Nothing is altered: the words are all still there.
    expect(card.querySelector('.chat-text')?.textContent).toBe(
      demoAnswer().text.replace(/\[(\d)\]/g, '[$1]'),
    );
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
    expect(within(overview).getByText('As of 28 Sep 2026, BSE end of day')).toBeInTheDocument();
    expect(within(overview).getByRole('img', { name: /Share price of/ })).toBeInTheDocument();
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

  it('has range tabs that filter the history, 1Y being the default', async () => {
    withPrices();
    render(<ChatView />);
    const overview = await screen.findByRole('region', { name: 'Reliance overview' });
    await within(overview).findAllByText('₹101.50');
    expect(
      within(overview)
        .getAllByRole('button', { pressed: true })
        .map((b) => b.textContent),
    ).toContain('1Y');
    expect(chartRows()).toBe(4);

    await userEvent.click(within(overview).getByRole('button', { name: '3M' }));
    expect(chartRows()).toBe(3);
    await userEvent.click(within(overview).getByRole('button', { name: '1M' }));
    expect(chartRows()).toBe(2);
    expect(within(overview).getByRole('button', { name: '1M' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(overview).queryByRole('button', { name: '1D' })).toBeNull();
    expect(within(overview).queryByRole('button', { name: '1W' })).toBeNull();
  });

  it('greys out ranges the history does not reach yet, and says how far back it goes', async () => {
    install({ prices: { RELIANCE: demoPrices('RELIANCE') } }); // five days: 22 to 28 Sep 2026
    render(<ChatView />);
    const overview = await screen.findByRole('region', { name: 'Reliance overview' });
    await within(overview).findAllByText('₹101.50');
    for (const name of ['3M', '6M', '1Y']) {
      const button = within(overview).getByRole('button', { name });
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute('title', 'Not enough history yet');
    }
    expect(within(overview).getByRole('button', { name: '1M' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(
      within(overview).getByText(
        'Prices from 22 Sep 2026 so far; the rest of the year is still being fetched.',
      ),
    ).toBeInTheDocument();
  });

  it('says prices are not loaded yet, with no chart and no range tabs', async () => {
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
    expect(rows[0]).toHaveTextContent('Revenue from operations');
    expect(rows[0]).toHaveTextContent('₹1,23,456 crore');
    expect(within(rows[0] as HTMLElement).getByText('+12.5%')).toHaveClass('rise');
    expect(rows[1]).toHaveTextContent('₹12,345.5 crore');
    expect(rows[2]).toHaveTextContent('₹45.60 per share');
    // Each figure names its source; the screener one links to its page.
    expect(
      within(rows[1] as HTMLElement).getByRole('link', { name: 'screener.in' }),
    ).toHaveAttribute('href', 'https://www.screener.in/company/DEMOA/consolidated/');
    expect(within(rows[0] as HTMLElement).getByRole('link', { name: 'Filing p.44' })).toBeVisible();
  });

  it('shows nothing invented when no figures are stored', async () => {
    install();
    render(<ChatView />);
    const card = await screen.findByRole('region', { name: 'Key metrics' });
    expect(await within(card).findByText('No key figures stored yet.')).toBeInTheDocument();
    expect(within(card).queryByRole('listitem')).toBeNull();
  });

  it('has no operating margin', async () => {
    install({ insights: { RELIANCE: insights } });
    render(<ChatView />);
    await screen.findByRole('region', { name: 'Key metrics (FY2026)' });
    expect(screen.queryByText(/operating margin/i)).toBeNull();
  });
});

describe('the recent news card', () => {
  const event = (over: object) => ({ ...demoInsights().events[0]!, ...over });

  it('lists the newest three events with source, date, summary and a safe link', async () => {
    install({
      insights: {
        RELIANCE: demoInsights({
          events: [
            event({ event_date: '2026-01-01', summary: 'Oldest one' }),
            event({ event_date: '2026-07-01', summary: 'Newest one' }),
            event({ event_date: '2026-05-01', summary: 'Middle one' }),
            event({ event_date: '2026-03-01', summary: 'Fourth one' }),
          ],
        }),
      },
    });
    render(<ChatView />);
    const card = await screen.findByRole('region', { name: 'Recent news' });
    await within(card).findByText('Newest one');
    const items = within(card).getAllByRole('listitem');
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent('Newest one');
    expect(items[0]).toHaveTextContent('01 Jul 2026');
    expect(items[0]).toHaveTextContent('Announcement · Results · p.2');
    expect(within(card).queryByText('Oldest one')).toBeNull();
    expect(
      within(items[0] as HTMLElement).getByRole('link', { name: 'Newest one' }),
    ).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('shows an event with an unsafe link as plain text', async () => {
    install({
      insights: {
        RELIANCE: demoInsights({
          events: [
            event({
              summary: 'Odd link',
              citation: { ...demoInsights().events[0]!.citation, url: 'javascript:alert(1)' },
            }),
          ],
        }),
      },
    });
    render(<ChatView />);
    const card = await screen.findByRole('region', { name: 'Recent news' });
    expect(await within(card).findByText('Odd link')).toBeInTheDocument();
    expect(within(card).queryByRole('link')).toBeNull();
  });

  it('says so when there are no events, and has no publisher news', async () => {
    install();
    render(<ChatView />);
    const card = await screen.findByRole('region', { name: 'Recent news' });
    expect(await within(card).findByText('No events stored for Reliance yet.')).toBeInTheDocument();
    expect(screen.queryByText(/Economic Times/i)).toBeNull();
  });
});

describe('the investor profile in the panel', () => {
  it('is at the bottom of the panel and can still be edited', async () => {
    install({ profileFields: [demoProfileField()] });
    render(<ChatView />);
    const memory = await screen.findByRole('region', { name: 'Your investor profile' });
    expect(panel()).toContainElement(memory);
    expect(await within(memory).findByText('Conservative')).toBeInTheDocument();
    await userEvent.click(within(memory).getByRole('button', { name: 'Edit Risk' }));
    expect(within(memory).getByRole('button', { name: 'Save Risk' })).toBeInTheDocument();
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
