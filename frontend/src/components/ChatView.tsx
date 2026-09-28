'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';

import { AppShell } from '@/components/AppShell';
import { ChatThread } from '@/components/ChatThread';
import { MemoryPanel } from '@/components/MemoryPanel';
import { ApiError } from '@/lib/api';
import {
  getConversation,
  listConversations,
  sendQuestion,
  type ChatMessage,
  type ConversationSummary,
} from '@/lib/chat';
import { signOut, useMe } from '@/lib/session';

const MIN_CHARS = 3; // the server refuses shorter questions
const MAX_CHARS = 1000; // and longer ones

const NOTE =
  'Answers come only from the stored filings and screener.in figures, each with its source. Not investment advice.';
const DISCLAIMER =
  'Not investment advice. Answers come only from stored filings and screener.in figures.';
const HINT =
  'Ask about RELIANCE, TCS or HDFC Bank: their results, borrowings, dividends, or what was said on a call.';
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
export function ChatView() {
  const me = useMe();
  const router = useRouter();
  const signedIn = me.status === 'signed-in';

  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [listFailed, setListFailed] = useState(false);
  const [listVersion, setListVersion] = useState(0); // bumped to load the list again
  const [thread, setThread] = useState<Thread>(NEW_THREAD);
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState<string | null>(null); // the question being answered
  const [problem, setProblem] = useState<string | null>(null);
  const [memoryRefresh, setMemoryRefresh] = useState(0); // bumped to refetch what is remembered
  // The conversation asked for last, so an older one that arrives late is ignored.
  const opening = useRef<string | null>(null);

  const busy = pending !== null;
  const ready = draft.trim().length >= MIN_CHARS && !busy && thread.phase !== 'loading';

  useEffect(() => {
    if (me.status === 'signed-out') router.replace('/');
  }, [me.status, router]);

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

  const open = (id: string) => {
    opening.current = id;
    setThread({ id, messages: [], phase: 'loading' });
    setProblem(null);
    getConversation(id).then(
      (conversation) => {
        if (opening.current === id) {
          setThread({ id, messages: conversation.messages, phase: 'ready' });
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

  const startNew = () => {
    opening.current = null;
    setThread(NEW_THREAD);
    setProblem(null);
  };

  const send = async () => {
    if (!ready) return;
    const question = draft.trim();
    setPending(question);
    setDraft('');
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
      setMemoryRefresh((version) => version + 1); // the reply may have changed what is remembered
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

  return (
    <AppShell email={me.user.email} onSignOut={leave}>
      <div className="chat-head">
        <h1>Chat</h1>
        <p className="muted">{NOTE}</p>
      </div>
      {listFailed && (
        <p role="alert" className="alert">
          {LIST_FAILED}
        </p>
      )}
      <div className="chat">
        <section className="chat-list" aria-label="Conversations">
          <button type="button" className="button secondary" onClick={startNew} disabled={busy}>
            New conversation
          </button>
          {conversations.length === 0 ? (
            <p className="muted">No conversations yet.</p>
          ) : (
            <ul>
              {conversations.map((conversation) => (
                <li key={conversation.id}>
                  <button
                    type="button"
                    className="chat-item"
                    aria-current={conversation.id === thread.id ? 'true' : undefined}
                    disabled={busy}
                    onClick={() => open(conversation.id)}
                  >
                    {conversation.title || 'Untitled conversation'}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="chat-main" aria-label="Conversation">
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
          {empty && <p className="muted">{HINT}</p>}
          <ChatThread messages={thread.messages} pending={pending} />
          {busy && (
            <p role="status" className="muted">
              Thinking…
            </p>
          )}
          {problem && (
            <p role="alert" className="alert">
              {problem}
            </p>
          )}
          <form className="chat-form" onSubmit={submit}>
            <textarea
              aria-label="Your question"
              placeholder="Ask a question. Enter sends, Shift+Enter adds a line."
              rows={3}
              maxLength={MAX_CHARS}
              value={draft}
              disabled={busy}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
            />
            <button type="submit" className="button" disabled={!ready}>
              Send
            </button>
          </form>
          <p className="chat-disclaimer muted">{DISCLAIMER}</p>
        </section>
        <MemoryPanel refreshSignal={memoryRefresh} />
      </div>
    </AppShell>
  );
}
