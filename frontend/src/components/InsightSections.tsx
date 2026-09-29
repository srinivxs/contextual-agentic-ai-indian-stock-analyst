/**
 * The four sections of the stock page: key facts, derived values, sentiment and events. Each one
 * only shows what the server sent, with its source beside it. React renders every value as text,
 * so nothing from the server is ever HTML.
 */

import {
  basisNote,
  citationLink,
  derivedValueLabel,
  eventDateLabel,
  eventTypeLabel,
  factTable,
  formatAmount,
  periodLabel,
  scoreLabel,
  sentimentLabel,
  type Citation,
  type DerivedValue,
  type KeyFact,
  type Sentiment,
  type StockEvent,
} from '@/lib/insights';

/** A small source label: a link that opens the source in a new tab, or plain text if unsafe. */
export function CitationChip({ citation }: { citation: Citation }) {
  const link = citationLink(citation);
  const title = citation.quote ?? undefined; // hovering shows the filing's own words
  const className = citation.source === 'rbi' ? 'chip chip-rbi' : 'chip'; // marks RBI sources
  return link ? (
    <a href={link} target="_blank" rel="noopener noreferrer" className={className} title={title}>
      {citation.label}
    </a>
  ) : (
    <span className={className} title={title}>
      {citation.label}
    </span>
  );
}

export function Citations({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <span className="chips">
      {citations.map((citation, index) => (
        <CitationChip key={`${index}-${citation.label}`} citation={citation} />
      ))}
    </span>
  );
}

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`;

function FactCell({ fact }: { fact: KeyFact | null }) {
  if (fact === null) {
    return (
      <td className="fact-cell muted" aria-label="No figure">
        –
      </td>
    );
  }
  const note = basisNote(fact.basis);
  const disputed = fact.status === 'disputed';
  return (
    <td className="fact-cell">
      <span className="fact-value">{formatAmount(fact.value, fact.unit)}</span>
      {note && <span className="fact-note">{note}</span>}
      {disputed && <span className="pill disputed">disputed</span>}
      {fact.status === 'agreed' && fact.corroborated_by > 0 && (
        <span className="fact-note">{`agreed by ${plural(fact.corroborated_by, 'other source')}`}</span>
      )}
      <CitationChip citation={fact.citation} />
      {disputed && fact.disputed_by.length > 0 && (
        <details className="fact-dispute">
          <summary>Sources that disagree</summary>
          <ul>
            {fact.disputed_by.map((citation, index) => (
              <li key={`${index}-${citation.label}`}>
                <CitationChip citation={citation} />
              </li>
            ))}
          </ul>
        </details>
      )}
    </td>
  );
}

/** One row per metric, one column per period, newest first. */
export function KeyFacts({ facts }: { facts: KeyFact[] }) {
  const table = factTable(facts);
  return (
    <section aria-labelledby="key-facts" className="insight">
      <h2 id="key-facts">Key facts</h2>
      {table.rows.length === 0 ? (
        <p className="muted">No facts extracted yet.</p>
      ) : (
        <div className="facts-scroll">
          <table className="facts">
            <thead>
              <tr>
                <th scope="col">Metric</th>
                {table.periods.map((period) => (
                  <th scope="col" key={period}>
                    {periodLabel(period)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row) => (
                <tr key={row.metric}>
                  <th scope="row">{row.label}</th>
                  {row.cells.map((fact, index) => (
                    <FactCell key={table.periods[index]} fact={fact} />
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Debt to equity, growth and dividend: the value (or why there is none), its reason and sources. */
export function DerivedValues({ derived }: { derived: DerivedValue[] }) {
  return (
    <section aria-labelledby="derived-values" className="insight">
      <h2 id="derived-values">Derived values</h2>
      <ul className="derived-list">
        {derived.map((item) => (
          <li key={item.name} className="derived">
            <span className="derived-name">{item.label}</span>
            <span className={item.status === 'ok' ? 'derived-value' : 'derived-value muted'}>
              {derivedValueLabel(item)}
            </span>
            <p className="derived-reason">{item.reason}</p>
            <Citations citations={item.citations} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The badge, and the score behind it. */
export function RecentSentiment({ sentiment }: { sentiment: Sentiment }) {
  const known = sentiment.status === 'ok' && sentiment.label !== null;
  return (
    <section aria-labelledby="recent-sentiment" className="insight">
      <h2 id="recent-sentiment">Recent sentiment</h2>
      <p className="sentiment-line">
        <span className={known ? `sentiment ${sentiment.label}` : 'sentiment none'}>
          {sentimentLabel(sentiment)}
        </span>
        {known && sentiment.score !== null && (
          <span className="muted">
            {`Score ${scoreLabel(sentiment.score)} from ${plural(sentiment.events_counted, 'event')}`}
          </span>
        )}
      </p>
    </section>
  );
}

/** Newest first: date, type, sentiment and impact, the summary and its source. */
export function RecentEvents({ events }: { events: StockEvent[] }) {
  return (
    <section aria-labelledby="recent-events" className="insight">
      <h2 id="recent-events">Recent events</h2>
      {events.length === 0 ? (
        <p className="muted">No events extracted yet.</p>
      ) : (
        <ol className="events">
          {events.map((event, index) => (
            <li key={`${index}-${event.event_date}`} className="event">
              <p className="event-head">
                <time dateTime={event.event_date}>{eventDateLabel(event.event_date)}</time>
                <span className="event-type">{eventTypeLabel(event.event_type)}</span>
                <span className="muted">{`${event.sentiment} · ${event.impact} impact`}</span>
              </p>
              <p className="event-summary">{event.summary}</p>
              <CitationChip citation={event.citation} />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
