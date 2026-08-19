/*
 * The overview.
 *
 * Four tiles, then the two things that actually need a person: accounts waiting
 * on a hand, and what is free right now. The recent ledger is under those,
 * because it is the answer to "is it working" rather than "what should I do".
 *
 * The attention panel is first on purpose. This app's failure mode is silent:
 * a session expires, every run after that stops, and nothing looks broken from
 * across the room. So the one thing the page leads with is the list of accounts
 * that cannot go on without somebody.
 */
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { CircleCheck, Gift, RefreshCw, TriangleAlert } from 'lucide-react'
import { api } from '@/lib/api'
import {
  ACCOUNT_STATUS_LABEL,
  ACCOUNT_STATUS_TONE,
  OUTCOME_LABEL,
  OUTCOME_TONE,
  fullTime,
  relativeTime,
  timeLeft,
} from '@/lib/utils'
import { EmptyState, ErrorState, PageHeader, Panel, StatusBadge, Tile } from '@/components/ui'

export function Dashboard() {
  const summary = useQuery({ queryKey: ['summary'], queryFn: api.summary, refetchInterval: 30_000 })
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: api.accounts.list })
  const offers = useQuery({ queryKey: ['offers'], queryFn: () => api.offers.list(true) })
  const claims = useQuery({
    queryKey: ['claims', { limit: 8 }],
    queryFn: () => api.claims.list({ limit: 8 }),
  })

  const needsHand = (accounts.data ?? []).filter(
    (account) => account.status === 'needs_attention' || account.status === 'never_signed_in',
  )

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="What is free, what has been claimed, and what needs you."
      />

      <div className="mb-4 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr))]">
        <Tile
          eyebrow="Free right now"
          value={summary.data?.free_now ?? null}
          detail={summary.data?.free_now === 0 ? 'Nothing is being given away.' : 'Across every store.'}
        />
        <Tile
          eyebrow="Claimed, all time"
          value={summary.data?.claimed_total ?? null}
          detail={
            summary.data?.claimed_7d
              ? `${summary.data.claimed_7d} in the last seven days.`
              : 'None in the last seven days.'
          }
        />
        <Tile
          eyebrow="Accounts"
          value={summary.data?.accounts ?? null}
          detail={
            summary.data?.accounts_needing_attention
              ? `${summary.data.accounts_needing_attention} waiting for you.`
              : 'All signed in.'
          }
          dot={summary.data?.accounts_needing_attention ? 'caution' : undefined}
        />
        <Tile
          eyebrow="Last run"
          value={summary.data?.last_run_at ? relativeTime(summary.data.last_run_at) : null}
          detail={
            summary.data?.scheduler_enabled
              ? 'The schedule is running.'
              : 'The schedule is paused.'
          }
          dot={summary.data?.scheduler_enabled ? 'good' : 'neutral'}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Needs a hand" count={needsHand.length || undefined} bodyClassName="p-0">
          {accounts.isError ? (
            <ErrorState error={accounts.error} onRetry={() => void accounts.refetch()} />
          ) : needsHand.length === 0 ? (
            <EmptyState
              icon={<CircleCheck size={24} />}
              title="Nothing is waiting"
              description="Every account is signed in. Trove will carry on by itself."
            />
          ) : (
            <ul className="divide-y divide-line-soft">
              {needsHand.map((account) => (
                <li key={account.id}>
                  <Link
                    to={`/accounts/${account.id}`}
                    className="flex items-center gap-3 px-strip py-2.5 transition-colors duration-hover hover:bg-control-hover"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-control text-strong">
                        {account.label}
                      </span>
                      <span className="block truncate text-small text-dim">
                        {account.status_reason ??
                          'Open the live view and sign in to this store.'}
                      </span>
                    </span>
                    <StatusBadge
                      tone={ACCOUNT_STATUS_TONE[account.status]}
                      label={ACCOUNT_STATUS_LABEL[account.status]}
                    />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel
          title="Free right now"
          count={offers.data?.length}
          commands={
            <Link to="/offers" className="btn-ghost" title="See every offer.">
              See all
            </Link>
          }
          bodyClassName="p-0"
        >
          {offers.isError ? (
            <ErrorState error={offers.error} onRetry={() => void offers.refetch()} />
          ) : (offers.data?.length ?? 0) === 0 ? (
            <EmptyState
              icon={<Gift size={24} />}
              title="Nothing is free at the moment"
              description="Trove checks the stores on its own. Offers appear here when they do."
            />
          ) : (
            <ul className="divide-y divide-line-soft">
              {(offers.data ?? []).slice(0, 6).map((offer) => (
                <li key={offer.id} className="flex items-center gap-3 px-strip py-2">
                  {/* Artwork is content and takes a rung of the ladder (7.21).
                      The block is drawn whether or not a picture arrives, so a
                      row with no art is the same height as its neighbours. */}
                  <span className="art aspect-wide w-art-row shrink-0">
                    {offer.image_url && (
                      <img
                        src={offer.image_url}
                        alt=""
                        loading="lazy"
                        className="h-full w-full object-cover"
                      />
                    )}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-control text-strong">{offer.title}</span>
                    <span className="block truncate text-small text-dim">
                      {timeLeft(offer.ends_at) ?? 'No end date given.'}
                    </span>
                  </span>
                  {offer.claimed_by.length > 0 && (
                    <span
                      className="badge-good shrink-0"
                      title={`Claimed on: ${offer.claimed_by.join(', ')}.`}
                    >
                      {offer.claimed_by.length}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel
        title="Recent activity"
        className="mt-4"
        commands={
          <Link to="/ledger" className="btn-ghost" title="See the whole ledger.">
            See all
          </Link>
        }
        bodyClassName="p-0"
      >
        {claims.isError ? (
          <ErrorState error={claims.error} onRetry={() => void claims.refetch()} />
        ) : (claims.data?.length ?? 0) === 0 ? (
          <EmptyState
            icon={<RefreshCw size={24} />}
            title="Nothing has been attempted yet"
            description="Every attempt Trove makes lands here, including the ones that found the game already in your library."
          />
        ) : (
          <ul className="divide-y divide-line-soft">
            {(claims.data ?? []).map((claim) => (
              <li key={claim.id} className="flex items-center gap-3 px-strip py-2">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-control text-fg">{claim.title}</span>
                  <span className="block truncate text-small text-dim">
                    {claim.account_label ?? 'An account that no longer exists'}
                    {claim.detail ? ` · ${claim.detail}` : ''}
                  </span>
                </span>
                <StatusBadge
                  tone={OUTCOME_TONE[claim.outcome]}
                  label={OUTCOME_LABEL[claim.outcome]}
                />
                <span
                  className="figure hidden w-24 shrink-0 text-right text-tiny text-dim sm:block"
                  title={fullTime(claim.created_at)}
                >
                  {relativeTime(claim.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {summary.data && !summary.data.scheduler_enabled && (
        <div className="notice mt-4 flex flex-wrap items-center gap-3">
          <TriangleAlert className="size-icon shrink-0 text-caution" aria-hidden="true" />
          <p className="min-w-0 flex-1">
            The schedule is paused, so Trove will not check anything on its own.
          </p>
          <Link to="/settings" className="btn-ghost">
            Open Settings
          </Link>
        </div>
      )}
    </>
  )
}
