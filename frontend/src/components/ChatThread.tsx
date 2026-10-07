'use client';

import { useState } from 'react';

import { ExternalIcon, SparkleIcon } from '@/components/Icons';
import {
  boldFigures,
  changeTone,
  clockLabel,
  sourceLink,
  answerPoints,
  splitMarkers,
  tableLink,
  type ChatMessage,
  type ChatSource,
  type ChatTable,
} from '@/lib/chat';

/**
 * The messages of one conversation: the user's questions, and each answer in a card with its
 * figures in bold, an optional table, and its numbered sources. React renders every value as
 * text, so nothing from the server is ever HTML.
 */

/** The page-wide id of one source of one answer, so a marker can point at it. */
const sourceId = (messageId: string, marker: number): string => `source-${messageId}-${marker}`;

const KIND_DETAIL: Record<ChatSource['source'], string> = {
  filing: 'Official filing (BSE)',
  screener: 'screener.in figure',
  rbi: 'RBI press release',
  derived: 'Computed from stored figures',
};

/** The time under a message, e.g. "10:24"; nothing when there is none to show. */
function Clock({ iso }: { iso: string }) {
  const label = clockLabel(iso);
  return label ? (
    <time className="chat-time" dateTime={iso}>
      {label}
    </time>
  ) : null;
}

/** A source's title: a link that opens it in a new tab, plain text if unsafe, or "Computed". */
function SourceLabel({ source, link }: { source: ChatSource; link: string | null }) {
  if (source.source === 'derived') {
    return (
      <>
        <span className="pill">Computed</span> <span>{source.label}</span>
      </>
    );
  }
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
    <div className="chat-sources-box">
      <p className="chat-sources-title">Sources</p>
      <ol className="chat-sources" aria-label="Sources">
        {sources.map((source, index) => {
          const link = sourceLink(source);
          return (
            <li
              key={`${index}-${source.marker}`}
              id={sourceId(messageId, source.marker)}
              tabIndex={-1} // so a marker can move the focus here
              className={source.marker === active ? 'chat-source highlighted' : 'chat-source'}
            >
              <span className="chat-source-marker">{source.marker}</span>
              <div className="chat-source-body">
                <div className="chat-source-title">
                  <SourceLabel source={source} link={link} />
                </div>
                <p className="chat-source-detail">{KIND_DETAIL[source.source]}</p>
                {source.quote && (
                  <details className="chat-quote">
                    <summary>Quote</summary>
                    <blockquote>{source.quote}</blockquote>
                  </details>
                )}
              </div>
              {link && (
                <a
                  className="chat-source-open"
                  href={link}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-hidden="true"
                  tabIndex={-1}
                >
                  <ExternalIcon />
                </a>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** A small table the server built from stored figures: its title, columns, rows and source. */
function AnswerTable({ table }: { table: ChatTable }) {
  const link = tableLink(table);
  const changeColumns = table.columns.map((column) => /change/i.test(column));
  return (
    <figure className="chat-table">
      <table>
        <caption>{table.title}</caption>
        <thead>
          <tr>
            {table.columns.map((column, index) => (
              <th key={index} scope="col" className={changeColumns[index] ? 'num' : undefined}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, index) =>
                index === 0 ? (
                  <th key={index} scope="row">
                    {cell}
                  </th>
                ) : (
                  <td
                    key={index}
                    className={changeColumns[index] ? `num chat-change ${changeTone(cell)}` : 'num'}
                  >
                    {cell}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {table.source && (
        <figcaption className="chat-table-source muted">
          Source:{' '}
          {link ? (
            <a href={link} target="_blank" rel="noopener noreferrer" className="doc-link">
              {table.source.label}
            </a>
          ) : (
            <span>{table.source.label}</span>
          )}
        </figcaption>
      )}
    </figure>
  );
}

/**
 * An answer. Its "[1]" markers become small buttons that highlight and focus their source. An
 * abstention or an out-of-scope reply is shown as a quiet notice: the system working, not failing.
 */
function Answer({ message }: { message: ChatMessage }) {
  const [active, setActive] = useState<number | null>(null);
  if (message.status !== 'answered') {
    return (
      <>
        <p className="chat-notice">{message.text}</p>
        {message.status === 'remembered' && (
          <a className="doc-link chat-match-link" href="/match/">
            See it on Match
          </a>
        )}
        <Clock iso={message.created_at} />
      </>
    );
  }

  const show = (marker: number) => {
    setActive(marker);
    document.getElementById(sourceId(message.id, marker))?.focus(); // the browser scrolls to it
  };
  const segments = splitMarkers(
    message.text,
    message.sources.map((s) => s.marker),
  );
  const renderSegments = (part: typeof segments) =>
    part.map((segment, index) =>
      segment.kind === 'text' ? (
        <span key={index}>
          {boldFigures(segment.text).map((piece, at) =>
            piece.bold ? <strong key={at}>{piece.text}</strong> : piece.text,
          )}
        </span>
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
    );
  // One point per claim, each on its own line with space between (a single claim stays a line).
  const points = answerPoints(segments);
  return (
    <>
      {points.length > 1 ? (
        <ul className="chat-points" aria-label="Answer">
          {points.map((point, index) => (
            <li key={index} className="chat-text">
              {renderSegments(point)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="chat-text">{renderSegments(segments)}</p>
      )}
      {message.table && <AnswerTable table={message.table} />}
      {message.sources.length > 0 && (
        <ChatSources messageId={message.id} sources={message.sources} active={active} />
      )}
      <Clock iso={message.created_at} />
    </>
  );
}

/** The user's message: a tinted bubble on the right, their initials beside it, the time under. */
function Mine({
  text,
  initials,
  at,
  pending = false,
}: {
  text: string;
  initials: string;
  at: string | null;
  pending?: boolean;
}) {
  return (
    <li className={pending ? 'chat-message user pending' : 'chat-message user'}>
      <div className="chat-mine-stack">
        <p className="bubble">{text}</p>
        {at !== null && <Clock iso={at} />}
      </div>
      <span className="chat-initials" aria-hidden="true">
        {initials}
      </span>
    </li>
  );
}

/** Oldest first; a question still waiting for its answer is shown at the end, as pending. */
export function ChatThread({
  messages,
  pending,
  initials,
}: {
  messages: ChatMessage[];
  pending: string | null;
  initials: string;
}) {
  return (
    <ol className="chat-thread" aria-label="Messages">
      {messages.map((message) =>
        message.role === 'user' ? (
          <Mine key={message.id} text={message.text} initials={initials} at={message.created_at} />
        ) : (
          <li key={message.id} className="chat-message assistant">
            <span className="chat-avatar small" aria-hidden="true">
              <SparkleIcon size={16} />
            </span>
            <div className="answer-body">
              <Answer message={message} />
            </div>
          </li>
        ),
      )}
      {pending !== null && <Mine text={pending} initials={initials} at={null} pending />}
    </ol>
  );
}
