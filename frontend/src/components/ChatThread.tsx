'use client';

import { useState } from 'react';

import { LeafIcon } from '@/components/Icons';
import { sourceLink, splitMarkers, type ChatMessage, type ChatSource } from '@/lib/chat';

/**
 * The messages of one conversation: the user's questions, and each answer with its numbered
 * sources. React renders every value as text, so nothing from the server is ever HTML.
 */

/** The page-wide id of one source of one answer, so a marker can point at it. */
const sourceId = (messageId: string, marker: number): string => `source-${messageId}-${marker}`;

/** A source's label: a link that opens it in a new tab, plain text if unsafe, or "Computed". */
function SourceLabel({ source }: { source: ChatSource }) {
  if (source.source === 'derived') {
    return (
      <>
        <span className="pill">Computed</span> <span>{source.label}</span>
      </>
    );
  }
  const link = sourceLink(source);
  const label = link ? (
    <a href={link} target="_blank" rel="noopener noreferrer" className="doc-link">
      {source.label}
    </a>
  ) : (
    <span>{source.label}</span>
  );
  if (source.source !== 'rbi') return label;
  return (
    <>
      <span className="pill">RBI</span> {label}
    </>
  );
}

function ChatSources({
  messageId,
  sources,
  active,
}: {
  messageId: string;
  sources: ChatSource[];
  active: number | null;
}) {
  return (
    <ol className="chat-sources" aria-label="Sources">
      {sources.map((source, index) => (
        <li
          key={`${index}-${source.marker}`}
          id={sourceId(messageId, source.marker)}
          tabIndex={-1} // so a marker can move the focus here
          className={source.marker === active ? 'chat-source highlighted' : 'chat-source'}
        >
          <span className="chat-source-marker">{`[${source.marker}]`}</span>
          <SourceLabel source={source} />
          {source.quote && (
            <details className="chat-quote">
              <summary>Quote</summary>
              <blockquote>{source.quote}</blockquote>
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}

/**
 * An answer. Its "[1]" markers become small buttons that highlight and focus their source. An
 * abstention or an out-of-scope reply is shown as a quiet notice: the system working, not failing.
 */
function Answer({ message }: { message: ChatMessage }) {
  const [active, setActive] = useState<number | null>(null);
  if (message.status !== 'answered') return <p className="chat-notice">{message.text}</p>;

  const show = (marker: number) => {
    setActive(marker);
    document.getElementById(sourceId(message.id, marker))?.focus(); // the browser scrolls to it
  };
  const segments = splitMarkers(
    message.text,
    message.sources.map((s) => s.marker),
  );
  return (
    <>
      <p className="chat-text">
        {segments.map((segment, index) =>
          segment.kind === 'text' ? (
            <span key={index}>{segment.text}</span>
          ) : (
            <sup key={index}>
              <button
                type="button"
                className="marker"
                aria-label={`Source ${segment.marker}`}
                aria-controls={sourceId(message.id, segment.marker)}
                onClick={() => show(segment.marker)}
              >
                {`[${segment.marker}]`}
              </button>
            </sup>
          ),
        )}
      </p>
      {message.sources.length > 0 && (
        <ChatSources messageId={message.id} sources={message.sources} active={active} />
      )}
    </>
  );
}

/** Oldest first; a question still waiting for its answer is shown at the end, as pending. */
export function ChatThread({
  messages,
  pending,
}: {
  messages: ChatMessage[];
  pending: string | null;
}) {
  return (
    <ol className="chat-thread" aria-label="Messages">
      {messages.map((message) =>
        message.role === 'user' ? (
          <li key={message.id} className="chat-message user">
            <p className="bubble">{message.text}</p>
          </li>
        ) : (
          <li key={message.id} className="chat-message assistant">
            <span className="chat-avatar small" aria-hidden="true">
              <LeafIcon size={16} />
            </span>
            <div className="answer-body">
              <Answer message={message} />
            </div>
          </li>
        ),
      )}
      {pending !== null && (
        <li className="chat-message user pending">
          <p className="bubble">{pending}</p>
        </li>
      )}
    </ol>
  );
}
