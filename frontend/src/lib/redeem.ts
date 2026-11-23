/*
 * Where a key is redeemed, per store.
 *
 * Trove never redeems a key itself - that is the person's account and the
 * person's click. What it can do is take them to the right page with as little
 * typing as possible: Steam and GOG both accept the key in the URL and show it
 * filled in, waiting for the person to confirm; Epic and Microsoft have a
 * redeem page but no prefill, so the key is copied and the page opened. The
 * rest redeem inside their own launcher, so the honest help is a sentence.
 *
 * The keys of this table are the ledger's `key_store` words (see
 * `adapters/prime.KEY_STORES`, and each key-handing adapter's own).
 */

export interface RedeemTarget {
  /** The store's name as a person says it. */
  name: string
  /** The redeem page for this key, or null when there is no web page for it. */
  url: (key: string) => string | null
  /** True when `url` already carries the key, so there is nothing to paste. */
  prefilled: boolean
  /** What to do when there is no page, or what the page will ask. */
  how: string
}

const TARGETS: Record<string, RedeemTarget> = {
  steam: {
    name: 'Steam',
    url: (key) => `https://store.steampowered.com/account/registerkey?key=${encodeURIComponent(key)}`,
    prefilled: true,
    how: 'Steam opens with the key filled in. Tick the agreement and press Continue.',
  },
  gog: {
    name: 'GOG',
    url: (key) => `https://www.gog.com/redeem/${encodeURIComponent(key)}`,
    prefilled: true,
    how: 'GOG opens with the code filled in. Press Continue, then Redeem.',
  },
  epic: {
    name: 'Epic Games',
    url: () => 'https://store.epicgames.com/redeem',
    prefilled: false,
    how: 'The key is copied. Paste it on the page that opens and press Redeem.',
  },
  microsoft: {
    name: 'Microsoft',
    url: () => 'https://redeem.microsoft.com/',
    prefilled: false,
    how: 'The key is copied. Paste it on the page that opens.',
  },
  ubisoft: {
    name: 'Ubisoft Connect',
    url: () => null,
    prefilled: false,
    how: 'Redeem it in the Ubisoft Connect app: the menu, then "Activate a key".',
  },
  ea: {
    name: 'EA app',
    url: () => null,
    prefilled: false,
    how: 'Redeem it in the EA app: the menu, then "Redeem code".',
  },
  battlenet: {
    name: 'Battle.net',
    url: () => null,
    prefilled: false,
    how: 'Redeem it in the Battle.net app: your account menu, then "Redeem a Code".',
  },
  legacy: {
    name: 'Legacy Games',
    url: () => null,
    prefilled: false,
    how: 'Redeem it in the Legacy Games launcher.',
  },
}

export function redeemTarget(store: string | null | undefined): RedeemTarget | null {
  if (!store) return null
  return TARGETS[store.toLowerCase()] ?? null
}
