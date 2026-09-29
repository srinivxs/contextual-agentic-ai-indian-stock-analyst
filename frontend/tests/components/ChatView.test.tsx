import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ChatView } from '@/components/ChatView';
import {
  CHAT_STOCKS,
  demoAnswer,
  demoChatSource,
  demoConversation,
  demoId,
  demoProfileField,
  demoQuestion,
  installFakeApi,
  type Options,
} from '../helpers/fakeApi';

// The real router object is stable between renders, so the mock's must be too.
const nav = vi.hoisted(() => {
  const replace = vi.fn();
  return { replace, router: { replace } };
});
vi.mock('next/navigation', () => ({ useRouter: () => nav.router }));

beforeEach(() => {
  nav.replace.mockReset();
});

const SEND = 'POST /api/v1/chat/messages';

/** The fake API with the three real stocks, which the side panel asks about. */
const install = (options: Options = {}) => installFakeApi({ stocks: CHAT_STOCKS, ...options });

/** Open the conversations menu in the header. */
async function openMenu(): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole('button', { name: 'Conversations' }));
  return screen.getByRole('group', { name: 'Conversation list' });
}

const box = (): HTMLElement => screen.getByRole('textbox', { name: 'Your question' });
const sendButton = (): HTMLElement => screen.getByRole('button', { name: 'Send' });
const thread = (): HTMLElement => screen.getByRole('list', { name: 'Messages' });

/** Wait for the page to be ready, then type a question and press Send. */
async function ask(question: string): Promise<void> {
  await userEvent.type(await screen.findByRole('textbox', { name: 'Your question' }), question);
  await userEvent.click(sendButton());
}

describe('the chat page', () => {
  it('is in the main navigation, beside Stocks and Documents', async () => {
    install();
    render(<ChatView />);

    const main = await screen.findByRole('navigation', { name: 'Main' });
    expect(within(main).getByRole('link', { name: 'Chat' })).toHaveAttribute('href', '/chat/');
    expect(within(main).getByRole('link', { name: 'Stocks' })).toBeInTheDocument();
    expect(within(main).getByRole('link', { name: 'Documents' })).toBeInTheDocument();
  });

  it('titles the chat, and says honestly where answers come from', async () => {
    install();
    render(<ChatView />);

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Chat with your analyst' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Answers only from stored data')).toBeInTheDocument();
    expect(screen.queryByText(/live Indian market data/)).toBeNull();
  });

  it('marks Chat as the current menu item', async () => {
    install();
    render(<ChatView />);
    const main = await screen.findByRole('navigation', { name: 'Main' });
    expect(within(main).getByRole('link', { name: 'Chat' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('greets an empty conversation and says what the analyst can do', async () => {
    install();
    render(<ChatView />);
    expect(
      await screen.findByText(/I can answer from the filings of RELIANCE/),
    ).toBeInTheDocument();
    expect(screen.getByText(/how each stock fits your preferences/)).toBeInTheDocument();
  });

  it('offers suggestions that fill the box without sending, and never advice prompts', async () => {
    const api = install();
    render(<ChatView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Compare TCS and HDFC Bank' }));

    expect(box()).toHaveValue('Compare TCS and HDFC Bank');
    expect(api.bodies).toEqual([]);
    for (const name of [
      'Analyse RELIANCE',
      'Latest news on TCS',
      'Which stocks have low debt?',
      'Match me',
    ]) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument();
    }
    expect(screen.queryByText(/should I buy/i)).toBeNull();
  });

  it('hides the greeting and suggestions once a conversation has begun', async () => {
    install();
    render(<ChatView />);
    await ask('How did revenue grow?');
    await screen.findByText(/reported revenue of/);
    expect(screen.queryByRole('button', { name: 'Match me' })).toBeNull();
    expect(screen.queryByText(/I can answer from the filings/)).toBeNull();
  });

  it('starts the box with the text of ?q=, and does not send it', async () => {
    const api = install();
    window.history.pushState({}, '', '/chat/?q=Analyse%20RELIANCE');
    try {
      render(<ChatView />);
      expect(await screen.findByRole('textbox', { name: 'Your question' })).toHaveValue(
        'Analyse RELIANCE',
      );
      expect(api.bodies).toEqual([]);
    } finally {
      window.history.pushState({}, '', '/');
    }
  });

  it('starts with an empty box when there is no ?q=', async () => {
    install();
    render(<ChatView />);
    expect(await screen.findByRole('textbox', { name: 'Your question' })).toHaveValue('');
  });

  it('says so when there are no conversations yet', async () => {
    install();
    render(<ChatView />);
    const menu = await openMenu();
    expect(within(menu).getByText('No conversations yet.')).toBeInTheDocument();
  });

  it('says "not investment advice" once, in the menu, and shows no profile panel', async () => {
    install({ profileFields: [demoProfileField()] });
    render(<ChatView />);

    expect(await screen.findByText('Enter sends, Shift+Enter adds a line.')).toBeInTheDocument();
    // the owner: one disclaimer is enough, the menu's note on every page
    expect(screen.getAllByText(/Not investment advice/)).toHaveLength(1);
    expect(screen.queryByText(/Answers come only from stored filings/)).toBeNull();
    expect(screen.queryByRole('region', { name: 'Your investor profile' })).toBeNull();
  });
});

describe('asking a question', () => {
  it('needs three characters that are not spaces before Send works', async () => {
    install();
    render(<ChatView />);

    await userEvent.type(await screen.findByRole('textbox', { name: 'Your question' }), '  ab  ');
    expect(sendButton()).toBeDisabled();
    await userEvent.type(box(), 'c');
    expect(sendButton()).toBeEnabled();
  });

  it('shows the question at once, says it is thinking, and waits with the box off', async () => {
    const api = install();
    const release = api.hold(SEND);
    render(<ChatView />);

    await ask('How much revenue did DemoCo Alpha report?');

    expect(await screen.findByRole('status')).toHaveTextContent('Thinking…');
    expect(within(thread()).getByText('How much revenue did DemoCo Alpha report?')).toBeVisible();
    expect(box()).toBeDisabled();
    expect(sendButton()).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Conversations' })).toBeDisabled();

    release();
    expect(await screen.findByText(/reported revenue of/)).toBeInTheDocument();
    expect(screen.queryByText('Thinking…')).toBeNull();
    expect(box()).toBeEnabled();
    expect(box()).toHaveValue('');
    // The question is shown once, now as stored, not twice.
    expect(within(thread()).getAllByText('How much revenue did DemoCo Alpha report?')).toHaveLength(
      1,
    );
  });

  it('sends the question trimmed, and a new conversation as null', async () => {
    const api = install();
    render(<ChatView />);

    await ask('  How did revenue grow?  ');
    await screen.findByText(/reported revenue of/);

    expect(api.bodies).toEqual([
      { key: SEND, body: { question: 'How did revenue grow?', conversation_id: null } },
    ]);
  });

  it('continues the same conversation, and lists it with the others', async () => {
    const api = install();
    render(<ChatView />);

    await ask('How did revenue grow?');
    await screen.findByText(/reported revenue of/);
    const list = await openMenu();
    expect(
      await within(list).findByRole('button', { name: 'How did revenue grow?' }),
    ).toHaveAttribute('aria-current', 'true');

    await ask('And net profit?');
    await waitFor(() => expect(api.bodies).toHaveLength(2));
    const [id] = [...api.conversations.keys()];
    expect(api.bodies[1]?.body).toEqual({ question: 'And net profit?', conversation_id: id });
    // Two questions and two answers (each answer's sources are a list of their own inside it).
    await waitFor(() => expect(thread().children).toHaveLength(4));
  });

  it('sends with Enter, and Shift+Enter starts a new line instead', async () => {
    const api = install();
    render(<ChatView />);

    await userEvent.type(
      await screen.findByRole('textbox', { name: 'Your question' }),
      'Revenue{Shift>}{Enter}{/Shift}growth',
    );
    expect(box()).toHaveValue('Revenue\ngrowth');
    expect(api.bodies).toEqual([]);

    await userEvent.type(box(), '{Enter}');
    await screen.findByText(/reported revenue of/);
    expect(api.bodies[0]?.body).toEqual({ question: 'Revenue\ngrowth', conversation_id: null });
  });

  it('does not send with Enter before the question is long enough', async () => {
    const api = install();
    render(<ChatView />);

    await userEvent.type(
      await screen.findByRole('textbox', { name: 'Your question' }),
      'ab{Enter}',
    );
    expect(api.bodies).toEqual([]);
  });
});

describe('an answer’s sources', () => {
  async function answered(answer = demoAnswer()): Promise<HTMLElement> {
    install({ chatAnswer: () => answer });
    render(<ChatView />);
    await ask('How much revenue did DemoCo Alpha report?');
    return screen.findByRole('list', { name: 'Sources' });
  }

  it('lists each source with its number and label', async () => {
    const sources = await answered();
    const items = within(sources).getAllByRole('listitem');
    expect(items).toHaveLength(3);
    const numbers = items.map((item) => item.querySelector('.chat-source-marker')?.textContent);
    expect(numbers).toEqual(['1', '2', '3']);
    expect(items[0]).toHaveTextContent('Annual report · Annual Report 2026 · p.44');
  });

  it('links a filing and a screener figure, each in a new tab, safely', async () => {
    const sources = await answered();

    const filing = within(sources).getByRole('link', {
      name: 'Annual report · Annual Report 2026 · p.44',
    });
    expect(filing).toHaveAttribute('href', demoChatSource().url);
    expect(filing).toHaveAttribute('target', '_blank');
    expect(filing).toHaveAttribute('rel', 'noopener noreferrer');

    const screener = within(sources).getByRole('link', {
      name: 'screener.in · profit-loss · Net Profit · Mar 2026',
    });
    expect(screener).toHaveAttribute('href', 'https://www.screener.in/company/DEMOA/consolidated/');
    expect(screener).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('shows the filing’s own words under the source', async () => {
    const sources = await answered();
    const quote = within(sources).getByText('Revenue from operations stood at ₹1,23,456 crore.');
    expect(quote.closest('details')).not.toBeNull();
  });

  it('shows a computed value as "Computed", with no link', async () => {
    const sources = await answered();
    const computed = within(sources).getAllByRole('listitem')[2] as HTMLElement;
    expect(within(computed).getByText('Computed')).toBeInTheDocument();
    expect(within(computed).getByText('Revenue growth, FY2026 against FY2025')).toBeInTheDocument();
    expect(within(computed).queryByRole('link')).toBeNull();
  });

  it('marks an RBI source and links it to rbi.org.in', async () => {
    const sources = await answered(
      demoAnswer({
        text: 'The RBI said so [1].',
        sources: [
          demoChatSource({
            source: 'rbi',
            label: 'RBI press release · 12 Sep 2026',
            url: 'https://www.rbi.org.in/Scripts/x.aspx?prid=1',
          }),
        ],
      }),
    );
    expect(within(sources).getByText('RBI')).toBeInTheDocument();
    const link = within(sources).getByRole('link', { name: 'RBI press release · 12 Sep 2026' });
    expect(link).toHaveAttribute('href', 'https://www.rbi.org.in/Scripts/x.aspx?prid=1');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('shows an RBI source on another host as plain text', async () => {
    const sources = await answered(
      demoAnswer({
        text: 'The RBI said so [1].',
        sources: [
          demoChatSource({
            source: 'rbi',
            label: 'RBI press release · 12 Sep 2026',
            url: 'https://www.bseindia.com/x.pdf',
          }),
        ],
      }),
    );
    expect(within(sources).getByText('RBI press release · 12 Sep 2026')).toBeInTheDocument();
    expect(within(sources).queryByRole('link')).toBeNull();
  });

  it('shows a source it may not link to as plain text', async () => {
    const sources = await answered(
      demoAnswer({
        text: 'Revenue rose [1].',
        sources: [demoChatSource({ label: 'Odd source', url: 'javascript:alert(1)' })],
      }),
    );
    expect(within(sources).getByText('Odd source')).toBeInTheDocument();
    expect(within(sources).queryByRole('link')).toBeNull();
  });

  it('turns each marker into a button that highlights and focuses its source', async () => {
    const sources = await answered();
    const items = within(sources).getAllByRole('listitem');

    const marker = screen.getByRole('button', { name: 'Source 2' });
    expect(marker).toHaveTextContent('[2]');
    expect(marker).toHaveAttribute('aria-controls', items[1]?.id);
    await userEvent.click(marker);

    expect(items[1]).toHaveFocus();
    expect(items[1]).toHaveClass('highlighted');
    expect(items[0]).not.toHaveClass('highlighted');

    await userEvent.click(screen.getByRole('button', { name: 'Source 1' }));
    expect(items[0]).toHaveClass('highlighted');
    expect(items[1]).not.toHaveClass('highlighted');
  });

  it('shows each point of an answer on its own line, with space between', async () => {
    install({
      chatAnswer: () =>
        demoAnswer({
          text: 'Margins held up. [1] Cash conversion was strong. [2]',
          sources: [demoChatSource({ marker: 1 }), demoChatSource({ marker: 2, label: 'Two' })],
        }),
    });
    render(<ChatView />);
    await ask('Why did profit grow?');
    const points = await screen.findByRole('list', { name: 'Answer' });
    const items = within(points).getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('Margins held up. [1]');
    expect(items[1]).toHaveTextContent('Cash conversion was strong. [2]');
  });

  it('leaves a marker without a source as plain text', async () => {
    await answered(demoAnswer({ text: 'Revenue rose [1] and [7].', sources: [demoChatSource()] }));
    expect(screen.getByRole('button', { name: 'Source 1' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Source 7' })).toBeNull();
    expect(screen.getByText(/and \[7\]\./)).toBeInTheDocument();
  });
});

describe('answers that are not answers', () => {
  it.each([
    ['abstained', "I don't have that in the data."],
    ['out_of_scope', 'I currently have research data only for Reliance, TCS and HDFC Bank.'],
    ['remembered', "Noted. I'll remember: Risk: Conservative; Debt: Avoid high debt."],
  ] as const)('shows %s as a quiet notice, not an error', async (status, text) => {
    install({ chatAnswer: () => demoAnswer({ status, text, sources: [] }) });
    render(<ChatView />);
    await ask('What is the share price?');

    const notice = await screen.findByText(text);
    expect(notice).toHaveClass('chat-notice');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByRole('list', { name: 'Sources' })).toBeNull();
  });

  it('links a remembered reply to the Match page, and no other reply', async () => {
    install({
      chatAnswer: () =>
        demoAnswer({
          status: 'remembered',
          text: "Noted. I'll remember: Risk: Conservative.",
          sources: [],
        }),
    });
    render(<ChatView />);

    await ask("I'm conservative and dividend-focused.");
    await screen.findByText("Noted. I'll remember: Risk: Conservative.");
    expect(screen.getByRole('link', { name: 'See it on Match' })).toHaveAttribute(
      'href',
      '/match/',
    );
  });

  it('has no Match link on an ordinary answer', async () => {
    install();
    render(<ChatView />);
    await ask('How did revenue grow?');
    await screen.findByRole('list', { name: 'Sources' });
    expect(screen.queryByRole('link', { name: 'See it on Match' })).toBeNull();
  });
});

describe('when the chat cannot answer', () => {
  it('says the chat is switched off (409)', async () => {
    install({ chatOff: true });
    render(<ChatView />);
    await ask('How did revenue grow?');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The chat is switched off on this server.',
    );
  });

  it('says to try again when the chat is unavailable (503), and keeps the question', async () => {
    install({ chatUnavailable: true });
    render(<ChatView />);
    await ask('How did revenue grow?');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "The chat isn't available right now. Try again in a moment.",
    );
    expect(box()).toHaveValue('How did revenue grow?');
    expect(within(thread()).queryByText('How did revenue grow?')).toBeNull();
    expect(screen.queryByText('Thinking…')).toBeNull();
  });

  it('shows a general error for anything else, without technical detail', async () => {
    const api = install();
    api.failWith(SEND, 500);
    render(<ChatView />);
    await ask('How did revenue grow?');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Something went wrong. Try again.');
    expect(alert).not.toHaveTextContent('internal_error');
  });

  it('goes back to the sign-in page when the session has ended', async () => {
    const api = install();
    api.failWith(SEND, 401);
    render(<ChatView />);
    await ask('How did revenue grow?');

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });

  it('clears the error on the next question', async () => {
    const api = install();
    api.failWith(SEND, 500);
    render(<ChatView />);
    await ask('How did revenue grow?');
    await screen.findByRole('alert');

    await userEvent.click(sendButton());
    await screen.findByText(/reported revenue of/);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('conversations', () => {
  const older = demoConversation({
    id: demoId(1),
    title: 'Older question',
    updated_at: '2026-09-26T10:00:00Z',
  });
  const newer = demoConversation({
    id: demoId(2),
    title: 'Newer question',
    updated_at: '2026-09-27T10:00:00Z',
    messages: [
      demoQuestion({ id: demoId(201), text: 'What was the dividend?' }),
      demoAnswer({
        id: demoId(202),
        status: 'abstained',
        text: "I don't have that in the data.",
        sources: [],
      }),
    ],
  });

  it('lists them newest first', async () => {
    install({ conversations: [older, newer] });
    render(<ChatView />);

    const list = await openMenu();
    await within(list).findByRole('button', { name: 'Newer question' });
    const titles = within(list)
      .getAllByRole('button')
      .filter((b) => !b.getAttribute('aria-label')?.startsWith('Delete'))
      .map((b) => b.textContent);
    expect(titles).toEqual(['New chat', 'Newer question', 'Older question']);
  });

  it('names a conversation without a title', async () => {
    install({ conversations: [demoConversation({ title: null })] });
    render(<ChatView />);
    await openMenu();
    expect(await screen.findByRole('button', { name: 'Untitled conversation' })).toBeVisible();
  });

  it('opens one, showing its messages, and switches to another', async () => {
    const api = install({ conversations: [older, newer] });
    render(<ChatView />);

    await openMenu();
    await userEvent.click(await screen.findByRole('button', { name: 'Older question' }));
    expect(await within(thread()).findByText(/reported revenue of/)).toBeInTheDocument();
    // Picking one closes the menu; opening it again marks the one on screen.
    expect(screen.queryByRole('group', { name: 'Conversation list' })).toBeNull();
    await openMenu();
    expect(screen.getByRole('button', { name: 'Older question' })).toHaveAttribute(
      'aria-current',
      'true',
    );
    expect(api.requests).toContain(`GET /api/v1/chat/conversations/${demoId(1)}`);

    await userEvent.click(screen.getByRole('button', { name: 'Newer question' }));
    expect(await within(thread()).findByText('What was the dividend?')).toBeInTheDocument();
    expect(within(thread()).queryByText(/reported revenue of/)).toBeNull();
    await openMenu();
    expect(screen.getByRole('button', { name: 'Older question' })).not.toHaveAttribute(
      'aria-current',
    );
  });

  it('continues an opened conversation', async () => {
    const api = install({ conversations: [older] });
    render(<ChatView />);

    await openMenu();
    await userEvent.click(await screen.findByRole('button', { name: 'Older question' }));
    await within(thread()).findByText(/reported revenue of/);
    await ask('And net profit?');

    await waitFor(() =>
      expect(api.bodies[0]?.body).toEqual({
        question: 'And net profit?',
        conversation_id: demoId(1),
      }),
    );
  });

  it('starts afresh with New conversation', async () => {
    const api = install({ conversations: [older] });
    render(<ChatView />);

    await openMenu();
    await userEvent.click(await screen.findByRole('button', { name: 'Older question' }));
    await within(thread()).findByText(/reported revenue of/);
    await openMenu();
    await userEvent.click(screen.getByRole('button', { name: 'New chat' }));

    expect(screen.queryByText(/reported revenue of/)).toBeNull();
    await ask('How did revenue grow?');
    await waitFor(() =>
      expect(api.bodies[0]?.body).toEqual({
        question: 'How did revenue grow?',
        conversation_id: null,
      }),
    );
  });

  it('says so when a conversation cannot be opened', async () => {
    const api = install({ conversations: [older] });
    api.failWith(`GET /api/v1/chat/conversations/${demoId(1)}`, 404);
    render(<ChatView />);

    await openMenu();
    await userEvent.click(await screen.findByRole('button', { name: 'Older question' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "We couldn't open that conversation.",
    );
  });

  it('says so when the list cannot be loaded, and still lets you ask', async () => {
    const api = install();
    api.failWith('GET /api/v1/chat/conversations', 500);
    render(<ChatView />);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "We couldn't load your conversations.",
    );
    await ask('How did revenue grow?');
    expect(await screen.findByText(/reported revenue of/)).toBeInTheDocument();
  });

  it('ignores a conversation that arrives after you moved on', async () => {
    const api = install({ conversations: [older, newer] });
    const release = api.hold(`GET /api/v1/chat/conversations/${demoId(1)}`);
    render(<ChatView />);

    await openMenu();
    await userEvent.click(await screen.findByRole('button', { name: 'Older question' }));
    expect(await screen.findByText('Loading the conversation…')).toBeInTheDocument();
    await openMenu();
    await userEvent.click(screen.getByRole('button', { name: 'Newer question' }));
    await within(thread()).findByText('What was the dividend?');

    release();
    await waitFor(() => expect(api.requests.filter((r) => r.includes(demoId(1)))).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(within(thread()).queryByText(/reported revenue of/)).toBeNull();
  });
});

describe('safety and sessions', () => {
  it('renders every text as text, never as HTML', async () => {
    const evil = '<img src=x onerror=alert(1)>';
    install({
      conversations: [demoConversation({ title: evil })],
      chatAnswer: () =>
        demoAnswer({
          text: `${evil} [1]`,
          sources: [demoChatSource({ label: evil, quote: evil })],
        }),
    });
    const { container } = render(<ChatView />);

    await ask(evil);
    await screen.findByRole('list', { name: 'Sources' });
    expect(within(thread()).getAllByText(evil).length).toBeGreaterThan(0);
    // The only images on the page are the three company logos in the side panel: nothing the
    // text contained ever became an element.
    expect(thread().querySelector('img')).toBeNull();
    expect(container.querySelector('img[src="x"], img[onerror]')).toBeNull();
  });

  it('sends a signed-out visitor to the sign-in page, asking nothing of the chat', async () => {
    const api = install({ signedIn: false });
    render(<ChatView />);

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
    expect(api.requests.filter((r) => r.includes('/chat/'))).toEqual([]);
  });

  it('goes to the sign-in page when the list says the session has ended', async () => {
    const api = install();
    api.failWith('GET /api/v1/chat/conversations', 401);
    render(<ChatView />);

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });

  it('goes to the sign-in page when opening a conversation finds the session ended', async () => {
    const api = install({ conversations: [demoConversation()] });
    api.failWith(`GET /api/v1/chat/conversations/${demoId(1)}`, 401);
    render(<ChatView />);

    await openMenu();
    await userEvent.click(await screen.findByRole('button', { name: demoConversation().title! }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
  });

  it('shows an error when it cannot find out who is signed in', async () => {
    const api = install();
    api.failWith('GET /api/v1/me', 500);
    render(<ChatView />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn.t load/i);
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('signs out and returns to the sign-in page', async () => {
    const api = install();
    render(<ChatView />);
    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }));

    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/'));
    expect(api.requests).toContain('POST /api/v1/auth/logout');
  });
});

describe('deleting a conversation', () => {
  const TITLE = demoConversation().title ?? '';
  const deleteButton = (menu: HTMLElement) =>
    within(menu).getByRole('button', { name: `Delete conversation: ${TITLE}` });

  it('deletes it after you confirm, and it leaves the list', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const api = install({ conversations: [demoConversation()] });
    render(<ChatView />);

    await userEvent.click(await within(await openMenu()).findByRole('button', { name: TITLE }));
    await screen.findByText(/reported revenue of/);
    await userEvent.click(deleteButton(await openMenu()));

    expect(confirm).toHaveBeenCalledWith(`Delete "${TITLE}"? This cannot be undone.`);
    expect(api.requests).toContain(`DELETE /api/v1/chat/conversations/${demoId(1)}`);
    // the open conversation was the deleted one: the page starts a new chat
    await waitFor(() => expect(screen.queryByText(/reported revenue of/)).toBeNull());
    const menu = await openMenu();
    expect(within(menu).queryByRole('button', { name: TITLE })).toBeNull();
    expect(within(menu).getByText('No conversations yet.')).toBeInTheDocument();
    expect(api.conversations.size).toBe(0);
    confirm.mockRestore();
  });

  it('keeps it when you cancel', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const api = install({ conversations: [demoConversation()] });
    render(<ChatView />);

    const menu = await openMenu();
    await userEvent.click(
      await within(menu).findByRole('button', { name: `Delete conversation: ${TITLE}` }),
    );

    expect(api.requests.some((r) => r.startsWith('DELETE'))).toBe(false);
    expect(within(menu).getByRole('button', { name: TITLE })).toBeInTheDocument();
    confirm.mockRestore();
  });

  it('says so when it could not be deleted', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const api = install({ conversations: [demoConversation()] });
    api.failWith(`DELETE /api/v1/chat/conversations/${demoId(1)}`, 500);
    render(<ChatView />);

    const menu = await openMenu();
    await userEvent.click(
      await within(menu).findByRole('button', { name: `Delete conversation: ${TITLE}` }),
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "We couldn't delete the conversation. Try again in a moment.",
    );
    expect(api.conversations.size).toBe(1);
    confirm.mockRestore();
  });

  it('removes one that is already gone from the list', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const api = install({ conversations: [demoConversation()] });
    api.failWith(`DELETE /api/v1/chat/conversations/${demoId(1)}`, 404);
    render(<ChatView />);

    const menu = await openMenu();
    await userEvent.click(
      await within(menu).findByRole('button', { name: `Delete conversation: ${TITLE}` }),
    );

    await waitFor(() => expect(within(menu).queryByRole('button', { name: TITLE })).toBeNull());
    expect(screen.queryByRole('alert')).toBeNull();
    confirm.mockRestore();
  });
});
