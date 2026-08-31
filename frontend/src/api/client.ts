/**
 * API client — the frontend's single fetch path (spec-2.1).
 *
 * Wire contract (conventions.md / AD-17): every non-2xx JSON response is
 * the error envelope `{code, message, details?}`. 4xx = user error (never
 * a state change), 5xx = server error. The session rides the httpOnly
 * cookie (SameSite=Lax, path /api) — `credentials: 'same-origin'`, no
 * token storage in the client (AD-9, AR14).
 *
 * 401 handling (AR29): there is one generic 401 for the whole app. The
 * module-level handler registered via `setUnauthorizedHandler` runs once
 * per 401 (the auth store clears and the router redirects), then the
 * ApiError still throws so callers can react.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

interface Envelope {
  code?: string
  message?: string
  details?: unknown
}

let unauthorizedHandler: (() => void) | null = null

/** Register the single 401 handler (AR29) — called before ApiError throws. */
export function setUnauthorizedHandler(handler: () => void) {
  unauthorizedHandler = handler
}

/** Same-origin by default — the Vite dev server proxies /api to FastAPI. */
const DEFAULT_BASE_URL = ''

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${DEFAULT_BASE_URL}${path}`, {
    ...init,
    headers: {
      ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
    credentials: 'same-origin',
  })
  if (response.status === 204) {
    return undefined as T
  }
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const envelope = (body ?? null) as Envelope | null
    const error = new ApiError(
      response.status,
      envelope?.code ?? 'http_error',
      envelope?.message ?? response.statusText,
      envelope?.details,
    )
    if (response.status === 401) {
      unauthorizedHandler?.()
    }
    throw error
  }
  if (body === null) {
    throw new ApiError(response.status, 'invalid_response', 'Empty or non-JSON response.')
  }
  return body as T
}
