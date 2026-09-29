/**
 * The chat (P12): the three calls, the shape checks on what comes back, and the two small helpers
 * the page needs to show an answer's sources.
 *
 * The server retrieves the evidence, has the model write the answer, checks every citation and
 * every number, and only then replies. Nothing here judges or changes an answer; it only makes
 * sure the reply has the agreed shape, and that a source can only ever link somewhere safe.
 */

import { ApiError, apiFetch } from '@/lib/api';
import { officialUrl, rbiUrl, screenerUrl } from '@/lib/documents';

const STATUSES = ['answered', 'abstained', 'out_of_scope', 'remembered'] as const;
const SOURCE_KINDS = ['filing', 'screener', 'rbi', 'derived'] as const;

/** Where one numbered claim of an answer comes from. The answer's text refers to it as "[1]". */
export type ChatSource = {
  marker: number;
  /** A page of a filing, a screener.in figure, an RBI press release, or a value the server computed from facts. */
  source: (typeof SOURCE_KINDS)[number];
  label: string;
  url: string | null;
  /** The filing's own words; null when there are none. */
  quote: string | null;
};

/** A small table the server builds from stored figures and adds under some answers. */
export type ChatTable = {
  title: string;
  columns: string[];
  /** Every row has one text cell per column; "" means nothing to show. */
  rows: string[][];
  source: { label: string; url: string | null } | null;
};

/** An answer to a question the chat asked back: the button, and the question a click sends. */
export type ChatChoice = { label: string; question: string };

export type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  /** How the question was dealt with; null for the user's own messages. */
  status: (typeof STATUSES)[number] | null;
  sources: ChatSource[];
  /** Checked here: a table with a bad shape is dropped (null), the answer stays. */
  table?: ChatTable | null;
  /** Checked here: sent with a live reply that asks back; a bad one is dropped. */
  choices?: ChatChoice[];
  created_at: string;
};

export type ConversationSummary = {
  id: string;
  /** The first question, shortened; null if the server has not named it. */
  title: string | null;
  created_at: string;
  updated_at: string;
};

export type Conversation = ConversationSummary & { messages: ChatMessage[] };

/** What one question gets back: the question as stored, and the answer. */
export type ChatReply = { conversation_id: string; question: ChatMessage; answer: ChatMessage };

// Shape checks. Each one starts from `value ?? {}`, so null or a missing object simply fails.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const text = (value: unknown): value is string => typeof value === 'string';
const orNull = (value: unknown, test: (v: unknown) => boolean): boolean =>
  value === null || test(value);
const oneOf =
  (allowed: readonly string[]) =>
  (value: unknown): boolean =>
    text(value) && allowed.includes(value);
const isUuid = (value: unknown): boolean => text(value) && UUID.test(value);

function isSource(value: unknown): value is ChatSource {
  const s = (value ?? {}) as Partial<ChatSource>;
  return (
    Number.isInteger(s.marker) &&
    (s.marker ?? 0) >= 1 &&
    oneOf(SOURCE_KINDS)(s.source) &&
    text(s.label) &&
    orNull(s.url, text) &&
    orNull(s.quote, text)
  );
}

const MAX_TABLE_ROWS = 20;
const MAX_TABLE_COLUMNS = 6;

const textList = (value: unknown): value is string[] => Array.isArray(value) && value.every(text);

/** The table if it has the agreed shape, else null. A bad table never spoils the answer. */
function checkedTable(value: unknown): ChatTable | null {
  const t = (value ?? {}) as Partial<ChatTable>;
  const source = (t.source ?? {}) as Partial<NonNullable<ChatTable['source']>>;
  const columns = t.columns;
  const rows = t.rows;
  const good =
    text(t.title) &&
    textList(columns) &&
    columns.length >= 1 &&
    columns.length <= MAX_TABLE_COLUMNS &&
    Array.isArray(rows) &&
    rows.length <= MAX_TABLE_ROWS &&
    rows.every((row) => textList(row) && row.length === columns.length) &&
    (t.source === null ||
      (typeof t.source === 'object' && text(source.label) && orNull(source.url, text)));
  return good ? (value as ChatTable) : null;
}

const MAX_CHOICES = 5;
const MAX_CHOICE_LABEL = 40;

/** The well-formed choices, at most five; anything else is dropped (the answer stays). */
function checkedChoices(value: unknown): ChatChoice[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((c): c is ChatChoice => {
      const choice = (c ?? {}) as Partial<ChatChoice>;
      return (
        text(choice.label) &&
        choice.label.length >= 1 &&
        choice.label.length <= MAX_CHOICE_LABEL &&
        text(choice.question) &&
        choice.question.trim().length >= 3
      );
    })
    .slice(0, MAX_CHOICES);
}

const withCheckedParts = (message: ChatMessage): ChatMessage => ({
  ...message,
  table: checkedTable(message.table),
  choices: checkedChoices(message.choices),
});

function isMessage(value: unknown): value is ChatMessage {
  const m = (value ?? {}) as Partial<ChatMessage>;
  // The user's messages carry no status; every answer carries one.
  const statusFits =
    m.role === 'user' ? m.status === null : m.role === 'assistant' && oneOf(STATUSES)(m.status);
  return (
    isUuid(m.id) &&
    statusFits &&
    text(m.text) &&
    Array.isArray(m.sources) &&
    m.sources.every(isSource) &&
    text(m.created_at)
  );
}

function isSummary(value: unknown): value is ConversationSummary {
  const c = (value ?? {}) as Partial<ConversationSummary>;
  return isUuid(c.id) && orNull(c.title, text) && text(c.created_at) && text(c.updated_at);
}

function isConversation(value: unknown): value is Conversation {
  const messages = (value as Partial<Conversation> | null)?.messages;
  return isSummary(value) && Array.isArray(messages) && messages.every(isMessage);
}

function isReply(value: unknown): value is ChatReply {
  const r = (value ?? {}) as Partial<ChatReply>;
  return (
    isUuid(r.conversation_id) &&
    isMessage(r.question) &&
    r.question.role === 'user' &&
    isMessage(r.answer) &&
    r.answer.role === 'assistant'
  );
}

/**
 * An id we are willing to put in a path. Anything else is answered here, as the server would
 * answer an unknown conversation (404), without asking it.
 */
function checkedId(id: string): string {
  if (!UUID.test(id)) throw new ApiError(404, 'not_found');
  return id;
}

/** Ask a question: in a new conversation (null) or to continue one. */
export async function sendQuestion(
  question: string,
  conversationId: string | null,
): Promise<ChatReply> {
  const conversation_id = conversationId === null ? null : checkedId(conversationId);
  const body = await apiFetch('/api/v1/chat/messages', {
    method: 'POST',
    body: { question, conversation_id },
  });
  if (!isReply(body)) throw new ApiError(200, 'unexpected_response');
  return {
    ...body,
    question: withCheckedParts(body.question),
    answer: withCheckedParts(body.answer),
  };
}

/** The user's conversations, newest first (the server sends at most 20). */
export async function listConversations(): Promise<ConversationSummary[]> {
  const body = await apiFetch('/api/v1/chat/conversations');
  const items = (body as { items?: unknown } | null)?.items;
  if (!Array.isArray(items) || !items.every(isSummary)) {
    throw new ApiError(200, 'unexpected_response');
  }
  return items;
}

/** One conversation with its messages, oldest first. */
export async function getConversation(id: string): Promise<Conversation> {
  // encodeURIComponent as well, although a UUID has nothing in it to escape.
  const body = await apiFetch(`/api/v1/chat/conversations/${encodeURIComponent(checkedId(id))}`);
  if (!isConversation(body)) throw new ApiError(200, 'unexpected_response');
  return { ...body, messages: body.messages.map(withCheckedParts) };
}

/**
 * The address a source may link to, or null. A filing may only link to BSE and a screener figure
 * only to a screener.in company page; a computed value has no page of its own. So a bad value can
 * never become a `javascript:` or look-alike link in the page.
 */
export function sourceLink(source: ChatSource): string | null {
  if (source.source === 'filing') return officialUrl(source.url);
  if (source.source === 'screener') return screenerUrl(source.url);
  if (source.source === 'rbi') return rbiUrl(source.url);
  return null;
}

export type Segment = { kind: 'text'; text: string } | { kind: 'marker'; marker: number };

/**
 * An answer's text cut into plain text and citation markers: "Revenue rose [1]." becomes the
 * text "Revenue rose ", the marker 1, and the text ".". A marker with no source in `known` stays
 * part of the text, so the page never offers a button that leads nowhere.
 */
export function splitMarkers(answer: string, known: readonly number[]): Segment[] {
  const segments: Segment[] = [];
  let last = 0; // where the text not yet added starts
  for (const match of answer.matchAll(/\[(\d{1,3})\]/g)) {
    const marker = Number(match[1]);
    if (!known.includes(marker)) continue; // stays in the text, added with the next piece
    const before = answer.slice(last, match.index);
    if (before) segments.push({ kind: 'text', text: before });
    segments.push({ kind: 'marker', marker });
    last = match.index + match[0].length;
  }
  const rest = answer.slice(last);
  if (rest) segments.push({ kind: 'text', text: rest });
  return segments;
}

/**
 * The answer's points, for showing each on its own line. The server writes every claim as its
 * text followed by its markers ("Margins held up. [1]") and joins claims with a space, so a
 * text piece that starts with a space right after a marker starts the next point. Nothing is
 * reworded.
 */
export function answerPoints(segments: Segment[]): Segment[][] {
  const points: Segment[][] = [];
  let current: Segment[] = [];
  segments.forEach((segment, index) => {
    const afterMarker = segments[index - 1]?.kind === 'marker';
    // a marker followed by a space ends a claim; "crore [1], net profit" goes on
    if (segment.kind === 'text' && afterMarker && /^\s/.test(segment.text) && current.length > 0) {
      points.push(current);
      current = [];
      const text = segment.text.trimStart();
      if (text) current.push({ kind: 'text', text });
      return;
    }
    current.push(segment);
  });
  if (current.length > 0) points.push(current);
  return points;
}

/** The address a table's source may link to: only a screener.in company page, else null. */
export function tableLink(table: ChatTable): string | null {
  return screenerUrl(table.source?.url ?? null);
}

/** "+1.3%" is a rise, "-5.9%" (or with a real minus sign) a fall; anything else is flat. */
export function changeTone(cell: string): 'rise' | 'fall' | 'flat' {
  const trimmed = cell.trim();
  if (!/\d/.test(trimmed) || Number(trimmed.replace(/[^0-9.]/g, '')) === 0) return 'flat';
  if (trimmed.startsWith('+')) return 'rise';
  if (trimmed.startsWith('-') || trimmed.startsWith('−')) return 'fall';
  return 'flat';
}

export type Piece = { text: string; bold: boolean };

// Amounts (with a unit word), percentages (with a sign), and numbers grouped with commas. Plain
// small numbers, years ("FY2026") and "[1]" markers are left alone.
const FIGURE =
  /(?:US\$|₹)\s?\d(?:[\d,]*\d)?(?:\.\d+)?(?: (?:lakh crore|crore|million|billion))?|[+\-−]?\d(?:[\d,]*\d)?(?:\.\d+)?%|\d{1,3}(?:,\d{2,3})+(?:\.\d+)?/g;

/**
 * The text cut into plain and bold pieces, the figures being bold. The pieces joined give back the
 * text exactly: this only decides how to show it, never what it says.
 */
export function boldFigures(source: string): Piece[] {
  const pieces: Piece[] = [];
  let last = 0;
  for (const match of source.matchAll(FIGURE)) {
    if (match.index > last) pieces.push({ text: source.slice(last, match.index), bold: false });
    pieces.push({ text: match[0], bold: true });
    last = match.index + match[0].length;
  }
  if (last < source.length) pieces.push({ text: source.slice(last), bold: false });
  return pieces;
}

/** "10:24" in the reader's own time; "" if the time cannot be read. */
export function clockLabel(iso: string): string {
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) return '';
  return time.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}
