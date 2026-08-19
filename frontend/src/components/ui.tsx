/*
 * The painted controls, composed from the component classes in index.css.
 * Everything here names a role, never a colour; the geometry comes from
 * STYLE-GUIDE section 7.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Check, TriangleAlert, X } from 'lucide-react'
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
