/**
 * What to tell the user when the backend sends them back with ?login_error=<code>.
 *
 * The code comes from the address bar, so it is untrusted: only the values the backend really
 * sends are recognised, and anything else gets the generic message. The raw value is never shown.
 */

const GENERIC = "We couldn't sign you in. Please try again.";

// A Map, not an object: `messages['constructor']` on a plain object would find an inherited function.
const MESSAGES = new Map<string, string>([
  ['cancelled', 'Sign-in was cancelled. You can try again whenever you like.'],
  ['not_invited', 'This demo is invite-only, and that Google account is not on the list.'],
  ['login_failed', GENERIC],
]);

export function loginErrorMessage(code: string | null): string | null {
  if (code === null) return null;
  return MESSAGES.get(code) ?? GENERIC;
}
