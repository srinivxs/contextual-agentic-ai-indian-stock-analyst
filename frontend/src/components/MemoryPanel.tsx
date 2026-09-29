'use client';

import { useEffect, useState } from 'react';

import type { ReactNode } from 'react';

import { ClockIcon, LeafIcon, PencilIcon, ShieldIcon, TrendIcon } from '@/components/Icons';
import {
  forgetAll,
  forgetField,
  getProfile,
  setField,
  type ProfileChoice,
  type ProfileFieldEntry,
  type ProfileFieldName,
} from '@/lib/profile';

/**
 * The investor memory (P13): what the chat has remembered about the user, shown on the Match
 * page. Every field can be edited by hand or forgotten; nothing here decides what to
 * remember, that is the chat's job from the user's own words.
 */

const HEADING = 'Your investor profile';
const EMPTY =
  "Nothing yet. Tell the chat about yourself, for example: I'm conservative, dividend-focused and I avoid high debt.";
const LOAD_FAILED = "We couldn't load what we remember about you.";
const SAVE_FAILED = "That choice wasn't accepted.";
const ACTION_FAILED = 'Something went wrong. Try again.';

const FIELD_ICONS: Record<ProfileFieldName, ReactNode> = {
  risk_preference: <LeafIcon />,
  debt_preference: <ShieldIcon />,
  investment_style: <TrendIcon />,
  other_preferences: <ClockIcon />,
};

/** The round, softly tinted icon at the start of a row. */
const FieldIcon = ({ field }: { field: ProfileFieldName }) => (
  <span className={`memory-icon ${field}`} aria-hidden="true">
    {FIELD_ICONS[field]}
  </span>
);

type Phase = 'loading' | 'ready' | 'error';
type Busy = ProfileFieldName | 'all' | null;

function Editor({
  choice,
  initial,
  busy,
  onCancel,
  onSave,
}: {
  choice: ProfileChoice;
  initial: string[];
  busy: boolean;
  onCancel: () => void;
  onSave: (values: string[]) => void;
}) {
  const [selected, setSelected] = useState<string[]>(initial);

  const toggle = (value: string, checked: boolean) => {
    if (choice.single) {
      setSelected(checked ? [value] : []);
      return;
    }
    setSelected((current) => (checked ? [...current, value] : current.filter((v) => v !== value)));
  };

  const valid = selected.length > 0;

  return (
    <fieldset className="memory-editor">
      <legend>{choice.label}</legend>
      {choice.options.map((option) => (
        <label key={option.value} className="memory-option">
          <input
            type={choice.single ? 'radio' : 'checkbox'}
            name={choice.field}
            aria-label={option.label}
            checked={selected.includes(option.value)}
            disabled={busy}
            onChange={(event) => toggle(option.value, event.target.checked)}
          />
          {option.label}
        </label>
      ))}
      <div className="memory-actions">
        <button
          type="button"
          className="button"
          aria-label={`Save ${choice.label}`}
          disabled={!valid || busy}
          onClick={() => onSave(selected)}
        >
          Save
        </button>
        <button
          type="button"
          className="button secondary"
          aria-label={`Cancel editing ${choice.label}`}
          disabled={busy}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </fieldset>
  );
}

function Remembered({
  choice,
  entry,
  busy,
  onEdit,
  onForget,
}: {
  choice: ProfileChoice;
  entry: ProfileFieldEntry;
  busy: boolean;
  onEdit: () => void;
  onForget: () => void;
}) {
  return (
    <>
      <div className="memory-row">
        <FieldIcon field={choice.field} />
        <p className="memory-field-label">{choice.label}</p>
        <p className="memory-chips">
          {entry.labels.map((label) => (
            <span key={label} className="memory-chip">
              {label}
            </span>
          ))}
        </p>
      </div>
      <div className="memory-detail">
        <p className="memory-source muted">
          {entry.source === 'chat' && entry.quote ? `You said: “${entry.quote}”` : 'Set by you'}
        </p>
        <p className="memory-date muted">
          <time dateTime={entry.updated_at}>{entry.updated_at.slice(0, 10)}</time>
        </p>
        <div className="memory-actions">
          <button
            type="button"
            className="memory-link"
            aria-label={`Edit ${choice.label}`}
            disabled={busy}
            onClick={onEdit}
          >
            <PencilIcon />
            Edit
          </button>
          <button
            type="button"
            className="memory-link"
            aria-label={`Forget ${choice.label}`}
            disabled={busy}
            onClick={onForget}
          >
            Forget
          </button>
        </div>
      </div>
    </>
  );
}

/** On the Match page: every field the chat (or the user) has set, and the fields still free. */
export function MemoryPanel({
  refreshSignal = 0,
  onChange,
}: {
  refreshSignal?: number;
  /** Called after the user's own save or forget went through, so a page using the profile can reload. */
  onChange?: () => void;
}) {
  const [phase, setPhase] = useState<Phase>('loading');
  const [fields, setFields] = useState<ProfileFieldEntry[]>([]);
  const [choices, setChoices] = useState<ProfileChoice[]>([]);
  const [editing, setEditing] = useState<ProfileFieldName | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [confirmForgetAll, setConfirmForgetAll] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getProfile().then(
      (profile) => {
        if (cancelled) return;
        setFields(profile.fields);
        setChoices(profile.choices);
        setPhase('ready');
        setError(null);
      },
      () => {
        if (cancelled) return;
        setPhase('error');
        setError(LOAD_FAILED);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [refreshSignal]);

  const entryFor = (field: ProfileFieldName): ProfileFieldEntry | undefined =>
    fields.find((f) => f.field === field);

  const save = async (choice: ProfileChoice, values: string[]) => {
    setBusy(choice.field);
    setError(null);
    try {
      const entry = await setField(choice.field, values);
      setFields((current) => [...current.filter((f) => f.field !== choice.field), entry]);
      setEditing(null);
      onChange?.();
    } catch {
      setError(SAVE_FAILED);
    } finally {
      setBusy(null);
    }
  };

  const forget = async (field: ProfileFieldName) => {
    setBusy(field);
    setError(null);
    try {
      await forgetField(field);
      setFields((current) => current.filter((f) => f.field !== field));
      onChange?.();
    } catch {
      setError(ACTION_FAILED);
    } finally {
      setBusy(null);
    }
  };

  const forgetEverything = async () => {
    setBusy('all');
    setError(null);
    try {
      await forgetAll();
      setFields([]);
      setConfirmForgetAll(false);
      onChange?.();
    } catch {
      setError(ACTION_FAILED);
    } finally {
      setBusy(null);
    }
  };

  const remembered = fields.length > 0;

  return (
    <section className="memory-panel" aria-label={HEADING}>
      <h2>{HEADING}</h2>
      {error && (
        <p role="alert" className="alert">
          {error}
        </p>
      )}
      {phase === 'loading' && (
        <p role="status" className="muted">
          Loading…
        </p>
      )}
      {phase === 'ready' && !remembered && !editing && <p className="muted">{EMPTY}</p>}
      {phase === 'ready' && (
        <ul className="memory-list">
          {choices.map((choice) => {
            const entry = entryFor(choice.field);
            const isEditing = editing === choice.field;
            const fieldBusy = busy === choice.field;
            return (
              <li key={choice.field} className="memory-item">
                {isEditing ? (
                  <Editor
                    choice={choice}
                    initial={entry?.values ?? []}
                    busy={fieldBusy}
                    onCancel={() => setEditing(null)}
                    onSave={(values) => void save(choice, values)}
                  />
                ) : entry ? (
                  <Remembered
                    choice={choice}
                    entry={entry}
                    busy={busy !== null}
                    onEdit={() => setEditing(choice.field)}
                    onForget={() => void forget(choice.field)}
                  />
                ) : (
                  <div className="memory-row">
                    <FieldIcon field={choice.field} />
                    <p className="memory-field-label">{choice.label}</p>
                    <button
                      type="button"
                      className="memory-link"
                      aria-label={`Add ${choice.label}`}
                      disabled={busy !== null}
                      onClick={() => setEditing(choice.field)}
                    >
                      Add
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {phase === 'ready' &&
        remembered &&
        (confirmForgetAll ? (
          <p className="memory-confirm">
            Forget everything?{' '}
            <button
              type="button"
              className="button"
              aria-label="Yes, forget everything"
              disabled={busy === 'all'}
              onClick={() => void forgetEverything()}
            >
              Yes
            </button>{' '}
            <button
              type="button"
              className="button secondary"
              aria-label="No, keep what is remembered"
              disabled={busy === 'all'}
              onClick={() => setConfirmForgetAll(false)}
            >
              No
            </button>
          </p>
        ) : (
          <button
            type="button"
            className="memory-link forget-all"
            onClick={() => setConfirmForgetAll(true)}
            disabled={busy !== null}
          >
            Forget everything
          </button>
        ))}
    </section>
  );
}
