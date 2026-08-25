/**
 * The single place the frontend talks to the API.
 *
 * Every panel previously built its own fetch, hand-wrote the Authorization
 * header and swallowed failures into a local string. Nothing handled 401, so
 * when the access token expired the app kept rendering "unavailable" forever
 * with no way back to the sign-in screen. Expiry is now a first-class event:
 * the client reports it once and the session is torn down deliberately.
 */

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }

  /** True when the caller aborted the request rather than the server failing. */
  static isAbort(error: unknown): boolean {
    return error instanceof DOMException && error.name === 'AbortError';
  }
}

export interface ApiClient {
  get<T>(path: string, init?: RequestInit): Promise<T>;
  post<T>(path: string, body: unknown, init?: RequestInit): Promise<T>;
  patch<T>(path: string, body: unknown, init?: RequestInit): Promise<T>;
  del(path: string, init?: RequestInit): Promise<void>;
  /** Escape hatch for non-JSON exchanges such as the WebRTC SDP handshake. */
  raw(path: string, init?: RequestInit): Promise<Response>;
  accessToken: string;
}

async function describeFailure(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === 'string' && body.detail) return body.detail;
  } catch {
    // A non-JSON error body is expected for gateway failures.
  }
  return fallback;
}

export function createApiClient(accessToken: string, onUnauthorized: () => void): ApiClient {
  let expiryReported = false;

  async function raw(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    headers.set('Authorization', `Bearer ${accessToken}`);

    const response = await fetch(path, { ...init, headers });

    if (response.status === 401) {
      // One notification per client, however many panels are mid-flight.
      if (!expiryReported) {
        expiryReported = true;
        onUnauthorized();
      }
      throw new ApiError(401, 'Your session has expired. Please sign in again.');
    }
    return response;
  }

  async function send<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
    const response = await raw(path, init);
    if (!response.ok) {
      throw new ApiError(response.status, await describeFailure(response, fallback));
    }
    return (await response.json()) as T;
  }

  function withBody(method: string, body: unknown, init: RequestInit = {}): RequestInit {
    const headers = new Headers(init.headers);
    headers.set('Content-Type', 'application/json');
    return { ...init, method, headers, body: JSON.stringify(body) };
  }

  return {
    accessToken,
    raw,
    get: (path, init) => send(path, { ...init, method: 'GET' }, `Request to ${path} failed.`),
    post: (path, body, init) => send(path, withBody('POST', body, init), `Request to ${path} failed.`),
    patch: (path, body, init) => send(path, withBody('PATCH', body, init), `Request to ${path} failed.`),
    del: async (path, init) => {
      const response = await raw(path, { ...init, method: 'DELETE' });
      if (!response.ok) {
        throw new ApiError(response.status, await describeFailure(response, `Request to ${path} failed.`));
      }
    },
  };
}
