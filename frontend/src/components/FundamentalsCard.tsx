/**
 * The Fundamentals card of the stock page (the owner, 2026-09-30): ten values in two columns, as
 * screener.in's company page gave them when last read (the date and a link to the page below),
 * with EPS (TTM) and P/B worked out from them and debt to equity from stored figures. Hovering a
 * value shows how it was worked out, or why it is missing.
 */

import { ExternalIcon } from '@/components/Icons';
import { screenerUrl } from '@/lib/documents';
import { eventDateLabel } from '@/lib/insights';
import { fundamentalValue, type Fundamentals } from '@/lib/fundamentals';

export type FundamentalsState =
  { phase: 'loading' } | { phase: 'problem' } | { phase: 'ready'; fundamentals: Fundamentals };

export function FundamentalsCard({ state }: { state: FundamentalsState }) {
  let body;
  if (state.phase === 'loading') {
    body = <p className="muted">Loading fundamentals…</p>;
  } else if (state.phase === 'problem') {
    body = (
      <p className="muted">{`We couldn't load the fundamentals. Reload the page to try again.`}</p>
    );
  } else {
    const { items, as_of: asOf, source_url: sourceUrl } = state.fundamentals;
    const link = screenerUrl(sourceUrl);
    body = (
      <>
        <dl className="fundamentals-grid">
          {items.map((item) => (
            <div key={item.name} className="fundamental">
              <dt>{item.label}</dt>
              <dd className={item.status === 'ok' ? 'num' : 'muted'} title={item.note ?? undefined}>
                {fundamentalValue(item)}
              </dd>
            </div>
          ))}
        </dl>
        <p className="muted fundamentals-source">
          {asOf
            ? `screener.in figures as of ${eventDateLabel(asOf.slice(0, 10))}; debt to equity from stored figures.`
            : 'screener.in figures not read yet: press Update data.'}
          {link && (
            <a
              href={link}
              target="_blank"
              rel="noopener noreferrer"
              className="source-icon"
              aria-label="screener.in company page"
              title="screener.in company page"
            >
              <ExternalIcon />
            </a>
          )}
        </p>
      </>
    );
  }
  return (
    <section aria-labelledby="fundamentals" className="stock-section">
      <h2 id="fundamentals">Fundamentals</h2>
      <div className="panel fundamentals-card">{body}</div>
    </section>
  );
}
