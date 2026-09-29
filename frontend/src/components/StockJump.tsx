'use client';

import { useEffect, useId, useRef, useState } from 'react';

import { SearchIcon } from '@/components/Icons';

/** The three stocks the app follows (seeded by migration 0001; the app never adds more). */
export const STOCKS = [
  { symbol: 'RELIANCE', name: 'Reliance Industries', words: 'reliance industries ril' },
  { symbol: 'TCS', name: 'Tata Consultancy Services', words: 'tcs tata consultancy services' },
  { symbol: 'HDFCBANK', name: 'HDFC Bank', words: 'hdfcbank hdfc bank' },
] as const;

/** The stocks whose ticker or name contains every word typed, in the usual order. */
export function findStocks(query: string): (typeof STOCKS)[number][] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  return STOCKS.filter((stock) => words.every((word) => stock.words.includes(word)));
}

export function stockHref(symbol: string): string {
  return `/stock/?symbol=${encodeURIComponent(symbol)}`;
}

/** The search box in the top bar: type part of a name or ticker, pick a stock, open its page. */
export function StockJump() {
  const [query, setQuery] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const found = findStocks(query);

  // "/" jumps to the box from anywhere on the page, as on many search-first sites.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        target !== null &&
        (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
      if (event.key === '/' && !typing) {
        event.preventDefault();
        input.current?.focus();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <form
      className="jump"
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        const first = found[0];
        if (first !== undefined) window.location.assign(stockHref(first.symbol));
      }}
    >
      <SearchIcon />
      <label htmlFor={`${listId}-q`} className="visually-hidden">
        Find a stock
      </label>
      <input
        ref={input}
        id={`${listId}-q`}
        type="search"
        placeholder="Find a stock: RELIANCE, TCS, HDFC Bank"
        autoComplete="off"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        aria-controls={listId}
      />
      <kbd className="jump-key" aria-hidden="true">
        /
      </kbd>
      {query.trim() !== '' && (
        <ul id={listId} className="jump-results" aria-label="Matching stocks">
          {found.length === 0 ? (
            <li className="jump-empty">This app follows RELIANCE, TCS and HDFC Bank only.</li>
          ) : (
            found.map((stock) => (
              <li key={stock.symbol}>
                <a href={stockHref(stock.symbol)}>
                  <span className="symbol">{stock.symbol}</span>
                  <span className="muted">{stock.name}</span>
                </a>
              </li>
            ))
          )}
        </ul>
      )}
    </form>
  );
}
