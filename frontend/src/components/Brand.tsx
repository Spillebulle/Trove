/*
 * The app mark.
 *
 * STYLE-GUIDE 11: a rounded square in the accent with no glyph, 15px in the bar
 * and 22px at web scale, which is what `size-mark` reads. The full logo, if
 * there is ever one, lives on an about screen and not here.
 *
 * A glyph is drawn inside it and that is a deliberate reading of "no glyph
 * rather than a letterform": the rule exists so a mark does not become a tiny
 * illustration competing with the app's name beside it. Three coins in a stack
 * is the smallest thing that says "a trove" and it is one shape at 22px. It is
 * `--brand-ink` rather than `--accent-ink`, so the mark is the same object in
 * both themes and the favicon and the top bar cannot disagree.
 */
export function Mark({ size }: { size?: number }) {
  return (
    <span
      // `size-mark` is the 15px / 22px square of section 11, and it is not
      // optional: without a size the span is a flex child in the top bar and
      // stretches to the bar's full height, which turns a 22px mark into a
      // 52px block. An explicit `size` overrides it for the sign-in page.
      className={
        'grid shrink-0 place-items-center rounded-tight bg-accent' +
        (size ? '' : ' size-mark')
      }
      style={size ? { width: size, height: size } : undefined}
      // The name is beside it in the bar, so the mark itself is decoration to
      // a screen reader and saying "Trove" twice helps nobody.
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 24 24"
        className={size ? undefined : 'h-[62%] w-[62%]'}
        width={size ? size * 0.62 : undefined}
        height={size ? size * 0.62 : undefined}
        fill="none"
        stroke="var(--brand-ink)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <ellipse cx="12" cy="6" rx="7" ry="3" />
        <path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6" />
        <path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
      </svg>
    </span>
  )
}

/** The mark at a stated size, for the sign-in page. */
export function Wordmark() {
  return (
    <div className="flex items-center gap-3">
      <Mark size={36} />
      <span className="text-page font-bold text-strong">Trove</span>
    </div>
  )
}
