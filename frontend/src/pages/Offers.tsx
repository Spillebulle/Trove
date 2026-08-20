/*
 * What is free right now.
 *
 * Cards with the store's own artwork, because this is the one page in the app
 * whose subject is a picture of something (STYLE-GUIDE 7.21) and a grid of
 * game art is more legible at a glance than a table of titles.
 *
 * The refresh button is safe to press and says so. Discovery touches no account
 * and opens no browser: it is one HTTP request per store to a public endpoint,
 * which is exactly why CLAUDE.md keeps it separate from claiming.
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Gift, RefreshCw } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/lib/app-context'
import { fullTime, relativeTime, timeLeft } from '@/lib/utils'
import { EmptyState, ErrorState, KindBadge, PageHeader, Spinner } from '@/components/ui'

export function Offers() {
  const queryClient = useQueryClient()
  const { push } = useToast()

  const offers = useQuery({ queryKey: ['offers'], queryFn: () => api.offers.list(true) })

  const refresh = useMutation({
    mutationFn: api.offers.refresh,
    onSuccess: (data) => {
      queryClient.setQueryData(['offers'], data)
      void queryClient.invalidateQueries({ queryKey: ['summary'] })
      push(
        data.length === 1
          ? 'One game is free right now.'
          : `${data.length} games are free right now.`,
        'good',
      )
    },
    onError: (error: Error) => push(error.message, 'critical'),
  })

  return (
    <>
      <PageHeader
        title="Free now"
        subtitle="What the stores are giving away. Checking this costs nothing and touches no account."
        actions={
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
      ) : (offers.data?.length ?? 0) === 0 ? (
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
          {(offers.data ?? []).map((offer) => {
            const ends = timeLeft(offer.ends_at)
            return (
              <article key={offer.id} className="card overflow-hidden">
                {/*
                 * The artwork is flush to the card's edge with the caption
                 * under it (7.15). Not an art card: an offer needs a claimed-by
                 * line and a countdown, which is text that belongs under the
                 * picture rather than over it.
                 */}
                <div className="art aspect-wide w-full rounded-b-none">
                  {offer.image_url && (
                    <img
                      src={offer.image_url}
                      alt=""
                      loading="lazy"
                      className="h-full w-full object-cover"
                    />
                  )}
                </div>

                <div className="p-strip">
                  <h2 className="truncate text-control font-semibold text-strong" title={offer.title}>
                    {offer.title}
                  </h2>

                  <p className="mt-0.5 flex items-center gap-2 text-small text-dim">
                    <span className="capitalize">{offer.store}</span>
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

                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {offer.claimed_by.length > 0 ? (
                      <span
                        className="badge-good"
                        title={`Claimed on: ${offer.claimed_by.join(', ')}.`}
                      >
                        Claimed on {offer.claimed_by.length}
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
    </>
  )
}
