/**
 * API client — the frontend's single fetch path (spec-2.1).
 *
 * Wire contract (conventions.md / AD-17): every non-2xx JSON response is
 * the error envelope `{code, message, details?}`. 4xx = user error (never
 * a state change), 5xx = server error. The session rides the httpOnly
 * cookie (SameSite=Lax, path /api) — `credentials: 'same-origin'`, no
 * token storage in the client (AD-9, AR14).
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

export interface ApiClientOptions {
  /** Called when any call returns the generic 401 (AR29) — the auth store
   * clears and the router redirects. */
  onUnauthorized?: () => void
}

/** Same-origin by default — the Vite dev server proxies /api to FastAPI. */
const DEFAULT_BASE_URL = ''

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  options: ApiClientOptions = {},
): Promise<T> {
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
      options.onUnauthorized?.()
    }
    throw error
  }
  return body as T
}
