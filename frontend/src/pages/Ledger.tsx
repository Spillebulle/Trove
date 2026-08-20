/*
 * The ledger: every attempt Trove has ever made.
 *
 * A table, because these are rows with columns and there are a lot of them
 * (7.16): header in `text-dim`, hairlines between rows, no zebra, no vertical
 * rules, and the times right-aligned and monospaced so a column of them lines
 * up rather than jitters.
 *
 * The filter is a segmented control rather than a dropdown because there are
 * five outcomes and 7.4 asks for a segmented control at five or fewer. It wraps
 * on a phone rather than turning into a second shape.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Clock, KeyRound } from 'lucide-react'
import { api } from '@/lib/api'
import { useToast } from '@/lib/app-context'
import type { Claim } from '@/lib/types'
import { OUTCOME_LABEL, OUTCOME_TONE, fullTime, relativeTime } from '@/lib/utils'
import {
  CopyButton,
  Dialog,
  EmptyState,
  ErrorState,
  KindBadge,
  PageHeader,
  Segmented,
  Spinner,
  StatusBadge,
} from '@/components/ui'

type Filter = 'all' | 'claimed' | 'needs_attention' | 'failed'

const FILTERS: ReadonlyArray<{ value: Filter; label: string }> = [
  { value: 'all', label: 'Everything' },
  { value: 'claimed', label: 'Claimed' },
  { value: 'needs_attention', label: 'Needed a hand' },
  { value: 'failed', label: 'Failed' },
]

export function Ledger() {
  const [filter, setFilter] = useState<Filter>('all')
  const [keyFor, setKeyFor] = useState<Claim | null>(null)

  const claims = useQuery({
    queryKey: ['claims', filter],
    queryFn: () =>
      api.claims.list({ limit: 200, outcome: filter === 'all' ? undefined : filter }),
  })

  return (
    <>
      <PageHeader
        title="Ledger"
        subtitle="Every attempt, including the ones that found the game already in your library."
        actions={<Segmented options={FILTERS} value={filter} onChange={setFilter} label="Show" />}
      />

      <div className="panel">
        {claims.isError ? (
          <ErrorState error={claims.error} onRetry={() => void claims.refetch()} />
        ) : claims.isLoading ? (
          <div className="skeleton h-64" />
        ) : (claims.data?.length ?? 0) === 0 ? (
          <EmptyState
            icon={<Clock size={24} />}
            title={filter === 'all' ? 'Nothing attempted yet' : 'Nothing here'}
            description={
              filter === 'all'
                ? 'Once Trove runs, every attempt it makes is listed here with what came of it.'
                : 'No attempt has ended this way. Try another filter.'
            }
          />
        ) : (
          <div className="scroll-x">
            <table className="w-full border-collapse text-control">
              <thead>
                <tr className="table-head">
                  <th className="px-strip py-2 font-normal">Game</th>
                  <th className="px-strip py-2 font-normal">Account</th>
                  <th className="px-strip py-2 font-normal">Outcome</th>
                  <th className="px-strip py-2 text-right font-normal">When</th>
                </tr>
              </thead>
              <tbody>
                {(claims.data ?? []).map((claim) => (
                  <tr key={claim.id} className="table-row">
                    {/*
                     * The poster earns its place here: a ledger of what you own
                     * reads as a shelf rather than a log when the artwork is on
                     * it. Small and fixed-size so the row height does not move,
                     * and the cell still reads without it - an old row claimed
                     * before Trove copied posters simply has none.
                     */}
                    <td className="max-w-[26rem] px-strip py-2">
                      <span className="flex items-start gap-3">
                        {claim.image_url && (
                          <img
                            src={claim.image_url}
                            alt=""
                            loading="lazy"
                            className="art mt-0.5 h-10 w-[4.5rem] shrink-0 object-cover"
                          />
                        )}
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-2">
                            <span className="truncate text-strong">{claim.title}</span>
                            <KindBadge kind={claim.kind} />
                          </span>
                          {claim.detail && (
                            <span className="block truncate text-small text-dim" title={claim.detail}>
                              {claim.detail}
                            </span>
                          )}
                      <span className="mt-1 flex flex-wrap items-center gap-2">
                        {claim.has_key && (
                          <button
                            type="button"
                            className="btn-ghost h-auto px-0 text-small"
                            onClick={() => setKeyFor(claim)}
                          >
                            <KeyRound size={12} />
                            Show the key
                          </button>
                        )}
                        {claim.screenshot && (
                          <a
                            href={api.screenshotUrl(claim.screenshot)}
                            target="_blank"
                            rel="noreferrer noopener"
                            className="text-small text-accent hover:brightness-110"
                          >
                            What Trove saw
                          </a>
                        )}
                      </span>
                        </span>
                      </span>
                    </td>
                    <td className="px-strip py-2 text-muted">
                      {claim.account_label ?? <span className="text-dim">Deleted</span>}
                    </td>
                    <td className="px-strip py-2">
                      <StatusBadge
                        tone={OUTCOME_TONE[claim.outcome]}
                        label={OUTCOME_LABEL[claim.outcome]}
                      />
                    </td>
                    {/* Figures are right-aligned and monospaced (7.16). */}
                    <td
                      className="figure whitespace-nowrap px-strip py-2 text-right text-tiny text-dim"
                      title={fullTime(claim.created_at)}
                    >
                      {relativeTime(claim.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <KeyDialog claim={keyFor} onClose={() => setKeyFor(null)} />
    </>
  )
}

/**
 * A claimed key, fetched only when asked for.
 *
 * The key is encrypted at rest and is deliberately absent from the list
 * response, so opening this is a request of its own. That is the point: a key
 * is worth money, and one that rides along in every ledger page is one sitting
 * in every browser cache and proxy log that ever saw the page.
 */
function KeyDialog({ claim, onClose }: { claim: Claim | null; onClose: () => void }) {
  const { push } = useToast()
  const key = useQuery({
    queryKey: ['claim-key', claim?.id],
    queryFn: () => api.claims.key(claim!.id),
    enabled: Boolean(claim),
    // Never cached: the value should not outlive the dialog that asked for it.
    gcTime: 0,
    staleTime: 0,
    retry: false,
  })

  if (key.isError && claim) {
    push(key.error instanceof Error ? key.error.message : 'The key could not be read.', 'critical')
  }

  return (
    <Dialog
      open={Boolean(claim)}
      onClose={onClose}
      title={claim?.title ?? 'Key'}
      subtitle={
        claim?.key_store
          ? `Redeem this on ${claim.key_store}.`
          : 'Redeem this on the store it came from.'
      }
      size="small"
      footer={
        <button type="button" className="btn-ghost" onClick={onClose}>
          Close
        </button>
      }
    >
      {key.isLoading ? (
        <span className="flex items-center gap-2 text-body text-dim">
          <Spinner />
          Reading the key.
        </span>
      ) : key.data ? (
        <div className="flex flex-wrap items-center gap-3">
          {/* Selectable text as well as a copy button: clipboard access is
              refused on a plain-HTTP page in some browsers, which is most
              self-hosted installs, and a key you cannot select would be a key
              you cannot use. */}
          <code className="well flex-1 select-all break-all p-3 font-mono text-control text-strong">
            {key.data.key_code}
          </code>
          <CopyButton value={key.data.key_code} label="Copy the key" />
        </div>
      ) : (
        <p className="text-body text-dim">The key could not be read.</p>
      )}
    </Dialog>
  )
}
