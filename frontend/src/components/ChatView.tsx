'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';

import { AppShell, initials } from '@/components/AppShell';
import { ChatPanel } from '@/components/ChatPanel';
import { ChatThread } from '@/components/ChatThread';
import {
  ArrowRightIcon,
  ArrowUpIcon,
  ChevronDownIcon,
  PlusIcon,
  ShieldCheckIcon,
  SparkleIcon,
  TrashIcon,
} from '@/components/Icons';
import { STOCKS } from '@/components/StockJump';
import { ApiError } from '@/lib/api';
import {
  getConversation,
  deleteConversation,
  listConversations,
  sendQuestion,
  type ChatMessage,
  type ConversationSummary,
} from '@/lib/chat';
import { followUps, stocksIn } from '@/lib/chatContext';
import { signOut, useMe } from '@/lib/session';

const MIN_CHARS = 3; // the server refuses shorter questions
const MAX_CHARS = 1000; // and longer ones

const DESCRIPTION =
  'Grounded in official filings, screener.in and BSE end-of-day prices · TCS, HDFC Bank, Reliance';
const PILL = 'Answers only from stored data';
const PLACEHOLDER = 'Ask anything about TCS, HDFC Bank or Reliance…';
const CAPABILITIES = [
  'Answer questions about RELIANCE, TCS and HDFC Bank from their filings',
  'Show key figures and how they changed',
  'Summarise recent news and its sentiment',
  'Check how each stock fits your preferences',
];
// They fill the box; the user decides whether to send (an answer costs money).
const SUGGESTIONS = [
  'Analyse RELIANCE',
  'Compare TCS and HDFC Bank',
  'Latest news on TCS',
  'Which stocks have low debt?',
  'Match me',
];
const LOAD_FAILED = "We couldn't load the page. Reload the page to try again.";
const LIST_FAILED = "We couldn't load your conversations.";
const OPEN_FAILED = "We couldn't open that conversation.";
const OFF = 'The chat is switched off on this server.';
const UNAVAILABLE = "The chat isn't available right now. Try again in a moment.";
const FAILED = 'Something went wrong. Try again.';

/** The conversation on screen. `id` is null for a new one, until its first answer arrives. */
type Thread = {
  id: string | null;
  messages: ChatMessage[];
  phase: 'ready' | 'loading' | 'problem';
};

const NEW_THREAD: Thread = { id: null, messages: [], phase: 'ready' };

/** The stock the newest of these questions names, or null when none of them names one. */
function stockOfLatestQuestion(messages: ChatMessage[]): string | null {
  for (const message of [...messages].reverse()) {
    if (message.role !== 'user') continue;
    const found = stocksIn(message.text)[0];
    if (found) return found;
  }
  return null;
}

/** The text of `?q=` in the address (the Home page links here), read without a router hook. */
function initialDraft(): string {
  if (typeof window === 'undefined') return ''; // the static prerender has no address
  return (new URLSearchParams(window.location.search).get('q') ?? '').slice(0, MAX_CHARS);
}

const isStatus = (error: unknown, status: number): boolean =>
  error instanceof ApiError && error.status === status;

/** What to tell the user when a question could not be answered. Never the server's own words. */
function sendFailure(error: unknown): string {
  if (isStatus(error, 409)) return OFF;
  if (isStatus(error, 503)) return UNAVAILABLE;
  return FAILED;
}

/**
 * The chat page (P12): the user's conversations on one side; on the other, the open conversation
 * and the box to ask the next question. The server does all the answering and checking.
 */
const DELETE_FAILED = "We couldn't delete the conversation. Try again in a moment.";

export function ChatView() {
  const me = useMe();
  const router = useRouter();
  const signedIn = me.status === 'signed-in';

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [listFailed, setListFailed] = useState(false);
  const [listVersion, setListVersion] = useState(0); // bumped to load the list again
  const [thread, setThread] = useState<Thread>(NEW_THREAD);
  const [draft, setDraft] = useState(initialDraft);
  const [pending, setPending] = useState<string | null>(null); // the question being answered
  const [problem, setProblem] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  // The stock the side panel shows: the one a question names, or the one picked by its tabs.
  const [stock, setStock] = useState<string>(STOCKS[0].symbol);
  const menuRef = useRef<HTMLDivElement>(null);
  // The conversation asked for last, so an older one that arrives late is ignored.
  const opening = useRef<string | null>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  // The part of the conversation that scrolls; the question box below it stays in place.
  const scrollRef = useRef<HTMLDivElement>(null);

  const busy = pending !== null;
  const ready = draft.trim().length >= MIN_CHARS && !busy && thread.phase !== 'loading';

  useEffect(() => {
    if (me.status === 'signed-out') router.replace('/');
  }, [me.status, router]);

  // Keep the newest message in view: a question sent, an answer arrived, a conversation opened.
  useEffect(() => {
    const scroller = scrollRef.current;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }, [thread.messages, pending]);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    listConversations().then(
      (items) => {
        if (cancelled) return;
        setConversations(items);
        setListFailed(false);
      },
      (error: unknown) => {
        if (cancelled) return;
        if (isStatus(error, 401))
          router.replace('/'); // the session ended: sign in again
        else setListFailed(true);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [signedIn, listVersion, router]);

  // A click anywhere outside the conversations menu closes it.
  useEffect(() => {
    if (!menuOpen) return;
    const away = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', away);
    return () => document.removeEventListener('mousedown', away);
  }, [menuOpen]);

  const open = (id: string) => {
    setMenuOpen(false);
    opening.current = id;
    setThread({ id, messages: [], phase: 'loading' });
    setProblem(null);
    getConversation(id).then(
      (conversation) => {
        if (opening.current === id) {
          setThread({ id, messages: conversation.messages, phase: 'ready' });
          const named = stockOfLatestQuestion(conversation.messages);
          if (named) setStock(named);
        }
      },
      (error: unknown) => {
        if (opening.current !== id) return;
        if (isStatus(error, 401)) router.replace('/');
        // Not continued: a question must never go to a conversation we could not show.
        else setThread({ id: null, messages: [], phase: 'problem' });
      },
    );
  };

  /** Delete a conversation after the user confirms; the open one gives way to a new chat. */
  const remove = async (conversation: ConversationSummary) => {
    const title = conversation.title || 'Untitled conversation';
    if (!window.confirm(`Delete "${title}"? This cannot be undone.`)) return;
    try {
      await deleteConversation(conversation.id);
    } catch (error) {
      if (isStatus(error, 401)) {
        router.replace('/');
        return;
      }
      if (!isStatus(error, 404)) {
        setProblem(DELETE_FAILED); // a 404 means it is already gone: remove it from the list
        return;
      }
    }
    setConversations((list) => list.filter((c) => c.id !== conversation.id));
    if (thread.id === conversation.id) startNew();
  };

  const startNew = () => {
    setMenuOpen(false);
    opening.current = null;
    setThread(NEW_THREAD);
    setProblem(null);
  };

  /** Sends the box's question, or a choice's question when one is clicked (nothing to type). */
  const send = async (chosen?: string) => {
    if (chosen === undefined ? !ready : busy || thread.phase === 'loading') return;
    const question = (chosen ?? draft).trim();
    setPending(question);
    const named = stocksIn(question)[0];
    if (named) setStock(named);
    if (chosen === undefined) setDraft('');
    setProblem(null);
    try {
      const reply = await sendQuestion(question, thread.id);
      opening.current = reply.conversation_id;
      setThread((current) => ({
        id: reply.conversation_id,
        messages: [...current.messages, reply.question, reply.answer],
        phase: 'ready',
      }));
      setListVersion((version) => version + 1); // a new conversation, or a newer one
    } catch (error) {
      if (isStatus(error, 401)) {
        router.replace('/');
        return;
      }
      setDraft(question); // give the question back, to try again
      setProblem(sendFailure(error));
    } finally {
      setPending(null);
    }
  };

  const suggest = (text: string) => {
    setDraft(text);
    boxRef.current?.focus();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void send();
  };

  // Enter sends; Shift+Enter is a new line; Enter that finishes an IME composition is neither.
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void send();
  };

  if (me.status === 'error') {
    return (
      <main className="page centered">
        <p role="alert" className="alert">
          {LOAD_FAILED}
        </p>
      </main>
    );
  }
  if (!signedIn) {
    return (
      <main className="page centered">
        <p role="status" className="muted">
          Loading…
        </p>
      </main>
    );
  }

  const leave = () => void signOut().finally(() => router.replace('/'));
  const empty = thread.phase === 'ready' && thread.messages.length === 0 && !busy;
  const followable = thread.phase === 'ready' && thread.messages.length > 0 && !busy;
  // A reply that asks back ("Which company do you mean?") offers its choices instead.
  const choices = followable ? (thread.messages.at(-1)?.choices ?? []) : [];

  return (
    <AppShell email={me.user.email} onSignOut={leave} active="chat" fill>
      {listFailed && (
        <p role="alert" className="alert">
          {LIST_FAILED}
        </p>
      )}
      <div className="chat">
        <ChatPanel symbol={stock} onPick={setStock} />
        <section className="chat-main" aria-label="Conversation">
          <header className="chat-card-head">
            <span className="chat-avatar" aria-hidden="true">
              <SparkleIcon />
            </span>
            <div className="chat-card-title">
              <h1>Chat with your analyst</h1>
              <p className="muted" title={DESCRIPTION}>
                {DESCRIPTION}
              </p>
            </div>
            <span className="chat-pill">
              <ShieldCheckIcon />
              {PILL}
            </span>
            <div
              className="chat-menu-wrap"
              ref={menuRef}
              onKeyDown={(event) => {
                if (event.key === 'Escape') setMenuOpen(false);
              }}
            >
              <button
                type="button"
                className="button secondary chat-menu-button"
                aria-expanded={menuOpen}
                aria-controls="chat-menu"
                disabled={busy}
                onClick={() => setMenuOpen((value) => !value)}
              >
                Conversations
                <ChevronDownIcon />
              </button>
              {menuOpen && (
                <div
                  id="chat-menu"
                  className="chat-menu"
                  role="group"
                  aria-label="Conversation list"
                >
                  <button type="button" className="chat-menu-new" onClick={startNew}>
                    <PlusIcon />
                    New chat
                  </button>
                  {conversations.length === 0 ? (
                    <p className="muted">No conversations yet.</p>
                  ) : (
                    <ul>
                      {conversations.map((conversation) => (
                        <li key={conversation.id} className="chat-item-row">
                          <button
                            type="button"
                            className="chat-item"
                            aria-current={conversation.id === thread.id ? 'true' : undefined}
                            onClick={() => open(conversation.id)}
                          >
                            {conversation.title || 'Untitled conversation'}
                          </button>
                          <button
                            type="button"
                            className="chat-item-delete"
                            aria-label={`Delete conversation: ${conversation.title || 'Untitled conversation'}`}
                            title="Delete"
                            onClick={() => void remove(conversation)}
                          >
                            <TrashIcon />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          </header>
          <div className="chat-scroll" ref={scrollRef}>
            <div className="chat-column">
              {thread.phase === 'loading' && (
                <p role="status" className="muted">
                  Loading the conversation…
                </p>
              )}
              {thread.phase === 'problem' && (
                <p role="alert" className="alert">
                  {OPEN_FAILED}
                </p>
              )}
              {empty && (
                <div className="chat-greeting">
                  <div className="chat-message assistant">
                    <span className="chat-avatar small" aria-hidden="true">
                      <SparkleIcon size={16} />
                    </span>
                    <div className="answer-body">
                      <p className="chat-text">
                        Hello! I can answer from the filings of RELIANCE, TCS and HDFC Bank. I can:
                      </p>
                      <ul className="chat-can">
                        {CAPABILITIES.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                  <div className="chat-suggestions" role="group" aria-label="Suggestions">
                    {SUGGESTIONS.map((text) => (
                      <button
                        key={text}
                        type="button"
                        className="chat-chip"
                        onClick={() => suggest(text)}
                      >
                        {text}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <ChatThread
                messages={thread.messages}
                pending={pending}
                initials={initials(me.user.email)}
              />
              {busy && (
                <p role="status" className="chat-thinking">
                  <span className="chat-avatar small" aria-hidden="true">
                    <SparkleIcon size={16} />
                  </span>
                  <span className="chat-thinking-bubble">
                    <span className="chat-typing" aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                    Thinking…
                  </span>
                </p>
              )}
              {choices.length > 0 && (
                <div className="chat-suggestions" role="group" aria-label="Choices">
                  {choices.map((choice) => (
                    <button
                      key={choice.label}
                      type="button"
                      className="chat-chip"
                      onClick={() => void send(choice.question)}
                    >
                      {choice.label}
                    </button>
                  ))}
                </div>
              )}
              {followable && choices.length === 0 && (
                <div className="chat-suggestions" role="group" aria-label="Follow-up suggestions">
                  {followUps(stock).map((text) => (
                    <button
                      key={text}
                      type="button"
                      className="chat-chip"
                      onClick={() => suggest(text)}
                    >
                      {text}
                      <ArrowRightIcon />
                    </button>
                  ))}
                </div>
              )}
              {problem && (
                <p role="alert" className="alert">
                  {problem}
                </p>
              )}
            </div>
          </div>
          <div className="chat-column chat-compose">
            <form className="chat-form" onSubmit={submit}>
              <textarea
                ref={boxRef}
                aria-label="Your question"
                placeholder={PLACEHOLDER}
                rows={1}
                maxLength={MAX_CHARS}
                value={draft}
                disabled={busy}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={onKeyDown}
              />
              <kbd className="chat-kbd" aria-hidden="true">
                Enter ↵
              </kbd>
              <button type="submit" className="chat-send" aria-label="Send" disabled={!ready}>
                <ArrowUpIcon />
              </button>
            </form>
            {/* No disclaimer here: the menu's "Not investment advice" note is on every page. */}
            <p className="chat-disclaimer muted">
              Press <strong>Enter</strong> to send · <strong>Shift + Enter</strong> for a new line
            </p>
          </div>
        </section>
      </div>
    </AppShell>
  );
}
