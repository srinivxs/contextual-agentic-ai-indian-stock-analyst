'use client';

import { useState, type FormEvent } from 'react';

import { hitLabel, pageLink, searchFilings, type SearchHit } from '@/lib/search';

const MIN_CHARS = 3; // the server refuses shorter questions

type State =
  | { phase: 'idle' }
  | { phase: 'searching' }
  | { phase: 'done'; hits: SearchHit[] }
  | { phase: 'problem'; message: string };

const OFF = 'Search is switched off on this server.';
const UNAVAILABLE = "Search isn't available right now. Try again in a moment.";

function Hit({ hit }: { hit: SearchHit }) {
  const link = pageLink(hit);
  const label = `${hitLabel(hit)} · page ${hit.page}`;
  return (
    <li className="hit">
      {link ? (
        <a href={link} target="_blank" rel="noopener noreferrer" className="doc-link">
          {label}
        </a>
      ) : (
        <span>{label}</span>
      )}
      <p className="hit-excerpt">{hit.excerpt}</p>
    </li>
  );
}

/**
 * "Search the filings" for one stock (P10b): the passages closest in meaning to a question, each
 * linked to its page of the official filing. It finds; it does not answer (that is P12's chat).
 */
export function SearchBox({ symbol, stockName }: { symbol: string; stockName: string }) {
  const [question, setQuestion] = useState('');
  const [state, setState] = useState<State>({ phase: 'idle' });
  const ready = question.trim().length >= MIN_CHARS && state.phase !== 'searching';

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    setState({ phase: 'searching' });
    try {
      const outcome = await searchFilings(question.trim(), symbol);
      if (outcome.status === 'ok') setState({ phase: 'done', hits: outcome.hits });
      else
        setState({
          phase: 'problem',
          message: outcome.status === 'off' ? OFF : UNAVAILABLE,
        });
    } catch {
      setState({ phase: 'problem', message: UNAVAILABLE });
    }
  };

  return (
    <section className="search" aria-label={`Search ${stockName}`}>
      <form role="search" className="search-form" onSubmit={(event) => void submit(event)}>
        <input
          type="search"
          aria-label={`Search ${stockName}’s filings`}
          placeholder="Search the filings, e.g. employee attrition"
          value={question}
          maxLength={300}
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button type="submit" className="button" disabled={!ready}>
          Search
        </button>
      </form>
      {state.phase === 'searching' && (
        <p role="status" className="muted">
          Searching…
        </p>
      )}
      {state.phase === 'problem' && (
        <p role="alert" className="alert">
          {state.message}
        </p>
      )}
      {state.phase === 'done' &&
        (state.hits.length === 0 ? (
          <p className="muted">No matching passages.</p>
        ) : (
          <ol className="hits" aria-label="Search results">
            {state.hits.map((hit) => (
              <Hit key={`${hit.document_id}-${hit.page}-${hit.excerpt.slice(0, 20)}`} hit={hit} />
            ))}
          </ol>
        ))}
    </section>
  );
}
