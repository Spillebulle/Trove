/*
 * The painted controls, composed from the component classes in index.css.
 * Everything here names a role, never a colour; the geometry comes from
 * STYLE-GUIDE section 7.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Check, Gift, TriangleAlert, X } from 'lucide-react'
import { cn, type Tone, BADGE_CLASS, DOT_CLASS } from '@/lib/utils'

/* ── Page header ─────────────────────────────────────────────────────────── */

/** A page's heading: 18px 600, one line of `text-muted`, actions at the right. */
export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-balance text-page font-semibold text-strong">{title}</h1>
        {subtitle && <p className="mt-0.5 text-body text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

/* ── Panels and tiles ────────────────────────────────────────────────────── */

/** A titled region of a page: the module header without the drag grip (7.5). */
export function Panel({
  title,
  count,
  commands,
  children,
  className,
  bodyClassName,
}: {
  title: string
  count?: number | string
  commands?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section className={cn('panel', className)}>
      <header className="panel-head">
        <h2 className="panel-title truncate">{title}</h2>
        {count != null && <span className="figure text-tiny text-dim">{count}</span>}
        {commands && <div className="ml-auto flex items-center gap-1">{commands}</div>}
      </header>
      <div className={cn('panel-body', bodyClassName)}>{children}</div>
    </section>
  )
}

/**
 * A stat: an eyebrow, a mono figure, a second line (7.14). No big icon.
 *
 * An unknown value is an en dash in `text-dim`, never "0", because "no data"
 * and "nothing happened" are different answers - which matters here, where a
 * dashboard showing 0 claims could mean the app has never run.
 */
export function Tile({
  eyebrow,
  value,
  detail,
  dot,
  className,
}: {
  eyebrow: string
  value: ReactNode | null
  detail?: ReactNode
  dot?: Tone
  className?: string
}) {
  return (
    <div className={cn('card p-3', className)}>
      <div className="flex items-center gap-1.5">
        {dot && (
          <span aria-hidden="true" className={cn('h-1.5 w-1.5 rounded-full', DOT_CLASS[dot])} />
        )}
        <span className="eyebrow truncate">{eyebrow}</span>
      </div>
      <div className="mt-1.5">
        {value == null || value === '' ? (
          <span className="figure text-[24px] leading-none text-dim">–</span>
        ) : (
          <span className="figure text-[24px] leading-none text-strong">{value}</span>
        )}
      </div>
      {detail && <div className="mt-1 text-small text-muted">{detail}</div>}
    </div>
  )
}

/* ── Status ──────────────────────────────────────────────────────────────── */

/** A state, as a dot beside a written label. Never colour alone. */
export function StatusBadge({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span className={BADGE_CLASS[tone]}>
      <span aria-hidden="true" className={cn('h-1.5 w-1.5 rounded-full', DOT_CLASS[tone])} />
      {label}
    </span>
  )
}

/**
 * What a thing is - an add-on, a key - rather than how it is doing.
 *
 * Deliberately not a `StatusBadge`: this is a classification, and the tones
 * (good / caution / critical) are spoken for by outcomes. It is also not the
 * accent, which never means a state or a category (§2.4). So: the plain badge,
 * with the word doing the work. A game gets nothing at all, because "game" is
 * the ordinary case and a badge on every row is a badge that says nothing.
 */
export function KindBadge({ kind }: { kind: string }) {
  if (kind !== 'dlc' && kind !== 'key') return null
  // `shrink-0` and no wrapping, wherever it lands. "Add-on" broke across two
  // lines on a narrow offer card next to a long store name - which grew the
  // row, pushed everything under it down, and made one card in a grid taller
  // than its neighbours. A badge is a fixed label: it is the *name* beside it
  // that should give up a character, and StoreBadge already truncates.
  return (
    <span className={cn(BADGE_CLASS.neutral, 'shrink-0 whitespace-nowrap')}>
      {kind === 'dlc' ? 'Add-on' : 'Key'}
    </span>
  )
}

/**
 * Which store a thing belongs to, as the store's own small mark.
 *
 * Monochrome through `currentColor`, deliberately never the brand colour: the
 * semantic colours are spoken for by statuses and the accent never means a
 * category (§2.4), so the mark takes whatever text tone the line it sits in
 * already has. The paths are simple-icons (CC0), bundled inline because this
 * app is self-hosted and fetches nothing from a CDN. Prime Gaming has no
 * glyph in that set, so Amazon's stands in under the Prime Gaming name.
 */
const STORE_MARKS: Record<string, { name: string; path: string }> = {
  epic: {
    name: 'Epic Games Store',
    path: 'M3.537 0C2.165 0 1.66.506 1.66 1.879V18.44a4.262 4.262 0 00.02.433c.031.3.037.59.316.92.027.033.311.245.311.245.153.075.258.13.43.2l8.335 3.491c.433.199.614.276.928.27h.002c.314.006.495-.071.928-.27l8.335-3.492c.172-.07.277-.124.43-.2 0 0 .284-.211.311-.243.28-.33.285-.621.316-.92a4.261 4.261 0 00.02-.434V1.879c0-1.373-.506-1.88-1.878-1.88zm13.366 3.11h.68c1.138 0 1.688.553 1.688 1.696v1.88h-1.374v-1.8c0-.369-.17-.54-.523-.54h-.235c-.367 0-.537.17-.537.539v5.81c0 .369.17.54.537.54h.262c.353 0 .523-.171.523-.54V8.619h1.373v2.143c0 1.144-.562 1.71-1.7 1.71h-.694c-1.138 0-1.7-.566-1.7-1.71V4.82c0-1.144.562-1.709 1.7-1.709zm-12.186.08h3.114v1.274H6.117v2.603h1.648v1.275H6.117v2.774h1.74v1.275h-3.14zm3.816 0h2.198c1.138 0 1.7.564 1.7 1.708v2.445c0 1.144-.562 1.71-1.7 1.71h-.799v3.338h-1.4zm4.53 0h1.4v9.201h-1.4zm-3.13 1.235v3.392h.575c.354 0 .523-.171.523-.54V4.965c0-.368-.17-.54-.523-.54zm-3.74 10.147a1.708 1.708 0 01.591.108 1.745 1.745 0 01.49.299l-.452.546a1.247 1.247 0 00-.308-.195.91.91 0 00-.363-.068.658.658 0 00-.28.06.703.703 0 00-.224.163.783.783 0 00-.151.243.799.799 0 00-.056.299v.008a.852.852 0 00.056.31.7.7 0 00.157.245.736.736 0 00.238.16.774.774 0 00.303.058.79.79 0 00.445-.116v-.339h-.548v-.565H7.37v1.255a2.019 2.019 0 01-.524.307 1.789 1.789 0 01-.683.123 1.642 1.642 0 01-.602-.107 1.46 1.46 0 01-.478-.3 1.371 1.371 0 01-.318-.455 1.438 1.438 0 01-.115-.58v-.008a1.426 1.426 0 01.113-.57 1.449 1.449 0 01.312-.46 1.418 1.418 0 01.474-.309 1.58 1.58 0 01.598-.111 1.708 1.708 0 01.045 0zm11.963.008a2.006 2.006 0 01.612.094 1.61 1.61 0 01.507.277l-.386.546a1.562 1.562 0 00-.39-.205 1.178 1.178 0 00-.388-.07.347.347 0 00-.208.052.154.154 0 00-.07.127v.008a.158.158 0 00.022.084.198.198 0 00.076.066.831.831 0 00.147.06c.062.02.14.04.236.061a3.389 3.389 0 01.43.122 1.292 1.292 0 01.328.17.678.678 0 01.207.24.739.739 0 01.071.337v.008a.865.865 0 01-.081.382.82.82 0 01-.229.285 1.032 1.032 0 01-.353.18 1.606 1.606 0 01-.46.061 2.16 2.16 0 01-.71-.116 1.718 1.718 0 01-.593-.346l.43-.514c.277.223.578.335.9.335a.457.457 0 00.236-.05.157.157 0 00.082-.142v-.008a.15.15 0 00-.02-.077.204.204 0 00-.073-.066.753.753 0 00-.143-.062 2.45 2.45 0 00-.233-.062 5.036 5.036 0 01-.413-.113 1.26 1.26 0 01-.331-.16.72.72 0 01-.222-.243.73.73 0 01-.082-.36v-.008a.863.863 0 01.074-.359.794.794 0 01.214-.283 1.007 1.007 0 01.34-.185 1.423 1.423 0 01.448-.066 2.006 2.006 0 01.025 0zm-9.358.025h.742l1.183 2.81h-.825l-.203-.499H8.623l-.198.498h-.81zm2.197.02h.814l.663 1.08.663-1.08h.814v2.79h-.766v-1.602l-.711 1.091h-.016l-.707-1.083v1.593h-.754zm3.469 0h2.235v.658h-1.473v.422h1.334v.61h-1.334v.442h1.493v.658h-2.255zm-5.3.897l-.315.793h.624zm-1.145 5.19h8.014l-4.09 1.348z',
  },
  gog: {
    name: 'GOG',
    path: 'M7.15 15.24H4.36a.4.4 0 0 0-.4.4v2c0 .21.18.4.4.4h2.8v1.32h-3.5c-.56 0-1.02-.46-1.02-1.03v-3.39c0-.56.46-1.02 1.03-1.02h3.48v1.32zM8.16 11.54c0 .58-.47 1.05-1.05 1.05H2.63v-1.35h3.78a.4.4 0 0 0 .4-.4V6.39a.4.4 0 0 0-.4-.4H4.39a.4.4 0 0 0-.41.4v2.02c0 .23.18.4.4.4H6v1.35H3.68c-.58 0-1.05-.46-1.05-1.04V5.68c0-.57.47-1.04 1.05-1.04H7.1c.58 0 1.05.47 1.05 1.04v5.86zM21.36 19.36h-1.32v-4.12h-.93a.4.4 0 0 0-.4.4v3.72h-1.33v-4.12h-.93a.4.4 0 0 0-.4.4v3.72h-1.33v-4.42c0-.56.46-1.02 1.03-1.02h5.61v5.44zM21.37 11.54c0 .58-.47 1.05-1.05 1.05h-4.48v-1.35h3.78a.4.4 0 0 0 .4-.4V6.39a.4.4 0 0 0-.4-.4h-2.03a.4.4 0 0 0-.4.4v2.02c0 .23.18.4.4.4h1.62v1.35H16.9c-.58 0-1.05-.46-1.05-1.04V5.68c0-.57.47-1.04 1.05-1.04h3.43c.58 0 1.05.47 1.05 1.04v5.86zM13.72 4.64h-3.44c-.58 0-1.04.47-1.04 1.04v3.44c0 .58.46 1.04 1.04 1.04h3.44c.57 0 1.04-.46 1.04-1.04V5.68c0-.57-.47-1.04-1.04-1.04m-.3 1.75v2.02a.4.4 0 0 1-.4.4h-2.03a.4.4 0 0 1-.4-.4V6.4c0-.22.17-.4.4-.4H13c.23 0 .4.18.4.4zM12.63 13.92H9.24c-.57 0-1.03.46-1.03 1.02v3.39c0 .57.46 1.03 1.03 1.03h3.39c.57 0 1.03-.46 1.03-1.03v-3.39c0-.56-.46-1.02-1.03-1.02m-.3 1.72v2a.4.4 0 0 1-.4.4v-.01H9.94a.4.4 0 0 1-.4-.4v-1.99c0-.22.18-.4.4-.4h2c.22 0 .4.18.4.4zM23.49 1.1a1.74 1.74 0 0 0-1.24-.52H1.75A1.74 1.74 0 0 0 0 2.33v19.34a1.74 1.74 0 0 0 1.75 1.75h20.5A1.74 1.74 0 0 0 24 21.67V2.33c0-.48-.2-.92-.51-1.24m0 20.58a1.23 1.23 0 0 1-1.24 1.24H1.75A1.23 1.23 0 0 1 .5 21.67V2.33a1.23 1.23 0 0 1 1.24-1.24h20.5a1.24 1.24 0 0 1 1.24 1.24v19.34z',
  },
  steam: {
    name: 'Steam',
    path: 'M11.979 0C5.678 0 .511 4.86.022 11.037l6.432 2.658c.545-.371 1.203-.59 1.912-.59.063 0 .125.004.188.006l2.861-4.142V8.91c0-2.495 2.028-4.524 4.524-4.524 2.494 0 4.524 2.031 4.524 4.527s-2.03 4.525-4.524 4.525h-.105l-4.076 2.911c0 .052.004.105.004.159 0 1.875-1.515 3.396-3.39 3.396-1.635 0-3.016-1.173-3.331-2.727L.436 15.27C1.862 20.307 6.486 24 11.979 24c6.627 0 11.999-5.373 11.999-12S18.605 0 11.979 0zM7.54 18.21l-1.473-.61c.262.543.714.999 1.314 1.25 1.297.539 2.793-.076 3.332-1.375.263-.63.264-1.319.005-1.949s-.75-1.121-1.377-1.383c-.624-.26-1.29-.249-1.878-.03l1.523.63c.956.4 1.409 1.5 1.009 2.455-.397.957-1.497 1.41-2.454 1.012H7.54zm11.415-9.303c0-1.662-1.353-3.015-3.015-3.015-1.665 0-3.015 1.353-3.015 3.015 0 1.665 1.35 3.015 3.015 3.015 1.663 0 3.015-1.35 3.015-3.015zm-5.273-.005c0-1.252 1.013-2.266 2.265-2.266 1.249 0 2.266 1.014 2.266 2.266 0 1.251-1.017 2.265-2.266 2.265-1.253 0-2.265-1.014-2.265-2.265z',
  },
  itchio: {
    name: 'itch.io',
    path: 'M3.13 1.338C2.08 1.96.02 4.328 0 4.95v1.03c0 1.303 1.22 2.45 2.325 2.45 1.33 0 2.436-1.102 2.436-2.41 0 1.308 1.07 2.41 2.4 2.41 1.328 0 2.362-1.102 2.362-2.41 0 1.308 1.137 2.41 2.466 2.41h.024c1.33 0 2.466-1.102 2.466-2.41 0 1.308 1.034 2.41 2.363 2.41 1.33 0 2.4-1.102 2.4-2.41 0 1.308 1.106 2.41 2.435 2.41C22.78 8.43 24 7.282 24 5.98V4.95c-.02-.62-2.082-2.99-3.13-3.612-3.253-.114-5.508-.134-8.87-.133-3.362 0-7.945.053-8.87.133zm6.376 6.477a2.74 2.74 0 0 1-.468.602c-.5.49-1.19.795-1.947.795a2.786 2.786 0 0 1-1.95-.795c-.182-.178-.32-.37-.446-.59-.127.222-.303.412-.486.59a2.788 2.788 0 0 1-1.95.795c-.092 0-.187-.025-.264-.052-.107 1.113-.152 2.176-.168 2.95v.005l-.006 1.167c.02 2.334-.23 7.564 1.03 8.85 1.952.454 5.545.662 9.15.663 3.605 0 7.198-.21 9.15-.664 1.26-1.284 1.01-6.514 1.03-8.848l-.006-1.167v-.004c-.016-.775-.06-1.838-.168-2.95-.077.026-.172.052-.263.052a2.788 2.788 0 0 1-1.95-.795c-.184-.178-.36-.368-.486-.59-.127.22-.265.412-.447.59a2.786 2.786 0 0 1-1.95.794c-.76 0-1.446-.303-1.948-.793a2.74 2.74 0 0 1-.468-.602 2.738 2.738 0 0 1-.463.602 2.787 2.787 0 0 1-1.95.794h-.16a2.787 2.787 0 0 1-1.95-.793 2.738 2.738 0 0 1-.464-.602zm-2.004 2.59v.002c.795.002 1.5 0 2.373.953.687-.072 1.406-.108 2.125-.107.72 0 1.438.035 2.125.107.873-.953 1.578-.95 2.372-.953.376 0 1.876 0 2.92 2.934l1.123 4.028c.832 2.995-.266 3.068-1.636 3.07-2.03-.075-3.156-1.55-3.156-3.025-1.124.184-2.436.276-3.748.277-1.312 0-2.624-.093-3.748-.277 0 1.475-1.125 2.95-3.156 3.026-1.37-.004-2.468-.077-1.636-3.072l1.122-4.027c1.045-2.934 2.545-2.934 2.92-2.934zM12 12.714c-.002.002-2.14 1.964-2.523 2.662l1.4-.056v1.22c0 .056.56.033 1.123.007.562.026 1.124.05 1.124-.008v-1.22l1.4.055C14.138 14.677 12 12.713 12 12.713z',
  },
  prime: {
    name: 'Prime Gaming',
    path: 'M.045 18.02c.072-.116.187-.124.348-.022 3.636 2.11 7.594 3.166 11.87 3.166 2.852 0 5.668-.533 8.447-1.595l.315-.14c.138-.06.234-.1.293-.13.226-.088.39-.046.525.13.12.174.09.336-.12.48-.256.19-.6.41-1.006.654-1.244.743-2.64 1.316-4.185 1.726a17.617 17.617 0 01-10.951-.577 17.88 17.88 0 01-5.43-3.35c-.1-.074-.151-.15-.151-.22 0-.047.021-.09.051-.13zm6.565-6.218c0-1.005.247-1.863.743-2.577.495-.71 1.17-1.25 2.04-1.615.796-.335 1.756-.575 2.912-.72.39-.046 1.033-.103 1.92-.174v-.37c0-.93-.105-1.558-.3-1.875-.302-.43-.78-.65-1.44-.65h-.182c-.48.046-.896.196-1.246.46-.35.27-.575.63-.675 1.096-.06.3-.206.465-.435.51l-2.52-.315c-.248-.06-.372-.18-.372-.39 0-.046.007-.09.022-.15.247-1.29.855-2.25 1.82-2.88.976-.616 2.1-.975 3.39-1.05h.54c1.65 0 2.957.434 3.888 1.29.135.15.27.3.405.48.12.165.224.314.283.45.075.134.15.33.195.57.06.254.105.42.135.51.03.104.062.3.076.615.01.313.02.493.02.553v5.28c0 .376.06.72.165 1.036.105.313.21.54.315.674l.51.674c.09.136.136.256.136.36 0 .12-.06.226-.18.314-1.2 1.05-1.86 1.62-1.963 1.71-.165.135-.375.15-.63.045a6.062 6.062 0 01-.526-.496l-.31-.347a9.391 9.391 0 01-.317-.42l-.3-.435c-.81.886-1.603 1.44-2.4 1.665-.494.15-1.093.227-1.83.227-1.11 0-2.04-.343-2.76-1.034-.72-.69-1.08-1.665-1.08-2.94l-.05-.076zm3.753-.438c0 .566.14 1.02.425 1.364.285.34.675.512 1.155.512.045 0 .106-.007.195-.02.09-.016.134-.023.166-.023.614-.16 1.08-.553 1.424-1.178.165-.28.285-.58.36-.91.09-.32.12-.59.135-.8.015-.195.015-.54.015-1.005v-.54c-.84 0-1.484.06-1.92.18-1.275.36-1.92 1.17-1.92 2.43l-.035-.02zm9.162 7.027c.03-.06.075-.11.132-.17.362-.243.714-.41 1.05-.5a8.094 8.094 0 011.612-.24c.14-.012.28 0 .41.03.65.06 1.05.168 1.172.33.063.09.099.228.099.39v.15c0 .51-.149 1.11-.424 1.8-.278.69-.664 1.248-1.156 1.68-.073.06-.14.09-.197.09-.03 0-.06 0-.09-.012-.09-.044-.107-.12-.064-.24.54-1.26.806-2.143.806-2.64 0-.15-.03-.27-.087-.344-.145-.166-.55-.257-1.224-.257-.243 0-.533.016-.87.046-.363.045-.7.09-1 .135-.09 0-.148-.014-.18-.044-.03-.03-.036-.047-.02-.077 0-.017.006-.03.02-.063v-.06z',
  },
}

/**
 * The mark with a tooltip, and optionally the store's name written out.
 *
 * A store with no mark here - a new adapter - falls back to the plain text
 * badge with the store's own key, so the backend can grow a store without a
 * frontend change and nothing renders blank.
 */
export function StoreBadge({ store, withName }: { store: string; withName?: boolean }) {
  const mark = STORE_MARKS[store]
  if (!mark) return <span className={BADGE_CLASS.neutral}>{store}</span>
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5" title={mark.name}>
      <svg
        viewBox="0 0 24 24"
        className="size-icon shrink-0"
        fill="currentColor"
        aria-hidden="true"
      >
        <path d={mark.path} />
      </svg>
      {withName ? <span className="truncate">{mark.name}</span> : <span className="sr-only">{mark.name}</span>}
    </span>
  )
}


/**
 * A game's artwork, and what to draw when there is none.
 *
 * The block is always drawn, whatever arrives. A row whose picture is missing
 * keeps the height and the left edge of its neighbours instead of jogging the
 * list - the ledger used to omit the whole block and the text slid left, while
 * the account page drew an empty one, so the same claim looked different on two
 * pages and neither looked deliberate.
 *
 * With no picture it shows the store's own mark, dimmed. `.art` is a filled
 * panel (`bg-control`), so an empty one reads as a picture that failed to load;
 * a mark in it reads as a row that never had one - which is the truth for a
 * claim written before Trove copied posters, and for one whose offer has since
 * been pruned. The mark carries the store's name in its tooltip and to a screen
 * reader, so the block says something either way.
 *
 * `className` carries the caller's own sizing, because the shape differs per
 * page: a wide thumbnail in a row, a full-width cover on a card.
 */
export function Art({
  src,
  store,
  className,
}: {
  src?: string | null
  store?: string
  className?: string
}) {
  // A stored poster URL can stop resolving - a store prunes its CDN, a
  // promotion ends - and the browser's answer to that is its own broken-image
  // glyph, which looks like a fault in Trove. Seen while checking this
  // component against a seeded row. `onError` turns it back into the same
  // "no picture" the component already draws well. Keyed by `src` so a row
  // re-rendered with a different picture gets a fresh try rather than
  // inheriting the last one's failure.
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [src])

  return (
    <span className={cn('art', className)}>
      {src && !failed ? (
        <img
          src={src}
          alt=""
          loading="lazy"
          className="h-full w-full object-cover"
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="flex h-full w-full items-center justify-center text-dim opacity-70">
          {store ? <StoreBadge store={store} /> : <Gift className="size-icon" />}
        </span>
      )}
    </span>
  )
}

/* ── Empty and error states ──────────────────────────────────────────────── */

/**
 * Nothing here, and why (7.19): centred, an optional 24px icon in the dashed
 * line colour, a sentence in `text-dim`, at most a secondary action. Never an
 * illustration.
 */
export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string
  description?: string
  action?: ReactNode
  icon?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon && (
        <span className="mb-1 grid place-items-center text-line-dashed" aria-hidden="true">
          {icon}
        </span>
      )}
      <h3 className="text-body font-semibold text-strong">{title}</h3>
      {description && <p className="max-w-sm text-balance text-body text-dim">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

/**
 * A request that failed, said so.
 *
 * Deliberately shorter than `EmptyState`, and that is not a lapse in the pair.
 * An empty state is a page's whole answer and may hold the room it is given; an
 * error is a sentence about one request, and most of these sit inside a panel
 * that is a couple of hundred pixels tall when it succeeds.
 */
export function ErrorState({
  error,
  onRetry,
  title = 'Could not load this',
}: {
  error: unknown
  onRetry?: () => void
  title?: string
}) {
  const message = error instanceof Error && error.message ? error.message : 'Something went wrong.'
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-8 text-center">
      <span className="mb-1 grid place-items-center text-critical" aria-hidden="true">
        <TriangleAlert size={24} />
      </span>
      <h3 className="text-body font-semibold text-strong">{title}</h3>
      <p className="max-w-sm text-balance text-body text-dim">{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-secondary mt-2">
          Try again
        </button>
      )}
    </div>
  )
}

/* ── Spinner ─────────────────────────────────────────────────────────────── */

/**
 * A last resort (7.18): 16px and `text-dim`.
 *
 * Sized through `size-icon` rather than `1em`, which would inherit whatever
 * font size it landed in and be a different spinner in a button than in a row.
 */
export function Spinner({ className, size }: { className?: string; size?: number }) {
  return (
    <svg
      className={cn('animate-spin', size == null && 'size-icon', className)}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" fill="none" opacity="0.2" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  )
}

/* ── Segmented control ───────────────────────────────────────────────────── */

/**
 * Two to five exclusive short options in a `line` bordered box. The selected
 * segment is `control` and `text-strong`, never the accent (7.9).
 *
 * A radiogroup rather than a tablist: these pick a filter, they do not switch
 * between panels, and `role="tab"` promises `aria-controls` and roving focus
 * that a filter does not have.
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: ReadonlyArray<{ value: T; label: string }>
  value: T
  onChange: (value: T) => void
  label?: string
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex flex-wrap gap-[2px] rounded-ctl border border-line p-[2px]"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn(
            'rounded-[4px] px-2.5 py-1 text-small transition-colors duration-hover ease-ease',
            value === option.value ? 'bg-control text-strong' : 'text-muted hover:text-fg',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/* ── Toggle ──────────────────────────────────────────────────────────────── */

/**
 * A 34 by 18 pill with a 14px knob (7.8). Off: `rail`, knob left. On:
 * `accent`, knob right. The label sits at the left, the toggle at the row's
 * right edge, and there is no text inside the pill.
 */
export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
  disabledReason,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  label: string
  description?: string
  disabled?: boolean
  disabledReason?: string
}) {
  return (
    <label
      className={cn('flex items-start justify-between gap-4 py-2', disabled && 'opacity-45')}
      title={disabled ? disabledReason : undefined}
    >
      <span className="min-w-0">
        <span className="block text-control text-fg">{label}</span>
        {description && <span className="mt-0.5 block text-small text-dim">{description}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        title={disabled ? disabledReason : undefined}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative mt-px h-[18px] w-[34px] shrink-0 rounded-full transition-colors duration-hover ease-ease',
          checked ? 'bg-accent' : 'bg-rail',
        )}
      >
        {/* Anchored with an explicit left: without one the knob falls at the
            button's static position, which lands outside the track. */}
        <span
          className={cn(
            'absolute left-[2px] top-[2px] h-3.5 w-3.5 rounded-full bg-knob shadow-knob',
            'transition-transform duration-hover ease-ease',
            checked ? 'translate-x-4' : 'translate-x-0',
          )}
        />
      </button>
    </label>
  )
}

/* ── Field ───────────────────────────────────────────────────────────────── */

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string
  hint?: ReactNode
  error?: string | null
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-control text-fg">{label}</span>
      {children}
      {/* An error is a sentence beneath the field, never a red outline alone
          (7.11): an outline says something is wrong and not what. */}
      {error ? (
        <span className="mt-1 block text-small text-caution">{error}</span>
      ) : (
        hint && <span className="mt-1 block text-small text-dim">{hint}</span>
      )}
    </label>
  )
}

/* ── Dialog ──────────────────────────────────────────────────────────────── */

const DIALOG_SIZE = {
  small: 'sm:w-[430px]',
  standard: 'sm:w-[760px]',
  large: 'sm:w-[1000px]',
} as const

const focusableIn = (panel: HTMLElement | null) =>
  Array.from(
    panel?.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])',
    ) ?? [],
  ).filter((node) => node.offsetParent !== null)

/**
 * The modal (7.17): `chrome` fill, radius 10, `shadow-modal`, the page dimmed
 * behind it. One scroll area; header and footer stay put.
 *
 * A modal owns the keyboard while it is open, and that is not decoration:
 * without it Tab walks the page underneath the backdrop, which is a list of
 * controls the user cannot see and did not ask for. So focus moves in on open,
 * is contained while open, and returns to whatever opened it on close.
 *
 * `busy` is the guide's "a modal that holds work in flight refuses to close and
 * says so". It is a prop rather than each caller's own guard, because a caller
 * that has to remember will forget.
 */
export function Dialog({
  open,
  onClose,
  title,
  subtitle,
  size = 'standard',
  busy,
  footer,
  footerNote,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: ReactNode
  size?: keyof typeof DIALOG_SIZE
  busy?: string
  footer?: ReactNode
  footerNote?: ReactNode
  children: ReactNode
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<Element | null>(null)
  const [refused, setRefused] = useState(false)

  const tryClose = () => {
    if (busy) {
      setRefused(true)
      return
    }
    onClose()
  }

  // The key listener is installed once per open, so it must not reach `busy`
  // or `onClose` through its closure - it would hold whichever ones existed
  // when the dialog opened. It calls through this instead.
  const tryCloseRef = useRef(tryClose)
  useEffect(() => {
    tryCloseRef.current = tryClose
  })

  // Focus in on open, back to the opener on close. Keyed on `open` alone: a
  // dependency that changes while the dialog is up would run the cleanup,
  // which focuses the opener behind the backdrop, and then re-focus the body.
  useEffect(() => {
    if (!open) {
      setRefused(false)
      return
    }
    openerRef.current = document.activeElement
    const panel = panelRef.current
    const items = focusableIn(panel)
    // The body's first control rather than the panel's: the panel's first is
    // the close mark, and landing there means Enter shuts the dialog the
    // instant it opens.
    const first = items.find((node) => node.closest('[data-dialog-body]')) ?? items[0]
    if (first) first.focus()
    else panel?.focus()
    return () => {
      ;(openerRef.current as HTMLElement | null)?.focus?.()
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const panel = panelRef.current

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        tryCloseRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const items = focusableIn(panel)
      if (items.length === 0) {
        event.preventDefault()
        return
      }
      const firstItem = items[0]
      const lastItem = items[items.length - 1]
      const active = document.activeElement
      // Wrapped at both ends, and pulled back in if focus is somehow outside:
      // the page behind a backdrop is not somewhere Tab may go.
      if (!panel?.contains(active)) {
        event.preventDefault()
        ;(event.shiftKey ? lastItem : firstItem).focus()
      } else if (event.shiftKey && active === firstItem) {
        event.preventDefault()
        lastItem.focus()
      } else if (!event.shiftKey && active === lastItem) {
        event.preventDefault()
        firstItem.focus()
      }
    }

    document.addEventListener('keydown', onKey, true)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey, true)
      document.body.style.overflow = previous
    }
  }, [open])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center">
      <div
        className="dialog-backdrop absolute inset-0"
        onClick={() => tryCloseRef.current()}
        aria-hidden="true"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={cn(
          'dialog relative flex max-h-[92vh] w-full flex-col animate-rise',
          DIALOG_SIZE[size],
        )}
      >
        <header className="flex items-start gap-3 border-b border-line px-strip py-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-page font-semibold text-strong">{title}</h2>
            {subtitle && <p className="mt-0.5 text-body text-muted">{subtitle}</p>}
          </div>
          <button
            type="button"
            onClick={() => tryCloseRef.current()}
            className="btn-icon"
            title="Close this."
            aria-label="Close this."
          >
            <X className="size-icon" />
          </button>
        </header>

        <div data-dialog-body className="min-h-0 flex-1 overflow-y-auto px-strip py-4">
          {children}
        </div>

        {(footer || footerNote || refused) && (
          <footer className="flex flex-wrap items-center gap-3 border-t border-line px-strip py-3">
            <span className="min-w-0 flex-1 text-small text-dim">
              {refused && busy ? <span className="text-caution">{busy}</span> : footerNote}
            </span>
            {footer && <div className="flex items-center gap-2">{footer}</div>}
          </footer>
        )}
      </div>
    </div>
  )
}

/* ── Confirm ─────────────────────────────────────────────────────────────── */

/**
 * The small dialog in front of something irreversible.
 *
 * Always paired with a sentence saying what is lost (7.6), which is why
 * `consequence` is required rather than optional: a confirmation that only asks
 * "are you sure?" has told the user nothing they did not already know.
 */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  consequence,
  confirmLabel = 'Delete',
  busy,
}: {
  open: boolean
  onClose: () => void
  onConfirm: () => void
  title: string
  consequence: string
  confirmLabel?: string
  busy?: boolean
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      size="small"
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn-danger" onClick={onConfirm} disabled={busy}>
            {busy && <Spinner />}
            {confirmLabel}
          </button>
        </>
      }
    >
      <p className="text-body text-fg">{consequence}</p>
    </Dialog>
  )
}

/* ── Copy ────────────────────────────────────────────────────────────────── */

/**
 * A button that copies a value and says it did.
 *
 * The confirmation is the button changing, not a toast: a toast for something
 * this small is a message about a message. It reverts after two seconds,
 * because a button stuck reading "Copied" is a button that lies about the next
 * press.
 */
export function CopyButton({ value, label = 'Copy' }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  return (
    <button
      type="button"
      className="btn-secondary"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value)
          setCopied(true)
        } catch {
          // Clipboard access is refused on a page served over plain HTTP in
          // some browsers, which is most self-hosted installs. Say nothing and
          // leave the value on screen to select by hand.
        }
      }}
    >
      {copied ? <Check className="size-icon" /> : null}
      {copied ? 'Copied' : label}
    </button>
  )
}
