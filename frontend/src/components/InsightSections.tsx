/**
 * The four sections of the stock page: key facts, derived values, sentiment and events. Each one
 * only shows what the server sent, with its source beside it: on this page as a small link icon
 * (the owner, 2026-09-30), the full source in its name and on hover. React renders every value as
 * text, so nothing from the server is ever HTML.
 */

import { ExternalIcon } from '@/components/Icons';
import {
  basisNote,
  capitalise,
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

/**
 * A source: a link that opens it in a new tab, or plain text if its address is unsafe. ``icon``
 * (the stock page) shows the link as a small icon; its name is still the full source, and hovering
 * shows the filing's own words, or the source when there are none.
 */
export function CitationChip({ citation, icon = false }: { citation: Citation; icon?: boolean }) {
  const link = citationLink(citation);
  const title = citation.quote ?? undefined; // hovering shows the filing's own words
  const className = citation.source === 'rbi' ? 'chip chip-rbi' : 'chip'; // marks RBI sources
  if (link && icon) {
    return (
      <a
        href={link}
        target="_blank"
        rel="noopener noreferrer"
        className={citation.source === 'rbi' ? 'source-icon rbi' : 'source-icon'}
        aria-label={citation.label}
        title={title ?? citation.label}
      >
        <ExternalIcon />
      </a>
    );
  }
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

export function Citations({ citations, icon = false }: { citations: Citation[]; icon?: boolean }) {
  if (citations.length === 0) return null;
  return (
    <span className="chips">
      {citations.map((citation, index) => (
        <CitationChip key={`${index}-${citation.label}`} citation={citation} icon={icon} />
      ))}
    </span>
  );
}

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`;

/**
 * One period's figure: the amount, the period, any basis note, and where it came from. Where
 * sources disagree, only the figure the server chose (the best-ranked source, ADR 020) is shown,
 * with its own source: the owner's choice for this page (2026-09-30); the chat still names the
 * other source's figure beside it.
 */
function FactEntry({ fact }: { fact: KeyFact }) {
  const note = basisNote(fact.basis);
  return (
    <div className="fact">
      <span className="fact-value">{formatAmount(fact.value, fact.unit)}</span>
      <span className="fact-period">{periodLabel(fact.period)}</span>
      {note && <span className="fact-note">{note}</span>}
      {fact.status === 'agreed' && fact.corroborated_by > 0 && (
        <span className="fact-note">{`agreed by ${plural(fact.corroborated_by, 'other source')}`}</span>
      )}
      <CitationChip citation={fact.citation} icon />
    </div>
  );
}

/** One card per metric: the newest figure large, older periods listed underneath. */
export function KeyFacts({ facts }: { facts: KeyFact[] }) {
  const table = factTable(facts);
  return (
    <section aria-labelledby="key-facts" className="stock-section">
      <h2 id="key-facts">Key facts</h2>
      {table.rows.length === 0 ? (
        <p className="muted">No facts extracted yet.</p>
      ) : (
        <ul className="figures">
          {table.rows.map((row) => {
            const [latest, ...earlier] = row.cells.filter((cell): cell is KeyFact => cell !== null);
            return (
              <li key={row.metric} className="figure-card">
                <h3>{row.label}</h3>
                {latest && <FactEntry fact={latest} />}
                {earlier.length > 0 && (
                  <ul className="figure-earlier" aria-label={`Earlier ${row.label}`}>
                    {earlier.map((fact) => (
                      <li key={fact.period}>
                        <FactEntry fact={fact} />
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/** Growth reads green or red by its sign; nothing else is coloured. */
function valueClass(item: DerivedValue): string {
  if (item.status !== 'ok') return 'derived-value muted';
  const growth = item.name === 'revenue_growth' || item.name === 'profit_growth';
  const number = Number(item.value);
  if (growth && number > 0) return 'derived-value rise';
  if (growth && number < 0) return 'derived-value fall';
  return 'derived-value';
}

/** Debt to equity, growth and dividend: the value (or why there is none), its reason and sources. */
export function DerivedValues({ derived }: { derived: DerivedValue[] }) {
  return (
    <section aria-labelledby="derived-values" className="stock-section">
      <h2 id="derived-values">Derived values</h2>
      <ul className="figures derived-list">
        {derived.map((item) => (
          <li key={item.name} className="figure-card derived">
            <span className="derived-name">{item.label}</span>
            <span className={valueClass(item)}>{derivedValueLabel(item)}</span>
            <p className="derived-reason">{item.reason}</p>
            <Citations citations={item.citations} icon />
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
    <section aria-labelledby="recent-sentiment" className="stock-section">
      <h2 id="recent-sentiment">Recent sentiment</h2>
      <p className="sentiment-line panel">
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
    <section aria-labelledby="recent-events" className="stock-section">
      <h2 id="recent-events">Recent events</h2>
      {events.length === 0 ? (
        <p className="muted">No events extracted yet.</p>
      ) : (
        <ol className="events panel">
          {events.map((event, index) => {
            const date = eventDateLabel(event.event_date); // "22 Sep 2026"
            const [day, ...monthYear] = date.split(' ');
            // A day with several events shows its date once, on the first of them.
            const newDay = index === 0 || events[index - 1]?.event_date !== event.event_date;
            return (
              <li
                key={`${index}-${event.event_date}`}
                className={newDay ? 'event' : 'event same-day'}
              >
                <div className="event-date" aria-hidden="true">
                  {newDay && (
                    <>
                      <span className="event-day">{day}</span>
                      <span className="event-month">{monthYear.join(' ')}</span>
                    </>
                  )}
                </div>
                <div className="event-body">
                  <p className="event-summary">
                    <span>{event.summary}</span>
                    <CitationChip citation={event.citation} icon />
                  </p>
                  <p className="event-meta">
                    <time dateTime={event.event_date} className="visually-hidden">
                      {date}
                    </time>
                    <span className="event-type">{eventTypeLabel(event.event_type)}</span>
                    <span className={`event-tone ${event.sentiment}`}>
                      {capitalise(event.sentiment)}
                    </span>
                    <span className="event-impact">{`${capitalise(event.impact)} impact`}</span>
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
