/**
 * Who is signed in. The browser holds the session as an HttpOnly cookie, so the only way to find out
 * is to ask the backend: GET /api/v1/me answers 200 (signed in) or 401 (not).
 */

import { useEffect, useState } from 'react';

import { ApiError, apiFetch } from '@/lib/api';

export type Me = { id: string; email: string };

export type MeState =
  | { status: 'loading'; user?: undefined }
  | { status: 'signed-out'; user?: undefined }
  | { status: 'error'; user?: undefined }
  | { status: 'signed-in'; user: Me };

function isMe(value: unknown): value is Me {
  const candidate = value as Partial<Me> | null;
  return typeof candidate?.id === 'string' && typeof candidate.email === 'string';
}

export function useMe(): MeState {
  const [state, setState] = useState<MeState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    apiFetch('/api/v1/me')
      .then((body) => {
        if (!cancelled) setState(isMe(body) ? { status: 'signed-in', user: body } : { status: 'error' });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        // Only a 401 means "not signed in". A 500 or a dropped connection is a failure of ours,
        // and bouncing the user to the sign-in page would make it look like they were logged out.
        setState(error instanceof ApiError && error.status === 401 ? { status: 'signed-out' } : { status: 'error' });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}

export async function signOut(): Promise<void> {
  await apiFetch('/api/v1/auth/logout', { method: 'POST' });
}
