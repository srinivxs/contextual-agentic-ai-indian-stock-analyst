import { describe, expect, it, vi } from 'vitest';

import { ApiError, apiFetch } from '@/lib/api';

const jsonResponse = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

function stubFetch(response: Response | Error): ReturnType<typeof vi.fn> {
  const mock = vi.fn(async () => {
    if (response instanceof Error) throw response;
    return response;
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

describe('apiFetch', () => {
  it('sends a same-origin request with the cookie and nothing else identifying', async () => {
    const mock = stubFetch(jsonResponse(200, { ok: true }));
    await apiFetch('/api/v1/me');

    expect(mock).toHaveBeenCalledTimes(1);
    const [url, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/v1/me');
    expect(init.method).toBe('GET');
    expect(init.credentials).toBe('same-origin');
    // The session is an HttpOnly cookie: JavaScript never adds credentials of its own.
    const headers = new Headers(init.headers);
    expect(headers.has('authorization')).toBe(false);
    expect(headers.has('cookie')).toBe(false);
  });

  it('returns the parsed JSON body', async () => {
    stubFetch(jsonResponse(200, { id: 'u1', email: 'a@example.test' }));
    await expect(apiFetch('/api/v1/me')).resolves.toEqual({ id: 'u1', email: 'a@example.test' });
  });

  it('returns undefined for 204 No Content', async () => {
    stubFetch(new Response(null, { status: 204 }));
    await expect(apiFetch('/api/v1/stocks/TCS/follow', { method: 'PUT' })).resolves.toBeUndefined();
  });

  it.each(['PUT', 'DELETE', 'POST'] as const)('passes the %s method through', async (method) => {
    const mock = stubFetch(new Response(null, { status: 204 }));
    await apiFetch('/api/v1/x', { method });
    expect((mock.mock.calls[0] as unknown as [string, RequestInit])[1].method).toBe(method);
  });

  it('sends a body as JSON, labelled as JSON, with still no credentials of its own', async () => {
    const mock = stubFetch(jsonResponse(200, { ok: true }));
    await apiFetch('/api/v1/chat/messages', {
      method: 'POST',
      body: { question: 'How did revenue grow?', conversation_id: null },
    });

    const [, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('same-origin');
    expect(init.body).toBe('{"question":"How did revenue grow?","conversation_id":null}');
    const headers = new Headers(init.headers);
    expect(headers.get('content-type')).toBe('application/json');
    expect(headers.has('authorization')).toBe(false);
    expect(headers.has('cookie')).toBe(false);
  });

  it('sends no body and no content type when there is nothing to send', async () => {
    const mock = stubFetch(new Response(null, { status: 204 }));
    await apiFetch('/api/v1/auth/logout', { method: 'POST' });

    const [, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.body).toBeUndefined();
    expect(new Headers(init.headers).has('content-type')).toBe(false);
  });

  it('refuses a body for a path outside /api/v1 without making any request', async () => {
    const mock = stubFetch(jsonResponse(200, {}));
    await expect(
      apiFetch('https://evil.example/api/v1/chat/messages', { method: 'POST', body: {} }),
    ).rejects.toThrow();
    expect(mock).not.toHaveBeenCalled();
  });

  it('turns the backend error envelope into an ApiError', async () => {
    stubFetch(
      jsonResponse(404, { error: { code: 'not_found', message: 'Not Found', request_id: 'abc' } }),
    );
    const failure = await apiFetch('/api/v1/nope').catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 404, code: 'not_found', requestId: 'abc' });
  });

  it('does not choke on an error that is not our envelope (an HTML 502 from a proxy)', async () => {
    stubFetch(new Response('<html>Bad Gateway</html>', { status: 502 }));
    const failure = await apiFetch('/api/v1/me').catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 502, code: 'unexpected_response' });
  });

  it('treats a success response that is not JSON as an error', async () => {
    stubFetch(new Response('not json', { status: 200 }));
    const failure = await apiFetch('/api/v1/me').catch((error: unknown) => error);

    expect(failure).toMatchObject({ code: 'unexpected_response' });
  });

  it('reports a network failure as status 0 without leaking the original message', async () => {
    stubFetch(new TypeError('connect ECONNREFUSED 10.0.0.7:8000'));
    const failure = await apiFetch('/api/v1/me').catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 0, code: 'network_error' });
    expect(String((failure as Error).message)).not.toContain('10.0.0.7');
  });

  it.each([
    ['an absolute URL', 'https://evil.example/api/v1/me'],
    ['a protocol-relative URL', '//evil.example/api/v1/me'],
    ['another API version', '/api/v2/me'],
    ['a relative path', 'api/v1/me'],
    ['a path that climbs out of /api/v1', '/api/v1/../admin'],
    ['a parent segment written as %2e%2e (browsers treat it as ..)', '/api/v1/%2e%2e/admin'],
    ['a single-dot segment', '/api/v1/./me'],
    ['a backslash (browsers treat it as a slash)', '/api/v1/..\\admin'],
    ['a control character (browsers ignore it)', '/api/v1/\t../admin'],
    ['a non-API path', '/stocks/'],
  ])('refuses %s without making any request', async (_label, path) => {
    const mock = stubFetch(jsonResponse(200, {}));
    await expect(apiFetch(path)).rejects.toThrow();
    expect(mock).not.toHaveBeenCalled();
  });
});
