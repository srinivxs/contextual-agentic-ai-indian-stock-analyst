import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import {
  getConversation,
  listConversations,
  sendQuestion,
  sourceLink,
  splitMarkers,
  type ChatSource,
} from '@/lib/chat';
import {
  demoAnswer,
  demoChatSource,
  demoConversation,
  demoId,
  demoQuestion,
  installFakeApi,
} from '../helpers/fakeApi';

/** Make every request answer 200 with this body, whatever it asks. */
function serve(body: unknown): ReturnType<typeof vi.fn> {
  const mock = vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', mock);
  return mock;
}

const reply = (overrides: Record<string, unknown> = {}) => ({
  conversation_id: demoId(1),
  question: demoQuestion(),
  answer: demoAnswer(),
  ...overrides,
});

describe('sendQuestion', () => {
  it('posts the question and a null conversation to start a new one', async () => {
    const api = installFakeApi();
    const answer = await sendQuestion('How did revenue grow?', null);

    expect(api.requests).toEqual(['POST /api/v1/chat/messages']);
    expect(api.bodies).toEqual([
      {
        key: 'POST /api/v1/chat/messages',
        body: { question: 'How did revenue grow?', conversation_id: null },
      },
    ]);
    expect(answer.question.text).toBe('How did revenue grow?');
    expect(answer.answer.status).toBe('answered');
    expect(api.conversations.has(answer.conversation_id)).toBe(true);
  });

  it('continues an existing conversation', async () => {
    const api = installFakeApi({ conversations: [demoConversation()] });
    const answer = await sendQuestion('And net profit?', demoId(1));

    expect(api.bodies[0]?.body).toEqual({
      question: 'And net profit?',
      conversation_id: demoId(1),
    });
    expect(answer.conversation_id).toBe(demoId(1));
  });

  it('refuses a conversation id that is not a UUID without asking the server', async () => {
    const api = installFakeApi();
    await expect(sendQuestion('How did revenue grow?', '../me')).rejects.toBeInstanceOf(ApiError);
    expect(api.requests).toEqual([]);
  });

  it.each([409, 503, 401])('passes a %s on as an ApiError', async (status) => {
    const api = installFakeApi();
    api.failWith('POST /api/v1/chat/messages', status);
    await expect(sendQuestion('How did revenue grow?', null)).rejects.toMatchObject({ status });
  });

  it.each([
    ['no answer', { answer: undefined }],
    ['a conversation id that is not a UUID', { conversation_id: 'abc' }],
    ['a question that claims to be the assistant', { question: demoAnswer() }],
    ['an answer that claims to be the user', { answer: demoQuestion() }],
    ['an answer with an unknown status', { answer: demoAnswer({ status: 'guessed' as never }) }],
    ['an answer without a status', { answer: demoAnswer({ status: null }) }],
    ['a user message with a status', { question: demoQuestion({ status: 'answered' }) }],
    ['a message whose text is not text', { answer: demoAnswer({ text: 42 as never }) }],
    ['a message whose id is not a UUID', { answer: demoAnswer({ id: '7' }) }],
    ['a message without a time', { answer: demoAnswer({ created_at: undefined as never }) }],
    ['sources that are not a list', { answer: demoAnswer({ sources: null as never }) }],
    [
      'a source of an unknown kind',
      { answer: demoAnswer({ sources: [demoChatSource({ source: 'blog' as never })] }) },
    ],
    [
      'a marker that is not a whole number',
      { answer: demoAnswer({ sources: [demoChatSource({ marker: 1.5 })] }) },
    ],
    ['a marker below 1', { answer: demoAnswer({ sources: [demoChatSource({ marker: 0 })] }) }],
    [
      'a source without a label',
      { answer: demoAnswer({ sources: [demoChatSource({ label: undefined as never })] }) },
    ],
    [
      'a url that is not text or null',
      { answer: demoAnswer({ sources: [demoChatSource({ url: 5 as never })] }) },
    ],
    [
      'a quote that is not text or null',
      { answer: demoAnswer({ sources: [demoChatSource({ quote: {} as never })] }) },
    ],
  ])('rejects a reply with %s', async (_label, overrides) => {
    serve(reply(overrides));
    await expect(sendQuestion('How did revenue grow?', null)).rejects.toMatchObject({
      status: 200,
      code: 'unexpected_response',
    });
  });

  it('rejects a reply that is not an object at all', async () => {
    serve(null);
    await expect(sendQuestion('How did revenue grow?', null)).rejects.toMatchObject({
      code: 'unexpected_response',
    });
  });

  it('accepts abstained and out-of-scope answers', async () => {
    serve(reply({ answer: demoAnswer({ status: 'abstained', sources: [] }) }));
    await expect(sendQuestion('What is the share price?', null)).resolves.toMatchObject({
      answer: { status: 'abstained' },
    });
    serve(reply({ answer: demoAnswer({ status: 'out_of_scope', sources: [] }) }));
    await expect(sendQuestion('What about another company?', null)).resolves.toMatchObject({
      answer: { status: 'out_of_scope' },
    });
  });
});

describe('listConversations', () => {
  it('returns the conversations newest first', async () => {
    const api = installFakeApi({
      conversations: [
        demoConversation({ id: demoId(1), title: 'Older', updated_at: '2026-09-26T10:00:00Z' }),
        demoConversation({ id: demoId(2), title: 'Newer', updated_at: '2026-09-27T10:00:00Z' }),
      ],
    });

    const list = await listConversations();
    expect(list.map((c) => c.title)).toEqual(['Newer', 'Older']);
    expect(list[0]).toEqual({
      id: demoId(2),
      title: 'Newer',
      created_at: '2026-09-27T10:00:00+00:00',
      updated_at: '2026-09-27T10:00:00Z',
    });
    expect(api.requests).toEqual(['GET /api/v1/chat/conversations']);
  });

  it('accepts a conversation without a title yet', async () => {
    serve({ items: [{ ...demoConversation({ title: null }), messages: undefined }] });
    await expect(listConversations()).resolves.toHaveLength(1);
  });

  it.each([
    ['no items', {}],
    ['items that are not a list', { items: 'x' }],
    ['an item without an id', { items: [{ title: 'x', created_at: 'a', updated_at: 'b' }] }],
    [
      'an item whose id is not a UUID',
      { items: [{ id: '1', title: 'x', created_at: 'a', updated_at: 'b' }] },
    ],
    ['an item with a numeric title', { items: [{ ...demoConversation(), title: 5 }] }],
    [
      'an item without times',
      { items: [{ id: demoId(1), title: 'x', created_at: null, updated_at: 'b' }] },
    ],
  ])('rejects a list with %s', async (_label, body) => {
    serve(body);
    await expect(listConversations()).rejects.toMatchObject({ code: 'unexpected_response' });
  });
});

describe('getConversation', () => {
  it('returns the conversation with its messages, oldest first', async () => {
    const api = installFakeApi({ conversations: [demoConversation()] });

    const conversation = await getConversation(demoId(1));
    expect(conversation.messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(api.requests).toEqual([`GET /api/v1/chat/conversations/${demoId(1)}`]);
  });

  it('passes a 404 on for a conversation that is not the user’s', async () => {
    installFakeApi();
    await expect(getConversation(demoId(9))).rejects.toMatchObject({ status: 404 });
  });

  it.each(['../me', '', 'abc', `${demoId(1)}/x`, `${demoId(1)}?x=1`])(
    'refuses %j without asking the server',
    async (id) => {
      const api = installFakeApi();
      await expect(getConversation(id)).rejects.toBeInstanceOf(ApiError);
      expect(api.requests).toEqual([]);
    },
  );

  it.each([
    ['no messages', { ...demoConversation(), messages: undefined }],
    ['a malformed message', { ...demoConversation(), messages: [{ role: 'user' }] }],
    ['no id', { ...demoConversation(), id: undefined }],
  ])('rejects a conversation with %s', async (_label, body) => {
    serve(body);
    await expect(getConversation(demoId(1))).rejects.toMatchObject({
      code: 'unexpected_response',
    });
  });
});

describe('sourceLink', () => {
  const filing = (url: string | null): ChatSource => demoChatSource({ url });
  const screener = (url: string | null): ChatSource =>
    demoChatSource({ source: 'screener', url, quote: null });

  it('links a filing to its https BSE address', () => {
    const url = 'https://www.bseindia.com/xml-data/corpfiling/AttachHis/x.pdf#page=4';
    expect(sourceLink(filing(url))).toBe(url);
  });

  it('links a screener figure to its https screener.in company page', () => {
    const url = 'https://www.screener.in/company/DEMOA/consolidated/';
    expect(sourceLink(screener(url))).toBe(url);
  });

  it('never links a computed value', () => {
    const url = 'https://www.bseindia.com/x.pdf';
    expect(sourceLink(demoChatSource({ source: 'derived', url }))).toBeNull();
  });

  it.each([
    ['no address', null],
    ['a javascript: address', 'javascript:alert(1)'],
    ['plain http', 'http://www.bseindia.com/x.pdf'],
    ['a look-alike host', 'https://www.bseindia.com.evil.example/x.pdf'],
    ['a look-alike prefix', 'https://www.bseindia.company/x.pdf'],
    ['a screener page on a filing', 'https://www.screener.in/company/DEMOA/'],
  ])('does not link a filing with %s', (_label, url) => {
    expect(sourceLink(filing(url))).toBeNull();
  });

  it.each([
    ['a javascript: address', 'javascript:alert(1)'],
    ['a screener page that is not a company page', 'https://www.screener.in/login/'],
    ['a look-alike host', 'https://www.screener.in.evil.example/company/DEMOA/'],
    ['a BSE address on a screener figure', 'https://www.bseindia.com/x.pdf'],
  ])('does not link a screener figure with %s', (_label, url) => {
    expect(sourceLink(screener(url))).toBeNull();
  });
});

describe('splitMarkers', () => {
  it('splits the text around the markers it knows', () => {
    expect(splitMarkers('Revenue rose [1], profit fell [2].', [1, 2])).toEqual([
      { kind: 'text', text: 'Revenue rose ' },
      { kind: 'marker', marker: 1 },
      { kind: 'text', text: ', profit fell ' },
      { kind: 'marker', marker: 2 },
      { kind: 'text', text: '.' },
    ]);
  });

  it('keeps adjacent markers apart', () => {
    expect(splitMarkers('Both [1][2]', [1, 2])).toEqual([
      { kind: 'text', text: 'Both ' },
      { kind: 'marker', marker: 1 },
      { kind: 'marker', marker: 2 },
    ]);
  });

  it('leaves a marker with no source as plain text', () => {
    expect(splitMarkers('Revenue rose [1] and [7].', [1])).toEqual([
      { kind: 'text', text: 'Revenue rose ' },
      { kind: 'marker', marker: 1 },
      { kind: 'text', text: ' and [7].' },
    ]);
  });

  it('returns plain text unchanged when there are no markers', () => {
    expect(splitMarkers("I don't have that in the data.", [])).toEqual([
      { kind: 'text', text: "I don't have that in the data." },
    ]);
  });

  it('treats brackets that are not a number as text', () => {
    expect(splitMarkers('See [a] and [1a] and [ 1 ].', [1])).toEqual([
      { kind: 'text', text: 'See [a] and [1a] and [ 1 ].' },
    ]);
  });

  it('returns nothing for empty text', () => {
    expect(splitMarkers('', [1])).toEqual([]);
  });
});
