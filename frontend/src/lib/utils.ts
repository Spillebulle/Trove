import type { AccountStatus, ClaimOutcome, RunStatus } from './types'

/** Join class names, dropping the falsy ones. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}

/*
 * The one place each stored word becomes something a person reads.
 *
 * Sentence case, British spelling, and no word that only the code knows
 * (STYLE-GUIDE 12). Every one of these is a *state*, so each also names the
 * semantic colour it wears - and none of them is the accent, which means
 * selected, in hand, primary and never a status.
 */

export const ACCOUNT_STATUS_LABEL: Record<AccountStatus, string> = {
  ok: 'Signed in',
  needs_attention: 'Needs a hand',
  never_signed_in: 'Not signed in yet',
  disabled: 'Switched off',
}

export type Tone = 'good' | 'caution' | 'critical' | 'neutral'

export const ACCOUNT_STATUS_TONE: Record<AccountStatus, Tone> = {
  ok: 'good',
  needs_attention: 'caution',
  // Not critical: an account nobody has signed in to yet is a step not taken,
  // not a thing that has gone wrong. It is the first thing a new install does.
  never_signed_in: 'neutral',
  disabled: 'neutral',
}

export const OUTCOME_LABEL: Record<ClaimOutcome, string> = {
  claimed: 'Claimed',
  already_owned: 'Already owned',
  not_eligible: 'Not eligible',
  needs_attention: 'Needs a hand',
  failed: 'Failed',
}

/*
 * `already_owned` is neutral, and that is the whole point of it.
 *
 * It is the normal steady state of a claimer that is working: after the first
 * week most rows are this. CLAUDE.md asks for it to read quietly rather than in
 * `critical`, so it takes the neutral badge and not a semantic colour at all.
 */
export const OUTCOME_TONE: Record<ClaimOutcome, Tone> = {
  claimed: 'good',
  already_owned: 'neutral',
  not_eligible: 'neutral',
  needs_attention: 'caution',
  failed: 'critical',
}

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  running: 'Running',
  ok: 'Finished',
  attention: 'Stopped for a hand',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export const RUN_STATUS_TONE: Record<RunStatus, Tone> = {
  running: 'neutral',
  ok: 'good',
  attention: 'caution',
  failed: 'critical',
  cancelled: 'neutral',
}

export const BADGE_CLASS: Record<Tone, string> = {
  good: 'badge-good',
  caution: 'badge-caution',
  critical: 'badge-critical',
  neutral: 'badge',
}

export const DOT_CLASS: Record<Tone, string> = {
  good: 'bg-good',
  caution: 'bg-caution',
  critical: 'bg-critical',
  neutral: 'bg-dim',
}

/* ── Time ─────────────────────────────────────────────────────────────────
 *
 * Relative under a day, the date above it, and the full timestamp in the
 * tooltip (STYLE-GUIDE 12). Both directions: this app says both "3 min ago"
 * about a run and "in 4 hours" about the next one, and the same function has
 * to read properly for each.
 */

export function relativeTime(value: string | null | undefined): string {
  if (!value) return '–'
  const then = new Date(value).getTime()
  if (Number.isNaN(then)) return '–'
  const seconds = Math.round((then - Date.now()) / 1000)
  const ahead = seconds > 0
  const magnitude = Math.abs(seconds)

  if (magnitude < 45) return ahead ? 'in a moment' : 'just now'

  const say = (count: number, unit: string) => {
    const plural = count === 1 ? unit : `${unit}s`
    return ahead ? `in ${count} ${plural}` : `${count} ${plural} ago`
  }

  if (magnitude < 3600) return say(Math.round(magnitude / 60), 'min')
  if (magnitude < 86400) return say(Math.round(magnitude / 3600), 'hour')
  if (magnitude < 86400 * 7) return say(Math.round(magnitude / 86400), 'day')

  return new Date(value).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

/** The full timestamp, for a tooltip beside a relative one. */
export function fullTime(value: string | null | undefined): string | undefined {
  if (!value) return undefined
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? undefined : date.toLocaleString()
}

/** How long is left, as a sentence. Used on an offer that is about to end. */
export function timeLeft(value: string | null | undefined): string | null {
  if (!value) return null
  const ms = new Date(value).getTime() - Date.now()
  if (Number.isNaN(ms) || ms <= 0) return null
  const hours = Math.floor(ms / 3600000)
  if (hours < 1) return 'Ends within the hour'
  if (hours < 24) return `Ends in ${hours} hour${hours === 1 ? '' : 's'}`
  const days = Math.round(hours / 24)
  return `Ends in ${days} day${days === 1 ? '' : 's'}`
}

/** A duration in seconds, as a figure with a unit. */
export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return '–'
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes} min ${Math.round(seconds % 60)} s`
}

/** An interval in hours, as a phrase. */
export function everyHours(hours: number): string {
  if (hours === 1) return 'Every hour'
  if (hours === 24) return 'Once a day'
  if (hours % 24 === 0) return `Every ${hours / 24} days`
  return `Every ${hours} hours`
}

/**
 * "2 games, 1 add-on" - the claimed count split by what was claimed.
 *
 * An add-on is worth counting apart from a game: it is a different kind of
 * thing to own, and one that only works if the game it belongs to is owned
 * too. Written out rather than as "2/1", which needs a key to read.
 */
export function claimedSplit(total: number, dlc: number): string {
  const games = Math.max(0, total - dlc)
  const part = (count: number, one: string, many: string) =>
    `${count} ${count === 1 ? one : many}`
  if (!dlc) return part(games, 'game', 'games')
  if (!games) return part(dlc, 'add-on', 'add-ons')
  return `${part(games, 'game', 'games')}, ${part(dlc, 'add-on', 'add-ons')}`
}
