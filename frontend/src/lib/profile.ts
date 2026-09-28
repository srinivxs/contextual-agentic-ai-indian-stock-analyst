/**
 * The investor memory (P13): a small structured profile the chat writes from the user's own
 * messages, and the user can edit or delete themselves. The server holds the one true copy;
 * this file only asks it, checks the shape of what comes back, and never invents a field or a
 * choice the server did not send.
 */

import { ApiError, apiFetch } from '@/lib/api';

const FIELD_NAMES = [
  'risk_preference',
  'debt_preference',
  'investment_style',
  'other_preferences',
] as const;
const SOURCES = ['chat', 'edited'] as const;

export type ProfileFieldName = (typeof FIELD_NAMES)[number];

/** One remembered field: its value(s), the user's own words if any, and who set it last. */
export type ProfileFieldEntry = {
  field: ProfileFieldName;
  values: string[];
  labels: string[];
  /** The user's own supporting words; null once the field was set by hand instead. */
  quote: string | null;
  source: (typeof SOURCES)[number];
  updated_at: string;
};

export type ProfileOption = { value: string; label: string };

/** How a field may be edited by hand: one choice or several, from a fixed list of options. */
export type ProfileChoice = {
  field: ProfileFieldName;
  label: string;
  single: boolean;
  options: ProfileOption[];
};

export type Profile = { fields: ProfileFieldEntry[]; choices: ProfileChoice[] };

// Shape checks. Each one starts from `value ?? {}`, so null or a missing object simply fails.

const text = (value: unknown): value is string => typeof value === 'string';
const orNull = (value: unknown, test: (v: unknown) => boolean): boolean =>
  value === null || test(value);
const oneOf =
  (allowed: readonly string[]) =>
  (value: unknown): boolean =>
    text(value) && allowed.includes(value);
const isFieldName = (value: unknown): value is ProfileFieldName => oneOf(FIELD_NAMES)(value);

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(text);
}

function isFieldEntry(value: unknown): value is ProfileFieldEntry {
  const f = (value ?? {}) as Partial<ProfileFieldEntry>;
  return (
    isFieldName(f.field) &&
    isStringArray(f.values) &&
    isStringArray(f.labels) &&
    orNull(f.quote, text) &&
    oneOf(SOURCES)(f.source) &&
    text(f.updated_at)
  );
}

function isOption(value: unknown): value is ProfileOption {
  const o = (value ?? {}) as Partial<ProfileOption>;
  return text(o.value) && text(o.label);
}

function isChoice(value: unknown): value is ProfileChoice {
  const c = (value ?? {}) as Partial<ProfileChoice>;
  return (
    isFieldName(c.field) &&
    text(c.label) &&
    typeof c.single === 'boolean' &&
    Array.isArray(c.options) &&
    c.options.every(isOption)
  );
}

function isProfile(value: unknown): value is Profile {
  const p = (value ?? {}) as Partial<Profile>;
  return (
    Array.isArray(p.fields) &&
    p.fields.every(isFieldEntry) &&
    Array.isArray(p.choices) &&
    p.choices.every(isChoice)
  );
}

/**
 * A field name we are willing to put in a path. Anything else is answered here, as the server
 * would answer an unknown field (404), without asking it.
 */
function checkedField(field: string): ProfileFieldName {
  if (!isFieldName(field)) throw new ApiError(404, 'not_found');
  return field;
}

/** What is remembered now, and the fixed choices the editor offers for every field. */
export async function getProfile(): Promise<Profile> {
  const body = await apiFetch('/api/v1/profile');
  if (!isProfile(body)) throw new ApiError(200, 'unexpected_response');
  return body;
}

/** Set one field by hand. The server answers 422 if the values are not a valid choice. */
export async function setField(
  field: ProfileFieldName,
  values: string[],
): Promise<ProfileFieldEntry> {
  const body = await apiFetch(`/api/v1/profile/${encodeURIComponent(checkedField(field))}`, {
    method: 'PUT',
    body: { values },
  });
  if (!isFieldEntry(body)) throw new ApiError(200, 'unexpected_response');
  return body;
}

/** Forget one field. */
export async function forgetField(field: ProfileFieldName): Promise<void> {
  await apiFetch(`/api/v1/profile/${encodeURIComponent(checkedField(field))}`, {
    method: 'DELETE',
  });
}

/** Forget everything remembered about the user. */
export async function forgetAll(): Promise<void> {
  await apiFetch('/api/v1/profile', { method: 'DELETE' });
}
