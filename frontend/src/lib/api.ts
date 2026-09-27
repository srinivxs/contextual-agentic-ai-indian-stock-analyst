/**
 * The one place the frontend talks to the backend.
 *
 * Two rules are enforced here so no caller can break them:
 * - Same origin only. A path must be under /api/v1/ and cannot be absolute, protocol-relative or
 *   climb out with "..". The browser therefore only ever contacts our own server.
 * - No credentials of our own. The session is an HttpOnly cookie that the browser attaches; script
 *   never reads it, stores it, or sends it by hand.
 */

export type Method = 'GET' | 'PUT' | 'DELETE' | 'POST';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;

  constructor(status: number, code: string, requestId: string | null = null) {
    // Deliberately not the server's message: what the user sees is decided by our own components.
    super(`API request failed (${status} ${code})`);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

const API_PREFIX = '/api/v1/';

function assertOurOwnApiPath(path: string): void {
  // Backslashes and control characters are treated by browsers as slashes or ignored, which would
  // let "/api/v1/..\\x" or "/api/v1/\t../x" slip past a segment check.
  const strange = /[\\\u0000-\u001f]/.test(path);
  const segments = path.split('?')[0]?.split('/') ?? [];
  const climbs = segments.some((segment) => {
    const plain = segment.replace(/%2e/gi, '.');
    return plain === '..' || plain === '.';
  });
  if (!path.startsWith(API_PREFIX) || strange || climbs) {
    throw new Error('apiFetch only calls paths under /api/v1/');
  }
}

async function errorFrom(response: Response): Promise<ApiError> {
  try {
    const body: unknown = await response.json();
    const error = (body as { error?: { code?: unknown; request_id?: unknown } } | null)?.error;
    if (error && typeof error.code === 'string') {
      const requestId = typeof error.request_id === 'string' ? error.request_id : null;
      return new ApiError(response.status, error.code, requestId);
    }
  } catch {
    // Not JSON at all (an HTML error page from a proxy, for example).
  }
  return new ApiError(response.status, 'unexpected_response');
}

/**
 * Returns the parsed JSON body, or undefined for 204. Throws ApiError on any failure.
 * A `body`, when given, is sent as JSON; the path rules above apply exactly the same.
 */
export async function apiFetch(
  path: string,
  init: { method?: Method; body?: unknown } = {},
): Promise<unknown> {
  assertOurOwnApiPath(path);

  const request: RequestInit = { method: init.method ?? 'GET', credentials: 'same-origin' };
  if (init.body !== undefined) {
    request.body = JSON.stringify(init.body);
    request.headers = { 'content-type': 'application/json' };
  }

  let response: Response;
  try {
    response = await fetch(path, request);
  } catch {
    throw new ApiError(0, 'network_error'); // never pass the original message on
  }

  if (!response.ok) throw await errorFrom(response);
  if (response.status === 204) return undefined;
  try {
    return (await response.json()) as unknown;
  } catch {
    throw new ApiError(response.status, 'unexpected_response');
  }
}
