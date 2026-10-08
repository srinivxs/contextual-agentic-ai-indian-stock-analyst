import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { signOut, useMe } from '@/lib/session';
import { installFakeApi } from '../helpers/fakeApi';

describe('useMe', () => {
  it('starts as loading, then reports the signed-in user', async () => {
    installFakeApi({ email: 'ada@example.test' });
    const { result } = renderHook(() => useMe());

    expect(result.current.status).toBe('loading');
    await waitFor(() => expect(result.current.status).toBe('signed-in'));
    expect(result.current.user?.email).toBe('ada@example.test');
  });

  it('reports signed-out on a 401', async () => {
    installFakeApi({ signedIn: false });
    const { result } = renderHook(() => useMe());

    await waitFor(() => expect(result.current.status).toBe('signed-out'));
    expect(result.current.user).toBeUndefined();
  });

  it('reports an error, NOT signed-out, when the server fails', async () => {
    // Bouncing someone to the login page because of a 500 would look like being logged out.
    const api = installFakeApi();
    api.failWith('GET /api/v1/me', 500);
    const { result } = renderHook(() => useMe());

    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current).toMatchObject({ status: 'error', offline: false });
  });

  it.each([502, 504])(
    'reports the backend as off (not a fault) when CloudFront answers %i',
    async (status) => {
      // The AWS stack is switched off between sessions: nothing stands behind /api.
      const api = installFakeApi();
      api.failWith('GET /api/v1/me', status);
      const { result } = renderHook(() => useMe());

      await waitFor(() => expect(result.current.status).toBe('error'));
      expect(result.current).toMatchObject({ status: 'error', offline: true });
    },
  );

  it('reports the backend as off when the network cannot reach it at all', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    try {
      const { result } = renderHook(() => useMe());
      await waitFor(() => expect(result.current.status).toBe('error'));
      expect(result.current).toMatchObject({ status: 'error', offline: true });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('signOut', () => {
  it('posts to the logout endpoint', async () => {
    const api = installFakeApi();
    await signOut();
    expect(api.requests).toContain('POST /api/v1/auth/logout');
  });

  it('rejects when the server refuses, so the caller can say so', async () => {
    const api = installFakeApi();
    api.failWith('POST /api/v1/auth/logout', 500);
    await expect(signOut()).rejects.toMatchObject({ status: 500 });
  });
});
