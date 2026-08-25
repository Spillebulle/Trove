/*
 * What is free right now, and what is announced to become free.
 *
 * Cards with the store's own artwork, because this is the one page in the app
 * whose subject is a picture of something (STYLE-GUIDE 7.21) and a grid of
 * game art is more legible at a glance than a table of titles.
 *
 * The refresh button is safe to press and says so. Discovery touches no account
 * and opens no browser: it is one HTTP request per store to a public endpoint,
 * which is exactly why CLAUDE.md keeps it separate from claiming.
 *
 * The page is split by `starts_at`: an offer whose start is still in the
 * future goes to the "Coming up" schedule below the grid rather than into it,
 * because a card in the grid reads as claimable now and an unstarted offer is
 * not. The split leans free-now on purpose (`isUpcoming`): a missing, past or
 * unreadable start date is free-now, so nothing can fall between the two
 * sections - and a minute tick moves a just-started offer across without a
 * reload.
 */
import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { CalendarPlus, Gift, RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/lib/app-context'
import type { Offer } from '@/lib/types'
import {
  BLOCKED_LABEL,
  fullTime,
  isUpcoming,
  relativeTime,
  shortDate,
  timeLeft,
} from '@/lib/utils'
import {
  Art,
  EmptyState,
  ErrorState,
  KindBadge,
  PageHeader,
  Panel,
  Spinner,
  StoreBadge,
} from '@/components/ui'

/** "Free from Thu 28 Aug to Thu 4 Sep" - the offer's window, as a sentence. */
function freeWindow(offer: Offer): string {
  const from = shortDate(offer.starts_at)
  const to = shortDate(offer.ends_at)
  if (from && to) return `Free from ${from} to ${to}`
  if (from) return `Free from ${from}`
  return 'Free soon'
}

export function Offers() {
  const queryClient = useQueryClient()
  const { push } = useToast()

  const offers = useQuery({ queryKey: ['offers'], queryFn: () => api.offers.list(true) })

  // Re-bucketed once a minute, so an offer whose start passes while the page
  // is open moves into the grid instead of sitting in a stale "Coming up".
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])

  const refresh = useMutation({
    mutationFn: api.offers.refresh,
    onSuccess: (data) => {
      queryClient.setQueryData(['offers'], data)
      void queryClient.invalidateQueries({ queryKey: ['summary'] })
      // The toast counts what is claimable now; an announced-for-next-week
      // offer is not that, and counting it would overpromise.
      const freeCount = data.filter((offer) => !isUpcoming(offer)).length
      push(
        freeCount === 1
          ? 'One game is free right now.'
          : `${freeCount} games are free right now.`,
        'good',
      )
    },
    onError: (error: Error) => push(error.message, 'critical'),
  })

  // The .ics download. Fetched rather than linked so the session cookie goes
  // with it, then handed to the browser as an ordinary file save. It is a
  // snapshot to import, not a feed to subscribe to - a calendar app polling a
  // URL sends no cookie, and the endpoint is authenticated on purpose.
  const calendar = useMutation({
    mutationFn: api.offers.calendar,
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'trove-offers.ics'
      anchor.click()
      URL.revokeObjectURL(url)
    },
    onError: (error: Error) => push(error.message, 'critical'),
  })

  const freeNow = (offers.data ?? []).filter((offer) => !isUpcoming(offer, now))
  const upcoming = (offers.data ?? [])
    .filter((offer) => isUpcoming(offer, now))
    .sort(
      (a, b) => new Date(a.starts_at ?? 0).getTime() - new Date(b.starts_at ?? 0).getTime(),
    )

  return (
    <>
      <PageHeader
        title="Free now"
        subtitle="What the stores are giving away. Checking this costs nothing and touches no account."
        actions={
          <>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => calendar.mutate()}
              disabled={calendar.isPending}
              title="Save the announced giveaways and the deadlines as calendar events."
            >
              {calendar.isPending ? <Spinner /> : <CalendarPlus className="size-icon" />}
              Download calendar (.ics)
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending}
              title="Ask every store what is free right now."
            >
              {refresh.isPending ? <Spinner /> : <RefreshCw className="size-icon" />}
              Check the stores
            </button>
          </>
        }
      />

      {offers.isError ? (
        <ErrorState error={offers.error} onRetry={() => void offers.refetch()} />
      ) : offers.isLoading ? (
        <div className="offer-grid">
          {[0, 1, 2, 3].map((key) => (
            <div key={key} className="card skeleton h-56" />
          ))}
        </div>
      ) : (
        <>
          {freeNow.length === 0 ? (
            <div className="card">
              <EmptyState
                icon={<Gift size={24} />}
                title="Nothing is free at the moment"
                description="Stores run giveaways in bursts. Trove checks on its own, and anything it finds appears here."
                action={
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => refresh.mutate()}
                    disabled={refresh.isPending}
                  >
                    Check now
                  </button>
                }
              />
            </div>
          ) : (
            <div className="offer-grid">
              {freeNow.map((offer) => {
                const ends = timeLeft(offer.ends_at)
                return (
                  <article
                    key={offer.id}
                    /* A column of full height, so the three lines at the foot
                       sit on the card's floor rather than wherever the text
                       above them happened to end. Grid items already stretch
                       to the tallest card in the row; without this the cards
                       are the same height but their footers are not aligned,
                       which is what makes a tidy grid look ragged. */
                    className="card flex h-full flex-col overflow-hidden"
                  >
                    {/*
                     * The artwork is flush to the card's edge with the caption
                     * under it (7.15). Not an art card: an offer needs a claimed-by
                     * line and a countdown, which is text that belongs under the
                     * picture rather than over it.
                     */}
                    <Art
                      src={offer.image_url}
                      store={offer.store}
                      className="block aspect-wide w-full rounded-b-none"
                    />

                    <div className="flex flex-1 flex-col p-strip">
                      <h2
                        className="truncate text-control font-semibold text-strong"
                        title={offer.title}
                      >
                        {offer.title}
                      </h2>

                      <p className="mt-0.5 flex min-w-0 items-center gap-2 text-small text-dim">
                        <StoreBadge store={offer.store} withName />
                        {/* An add-on is worth a badge rather than a word: it is the
                            one kind that may not be claimable on its own, and the
                            card should say so at a glance. */}
                        <KindBadge kind={offer.kind} />
                      </p>

                      <p
                        className="mt-2 text-small text-fg"
                        title={fullTime(offer.ends_at)}
                      >
                        {ends ?? 'No end date given.'}
                      </p>

                      {/* `mt-auto` is the floor: this row and the "seen" line
                          under it are pushed down, and the slack lands above
                          them instead of below. */}
                      <div className="mt-auto flex flex-wrap items-center gap-2 pt-2">
                        {offer.claimed_by.length > 0 ? (
                          <span
                            className="badge-good"
                            title={`Claimed on: ${offer.claimed_by.join(', ')}.`}
                          >
                            Claimed on {offer.claimed_by.length}
                          </span>
                        ) : offer.blocked_reason ? (
                          /* Something tried and found a settled answer. Say
                             which, because "Not claimed yet" reads as a thing
                             still to come. `caution`, not `critical`: nothing
                             is broken, there is just nothing to do here. */
                          <span
                            className="badge-caution"
                            title={offer.blocked_detail ?? undefined}
                          >
                            {BLOCKED_LABEL[offer.blocked_reason] ?? 'Not claimed yet'}
                          </span>
                        ) : (
                          <span className="badge">Not claimed yet</span>
                        )}
                        {offer.url && (
                          <a
                            href={offer.url}
                            target="_blank"
                            rel="noreferrer noopener"
                            className="ml-auto text-small text-accent hover:brightness-110"
                            title="Open this on the store, in your own browser."
                          >
                            Store page
                          </a>
                        )}
                      </div>

                      <p className="mt-2 text-tiny text-dim">
                        Seen {relativeTime(offer.first_seen_at)}
                      </p>
                    </div>
                  </article>
                )
              })}
            </div>
          )}

          {/*
           * The announced-in-advance offers, as a dated schedule. A list and
           * not a month grid: there are one to three of these in a normal
           * week, and a calendar of thirty empty cells says less than three
           * dated rows. No claim state on a row - nothing can be claimed
           * before it starts, so the row is a fact, not a task, and it stays
           * in the neutral text colours rather than wearing a tone.
           */}
          {upcoming.length > 0 && (
            <Panel title="Coming up" count={upcoming.length} className="mt-5" bodyClassName="p-0">
              <ul className="divide-y divide-line-soft">
                {upcoming.map((offer) => (
                  <li key={offer.id} className="flex items-center gap-3 px-strip py-2.5">
                    {/* Artwork is content and takes a rung of the ladder (7.21).
                        The block is drawn whether or not a picture arrives, so a
                        row with no art is the same height as its neighbours. */}
                    <Art
                      src={offer.image_url}
                      store={offer.store}
                      className="aspect-wide w-art-row shrink-0"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2">
                        <span className="truncate text-control text-strong">{offer.title}</span>
                        <KindBadge kind={offer.kind} />
                      </span>
                      <span
                        className="mt-0.5 flex items-center gap-1.5 text-small text-dim"
                        title={fullTime(offer.starts_at)}
                      >
                        {/* The mark alone; the tooltip carries the store's
                            name, and the window is the part worth the width. */}
                        <StoreBadge store={offer.store} />
                        <span className="truncate">{freeWindow(offer)}</span>
                      </span>
                    </span>
                    <span
                      className="shrink-0 text-small text-dim"
                      title={fullTime(offer.starts_at)}
                    >
                      {relativeTime(offer.starts_at)}
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </>
      )}
    </>
  )
}
