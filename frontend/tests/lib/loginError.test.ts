import { describe, expect, it } from 'vitest';

import { loginErrorMessage } from '@/lib/loginError';

describe('loginErrorMessage', () => {
  it('shows nothing when there was no login error', () => {
    expect(loginErrorMessage(null)).toBeNull();
  });

  it('has its own message for a cancelled login', () => {
    expect(loginErrorMessage('cancelled')).toMatch(/cancel/i);
  });

  it('says the demo is invite-only when the account is not on the list', () => {
    expect(loginErrorMessage('not_invited')).toMatch(/invite-only/i);
  });

  it('has a generic message for a failed login', () => {
    expect(loginErrorMessage('login_failed')).toMatch(/couldn.t sign you in/i);
  });

  it('gives an unknown value the generic message and never repeats it back', () => {
    const hostile = '<img src=x onerror=alert(1)>';
    const shown = loginErrorMessage(hostile);

    expect(shown).toBe(loginErrorMessage('login_failed'));
    expect(shown).not.toContain(hostile);
  });

  // A plain object lookup would answer these with inherited functions instead of text.
  it.each(['__proto__', 'constructor', 'toString', 'hasOwnProperty'])(
    'is not fooled by the inherited object property %s',
    (name) => {
      expect(loginErrorMessage(name)).toBe(loginErrorMessage('login_failed'));
    },
  );

  it('treats an empty value like any other unknown one', () => {
    expect(loginErrorMessage('')).toBe(loginErrorMessage('login_failed'));
  });
});
