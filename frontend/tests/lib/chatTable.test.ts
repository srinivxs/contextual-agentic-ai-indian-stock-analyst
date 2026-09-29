import { describe, expect, it, vi } from 'vitest';

import {
  boldFigures,
  changeTone,
  clockLabel,
  getConversation,
  sendQuestion,
  tableLink,
} from '@/lib/chat';
import { demoAnswer, demoConversation, demoId, demoQuestion, demoTable } from '../helpers/fakeApi';

function serve(body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
    ),
  );
}

const reply = (answer: unknown) => ({
  conversation_id: demoId(1),
  question: demoQuestion(),
  answer,
});
const withTable = (table: unknown) => reply({ ...demoAnswer(), table });

describe('the answer table', () => {
  it('keeps a valid table, and gives every message a table field (null when none)', async () => {
    serve(withTable(demoTable()));
    const got = await sendQuestion('Net profit?', null);
    expect(got.answer.table).toEqual(demoTable());
    expect(got.question.table).toBeNull();
  });

  it('treats a missing or null table as none', async () => {
    serve(reply(demoAnswer()));
    expect((await sendQuestion('Net profit?', null)).answer.table).toBeNull();
    serve(withTable(null));
    expect((await sendQuestion('Net profit?', null)).answer.table).toBeNull();
  });

  it('accepts a table with no source', async () => {
    serve(withTable({ ...demoTable(), source: null }));
    expect((await sendQuestion('Net profit?', null)).answer.table?.source).toBeNull();
  });

  it.each([
    ['a string', 'table'],
    ['no title', { ...demoTable(), title: undefined }],
    ['no columns', { ...demoTable(), columns: [] }],
    ['columns that are not text', { ...demoTable(), columns: [1, 2, 3] }],
    ['a row of the wrong length', { ...demoTable(), rows: [['FY2026', '1']] }],
    ['a cell that is not text', { ...demoTable(), rows: [['FY2026', 1, '+1%']] }],
    ['rows that are not a list', { ...demoTable(), rows: 'x' }],
    ['a source that is not an object', { ...demoTable(), source: 'x' }],
    ['a source without a label', { ...demoTable(), source: { url: null } }],
    ['a source url that is not text', { ...demoTable(), source: { label: 'x', url: 5 } }],
    [
      'far too many rows',
      { ...demoTable(), rows: Array.from({ length: 50 }, () => ['a', 'b', 'c']) },
    ],
  ])('drops a table with %s, but keeps the answer', async (_label, table) => {
    serve(withTable(table));
    const got = await sendQuestion('Net profit?', null);
    expect(got.answer.table).toBeNull();
    expect(got.answer.text).toBe(demoAnswer().text);
  });

  it('normalises the tables of a stored conversation too', async () => {
    serve(
      demoConversation({
        messages: [demoQuestion(), { ...demoAnswer(), table: demoTable() }, demoAnswer()],
      }),
    );
    const conversation = await getConversation(demoId(1));
    expect(conversation.messages.map((m) => m.table)).toEqual([null, demoTable(), null]);
  });

  it('links the source only to a screener.in page', () => {
    expect(tableLink(demoTable())).toBe('https://www.screener.in/company/DEMOA/consolidated/');
    const link = (url: string | null) => tableLink(demoTable({ source: { label: 'x', url } }));
    expect(link('https://evil.example/')).toBeNull();
    expect(link('javascript:alert(1)')).toBeNull();
    expect(link(null)).toBeNull();
    expect(tableLink(demoTable({ source: null }))).toBeNull();
  });

  it('says which change is a rise and which a fall', () => {
    expect(changeTone('+1.3%')).toBe('rise');
    expect(changeTone('-5.9%')).toBe('fall');
    expect(changeTone('−5.9%')).toBe('fall');
    expect(changeTone('0.0%')).toBe('flat');
    expect(changeTone('')).toBe('flat');
    expect(changeTone('n/a')).toBe('flat');
  });
});

describe('boldFigures', () => {
  const bold = (text: string) =>
    boldFigures(text)
      .filter((p) => p.bold)
      .map((p) => p.text);

  it('never alters the text', () => {
    for (const text of [
      'DemoCo reported revenue of ₹1,23,456 crore, up 12.5% from 1,10,000.',
      'Plain words only.',
      '',
      '₹5.50 per share; US$1,800 million; -3.2% and +4%; FY2026 [1] [2].',
    ]) {
      expect(
        boldFigures(text)
          .map((p) => p.text)
          .join(''),
      ).toBe(text);
    }
  });

  it('bolds rupee and dollar amounts, with their unit', () => {
    expect(bold('Revenue was ₹1,23,456 crore in FY2026.')).toEqual(['₹1,23,456 crore']);
    expect(bold('Sales of US$1,800 million.')).toEqual(['US$1,800 million']);
    expect(bold('A dividend of ₹5.50 per share.')).toEqual(['₹5.50']);
  });

  it('bolds percentages, with their sign', () => {
    expect(bold('Up 12.5% and down -3.2%, then +4%.')).toEqual(['12.5%', '-3.2%', '+4%']);
  });

  it('bolds plain numbers with commas, but not years, markers or small numbers', () => {
    expect(bold('Sales rose to 1,10,000 in FY2026 [1] across 3 segments in 2025.')).toEqual([
      '1,10,000',
    ]);
  });

  it('gives no bold part for text without figures, and nothing for nothing', () => {
    expect(bold('No figures here [1].')).toEqual([]);
    expect(boldFigures('')).toEqual([]);
  });
});

describe('clockLabel', () => {
  it('shows hours and minutes', () => {
    expect(clockLabel('2026-09-27T10:24:00+00:00')).toMatch(/^\d{2}:\d{2}$/);
  });

  it('shows nothing for a time it cannot read', () => {
    expect(clockLabel('yesterday')).toBe('');
    expect(clockLabel('')).toBe('');
  });
});
