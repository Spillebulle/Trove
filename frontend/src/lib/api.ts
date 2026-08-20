import type {
  Account,
  AuthStatus,
  Health,
  Claim,
  Offer,
  Run,
  SettingsPayload,
  StoreInfo,
  Summary,
  TestResult,
} from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

type Query = Record<string, string | number | boolean | null | undefined>

function withQuery(path: string, query?: Query): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') {
      params.append(key, String(value))
    }
  }
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    // The session is a cookie, and a cross-origin dev server will not send it
    // without this.
    credentials: 'same-origin',
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })

  if (!response.ok) {
    /*
     * FastAPI puts the sentence in `detail`, and every error this app raises is
     * written as a sentence for a person to read. Show that rather than the
     * status text: "The browser profile is in use by the live view." is an
     * answer, and "Conflict" is not.
     */
    let message = response.statusText || 'Something went wrong.'
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') message = body.detail
      else if (Array.isArray(body?.detail) && body.detail[0]?.msg) message = body.detail[0].msg
    } catch {
      // A non-JSON body is not worth a second failure.
    }
    throw new ApiError(message, response.status)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })

export const api = {
  auth: {
    status: () => request<AuthStatus>('/api/auth/status'),
    login: (username: string, password: string) =>
      post<AuthStatus>('/api/auth/login', { username, password }),
    logout: () => post<void>('/api/auth/logout'),
    changePassword: (current_password: string, new_password: string) =>
      post<void>('/api/auth/password', { current_password, new_password }),
  },

  /**
   * The running version, among other things. Unauthenticated on purpose: it is
   * what the container's healthcheck hits, and the sidebar reads it so the
   * interface reports the build it is actually talking to.
   */
  health: () => request<Health>('/api/health'),

  summary: () => request<Summary>('/api/summary'),

  accounts: {
    stores: () => request<StoreInfo[]>('/api/accounts/stores'),
    list: () => request<Account[]>('/api/accounts'),
    get: (id: number) => request<Account>(`/api/accounts/${id}`),
    create: (body: { store: string; label: string; interval_hours?: number | null }) =>
      post<Account>('/api/accounts', body),
    update: (
      id: number,
      body: Partial<Account> & { totp_secret?: string; login_email?: string; login_password?: string },
    ) => patch<Account>(`/api/accounts/${id}`, body),
    remove: (id: number) => request<void>(`/api/accounts/${id}`, { method: 'DELETE' }),
    run: (id: number, watch = false) =>
      post<{ started: boolean }>(`/api/accounts/${id}/run${watch ? '?watch=true' : ''}`),
    stopWatching: (id: number) => post<void>(`/api/accounts/${id}/stop-watching`),
    clearAttention: (id: number) => post<Account>(`/api/accounts/${id}/clear-attention`),
    resetProfile: (id: number) => post<Account>(`/api/accounts/${id}/reset-profile`),
    signInHere: (id: number) => post<Account>(`/api/accounts/${id}/sign-in-here`),
    // Open the un-driven window on the checkout page to finish a claim the
    // driven browser could not, because of a captcha it cannot pass.
    finishClaim: (id: number) => post<Account>(`/api/accounts/${id}/finish-claim`),
    checkSession: (id: number) => post<Account>(`/api/accounts/${id}/check-session`),
    canSignInHere: (id: number) =>
      request<{ ok: boolean; via: 'desktop' | 'screen' | null; reason: string | null }>(
        `/api/accounts/${id}/can-sign-in-here`,
      ),
    closeSignIn: (id: number) => post<void>(`/api/accounts/${id}/close-sign-in`),
    // Type a stored detail into the sign-in window on the container's screen.
    typeIntoScreen: (id: number, what: 'email' | 'password' | 'code' | 'enter' | 'tab') =>
      post<void>(`/api/accounts/${id}/type`, { what }),
    canOpenLive: (id: number) =>
      request<{ ok: boolean; reason: string | null }>(`/api/live/${id}/can-open`),
  },

  offers: {
    list: (current = true) => request<Offer[]>(withQuery('/api/offers', { current })),
    refresh: () => post<Offer[]>('/api/offers/refresh'),
  },

  claims: {
    list: (query?: { account_id?: number; outcome?: string; limit?: number; offset?: number }) =>
      request<Claim[]>(withQuery('/api/claims', query as Query)),
    key: (id: number) =>
      request<{ key_code: string; key_store: string | null }>(`/api/claims/${id}/key`),
  },

  runs: {
    list: (query?: { account_id?: number; limit?: number }) =>
      request<Run[]>(withQuery('/api/runs', query as Query)),
  },

  settings: {
    read: () => request<SettingsPayload>('/api/settings'),
    write: (values: Record<string, unknown>) => patch<SettingsPayload>('/api/settings', { values }),
    testNotification: (channel: string, webhook_url?: string) =>
      post<TestResult>('/api/settings/notify/test', { channel, webhook_url }),
  },

  screen: {
    available: () =>
      request<{
        ok: boolean
        reason: string | null
        holders?: Record<string, string>
        typing?: boolean
      }>('/api/screen/available'),
  },

  diagnostics: {
    // Launches a browser and asks it what it is. Seconds, not milliseconds.
    browser: () => request<BrowserDiagnostics>('/api/diagnostics/browser'),
  },

  screenshotUrl: (name: string) => `/api/screenshots/${encodeURIComponent(name)}`,
}

export interface BrowserFinding {
  level: 'critical' | 'caution' | 'info'
  text: string
}

/** What `python -m app.diagnose` prints; see `backend/app/diagnose.py`. */
export interface BrowserDiagnostics {
  trove: string
  headless: boolean
  in_container: boolean
  display: string | null
  vnc: string | null
  channel_setting: string
  channel?: string
  browser_version?: string | null
  launch_args: string[]
  page?: Record<string, unknown>
  error?: string
  seconds: number
  findings: BrowserFinding[]
}

/**
 * The live view's socket URL.
 *
 * Built from `location` rather than a configured base, so it follows whatever
 * host and scheme the page was served from. The scheme swap is the part that
 * is easy to get wrong: a page on HTTPS must open `wss:`, and a mixed-content
 * `ws:` is refused by the browser with no error the app can catch.
 */
export function liveSocketUrl(accountId: number): string {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}/api/live/${accountId}`
}

/** The container's screen, bridged to its VNC server. See `ScreenView.tsx`. */
export function screenSocketUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}/api/screen`
}
