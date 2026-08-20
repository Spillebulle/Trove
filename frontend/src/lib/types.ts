/*
 * What the API returns, transcribed from `backend/app/schemas.py`.
 *
 * The vocabularies below are the stored words the backend uses, not display
 * strings: `lib/utils.ts` holds the one place each is turned into something a
 * person reads, so a status word appears in exactly one sentence in the whole
 * interface.
 */

export type AccountStatus = 'ok' | 'needs_attention' | 'never_signed_in' | 'disabled'

export type ClaimOutcome =
  | 'claimed'
  | 'already_owned'
  | 'not_eligible'
  | 'needs_attention'
  | 'failed'

export type RunStatus = 'running' | 'ok' | 'attention' | 'failed' | 'cancelled'

export interface StoreRequirement {
  name: string
  description: string
  required: boolean
}

export interface StoreInfo {
  store: string
  display_name: string
  blurb: string
  login_url: string
  requirements: StoreRequirement[]
}

export interface Account {
  id: number
  store: string
  label: string
  status: AccountStatus
  status_reason: string | null
  status_at: string | null
  status_screenshot: string | null
  enabled: boolean
  interval_hours: number | null
  effective_interval_hours: number
  last_run_at: string | null
  next_run_at: string | null
  has_totp: boolean
  /** A watched run is paused on a captcha, waiting for you on the screen. */
  waiting_for_captcha: boolean
  /** The stored sign-in email, if any; the password only as a yes/no. */
  login_email: string | null
  has_login_password: boolean
  notes: string | null
  created_at: string | null
  claimed_count: number
  /** Who has the browser profile open, if anybody. Null means it is free. */
  busy_with: string | null
}

export interface Offer {
  id: number
  store: string
  external_id: string
  title: string
  url: string | null
  image_url: string | null
  kind: string
  starts_at: string | null
  ends_at: string | null
  source: string
  first_seen_at: string | null
  last_seen_at: string | null
  claimed_by: string[]
}

export interface Claim {
  id: number
  account_id: number | null
  account_label: string | null
  offer_id: number | null
  run_id: number | null
  store: string
  title: string
  outcome: ClaimOutcome
  detail: string | null
  /** Whether there is a key to reveal. The key itself is a separate request. */
  has_key: boolean
  key_store: string | null
  screenshot: string | null
  created_at: string | null
}

export interface Run {
  id: number
  account_id: number | null
  account_label: string | null
  store: string
  status: RunStatus
  trigger: string
  started_at: string | null
  finished_at: string | null
  duration_s: number | null
  offers_seen: number
  claimed: number
  already_owned: number
  message: string | null
}

export interface Summary {
  accounts: number
  accounts_needing_attention: number
  free_now: number
  claimed_total: number
  claimed_7d: number
  last_run_at: string | null
  scheduler_enabled: boolean
  scheduler_running: boolean
}

export interface SettingsPayload {
  values: Record<string, unknown>
  scheduler: { running: boolean; watching: number[] }
}

export interface Health {
  status: string
  /** The version of the backend that is actually running. */
  version: string
  app: string
}

export interface AuthStatus {
  authenticated: boolean
  username: string | null
}

export interface TestResult {
  ok: boolean
  message: string
}

/**
 * The placeholder a secret comes back as.
 *
 * The API never returns a stored webhook URL. It sends this instead, and
 * sending it back means "leave it alone" - which is what a form does when it
 * never held the real value to begin with.
 */
export const REDACTED = '__set__'
